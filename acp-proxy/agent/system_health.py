"""系统状态感知（路线图 2.4）——服务/端口/磁盘/内存/负载/进程 一键体检

对标 Hermes 日常运维体检（systemctl status / df / free / ps）合并为单次
工具调用。原则：

1. **只读**：全部检查均为读取系统状态，不做任何修改（不 kill 进程、
   不重启服务、不写文件）——天然安全，可放心由 agent 主动调用。
2. **确定性**：不走 LLM，阈值判定 + 结构化输出；每项检查独立 try/except，
   单项失败只记 unknown 不炸整轮体检。
3. **可注入**：服务状态/磁盘/负载/ps 等采集器可注入（测试用 fake），
   生产缺省走真实系统（systemctl --user / shutil.disk_usage / /proc/meminfo）。
4. **可配置**：受检服务列表/目录/端口可用环境变量覆盖
   （SOULMATE_HEALTH_SERVICES / SOULMATE_HEALTH_PATHS / SOULMATE_HEALTH_PORTS），
   不硬编码部署拓扑。

状态四态：ok（正常）/ warn（预警）/ critical（异常）/ unknown（取不到）。
整体结论取所有检查项最差值。
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

# 体检分区（sections 参数值域，顺序即报告顺序）
SECTION_NAMES = ("services", "ports", "disk", "memory", "load", "processes")

STATUS_ORDER = {"ok": 0, "unknown": 1, "warn": 2, "critical": 3}

# 阈值（percent / load倍数）
DISK_WARN_PCT = 85.0
DISK_CRIT_PCT = 95.0
MEM_WARN_PCT = 85.0
MEM_CRIT_PCT = 95.0
LOAD_WARN_X = 1.0   # load1 ≥ cpu数×1.0 → warn
LOAD_CRIT_X = 2.0   # load1 ≥ cpu数×2.0 → critical
ZOMBIE_WARN = 1     # 僵尸进程数 ≥1 → warn
ZOMBIE_CRIT = 50
PROC_TOP_N = 5      # 内存占用前N进程列入报告

# 缺省受检对象（可被环境变量覆盖，不硬编码部署拓扑）
DEFAULT_SERVICES = ("opensoul", "openmate-web", "acp-proxy-a", "acp-proxy-b")
DEFAULT_PATHS = ("/", os.path.expanduser("~"))
# "8090=opensoul,8092=acp-proxy-a" 形式；值为服务备注名
DEFAULT_PORTS = ("8090=opensoul", "8092=acp-proxy-a")

STATUS_EMOJI = {"ok": "✅", "warn": "⚠️", "critical": "❌", "unknown": "❓"}


@dataclass
class CheckResult:
    """单项检查结果。"""
    section: str          # 所属分区（SECTION_NAMES 之一）
    name: str             # 检查对象（服务名/路径/端口/指标名）
    status: str           # ok / warn / critical / unknown
    detail: str = ""      # 人类可读说明（含关键数值）


@dataclass
class HealthReport:
    """整轮体检汇总。"""
    results: list[CheckResult] = field(default_factory=list)

    @property
    def overall(self) -> str:
        if not self.results:
            return "unknown"
        return max((r.status for r in self.results),
                   key=lambda s: STATUS_ORDER.get(s, 1))

    def counts(self) -> dict:
        out = {"ok": 0, "warn": 0, "critical": 0, "unknown": 0}
        for r in self.results:
            out[r.status] = out.get(r.status, 0) + 1
        return out

    def format(self) -> str:
        """工具输出文本：整体结论 + 分区明细（每项带状态emoji+关键数值）。"""
        c = self.counts()
        lines = [
            f"🩺 系统体检：{STATUS_EMOJI.get(self.overall, '❓')} {self.overall}"
            f"（{len(self.results)}项：ok={c['ok']} warn={c['warn']}"
            f" critical={c['critical']} unknown={c['unknown']}）"
        ]
        for section in SECTION_NAMES:
            rows = [r for r in self.results if r.section == section]
            if not rows:
                continue
            lines.append(f"\n[{section}]")
            for r in rows:
                lines.append(
                    f"  {STATUS_EMOJI.get(r.status, '❓')} {r.name}: {r.detail}")
        return "\n".join(lines)


def _pct(used: float, total: float) -> float:
    return round(used * 100.0 / total, 1) if total else 0.0


class SystemHealthChecker:
    """系统状态体检器（只读、确定性、采集器可注入）。"""

    def __init__(
        self,
        services: Optional[Sequence[str]] = None,
        paths: Optional[Sequence[str]] = None,
        ports: Optional[Sequence[str]] = None,
        *,
        service_runner: Optional[Callable[[str], str]] = None,
        port_checker: Optional[Callable[[int], bool]] = None,
        disk_usage_fn: Optional[Callable[[str], tuple]] = None,
        meminfo_path: Optional[str] = None,
        load_fn: Optional[Callable[[], tuple]] = None,
        cpu_count: Optional[int] = None,
        ps_runner: Optional[Callable[[], str]] = None,
    ):
        self.services = tuple(services) if services is not None else self._env_list(
            "SOULMATE_HEALTH_SERVICES", DEFAULT_SERVICES)
        self.paths = tuple(paths) if paths is not None else self._env_list(
            "SOULMATE_HEALTH_PATHS", DEFAULT_PATHS)
        self.ports = tuple(ports) if ports is not None else self._env_list(
            "SOULMATE_HEALTH_PORTS", DEFAULT_PORTS)
        self._service_runner = service_runner or self._run_systemctl
        self._port_checker = port_checker or self._run_port_check
        self._disk_usage_fn = disk_usage_fn or shutil.disk_usage
        self._meminfo_path = meminfo_path or "/proc/meminfo"
        self._load_fn = load_fn or os.getloadavg
        self._cpu_count = cpu_count or os.cpu_count() or 1
        self._ps_runner = ps_runner or self._run_ps

    # ── 采集器缺省实现（生产路径）─────────────────────────────────

    @staticmethod
    def _env_list(name: str, default: Sequence[str]) -> tuple:
        raw = os.environ.get(name, "").strip()
        if not raw:
            return tuple(default)
        return tuple(p.strip() for p in raw.split(",") if p.strip())

    @staticmethod
    def _run_systemctl(service: str) -> str:
        """systemctl --user is-active <service>，取不到返回 unknown。"""
        try:
            proc = subprocess.run(
                ["systemctl", "--user", "is-active", service],
                capture_output=True, text=True, errors="replace", timeout=5,
            )
            return (proc.stdout or proc.stderr or "").strip() or "unknown"
        except Exception:
            return "unknown"

    @staticmethod
    def _run_port_check(port: int) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            return False

    @staticmethod
    def _run_ps() -> str:
        try:
            proc = subprocess.run(
                ["ps", "-eo", "pid,stat,comm,%mem,%cpu", "--sort=-%mem"],
                capture_output=True, text=True, errors="replace", timeout=10,
            )
            return proc.stdout or ""
        except Exception:
            return ""

    # ── 各分区检查 ──────────────────────────────────────────────

    def check_services(self) -> list[CheckResult]:
        results: list[CheckResult] = []
        for svc in self.services:
            try:
                state = str(self._service_runner(svc) or "unknown").strip().lower()
            except Exception as e:  # 采集器异常只降级单项，不炸整段
                results.append(CheckResult(
                    "services", svc, "unknown", f"状态检查异常: {e}"))
                continue
            if state == "active":
                status, detail = "ok", "active（运行中）"
            elif state in ("activating", "reloading"):
                status, detail = "warn", f"{state}（启动/重载中）"
            elif state in ("inactive", "failed", "deactivating"):
                status, detail = "critical", f"{state}（未运行）"
            else:
                status, detail = "unknown", f"状态不可知（{state}）"
            results.append(CheckResult("services", svc, status, detail))
        return results

    def check_ports(self) -> list[CheckResult]:
        results: list[CheckResult] = []
        for spec in self.ports:
            port_s, _, label = str(spec).partition("=")
            try:
                port = int(port_s.strip())
            except ValueError:
                results.append(CheckResult(
                    "ports", str(spec), "unknown", "端口格式非法（应为 端口=备注）"))
                continue
            name = f"{port}" + (f"（{label.strip()}）" if label.strip() else "")
            try:
                listening = self._port_checker(port)
            except Exception as e:  # 采集器异常只降级单项，不炸整段
                results.append(CheckResult(
                    "ports", name, "unknown", f"端口检查异常: {e}"))
                continue
            if listening:
                results.append(CheckResult("ports", name, "ok", "本地监听中"))
            else:
                results.append(CheckResult("ports", name, "critical", "本地未监听"))
        return results

    def check_disk(self) -> list[CheckResult]:
        results: list[CheckResult] = []
        for path in self.paths:
            try:
                usage = self._disk_usage_fn(path)
                total, used = float(usage[0]), float(usage[1])
                pct = _pct(used, total)
                free_gb = round((total - used) / (1024 ** 3), 1)
                if pct >= DISK_CRIT_PCT:
                    status = "critical"
                elif pct >= DISK_WARN_PCT:
                    status = "warn"
                else:
                    status = "ok"
                detail = f"已用{pct}%（剩余{free_gb}GB）"
            except Exception as e:
                status, detail = "unknown", f"读取失败: {e}"
            results.append(CheckResult("disk", path, status, detail))
        return results

    def check_memory(self) -> list[CheckResult]:
        try:
            with open(self._meminfo_path, encoding="utf-8", errors="replace") as f:
                text = f.read()
        except Exception as e:
            return [CheckResult("memory", "mem", "unknown", f"读取失败: {e}")]
        kv: dict = {}
        for m in re.finditer(r"^(\w+):\s+(\d+)\s*kB", text, re.M):
            kv[m.group(1)] = int(m.group(2))
        results: list[CheckResult] = []
        total = kv.get("MemTotal", 0)
        if total:
            avail = kv.get("MemAvailable", 0)
            used = total - avail
            pct = _pct(used, total)
            status = ("critical" if pct >= MEM_CRIT_PCT
                      else "warn" if pct >= MEM_WARN_PCT else "ok")
            results.append(CheckResult(
                "memory", "mem", status,
                f"已用{pct}%（{round(used / (1024 ** 2), 1)}"
                f"/{round(total / (1024 ** 2), 1)}GB）"))
        else:
            results.append(CheckResult("memory", "mem", "unknown", "MemTotal 缺失"))
        swap_total = kv.get("SwapTotal", 0)
        if swap_total:
            swap_used = swap_total - kv.get("SwapFree", 0)
            results.append(CheckResult(
                "memory", "swap", "ok",
                f"已用{_pct(swap_used, swap_total)}%"
                f"（{round(swap_used / (1024 ** 2), 1)}"
                f"/{round(swap_total / (1024 ** 2), 1)}GB）"))
        return results

    def check_load(self) -> list[CheckResult]:
        try:
            load1, load5, load15 = self._load_fn()
        except Exception as e:
            return [CheckResult("load", "loadavg", "unknown", f"读取失败: {e}")]
        cpu = max(int(self._cpu_count), 1)
        if load1 >= cpu * LOAD_CRIT_X:
            status = "critical"
        elif load1 >= cpu * LOAD_WARN_X:
            status = "warn"
        else:
            status = "ok"
        return [CheckResult(
            "load", "loadavg", status,
            f"{load1:.2f}/{load5:.2f}/{load15:.2f}（cpu={cpu}核，"
            f"warn≥{cpu * LOAD_WARN_X:.0f} crit≥{cpu * LOAD_CRIT_X:.0f}）")]

    def check_processes(self) -> list[CheckResult]:
        raw = self._ps_runner()
        if not raw.strip():
            return [CheckResult("processes", "ps", "unknown", "ps 输出为空")]
        rows = []
        zombies = 0
        for line in raw.splitlines()[1:]:
            parts = line.split(None, 4)
            if len(parts) < 5:
                continue
            pid, stat, comm, mem_s, cpu_s = parts
            if "Z" in stat:
                zombies += 1
            try:
                mem_v = float(mem_s)
            except ValueError:
                mem_v = 0.0
            rows.append((mem_v, pid, stat, comm, cpu_s))
        rows.sort(key=lambda r: -r[0])
        top = "；".join(
            f"{comm}(pid {pid}, mem {mem_v:.1f}%, cpu {cpu_s}%)"
            for mem_v, pid, stat, comm, cpu_s in rows[:PROC_TOP_N])
        results = [CheckResult(
            "processes", "top-mem", "ok",
            f"共{len(rows)}个进程，内存前{min(len(rows), PROC_TOP_N)}: {top or '无'}")]
        if zombies >= ZOMBIE_CRIT:
            z_status = "critical"
        elif zombies >= ZOMBIE_WARN:
            z_status = "warn"
        else:
            z_status = "ok"
        results.append(CheckResult(
            "processes", "zombie", z_status, f"{zombies}个僵尸进程"))
        return results

    # ── 整轮体检 ────────────────────────────────────────────────

    def run(self, sections: Optional[Sequence[str]] = None) -> HealthReport:
        """执行体检。sections 缺省全部分区；未知分区名忽略（报告自报）。"""
        wanted = [s for s in (sections or SECTION_NAMES)
                  if s in SECTION_NAMES]
        report = HealthReport()
        runners = {
            "services": self.check_services,
            "ports": self.check_ports,
            "disk": self.check_disk,
            "memory": self.check_memory,
            "load": self.check_load,
            "processes": self.check_processes,
        }
        for name in wanted:
            try:
                report.results.extend(runners[name]())
            except Exception as e:  # 单项失败不炸整轮
                report.results.append(CheckResult(
                    name, name, "unknown", f"检查异常: {e}"))
        return report

    def execute(self, sections: Optional[Sequence[str]] = None) -> str:
        """工具入口：返回格式化体检报告文本。"""
        return self.run(sections).format()

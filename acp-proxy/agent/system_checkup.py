# -*- coding: utf-8 -*-
"""系统状态一键体检（路线图 2.4）—— 服务/磁盘/进程三项运维基础体检。

设计原则：
- 只读探测，绝不修改系统状态（无 sudo / 无 systemctl / 无进程终止）
- 纯标准库（socket / shutil / /proc），无第三方依赖，测试可确定性断言
- 三段式体检：services（TCP端口探活）/ disk（磁盘占用阈值）/ processes（负载+内存+Top进程）
- 输出 = 结构化 dict（run()）+ 人类可读报告（format_report()），供 agent 直接转述
"""

from __future__ import annotations

import socket
import shutil
import time
from pathlib import Path

# 本机核心服务（名称, 端口）——与 docs/soulmate-roadmap.md 部署拓扑一致
DEFAULT_SERVICES = [
    ("OpenSoul后端", 8090),
    ("进化网关", 8091),
    ("ACP实例A", 8092),
    ("A2A服务", 8093),
    ("MCP客户端", 8094),
    ("ACP实例B", 8095),
    ("OpenMate前端", 3000),
]

# 磁盘阈值（使用百分比）
DISK_WARN_PCT = 85.0
DISK_CRIT_PCT = 95.0
# 内存可用量阈值（MB）
MEM_WARN_MB = 512.0
MEM_CRIT_MB = 256.0


def probe_port(port: int, host: str = "127.0.0.1", timeout: float = 1.0) -> bool:
    """TCP端口探活：可连接=在线。任何异常一律视为离线（只读探测不外抛）。"""
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except Exception:
        return False


def disk_status(used_percent: float) -> str:
    """磁盘使用率→状态：ok / warn / critical（纯函数，便于测试）。"""
    if used_percent >= DISK_CRIT_PCT:
        return "critical"
    if used_percent >= DISK_WARN_PCT:
        return "warn"
    return "ok"


def parse_meminfo(text: str) -> dict:
    """/proc/meminfo 文本→{key: MB数}。只取体检需要的字段（纯函数）。"""
    wanted = {"MemTotal": "total_mb", "MemAvailable": "available_mb", "MemFree": "free_mb"}
    out: dict = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].rstrip(":") in wanted:
            try:
                kb = float(parts[1])
            except ValueError:
                continue
            out[wanted[parts[0].rstrip(":")]] = round(kb / 1024.0, 1)
    return out


class SystemCheckup:
    """系统状态一键体检器（服务/磁盘/进程）。"""

    def __init__(self, services=None):
        self.services = list(services) if services is not None else list(DEFAULT_SERVICES)

    # ── 1. 服务状态：TCP端口探活 ──────────────────────────────
    def check_services(self, services=None, timeout: float = 1.0) -> dict:
        items = []
        for name, port in (services if services is not None else self.services):
            listening = probe_port(int(port), timeout=timeout)
            items.append({
                "name": name,
                "port": int(port),
                "listening": listening,
                "status": "ok" if listening else "down",
            })
        online = sum(1 for i in items if i["listening"])
        return {
            "section": "services",
            "items": items,
            "online": online,
            "total": len(items),
        }

    # ── 2. 磁盘占用 ───────────────────────────────────────────
    def check_disk(self, paths=None) -> dict:
        if paths is None:
            home = Path.home()
            paths = ["/", str(home), str(home / "openmate"), str(home / "opensoul")]
        items = []
        seen_devices = set()
        for p in paths:
            path = Path(p)
            try:
                st_dev = path.stat().st_dev
            except Exception:
                continue  # 路径不存在直接跳过
            if st_dev in seen_devices:
                continue  # 同一挂载点去重（/ 与 /home 同盘时只报一次）
            seen_devices.add(st_dev)
            try:
                usage = shutil.disk_usage(str(path))
            except Exception:
                continue
            used_pct = round(usage.used / usage.total * 100.0, 1) if usage.total else 0.0
            items.append({
                "path": str(path),
                "total_gb": round(usage.total / 1024 ** 3, 1),
                "used_gb": round(usage.used / 1024 ** 3, 1),
                "free_gb": round(usage.free / 1024 ** 3, 1),
                "used_percent": used_pct,
                "status": disk_status(used_pct),
            })
        return {"section": "disk", "items": items}

    # ── 3. 进程/内存/负载（/proc，纯只读）──────────────────────
    def check_processes(self, top_n: int = 5) -> dict:
        proc = Path("/proc")
        loadavg = ""
        try:
            loadavg = (proc / "loadavg").read_text().split()[:3]
        except Exception:
            loadavg = []

        mem: dict = {}
        try:
            mem = parse_meminfo((proc / "meminfo").read_text())
        except Exception:
            mem = {}
        mem_used_pct = None
        if mem.get("total_mb") and mem.get("available_mb") is not None:
            mem_used_pct = round(
                (mem["total_mb"] - mem["available_mb"]) / mem["total_mb"] * 100.0, 1)

        procs = []
        count = 0
        try:
            entries = list(proc.iterdir())
        except Exception:
            entries = []
        for entry in entries:
            if not entry.name.isdigit():
                continue
            count += 1
            rss_mb = 0.0
            name = ""
            try:
                name = (entry / "comm").read_text().strip()
            except Exception:
                pass
            try:
                # statm 第3列为常驻集页数
                pages = int((entry / "statm").read_text().split()[2])
                rss_mb = round(pages * 4096 / 1024 ** 2, 1)
            except Exception:
                pass
            procs.append({"pid": int(entry.name), "name": name, "rss_mb": rss_mb})
        procs.sort(key=lambda x: x["rss_mb"], reverse=True)

        status = "ok"
        if mem.get("available_mb") is not None:
            if mem["available_mb"] < MEM_CRIT_MB:
                status = "critical"
            elif mem["available_mb"] < MEM_WARN_MB:
                status = "warn"

        return {
            "section": "processes",
            "loadavg": loadavg,
            "memory": {**mem, "used_percent": mem_used_pct},
            "process_count": count,
            "top": procs[: max(1, int(top_n))],
            "status": status,
        }

    # ── 汇总 ──────────────────────────────────────────────────
    def run(self, section: str = "all") -> dict:
        """一键体检。section ∈ all/services/disk/processes。"""
        section = (section or "all").strip().lower()
        report = {"ts": time.time(), "section": section, "sections": {}}
        if section in ("all", "services"):
            report["sections"]["services"] = self.check_services()
        if section in ("all", "disk"):
            report["sections"]["disk"] = self.check_disk()
        if section in ("all", "processes"):
            report["sections"]["processes"] = self.check_processes()
        if not report["sections"]:
            report["error"] = f"未知 section: {section}（可选 all/services/disk/processes）"
        report["overall_status"] = self._overall(report["sections"])
        return report

    @staticmethod
    def _overall(sections: dict) -> str:
        """healthy / degraded / unhealthy 三态聚合。"""
        statuses = []
        svc = sections.get("services")
        if svc and svc.get("online", svc.get("total", 0)) < svc.get("total", 0):
            statuses.append("degraded")  # 有服务离线→降级（不直接判死）
        for d in sections.get("disk", {}).get("items", []):
            statuses.append(d.get("status", "ok"))
        p = sections.get("processes")
        if p:
            statuses.append(p.get("status", "ok"))
        if "critical" in statuses:
            return "unhealthy"
        if "warn" in statuses or "degraded" in statuses:
            return "degraded"
        return "healthy"

    # ── 人类可读报告 ──────────────────────────────────────────
    @staticmethod
    def format_report(report: dict) -> str:
        lines = [f"🩺 系统体检报告  总体状态: {report.get('overall_status', 'unknown')}"]
        svc = report.get("sections", {}).get("services")
        if svc:
            lines.append(f"\n## 服务（{svc['online']}/{svc['total']} 在线）")
            for it in svc["items"]:
                mark = "✅" if it["listening"] else "❌"
                lines.append(f"- {mark} {it['name']} :{it['port']} "
                             f"{'在线' if it['listening'] else '离线'}")
        disk = report.get("sections", {}).get("disk")
        if disk:
            lines.append("\n## 磁盘")
            for it in disk["items"]:
                mark = {"ok": "✅", "warn": "⚠️", "critical": "🔴"}.get(it["status"], "·")
                lines.append(
                    f"- {mark} {it['path']}: {it['used_gb']}G/{it['total_gb']}G "
                    f"（{it['used_percent']}%，剩 {it['free_gb']}G）")
        proc = report.get("sections", {}).get("processes")
        if proc:
            lines.append("\n## 进程与内存")
            if proc.get("loadavg"):
                lines.append(f"- 负载(1/5/15min): {' / '.join(proc['loadavg'])}")
            m = proc.get("memory") or {}
            if m.get("total_mb"):
                lines.append(
                    f"- 内存: {m['total_mb'] / 1024:.1f}G 总 / "
                    f"{m.get('available_mb', 0) / 1024:.1f}G 可用"
                    f"（{m.get('used_percent', '?')}% 用）")
            lines.append(f"- 进程数: {proc.get('process_count', 0)}")
            if proc.get("top"):
                lines.append("- 内存占用Top:")
                for p in proc["top"]:
                    lines.append(f"  - {p['name']} (pid {p['pid']}): {p['rss_mb']}MB")
        if report.get("error"):
            lines.append(f"\n⚠️ {report['error']}")
        return "\n".join(lines)

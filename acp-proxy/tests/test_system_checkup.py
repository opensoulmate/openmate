# -*- coding: utf-8 -*-
"""路线图 2.4 系统状态一键体检——契约测试。

覆盖：
1. probe_port：真实监听端口=在线，关闭端口=离线（不外抛）
2. disk_status 阈值纯函数：ok/warn/critical 三段
3. parse_meminfo：fixture 文本解析 MB
4. check_disk 真实文件系统：字段齐全、百分比∈[0,100]、同盘去重
5. check_processes 真实 /proc：进程数≥1、Top按RSS降序、内存字段
6. check_services：真实临时监听端口判在线 + 默认清单字段
7. run：section 过滤（disk only / 未知 section 报错）、overall_status 三态聚合
8. format_report：三段标题+总体状态+服务名可见
9. 接线护栏：soulmate_agent 真实路径含 SystemCheckup 初始化 + 工具定义 +
   主循环/code-mode 双 dispatch + 系统提示工具清单
"""
import socket
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.system_checkup import (  # noqa: E402
    SystemCheckup,
    disk_status,
    parse_meminfo,
    probe_port,
)

AGENTS = Path(__file__).resolve().parents[1] / "agent"


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    return s, port


# ── 1. probe_port ─────────────────────────────────────
def test_probe_port_up_and_down():
    srv, port = _free_port()
    try:
        srv.listen(1)
        assert probe_port(port, timeout=1.0) is True
    finally:
        srv.close()
    assert probe_port(port, timeout=0.5) is False  # 关闭后离线
    assert probe_port(-1) is False  # 非法端口不外抛


# ── 2. disk_status 阈值 ────────────────────────────────
def test_disk_status_thresholds():
    assert disk_status(0.0) == "ok"
    assert disk_status(84.9) == "ok"
    assert disk_status(85.0) == "warn"
    assert disk_status(94.9) == "warn"
    assert disk_status(95.0) == "critical"
    assert disk_status(100.0) == "critical"


# ── 3. parse_meminfo ───────────────────────────────────
def test_parse_meminfo_fixture():
    text = (
        "MemTotal:       16384000 kB\n"
        "MemFree:         2048000 kB\n"
        "MemAvailable:    8192000 kB\n"
        "Buffers:          512000 kB\n"
    )
    got = parse_meminfo(text)
    assert got["total_mb"] == 16000.0
    assert got["free_mb"] == 2000.0
    assert got["available_mb"] == 8000.0
    assert "buffers_mb" not in got  # 只取体检字段


# ── 4. check_disk 真实文件系统 ─────────────────────────
def test_check_disk_real_and_dedupe(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    cu = SystemCheckup()
    got = cu.check_disk(paths=["/", str(tmp_path), str(sub)])
    assert got["section"] == "disk"
    assert got["items"], "磁盘条目不应为空"
    for it in got["items"]:
        for k in ("path", "total_gb", "used_gb", "free_gb", "used_percent", "status"):
            assert k in it
        assert 0.0 <= it["used_percent"] <= 100.0
        assert it["status"] in ("ok", "warn", "critical")
    # tmp_path 与 sub 同盘 → 去重只留一个
    tmp_entries = [i for i in got["items"] if str(tmp_path) in i["path"]]
    assert len(tmp_entries) == 1


# ── 5. check_processes 真实 /proc ──────────────────────
def test_check_processes_real():
    cu = SystemCheckup()
    got = cu.check_processes(top_n=3)
    assert got["section"] == "processes"
    assert got["process_count"] >= 1
    assert got["loadavg"], "loadavg 应可读取"
    assert got["memory"].get("total_mb", 0) > 0
    top = got["top"]
    assert 1 <= len(top) <= 3
    rss = [p["rss_mb"] for p in top]
    assert rss == sorted(rss, reverse=True)
    assert got["status"] in ("ok", "warn", "critical")


# ── 6. check_services ─────────────────────────────────
def test_check_services_with_real_listener():
    srv, port = _free_port()
    try:
        srv.listen(1)
        cu = SystemCheckup()
        got = cu.check_services(services=[("测试服务", port), ("幽灵服务", port + 1)])
    finally:
        srv.close()
    assert got["online"] == 1
    assert got["total"] == 2
    assert got["items"][0]["listening"] is True
    assert got["items"][1]["listening"] is False
    assert got["items"][1]["status"] == "down"


def test_default_services_list_used():
    got = SystemCheckup().check_services()
    names = [i["name"] for i in got["items"]]
    assert "OpenSoul后端" in names and "ACP实例A" in names
    assert got["total"] == len(got["items"])


# ── 7. run + overall ───────────────────────────────────
def test_run_section_filter_and_overall():
    cu = SystemCheckup()
    disk_only = cu.run(section="disk")
    assert set(disk_only["sections"].keys()) == {"disk"}
    assert disk_only["overall_status"] in ("healthy", "degraded", "unhealthy")

    bad = cu.run(section="nope")
    assert "error" in bad and not bad["sections"]

    full = cu.run()
    assert set(full["sections"].keys()) == {"services", "disk", "processes"}
    assert full["overall_status"] in ("healthy", "degraded", "unhealthy")


def test_overall_aggregation_three_states():
    ok = {"disk": {"items": [{"status": "ok"}]}, "processes": {"status": "ok"},
          "services": {"online": 2, "total": 2}}
    warn = {"disk": {"items": [{"status": "warn"}]}}
    crit = {"disk": {"items": [{"status": "critical"}]}}
    down = {"services": {"online": 1, "total": 2}}
    assert SystemCheckup._overall(ok) == "healthy"
    assert SystemCheckup._overall(warn) == "degraded"
    assert SystemCheckup._overall(down) == "degraded"
    assert SystemCheckup._overall(crit) == "unhealthy"


# ── 8. format_report ───────────────────────────────────
def test_format_report_sections_visible():
    report = SystemCheckup().run()
    text = SystemCheckup.format_report(report)
    assert "系统体检报告" in text
    assert f"总体状态: {report['overall_status']}" in text
    assert "## 服务" in text and "OpenSoul后端" in text
    assert "## 磁盘" in text
    assert "## 进程与内存" in text


# ── 9. 接线护栏 ────────────────────────────────────────
def test_wiring_in_soulmate_agent():
    src = (AGENTS / "soulmate_agent.py").read_text(encoding="utf-8")
    assert "from agent.system_checkup import SystemCheckup" in src
    assert "self._system_checkup = SystemCheckup()" in src
    assert '"name": "system_checkup"' in src
    assert "def _system_checkup_dispatch(self, func_args: dict) -> str:" in src
    # 主循环 + code-mode 内层双 dispatch 各一次
    assert src.count("self._system_checkup_dispatch(func_args)") == 2
    assert 'elif func_name == "system_checkup":' in src
    assert 'if func_name == "system_checkup":' in src
    # 系统提示工具清单
    assert "- system_checkup: 系统状态一键体检" in src


def test_dispatch_lazy_build_and_report():
    # 兜底懒建（build_env 手工搭 agent 无 _system_checkup 也不崩）+ 真实报告输出
    from agent.soulmate_agent import SoulMateAgent
    bare = SoulMateAgent.__new__(SoulMateAgent)  # 不跑 __init__，无 _system_checkup
    out = SoulMateAgent._system_checkup_dispatch(bare, {"section": "disk"})
    assert "系统体检报告" in out and "## 磁盘" in out
    assert isinstance(bare._system_checkup, SystemCheckup)  # 懒建兜底生效
    out2 = bare._system_checkup_dispatch({"section": "processes"})
    assert "## 进程与内存" in out2

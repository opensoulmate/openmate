"""路线图 2.4 系统状态感知——SystemHealthChecker 单测 + 接线护栏

覆盖：
1. services：active/activating/inactive/failed/unknown 五态映射
2. ports：监听中 / 未监听 / 格式非法
3. disk：ok/warn/critical 阈值 + 读取失败 → unknown
4. memory：/proc/meminfo 解析（mem+swap）+ 文件缺失 → unknown
5. load：load1 vs cpu数 三档阈值
6. processes：ps 输出解析（内存TopN + 僵尸计数）+ 空输出 → unknown
6b. run：sections 过滤 + 未知分区忽略 + 单项异常不炸整轮 + overall 最差值
7. env 覆盖（SOULMATE_HEALTH_SERVICES/PATHS/PORTS）
8. 接线护栏：soulmate_agent 工具面/主dispatch/code-mode dispatch/系统提示
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.system_health import (  # noqa: E402
    CheckResult,
    HealthReport,
    SystemHealthChecker,
)


def _checker(**kw):
    return SystemHealthChecker(
        services=kw.pop("services", ("svc-a",)),
        paths=kw.pop("paths", ("/",)),
        ports=kw.pop("ports", ("8090=opensoul",)),
        **kw,
    )


# ── 1. services ─────────────────────────────────────────────────

def test_services_state_mapping():
    states = {"svc-ok": "active", "svc-start": "activating",
              "svc-dead": "failed", "svc-off": "inactive", "svc-x": "garbage"}
    c = _checker(services=tuple(states),
                 service_runner=lambda s: states[s])
    out = {r.name: r.status for r in c.check_services()}
    assert out == {"svc-ok": "ok", "svc-start": "warn",
                   "svc-dead": "critical", "svc-off": "critical",
                   "svc-x": "unknown"}


def test_services_runner_exception_becomes_unknown():
    def boom(_):
        raise RuntimeError("systemctl 挂了")
    c = _checker(services=("svc",), service_runner=boom)
    r = c.check_services()[0]
    assert r.status == "unknown"


# ── 2. ports ────────────────────────────────────────────────────

def test_ports_listening_and_dead_and_bad_spec():
    c = _checker(ports=("8090=opensoul", "8095=acp-b", "abc=bad"),
                 port_checker=lambda p: p == 8090)
    out = {r.name: (r.status, r.detail) for r in c.check_ports()}
    assert out["8090（opensoul）"][0] == "ok"
    assert out["8095（acp-b）"][0] == "critical"
    assert out["abc=bad"][0] == "unknown"


# ── 3. disk ─────────────────────────────────────────────────────

def test_disk_thresholds_ok_warn_critical():
    usages = {"/ok": (100, 50), "/warn": (100, 90), "/crit": (100, 97)}
    c = _checker(paths=("/ok", "/warn", "/crit"),
                 disk_usage_fn=lambda p: usages[p])
    out = {r.name: (r.status, r.detail) for r in c.check_disk()}
    assert out["/ok"][0] == "ok" and "50.0%" in out["/ok"][1]
    assert out["/warn"][0] == "warn"
    assert out["/crit"][0] == "critical"


def test_disk_read_failure_unknown():
    def boom(_):
        raise OSError("no such path")
    r = _checker(paths=("/gone",), disk_usage_fn=boom).check_disk()[0]
    assert r.status == "unknown" and "读取失败" in r.detail


# ── 4. memory ───────────────────────────────────────────────────

_MEMINFO = """MemTotal:        8000000 kB
MemAvailable:    2000000 kB
SwapTotal:       4000000 kB
SwapFree:        3000000 kB
"""


def test_memory_parse_mem_and_swap(tmp_path):
    p = tmp_path / "meminfo"
    p.write_text(_MEMINFO, encoding="utf-8")
    c = _checker(meminfo_path=str(p))
    out = {r.name: (r.status, r.detail) for r in c.check_memory()}
    # used = 6/8 = 75% → ok
    assert out["mem"][0] == "ok" and "75.0%" in out["mem"][1]
    assert out["swap"][0] == "ok" and "25.0%" in out["swap"][1]


def test_memory_high_usage_warns(tmp_path):
    p = tmp_path / "meminfo"
    p.write_text("MemTotal: 1000 kB\nMemAvailable: 100 kB\n", encoding="utf-8")
    r = _checker(meminfo_path=str(p)).check_memory()[0]
    assert r.status == "warn" and "90.0%" in r.detail


def test_memory_missing_file_unknown(tmp_path):
    c = _checker(meminfo_path=str(tmp_path / "nope"))
    r = c.check_memory()[0]
    assert r.status == "unknown"


# ── 5. load ─────────────────────────────────────────────────────

def test_load_thresholds():
    c4 = _checker(cpu_count=4, load_fn=lambda: (2.0, 1.5, 1.0))
    assert c4.check_load()[0].status == "ok"
    c4w = _checker(cpu_count=4, load_fn=lambda: (4.5, 3.0, 2.0))
    assert c4w.check_load()[0].status == "warn"
    c4c = _checker(cpu_count=4, load_fn=lambda: (9.0, 6.0, 3.0))
    assert c4c.check_load()[0].status == "critical"


def test_load_read_failure_unknown():
    def boom():
        raise OSError("no /proc/loadavg")
    r = _checker(load_fn=boom).check_load()[0]
    assert r.status == "unknown"


# ── 6. processes ────────────────────────────────────────────────

_PS = """    PID STAT COMM     %MEM %CPU
    100 S    python3   4.2 10.0
    200 S    node     40.5 55.0
    300 Z    defunct   0.0  0.0
    400 Z    defunct   0.0  0.0
    500 S    bash      0.1  0.0
"""


def test_processes_top_mem_and_zombies():
    c = _checker(ps_runner=lambda: _PS)
    out = {r.name: (r.status, r.detail) for r in c.check_processes()}
    assert out["top-mem"][0] == "ok"
    assert "共5个进程" in out["top-mem"][1]
    assert out["top-mem"][1].count("pid") == 5  # PROC_TOP_N=5 全列
    assert "zombie" not in out["top-mem"][1]
    assert out["zombie"] == ("warn", "2个僵尸进程")


def test_processes_empty_output_unknown():
    r = _checker(ps_runner=lambda: "").check_processes()[0]
    assert r.status == "unknown"


# ── 6b. run / overall / sections 过滤 ───────────────────────────

def test_run_sections_filter_and_unknown_section_ignored():
    usages = {"/": (100, 50)}
    c = _checker(paths=("/",), disk_usage_fn=lambda p: usages[p],
                 port_checker=lambda p: True,
                 service_runner=lambda s: "active",
                 meminfo_path="/nonexistent-meminfo",
                 load_fn=lambda: (0.1, 0.1, 0.1), cpu_count=4,
                 ps_runner=lambda: _PS)
    report = c.run(["disk"])
    assert [r.section for r in report.results] == ["disk"]
    # 未知分区名被忽略，不进报告
    report2 = c.run(["disk", "bogus-section"])
    assert [r.section for r in report2.results] == ["disk"]


def test_run_single_section_exception_does_not_kill_run():
    def boom(_):
        raise RuntimeError("炸")
    c = _checker(paths=("/",), disk_usage_fn=boom,
                 service_runner=lambda s: "active")
    report = c.run(["services", "disk"])
    by_section = {r.section: r.status for r in report.results}
    assert by_section["services"] == "ok"
    assert by_section["disk"] == "unknown"  # 异常只降级，不炸整轮


def test_overall_worst_wins_and_format_contains_summary():
    report = HealthReport(results=[
        CheckResult("disk", "/", "ok", "fine"),
        CheckResult("services", "x", "warn", "activating"),
        CheckResult("ports", "8090", "critical", "未监听"),
    ])
    assert report.overall == "critical"
    text = report.format()
    assert "🩺 系统体检" in text and "critical" in text
    assert "[disk]" in text and "[services]" in text and "[ports]" in text
    assert report.counts() == {"ok": 1, "warn": 1, "critical": 1, "unknown": 0}


def test_execute_default_all_sections():
    usages = {"/": (100, 10), "/home": (100, 20)}
    c = SystemHealthChecker(
        services=("svc",), paths=("/", "/home"),
        ports=("8090=x",),
        service_runner=lambda s: "active",
        port_checker=lambda p: True,
        disk_usage_fn=lambda p: usages[p],
        load_fn=lambda: (0.01, 0.01, 0.01), cpu_count=8,
        ps_runner=lambda: _PS,
    )
    text = c.execute()  # 缺省全部六分区（memory 走真实 /proc/meminfo）
    for marker in ("[services]", "[ports]", "[disk]", "[memory]", "[load]",
                   "[processes]"):
        assert marker in text


# ── 7. env 覆盖 ─────────────────────────────────────────────────

def test_env_overrides(monkeypatch):
    monkeypatch.setenv("SOULMATE_HEALTH_SERVICES", "a, b ,c")
    monkeypatch.setenv("SOULMATE_HEALTH_PATHS", "/tmp,/var")
    monkeypatch.setenv("SOULMATE_HEALTH_PORTS", "9000=custom")
    c = SystemHealthChecker()
    assert c.services == ("a", "b", "c")
    assert c.paths == ("/tmp", "/var")
    assert c.ports == ("9000=custom",)


# ── 8. 接线护栏（soulmate_agent 真实路径）────────────────────────

def _agent_src():
    return (Path(__file__).resolve().parents[1] / "agent" / "soulmate_agent.py").read_text(encoding="utf-8")


def test_wiring_tool_registered_in_builtin_tools():
    src = _agent_src()
    assert '"name": "system_checkup"' in src
    assert "builtin_tools.append(checkup_tool)" in src


def test_wiring_main_dispatch_branch():
    src = _agent_src()
    main_start = src.index('elif func_name == "system_checkup":')
    assert "self._system_health_dispatch(func_args)" in src[main_start:main_start + 400]


def test_wiring_code_mode_dispatch():
    src = _agent_src()
    cm_start = src.index("async def _code_mode_tool_call")
    cm_end = src.index("async def _traj_record_round")
    cm_src = src[cm_start:cm_end]
    assert 'if func_name == "system_checkup":' in cm_src
    assert "self._system_health_dispatch(func_args)" in cm_src


def test_wiring_shared_dispatch_and_prompt_list():
    src = _agent_src()
    assert "def _system_health_dispatch" in src  # 主循环/code-mode 共用入口
    assert "SystemHealthChecker()" in src        # lazy-init 实际构造
    assert "system_checkup: 系统状态一键体检" in src  # 系统提示工具清单

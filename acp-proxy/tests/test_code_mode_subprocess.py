# -*- coding: utf-8 -*-
"""路线图 14.1 — Code Mode 脚本执行进程级隔离（周期#84）。

背景（周期#83 实证）：吞异常死循环（while True: try/except pass）的 worker 线程
持 GIL 自旋，PyThreadState_SetAsyncExc 注入的退出异常被脚本吞掉收不掉——残留
线程把同进程所有线程拖慢单倍~百倍（0.14s 目录 walk 变 60s+）。真解=脚本执行搬
独立子进程（agent/code_mode_worker.py，browser_worker 同款 JSONL/子进程模式）。

验证三层：
1. GIL 面消失：吞异常自旋脚本批超时被 kill 期间，父进程 CPU 任务不受拖慢
2. 语义等价迁移：批级超时部分产物不丢（result 流式快照+call_log）、单次调用
   超时标记、调用上限熔断、参数校验、异常可见、stdout/result 输出优先级
3. 进程卫生：批超时后无 code_mode_worker 残留进程（killpg 进程组）；
   SOULMATE_CODE_MODE_ISOLATION=thread 旧线程模式回退仍可用
"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.code_mode import CodeModeExecutor
from tests.test_steering import run


def _fake_dispatch(calls: list):
    async def dispatch(name, args):
        calls.append((name, args))
        return f"r:{name}"
    return dispatch


def _worker_procs() -> list:
    """扫 /proc 找存活的 code_mode_worker 子进程（进程卫生断言用）。"""
    found = []
    for p in Path("/proc").iterdir():
        if not p.name.isdigit():
            continue
        try:
            cmd = (p / "cmdline").read_bytes().decode("utf-8", "replace")
        except Exception:
            continue
        if "code_mode_worker" in cmd:
            found.append(int(p.name))
    return found


# ════════════════════════════════════════════════════════════════
# 1. GIL 面消失 — 周期#83 事故形态（吞异常死循环）不再楔死父进程
# ════════════════════════════════════════════════════════════════
class TestGilIsolation:
    def test_swallowing_spinner_does_not_wedge_parent(self):
        """while True + try/except（线程回收收不掉的形态）：父进程保持响应力"""
        calls = []
        ex = CodeModeExecutor(batch_timeout=1.2)
        script = (
            "result = read_file(path='/done-first')\n"
            "while True:\n"
            "    try:\n"
            "        pass\n"
            "    except BaseException:\n"
            "        pass\n"
        )

        async def _probe(_ex, _script, _calls):
            async def probe():
                await asyncio.sleep(0.4)  # 让子进程进入自旋
                t = time.monotonic()
                x = 0
                for i in range(300_000):  # 纯 CPU 小任务：GIL 被楔时 ~100 倍变慢
                    x += i
                return time.monotonic() - t
            res, probe_s = await asyncio.gather(
                _ex.execute(_script, ["read_file"], _fake_dispatch(_calls)), probe())
            return res, probe_s

        t0 = time.monotonic()
        res, probe_s = run(_probe(ex, script, calls))
        elapsed = time.monotonic() - t0
        assert res.timed_out is True            # 批级超时语义不变
        assert "CODE_MODE_TIMEOUT" in res.error
        assert probe_s < 1.0                    # 父进程 CPU 任务未被拖慢（GIL 面消失）
        assert elapsed < 8.0                    # 有界退出

    def test_no_orphan_worker_after_timeout(self):
        """批超时 kill 进程组：无 code_mode_worker 残留进程"""
        before = set(_worker_procs())
        ex = CodeModeExecutor(batch_timeout=0.8)
        res = run(ex.execute("while True:\n    pass\n", [],
                             lambda n, a: asyncio.sleep(0)))
        assert res.timed_out is True
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            leftover = set(_worker_procs()) - before
            if not leftover:
                break
            time.sleep(0.1)
        assert set(_worker_procs()) - before == set()


# ════════════════════════════════════════════════════════════════
# 2. 语义等价迁移（对照线程模式契约）
# ════════════════════════════════════════════════════════════════
class TestProcessSemanticsParity:
    def test_partial_result_log_and_output_on_kill(self):
        """批超时被杀：已完成调用的 call_log + result 流式快照不丢（线程模式同款）"""
        calls = []
        ex = CodeModeExecutor(batch_timeout=1.0)
        script = (
            "print('pre-output')\n"
            "result = read_file(path='/done-first')\n"
            "while True:\n"
            "    pass\n"
        )
        res = run(ex.execute(script, ["read_file"], _fake_dispatch(calls)))
        assert res.timed_out is True
        assert res.ok is False
        assert len(res.call_log) == 1
        assert res.call_log[0]["ok"] is True
        assert res.call_log[0]["name"] == "read_file"
        assert res.output == "r:read_file"      # result 变量赋值即时快照保留
        assert len(calls) == 1

    def test_stdout_streamed_on_kill_when_no_result_var(self):
        """无 result 变量时 stdout 也是流式快照：被杀前的 print 不丢"""
        ex = CodeModeExecutor(batch_timeout=1.0)
        script = "print('partial-print')\nwhile True:\n    pass\n"
        res = run(ex.execute(script, [], lambda n, a: asyncio.sleep(0)))
        assert res.timed_out is True
        assert res.output == "partial-print"

    def test_per_call_timeout_marker_script_continues(self):
        """单次调用超时→标记回传脚本可继续，call_log 记 call_timeout（同线程模式）"""
        calls = []

        async def slow_dispatch(name, args):
            calls.append(name)
            if name == "slow_tool":
                await asyncio.sleep(2.0)
            return f"r:{name}"

        ex = CodeModeExecutor(call_timeout=0.3, batch_timeout=10.0)
        script = (
            "a = slow_tool(p=1)\n"
            "b = read_file(path='/x')\n"
            "result = a + '|' + b\n"
        )
        res = run(ex.execute(script, ["slow_tool", "read_file"], slow_dispatch))
        assert res.ok is True
        assert "超时" in res.output
        assert calls == ["slow_tool", "read_file"]
        assert res.call_log[0]["error"] == "call_timeout"
        assert res.call_log[0]["ok"] is False
        assert res.call_log[1]["ok"] is True

    def test_call_limit_circuit_breaker(self):
        calls = []
        ex = CodeModeExecutor(max_calls=3)
        script = (
            "for i in range(10):\n"
            "    terminal(command=f'cmd-{i}')\n"
            "result = 'unreachable'\n"
        )
        res = run(ex.execute(script, ["terminal"], _fake_dispatch(calls)))
        assert res.ok is False
        assert "上限3" in res.error
        assert res.calls_dispatched == 3
        assert len(calls) == 3
        assert len(res.call_log) == 3

    def test_kwargs_and_dict_positional_roundtrip(self):
        calls = []
        ex = CodeModeExecutor()
        script = (
            "a = read_file({'path': '/dict-arg'})\n"
            "b = read_file(path='/kw-arg')\n"
            "result = a + b\n"
        )
        res = run(ex.execute(script, ["read_file"], _fake_dispatch(calls)))
        assert res.ok is True
        assert calls[0][1] == {"path": "/dict-arg"}
        assert calls[1][1] == {"path": "/kw-arg"}
        assert [e["name"] for e in res.call_log] == ["read_file", "read_file"]

    def test_non_dict_positional_rejected_zero_dispatch(self):
        calls = []
        ex = CodeModeExecutor()
        res = run(ex.execute("read_file('/x')", ["read_file"], _fake_dispatch(calls)))
        assert res.ok is False
        assert "关键字" in res.error
        assert res.calls_dispatched == 0
        assert calls == []

    def test_result_var_wins_over_stdout(self):
        ex = CodeModeExecutor()
        res = run(ex.execute("print('noise')\nresult = 'final-value'\n",
                             [], _fake_dispatch([])))
        assert res.output == "final-value"

    def test_result_none_falls_back_to_stdout(self):
        """线程模式同款：result=None 视为未赋值，回落 stdout"""
        ex = CodeModeExecutor()
        res = run(ex.execute("print('fallback-out')\nresult = None\n",
                             [], _fake_dispatch([])))
        assert res.output == "fallback-out"

    def test_import_blocked_visible(self):
        ex = CodeModeExecutor()
        res = run(ex.execute("import os\nresult = os.getcwd()",
                             ["read_file"], _fake_dispatch([])))
        assert res.ok is False
        assert res.error
        res2 = run(ex.execute("__import__('subprocess')",
                              ["read_file"], _fake_dispatch([])))
        assert res2.ok is False
        assert "__import__" in res2.error

    def test_syntax_error_visible(self):
        ex = CodeModeExecutor()
        res = run(ex.execute("def broken(:\n    pass\n",
                             [], _fake_dispatch([])))
        assert res.ok is False
        assert "脚本异常" in res.error and "SyntaxError" in res.error

    def test_dispatch_exception_visible_in_output_and_log(self):
        calls = []

        async def broken(name, args):
            calls.append(name)
            raise RuntimeError("gate backend down")

        ex = CodeModeExecutor()
        res = run(ex.execute("result = terminal(command='echo x')",
                             ["terminal"], broken))
        assert res.ok is True
        assert "工具调用异常" in res.output
        assert "gate backend down" in res.output
        assert res.call_log[0]["ok"] is False
        assert "gate backend down" in res.call_log[0]["error"]

    def test_blocked_result_marks_blocked_entry(self):
        async def deny(name, args):
            return "[被拦截:deny] terminal"

        ex = CodeModeExecutor()
        res = run(ex.execute("result = terminal(command='x')",
                             ["terminal"], deny))
        assert res.call_log[0]["blocked"] is True
        assert "[被拦截:deny]" in res.output


# ════════════════════════════════════════════════════════════════
# 3. 进程卫生 + 隔离面路由
# ════════════════════════════════════════════════════════════════
class TestIsolationRouting:
    def test_default_isolation_is_process(self):
        """默认走子进程：日志行/独立进程证据（真跑一次批量化后无残留进程）"""
        before = set(_worker_procs())
        ex = CodeModeExecutor()
        res = run(ex.execute("result = read_file(path='/x')",
                             ["read_file"], _fake_dispatch([])))
        assert res.ok is True and res.output == "r:read_file"
        assert set(_worker_procs()) - before == set()  # 跑完即退，不留常驻

    def test_thread_fallback_still_works(self, monkeypatch):
        """SOULMATE_CODE_MODE_ISOLATION=thread 旧线程模式回退可用"""
        monkeypatch.setenv("SOULMATE_CODE_MODE_ISOLATION", "thread")
        calls = []
        ex = CodeModeExecutor()
        res = run(ex.execute("result = read_file(path='/x') + '-thread'",
                             ["read_file"], _fake_dispatch(calls)))
        assert res.ok is True
        assert res.output == "r:read_file-thread"
        assert len(res.call_log) == 1

    def test_thread_fallback_timeout_reclaims_spinner(self, monkeypatch):
        """线程模式下周期#83 回收机制仍在（保留路径不腐化）"""
        import threading
        monkeypatch.setenv("SOULMATE_CODE_MODE_ISOLATION", "thread")
        monkeypatch.setattr("agent.code_mode.RECLAIM_GRACE_S", 0.2)
        ex = CodeModeExecutor(batch_timeout=0.5)
        res = run(ex.execute("while True:\n    pass\n", [],
                             lambda n, a: asyncio.sleep(0)))
        assert res.timed_out is True
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            alive = [t for t in threading.enumerate()
                     if t.name == "code-mode-exec" and t.is_alive()]
            if not alive:
                break
            time.sleep(0.1)
        assert [t for t in threading.enumerate()
                if t.name == "code-mode-exec" and t.is_alive()] == []

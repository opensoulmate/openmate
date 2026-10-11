"""Code Mode工具批量化 — goose code_execution + kilocode code-mode（两方定案）

SUMMARY.md §五 cortex差距表 P1："Code Mode工具批量化（N次调用批成1个execute） |
goose code_execution+kilocode code-mode（两方定案）"。
63-goose-source-supplement6.md #4："全部MCP工具变成Deno沙箱内可调函数，N次工具调用
批成1个execute_typescript脚本 | P0省钱省轮次"。

设计（goose pctx工程教训 + kilocode code-mode语义）：
- 脚本在受限命名空间内 exec：每个会话工具名=一个stub函数，调用即 dispatch(name, args)
  （异步），返回工具结果字符串——N次工具调用只花1轮LLM round trip。
- stub是同步函数、脚本跑在worker线程（主事件循环不被脚本阻塞——goose
  spawn_blocking同款思路），stub经 asyncio.run_coroutine_threadsafe 桥接回主循环。
- 每次stub调用都必须经过调用方传入的dispatch（soulmate侧dispatch内含permission gate
  ——批量化绝不绕过权限引擎，每次内层调用独立过门禁）。
- 可观测（goose #5 tool_graph"批量化后仍能审计每步工具调用结构"的轻量版）：
  call_log逐条记录 {name, args_preview, ok, blocked, duration_ms, result_len, error}。
- 工程护栏（goose code_execution注释即教材："a hung script would wedge code execution
  for every session: bound the wait"）：
  * 批级超时（默认90s）：超时→cancel标记置位，worker里后续stub调用被拒绝不再产生
    副作用（AbortOnDrop语义），已完成的调用日志/部分结果照常返回
  * 挂死脚本残留线程回收（宽限期默认2s）：超时被放弃的worker线程若仍在自旋
    （如 while True: pass 持GIL楔死同进程其余线程），到点注入退出异常强制收口
    （fire-and-forget，脚本吞异常时如实告警不谎称回收成功）
  * 单次调用超时（默认30s）：stub返回超时标记，脚本可继续（单次挂死不楔住整批）
  * 批内调用数上限（默认40）：失控循环到顶即停，错误显式返回
  * 脚本异常→部分call_log+stdout+traceback全部显式返回（mem0 §1.1"失败必须可见，
    禁止静默降级"）
- 受限builtins：__import__不注入——批内脚本要跑任意代码应调用terminal/execute_code
  工具（这些工具本身仍过权限gate），而不是绕过权限层直接import subprocess。
  json/re/math三个纯计算模块可用。

进程级隔离（路线图14.1，周期#84）：脚本执行默认搬独立子进程
（agent/code_mode_worker.py，browser_worker 同款 JSONL/子进程模式）。周期#83
实证吞异常死循环（while True: try/except pass）连线程回收（PyThreadState_
SetAsyncExc）都收不掉——残留线程持 GIL 自旋把同进程所有线程拖慢单倍~百倍。
进程级隔离后 GIL 面彻底消失：批级超时=kill 进程组（连带清子进程），挂死只损失
本批；单次调用超时/调用上限/部分结果语义等价迁移（`result` 赋值与 print 即时
流式快照送出，被杀时部分产物不丢；内层工具仍由父进程 dispatch=不绕权限引擎）。
SOULMATE_CODE_MODE_ISOLATION=thread 回退旧线程模式（代码路径保留，测试/应急用），
默认 process。
"""

import asyncio
import ctypes
import io
import json
import logging
import math
import os
import re
import signal
import sys
import threading
import time
import traceback
from concurrent.futures import TimeoutError as FuturesTimeoutError
from contextlib import redirect_stdout
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("acp.code_mode")

_VALID_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class CodeModeCallLimit(Exception):
    """批内工具调用次数超过上限（失控循环熔断）。"""


class CodeModeCancelled(Exception):
    """批任务已被取消（批级超时后worker内后续stub调用直接拒绝）。"""


# 挂死脚本残留线程回收宽限期（秒），SOULMATE_CODE_MODE_RECLAIM_S 可配。
# 周期#83 实证：批超时放弃 worker 后，`while True: pass` 类自旋脚本线程持 GIL
# 不放，把同进程其他线程拖慢单倍~百倍（实测 0.14s 的目录 walk 变 60s+，
# 回合工具执行全链随之爬行）——"不楔住会话"只做了一半：有界等待做到了，
# 残留线程回收没做。到点注入异步异常强制收口（_run 的 except BaseException
# 接住→线程干净退出），fire-and-forget 不阻塞调用方。
RECLAIM_GRACE_S = float(os.environ.get("SOULMATE_CODE_MODE_RECLAIM_S", "2.0"))

# 脚本执行隔离面（路线图14.1）：process=独立子进程（默认，GIL面彻底消失）/
# thread=旧线程模式（保留代码路径，测试/应急用）。逐次读 env 便于测试与运维切换。
ISOLATION_ENV = "SOULMATE_CODE_MODE_ISOLATION"


def _isolation() -> str:
    v = str(os.environ.get(ISOLATION_ENV, "process") or "process").strip().lower()
    return "thread" if v in ("thread", "inproc", "in-process") else "process"


def _reclaim_hung_thread(
    thread: threading.Thread, grace: float = RECLAIM_GRACE_S,
) -> None:
    """回收批超时后被放弃的 worker 线程（Timer 线程内 fire-and-forget）。

    PyThreadState_SetAsyncExc 在字节码边界注入 SystemExit——_run 的
    except BaseException 收口后线程退出。脚本自行吞异常继续跑时回收失败，
    如实告警不谎称成功（daemon 线程，进程退出自然回收）。
    """
    def _fire() -> None:
        if not thread.is_alive() or thread.ident is None:
            return  # 已自行退出/未曾启动：无需回收
        try:
            res = ctypes.pythonapi.PyThreadState_SetAsyncExc(
                ctypes.c_ulong(thread.ident), ctypes.py_object(SystemExit))
        except Exception as e:  # ctypes 注入不可用等：如实告警
            logger.warning(f"[code-mode] 挂死脚本回收不可用: {e}")
            return
        if res == 0:
            return  # 注入时线程刚退出
        logger.warning(
            f"[code-mode] 挂死脚本残留自旋已回收（注入退出异常） thread={thread.name}")

    t = threading.Timer(grace, _fire)
    t.daemon = True
    t.start()


# 受限builtins（不含__import__——见模块docstring）
_SAFE_BUILTINS = {
    "abs": abs, "all": all, "any": any, "bool": bool, "dict": dict,
    "enumerate": enumerate, "filter": filter, "float": float, "format": format,
    "int": int, "isinstance": isinstance, "len": len, "list": list, "map": map,
    "max": max, "min": min, "next": next, "print": print, "range": range,
    "repr": repr, "reversed": reversed, "round": round, "set": set,
    "sorted": sorted, "str": str, "sum": sum, "tuple": tuple, "type": type,
    "zip": zip, "Exception": Exception, "RuntimeError": RuntimeError,
    "ValueError": ValueError, "TypeError": TypeError, "KeyError": KeyError,
    "NameError": NameError, "StopIteration": StopIteration,
}

# 注入工具stub时不得覆盖的安全键（工具名与内建名冲突时保护内建语义）
_PROTECTED_NAMES = set(_SAFE_BUILTINS) | {"json", "re", "math", "result", "tool_names"}


@dataclass
class CodeModeResult:
    ok: bool
    output: str = ""
    call_log: list = field(default_factory=list)
    error: str = ""
    timed_out: bool = False
    duration_ms: int = 0
    calls_dispatched: int = 0


class CodeModeExecutor:
    """把N次工具调用批成1次脚本执行的Code Mode执行器。

    execute()是唯一入口：传入LLM生成的Python脚本、当前会话可用工具名列表、
    内层dispatch回调（async (name, args) -> str）。
    """

    def __init__(
        self,
        max_calls: int = 40,
        batch_timeout: float = 90.0,
        call_timeout: float = 30.0,
    ):
        self.max_calls = int(max_calls)
        self.batch_timeout = float(batch_timeout)
        self.call_timeout = float(call_timeout)

    # ── namespace/stub构建 ────────────────────────────────────────
    def _make_stub(self, tool_name: str, dispatch, loop, state: dict):
        """工具stub：同步函数，跨线程桥接异步dispatch，逐条记call_log。"""
        call_timeout = self.call_timeout

        def _stub(*args, **kwargs):
            if state["cancel"].is_set():
                raise CodeModeCancelled(
                    "批任务已取消（批级超时），后续工具调用不再执行")
            if state["calls"] >= self.max_calls:
                raise CodeModeCallLimit(
                    f"批内工具调用超过上限{self.max_calls}次（失控循环熔断）")
            # 参数规范化：kwargs即工具参数；单个dict位置参数也接受；
            # 其余位置参数不猜测语义，显式报错（AIHawk：截断/异常必须显式标记）
            if len(args) == 1 and isinstance(args[0], dict) and not kwargs:
                targs = dict(args[0])
            elif args:
                raise TypeError(
                    f"{tool_name}() 参数必须用关键字（如 {tool_name}(path='...')）"
                    "或单个dict")
            else:
                targs = dict(kwargs)
            state["calls"] += 1
            entry = {
                "name": tool_name,
                "args_preview": json.dumps(targs, ensure_ascii=False)[:200],
                "ok": False, "blocked": False,
                "duration_ms": 0, "result_len": 0, "error": "",
            }
            state["call_log"].append(entry)
            t0 = time.monotonic()
            try:
                fut = asyncio.run_coroutine_threadsafe(dispatch(tool_name, targs), loop)
                result = fut.result(timeout=call_timeout)
            except FuturesTimeoutError:
                entry["error"] = "call_timeout"
                result = f"[CODE_MODE] 工具调用超时({call_timeout}s): {tool_name}"
            except Exception as e:  # dispatch内部异常（含gate故障降级）
                entry["error"] = str(e)[:200]
                result = f"[CODE_MODE] 工具调用异常: {tool_name}: {e}"
            else:
                entry["ok"] = True
            entry["duration_ms"] = int((time.monotonic() - t0) * 1000)
            text = str(result) if result is not None else ""
            entry["result_len"] = len(text)
            if text.startswith("[被拦截"):
                # gate正常工作（deny）不是执行错误——标记blocked供审计
                entry["blocked"] = True
            return text

        _stub.__name__ = tool_name
        return _stub

    def build_namespace(self, available_tools, dispatch, loop, state: dict) -> dict:
        """受限执行命名空间：安全builtins + json/re/math + 工具stub。"""
        ns: dict = {
            "__builtins__": dict(_SAFE_BUILTINS),
            "json": json, "re": re, "math": math,
        }
        injected = []
        for name in available_tools or []:
            name = str(name)
            if not _VALID_IDENT.match(name):
                logger.warning(f"[code-mode] 跳过非法工具标识符: {name!r}")
                continue
            if name in _PROTECTED_NAMES:
                logger.warning(f"[code-mode] 工具名与安全键冲突，跳过注入: {name}")
                continue
            ns[name] = self._make_stub(name, dispatch, loop, state)
            injected.append(name)
        ns["tool_names"] = injected
        return ns

    # ── 执行入口 ──────────────────────────────────────────────────
    async def execute(self, script: str, available_tools, dispatch) -> CodeModeResult:
        """执行批处理脚本。dispatch: async (name: str, args: dict) -> str。

        隔离面路由（路线图14.1）：默认 process（子进程，GIL面消失）；
        SOULMATE_CODE_MODE_ISOLATION=thread 走旧线程模式。
        """
        if _isolation() == "thread":
            return await self._execute_in_thread(script, available_tools, dispatch)
        return await self._execute_in_process(script, available_tools, dispatch)

    async def _execute_in_thread(self, script: str, available_tools, dispatch) -> CodeModeResult:
        """旧线程模式（SOULMATE_CODE_MODE_ISOLATION=thread）：保留语义与回收机制。"""
        loop = asyncio.get_running_loop()
        state = {
            "call_log": [],
            "calls": 0,
            "cancel": threading.Event(),  # 批级取消：超时后stub直接拒绝
        }
        ns = self.build_namespace(available_tools, dispatch, loop, state)
        stdout_buf = io.StringIO()
        error = ""
        ok = False
        timed_out = False
        t0 = time.monotonic()

        # daemon线程执行脚本（不用run_in_executor：默认executor非daemon线程在
        # asyncio.run收尾时会被join——挂死脚本会永久阻塞解释器退出；goose教训：
        # "a hung script would wedge code execution for every session"）
        exec_fut: asyncio.Future = loop.create_future()

        def _set_res(_fut, _exc):
            if _fut.done():
                return
            if _exc is None:
                _fut.set_result(None)
            else:
                _fut.set_exception(_exc)

        def _run():
            exc = None
            try:
                compiled = compile(script or "", "<code_mode>", "exec")
                with redirect_stdout(stdout_buf):
                    exec(compiled, ns)  # noqa: S102 — 受限ns，见模块docstring
            except BaseException as e:  # 含SyntaxError，全部转future异常
                exc = e
            try:
                loop.call_soon_threadsafe(_set_res, exec_fut, exc)
            except RuntimeError:
                pass  # 事件循环已关闭：结果丢弃，daemon线程直接退出

        worker = threading.Thread(target=_run, daemon=True, name="code-mode-exec")
        worker.start()
        try:
            await asyncio.wait_for(asyncio.shield(exec_fut), timeout=self.batch_timeout)
            ok = True
        except asyncio.TimeoutError:
            state["cancel"].set()  # AbortOnDrop：worker里后续stub不再dispatch
            _reclaim_hung_thread(worker, grace=RECLAIM_GRACE_S)  # 残留自旋线程到点回收（GIL楔死另一半）
            timed_out = True
            error = (f"[CODE_MODE_TIMEOUT] 批执行超过{self.batch_timeout}s已取消；"
                     "已完成的工具调用见日志，后续调用已阻断")
        except CodeModeCallLimit as e:
            error = f"[CODE_MODE] {e}"
        except CodeModeCancelled:
            error = "[CODE_MODE] 批任务已被取消"
        except BaseException as e:  # 脚本异常也必须显式可见（含SyntaxError）
            error = (f"[CODE_MODE] 脚本异常: {e}\n"
                     f"{traceback.format_exc(limit=6)}")
        duration_ms = int((time.monotonic() - t0) * 1000)
        # result变量优先，否则stdout（print被捕获）
        ns_result = ns.get("result")
        output = str(ns_result) if ns_result is not None else stdout_buf.getvalue().strip()
        res = CodeModeResult(
            ok=ok,
            output=output[:8000],
            call_log=list(state["call_log"]),
            error=error,
            timed_out=timed_out,
            duration_ms=duration_ms,
            calls_dispatched=state["calls"],
        )
        logger.info(
            f"[code-mode] ok={res.ok} calls={res.calls_dispatched} "
            f"logged={len(res.call_log)} {res.duration_ms}ms"
            f"{' timed_out' if res.timed_out else ''}")
        return res

    # ── 子进程隔离执行（路线图14.1，默认路径）────────────────────
    async def _execute_in_process(self, script: str, available_tools, dispatch) -> CodeModeResult:
        """脚本在独立子进程执行（agent/code_mode_worker.py JSONL 协议）。

        语义等价迁移对照线程模式：
        - 内层工具：子进程 stub 发 tool_call → 本进程 dispatch（含 permission gate）
          → 回传 tool_result；单次调用超时在本进程判定（ok/error 标记随结果回传）。
        - 批级超时：kill 进程组（start_new_session+killpg），AbortOnDrop 语义——
          后续 stub 不再执行；call_log/输出/result 经子进程流式快照保留（部分结果不丢）。
        - 调用上限/参数校验/异常可见：子进程同款语义（见 code_mode_worker.py）。
        """
        t0 = time.monotonic()
        worker_path = Path(__file__).resolve().parent / "code_mode_worker.py"

        async def _write(obj: dict) -> bool:
            try:
                proc.stdin.write(
                    (json.dumps(obj, ensure_ascii=False, default=str) + "\n").encode("utf-8"))
                await proc.stdin.drain()
                return True
            except Exception:
                return False

        async def _kill_proc() -> None:
            # killpg 连带清掉脚本可能留下的子进程（进程组=start_new_session 独立）
            if proc.returncode is not None:
                return
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                try:
                    proc.kill()
                except Exception:
                    pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass

        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, str(worker_path),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(Path(__file__).resolve().parent.parent),
                start_new_session=True,
            )
        except Exception as e:
            return CodeModeResult(
                ok=False, error=f"[CODE_MODE] 子进程启动失败: {e}",
                duration_ms=int((time.monotonic() - t0) * 1000))
        assert proc.stdin is not None and proc.stdout is not None \
            and proc.stderr is not None  # PIPE 模式恒非空（类型收窄）

        call_log: dict = {}          # seq → entry（开始/结束两次快照 upsert）
        stdout_parts: list = []
        state_results: list = []     # `result` 变量流式快照（最新为准）
        final_fut: asyncio.Future = asyncio.get_running_loop().create_future()

        async def _pump() -> None:
            try:
                while True:
                    line = await proc.stdout.readline()
                    if not line:
                        break
                    try:
                        msg = json.loads(line)
                    except Exception:
                        continue
                    typ = msg.get("type")
                    if typ == "tool_call":
                        seq = msg.get("seq")
                        name = str(msg.get("name", ""))
                        args = msg.get("args") or {}
                        ok, err = True, ""
                        try:
                            r = await asyncio.wait_for(
                                dispatch(name, args), timeout=self.call_timeout)
                        except asyncio.TimeoutError:
                            ok, err = False, "call_timeout"
                            r = (f"[CODE_MODE] 工具调用超时({self.call_timeout}s): {name}")
                        except Exception as e:  # dispatch异常（含gate故障降级）
                            ok, err = False, str(e)[:200]
                            r = f"[CODE_MODE] 工具调用异常: {name}: {e}"
                        await _write({
                            "type": "tool_result", "seq": seq, "ok": ok,
                            "error": err, "result": str(r) if r is not None else ""})
                    elif typ == "call_log":
                        entry = msg.get("entry") or {}
                        if entry.get("seq") is not None:
                            call_log[entry["seq"]] = entry
                    elif typ == "stdout":
                        stdout_parts.append(str(msg.get("text", "")))
                    elif typ == "state":
                        state_results.append(msg.get("result"))
                    elif typ == "final":
                        if not final_fut.done():
                            final_fut.set_result(msg)
            except Exception as e:
                if not final_fut.done():
                    final_fut.set_exception(e)

        pump_task = asyncio.ensure_future(_pump())
        await _write({
            "action": "exec", "script": script,
            "tool_names": [str(t) for t in (available_tools or [])],
            "max_calls": self.max_calls, "call_timeout": self.call_timeout,
        })

        ok, error, timed_out = False, "", False
        try:
            final_msg = await asyncio.wait_for(asyncio.shield(final_fut), timeout=self.batch_timeout)
            ok = bool(final_msg.get("ok"))
            error = str(final_msg.get("error") or "")
            for entry in (final_msg.get("call_log") or []):
                if entry.get("seq") is not None:
                    call_log[entry["seq"]] = entry
        except asyncio.TimeoutError:
            timed_out = True
            await _write({"type": "cancel"})  # 尽力而为（自旋时收不到也无妨）
            try:  # 宽限一小拍：在途调用收尾即返回（随后必 kill，不留自旋）
                final_msg = await asyncio.wait_for(asyncio.shield(final_fut), timeout=0.5)
                ok = bool(final_msg.get("ok"))
                error = str(final_msg.get("error") or "")
            except Exception:
                pass
            if not final_fut.done():
                ok = False
                error = (f"[CODE_MODE_TIMEOUT] 批执行超过{self.batch_timeout}s已取消；"
                         "已完成的工具调用见日志，后续调用已阻断")
        except asyncio.CancelledError:
            await _kill_proc()
            pump_task.cancel()
            raise
        except Exception as e:
            ok = False
            error = f"[CODE_MODE] 子进程通信异常: {e}"

        await _kill_proc()
        pump_task.cancel()
        stderr_tail = ""
        try:
            stderr_tail = (await proc.stderr.read()).decode("utf-8", "replace").strip()
        except Exception:
            pass
        if not ok and not timed_out and not final_fut.done() and not error:
            # 子进程崩溃/被杀且无显式错误：如实报退出码+stderr尾，禁静默
            error = (f"[CODE_MODE] 子进程异常退出 code={proc.returncode}"
                     + (f": {stderr_tail[-400:]}" if stderr_tail else ""))
        if not final_fut.done() and error and not timed_out and stderr_tail and "脚本异常" in error:
            error = f"{error}\n[stderr] {stderr_tail[-400:]}"

        # 输出语义与线程模式一致：result 变量优先，否则 stdout
        out_val = state_results[-1] if state_results else None
        output = str(out_val) if out_val is not None else "".join(stdout_parts).strip()
        calls_dispatched = max(
            [int(e.get("seq") or 0) for e in call_log.values()] or [0])
        if final_fut.done() and not timed_out:
            try:
                calls_dispatched = int(final_fut.result().get("calls_dispatched") or 0)
            except Exception:
                pass
        res = CodeModeResult(
            ok=ok,
            output=output[:8000],
            call_log=[call_log[k] for k in sorted(call_log)],
            error=error,
            timed_out=timed_out,
            duration_ms=int((time.monotonic() - t0) * 1000),
            calls_dispatched=calls_dispatched,
        )
        logger.info(
            f"[code-mode/process] ok={res.ok} calls={res.calls_dispatched} "
            f"logged={len(res.call_log)} {res.duration_ms}ms"
            f"{' timed_out' if res.timed_out else ''}")
        return res

    # ── 结果格式化（soulmate消费方统一文本协议） ───────────────────
    @staticmethod
    def format_result(res: CodeModeResult) -> str:
        n = len(res.call_log)
        if res.timed_out:
            head = (f"[CODE_MODE] ⏱ 批执行超时取消（已合并执行{n}次工具调用，"
                    "部分结果如下）")
        elif not res.ok:
            head = f"[CODE_MODE] ⚠ 批执行异常（已合并执行{n}次工具调用）"
        else:
            head = (f"[CODE_MODE] ✅ {n}次工具调用已合并为1轮执行"
                    f"（{res.duration_ms}ms，省{n}轮LLM round trip）")
        lines = [head, "调用日志:"]
        if not res.call_log:
            lines.append("(脚本未调用任何工具)")
        for i, e in enumerate(res.call_log, 1):
            if e.get("blocked"):
                status = "拦截(权限)"
            elif e.get("ok"):
                status = "ok"
            else:
                status = f"失败({e.get('error') or 'error'})"
            lines.append(
                f"{i}. {e['name']}({e['args_preview']}) → {status} "
                f"{e['duration_ms']}ms len={e['result_len']}")
        if res.error:
            lines.append(res.error)
        lines.append("--- 脚本输出 ---")
        lines.append(res.output if res.output else "(空)")
        return "\n".join(lines)

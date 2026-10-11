"""code_mode_worker.py — Code Mode 脚本执行子进程（路线图 14.1 进程级隔离）。

背景（周期#83 实证）：脚本在主进程 worker 线程里 exec 时，吞异常死循环
（while True: try/except pass）连 PyThreadState_SetAsyncExc 都收不掉——残留
线程持 GIL 自旋把同进程所有线程拖慢单倍~百倍（0.14s 目录 walk 变 60s+），
"挂死脚本不能楔住会话"只做到有界等待，GIL 面没消除。真解=脚本执行搬独立
子进程（browser_worker 同款 JSONL/子进程模式）：挂死只损失本进程，批级超时
直接 kill 进程组，GIL 楔死面彻底消失。

JSONL 协议（本进程 stdin 收 / stdout 发，每行一个 JSON）：
  父→子  {"action":"exec","script":...,"tool_names":[...],"max_calls":N,"call_timeout":S}
         {"type":"tool_result","seq":N,"ok":bool,"error":"...","result":"..."}
         {"type":"cancel"}                     （尽力而为，脚本自旋时收不到也无妨——父进程会 kill）
  子→父  {"type":"tool_call","seq":N,"name":...,"args":{...}}   （stub 跨进程请求内层工具）
         {"type":"call_log","entry":{...}}     （每次 stub 调用开始/结束各发一次，父进程按 seq upsert）
         {"type":"stdout","text":...}          （print 内建即时流式输出——超时被杀时部分输出不丢）
         {"type":"state","result":...}         （`result` 变量每次赋值即时快照——超时被杀时部分结果不丢）
         {"type":"final","ok":bool,"error":"...","calls_dispatched":N,"call_log":[...]}

语义与线程模式（SOULMATE_CODE_MODE_ISOLATION=thread）等价迁移：
- 内层工具调用仍由父进程 dispatch（soulmate 侧含 permission gate，批量化绝不
  绕过权限引擎）；单次调用超时由父进程判定并回传 ok/error 标记，stub 照单记录。
- 批级超时=父进程 kill 进程组（start_new_session + killpg，连带清掉脚本可能留下的
  子进程）；已完成的 call_log/输出/结果经流式快照保留——与线程模式"部分结果不丢"
  等价，且连"吞异常自旋线程回收不掉"的形态也一并消灭。
- 受限 builtins 与父进程同源（agent.code_mode 单一真源 import）：无 __import__/
  open/exec/eval；print 换成流式版本（参数语义 sep/end 与内建一致）。
本文件只做确定性执行，不做 LLM。
"""

import json
import math
import re
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.code_mode import (  # noqa: E402 — 路径自举后才能 import（单一真源）
    CodeModeCallLimit,
    CodeModeCancelled,
    _PROTECTED_NAMES,
    _SAFE_BUILTINS,
    _VALID_IDENT,
)

_OUT = sys.stdout


def _emit(obj: dict) -> None:
    """子→父 单行 JSON。写失败=父进程已走（批超时被杀/通道关闭），静默退出。"""
    try:
        _OUT.write(json.dumps(obj, ensure_ascii=False, default=str) + "\n")
        _OUT.flush()
    except Exception:
        pass


def _make_print():
    """流式 print：语义对齐内建 print（sep/end），写到 JSONL stdout 通道。

    不用 redirect_stdout 攒 StringIO——脚本被批超时 kill 时 StringIO 里的
    输出全丢；即时 emit 使"部分输出不丢"在子进程模式下依然成立。
    file= 显式传入时忽略目标直接走本通道（受限 ns 下没有可用的文件对象语义）。
    """

    def _print(*args, sep=" ", end="\n", **_kw):
        try:
            text = sep.join(str(a) for a in args) + end
        except Exception:
            text = "".join(str(a) for a in args) + end
        _emit({"type": "stdout", "text": text})

    return _print


class _StreamingNS(dict):
    """exec 命名空间：`result` 赋值即快照（STORE_NAME/STORE_GLOBAL 走 __setitem__）。

    线程模式超时后能读到 worker 线程留下的 ns["result"]；子进程被 kill 前必须
    把 result 增量送出，否则"异常/超时前的部分结果"语义在子进程模式下丢失。
    """

    def __setitem__(self, key, value):
        dict.__setitem__(self, key, value)
        if key == "result":
            _emit({"type": "state", "result": None if value is None else str(value)})


def _make_stub(tool_name: str, state: dict):
    """工具stub：发 tool_call 请求，阻塞等父进程 tool_result（EOF/cancel 有界退出）。

    参数规范化/调用上限/计数语义与线程模式逐字一致（calls 只计通过参数校验的调用）。
    """
    max_calls = state["max_calls"]

    def _stub(*args, **kwargs):
        if state["cancel"]:
            raise CodeModeCancelled("批任务已取消（批级超时），后续工具调用不再执行")
        if state["calls"] >= max_calls:
            raise CodeModeCallLimit(f"批内工具调用超过上限{max_calls}次（失控循环熔断）")
        if len(args) == 1 and isinstance(args[0], dict) and not kwargs:
            targs = dict(args[0])
        elif args:
            raise TypeError(
                f"{tool_name}() 参数必须用关键字（如 {tool_name}(path='...')）"
                "或单个dict")
        else:
            targs = dict(kwargs)
        state["calls"] += 1
        state["seq"] += 1
        seq = state["seq"]
        entry = {
            "seq": seq, "name": tool_name,
            "args_preview": json.dumps(targs, ensure_ascii=False, default=str)[:200],
            "ok": False, "blocked": False,
            "duration_ms": 0, "result_len": 0, "error": "",
        }
        state["call_log"].append(entry)
        _emit({"type": "call_log", "entry": entry})  # 开始即可见（被杀时在途调用不丢账）
        t0 = time.monotonic()
        _emit({"type": "tool_call", "seq": seq, "name": tool_name, "args": targs})
        result, ok, err = "", False, ""
        for line in sys.stdin:  # 阻塞等父进程响应（父进程保证有应答或通道关闭）
            try:
                msg = json.loads(line)
            except Exception:
                continue
            if msg.get("type") == "cancel":
                state["cancel"] = True
                entry["duration_ms"] = int((time.monotonic() - t0) * 1000)
                entry["error"] = "cancelled"
                _emit({"type": "call_log", "entry": entry})
                raise CodeModeCancelled("批任务已被取消")
            if msg.get("type") == "tool_result" and msg.get("seq") == seq:
                result = msg.get("result", "")
                ok = bool(msg.get("ok", True))
                err = str(msg.get("error", "") or "")
                break
        else:
            # stdin EOF：父进程已消失——如实标记，不假装成功
            entry["duration_ms"] = int((time.monotonic() - t0) * 1000)
            entry["error"] = "channel_closed"
            _emit({"type": "call_log", "entry": entry})
            return "[CODE_MODE] 父进程通道已关闭"
        entry["ok"] = ok
        entry["error"] = err
        text = str(result) if result is not None else ""
        entry["result_len"] = len(text)
        if text.startswith("[被拦截"):
            entry["blocked"] = True  # gate deny 是审计事实不是执行错误
        entry["duration_ms"] = int((time.monotonic() - t0) * 1000)
        _emit({"type": "call_log", "entry": entry})
        return text

    _stub.__name__ = tool_name
    return _stub


def _build_ns(tool_names, state: dict) -> _StreamingNS:
    ns = _StreamingNS()
    builtins = dict(_SAFE_BUILTINS)
    builtins["print"] = _make_print()
    ns["__builtins__"] = builtins
    ns["json"] = json
    ns["re"] = re
    ns["math"] = math
    injected = []
    for name in tool_names or []:
        name = str(name)
        if not _VALID_IDENT.match(name):
            continue
        if name in _PROTECTED_NAMES:
            continue
        ns[name] = _make_stub(name, state)
        injected.append(name)
    ns["tool_names"] = injected
    return ns


def main() -> int:
    try:
        req = json.loads(sys.stdin.readline())
    except Exception as e:
        _emit({"type": "final", "ok": False,
               "error": f"[CODE_MODE] 执行请求解析失败: {e}",
               "calls_dispatched": 0, "call_log": []})
        return 1
    if req.get("type") == "cancel":
        return 0
    script = str(req.get("script", "") or "")
    state = {
        "calls": 0, "seq": 0, "cancel": False, "call_log": [],
        "max_calls": int(req.get("max_calls", 40) or 40),
    }
    ns = _build_ns(req.get("tool_names"), state)
    ok, error = False, ""
    try:
        compiled = compile(script, "<code_mode>", "exec")
        exec(compiled, ns)  # noqa: S102 — 受限 ns，见模块 docstring
        ok = True
    except CodeModeCallLimit as e:
        error = f"[CODE_MODE] {e}"
    except CodeModeCancelled:
        error = "[CODE_MODE] 批任务已被取消"
    except BaseException as e:  # 含 SyntaxError，全部显式可见
        error = f"[CODE_MODE] 脚本异常: {e}\n{traceback.format_exc(limit=6)}"
    _emit({
        "type": "final", "ok": ok, "error": error,
        "calls_dispatched": state["calls"], "call_log": state["call_log"],
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())

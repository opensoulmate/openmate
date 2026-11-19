"""Ollama ACP adapter — 自研 stdio ACP 桥（重建版，2026-11-20）。

背景：AGENT_ROUTES['ollama'] 引用 `python -m agent.ollama_acp`，但源文件曾丢失
（仅存 __pycache__ 旧 pyc），导致 ollama 路由子进程即起即死、WS 静默断开。
本模块按 ACP JSON-RPC 2.0 over stdio 重建该适配器，后端为本机 Ollama HTTP API。

协议面（与 ws_acp.py 透传面一致）：
- initialize            → result {protocolVersion, agentCapabilities}
- session/new           → result {sessionId}
- session/prompt        → 真调用 POST /api/chat，流式 agent_message_chunk，
                          收尾 id 应答 {stopReason:"endTurn"}
- 其余方法              → -32601 Method not found（如实，不冒充）
模型：env OLLAMA_MODEL 优先，否则取 /api/tags 中第一个非 embedding 模型；
调用失败必须以 JSON-RPC error 回报，绝不假成功。
"""

import json
import os
import sys
import urllib.request
import uuid

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
_TIMEOUT = float(os.environ.get("OLLAMA_ACP_TIMEOUT", "300"))


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _rpc(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _reply(id_, result=None, error=None) -> None:
    msg = {"jsonrpc": "2.0", "id": id_}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result
    _rpc(msg)


def _pick_model() -> str:
    env_model = os.environ.get("OLLAMA_MODEL", "").strip()
    if env_model:
        return env_model
    try:
        with urllib.request.urlopen(OLLAMA_URL + "/api/tags", timeout=10) as r:
            tags = json.loads(r.read())
        for m in tags.get("models", []):
            name = m.get("name") or ""
            # bge-m3 之类 embedding 模型不参选（按名字粗筛 + /api/tags 无能力字段）
            if name and "bge" not in name.lower() and "embed" not in name.lower():
                return name
    except Exception as e:  # noqa: BLE001
        _log(f"[ollama_acp] /api/tags failed: {e}")
    return "qwen2.5:0.5b"


def _chat(model: str, prompt: str, session_id: str) -> None:
    """真调用 ollama /api/chat，逐段回传 agent_message_chunk。"""
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
    }).encode()
    req = urllib.request.Request(
        OLLAMA_URL + "/api/chat", data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    text_parts = []
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                chunk = json.loads(line)
            except json.JSONDecodeError:
                continue
            if chunk.get("error"):
                raise RuntimeError(str(chunk["error"]))
            delta = (chunk.get("message") or {}).get("content") or ""
            if delta:
                text_parts.append(delta)
                _rpc({"jsonrpc": "2.0", "method": "session/update",
                      "params": {"sessionId": session_id,
                                 "update": {"sessionUpdate": "agent_message_chunk",
                                            "content": {"type": "text", "text": delta}}}})
    if not text_parts:
        raise RuntimeError("ollama returned empty reply")


def main() -> None:
    model = _pick_model()
    _log(f"[ollama_acp] ready model={model}")
    session_id = None
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = msg.get("method")
        id_ = msg.get("id")
        if method == "initialize":
            _reply(id_, result={
                "protocolVersion": 1,
                "agentCapabilities": {
                    "loadSession": False,
                    "promptCapabilities": {"image": False, "embeddedContext": False},
                },
            })
        elif method == "session/new":
            session_id = "ollama-" + uuid.uuid4().hex[:12]
            _reply(id_, result={"sessionId": session_id})
        elif method == "session/prompt":
            params = msg.get("params") or {}
            sid = str(params.get("sessionId") or session_id or "ollama-unknown")
            prompt = "".join(
                p.get("text", "") for p in params.get("prompt", [])
                if isinstance(p, dict) and p.get("type") == "text")
            try:
                _chat(model, prompt, sid)
                _reply(id_, result={"stopReason": "endTurn"})
            except Exception as e:  # noqa: BLE001 — 失败必须如实上报
                _log(f"[ollama_acp] chat failed: {e}")
                _reply(id_, error={"code": -32000, "message": f"ollama chat failed: {e}"})
        elif id_ is not None:
            _reply(id_, error={"code": -32601, "message": f"Method not found: {method}"})


if __name__ == "__main__":
    main()

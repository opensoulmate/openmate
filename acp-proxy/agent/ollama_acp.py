"""Ollama ACP 适配器 — 让 ollama 成为独立的 ACP agent。

背景：ollama 本体没有 acp 子命令（`ollama acp` 报 unknown command）。
本适配器在 stdio 上说 ACP(JSON-RPC)，在背后调本地 ollama HTTP API (/api/chat 流式)，
使 ollama 作为独立 agent 注册进 ws_acp 路由表（独立性铁律：每个 agent 自己的大脑）。

用法：python -m agent.ollama_acp   （模型：环境变量 OLLAMA_MODEL，缺省取 ollama list 第一个）
"""
import json
import os
import sys
import urllib.request
import uuid

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
MODEL = os.environ.get("OLLAMA_MODEL", "")


def _log(msg: str) -> None:
    print(f"[ollama-acp] {msg}", file=sys.stderr, flush=True)


def _pick_model() -> str:
    global MODEL
    if MODEL:
        return MODEL
    try:
        with urllib.request.urlopen(f"{OLLAMA_HOST}/api/tags", timeout=10) as r:
            data = json.loads(r.read())
        models = data.get("models") or []
        if models:
            MODEL = models[0].get("name") or models[0].get("model") or ""
            _log(f"auto-selected model: {MODEL}")
    except Exception as e:
        _log(f"pick model failed: {e}")
    return MODEL


def _reply(msg_id, result=None, error=None):
    frame = {"jsonrpc": "2.0", "id": msg_id}
    if error is not None:
        frame["error"] = error
    else:
        frame["result"] = result
    print(json.dumps(frame, ensure_ascii=False), flush=True)


def _notify_update(session_id: str, text: str):
    print(json.dumps({
        "jsonrpc": "2.0", "method": "session/update",
        "params": {
            "sessionId": session_id,
            "sessionUpdate": "agent_message_chunk",
            "content": {"type": "text", "text": text},
        },
    }, ensure_ascii=False), flush=True)


def _chat_stream(session_id: str, history: list) -> str:
    """调 ollama /api/chat 流式生成，逐块推送 session/update，返回完整回复。"""
    model = _pick_model()
    if not model:
        raise RuntimeError("no ollama model available（先 ollama pull 一个模型）")
    payload = json.dumps({"model": model, "messages": history, "stream": True}).encode()
    req = urllib.request.Request(
        f"{OLLAMA_HOST}/api/chat", data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    full = []
    with urllib.request.urlopen(req, timeout=600) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                chunk = json.loads(line)
            except Exception:
                continue
            piece = ((chunk.get("message") or {}).get("content")) or ""
            if piece:
                full.append(piece)
                _notify_update(session_id, piece)
            if chunk.get("done"):
                break
    return "".join(full)


def main() -> None:
    _log("started (ACP over stdio)")
    sessions: dict = {}  # session_id -> message history
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue
        method = msg.get("method")
        msg_id = msg.get("id")
        params = msg.get("params") or {}
        try:
            if method == "initialize":
                _reply(msg_id, {"protocolVersion": 1, "agentInfo": {"name": "ollama", "version": "1.0"}})
            elif method == "session/new":
                sid = f"ol-{uuid.uuid4().hex[:12]}"
                sessions[sid] = []
                _reply(msg_id, {"session_id": sid, "sessionId": sid})
            elif method == "session/prompt":
                sid = params.get("sessionId") or params.get("session_id") or ""
                prompt = params.get("prompt") or []
                text = "".join(
                    p.get("text", "") for p in prompt if isinstance(p, dict) and p.get("type") == "text"
                ) or (prompt if isinstance(prompt, str) else "")
                hist = sessions.setdefault(sid, [])
                hist.append({"role": "user", "content": text})
                answer = _chat_stream(sid, hist)
                hist.append({"role": "assistant", "content": answer})
                _reply(msg_id, {"stopReason": "endTurn", "content": [{"type": "text", "text": answer}]})
            elif method in ("session/load", "session/cancel"):
                _reply(msg_id, {})
            else:
                _reply(msg_id, error={"code": -32601, "message": f"method not found: {method}"})
        except Exception as e:
            _log(f"error on {method}: {e}")
            _reply(msg_id, error={"code": -32603, "message": str(e)})


if __name__ == "__main__":
    main()

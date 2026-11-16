"""api.py — 本地 REST 层（127.0.0.1 + token）。

零第三方服务端依赖（asyncio 手写 HTTP/1.1，Connection: close）。
Electron main spawn 本进程后读 stdout 的 listening JSON 拿端口+token；
后端以服务方式跑同一条命令即可（同一套代码两处部署）。

路由（全部 JSON；除 /health 外都要 Authorization: Bearer *** X-Auth-Token）：
  GET  /health
  GET  /api/tasks
  POST /api/tasks                  {url, dir?, filename?, options?}
  POST /api/tasks/{id}/pause
  POST /api/tasks/{id}/resume
  POST /api/tasks/{id}/remove      {remove_files?}
  GET  /api/events?after=N
  GET  /api/config
  PUT  /api/config                 {dir?, concurrency?, speedLimit?}
  POST /api/metadata               {source}
  POST /api/shutdown
"""

from __future__ import annotations

import asyncio
import json
import urllib.parse
from typing import Any, Dict, Optional, Tuple

from .engine import Engine, EngineError
from .http_engine import HttpEngineError
from .pan_share import PanShareError

MAX_BODY = 1024 * 1024

_REASON = {200: "OK", 201: "Created", 400: "Bad Request", 401: "Unauthorized",
           404: "Not Found", 405: "Method Not Allowed", 500: "Internal Server Error"}


class ApiServer:
    def __init__(self, engine: Engine, token: str, host: str = "127.0.0.1", port: int = 0):
        self.engine = engine
        self.token = token
        self.host = host
        self.port = port
        self._server: Optional[asyncio.base_events.Server] = None
        self._stopping = asyncio.Event()

    async def start(self) -> int:
        self._server = await asyncio.start_server(self._handle, self.host, self.port)
        self.port = self._server.sockets[0].getsockname()[1]
        return self.port

    async def serve(self) -> None:
        assert self._server is not None
        async with self._server:
            await self._stopping.wait()

    async def stop(self) -> None:
        self._stopping.set()
        if self._server is not None:
            self._server.close()

    # ── 连接处理 ────────────────────────────────────────────

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 30)
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionError):
            writer.close()
            return
        try:
            lines = head.decode("latin-1").split("\r\n")
            method, target, _ = lines[0].split(" ", 2)
            headers: Dict[str, str] = {}
            for line in lines[1:]:
                if ":" in line:
                    k, _, v = line.partition(":")
                    headers[k.strip().lower()] = v.strip()
            if method == "OPTIONS":
                # 浏览器预检（renderer 直连引擎 REST 是跨源 fetch）：免 token 放行
                await self._respond(writer, 204, b"")
                return
            body = b""
            clen = int(headers.get("content-length", "0") or 0)
            if clen:
                body = await reader.readexactly(min(clen, MAX_BODY))
            status, payload = await self._route(method, target, headers, body)
        except Exception as e:  # noqa: BLE001
            status, payload = 500, _err("internal", f"服务器内部错误: {e}")
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        await self._respond(writer, status, data)

    async def _respond(self, writer: asyncio.StreamWriter, status: int, data: bytes) -> None:
        head_out = (
            f"HTTP/1.1 {status} {_REASON.get(status, 'Unknown')}\r\n"
            f"Content-Type: application/json; charset=utf-8\r\n"
            f"Content-Length: {len(data)}\r\n"
            # renderer 直连（Electron 页面 origin ≠ 127.0.0.1:port）：CORS 必须放行。
            # 仅绑 127.0.0.1 + token 鉴权，Allow-Origin:* 不扩面。
            f"Access-Control-Allow-Origin: *\r\n"
            f"Access-Control-Allow-Methods: GET, POST, PUT, DELETE, OPTIONS\r\n"
            f"Access-Control-Allow-Headers: Content-Type, Authorization, X-Auth-Token\r\n"
            f"Connection: close\r\n\r\n"
        ).encode("latin-1")
        try:
            writer.write(head_out + data)
            await writer.drain()
        except (ConnectionError, RuntimeError):
            pass
        finally:
            writer.close()

    # ── 路由 ────────────────────────────────────────────────

    def _authed(self, headers: Dict[str, str]) -> bool:
        auth = headers.get("authorization", "")
        if auth.lower().startswith("bearer ") and auth[7:].strip() == self.token:
            return True
        return headers.get("x-auth-token", "") == self.token

    async def _route(
        self, method: str, target: str, headers: Dict[str, str], body: bytes
    ) -> Tuple[int, Dict[str, Any]]:
        parsed = urllib.parse.urlsplit(target)
        path = parsed.path.rstrip("/") or "/"
        query = dict(urllib.parse.parse_qsl(parsed.query))

        if path == "/health":
            return 200, {"ok": True, "engine": "openmate-download-engine", "origin": self.engine.origin}
        if not self._authed(headers):
            return 401, _err("unauthorized", "未授权：缺少或错误的 token")

        try:
            if path == "/api/tasks" and method == "GET":
                return 200, {"tasks": self.engine.list()}
            if path == "/api/tasks" and method == "POST":
                d = _json_body(body)
                task = await self.engine.add(
                    d.get("url", ""),
                    d.get("dir"),
                    d.get("filename"),
                    d.get("options"),
                )
                return 201, {"task": task}
            if path == "/api/events" and method == "GET":
                return 200, self.engine.events(int(query.get("after", 0) or 0))
            if path == "/api/config" and method == "GET":
                return 200, self.engine.config_get()
            if path == "/api/config" and method in ("PUT", "POST"):
                d = _json_body(body)
                # 网盘登录凭据（用户自有账号，传空串清除）：两键双认
                extra_cfg = None
                for k in ("panCookie", "pan_cookie"):
                    if k in d:
                        extra_cfg = d[k]
                        break
                return 200, self.engine.config_set(
                    d.get("dir"),
                    d.get("concurrency"),
                    d.get("speedLimit", d.get("speed_limit")),
                    extra_cfg,
                )
            if path == "/api/metadata" and method == "POST":
                d = _json_body(body)
                files = await self.engine.metadata(
                    d.get("source", ""), float(d.get("timeout", 90)), d.get("options")
                )
                return 200, {"files": files}
            if path == "/api/shutdown" and method == "POST":
                asyncio.get_running_loop().call_soon(self._stopping.set)
                return 200, {"ok": True}

            parts = [p for p in path.split("/") if p]  # api/tasks/<id>/<action>
            if len(parts) == 4 and parts[0] == "api" and parts[1] == "tasks" and method == "POST":
                tid, action = parts[2], parts[3]
                if action == "pause":
                    return 200, {"task": await self.engine.pause(tid)}
                if action == "resume":
                    return 200, {"task": await self.engine.resume(tid)}
                if action == "remove":
                    d = _json_body(body) if body else {}
                    remove_files = bool(
                        d.get("remove_files", query.get("removeFiles", query.get("remove_files", "")))
                    )
                    res = await self.engine.remove(tid, remove_files)
                    return 200, {"ok": True, "removed_file": bool(res.get("removed_file"))}
            return 404, _err("not_found", f"未知路由: {method} {path}")
        except (EngineError, HttpEngineError, PanShareError) as e:
            return 400, _err(_classify_error(e), str(e))
        except ValueError as e:
            return 400, _err("bad_request", f"请求非法: {e}")
        except Exception as e:  # noqa: BLE001
            return 500, _err("internal", f"引擎错误: {e}")


def _err(code: str, message: str) -> Dict[str, Any]:
    """错误形状=契约 §2 细分码（与 mock 引擎一致）：{"error": {"code", "message"}}。"""
    return {"error": {"code": code, "message": message}}


def _classify_error(e: Exception) -> str:
    """异常 → 契约 §2 细分码（不接受单一模糊串）。

    PanShareError 自带细分码（pan_link_invalid/pan_extract_code_wrong/
    pan_login_required/pan_speed_limited/pan_risk_control/pan_resolve_failed）
    直通前端。
    """
    code = getattr(e, "code", None)
    if code:
        return code
    msg = str(e)
    if "不支持的来源" in msg or "仅支持" in msg or "不支持" in msg:
        return "unsupported_scheme"
    if "不存在" in msg:
        return "not_found"
    if isinstance(e, HttpEngineError):
        return "network_error"
    if "Range" in msg or "range" in msg:
        return "range_unsupported"
    return "bad_request"


def _json_body(body: bytes) -> Dict[str, Any]:
    if not body:
        return {}
    d = json.loads(body.decode("utf-8"))
    if not isinstance(d, dict):
        raise ValueError("请求体必须是 JSON 对象")
    return d

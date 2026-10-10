"""路线图 4.1c 会话级路由：微信peer↔OpenMate会话映射管理（查询/改绑/解绑）。

4.1a 的 BridgeStore 只在首见消息时自动建映射，用户无法查看或干预错绑/换绑。
本模块提供显式管理面（确定性操作，不走LLM）：
- 查询：全量列出映射，或按 peer / session_id 过滤（反查同会话的所有peer）
- 改绑：把某peer显式指到指定OpenMate会话（换绑已有会话/纠正错绑），返回旧绑定
- 解绑：删除映射（该peer下次来消息按首见逻辑自动新建会话，历史会话保留不动）

铁律：只操作 peers 映射表，不碰消息收发、不碰 hermes 侧；store可注入（测试
独立库路径）；校验失败抛 ValueError（dispatch层转"错误:"文本），不静默吞。
"""

from __future__ import annotations

import time
from typing import Any

from agent.wechat_bridge import BridgeStore


class WeChatRouter:
    """peer↔session 映射管理面（读写与微信桥同一 SQLite）。"""

    def __init__(self, store: BridgeStore | None = None):
        if store is None:
            store = BridgeStore()
        self._store = store

    # ── 校验 ──────────────────────────────────────────────────

    @staticmethod
    def _clean(value: Any, field: str) -> str:
        s = "" if value is None else str(value).strip()
        if not s:
            raise ValueError(f"{field} 不能为空")
        if any(ch.isspace() for ch in s):
            raise ValueError(f"{field} 不能包含空白字符: {s!r}")
        return s

    # ── 查询 ──────────────────────────────────────────────────

    def list_routes(
        self,
        peer: str | None = None,
        session_id: str | None = None,
    ) -> list[dict]:
        """全部映射（最近消息倒序）；peer/session_id 任一给定即过滤（可反查）。"""
        p = str(peer).strip() if peer is not None else ""
        s = str(session_id).strip() if session_id is not None else ""
        mappings = self._store.list_mappings()
        if p:
            mappings = [m for m in mappings if m["peer"] == p]
        if s:
            mappings = [m for m in mappings if m["session_id"] == s]
        return mappings

    # ── 改绑 / 解绑 ───────────────────────────────────────────

    def bind(self, peer: str, session_id: str, now_ms: int | None = None) -> dict:
        """显式绑定/改绑 peer→session。已存在则改绑并返回 previous_session_id。"""
        p = self._clean(peer, "peer")
        s = self._clean(session_id, "session_id")
        prev = self._store.session_for(p)
        self._store.remember_session(
            p, s, int(now_ms if now_ms is not None else time.time() * 1000)
        )
        return {
            "peer": p,
            "session_id": s,
            "previous_session_id": prev,
            "created": prev is None,
        }

    def unbind(self, peer: str) -> dict:
        """解绑：删除映射。removed=False 表示原本就没有映射（幂等不报错）。"""
        p = self._clean(peer, "peer")
        prev = self._store.unbind(p)
        return {"peer": p, "session_id": prev, "removed": prev is not None}


# ── 进程内单例（与 get_health_monitor 同惯例）──────────────────

_router: WeChatRouter | None = None


def get_wechat_router() -> WeChatRouter:
    global _router
    if _router is None:
        _router = WeChatRouter()
    return _router

"""网络受限会话工具面收缩 — kilocode supplement3 #15 移植

调研来源（kilocode-source-supplement3.md #15）：
- kilocode src/kilocode/sandbox/policy.ts `networkRestricted(sessionID)`：
  sandbox启用且mode≠"allow" → 会话网络受限
- kilocode src/tool/registry.ts `describeCodeMode`：`if (input.networkRestricted)
  return` —— 受限会话**registry直接不暴露code-mode工具**（"按环境裁剪工具面
  优于'给了再拦'"——工具面收缩，而非运行时拒绝）
- kilocode src/tool/code-mode.ts:222 执行层双保险：`const mcpTools = restricted ?
  {} : Permission.visibleTools(...)` —— 即便进了code-mode，MCP目录也是空的
- kilocode src/session/prompt.ts:947-971：受限会话MCP resource读取显式失败
  （"Sandbox denied MCP resource access"）——模式切换/限制是显式拒绝不是静默

本模块职责（单一真源）：
1. `SandboxPolicy.network_restricted(session_id)` —— 会话是否网络受限
   （per-session显式设置 > 全局default > env SOULMATE_NETWORK_RESTRICTED > False）
2. `filter_tools(...)` —— 工具面收缩（kilocode registry语义）：受限会话的
   LLM工具列表里**没有**网络类工具
3. `deny_reason(...)` —— 运行时fail-safe兜底：模型幻觉出被裁工具名（或任何
   绕过工具面的调用路径）时显式拒绝，绝不静默执行

网络类工具分类（kilocode语义 + 本系统适配）：
- web_search / web_extract：直接出网（适配补充——kilocode靠真沙箱拦网络，
  本系统暂无网络沙箱（mirror差距P1），同类工具必须同裁，否则"网络受限"
  名不副实）
- batch_execute：code-mode工具（kilocode registry.describeCodeMode对应物）
- MCP动态工具（server__tool）+ MCP resource三件套：kilocode prompt.ts明令
  受限会话拒绝resource读取、code-mode.ts清空MCP目录

保真声明：terminal/execute_code不裁（kilocode同样保留bash/edit——其网络
限制由沙箱层执行）。任意shell的网络访问需真沙箱（mirror organ差距P1）才能
硬性禁止，本切片先收工具面+工具路由层，报告"遗留问题"如实标注。

失败语义（evolution-engine-patterns.md mem0 §1.1「处理必须可见，禁止静默」）：
- store文件不存在 → 不受限（未配置=无限制，与kilocode默认一致）
- store文件损坏/不可读 → **fail-closed按受限处理**（限制类配置宁可收紧不静默
  放开）+ WARNING日志 + describe()透出store_error供API诊断
- 写失败 → set_restricted返回False（不假装成功）
"""

import json
import logging
import os
from pathlib import Path
from typing import Iterable, Optional

logger = logging.getLogger("acp-agent.sandbox-policy")

DEFAULT_STORE_PATH = Path.home() / ".hermes" / "soulmate" / "sandbox_policy.json"
ENV_FLAG = "SOULMATE_NETWORK_RESTRICTED"

# 直接网络类 + code-mode（batch_execute）——静态分类
NETWORK_TOOL_NAMES = frozenset({
    "web_search",
    "web_extract",
    "batch_execute",
    "browser",  # 路线图7.1：浏览器自动化直接出网，受限会话同款收缩
    "text_to_speech",  # 路线图7.3：edge-tts 主后端出网（离线兜底espeak-ng不受影响）
    "image",  # 路线图7.4：AI 图像生成 API 后端出网（Pillow 本地编辑/程序化绘制不受影响）
})


def _env_default() -> bool:
    return str(os.environ.get(ENV_FLAG, "")).strip().lower() in ("1", "true", "yes", "on")


class SandboxPolicy:
    """会话级网络受限策略 —— agent子进程与app进程共享JSON store（文件真源）。

    store结构：{"default": bool, "sessions": {session_id: bool}}
    """

    def __init__(self, store_path: str | Path = ""):
        self.store_path = Path(store_path) if store_path else DEFAULT_STORE_PATH

    # ── store ─────────────────────────────────────────────────────

    def _load(self) -> tuple[dict, str]:
        """返回(数据, store_error)。store_error非空=按fail-closed受限处理。"""
        try:
            if not self.store_path.exists():
                return {"default": _env_default(), "sessions": {}}, ""
            with open(self.store_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return {}, f"store root not object: {type(data).__name__}"
            sessions = data.get("sessions") or {}
            if not isinstance(sessions, dict):
                return {}, "store sessions not object"
            return {
                "default": bool(data.get("default", False)) or _env_default(),
                "sessions": {str(k): bool(v) for k, v in sessions.items()},
            }, ""
        except Exception as e:  # 损坏/不可读 → fail-closed
            err = f"store unreadable ({e}) — fail-closed按受限处理"
            logger.warning("[sandbox] %s: %s", self.store_path, err)
            return {}, err

    def _save(self, data: dict) -> bool:
        try:
            self.store_path.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(data, ensure_ascii=False, indent=2)
            from utils.file_safety import atomic_write
            ok, err = atomic_write(self.store_path, payload)
            if not ok:
                logger.warning("[sandbox] store写入失败: %s", err)
            return ok
        except Exception as e:
            logger.warning("[sandbox] store写入异常: %s", e)
            return False

    def set_restricted(self, session_id: str, restricted: bool) -> bool:
        """per-session显式设置（True=网络受限）。返回是否落盘成功。"""
        data, _ = self._load()
        data.setdefault("sessions", {})[str(session_id)] = bool(restricted)
        return self._save(data)

    def clear_session(self, session_id: str) -> bool:
        """删除per-session覆盖（回到default/env语义）。"""
        data, _ = self._load()
        sessions = data.setdefault("sessions", {})
        if str(session_id) not in sessions:
            return True
        sessions.pop(str(session_id), None)
        return self._save(data)

    def set_default(self, restricted: bool) -> bool:
        data, _ = self._load()
        data["default"] = bool(restricted)
        return self._save(data)

    def snapshot(self) -> dict:
        data, err = self._load()
        return {
            "default": data.get("default", False),
            "env_default": _env_default(),
            "sessions": dict(data.get("sessions", {})),
            "store_path": str(self.store_path),
            "store_error": err,
        }

    # ── 判定 ─────────────────────────────────────────────────────

    def network_restricted(self, session_id: str) -> bool:
        """会话是否网络受限（kilocode SandboxPolicy.networkRestricted对应物）。

        per-session显式 > store default（含env兜底）；store损坏→fail-closed True。
        """
        data, err = self._load()
        if err:
            return True
        sessions = data.get("sessions", {})
        if str(session_id) in sessions:
            return bool(sessions[str(session_id)])
        return bool(data.get("default", False))

    # ── 分类 ─────────────────────────────────────────────────────

    @staticmethod
    def is_network_tool(name: str, mcp_names: Iterable[str] = ()) -> bool:
        """工具名是否网络类（静态集 + MCP动态名 + MCP resource三件套）。"""
        if not name:
            return False
        if name in NETWORK_TOOL_NAMES:
            return True
        try:
            from agent.mcp_resources import MCP_RESOURCE_TOOL_NAMES
            if name in MCP_RESOURCE_TOOL_NAMES:
                return True
        except Exception:  # 分类绝不因import炸——但resource名漏分类由调用方mcp_names兜
            pass
        return name in set(mcp_names or ())

    def filter_tools(
        self,
        session_id: str,
        tools: list,
        mcp_names: Iterable[str] = (),
    ) -> tuple[list, list[str]]:
        """工具面收缩（kilocode registry语义）。返回(保留工具列表, 被裁工具名)。

        不受限 → 恒等返回（零行为变化）。受限 → 网络类工具不进LLM工具列表。
        """
        if not self.network_restricted(session_id):
            return list(tools), []
        mcp = set(mcp_names or ())
        kept: list = []
        dropped: list[str] = []
        for tool in tools:
            name = ""
            if isinstance(tool, dict):
                name = ((tool.get("function") or {}) or {}).get("name", "") or ""
            if self.is_network_tool(name, mcp):
                dropped.append(name)
            else:
                kept.append(tool)
        return kept, dropped

    def deny_reason(
        self,
        session_id: str,
        tool_name: str,
        mcp_names: Iterable[str] = (),
    ) -> Optional[str]:
        """运行时fail-safe兜底：受限会话调用网络类工具 → 显式拒绝文本（合成
        工具结果），否则None。kilocode code-mode.ts:222双保险语义。"""
        if not self.network_restricted(session_id):
            return None
        if not self.is_network_tool(tool_name, mcp_names):
            return None
        return (
            f"[SANDBOX DENIED] {tool_name} 在网络受限会话中不可用，调用未执行。"
            f"本会话工具面已按网络能力收缩（kilocode SandboxPolicy.networkRestricted"
            f"语义：按环境裁剪工具面，优于给了再拦）。可用工具以本轮工具列表为准；"
            f"如需网络能力，请让用户解除本会话的网络限制。"
        )

    def describe(
        self,
        session_id: str,
        tool_names: Iterable[str] = (),
        mcp_names: Iterable[str] = (),
    ) -> dict:
        """会话策略+受影响工具面快照（API可观测性——"限制了什么"可见）。"""
        restricted = self.network_restricted(session_id)
        network_tools = sorted(
            n for n in (tool_names or ()) if self.is_network_tool(n, mcp_names)
        )
        return {
            "session_id": str(session_id),
            "network_restricted": restricted,
            "network_tools_in_face": network_tools,
            "dropped_if_restricted": network_tools if restricted else [],
            **self.snapshot(),
        }


_PROCESS_DEFAULT: Optional[SandboxPolicy] = None


def default_policy() -> SandboxPolicy:
    """进程级默认策略（极简测试harness未注入实例时的回退；默认不受限）。"""
    global _PROCESS_DEFAULT
    if _PROCESS_DEFAULT is None:
        _PROCESS_DEFAULT = SandboxPolicy()
    return _PROCESS_DEFAULT

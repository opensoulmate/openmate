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

── P1-01 危险编码工具轨道隔离（blueprint P1.1 双轨 Workspace）────────
blueprint语义："危险编码工具绝不暴露给 personal 轨道，除非用户显式切换"
（personal=受限工具集：日程/搜索/消息，**默认无任意 shell**）。
实现与网络轴同构（同一 store、同一 filter_tools/deny_reason 双保险）：
- 轨道（tool_track）：per-session显式 > default_track（默认 coding=零行为
  变化，存量会话不受影响）；store损坏→fail-closed按 personal（限制类配置
  宁可收紧不静默放开）。
- personal 轨道：危险编码工具（DANGEROUS_CODING_TOOL_NAMES）**不进 LLM
  工具面**（"不暴露"=工具面收缩，同 kilocode registry 语义），运行时
  fail-safe 兜底显式拒绝幻觉调用；显式 set_track("coding") 切换后恢复。
- 边界声明：request_evolution/skill_save 走各自安全门（进化评审/单写者
  守卫），delegate_tasks/group_task/schedule_task 走被委派方门禁——非
  "直接执行/改写"原语，不入危险集（⚠️ 间接通道如实声明：委派/排程的执行面
  在执行时刻的会话轨道，personal 轨道不阻断委派排程本身，是否随轨道收缩
  待用户拍板）；如需收紧另行拍板。

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

# ── P1-01 危险编码工具轨道（blueprint P1.1 双轨 Workspace）──
TRACK_CODING = "coding"
TRACK_PERSONAL = "personal"
TRACKS = (TRACK_CODING, TRACK_PERSONAL)
# 危险编码工具=任意 shell + 代码执行 + 文件改写 + code-mode 批量执行
# （"危险编码工具绝不暴露给 personal 轨道"的工具面真源；batch_execute 与
# 网络集有交集——两轴独立判定，任一命中即裁）。
# ⚠️ process 必须在列（周期#126 终验实证侧门）：terminal 被裁后模型改走
# process 后台进程通道执行同一条 shell 命令="拦正路留侧门"同类缺陷，
# 执行原语全家（terminal/execute_code/process）必须同裁。
DANGEROUS_CODING_TOOL_NAMES = frozenset({
    "terminal",
    "execute_code",
    "process",
    "write_file",
    "patch",
    "batch_execute",
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
        """返回(数据, store_error)。store_error非空=按fail-closed受限处理
        （网络轴=受限、轨道轴=personal 双收紧）。"""
        try:
            if not self.store_path.exists():
                return {
                    "default": _env_default(),
                    "sessions": {},
                    "default_track": TRACK_CODING,
                    "tool_tracks": {},
                }, ""
            with open(self.store_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return {}, f"store root not object: {type(data).__name__}"
            sessions = data.get("sessions") or {}
            if not isinstance(sessions, dict):
                return {}, f"store sessions not object"
            tracks = data.get("tool_tracks") or {}
            if not isinstance(tracks, dict):
                return {}, f"store tool_tracks not object"
            # 非法轨道值 → fail-closed按 personal（收紧方向，绝不静默放开）
            def _track_or_personal(v) -> str:
                v = str(v or "")
                return v if v in TRACKS else TRACK_PERSONAL
            return {
                "default": bool(data.get("default", False)) or _env_default(),
                "sessions": {str(k): bool(v) for k, v in sessions.items()},
                "default_track": _track_or_personal(
                    data.get("default_track", TRACK_CODING)),
                "tool_tracks": {str(k): _track_or_personal(v) for k, v in tracks.items()},
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

    def set_track(self, session_id: str, track: str) -> bool:
        """per-session显式设置工具轨道（P1-01；"用户显式切换"的落点）。

        track 取值 TRACK_CODING/TRACK_PERSONAL；非法值拒绝落盘（不静默纠正）。
        """
        if track not in TRACKS:
            logger.warning("[sandbox] set_track 非法轨道值 %r（合法: %s）",
                           track, "/".join(TRACKS))
            return False
        data, _ = self._load()
        data.setdefault("tool_tracks", {})[str(session_id)] = track
        return self._save(data)

    def set_default_track(self, track: str) -> bool:
        """全局默认轨道（未显式设置的会话跟随）。"""
        if track not in TRACKS:
            logger.warning("[sandbox] set_default_track 非法轨道值 %r", track)
            return False
        data, _ = self._load()
        data["default_track"] = track
        return self._save(data)

    def clear_session(self, session_id: str) -> bool:
        """删除per-session覆盖（回到default/env语义；网络+轨道两轴一起清）。"""
        data, _ = self._load()
        sessions = data.setdefault("sessions", {})
        tracks = data.setdefault("tool_tracks", {})
        existed = str(session_id) in sessions or str(session_id) in tracks
        sessions.pop(str(session_id), None)
        tracks.pop(str(session_id), None)
        return self._save(data) if existed else True

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
            "default_track": data.get("default_track", TRACK_CODING),
            "tool_tracks": dict(data.get("tool_tracks", {})),
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

    def tool_track(self, session_id: str) -> str:
        """会话工具轨道（P1-01）：per-session显式 > default_track（默认coding）。

        store损坏/非法值→fail-closed返回 personal（限制类配置宁可收紧）。
        """
        data, err = self._load()
        if err:
            return TRACK_PERSONAL
        tracks = data.get("tool_tracks", {})
        if str(session_id) in tracks:
            return str(tracks[str(session_id)])
        return str(data.get("default_track", TRACK_CODING))

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

    @staticmethod
    def is_dangerous_coding_tool(name: str) -> bool:
        """工具名是否危险编码工具（P1-01：personal 轨道不暴露的执行/改写原语）。"""
        return bool(name) and name in DANGEROUS_CODING_TOOL_NAMES

    def filter_tools(
        self,
        session_id: str,
        tools: list,
        mcp_names: Iterable[str] = (),
    ) -> tuple[list, list[str]]:
        """工具面收缩（kilocode registry语义）。返回(保留工具列表, 被裁工具名)。

        两轴独立（任一命中即裁）：
        - 网络轴：network_restricted → 网络类工具不进LLM工具列表
        - 轨道轴（P1-01）：personal 轨道 → 危险编码工具不进LLM工具列表
        两轴都放开 → 恒等返回（零行为变化）。
        """
        restricted = self.network_restricted(session_id)
        personal = self.tool_track(session_id) == TRACK_PERSONAL
        if not restricted and not personal:
            return list(tools), []
        mcp = set(mcp_names or ())
        kept: list = []
        dropped: list[str] = []
        for tool in tools:
            name = ""
            if isinstance(tool, dict):
                name = ((tool.get("function") or {}) or {}).get("name", "") or ""
            drop = (restricted and self.is_network_tool(name, mcp)) or (
                personal and self.is_dangerous_coding_tool(name))
            if drop:
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
        """运行时fail-safe兜底：被工具面裁掉的工具被调用（模型幻觉/绕过路径）→
        显式拒绝文本（合成工具结果），否则None。kilocode code-mode.ts:222双保险语义。"""
        if self.network_restricted(session_id) and self.is_network_tool(
                tool_name, mcp_names):
            return (
                f"[SANDBOX DENIED] {tool_name} 在网络受限会话中不可用，调用未执行。"
                f"本会话工具面已按网络能力收缩（kilocode SandboxPolicy.networkRestricted"
                f"语义：按环境裁剪工具面，优于给了再拦）。可用工具以本轮工具列表为准；"
                f"如需网络能力，请让用户解除本会话的网络限制。"
            )
        if self.tool_track(session_id) == TRACK_PERSONAL and self.is_dangerous_coding_tool(
                tool_name):
            return (
                f"[SANDBOX DENIED] {tool_name} 是危险编码工具，不对 personal 轨道开放"
                f"（blueprint P1.1：危险编码工具绝不暴露给 personal 轨道，除非用户显式切换），"
                f"调用未执行。本会话工具面已按 personal 轨道收缩；"
                f"如需危险编码工具，请让用户显式切换本会话到 coding 轨道。"
            )
        return None

    def describe(
        self,
        session_id: str,
        tool_names: Iterable[str] = (),
        mcp_names: Iterable[str] = (),
    ) -> dict:
        """会话策略+受影响工具面快照（API可观测性——"限制了什么"可见）。"""
        restricted = self.network_restricted(session_id)
        track = self.tool_track(session_id)
        network_tools = sorted(
            n for n in (tool_names or ()) if self.is_network_tool(n, mcp_names)
        )
        dangerous_tools = sorted(
            n for n in (tool_names or ()) if self.is_dangerous_coding_tool(n)
        )
        return {
            "session_id": str(session_id),
            "network_restricted": restricted,
            "tool_track": track,
            "network_tools_in_face": network_tools,
            "dangerous_coding_tools_in_face": dangerous_tools,
            "dropped_if_restricted": network_tools if restricted else [],
            "dropped_if_personal": dangerous_tools if track == TRACK_PERSONAL else [],
            **self.snapshot(),
        }


_PROCESS_DEFAULT: Optional[SandboxPolicy] = None


def default_policy() -> SandboxPolicy:
    """进程级默认策略（极简测试harness未注入实例时的回退；默认不受限）。"""
    global _PROCESS_DEFAULT
    if _PROCESS_DEFAULT is None:
        _PROCESS_DEFAULT = SandboxPolicy()
    return _PROCESS_DEFAULT

# -*- coding: utf-8 -*-
"""插件 MCP 工具 + plugin_dev 自我开发闭环（SoulMate 触发面，工具形状=MCP 工具契约
docs/plugin-system-design.md §4/§7）：

- 插件 MCP 工具（<plugin-id>__<tool>）动态进 function-calling 工具表——UI 面板与
  SoulMate 对话共用同一 handler（同一注册桥，一处把关权限/审批）
- plugin_dev 自我开发闭环：draft（生成插件三件套草稿+自动 dry-run）→ 用户确认
  （铁律：生成≠安装，install 必须人工确认）→ install（装载：菜单/页面/工具长出）

双通道（browser_flow 同款访问面）：进程内 plugin.mcp_bridge 注册表直调优先
（agent 与宿主同进程时零开销）；不可达时走 HTTP 面（POST /plugins/{id}/tools/{tool}、
/plugins/forge/*）。后端不可达/超时/非 JSON 显式报因禁静默。

审批闸（用户铁律：「发消息/生成文档」类动作须确认）：工具声明 approval
=always/on-first-use 时，先经 request_approval 回调（ACP session/request_permission
真人弹窗）拿到放行才带 approved=true 调用；plugin_dev install 一律先确认。

配置（环境变量）：
- SOULMATE_PLUGIN_HTTP           后端基址（缺省 http://127.0.0.1:8092）
- SOULMATE_PLUGIN_HTTP_TIMEOUT   出站超时秒（缺省 20）
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Awaitable, Callable

import httpx

logger = logging.getLogger("agent.plugin_tools")

DEFAULT_HTTP_BASE = "http://127.0.0.1:8092"
DEFAULT_TIMEOUT_S = 20.0
DEFS_CACHE_TTL_S = 60.0          # 工具表缓存（_fetch_mcp_tools 同款 TTL）
PLUGIN_DEV_TOOL = "plugin_dev"

# 审批回调：async (tool_name, tool_args, decision) -> bool（ACP 真人弹窗）
ApprovalCallback = Callable[[str, dict[str, Any], dict[str, Any]], Awaitable[bool]]

PLUGIN_DEV_DESCRIPTION = (
    "插件自我开发闭环——把用户需求沉淀为常驻插件（菜单+页面+工具，UI 与对话共用）。"
    "当用户说「我需要一个XX功能」/「帮我做个XX小工具」/要新增常驻入口时使用。"
    "动作：draft=生成插件三件套草稿（plugin.yaml+main.py+ui 页面 JSON，含自带测试）"
    "并自动 dry-run 校验（清单/DSL/流程/测试不过不能装）；dry_run=重跑校验；"
    "install=安装装载（⚠️必须用户看过草稿并确认后才调用——生成≠安装，"
    "调用时会弹人工确认，拒绝则不装）；list=草稿列表；status=看草稿文件与校验报告。"
    "闭环：draft → 向用户展示摘要征求确认 → install → 菜单/页面/工具即刻长出。"
)


def plugin_dev_tool_def() -> dict[str, Any]:
    """plugin_dev 工具（function-calling 形状）"""
    return {
        "type": "function",
        "function": {
            "name": PLUGIN_DEV_TOOL,
            "description": PLUGIN_DEV_DESCRIPTION,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["draft", "dry_run", "install", "list", "status"],
                        "description": "操作：draft 生成草稿 / dry_run 校验 / install 安装（须用户确认）/ list 列草稿 / status 看草稿",
                    },
                    "id": {"type": "string", "description": "插件 id（小写连字符，如 my-notes）"},
                    "name": {"type": "string", "description": "插件显示名（draft 用）"},
                    "description": {"type": "string", "description": "一句话描述（draft 用）"},
                    "feature": {"type": "string", "description": "功能描述（draft 用）：用户要什么"},
                    "item_label": {"type": "string", "description": "记录项名称（draft 用）：如「笔记」「客户」"},
                    "icon": {"type": "string", "description": "lucide 图标名（draft 用，可省略）"},
                    "files": {
                        "type": "object",
                        "description": "自写文件（可选，高级）：{相对路径: 文件内容}，覆盖模板草稿",
                    },
                },
                "required": ["action"],
            },
        },
    }


class PluginTools:
    """插件 MCP 工具 + plugin_dev 触发面（薄转发：注册桥/HTTP 面 + 形状对齐）"""

    def __init__(self, http_base: str = "", timeout_s: float = 0.0,
                 registry: Any = None, http: Any = None):
        self.http_base = (http_base or os.environ.get("SOULMATE_PLUGIN_HTTP")
                          or DEFAULT_HTTP_BASE).rstrip("/")
        self.timeout_s = timeout_s or float(
            os.environ.get("SOULMATE_PLUGIN_HTTP_TIMEOUT", DEFAULT_TIMEOUT_S))
        self._defs_cache: tuple[float, list[dict[str, Any]]] = (0.0, [])
        self._registry_inst = registry   # 注入点（测试/特殊装配）；缺省进程内单例
        self._http = http or httpx       # 注入点（测试用 TestClient 垫片）

    # ── 工具表（function-calling 形状）──

    def handles(self, func_name: str) -> bool:
        """是否归本触发面（plugin_dev + 缓存/注册表内的插件 MCP 工具）"""
        if func_name == PLUGIN_DEV_TOOL:
            return True
        registry = self._registry()
        if registry is not None and registry.has_tool(func_name):
            return True
        _, defs = self._defs_cache
        return any(d["function"]["name"] == func_name for d in defs)

    async def tool_defs(self) -> list[dict[str, Any]]:
        """插件 MCP 工具 + plugin_dev（MCP tools/list 形状 → function-calling 形状）"""
        cached_at, cached = self._defs_cache
        if time.time() - cached_at < DEFS_CACHE_TTL_S and cached:
            return [plugin_dev_tool_def()] + cached
        defs = self._collect_defs()
        self._defs_cache = (time.time(), defs)
        return [plugin_dev_tool_def()] + defs

    def _collect_defs(self) -> list[dict[str, Any]]:
        """进程内注册表优先；不可达走 HTTP /plugins（manifest.tools 形状）"""
        registry = self._registry()
        if registry is not None:
            tools = registry.list_tools()
            if tools:
                return [
                    {
                        "type": "function",
                        "function": {
                            "name": t["name"],
                            "description": t["description"],
                            "parameters": t["inputSchema"] or {"type": "object", "properties": {}},
                        },
                    }
                    for t in tools
                ]
        try:
            resp = self._http.get(f"{self.http_base}/plugins", timeout=self.timeout_s)
            if resp.status_code != 200:
                logger.warning("[plugin-tools] /plugins HTTP %s", resp.status_code)
                return []
            defs: list[dict[str, Any]] = []
            for item in resp.json().get("plugins", []):
                manifest = item.get("manifest") or {}
                plugin_id = manifest.get("id", "")
                if not plugin_id:
                    continue
                for tool in manifest.get("tools", []):
                    defs.append({
                        "type": "function",
                        "function": {
                            "name": f"{plugin_id}__{tool['name']}",
                            "description": tool.get("description", ""),
                            "parameters": tool.get("input_schema")
                            or {"type": "object", "properties": {}},
                        },
                    })
            return defs
        except Exception as e:  # noqa: BLE001 — 显式报因禁静默（工具表缺失记日志）
            logger.warning("[plugin-tools] 工具表获取失败: %s", e)
            return []

    # ── 分发 ──

    async def run(self, func_name: str, func_args: dict[str, Any],
                  session_id: str = "",
                  request_approval: ApprovalCallback | None = None) -> str | None:
        """返回 None=不是插件工具（交回 MCP 路由）；否则返回结果文本"""
        if func_name == PLUGIN_DEV_TOOL:
            return await self.plugin_dev(func_args, request_approval)
        if not self.handles(func_name):
            return None
        meta = self._tool_meta(func_name)
        approval = str((meta or {}).get("approval", "never"))
        if approval in ("always", "on-first-use"):
            allowed = await self._ask_approval(
                request_approval, func_name, func_args,
                f"工具声明 approval={approval}（生成文档/发消息类动作须确认）")
            if not allowed:
                return f"[已拒绝] 用户未批准调用插件工具 {func_name}"
        return await self._call_tool(func_name, func_args)

    def _tool_meta(self, func_name: str) -> dict[str, Any] | None:
        registry = self._registry()
        if registry is not None:
            meta = registry.tool_meta(func_name)
            if meta is not None:
                return meta
        for d in self._defs_cache[1]:
            if d["function"]["name"] == func_name:
                return {"approval": "never", **d["function"]}
        return None

    async def _call_tool(self, full_name: str, args: dict[str, Any]) -> str:
        """进程内注册桥直调优先；HTTP 面兜底（POST /plugins/{id}/tools/{tool}）"""
        registry = self._registry()
        if registry is not None and registry.has_tool(full_name):
            try:
                outcome = await registry.call_tool_async(full_name, args, approved=True)
                return json.dumps(outcome, ensure_ascii=False, default=str)
            except Exception as e:  # noqa: BLE001 — 落 HTTP 兜底前留痕
                logger.warning("[plugin-tools] 进程内调用失败，转 HTTP: %s", e)
        plugin_id, _, tool_name = full_name.partition("__")
        if not tool_name:
            return f"错误: 插件工具名非法: {full_name!r}（应为 <plugin-id>__<tool>）"
        try:
            resp = self._http.post(
                f"{self.http_base}/plugins/{plugin_id}/tools/{tool_name}",
                json={"args": args, "approved": True},
                timeout=self.timeout_s,
            )
        except Exception as e:  # noqa: BLE001
            return f"错误: 插件工具不可达（{self.http_base}）: {e}"
        if resp.status_code == 409:
            return f"错误: 工具 {full_name} 需要用户审批后方可调用"
        if resp.status_code == 404:
            return f"错误: 工具未注册: {full_name}"
        if resp.status_code != 200:
            return f"错误: 工具调用失败（HTTP {resp.status_code}）: {resp.text[:200]}"
        try:
            return json.dumps(resp.json(), ensure_ascii=False, default=str)
        except ValueError:
            return f"错误: 工具返回非 JSON: {resp.text[:200]}"

    # ── plugin_dev（自我开发闭环）──

    async def plugin_dev(self, args: dict[str, Any],
                         request_approval: ApprovalCallback | None = None) -> str:
        action = str(args.get("action", "") or "").strip()
        draft_id = str(args.get("id", "") or "").strip()
        try:
            if action == "list":
                data = await self._forge_get("/plugins/forge/drafts")
            elif action == "status":
                data = await self._forge_get(f"/plugins/forge/drafts/{draft_id}")
            elif action == "draft":
                payload: dict[str, Any] = {}
                if args.get("files"):
                    payload["files"] = args["files"]
                    payload["spec"] = {"id": draft_id}
                else:
                    payload["spec"] = {k: args[k] for k in
                                       ("id", "name", "description", "feature",
                                        "item_label", "icon") if args.get(k)}
                data = await self._forge_post("/plugins/forge/drafts", payload)
            elif action == "dry_run":
                data = await self._forge_post(
                    f"/plugins/forge/drafts/{draft_id}/dry-run", {})
            elif action == "install":
                # 铁律：生成≠安装——install 一律先经人工确认（ACP 真人弹窗）
                allowed = await self._ask_approval(
                    request_approval, PLUGIN_DEV_TOOL, args,
                    f"安装插件草稿 {draft_id}（生成≠安装，装载后菜单/页面/工具即刻生效）")
                if not allowed:
                    return f"[已拒绝] 用户未确认安装插件 {draft_id}，草稿保留未安装"
                data = await self._forge_post(
                    f"/plugins/forge/drafts/{draft_id}/install", {"confirmed": True})
            else:
                return ("错误: action 非法（draft | dry_run | install | list | status），"
                        f"实际 {action!r}")
        except ValueError as e:
            return f"错误: {e}"
        return json.dumps(data, ensure_ascii=False, indent=1, default=str)

    async def _forge_get(self, path: str) -> Any:
        try:
            resp = self._http.get(f"{self.http_base}{path}", timeout=self.timeout_s)
        except Exception as e:  # noqa: BLE001
            raise ValueError(f"锻造坊不可达（{self.http_base}）: {e}") from e
        return self._forge_response(resp)

    async def _forge_post(self, path: str, payload: dict[str, Any]) -> Any:
        try:
            resp = self._http.post(f"{self.http_base}{path}", json=payload, timeout=self.timeout_s)
        except Exception as e:  # noqa: BLE001
            raise ValueError(f"锻造坊不可达（{self.http_base}）: {e}") from e
        return self._forge_response(resp)

    @staticmethod
    def _forge_response(resp: Any) -> Any:
        if resp.status_code in (400, 404, 409):
            detail = resp.text[:200]
            if resp.headers.get("content-type", "").startswith("application/json"):
                detail = str(resp.json().get("detail", detail))
            raise ValueError(detail)
        if resp.status_code != 200:
            raise ValueError(f"锻造坊 HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            return resp.json()
        except ValueError as e:
            raise ValueError(f"锻造坊返回非 JSON: {resp.text[:200]}") from e

    # ── 工具 ──

    def _registry(self):
        """进程内 MCP 工具注册桥（注入优先，缺省全局单例）；不可达返回 None"""
        if self._registry_inst is not None:
            return self._registry_inst
        try:
            from plugin.mcp_bridge import tool_registry
            return tool_registry
        except Exception:  # noqa: BLE001 — sys.path 不含 acp-proxy 的运行形态
            return None

    @staticmethod
    async def _ask_approval(request_approval: ApprovalCallback | None,
                            tool_name: str, tool_args: dict[str, Any],
                            reason: str) -> bool:
        if request_approval is None:
            return False
        decision = {"risk": "medium", "decision_reason": reason, "decision_id": tool_name}
        try:
            return bool(await request_approval(tool_name, tool_args, decision))
        except Exception as e:  # noqa: BLE001 — 审批通道故障=不放行（fail-safe）
            logger.error("[plugin-tools] 审批请求失败（不放行）: %s", e)
            return False

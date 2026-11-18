"""
流程工作室（建流零门槛）— 一句话建流 / 流程模板库 / 常驻保存 / YAML 导出

产品第一原则：所想即所见，所见即所得——
- 一句话建流：自然语言 → LLM（SoulMate 同款 LLMEngine）生成流程定义 JSON
  → flow_def.parse_flow 校验（失败带报因回喂重试）→ 规范 YAML 落盘（引擎覆盖件仓
  data/flow-defs/，重启自动装载、插件重载不冲掉）→ 注册进流程引擎（常驻入口）
- 流程模板库：定时对账 / 文件监听处理 / 人工审批链 / 多 Agent 协作，参数替换即用
- 可视化编辑配套：flow_def.dump_flow 规范字典 ↔ 前端表单 ↔ save 校验回写；
  YAML 导出 = 同一 dump_flow 单一真源

既有件整合（不重建）：序列化用 flow_def.dump_flow；持久化用引擎覆盖件
（FlowEngine._save_overlay，update_flow/clone_flow 同款）；删除走 engine.delete_flow
（覆盖件/暂停态/实例一并清）。红线：生成唯一入口 = LLMEngine（不旁路直连 LLM、
不藏 key、无正则兜底），失败可见（返回报因），不静默生成"看起来对"的流程。

分支排布约定（flow_engine 语义：decision 跳到目标后顺序落到底）：
互斥分支的共享收尾节点放节点列表最后——一侧分支链插在收尾节点前（执行后落入收尾），
另一侧分支直接跳收尾节点（跳过分支链）。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Awaitable, Callable

import yaml

from plugin.flow_def import (
    EvalContext,
    ExprError,
    FlowDefError,
    FlowDefinition,
    dump_flow,
    eval_expr,
    parse_flow,
)

logger = logging.getLogger("plugin.flow_studio")

ChatFn = Callable[[list[dict]], Awaitable[str]]


# ---- 规范序列化（引擎 FlowDefinition ↔ 前端编辑字典 ↔ YAML，单一真源=flow_def.dump_flow）----

def defn_to_data(defn: FlowDefinition) -> dict[str, Any]:
    """FlowDefinition → 可编辑/可导出的规范字典（parse_flow 可回灌）"""
    return dump_flow(defn)


def data_to_yaml(data: dict[str, Any]) -> str:
    """规范字典 → YAML 文本（unicode 原样、字段序稳定）"""
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)


def validate_data(data: Any, *, source: str = "<flow>") -> tuple[FlowDefinition, dict[str, Any]]:
    """校验字典 → (FlowDefinition, 规范字典)；FlowDefError 原样上抛（带字段路径）

    parse_flow 只校验结构/引用；此处补表达式语法校验（decision.when），
    可视化编辑保存前就能看见表达式错误，而不是跑到运行时才炸。
    """
    defn = parse_flow(data, source=source)
    for node in defn.nodes:
        if node.type == "decision" and node.when:
            try:
                eval_expr(node.when, EvalContext())
            except ExprError as e:
                raise FlowDefError(f"节点 {node.id}.when 表达式非法: {e}") from e
    return defn, dump_flow(defn)


# ---- 常驻保存（引擎覆盖件仓：重启自动装载、插件重载不冲掉）----

def save_user_flow(data: dict[str, Any], *, engine: Any) -> dict[str, Any]:
    """校验 → 覆盖件落盘（data/flow-defs/<id>.yaml）→ 注册进流程引擎（同 id 覆盖）

    新建与更新同一入口：覆盖件优先于插件定义（引擎 register_flow 同 id 覆盖语义）。
    """
    defn, canonical = validate_data(data, source="<save>")
    engine._save_overlay(defn)          # 与 update_flow/clone_flow 同一覆盖件仓
    engine.register_flow(defn, "user")
    logger.info("流程已保存（常驻）: %s → %s", defn.id,
                engine._overlay_path(defn.id))
    return {
        "flow_id": defn.id,
        "name": defn.name,
        "version": defn.version,
        "nodes": len(defn.nodes),
        "triggers": [t.type for t in defn.triggers],
        "path": str(engine._overlay_path(defn.id)),
        "yaml": data_to_yaml(canonical),
        "definition": canonical,
    }


# ---- 流程模板库（场景模板：参数替换即用）----

def _labels(zh: str, en: str, ja: str) -> dict[str, str]:
    return {"zh": zh, "en": en, "ja": ja}


TEMPLATES: list[dict[str, Any]] = [
    {
        "id": "scheduled-reconcile",
        "name": _labels("定时对账", "Scheduled Reconciliation", "定期照合"),
        "description": _labels(
            "每天定时拉取业务数据做对账核对，发现差异立刻提醒",
            "Pull business data on a schedule, flag discrepancies and alert",
            "毎日決まった時刻に業務データを照合し、差異があれば通知"),
        "scene": _labels("定时对账", "Scheduled reconciliation", "定期照合"),
        "params": [
            {"key": "flow_id", "type": "text", "default": "reconcile-check",
             "label": _labels("流程 id（kebab-case）", "Flow id (kebab-case)", "フロー id（kebab-case）")},
            {"key": "flow_name", "type": "text", "default": "定时对账",
             "label": _labels("流程名称", "Flow name", "フロー名")},
            {"key": "cron", "type": "cron", "default": "0 22 * * *",
             "label": _labels("对账时间（cron 分 时 日 月 周）", "Schedule (cron min hour dom mon dow)", "実行時刻（cron）")},
            {"key": "source_tool", "type": "text", "default": "business-opportunity__opportunity_stats",
             "label": _labels("取数工具（plugin__tool）", "Data tool (plugin__tool)", "取得ツール（plugin__tool）")},
            {"key": "check_expr", "type": "text", "default": "$.fetch.count > 0",
             "label": _labels("差异判断表达式", "Discrepancy expression", "差異判定式")},
            {"key": "channel", "type": "text", "default": "in-app",
             "label": _labels("提醒渠道", "Notify channel", "通知チャネル")},
            {"key": "alert_message", "type": "text", "default": "对账发现差异，请及时核对",
             "label": _labels("差异提醒文案", "Alert message", "差異通知文")},
        ],
        "flow_yaml": (
            'id: "{{flow_id}}"\n'
            'name: "{{flow_name}}"\n'
            "version: 1\n"
            "triggers:\n"
            "  - type: cron\n"
            '    cron: "{{cron}}"\n'
            "  - type: manual\n"
            "nodes:\n"
            "  - id: fetch\n"
            "    type: tool\n"
            '    tool: "{{source_tool}}"\n'
            "    args: {}\n"
            "    retry:\n"
            "      max: 2\n"
            "      backoff: fixed\n"
            "      base_seconds: 2\n"
            "    timeout_seconds: 60\n"
            "    on_error: skip\n"
            "  - id: check\n"
            "    type: decision\n"
            '    when: "{{check_expr}}"\n'
            "    then: alert\n"
            "    else: finish\n"
            "  - id: alert\n"
            "    type: action\n"
            "    actions:\n"
            "      - type: notify\n"
            '        channel: "{{channel}}"\n'
            '        message: "{{alert_message}}"\n'
            "  - id: finish\n"
            "    type: action\n"
            "    actions:\n"
            "      - type: notify\n"
            '        channel: "{{channel}}"\n'
            '        message: "{{flow_name}} 完成"\n'
            "observability:\n"
            "  retain_runs: 30\n"
        ),
    },
    {
        "id": "file-watch-process",
        "name": _labels("文件监听处理", "File Watch & Process", "ファイル監視処理"),
        "description": _labels(
            "监听文件事件，派工处理并按结果提醒（事件触发示例）",
            "React to file events: dispatch processing, notify by outcome",
            "ファイルイベントを監視し、処理を派工、結果に応じて通知"),
        "scene": _labels("文件监听处理", "File watch", "ファイル監視"),
        "params": [
            {"key": "flow_id", "type": "text", "default": "file-watch",
             "label": _labels("流程 id（kebab-case）", "Flow id (kebab-case)", "フロー id（kebab-case）")},
            {"key": "flow_name", "type": "text", "default": "文件监听处理",
             "label": _labels("流程名称", "Flow name", "フロー名")},
            {"key": "event", "type": "text", "default": "file.created",
             "label": _labels("监听事件名", "Event name", "イベント名")},
            {"key": "task", "type": "text", "default": "处理新到文件：解析内容、归档并回报处理结果（ok/失败原因）",
             "label": _labels("处理任务（交付物：result.ok）", "Processing task (deliver result.ok)", "処理タスク（納品：result.ok）")},
            {"key": "user", "type": "text", "default": "用户",
             "label": _labels("处理人", "Assignee", "担当者")},
            {"key": "timeout_hours", "type": "number", "default": "24",
             "label": _labels("处理期限（小时）", "Deadline (hours)", "期限（時間）")},
            {"key": "channel", "type": "text", "default": "in-app",
             "label": _labels("提醒渠道", "Notify channel", "通知チャネル")},
        ],
        "flow_yaml": (
            'id: "{{flow_id}}"\n'
            'name: "{{flow_name}}"\n'
            "version: 1\n"
            "triggers:\n"
            "  - type: event\n"
            '    event: "{{event}}"\n'
            "  - type: manual\n"
            "nodes:\n"
            "  - id: process\n"
            "    type: human\n"
            '    user: "{{user}}"\n'
            '    task: "{{task}}"\n'
            '    form: "处理结果（result.ok=true/false + 说明）"\n'
            "    timeout_hours: {{timeout_hours}}\n"
            "    on_timeout: skip\n"
            "  - id: check\n"
            "    type: decision\n"
            '    when: "$.process.result.ok == true"\n'
            "    then: done\n"
            "    else: remind\n"
            "  - id: remind\n"
            "    type: action\n"
            "    actions:\n"
            "      - type: notify\n"
            '        channel: "{{channel}}"\n'
            '        message: "{{flow_name}}：处理未完成，请跟进"\n'
            "  - id: done\n"
            "    type: action\n"
            "    actions:\n"
            "      - type: notify\n"
            '        channel: "{{channel}}"\n'
            '        message: "{{flow_name}}：处理完成"\n'
            "observability:\n"
            "  retain_runs: 30\n"
        ),
    },
    {
        "id": "approval-chain",
        "name": _labels("人工审批链", "Approval Chain", "承認チェーン"),
        "description": _labels(
            "提交 → 多级人工审批（超时处置可配）→ 结果通知",
            "Submit → multi-step human approval (configurable timeout) → notify",
            "提出 → 多段人間承認（タイムアウト設定可） → 結果通知"),
        "scene": _labels("人工审批链", "Approval chain", "承認チェーン"),
        "params": [
            {"key": "flow_id", "type": "text", "default": "approval-chain",
             "label": _labels("流程 id（kebab-case）", "Flow id (kebab-case)", "フロー id（kebab-case）")},
            {"key": "flow_name", "type": "text", "default": "人工审批链",
             "label": _labels("流程名称", "Flow name", "フロー名")},
            {"key": "form", "type": "text", "default": "审批单（同意/驳回 + 意见）",
             "label": _labels("审批表单说明", "Approval form", "承認フォーム")},
            {"key": "approver1", "type": "text", "default": "用户",
             "label": _labels("一级审批人", "First approver", "一次承認者")},
            {"key": "approver2", "type": "text", "default": "用户",
             "label": _labels("二级审批人", "Second approver", "二次承認者")},
            {"key": "timeout_hours", "type": "number", "default": "72",
             "label": _labels("单级期限（小时）", "Deadline per step (hours)", "各段期限（時間）")},
            {"key": "channel", "type": "text", "default": "in-app",
             "label": _labels("通知渠道", "Notify channel", "通知チャネル")},
        ],
        "flow_yaml": (
            'id: "{{flow_id}}"\n'
            'name: "{{flow_name}}"\n'
            "version: 1\n"
            "triggers:\n"
            "  - type: manual\n"
            "nodes:\n"
            "  - id: submit\n"
            "    type: action\n"
            "    actions:\n"
            "      - type: notify\n"
            '        channel: "{{channel}}"\n'
            '        message: "{{flow_name}}：已提交，进入审批链"\n'
            "  - id: approve-l1\n"
            "    type: human\n"
            '    user: "{{approver1}}"\n'
            '    task: "一级审批：{{form}}"\n'
            '    form: "{{form}}"\n'
            "    timeout_hours: {{timeout_hours}}\n"
            "    on_timeout: fail\n"
            "  - id: approve-l2\n"
            "    type: human\n"
            '    user: "{{approver2}}"\n'
            '    task: "二级审批：{{form}}"\n'
            '    form: "{{form}}"\n'
            "    timeout_hours: {{timeout_hours}}\n"
            "    on_timeout: fail\n"
            "  - id: finish\n"
            "    type: action\n"
            "    actions:\n"
            "      - type: notify\n"
            '        channel: "{{channel}}"\n'
            '        message: "{{flow_name}}：审批链全部通过"\n'
            "observability:\n"
            "  retain_runs: 30\n"
        ),
    },
    {
        "id": "multi-agent-collab",
        "name": _labels("多 Agent 协作", "Multi-Agent Collaboration", "マルチエージェント協働"),
        "description": _labels(
            "SoulMate 分工干活（带预算）→ 人工验收 → 收尾通知",
            "SoulMate agents work in sequence (with budget) → human review → notify",
            "SoulMate が分業（予算付き）→ 人間検収 → 完了通知"),
        "scene": _labels("多 Agent 协作", "Multi-agent", "マルチエージェント"),
        "params": [
            {"key": "flow_id", "type": "text", "default": "agent-collab",
             "label": _labels("流程 id（kebab-case）", "Flow id (kebab-case)", "フロー id（kebab-case）")},
            {"key": "flow_name", "type": "text", "default": "多 Agent 协作",
             "label": _labels("流程名称", "Flow name", "フロー名")},
            {"key": "task_a", "type": "text", "default": "收集并整理工单/数据源的原始材料，输出摘要",
             "label": _labels("Agent A 任务", "Agent A task", "Agent A タスク")},
            {"key": "task_b", "type": "text", "default": "基于摘要深入分析，输出结论与建议",
             "label": _labels("Agent B 任务", "Agent B task", "Agent B タスク")},
            {"key": "reviewer", "type": "text", "default": "用户",
             "label": _labels("验收人", "Reviewer", "検収者")},
            {"key": "max_tokens", "type": "number", "default": "100000",
             "label": _labels("单 Agent token 预算（0=不限）", "Token budget per agent (0=unlimited)", "Agent ごとのトークン予算")},
            {"key": "channel", "type": "text", "default": "in-app",
             "label": _labels("通知渠道", "Notify channel", "通知チャネル")},
        ],
        "flow_yaml": (
            'id: "{{flow_id}}"\n'
            'name: "{{flow_name}}"\n'
            "version: 1\n"
            "triggers:\n"
            "  - type: manual\n"
            "nodes:\n"
            "  - id: collect\n"
            "    type: agent\n"
            '    user: "用户"\n'
            "    target_agent: soulmate\n"
            '    task: "{{task_a}}"\n'
            "    timeout_hours: 24\n"
            "    on_timeout: fail\n"
            "    budget:\n"
            "      max_tokens: {{max_tokens}}\n"
            "  - id: analyze\n"
            "    type: agent\n"
            '    user: "用户"\n'
            "    target_agent: soulmate\n"
            '    task: "{{task_b}}（上游产出：$.collect.result）"\n'
            "    timeout_hours: 24\n"
            "    on_timeout: fail\n"
            "    budget:\n"
            "      max_tokens: {{max_tokens}}\n"
            "  - id: review\n"
            "    type: human\n"
            '    user: "{{reviewer}}"\n'
            '    task: "验收分析结论（通过/驳回+意见）"\n'
            '    form: "验收意见"\n'
            "    timeout_hours: 48\n"
            "    on_timeout: fail\n"
            "    review: true\n"
            "  - id: finish\n"
            "    type: action\n"
            "    actions:\n"
            "      - type: notify\n"
            '        channel: "{{channel}}"\n'
            '        message: "{{flow_name}}：协作完成（已验收）"\n'
            "observability:\n"
            "  retain_runs: 30\n"
        ),
    },
]

_TEMPLATE_MAP = {item["id"]: item for item in TEMPLATES}


def list_templates() -> list[dict[str, Any]]:
    """模板库清单（三语 name/description/params + 默认值）"""
    result = []
    for item in TEMPLATES:
        result.append({
            "id": item["id"],
            "name": item["name"],
            "description": item["description"],
            "scene": item["scene"],
            "params": [
                {"key": p["key"], "type": p["type"], "default": p["default"],
                 "label": p["label"]}
                for p in item["params"]
            ],
        })
    return result


_PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


def _substitute(text: str, params: dict[str, Any], defaults: dict[str, str],
                used: set[str]) -> str:
    def _replace(match: re.Match) -> str:
        key = match.group(1)
        used.add(key)
        value = params.get(key, defaults.get(key, ""))
        # 值嵌在 YAML 双引号内：转义引号/反斜杠防破格式
        return str(value).replace("\\", "\\\\").replace('"', '\\"')

    return _PLACEHOLDER.sub(_replace, text)


def instantiate_template(template_id: str, params: dict[str, Any] | None = None,
                         *, source: str = "<template>") -> dict[str, Any]:
    """模板 + 参数 → 校验过的流程定义（未注册；预览/保存由调用方决定）"""
    item = _TEMPLATE_MAP.get(template_id)
    if item is None:
        raise FlowDefError(f"模板不存在: {template_id!r}（{sorted(_TEMPLATE_MAP)}）")
    params = dict(params or {})
    defaults = {p["key"]: str(p["default"]) for p in item["params"]}
    text = _substitute(item["flow_yaml"], params, defaults, set())
    raw = yaml.safe_load(text)
    defn, canonical = validate_data(raw, source=source)
    return {
        "template_id": template_id,
        "definition": canonical,
        "yaml": data_to_yaml(canonical),
        "flow_id": defn.id,
        "name": defn.name,
    }


# ---- 一句话建流（自然语言 → LLM 生成 → 校验）----

FLOW_SCHEMA_GUIDE = """你是 OpenMate 流程引擎的流程定义生成器。用户用自然语言描述业务流程，
你输出 flow_engine 可装载的流程定义 JSON（不是 YAML、不是解释）。

【顶层字段】
- id: 流程 id，kebab-case（^[a-z][a-z0-9-]{0,63}$），用英文短横线风格
- name: 流程名（用用户的语言，简洁业务名）
- version: 1
- triggers: 非空数组。元素 {"type":"cron","cron":"分 时 日 月 周"}（标准 5 字段）
  或 {"type":"event","event":"事件名"} 或 {"type":"manual"}。
  用户给的时间（如"每天晚上""每周一9点"）翻译成 cron；同时总是附带 {"type":"manual"} 便于手动跑。
- nodes: 非空数组，按执行顺序排列（顺序推进；decision 跳转）
- observability: {"retain_runs": 30}

【节点通用字段】id（kebab-case，唯一）、type、retry:{max,backoff:fixed|exponential,base_seconds}、
timeout_seconds（软预算只记不杀）、on_error: "fail"|"skip"|"goto:<节点id>"

【节点类型】
1. tool —— 调工具：tool(必须是下方可用工具清单里的 <plugin>__<tool> 全名)、args(对象)。
   args 的值若是整串 $.路径 会被解析为上游输出（如 "$.fetch.count"）。
2. decision —— 条件分支：when(表达式)、then(节点id)、else(节点id，字段名就叫 else)。
3. loop —— 遍历：over(如 "$.fetch.items")、body(节点id或数组，body 节点只经 loop 执行)。
4. approval —— 人工审批闸：form(表单说明)、timeout_hours(默认72)、on_timeout:"fail"|"skip"。
5. action —— 动作：actions 非空数组，元素 {type:"notify"|"emit-event"|"update-panel"|"compress-handoff", channel, message, event, panel}。
   提醒/通知一律用 {type:"notify", channel:"in-app", message:"..."}。
6. parallel —— 并行：branches 非空数组 [{id,tool,args}]、max_concurrency。
7. agent —— 委派 SoulMate 干活（A2A）：task(任务指令)、user 或 role 二选一（收件人/组织角色）、
   target_agent:"soulmate"、timeout_hours、on_timeout、budget:{max_tokens,max_cost_usd,max_duration_seconds}、
   package:"full"|"compact"、review:true 时为验收门。
8. human —— 派工给人：task(任务指令+交付物要求)、user 或 role 二选一、form(交付表单说明)、
   timeout_hours、on_timeout、review:true 时驳回不算完成。

【安全表达式】只支持取值/比较/布尔组合：
$.<节点id>.<字段>（节点输出）、$item.<字段>（loop 当前项）、$run.id/$run.trigger/$run.inputs；
运算符 == != > >= < <= && || ! 与括号；字面量 数字/字符串('单引号'或"双引号")/true/false/null。
human/agent 节点交付后输出 {result: 交付物, ...}，引用交付结果写 $.<节点id>.result.<字段>。

【分支排布铁律】decision 跳到目标后会顺序执行到节点列表末尾。互斥 if/else 必须这样排：
共享收尾节点（通常是 notify）放列表最后；一侧分支链插在收尾节点之前（跳到它→执行→落入收尾）；
另一侧分支直接跳收尾节点（跳过分支链）。示例：节点序 [check(decision, then=remind, else=finish),
remind(action), finish(action)]——漏发走 remind+finish，正常只走 finish。
若两侧分支都有动作，各分支链末尾放一个 {type:"decision", when:"true", then:"finish", else:"finish"}
跳转节点，finish 放最后。

【生成规则】
- tool 节点的 tool 只能取自下方可用工具清单；需求里没有匹配工具的步骤，
  用 human 节点（个人提醒/检查场景，user 默认"用户"）或 agent 节点（委派 SoulMate）承接，不许编造工具名。
- 需求含"提醒/通知/告诉我"→ 必须有 notify 动作节点。
- 每个流程都要有收尾 notify 节点（记录完成），放在列表最后。
- 不输出 markdown 代码块、不输出解释，只输出一个 JSON 对象（本体即流程定义）。
"""


def _available_tools_lines(tools: list[dict[str, Any]] | None = None) -> str:
    if tools is None:
        try:
            from plugin.mcp_bridge import tool_registry
            tools = tool_registry.list_tools()
        except Exception as e:  # noqa: BLE001 — 工具清单不可达不阻断生成
            logger.warning("工具清单获取失败: %s", e)
            tools = []
    if not tools:
        return "（当前无可用工具，所有业务步骤用 human/agent 节点承接）"
    lines = []
    for tool in tools:
        desc = str(tool.get("description", "")).strip().splitlines()
        lines.append(f"- {tool['name']}: {desc[0] if desc else ''}")
    return "\n".join(lines)


def extract_json(text: str) -> dict[str, Any]:
    """从 LLM 输出提取 JSON 对象（容忍 ```json 围栏与前后杂文）"""
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.S)
    if fence:
        cleaned = fence.group(1).strip()
    start = cleaned.find("{")
    if start < 0:
        raise FlowDefError(f"生成结果里找不到 JSON 对象: {text[:200]!r}")
    decoder = json.JSONDecoder()
    try:
        data, _ = decoder.raw_decode(cleaned[start:])
    except json.JSONDecodeError as e:
        raise FlowDefError(f"生成结果 JSON 解析失败: {e}") from e
    if not isinstance(data, dict):
        raise FlowDefError("生成结果不是 JSON 对象")
    return data


async def generate_from_nl(prompt: str, *, chat: ChatFn | None = None,
                           tools: list[dict[str, Any]] | None = None,
                           max_attempts: int = 3) -> dict[str, Any]:
    """一句话建流：自然语言 → LLM 生成 → parse_flow 校验（失败回喂重试）

    返回 {ok, definition, yaml, attempts, error?}；全部失败时 ok=false 带报因（禁兜底假造）。
    """
    prompt = (prompt or "").strip()
    if not prompt:
        return {"ok": False, "error": "需求描述为空", "attempts": 0}
    if chat is None:
        chat = _default_chat
    tool_lines = _available_tools_lines(tools)
    messages: list[dict] = [
        {"role": "system", "content": FLOW_SCHEMA_GUIDE},
        {"role": "user", "content": f"【可用工具清单】\n{tool_lines}\n\n【流程需求】\n{prompt}"},
    ]
    last_error = ""
    for attempt in range(1, max_attempts + 1):
        raw = await chat(messages)
        try:
            data = extract_json(raw or "")
            defn, canonical = validate_data(data, source=f"<nl#attempt{attempt}>")
        except FlowDefError as e:
            last_error = str(e)
            logger.info("一句话建流第 %d 次校验失败: %s", attempt, last_error)
            messages.append({"role": "assistant", "content": (raw or "")[:4000]})
            messages.append({"role": "user", "content": (
                f"你上次输出的流程定义校验失败：{last_error}\n"
                "请修正后重新输出完整 JSON（只输出 JSON 对象本体）。")})
            continue
        return {
            "ok": True,
            "definition": canonical,
            "yaml": data_to_yaml(canonical),
            "flow_id": defn.id,
            "name": defn.name,
            "attempts": attempt,
        }
    return {"ok": False, "error": last_error or "生成失败", "attempts": max_attempts}


async def _default_chat(messages: list[dict]) -> str:
    """默认生成方 = LLMEngine（SoulMate 同款推理层，配置单一真源）"""
    from agent.llm_engine import LLMEngine
    engine = LLMEngine()
    return await engine.chat(messages)

"""
流程定义 — YAML 流程定义解析/校验 + 安全表达式 + cron 匹配

设计文档: docs/plugin-system-design.md §5
- 定义格式：triggers(cron|event|manual) → nodes(tool|decision|loop|approval|action)
- 节点级 retry(max/backoff/base_seconds) + on_error(fail|skip|goto:<node>)
- 表达式只允许取值/比较/布尔组合，不允许任意代码（安全+可观测）：
  $.<node-id>.<field>（节点输出寻址）、$item.<field>（loop 当前项）、
  $run.id/$run.trigger/$run.inputs（运行上下文）
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# ---- 常量 ----

NODE_TYPES = {"tool", "decision", "loop", "approval", "action", "parallel",
              "agent", "human"}
TRIGGER_TYPES = {"cron", "event", "manual"}
ON_ERROR_PATTERN = re.compile(r"^(fail|skip|goto:[a-z][a-z0-9-]{0,63})$")
NODE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
TOOL_FULL_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,63}__[a-z][a-z0-9_-]{0,63}$")
BACKOFF_VALUES = {"fixed", "exponential"}
ACTION_TYPES = {"notify", "emit-event", "update-panel", "compress-handoff"}
ON_TIMEOUT_VALUES = {"fail", "skip"}


class FlowDefError(ValueError):
    """流程定义校验失败（消息带字段路径）"""


# ---- 定义数据结构 ----

@dataclass(frozen=True)
class TriggerSpec:
    type: str                      # cron | event | manual
    cron: str = ""
    event: str = ""


@dataclass(frozen=True)
class RetrySpec:
    max: int = 0                   # 失败后的最大重试次数（总尝试 = 1 + max）
    backoff: str = "fixed"         # fixed | exponential
    base_seconds: float = 1.0

    def delay(self, retry_index: int) -> float:
        """第 retry_index 次重试前的等待（retry_index 从 1 起）"""
        if self.backoff == "exponential":
            return self.base_seconds * (2 ** (retry_index - 1))
        return self.base_seconds


@dataclass(frozen=True)
class ActionSpec:
    type: str                      # notify | emit-event | update-panel
    channel: str = ""
    message: str = ""
    event: str = ""
    panel: str = ""


@dataclass(frozen=True)
class ParallelBranchSpec:
    """parallel 节点分支（子任务）：一条分支=一次工具子任务调用。"""
    id: str
    tool: str = ""
    args: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BudgetSpec:
    """协作节点预算（YAML budget: 块）——0=不限；超出只记账 over_budget，禁硬杀。"""
    max_tokens: int = 0
    max_cost_usd: float = 0.0
    max_duration_seconds: float = 0.0


@dataclass(frozen=True)
class NodeSpec:
    id: str
    type: str
    # tool
    tool: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    retry: RetrySpec = field(default_factory=RetrySpec)
    timeout_seconds: float = 0.0   # 软预算：超时只记录 over_budget，绝不杀执行（铁律）
    on_error: str = "fail"         # fail | skip | goto:<node-id>
    # decision
    when: str = ""
    then: str = ""
    else_: str = ""
    # loop
    over: str = ""
    body: tuple[str, ...] = ()
    # approval
    form: str = ""
    timeout_hours: float = 72.0
    on_timeout: str = "fail"       # fail | skip
    # action
    actions: tuple[ActionSpec, ...] = ()
    # parallel（子代理治理运行时基线：并发乘数×2 超出排队 + 统一轮数上限）
    branches: tuple[ParallelBranchSpec, ...] = ()
    max_concurrency: int = 0       # 0 = 引擎默认基础并发
    # agent / human 协作节点（工单收发台：SoulMate 干活回流 / 人收单交付回流）
    task: str = ""                 # 任务指令（整串 $ 路径可解析节点输出）
    user: str = ""                 # 显式收件人（人/员工）
    role: str = ""                 # 组织角色路由（接 RBAC 角色表）
    target_agent: str = "soulmate"  # agent 节点：A2A 目标 SoulMate
    budget: BudgetSpec = field(default_factory=BudgetSpec)  # 预算隔离（软预算禁硬杀）
    package: str = "full"          # 任务包档位（E1：full | compact 防上下文爆）
    review: bool = False           # 人 工节点=验收门（E4：驳回→不算节点完成）


@dataclass(frozen=True)
class FlowDefinition:
    id: str
    name: str
    version: int
    triggers: tuple[TriggerSpec, ...]
    nodes: tuple[NodeSpec, ...]
    retain_runs: int = 50
    source: str = ""

    @property
    def node_map(self) -> dict[str, NodeSpec]:
        return {n.id: n for n in self.nodes}

    @property
    def body_node_ids(self) -> frozenset[str]:
        """loop 节点拥有的 body 节点：顺序推进时跳过（只经 loop 逐项执行）"""
        ids: set[str] = set()
        for node in self.nodes:
            if node.type == "loop":
                ids.update(node.body)
        return frozenset(ids)

    def next_node_id(self, node_id: str) -> str | None:
        """顺序后继（跳过 loop body 节点）；末尾返回 None"""
        ids = [n.id for n in self.nodes if n.id not in self.body_node_ids]
        try:
            index = ids.index(node_id)
        except ValueError:
            return None
        return ids[index + 1] if index + 1 < len(ids) else None

    def first_node_id(self) -> str:
        for node in self.nodes:
            if node.id not in self.body_node_ids:
                return node.id
        raise FlowDefError(f"流程 {self.id} 没有可执行的入口节点")


# ---- 解析与校验 ----

def _require(data: dict, key: str, typ: type, where: str) -> Any:
    if key not in data:
        raise FlowDefError(f"{where}.{key} 缺失")
    value = data[key]
    if not isinstance(value, typ):
        raise FlowDefError(f"{where}.{key} 类型应为 {typ.__name__}，实际 {type(value).__name__}")
    return value


def _parse_retry(data: Any, where: str) -> RetrySpec:
    if data is None:
        return RetrySpec()
    if not isinstance(data, dict):
        raise FlowDefError(f"{where} 应为映射")
    max_retries = data.get("max", 0)
    if not isinstance(max_retries, int) or max_retries < 0:
        raise FlowDefError(f"{where}.max 应为非负整数")
    backoff = data.get("backoff", "fixed")
    if backoff not in BACKOFF_VALUES:
        raise FlowDefError(f"{where}.backoff 非法: {backoff!r}（fixed | exponential）")
    base = data.get("base_seconds", 1)
    if not isinstance(base, (int, float)) or base < 0:
        raise FlowDefError(f"{where}.base_seconds 应为非负数")
    return RetrySpec(max=max_retries, backoff=backoff, base_seconds=float(base))


def _parse_actions(data: Any, where: str) -> tuple[ActionSpec, ...]:
    if not isinstance(data, list) or not data:
        raise FlowDefError(f"{where} 应为非空列表")
    actions: list[ActionSpec] = []
    for index, item in enumerate(data):
        aw = f"{where}[{index}]"
        if not isinstance(item, dict):
            raise FlowDefError(f"{aw} 应为映射")
        typ = _require(item, "type", str, aw)
        if typ not in ACTION_TYPES:
            raise FlowDefError(f"{aw}.type 非法: {typ!r}（{sorted(ACTION_TYPES)}）")
        actions.append(
            ActionSpec(
                type=typ,
                channel=str(item.get("channel", "")),
                message=str(item.get("message", "")),
                event=str(item.get("event", "")),
                panel=str(item.get("panel", "")),
            )
        )
    return tuple(actions)


def _parse_budget(data: Any, where: str) -> BudgetSpec:
    """协作节点预算块：max_tokens/max_cost_usd/max_duration_seconds（非负，0=不限）"""
    if data is None:
        return BudgetSpec()
    if not isinstance(data, dict):
        raise FlowDefError(f"{where} 应为映射")
    budget = BudgetSpec(
        max_tokens=data.get("max_tokens", 0),
        max_cost_usd=data.get("max_cost_usd", 0),
        max_duration_seconds=data.get("max_duration_seconds", 0),
    )
    if not isinstance(budget.max_tokens, int) or budget.max_tokens < 0:
        raise FlowDefError(f"{where}.max_tokens 应为非负整数")
    for key, value in (("max_cost_usd", budget.max_cost_usd),
                       ("max_duration_seconds", budget.max_duration_seconds)):
        if not isinstance(value, (int, float)) or value < 0:
            raise FlowDefError(f"{where}.{key} 应为非负数")
    return budget


def _parse_node(data: Any, index: int) -> NodeSpec:
    where = f"nodes[{index}]"
    if not isinstance(data, dict):
        raise FlowDefError(f"{where} 应为映射")
    node_id = _require(data, "id", str, where)
    if not NODE_ID_PATTERN.match(node_id):
        raise FlowDefError(f"{where}.id 非法: {node_id!r}")
    typ = _require(data, "type", str, where)
    if typ not in NODE_TYPES:
        raise FlowDefError(f"{where}.type 非法: {typ!r}（{sorted(NODE_TYPES)}）")

    on_error = str(data.get("on_error", "fail"))
    if not ON_ERROR_PATTERN.match(on_error):
        raise FlowDefError(f"{where}.on_error 非法: {on_error!r}（fail | skip | goto:<node-id>）")

    timeout_seconds = data.get("timeout_seconds", 0)
    if not isinstance(timeout_seconds, (int, float)) or timeout_seconds < 0:
        raise FlowDefError(f"{where}.timeout_seconds 应为非负数")

    kwargs: dict[str, Any] = {
        "id": node_id,
        "type": typ,
        "retry": _parse_retry(data.get("retry"), f"{where}.retry"),
        "timeout_seconds": float(timeout_seconds),
        "on_error": on_error,
    }
    if typ == "tool":
        tool = _require(data, "tool", str, where)
        if not TOOL_FULL_PATTERN.match(tool):
            raise FlowDefError(f"{where}.tool 非法: {tool!r}（应为 <plugin-id>__<tool-name>）")
        args = data.get("args", {})
        if not isinstance(args, dict):
            raise FlowDefError(f"{where}.args 应为映射")
        kwargs["tool"] = tool
        kwargs["args"] = args
    elif typ == "decision":
        kwargs["when"] = _require(data, "when", str, where)
        kwargs["then"] = _require(data, "then", str, where)
        kwargs["else_"] = _require(data, "else", str, where)
    elif typ == "parallel":
        raw_branches = _require(data, "branches", list, where)
        if not raw_branches:
            raise FlowDefError(f"{where}.branches 应为非空列表")
        branches: list[ParallelBranchSpec] = []
        seen: set[str] = set()
        for bi, item in enumerate(raw_branches):
            bw = f"{where}.branches[{bi}]"
            if not isinstance(item, dict):
                raise FlowDefError(f"{bw} 应为映射")
            bid = _require(item, "id", str, bw)
            if not NODE_ID_PATTERN.match(bid):
                raise FlowDefError(f"{bw}.id 非法: {bid!r}")
            if bid in seen:
                raise FlowDefError(f"{bw}.id 重复: {bid!r}")
            seen.add(bid)
            tool = _require(item, "tool", str, bw)
            if not TOOL_FULL_PATTERN.match(tool):
                raise FlowDefError(f"{bw}.tool 非法: {tool!r}（应为 <plugin-id>__<tool-name>）")
            args = item.get("args", {})
            if not isinstance(args, dict):
                raise FlowDefError(f"{bw}.args 应为映射")
            branches.append(ParallelBranchSpec(id=bid, tool=tool, args=args))
        max_concurrency = data.get("max_concurrency", 0)
        if not isinstance(max_concurrency, int) or max_concurrency < 0:
            raise FlowDefError(f"{where}.max_concurrency 应为非负整数")
        kwargs["branches"] = tuple(branches)
        kwargs["max_concurrency"] = max_concurrency
    elif typ == "loop":
        over = _require(data, "over", str, where)
        body = data.get("body")
        if isinstance(body, str):
            body = [body]
        if not isinstance(body, list) or not body or not all(isinstance(b, str) for b in body):
            raise FlowDefError(f"{where}.body 应为节点 id（字符串或列表）")
        kwargs["over"] = over
        kwargs["body"] = tuple(body)
    elif typ == "approval":
        timeout_hours = data.get("timeout_hours", 72)
        if not isinstance(timeout_hours, (int, float)) or timeout_hours <= 0:
            raise FlowDefError(f"{where}.timeout_hours 应为正数")
        on_timeout = str(data.get("on_timeout", "fail"))
        if on_timeout not in ON_TIMEOUT_VALUES:
            raise FlowDefError(f"{where}.on_timeout 非法: {on_timeout!r}（fail | skip）")
        kwargs["form"] = str(data.get("form", ""))
        kwargs["timeout_hours"] = float(timeout_hours)
        kwargs["on_timeout"] = on_timeout
    elif typ in ("agent", "human"):
        # 协作节点：任务发给员工的 SoulMate（agent）或人（human），按组织角色路由
        task_text = _require(data, "task", str, where)
        if not task_text.strip():
            raise FlowDefError(f"{where}.task 不可为空")
        user = str(data.get("user", "") or "")
        role = str(data.get("role", "") or "")
        if not user and not role:
            raise FlowDefError(f"{where} 协作节点须指定 user 或 role（组织角色路由）")
        timeout_hours = data.get("timeout_hours", 72)
        if not isinstance(timeout_hours, (int, float)) or timeout_hours <= 0:
            raise FlowDefError(f"{where}.timeout_hours 应为正数")
        on_timeout = str(data.get("on_timeout", "fail"))
        if on_timeout not in ON_TIMEOUT_VALUES:
            raise FlowDefError(f"{where}.on_timeout 非法: {on_timeout!r}（fail | skip）")
        target_agent = str(data.get("target_agent", "soulmate") or "soulmate")
        package = str(data.get("package", "full") or "full")
        if package not in ("full", "compact"):
            raise FlowDefError(f"{where}.package 非法: {package!r}（full | compact）")
        kwargs["task"] = task_text
        kwargs["user"] = user
        kwargs["role"] = role
        kwargs["target_agent"] = target_agent
        kwargs["form"] = str(data.get("form", ""))
        kwargs["timeout_hours"] = float(timeout_hours)
        kwargs["on_timeout"] = on_timeout
        kwargs["budget"] = _parse_budget(data.get("budget"), f"{where}.budget")
        kwargs["package"] = package
        kwargs["review"] = bool(data.get("review", False))
    elif typ == "action":
        kwargs["actions"] = _parse_actions(data.get("actions"), f"{where}.actions")
    return NodeSpec(**kwargs)


def parse_flow(data: Any, *, source: str = "<flow>") -> FlowDefinition:
    """校验流程定义 YAML/JSON 映射，返回强类型 FlowDefinition"""
    if not isinstance(data, dict):
        raise FlowDefError(f"{source} 顶层应为映射")
    flow_id = _require(data, "id", str, source)
    if not NODE_ID_PATTERN.match(flow_id):
        raise FlowDefError(f"{source}.id 非法: {flow_id!r}")
    name = _require(data, "name", str, source)
    version = data.get("version", 1)
    if not isinstance(version, int):
        raise FlowDefError(f"{source}.version 应为整数")

    raw_triggers = _require(data, "triggers", list, source)
    if not raw_triggers:
        raise FlowDefError(f"{source}.triggers 应为非空列表")
    triggers: list[TriggerSpec] = []
    for index, item in enumerate(raw_triggers):
        tw = f"{source}.triggers[{index}]"
        if not isinstance(item, dict):
            raise FlowDefError(f"{tw} 应为映射")
        typ = _require(item, "type", str, tw)
        if typ not in TRIGGER_TYPES:
            raise FlowDefError(f"{tw}.type 非法: {typ!r}（{sorted(TRIGGER_TYPES)}）")
        if typ == "cron":
            cron = _require(item, "cron", str, tw)
            try:
                cron_matches(cron, _ANY_TIME)
            except ValueError as e:
                raise FlowDefError(f"{tw}.cron 非法: {cron!r}（{e}）") from e
            triggers.append(TriggerSpec(type="cron", cron=cron))
        elif typ == "event":
            event = _require(item, "event", str, tw)
            triggers.append(TriggerSpec(type="event", event=event))
        else:
            triggers.append(TriggerSpec(type="manual"))

    raw_nodes = _require(data, "nodes", list, source)
    if not raw_nodes:
        raise FlowDefError(f"{source}.nodes 应为非空列表")
    nodes = [_parse_node(item, i) for i, item in enumerate(raw_nodes)]
    seen: set[str] = set()
    for node in nodes:
        if node.id in seen:
            raise FlowDefError(f"节点 id 重复: {node.id!r}")
        seen.add(node.id)

    defn = FlowDefinition(
        id=flow_id,
        name=name,
        version=version,
        triggers=tuple(triggers),
        nodes=tuple(nodes),
        source=source,
    )
    # 引用完整性：decision/loop/on_error 指向的节点必须存在
    node_ids = set(seen)
    for node in nodes:
        for ref, where in _node_refs(node):
            if ref not in node_ids:
                raise FlowDefError(f"节点 {node.id} 引用不存在的节点: {ref!r}（{where}）")
    defn.first_node_id  # 校验存在可执行入口节点（纯 body 节点流程在此拒绝）

    obs = data.get("observability", {})
    if obs is None:
        obs = {}
    if not isinstance(obs, dict):
        raise FlowDefError(f"{source}.observability 应为映射")
    retain = obs.get("retain_runs", 50)
    if not isinstance(retain, int) or retain < 1:
        raise FlowDefError(f"{source}.observability.retain_runs 应为正整数")
    return FlowDefinition(
        id=flow_id,
        name=name,
        version=version,
        triggers=tuple(triggers),
        nodes=tuple(nodes),
        retain_runs=retain,
        source=source,
    )


def _node_refs(node: NodeSpec) -> list[tuple[str, str]]:
    refs: list[tuple[str, str]] = []
    if node.type == "decision":
        refs.append((node.then, "then"))
        refs.append((node.else_, "else"))
    if node.type == "loop":
        for body_id in node.body:
            refs.append((body_id, "body"))
    if node.on_error.startswith("goto:"):
        refs.append((node.on_error.split(":", 1)[1], "on_error"))
    return refs


def load_flow_file(path) -> FlowDefinition:
    """从 YAML 文件读取并校验流程定义"""
    import yaml

    from pathlib import Path

    p = Path(path)
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise FlowDefError(f"流程定义 YAML 解析失败: {e}") from e
    return parse_flow(raw, source=str(p))


# ---- 安全表达式（取值/比较/布尔组合，禁止任意代码）----

class ExprError(ValueError):
    """表达式非法或求值失败（禁静默：错误显式回注）"""


_TOKEN_PATTERN = re.compile(
    r"\s*(?:"
    r"(?P<number>-?\d+(?:\.\d+)?)"
    r"|(?P<string>\"[^\"]*\"|'[^']*')"
    r"|(?P<true>true)|(?P<false>false)|(?P<null>null)"
    r"|(?P<and>&&)|(?P<or>\|\|)|(?P<not>!)"
    r"|(?P<eq>==)|(?P<ne>!=)|(?P<ge>>=)|(?P<le><=)|(?P<gt>>)|(?P<lt><)"
    r"|(?P<lparen>\()|(?P<rparen>\))"
    r"|(?P<path>\$(?:\.[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*|item(?:\.[A-Za-z0-9_-]+)*|run(?:\.[A-Za-z0-9_-]+)*))"
    r")"
)


@dataclass
class _Tok:
    kind: str
    value: Any


def _tokenize(text: str) -> list[_Tok]:
    tokens: list[_Tok] = []
    pos = 0
    while pos < len(text):
        match = _TOKEN_PATTERN.match(text, pos)
        if match is None:
            rest = text[pos:].strip()
            if not rest:
                break
            raise ExprError(f"表达式非法（无法解析: {rest!r}）")
        pos = match.end()
        kind = match.lastgroup or ""
        value = match.group(kind)
        if kind == "number":
            tokens.append(_Tok("number", float(value) if "." in value else int(value)))
        elif kind == "string":
            tokens.append(_Tok("string", value[1:-1]))
        elif kind == "true":
            tokens.append(_Tok("bool", True))
        elif kind == "false":
            tokens.append(_Tok("bool", False))
        elif kind == "null":
            tokens.append(_Tok("null", None))
        elif kind == "path":
            tokens.append(_Tok("path", value))
        else:
            tokens.append(_Tok(kind, value))
    return tokens


class _Parser:
    def __init__(self, tokens: list[_Tok]):
        self.tokens = tokens
        self.pos = 0

    def peek(self) -> _Tok | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def take(self) -> _Tok:
        if self.pos >= len(self.tokens):
            raise ExprError("表达式意外结束")
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def parse(self) -> Any:
        value = self.parse_or()
        if self.pos != len(self.tokens):
            raise ExprError(f"表达式多余内容: {self.tokens[self.pos].value!r}")
        return value

    def parse_or(self) -> Any:
        left = self.parse_and()
        while (tok := self.peek()) and tok.kind == "or":
            self.take()
            right = self.parse_and()
            left = _resolve(left) or _resolve(right)
        return left

    def parse_and(self) -> Any:
        left = self.parse_not()
        while (tok := self.peek()) and tok.kind == "and":
            self.take()
            right = self.parse_not()
            left = _resolve(left) and _resolve(right)
        return left

    def parse_not(self) -> Any:
        tok = self.peek()
        if tok and tok.kind == "not":
            self.take()
            return not _resolve(self.parse_not())
        return self.parse_compare()

    def parse_compare(self) -> Any:
        left = self.parse_atom()
        tok = self.peek()
        if tok and tok.kind in ("eq", "ne", "ge", "le", "gt", "lt"):
            self.take()
            right = self.parse_atom()
            return _compare(tok.kind, _resolve(left), _resolve(right))
        return left

    def parse_atom(self) -> Any:
        tok = self.take()
        if tok.kind == "lparen":
            value = self.parse_or()
            closing = self.take()
            if closing.kind != "rparen":
                raise ExprError("括号不匹配")
            return value
        if tok.kind in ("number", "string", "bool", "null", "path", "literal"):
            return tok
        raise ExprError(f"表达式意外的记号: {tok.value!r}")


def _resolve(value: Any) -> Any:
    return value.value if isinstance(value, _Tok) else value


def _compare(op: str, left: Any, right: Any) -> bool:
    try:
        if op == "eq":
            return left == right
        if op == "ne":
            return left != right
        if op == "ge":
            return left >= right
        if op == "le":
            return left <= right
        if op == "gt":
            return left > right
        return left < right
    except TypeError:
        return False


@dataclass
class EvalContext:
    """表达式求值上下文：节点输出 / loop 当前项 / 运行上下文"""

    outputs: dict[str, Any] = field(default_factory=dict)
    item: Any = None
    run: dict[str, Any] = field(default_factory=dict)


def resolve_path(path: str, ctx: EvalContext) -> Any:
    """解析 $路径：$.<node-id>.<field>、$item.<field>、$run.<field>"""
    if path.startswith("$."):
        segments = path[2:].split(".")
        cursor: Any = ctx.outputs.get(segments[0])
        segments = segments[1:]
    elif path == "$item" or path.startswith("$item."):
        cursor = ctx.item
        segments = path[len("$item"):].lstrip(".").split(".") if path != "$item" else []
    elif path == "$run" or path.startswith("$run."):
        cursor = ctx.run
        segments = path[len("$run"):].lstrip(".").split(".") if path != "$run" else []
    else:
        raise ExprError(f"非法路径: {path!r}（$.<node-id>… | $item… | $run…）")
    for segment in segments:
        if isinstance(cursor, dict):
            cursor = cursor.get(segment)
        elif isinstance(cursor, list) and segment.isdigit():
            index = int(segment)
            cursor = cursor[index] if index < len(cursor) else None
        else:
            return None
        if cursor is None:
            return None
    return cursor


_PATH_ONLY = re.compile(
    r"^\$(?:\.[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*|item(?:\.[A-Za-z0-9_-]+)*|run(?:\.[A-Za-z0-9_-]+)*)$"
)


def eval_expr(text: str, ctx: EvalContext) -> Any:
    """求值表达式（安全子集）。路径返回原值（不强制布尔）。"""
    tokens = _tokenize(text)
    if not tokens:
        raise ExprError("空表达式")
    if len(tokens) == 1 and tokens[0].kind == "path":
        return resolve_path(tokens[0].value, ctx)
    # 路径记号就地解析为字面值（比较/布尔组合在解析期只见值，不见代码）
    resolved = [
        _Tok("literal", resolve_path(tok.value, ctx)) if tok.kind == "path" else tok
        for tok in tokens
    ]
    result = _Parser(resolved).parse()
    return _resolve(result)


def resolve_value(value: Any, ctx: EvalContext) -> Any:
    """递归解析 args 值：整串为 $路径 → 解析为原值；dict/list 递归；其余原样"""
    if isinstance(value, str) and _PATH_ONLY.match(value):
        return resolve_path(value, ctx)
    if isinstance(value, dict):
        return {k: resolve_value(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_value(v, ctx) for v in value]
    return value


# ---- cron 匹配（5 字段：分 时 日 月 周）----

import datetime as _dt

_ANY_TIME = _dt.datetime(2000, 1, 1, 0, 0)  # 只用于格式校验的哨兵时刻


def _parse_field(text: str, low: int, high: int, where: str) -> set[int]:
    values: set[int] = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            raise ValueError(f"{where} 含空项")
        step = 1
        if "/" in part:
            part, step_text = part.split("/", 1)
            if not step_text.isdigit() or int(step_text) < 1:
                raise ValueError(f"{where} 步长非法: {step_text!r}")
            step = int(step_text)
        if part == "*":
            start, end = low, high
        elif "-" in part:
            a, b = part.split("-", 1)
            if not (a.lstrip("-").isdigit() and b.lstrip("-").isdigit()):
                raise ValueError(f"{where} 范围非法: {part!r}")
            start, end = int(a), int(b)
        elif part.isdigit() or (part.startswith("-") and part[1:].isdigit()):
            start = end = int(part)
        else:
            raise ValueError(f"{where} 项非法: {part!r}")
        if start < low or end > high or start > end:
            raise ValueError(f"{where} 超界: {part!r}（{low}-{high}）")
        values.update(range(start, end + 1, step))
    return values


def cron_matches(expr: str, when: _dt.datetime) -> bool:
    """标准 5 字段 cron 是否在 when（分钟粒度）触发"""
    fields = expr.split()
    if len(fields) != 5:
        raise ValueError(f"cron 应为 5 字段，实际 {len(fields)} 字段")
    minute, hour, dom, month, dow = fields
    if when.minute not in _parse_field(minute, 0, 59, "cron.分"):
        return False
    if when.hour not in _parse_field(hour, 0, 23, "cron.时"):
        return False
    if when.month not in _parse_field(month, 1, 12, "cron.月"):
        return False
    day_ok = when.day in _parse_field(dom, 1, 31, "cron.日")
    # 周字段：0/7 均为周日（datetime.weekday()：周一=0 … 周日=6）
    dow_values = _parse_field(dow, 0, 7, "cron.周")
    if 7 in dow_values:
        dow_values.add(0)
    dow_ok = ((when.weekday() + 1) % 7) in dow_values
    if dom == "*" and dow == "*":
        return True
    if dom == "*":
        return dow_ok
    if dow == "*":
        return day_ok
    return day_ok or dow_ok  # 日/周同时限定时取并集（Vixie cron 语义）

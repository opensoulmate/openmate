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

import fnmatch
import posixpath
import re
from dataclasses import dataclass, field, replace as _dc_replace
from typing import Any, Iterable, Mapping, Sequence

# ---- 常量 ----

NODE_TYPES = {"tool", "decision", "loop", "approval", "action", "parallel",
              "agent", "human", "verify"}
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
    write_scope: tuple[str, ...] = ()   # 写域声明（并行分支互踩静态拒载）


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
    # 写域声明（shared_scope_pairs 静态拒载：并行节点写域重叠即拒绝装载）
    write_scope: tuple[str, ...] = ()
    # settle 验收标准（agent/human：交付回流时求值，未满足→拒 ok 修复轮）
    acceptance: str = ""
    # verify 验收节点（Test·verified 翻档面：内容级探针 + 非执行者验收）
    for_: str = ""                 # 被验收节点 id（YAML 键 `for`；缺省=顺序前驱）
    probe: str = ""                # 内容级探针工具（<plugin>__<tool>，验收证据来源）
    verifier: str = ""             # 验收者身份（缺省 probe:<tool>；≠执行者才许翻档）
    max_fixes: int = 2             # 验证不过修复轮上限（超出→重规划 on_error）


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


# ---- 并行写域隔离（shared_scope_pairs 移植自 dag_core.py:145-209，纯 stdlib）----

def _scope_path(entry: str) -> tuple[tuple[str, ...], str, str]:
    """写域条目 → (它写在哪些目录段下, 它的 glob, 它的具体文件)。

    ``\\\\`` 按 ``/`` 读、大小写折叠、其余过 posixpath.normpath（``./``、重复/结尾
    ``/``、``x/..`` 都不改变语义；开头 ``/`` 保留为首个 ``/`` 段）。含 ``*``/``?``
    的段：写域落在 glob 段之前的目录下，整串保留为 pattern（fnmatch 的 ``*`` 本就
    跨 ``/``，``src/**/*.py`` 必须命中 ``src/x.py``），``[`` 转义（``app/[slug]/x.tsx``
    更常是文件名而非字符类）。条目既非 glob 也不是目录写法（结尾 ``/``）且末段带
    后缀时，它就是那个具体文件。
    """
    text = entry.replace("\\", "/").casefold()
    absolute = text.startswith("/")
    normal = posixpath.normpath(text.lstrip("/") or ".")
    segments = [] if normal == "." else normal.split("/")
    globbed = [i for i, s in enumerate(segments) if "*" in s or "?" in s]
    under = (( "/",) if absolute else ()) + tuple(
        segments[:globbed[0]] if globbed else segments)
    whole = ("/" if absolute else "") + "/".join(segments)
    pattern = whole.replace("**/", "*").replace("[", "[[]") if globbed else ""
    concrete = (not globbed and not text.endswith("/")
                and bool(segments) and posixpath.splitext(segments[-1])[1])
    return under, pattern, whole if concrete else ""


def _meets(first: tuple[tuple[str, ...], str, str],
           second: tuple[tuple[str, ...], str, str]) -> bool:
    """两条写域是否可能写同一路径：一目录包含另一；glob 与具体文件按 fnmatch。

    glob 对 glob、目录对目录只要目录相交就算 meet（并发互踩无法静态排除）。
    """
    (f_under, f_glob, f_file), (s_under, s_glob, s_file) = first, second
    if f_under != s_under[:len(f_under)] and s_under != f_under[:len(s_under)]:
        return False
    if f_glob and s_file:
        return fnmatch.fnmatchcase(s_file, f_glob)
    if s_glob and f_file:
        return fnmatch.fnmatchcase(f_file, s_glob)
    return True


def shared_scope_pairs(
    scopes: Sequence[tuple[str, Iterable[str]]],
    parents: Mapping[str, Iterable[str]], *, as_paths: bool = False,
) -> list[tuple[str, str, list[str], bool]]:
    """枚举写域相交的节点对：(甲, 乙, 共享条目, 是否已有直接边)。

    条目默认按书写原样比较；as_paths=True 走 _meets 路径语义
    （``src`` 命中 ``src/x.py``、``src/a`` 不命中 ``src/ab``、
    ``*.md`` 命中 ``docs/a.md`` 不命中 ``src/x.py``）。
    有直接边（一者直接依赖另一者）= 顺序执行不并发，可共享写域；无边即冲突。
    """
    edges = {(node, parent) for node, listed in parents.items()
             for parent in listed}
    indexed = [(node, {entry: _scope_path(entry) if as_paths else ((entry,), "", "")
                       for entry in scope})
               for node, scope in scopes]
    pairs: list[tuple[str, str, list[str], bool]] = []
    for position, (first, first_scope) in enumerate(indexed):
        for second, second_scope in indexed[position + 1:]:
            shared: set[str] = set()
            for f_entry, f_path in first_scope.items():
                for s_entry, s_path in second_scope.items():
                    if _meets(f_path, s_path):
                        shared.update((f_entry, s_entry))
            if shared:
                pairs.append((first, second, sorted(shared),
                              (first, second) in edges or (second, first) in edges))
    return pairs


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


def _parse_write_scope(data: Any, where: str) -> tuple[str, ...]:
    """写域声明：非空字符串列表（路径/目录/glob 三态，见 _scope_path）"""
    if data is None:
        return ()
    if not isinstance(data, list) or not all(isinstance(s, str) and s.strip() for s in data):
        raise FlowDefError(f"{where} 应为非空字符串列表（路径/目录/glob 写域声明）")
    return tuple(s.strip() for s in data)


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
        "write_scope": _parse_write_scope(data.get("write_scope"), f"{where}.write_scope"),
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
            branches.append(ParallelBranchSpec(
                id=bid, tool=tool, args=args,
                write_scope=_parse_write_scope(item.get("write_scope"), f"{bw}.write_scope")))
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
        acceptance = str(data.get("acceptance", "") or "")
        if acceptance and not acceptance.strip():
            raise FlowDefError(f"{where}.acceptance 不可为空白")
        kwargs["acceptance"] = acceptance
        for_target = str(data.get("for", "") or "")
        if for_target and not NODE_ID_PATTERN.match(for_target):
            raise FlowDefError(f"{where}.for 非法: {for_target!r}")
        kwargs["for_"] = for_target   # review 验收门的验收目标（E4→双轴翻档）
    elif typ == "verify":
        # 验收节点：内容级探针（probe）/ 验收条件（when）至少声明一个
        probe = str(data.get("probe", "") or "")
        if probe and not TOOL_FULL_PATTERN.match(probe):
            raise FlowDefError(f"{where}.probe 非法: {probe!r}（应为 <plugin-id>__<tool-name>）")
        when = str(data.get("when", "") or "")
        if not probe and not when:
            raise FlowDefError(
                f"{where} 验收节点须声明内容级探针 probe 或验收条件 when（报成功没用）")
        for_target = str(data.get("for", "") or "")
        if for_target and not NODE_ID_PATTERN.match(for_target):
            raise FlowDefError(f"{where}.for 非法: {for_target!r}")
        max_fixes = data.get("max_fixes", 2)
        if not isinstance(max_fixes, int) or not 0 <= max_fixes <= 10:
            raise FlowDefError(f"{where}.max_fixes 应为 0-10 整数")
        kwargs["probe"] = probe
        kwargs["when"] = when
        kwargs["for_"] = for_target
        kwargs["verifier"] = str(data.get("verifier", "") or "")
        kwargs["max_fixes"] = max_fixes
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

    # verify 节点缺省验收目标 = 顺序前驱（非 loop body 节点）
    body_ids: set[str] = set()
    for node in nodes:
        if node.type == "loop":
            body_ids.update(node.body)
    resolved_nodes: list[NodeSpec] = []
    prev_id = ""
    for node in nodes:
        if node.type == "verify" and not node.for_:
            if not prev_id:
                raise FlowDefError(
                    f"verify 节点 {node.id!r} 无验收目标（须声明 for 或置于产活节点之后）")
            node = _dc_replace(node, for_=prev_id)
        if node.id not in body_ids:
            prev_id = node.id
        resolved_nodes.append(node)
    nodes = resolved_nodes

    # 并行写域隔离（shared_scope_pairs 静态拒载）：parallel 分支同时跑（彼此无边），
    # 写域重叠即拒绝装载——"加顺序依赖或拆开 scope"
    for node in nodes:
        if node.type != "parallel":
            continue
        scopes = [(b.id, b.write_scope) for b in node.branches if b.write_scope]
        conflicts = [p for p in shared_scope_pairs(scopes, {}, as_paths=True)
                     if not p[3]]
        if conflicts:
            detail = "; ".join(
                f"{a}↔{b} 共享写域 {shared}" for a, b, shared, _ in conflicts)
            raise FlowDefError(
                f"{source} parallel 节点 {node.id!r} 并行分支写域重叠: {detail}"
                f"（并发互踩静态拒载：加顺序依赖或拆开 scope）")

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


def _dump_retry(retry: RetrySpec) -> dict[str, Any] | None:
    if retry == RetrySpec():
        return None
    data: dict[str, Any] = {"max": retry.max}
    if retry.backoff != "fixed":
        data["backoff"] = retry.backoff
    if retry.base_seconds != 1.0:
        data["base_seconds"] = retry.base_seconds
    return data


def _dump_node(node: NodeSpec) -> dict[str, Any]:
    """NodeSpec → 可 parse_flow 回读的映射（克隆/编辑参数持久化用）"""
    data: dict[str, Any] = {"id": node.id, "type": node.type}
    retry = _dump_retry(node.retry)
    if retry is not None:
        data["retry"] = retry
    if node.timeout_seconds:
        data["timeout_seconds"] = node.timeout_seconds
    if node.on_error != "fail":
        data["on_error"] = node.on_error
    if node.write_scope:
        data["write_scope"] = list(node.write_scope)
    if node.type == "tool":
        data["tool"] = node.tool
        if node.args:
            data["args"] = dict(node.args)
    elif node.type == "decision":
        data["when"] = node.when
        data["then"] = node.then
        data["else"] = node.else_
    elif node.type == "parallel":
        data["branches"] = [
            {k: v for k, v in
             {"id": b.id, "tool": b.tool, **({"args": dict(b.args)} if b.args else {}),
              **({"write_scope": list(b.write_scope)} if b.write_scope else {})}.items()}
            for b in node.branches
        ]
        if node.max_concurrency:
            data["max_concurrency"] = node.max_concurrency
    elif node.type == "loop":
        data["over"] = node.over
        data["body"] = list(node.body)
    elif node.type == "approval":
        if node.form:
            data["form"] = node.form
        if node.timeout_hours != 72.0:
            data["timeout_hours"] = node.timeout_hours
        if node.on_timeout != "fail":
            data["on_timeout"] = node.on_timeout
    elif node.type in ("agent", "human"):
        data["task"] = node.task
        if node.user:
            data["user"] = node.user
        if node.role:
            data["role"] = node.role
        if node.target_agent != "soulmate":
            data["target_agent"] = node.target_agent
        if node.form:
            data["form"] = node.form
        if node.timeout_hours != 72.0:
            data["timeout_hours"] = node.timeout_hours
        if node.on_timeout != "fail":
            data["on_timeout"] = node.on_timeout
        budget = _dump_budget(node.budget)
        if budget:
            data["budget"] = budget
        if node.package != "full":
            data["package"] = node.package
        if node.review:
            data["review"] = True
        if node.acceptance:
            data["acceptance"] = node.acceptance
        if node.for_:
            data["for"] = node.for_
    elif node.type == "verify":
        data["for"] = node.for_
        if node.probe:
            data["probe"] = node.probe
        if node.when:
            data["when"] = node.when
        if node.verifier:
            data["verifier"] = node.verifier
        if node.max_fixes != 2:
            data["max_fixes"] = node.max_fixes
    elif node.type == "action":
        data["actions"] = [
            {k: v for k, v in
             {"type": a.type, "channel": a.channel, "message": a.message,
              "event": a.event, "panel": a.panel}.items() if v}
            for a in node.actions
        ]
    return data


def _dump_budget(budget: BudgetSpec) -> dict[str, Any]:
    data: dict[str, Any] = {}
    if budget.max_tokens:
        data["max_tokens"] = budget.max_tokens
    if budget.max_cost_usd:
        data["max_cost_usd"] = budget.max_cost_usd
    if budget.max_duration_seconds:
        data["max_duration_seconds"] = budget.max_duration_seconds
    return data


def dump_flow(defn: FlowDefinition) -> dict[str, Any]:
    """FlowDefinition → 可 parse_flow 回读/可存 YAML 的映射（编辑参数/克隆序列化）"""
    triggers: list[dict[str, Any]] = []
    for trigger in defn.triggers:
        item: dict[str, Any] = {"type": trigger.type}
        if trigger.type == "cron":
            item["cron"] = trigger.cron
        elif trigger.type == "event":
            item["event"] = trigger.event
        triggers.append(item)
    return {
        "id": defn.id,
        "name": defn.name,
        "version": defn.version,
        "triggers": triggers,
        "nodes": [_dump_node(n) for n in defn.nodes],
        "observability": {"retain_runs": defn.retain_runs},
    }


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
    if node.type == "verify" and node.for_:
        refs.append((node.for_, "for"))
    if node.type in ("agent", "human") and node.for_:
        refs.append((node.for_, "for"))   # review 验收门目标
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

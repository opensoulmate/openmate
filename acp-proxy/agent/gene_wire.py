"""gene技能提炼执行账本的wire格式（POST /api/gene/skill/extract 契约）。

轨迹账本条目是扁平dict {name, arguments, result_preview}，extract契约要的是
工具调用形态 [{"function": {"name", "arguments"}}, ...]（opensoul
skill_learner.extract_from_execution 按 tc["function"]["name"] 取名）。

旧实现 `str(tool_calls_log)[:1000]` 两个都错（P0 wire bug，生产技能提炼链全灭）：
1. Python repr不是JSON（单引号/True/False）→ opensoul json.loads失败→
   tool_calls=[]→extract_from_execution空契约直接None→400"没提炼出技能"；
2. 即便能解析，扁平形态没有function包装→tools_used全空名、tool_sequence空。

本模块是单一序列化真源 + 预算化截断（AIHawk SHOWN/SENT双预算：截断必须
显式标记）：超预算丢中段、保头尾（kilocode Truncate双端保留：开头是流程
入口、结尾是产出步，中段多是同类重复），以 __TRUNCATED__ 哨兵条目显式记
dropped/total——消费方（skill_learner）把 __x__ dunder名当元数据过滤，不进
tool_sequence（否则同一任务不同截断点→不同skill_id碎片化）。
"""

import json

# 截断哨兵（dunder名=元数据条目，消费方过滤，不进tool_sequence/模板）
SENTINEL_TRUNCATED = "__TRUNCATED__"

# 默认载荷预算（字符）：localhost内网调用，取8K——常规回合（<16条工具）不截断
DEFAULT_BUDGET = 8000
# 单条arguments值裁剪长度（result_preview本来就不进wire）
DEFAULT_ARG_CHARS = 300


def _clip_value(v, arg_chars: int):
    """裁剪arguments里的值：标量直过，容器浅递归限量，长字符串截断。"""
    if isinstance(v, (int, float, bool)) or v is None:
        return v
    if isinstance(v, dict):
        return {
            str(k)[:64]: _clip_value(x, arg_chars)
            for k, x in list(v.items())[:16]
        }
    if isinstance(v, list):
        return [_clip_value(x, arg_chars) for x in v[:16]]
    return str(v)[:arg_chars]


def tool_calls_for_extract(
    tool_calls_log, budget: int = DEFAULT_BUDGET, arg_chars: int = DEFAULT_ARG_CHARS
) -> str:
    """把回合工具账本序列化成extract契约的JSON字符串（可直接作execution_log）。

    - 空账本 → "[]"（消费方空契约照旧不提炼，语义不变）；
    - 预算内 → [{"function": {"name", "arguments"}}, ...]完整序列；
    - 超预算 → 头尾各留一半预算、中段丢弃，末尾拼 __TRUNCATED__ 哨兵
      （arguments={"dropped": N, "total": M}）显式记账，截断不静默。
    """
    entries = []
    for tc in tool_calls_log or []:
        if not isinstance(tc, dict):
            continue
        entries.append({
            "function": {
                "name": str(tc.get("name", ""))[:64],
                "arguments": _clip_value(tc.get("arguments"), arg_chars),
            }
        })

    def _enc(e):
        return json.dumps(e, ensure_ascii=False)

    costs = [len(_enc(e)) + 1 for e in entries]  # +1=逗号/换行
    total = 2 + sum(costs)  # "[]"
    if total <= budget:
        return json.dumps(entries, ensure_ascii=False)

    half = max((budget - 100) // 2, 0)  # 留100给哨兵+括号
    head, used = [], 0
    for e, c in zip(entries, costs):
        if used + c > half:
            break
        head.append(e)
        used += c
    tail_pairs, used = [], 0
    for e, c in reversed(list(zip(entries, costs))):
        if len(head) + len(tail_pairs) >= len(entries):
            break
        if used + c > half:
            break
        tail_pairs.append((e, c))
        used += c
    tail = [e for e, _ in reversed(tail_pairs)]
    if not head:
        # 极端情形：首条就超半预算（巨条目）——至少保首条，账不悬空。
        # 首条不可能同时在tail里（tail准入条件同为≤half），无重复风险。
        head = [entries[0]]
    dropped = len(entries) - len(head) - len(tail)
    marker = {"function": {
        "name": SENTINEL_TRUNCATED,
        "arguments": {"dropped": dropped, "total": len(entries)},
    }}
    return json.dumps(head + [marker] + tail, ensure_ascii=False)

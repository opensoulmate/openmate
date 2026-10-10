"""
LLM安全防护模块 — 通用工具函数

防护机制来源：
- finish_reason检测+自动续写：Aider (base_coder.py:1894-1911, 1492-1505)
- omission占位符检测：Gemini CLI
- 输出预算管理：Cline (6000字符限制)
"""

import logging
import re

logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────

MAX_CONTINUATION = 3  # 最大续写次数
MAX_OUTPUT_TOKENS = 4096  # 单次输出token上限
MAX_TOOL_OUTPUT_CHARS = 8000  # 工具输出字符上限
# 10.1（2026-10-11）：8→12，随MAX_ATTEMPTS 3→5联动（5发×4段=20理论最坏，
# 硬顶12仍严格小于理论最坏=封顶仍真实生效）
MAX_TURN_SENDS = 12  # 逻辑回合HTTP发送硬顶（续写×重试联合封顶）


class TurnSendBudget:
    """逻辑回合HTTP发送预算 — 续写×重试联合封顶（单一真源）

    问题（2026-10-01销账多轮挂账项）：续写（最多MAX_CONTINUATION+1段）与HTTP重试
    （每段MAX_ATTEMPTS发）此前各自独立计数、互不知情——4段×3发=理论12发/逻辑回合
    无联合上界，极端截断+限流一轮可狂发十余个HTTP请求（latency与配额双杀）。

    语义：每次真实HTTP发送前扣1（spend()），余额不足返回False=调用方不得再发送，
    显式失败/显式截断标记（AIHawk"截断必须显式标记"），不静默不多发。预算对象随
    递归线程传递（与_chain同款）：一次chat_stream*/chat()调用=一个逻辑回合=一个
    预算实例，重试/续写/降级链切换共享同一额度。

    参照：kilocode Truncate服务双限（行数/字节）封顶语义 + AIHawk SHOWN/SENT
    双预算（截断必须显式标记）。
    """

    def __init__(self, limit: int = MAX_TURN_SENDS):
        self.limit = limit
        self.spent = 0

    def spend(self) -> bool:
        """扣1发；余额不足返回False且不扣减（调用方不得再发HTTP请求）"""
        if self.spent >= self.limit:
            return False
        self.spent += 1
        return True

    @property
    def remaining(self) -> int:
        return self.limit - self.spent


# ── Omission占位符检测（Gemini CLI方案）────────────────

_OMISSION_PATTERNS = [
    re.compile(r"\(rest of methods \.\.\.\)"),
    re.compile(r"# \.\.\. existing code \.\.\."),
    re.compile(r"// \.\.\. rest of the function"),
    re.compile(r"\[\.\.\.\]"),
    re.compile(r"\(truncated\)"),
    re.compile(r"// \.\.\. remaining code"),
    re.compile(r"# \.\.\. rest of"),
    re.compile(r"/\* \.\.\. existing code \.\.\. \*/"),
    re.compile(r"<!-- \.\.\. existing code \.\.\. -->"),
]

# 公开别名：跨模块单一真源引用面（llm_engine/llm_gateway/dna_evolution统一引用此处，
# 禁止各文件再维护pattern副本——旧三副本已发生漂移：dna_evolution 7条 vs 此处9条）
OMISSION_PATTERNS = _OMISSION_PATTERNS


def check_omission(text: str) -> str | None:
    """检测LLM输出中的省略占位符

    来源：Gemini CLI omission-placeholder检测器

    返回：
        None — 无占位符
        str — 匹配的占位符描述
    """
    for pattern in _OMISSION_PATTERNS:
        match = pattern.search(text)
        if match:
            return f"Omission placeholder detected: {match.group()}"
    return None


# ── finish_reason检测（Aider方案）─────────────────────

def check_finish_reason(choice: dict) -> str:
    """检查LLM响应的finish_reason

    来源：Aider base_coder.py:1894-1911

    返回：
        "stop" — 正常结束
        "length" — 截断
        "unknown" — 未知状态
    """
    finish_reason = choice.get("finish_reason", "stop")
    if finish_reason == "length":
        return "length"
    elif finish_reason == "stop":
        return "stop"
    else:
        return "unknown"


def build_continuation_messages(
    messages: list[dict],
    partial_content: str,
    error_hint: str | None = None,
) -> list[dict]:
    """构建续写消息（Aider方案）

    来源：Aider base_coder.py:1492-1505

    参数：
        messages — 原始消息列表
        partial_content — 已生成的部分内容
        error_hint — 可选的错误提示（如omission检测结果）
    """
    new_messages = list(messages)

    if error_hint:
        # 有错误提示时，要求LLM重新完整输出
        new_messages.append({"role": "assistant", "content": partial_content})
        new_messages.append({
            "role": "user",
            "content": (
                f"Your previous output had issues: {error_hint}\n\n"
                "Please provide the COMPLETE implementation without any shortcuts, "
                "placeholders, or omissions. Start from the beginning."
            ),
        })
    else:
        # 普通续写
        new_messages.append({"role": "assistant", "content": partial_content})
        new_messages.append({
            "role": "user",
            "content": "Continue from where you left off. Do not repeat any content already provided.",
        })

    return new_messages


# ── 安全LLM调用封装 ──────────────────────────────────
# 旧 safe_llm_call（本文件曾内联第三份续写/omission防护循环）已于 2026-09-29
# 合并轮删除：①零生产调用点死路径（仅测试docstring提及）；②失败契约反模式
# ——非200静默 return full_content / except break，正是本轮传输合并要消灭的
# "静默半截"旧契约。带防护的完整调用请用
# utils.llm_transport.complete_with_protection（重试/park/LLMCallError契约/续写
# 防护单一真源），业务调用面走 utils.llm_gateway.llm_call。

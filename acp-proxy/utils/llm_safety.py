"""
LLM安全防护模块 — 通用工具函数

防护机制来源：
- finish_reason检测+自动续写：Aider (base_coder.py:1894-1911, 1492-1505)
- omission占位符检测：Gemini CLI
- 输出预算管理：Cline (6000字符限制)
"""

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────

MAX_CONTINUATION = 3  # 最大续写次数
MAX_OUTPUT_TOKENS = 4096  # 单次输出token上限
MAX_TOOL_OUTPUT_CHARS = 8000  # 工具输出字符上限


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

async def safe_llm_call(
    client: Any,
    base_url: str,
    headers: dict,
    model: str,
    messages: list[dict],
    temperature: float = 0.7,
    max_tokens: int = MAX_OUTPUT_TOKENS,
    max_continuation: int = MAX_CONTINUATION,
    strand_id: str = "",
) -> str:
    """安全的LLM调用，集成所有防护机制

    防护层：
    1. finish_reason=length检测 + 自动续写（Aider方案）
    2. omission占位符检测 + 重新生成（Gemini CLI方案）
    3. 输出字符数限制（Cline方案）
    """
    full_content = ""
    current_messages = list(messages)

    for attempt in range(max_continuation + 1):
        try:
            resp = await client.post(
                f"{base_url}/chat/completions",
                headers=headers,
                json={
                    "model": model,
                    "messages": current_messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
            )

            if resp.status_code != 200:
                logger.error(f"[{strand_id}] LLM call failed: {resp.status_code} {resp.text[:200]}")
                return full_content

            data = resp.json()
            choice = data.get("choices", [{}])[0]
            content = choice.get("message", {}).get("content", "")
            finish_reason = choice.get("finish_reason", "stop")

            full_content += content

            # 检查1: omission占位符
            omission = check_omission(full_content)
            if omission:
                logger.warning(f"[{strand_id}] {omission}")
                current_messages = build_continuation_messages(
                    current_messages, full_content, error_hint=omission
                )
                full_content = ""  # 重置，让LLM重新完整输出
                continue

            # 检查2: finish_reason
            if finish_reason == "length":
                logger.warning(
                    f"[{strand_id}] LLM output truncated (finish_reason=length), "
                    f"attempt {attempt+1}/{max_continuation}"
                )
                current_messages = build_continuation_messages(current_messages, full_content)
                continue

            # 正常结束
            break

        except Exception as e:
            logger.error(f"[{strand_id}] LLM call exception: {e}")
            break

    # 输出字符数限制
    if len(full_content) > MAX_TOOL_OUTPUT_CHARS:
        logger.warning(
            f"[{strand_id}] Output truncated to {MAX_TOOL_OUTPUT_CHARS} chars "
            f"(was {len(full_content)})"
        )
        full_content = full_content[:MAX_TOOL_OUTPUT_CHARS]

    return full_content

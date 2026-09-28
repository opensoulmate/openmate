# -*- coding: utf-8 -*-
"""LLM传输错误契约单一真源 — mem0 §1.1「失败必须可见，禁止静默降级」（2026-09-29收口）

背景（多轮dev-report遗留项"先统一错误契约再合并传输"的第一步）：接线前LLM传输
层有**三种**失败形态并存，上游无法用统一方式区分"LLM挂了"与"LLM说没有可改进项"：
1. `dna_evolution.DNAStrand._call_llm`：raise 自有 LLMCallError（partial_content=半截输出）
2. `agent.llm_engine`（会话真实路径）：返回可见 "[LLM错误: …]" 字符串/流式标记
3. `utils.llm_gateway.llm_call`：**静默返回空串/半截**（`return full_content`）——
   正是 mem0 §1.1 点名的反模式："原来静默导致上游无法区分'不可用'和'无事实'"
   （2026-09-27 00:10轮已修dna侧，gateway侧一直挂着）

本模块定义唯一契约（两面收口，第三态"空串=失败"废除）：
- **程序化调用面**（dna_evolution._call_llm、utils.llm_gateway.llm_call）：
  成功=返回str；失败=raise LLMCallError，携带 partial_content（续写中断的半截
  输出不丢，调用方可取）。HTTP 200但空输出同判失败（"空内容"），不许伪装成功。
- **会话面**（agent.llm_engine chat/chat_stream*）：失败以可见 "[LLM错误: …]"
  字符串/流式显式标记呈现（面向用户的失败必须可展示），既有契约不动。

partial_content语义：失败时刻已生成的部分内容挂在异常上——"失败"与"半截成果"
两件事都可见，调用方自行决定丢弃还是利用（dna侧测试钉住该语义）。
"""
from __future__ import annotations


class LLMCallError(RuntimeError):
    """LLM调用失败（HTTP非200 / 空响应 / 传输异常）。

    mem0 §1.1「失败必须可见，禁止静默降级」：禁止静默返回空字符串让上游把
    "LLM挂了"记成"LLM说没有可改进项"。调用方必须把本异常记为**失败**，
    与"LLM成功但无改进"走不同的记账分支。
    """

    partial_content: str = ""  # 续写中断时的半截输出（可选，调用方可取）

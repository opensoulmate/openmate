"""上下文压缩 + Work-State 模板

借鉴自：
- Claude Code 的 compact（摘要替换历史）
- Deep Agents 的文件工作记忆
- Mem0 的记忆分层
- LangGraph 的 context compression

核心思想：
1. 上下文不是越多越好——长对话需要压缩
2. Work-State模板：把关键状态提取出来，不依赖完整历史
3. 文件工作记忆：大文件不进上下文，只进路径引用
"""

import time
import logging
import hashlib
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("acp-agent.context-compression")

# Pi #5 阶段交接压缩——业务摘要提示词模板（默认压缩指令）：
# 正文/台账以工作区文件为准，不在摘要中摘录（文件路径引用即可），摘要只留业务状态。
STAGE_HANDOFF_COMPRESSION_PROMPT = (
    "生成业务摘要时遵守：正文/台账/表格以工作区文件为准，不在摘要中摘录其内容，"
    "只保留文件路径引用；摘要仅保留当前目标、当前步骤、关键决策、错误与修复、待办。"
)


@dataclass
class WorkState:
    """工作状态模板 — 从对话历史中提取的关键信息
    
    这个结构借鉴了Deep Agents的文件工作记忆模式：
    不是保留完整对话历史，而是提取出关键状态。
    """
    # 当前任务
    current_goal: str = ""
    current_step: str = ""
    
    # 文件状态
    files_read: list[str] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)
    files_created: list[str] = field(default_factory=list)
    
    # 关键决策
    decisions: list[str] = field(default_factory=list)
    
    # 错误和修复
    errors_encountered: list[str] = field(default_factory=list)
    fixes_applied: list[str] = field(default_factory=list)
    
    # 用户偏好（从对话中学习）
    user_preferences: list[str] = field(default_factory=list)
    
    # 待办事项
    pending_tasks: list[str] = field(default_factory=list)
    
    # 上次更新时间
    last_updated: float = field(default_factory=time.time)
    
    def to_prompt(self, instructions: str | None = None) -> str:
        """转换为注入到system prompt的工作状态摘要

        instructions：压缩指令（阶段交接压缩业务摘要提示词模板）——随摘要头部注入，
        指引摘要遵循（如"正文/台账以工作区文件为准，不在摘要中摘录"）。
        """
        sections = []
        if instructions:
            sections.append(f"## 压缩指令\n{instructions}")

        if self.current_goal:
            sections.append(f"## 当前目标\n{self.current_goal}")
        
        if self.current_step:
            sections.append(f"## 当前步骤\n{self.current_step}")
        
        if self.files_read or self.files_modified or self.files_created:
            file_lines = []
            for f in self.files_read[-5:]:  # 只保留最近5个
                file_lines.append(f"  📖 {f}")
            for f in self.files_modified[-5:]:
                file_lines.append(f"  ✏️ {f}")
            for f in self.files_created[-5:]:
                file_lines.append(f"  📝 {f}")
            sections.append("## 文件操作\n" + "\n".join(file_lines))
        
        if self.decisions:
            sections.append(
                "## 关键决策\n" + "\n".join(f"  • {d}" for d in self.decisions[-5:])
            )
        
        if self.errors_encountered:
            sections.append(
                "## 遇到的错误\n" + "\n".join(f"  ❌ {e}" for e in self.errors_encountered[-3:])
            )
        
        if self.fixes_applied:
            sections.append(
                "## 已应用的修复\n" + "\n".join(f"  ✅ {f}" for f in self.fixes_applied[-3:])
            )
        
        if self.user_preferences:
            sections.append(
                "## 用户偏好\n" + "\n".join(f"  💡 {p}" for p in self.user_preferences[-5:])
            )
        
        if self.pending_tasks:
            sections.append(
                "## 待办事项\n" + "\n".join(f"  ⏳ {t}" for t in self.pending_tasks[-5:])
            )
        
        return "\n\n".join(sections)
    
    def is_stale(self, max_age_seconds: float = 3600) -> bool:
        """状态是否过期（超过1小时未更新）"""
        return (time.time() - self.last_updated) > max_age_seconds
    
    def update(self, **kwargs):
        """更新状态"""
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)
        self.last_updated = time.time()


class ContextCompressor:
    """上下文压缩器
    
    Usage:
        compressor = ContextCompressor(max_tokens=8000)
        
        # 压缩消息历史
        compressed = compressor.compress(messages)
        
        # 获取工作状态
        work_state = compressor.get_work_state(session_id)
        state_prompt = work_state.to_prompt()
    """
    
    def __init__(
        self,
        max_tokens: int = 8000,
        compression_threshold: float = 0.8,  # 超过80%时触发压缩
        keep_recent: int = 10,  # 压缩时保留最近N条
    ):
        self.max_tokens = max_tokens
        self.compaction_pending = False   # Pi #5：压缩失败置位，断点补偿重压
        self.compression_threshold = compression_threshold
        self.keep_recent = keep_recent
        self._work_states: dict[str, WorkState] = {}
        self._compression_count = 0
    
    def _estimate_tokens(self, text: str) -> int:
        """粗略估算token数（中文约1.5字/token，英文约4字符/token）"""
        chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
        other_chars = len(text) - chinese_chars
        return int(chinese_chars * 1.5 + other_chars / 4)
    
    def _estimate_messages_tokens(self, messages: list[dict]) -> int:
        """估算消息列表的token数"""
        total = 0
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                total += self._estimate_tokens(content)
            total += 10  # 每条消息的overhead
        return total
    
    def get_work_state(self, session_id: str) -> WorkState:
        """获取或创建工作状态"""
        if session_id not in self._work_states:
            self._work_states[session_id] = WorkState()
        return self._work_states[session_id]
    
    def update_work_state(
        self,
        session_id: str,
        user_text: str | None = None,
        assistant_text: str | None = None,
        tool_results: list[dict] | None = None,
    ):
        """从对话中更新工作状态"""
        state = self.get_work_state(session_id)
        
        if user_text:
            # 提取文件操作
            import re
            file_reads = re.findall(r'read_file.*?["\']([^"\']+)["\']', user_text)
            for f in file_reads:
                if f not in state.files_read:
                    state.files_read.append(f)
            
            # 提取待办
            if any(kw in user_text for kw in ["TODO", "待办", "需要", "应该", "必须"]):
                state.pending_tasks.append(user_text[:100])
        
        if assistant_text:
            # 提取决策
            if any(kw in assistant_text for kw in ["决定", "选择", "方案", "因为"]):
                state.decisions.append(assistant_text[:200])
            
            # 提取错误
            if any(kw in assistant_text for kw in ["错误", "失败", "error", "failed"]):
                state.errors_encountered.append(assistant_text[:200])
            
            # 提取修复
            if any(kw in assistant_text for kw in ["修复", "fix", "解决", "成功"]):
                state.fixes_applied.append(assistant_text[:200])
        
        if tool_results:
            for result in tool_results:
                tool_name = result.get("tool_name", "")
                if "read_file" in tool_name:
                    path = result.get("args", {}).get("path", "")
                    if path and path not in state.files_read:
                        state.files_read.append(path)
                elif "write_file" in tool_name or "edit_file" in tool_name:
                    path = result.get("args", {}).get("path", "")
                    if path and path not in state.files_modified:
                        state.files_modified.append(path)
        
        state.last_updated = time.time()
    
    def should_compress(self, messages: list[dict]) -> bool:
        """判断是否需要压缩"""
        total_tokens = self._estimate_messages_tokens(messages)
        return total_tokens > self.max_tokens * self.compression_threshold
    
    def compress(
        self,
        messages: list[dict],
        session_id: str | None = None,
        instructions: str | None = None,
        force: bool = False,
    ) -> list[dict] | None:
        """压缩消息历史（阶段交接压缩，Pi #5）

        - instructions：自定义压缩指令（业务摘要提示词模板），进摘要头部；
          不传则用 STAGE_HANDOFF_COMPRESSION_PROMPT 默认模板。
        - force=True：跳过阈值判定强制压缩（compaction_pending 断点补偿用）。
        - 失败语义：置 compaction_pending=True 并返回 None——调用方留痕，
          下一轮以 force=True 断点补偿重压；成功后自动清标志。
        """
        if not force and not self.should_compress(messages):
            return messages
        try:
            result = self._compress_impl(
                messages, session_id=session_id,
                instructions=instructions or STAGE_HANDOFF_COMPRESSION_PROMPT)
        except Exception as e:  # noqa: BLE001 — 压缩失败绝不打断主流程，置待补偿
            self.compaction_pending = True
            logger.warning(
                "[context-compression] 压缩失败（compaction_pending 置位，待断点补偿）: %s", e)
            return None
        self.compaction_pending = False
        return result

    def _compress_impl(
        self,
        messages: list[dict],
        session_id: str | None = None,
        instructions: str | None = None,
    ) -> list[dict]:
        """压缩消息历史
        
        策略：
        1. 保留最近N条消息
        2. 更早的消息用工作状态摘要替代
        3. 系保system消息不压缩
        """
        if not self.should_compress(messages):
            return messages
        
        self._compression_count += 1
        
        # 分离system消息和普通消息
        system_msgs = [m for m in messages if m.get("role") == "system"]
        normal_msgs = [m for m in messages if m.get("role") != "system"]
        
        if len(normal_msgs) <= self.keep_recent:
            return messages
        
        # 保留最近的消息
        recent = normal_msgs[-self.keep_recent:]
        older = normal_msgs[:-self.keep_recent]
        
        # 从旧消息中更新工作状态
        if session_id:
            for msg in older:
                if msg.get("role") == "user":
                    self.update_work_state(session_id, user_text=msg.get("content", ""))
                elif msg.get("role") == "assistant":
                    self.update_work_state(session_id, assistant_text=msg.get("content", ""))
        
        # 生成压缩摘要（阶段交接压缩：instructions 随摘要头部注入）
        if session_id:
            work_state = self.get_work_state(session_id)
            summary = work_state.to_prompt(instructions=instructions)
        else:
            base = self._generate_summary(older)
            summary = f"[压缩指令] {instructions}\n\n{base}" if instructions else base
        
        # 构建压缩后的消息列表
        compressed = system_msgs.copy()
        
        # 添加摘要作为system消息
        if summary:
            compressed.append({
                "role": "system",
                "content": f"[对话历史摘要]\n{summary}",
            })
        
        # 添加最近的消息
        compressed.extend(recent)
        
        logger.info(
            f"[context-compression] Compressed {len(older)} messages into summary "
            f"(kept {len(recent)} recent, total {len(compressed)})"
        )
        
        return compressed
    
    def _generate_summary(self, messages: list[dict]) -> str:
        """生成简单的摘要（无LLM版本）"""
        if not messages:
            return ""
        
        user_msgs = [m.get("content", "")[:100] for m in messages if m.get("role") == "user"]
        assistant_msgs = [m.get("content", "")[:100] for m in messages if m.get("role") == "assistant"]
        
        summary_parts = []
        if user_msgs:
            summary_parts.append(f"用户消息({len(user_msgs)}条):")
            for msg in user_msgs[-3:]:
                summary_parts.append(f"  - {msg}")
        
        if assistant_msgs:
            summary_parts.append(f"AI回复({len(assistant_msgs)}条):")
            for msg in assistant_msgs[-3:]:
                summary_parts.append(f"  - {msg}")
        
        return "\n".join(summary_parts)
    
    def get_stats(self) -> dict:
        """统计信息"""
        return {
            "active_work_states": len(self._work_states),
            "compression_count": self._compression_count,
            "max_tokens": self.max_tokens,
            "compression_threshold": self.compression_threshold,
            "keep_recent": self.keep_recent,
        }

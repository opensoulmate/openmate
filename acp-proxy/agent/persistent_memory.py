# -*- coding: utf-8 -*-
"""MEMORY.md 型人类可读持久记忆（路线图 1.1）。

与 SQLite 黑盒记忆引擎（memory_retrieval / memory_consolidator / pref_learner）互补：
本模块是**文件型**持久记忆——单一 Markdown 文件，人类可以直接打开阅读和手工编辑，
agent 通过 memory_read / memory_write / memory_append 三个工具维护，并在每次
prompt 时把内容注入系统提示（跨会话加载）。

设计要点：
- 路径默认 ~/.hermes/soulmate/memory/MEMORY.md（与 memory.db 同目录），可被
  SOULMATE_MEMORY_PATH 环境变量覆盖（测试隔离用）。
- 写入用 utils.file_safety.atomic_write（项目铁律：所有文件写入走原子写）。
- append 追加带时间戳的条目（`- [YYYY-MM-DD HH:MM] 内容`），保留既有内容。
- load_prompt_section 供系统提示注入：超长截断并显式标注，绝不静默吞内容。
"""
import os
import time
from pathlib import Path

from agent.tool_exec_fencing import FileFenceTimeout, file_fence
from utils.file_safety import atomic_write

DEFAULT_MEMORY_PATH = str(
    Path.home() / ".hermes" / "soulmate" / "memory" / "MEMORY.md"
)


class PersistentMemory:
    """单一 Markdown 文件的持久记忆。"""

    def __init__(self, path: str = ""):
        if not path:
            path = os.environ.get("SOULMATE_MEMORY_PATH") or DEFAULT_MEMORY_PATH
        self.path = path

    # ── 读 ────────────────────────────────────────────────────────
    def read(self, offset: int = 1, limit: int = 200) -> str:
        """按行读取（带行号，与 read_file 同款格式）。文件不存在返回提示。"""
        p = Path(self.path)
        if not p.exists():
            return f"(持久记忆文件不存在: {self.path}，可用 memory_append 创建)"
        try:
            lines = p.read_text(encoding="utf-8").split("\n")
        except Exception as e:
            return f"错误: 读取持久记忆失败: {e}"
        offset = max(1, int(offset or 1))
        limit = max(1, int(limit or 200))
        seg = lines[offset - 1: offset - 1 + limit]
        total = len(lines)
        out = "\n".join(f"{offset + i}|{line}" for i, line in enumerate(seg))
        if offset - 1 + limit < total:
            out += f"\n[还有 {total - (offset - 1 + limit)} 行，继续用 offset={offset + limit} 读取]"
        return out if out else "(空文件)"

    # ── 写（整体改写）──────────────────────────────────────────────
    def write(self, content: str) -> tuple:
        """整体覆盖写入。返回 (ok, message)。content为空拒绝（防误清空）。"""
        if not content or not content.strip():
            return False, "错误: content 不能为空（防误清空；要清空请写入占位说明）"
        try:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            # 5.2 并发隔离：覆盖写与追加互斥（多会话/子代理并发下防 lost update）
            with file_fence(self.path):
                ok, err = atomic_write(self.path, content)
            if not ok:
                return False, f"错误: 写入失败: {err}"
            return True, f"已写入持久记忆 {self.path} ({len(content)} 字符)"
        except FileFenceTimeout as e:
            return False, f"错误: {e}"
        except Exception as e:
            return False, f"错误: 写入持久记忆失败: {e}"

    # ── 追加 ──────────────────────────────────────────────────────
    def append(self, entry: str) -> tuple:
        """追加一条带时间戳的记忆条目（`- [时间] 内容`）。返回 (ok, message)。"""
        if not entry or not entry.strip():
            return False, "错误: entry 不能为空"
        entry = entry.strip()
        stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime())
        line = f"- [{stamp}] {entry}"
        try:
            p = Path(self.path)
            # 5.2 并发隔离：读-改-写全程持栅栏（多会话/子代理并发追加不丢行）
            with file_fence(self.path):
                existing = p.read_text(encoding="utf-8") if p.exists() else ""
                if existing and not existing.endswith("\n"):
                    existing += "\n"
                new_content = existing + line + "\n"
                p.parent.mkdir(parents=True, exist_ok=True)
                ok, err = atomic_write(self.path, new_content)
            if not ok:
                return False, f"错误: 追加失败: {err}"
            return True, f"已追加记忆条目: {line}"
        except FileFenceTimeout as e:
            return False, f"错误: {e}"
        except Exception as e:
            return False, f"错误: 追加持久记忆失败: {e}"

    # ── 系统提示注入 ──────────────────────────────────────────────
    def load_prompt_section(self, max_chars: int = 4000) -> str:
        """返回供系统提示注入的内容；文件不存在/为空返回 ""。

        超长时截断并显式标注截断（不静默），提示可用 memory_read 读全量。
        """
        p = Path(self.path)
        if not p.exists():
            return ""
        try:
            content = p.read_text(encoding="utf-8").strip()
        except Exception:
            return ""
        if not content:
            return ""
        if len(content) > max_chars:
            content = (
                content[:max_chars]
                + f"\n[记忆过长已截断，共{len(content)}字符——完整内容用 memory_read 读取]"
            )
        return content

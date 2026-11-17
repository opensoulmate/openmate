"""编辑安全系统 — 防止LLM写坏文件

借鉴自（18个编码Agent项目调研结论）：
- Aider: SEARCH/REPLACE块 + 精确匹配 + dirty commit
- Cline: old_string恰好出现1次 + EOL归一化
- OpenHands: MAX_LINES_TO_EDIT硬限 + 原子写入
- OpenCode: 比例失衡拒绝 + shadow-git
- Gemini CLI: 省略占位符检测 + LLM修一次

核心铁律（调研结论）：
1. 禁止默认整文件Write（新建文件除外）
2. 内容锚定：old_string必须在盘上精确命中
3. 唯一性强制：命中0或>1 → 拒绝
4. 先读后改：未Read过的文件禁止Edit
5. 只替换匹配区，其余字节原样保留
6. 比例/省略守卫
7. 写前快照/写后可undo
8. 原子写入：临时文件+rename
"""

import os
import re
import ast
import json
import time
import shutil
import logging
import tempfile
import hashlib
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("acp-agent.edit-safety")


@dataclass
class EditConfig:
    """编辑安全配置"""
    allow_whole_file_write: bool = False  # 禁止整文件重写
    max_edit_span_ratio: float = 10.0     # new/old 长度比上限
    forbid_omission_markers: bool = True  # 禁止省略标记
    require_read_before_edit: bool = True # 先读后改
    snapshot_before_edit: bool = True     # 写前快照
    atomic_write: bool = True             # 原子写入
    max_edit_lines: int = 300             # 单次编辑最大行数
    edit_retry_max: int = 3               # 最大重试次数
    verify_after_write: bool = True       # 写后语法校验


@dataclass
class EditResult:
    """编辑结果"""
    success: bool
    file_path: str
    action: str  # "edit", "write", "create"
    lines_changed: int = 0
    bytes_written: int = 0
    snapshot_path: str | None = None
    error: str | None = None
    suggestions: list[str] = field(default_factory=list)
    
    def to_model_message(self) -> str:
        if self.success:
            return (
                f"✅ 文件 `{self.file_path}` {self.action}成功 "
                f"({self.lines_changed}行变更, {self.bytes_written}字节)"
            )
        msg = f"❌ 文件 `{self.file_path}` {self.action}失败: {self.error}"
        if self.suggestions:
            msg += "\n建议:\n" + "\n".join(f"  - {s}" for s in self.suggestions)
        return msg


class EditSafetyGuard:
    """编辑安全守卫
    
    Usage:
        guard = EditSafetyGuard(config=EditConfig())
        
        # 先读
        content = guard.read_file("/path/to/file.py")
        
        # 再改（锚点编辑）
        result = guard.edit_file(
            path="/path/to/file.py",
            old_string="def foo():\n    pass",
            new_string="def foo():\n    return 42",
        )
        
        if not result.success:
            # 把错误信息回注给模型
            return result.to_model_message()
    """
    
    def __init__(self, config: EditConfig | None = None, snapshot_dir: str | None = None):
        self.config = config or EditConfig()
        self.snapshot_dir = snapshot_dir or "/tmp/openmate_edit_snapshots"
        Path(self.snapshot_dir).mkdir(parents=True, exist_ok=True)
        self._read_cache: dict[str, str] = {}  # path -> content_hash at read time
    
    def _normalize_eol(self, text: str) -> str:
        """EOL归一化"""
        return text.replace("\r\n", "\n").replace("\r", "\n")
    
    def _normalize_nfc(self, text: str) -> str:
        """NFKC归一化（处理全角/半角等）"""
        import unicodedata
        return unicodedata.normalize("NFKC", text)
    
    def _find_omission_markers(self, text: str) -> list[str]:
        """检测省略标记"""
        patterns = [
            r"//\s*\.\.\.",
            r"#\s*\.\.\.",
            r"/\*\s*\.\.\.\s*\*/",
            r"rest of \w+",
            r"省略.*代码",
            r"其余.*类似",
            r"以此类推",
            r"<\.\.\.>",
            r"\[\.\.\.\]",
        ]
        found = []
        for p in patterns:
            if re.search(p, text, re.IGNORECASE):
                found.append(p)
        return found
    
    def _snapshot(self, path: str) -> str | None:
        """写前快照"""
        if not self.config.snapshot_before_edit:
            return None
        try:
            src = Path(path)
            if not src.exists():
                return None
            content_hash = hashlib.md5(src.read_bytes()).hexdigest()[:8]
            snap_name = f"{src.name}.{content_hash}.{int(time.time())}.bak"
            snap_path = Path(self.snapshot_dir) / snap_name
            shutil.copy2(path, snap_path)
            logger.debug(f"[edit-safety] Snapshot: {snap_path}")
            return str(snap_path)
        except Exception as e:
            logger.warning(f"[edit-safety] Snapshot failed: {e}")
            return None
    
    def _atomic_write(self, path: str, content: str) -> int:
        """原子写入：临时文件 + rename"""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        
        if self.config.atomic_write:
            fd, tmp_path = tempfile.mkstemp(
                dir=str(target.parent),
                prefix=f".{target.name}.",
                suffix=".tmp",
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(content)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, path)
                return len(content.encode("utf-8"))
            except Exception:
                # 清理临时文件
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        else:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            return len(content.encode("utf-8"))
    
    def _verify_python(self, path: str) -> tuple[bool, str]:
        """写后Python语法校验"""
        if not path.endswith(".py"):
            return True, ""
        try:
            with open(path, "r", encoding="utf-8") as f:
                source = f.read()
            ast.parse(source)
            return True, ""
        except SyntaxError as e:
            return False, f"Python语法错误: line {e.lineno}: {e.msg}"
    
    def read_file(self, path: str) -> str | None:
        """读取文件（标记为已读，允许后续edit）"""
        try:
            p = Path(path)
            if not p.exists():
                return None
            content = p.read_text(encoding="utf-8")
            content_hash = hashlib.md5(content.encode()).hexdigest()
            self._read_cache[str(p)] = content_hash
            return content
        except Exception as e:
            logger.error(f"[edit-safety] Read failed: {e}")
            return None
    
    def edit_file(
        self,
        path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        """安全的锚点编辑
        
        流程：
        1. 检查是否读过
        2. EOL/NFKC归一化匹配
        3. 计数old_string出现次数
        4. 比例守卫
        5. 省略标记检测
        6. 快照
        7. 执行替换
        8. 原子写入
        9. 写后校验
        """
        p = Path(path)
        
        # 1. 检查是否读过
        if self.config.require_read_before_edit:
            if str(p) not in self._read_cache:
                return EditResult(
                    success=False,
                    file_path=path,
                    action="edit",
                    error="文件未读取过，必须先Read再Edit",
                    suggestions=["先调用read_file读取文件内容"],
                )
        
        # 读取当前内容
        if not p.exists():
            return EditResult(
                success=False,
                file_path=path,
                action="edit",
                error=f"文件不存在: {path}",
            )
        
        try:
            original = p.read_text(encoding="utf-8")
        except Exception as e:
            return EditResult(
                success=False,
                file_path=path,
                action="edit",
                error=f"读取失败: {e}",
            )
        
        # 2. EOL归一化匹配
        norm_original = self._normalize_eol(original)
        norm_old = self._normalize_eol(old_string)
        
        # 也尝试NFKC归一化
        nfkc_original = self._normalize_nfc(norm_original)
        nfkc_old = self._normalize_nfc(norm_old)
        
        # 3. 计数出现次数
        count = norm_original.count(norm_old)
        if count == 0:
            # 尝试NFKC匹配
            count = nfkc_original.count(nfkc_old)
            if count == 0:
                # 找最接近的片段作为建议
                suggestions = self._find_similar(norm_original, norm_old)
                return EditResult(
                    success=False,
                    file_path=path,
                    action="edit",
                    error=f"old_string在文件中不存在（0次匹配）",
                    suggestions=suggestions,
                )
        
        if count > 1 and not replace_all:
            # 找到各出现位置
            lines = []
            start = 0
            for i in range(count):
                idx = norm_original.find(norm_old, start)
                line_num = norm_original[:idx].count("\n") + 1
                lines.append(f"line {line_num}")
                start = idx + len(norm_old)
            
            return EditResult(
                success=False,
                file_path=path,
                action="edit",
                error=f"old_string出现{count}次，无法确定要替换哪一处",
                suggestions=[
                    f"出现位置: {', '.join(lines[:5])}",
                    "增加更多上下文使old_string唯一",
                    "或设置replace_all=true替换所有",
                ],
            )
        
        # 4. 比例守卫
        if len(norm_old) > 0:
            ratio = len(new_string) / len(norm_old)
            if ratio > self.config.max_edit_span_ratio:
                return EditResult(
                    success=False,
                    file_path=path,
                    action="edit",
                    error=(
                        f"新内容长度是旧内容的{ratio:.1f}倍 "
                        f"(上限{self.config.max_edit_span_ratio}倍)，"
                        f"可能不是局部编辑"
                    ),
                    suggestions=[
                        "如果是新建文件，使用write_file",
                        "如果确实需要大量修改，分多次edit",
                    ],
                )
        
        # 5. 省略标记检测
        if self.config.forbid_omission_markers:
            omissions = self._find_omission_markers(new_string)
            if omissions:
                return EditResult(
                    success=False,
                    file_path=path,
                    action="edit",
                    error=f"新内容包含省略标记: {omissions}",
                    suggestions=["写出完整的代码，不要用省略号代替"],
                )
        
        # 行数检查
        old_lines = norm_old.count("\n") + 1
        if old_lines > self.config.max_edit_lines:
            return EditResult(
                success=False,
                file_path=path,
                action="edit",
                error=f"old_string有{old_lines}行，超过上限{self.config.max_edit_lines}行",
                suggestions=["缩小编辑范围，分多次编辑"],
            )
        
        # 6. 快照
        snapshot_path = self._snapshot(path)
        
        # 7. 执行替换
        if replace_all:
            new_content = norm_original.replace(norm_old, new_string)
            lines_changed = count * (new_string.count("\n") + 1)
        else:
            new_content = norm_original.replace(norm_old, new_string, 1)
            lines_changed = new_string.count("\n") + 1
        
        # 8. 原子写入
        try:
            bytes_written = self._atomic_write(path, new_content)
        except Exception as e:
            # 从快照恢复
            if snapshot_path and Path(snapshot_path).exists():
                shutil.copy2(snapshot_path, path)
            return EditResult(
                success=False,
                file_path=path,
                action="edit",
                error=f"写入失败: {e}",
                snapshot_path=snapshot_path,
            )
        
        # 9. 写后校验
        if self.config.verify_after_write:
            valid, verify_error = self._verify_python(path)
            if not valid:
                # 从快照恢复
                if snapshot_path and Path(snapshot_path).exists():
                    shutil.copy2(snapshot_path, path)
                    logger.warning(f"[edit-safety] Reverted {path} due to syntax error")
                return EditResult(
                    success=False,
                    file_path=path,
                    action="edit",
                    error=verify_error,
                    snapshot_path=snapshot_path,
                    suggestions=["检查新代码的语法，修复后重试"],
                )
        
        # 更新read cache
        new_hash = hashlib.md5(new_content.encode()).hexdigest()
        self._read_cache[path] = new_hash
        
        logger.info(
            f"[edit-safety] Edit OK: {path} "
            f"({lines_changed} lines, {bytes_written} bytes)"
        )
        
        return EditResult(
            success=True,
            file_path=path,
            action="edit",
            lines_changed=lines_changed,
            bytes_written=bytes_written,
            snapshot_path=snapshot_path,
        )
    
    def write_file(
        self,
        path: str,
        content: str,
        is_new_file: bool = True,
    ) -> EditResult:
        """安全的文件写入（仅限新建文件或用户显式要求重写）"""
        p = Path(path)
        exists = p.exists()
        
        # 检查是否允许整文件写入
        if exists and not is_new_file and not self.config.allow_whole_file_write:
            return EditResult(
                success=False,
                file_path=path,
                action="write",
                error="禁止整文件重写（已存在的文件）",
                suggestions=[
                    "使用edit_file进行局部修改",
                    "如果确实需要重写，请设置allow_whole_file_write=True",
                ],
            )
        
        if exists and is_new_file:
            # 文件已存在但标记为新建，也拒绝
            return EditResult(
                success=False,
                file_path=path,
                action="write",
                error=f"文件已存在: {path}",
                suggestions=["使用edit_file修改已有文件"],
            )
        
        # 省略标记检测
        if self.config.forbid_omission_markers:
            omissions = self._find_omission_markers(content)
            if omissions:
                return EditResult(
                    success=False,
                    file_path=path,
                    action="write",
                    error=f"内容包含省略标记: {omissions}",
                )
        
        # 快照（如果文件存在）
        snapshot_path = self._snapshot(path) if exists else None
        
        # 写入
        try:
            bytes_written = self._atomic_write(path, content)
        except Exception as e:
            if snapshot_path and Path(snapshot_path).exists():
                shutil.copy2(snapshot_path, path)
            return EditResult(
                success=False,
                file_path=path,
                action="write",
                error=f"写入失败: {e}",
                snapshot_path=snapshot_path,
            )
        
        # 写后校验
        if self.config.verify_after_write:
            valid, verify_error = self._verify_python(path)
            if not valid:
                if snapshot_path and Path(snapshot_path).exists():
                    shutil.copy2(snapshot_path, path)
                elif not exists:
                    # 新文件，直接删除
                    try:
                        p.unlink()
                    except OSError:
                        pass
                return EditResult(
                    success=False,
                    file_path=path,
                    action="write",
                    error=verify_error,
                    snapshot_path=snapshot_path,
                )
        
        # 更新read cache
        new_hash = hashlib.md5(content.encode()).hexdigest()
        self._read_cache[path] = new_hash
        
        lines = content.count("\n") + 1
        logger.info(f"[edit-safety] Write OK: {path} ({lines} lines, {bytes_written} bytes)")
        
        return EditResult(
            success=True,
            file_path=path,
            action="write",
            lines_changed=lines,
            bytes_written=bytes_written,
            snapshot_path=snapshot_path,
        )
    
    def _find_similar(self, content: str, target: str, max_suggestions: int = 3) -> list[str]:
        """找最接近的片段（Did you mean）"""
        suggestions = []
        target_lines = target.strip().split("\n")
        if not target_lines:
            return suggestions
        
        first_line = target_lines[0].strip()
        if len(first_line) < 5:
            return suggestions
        
        content_lines = content.split("\n")
        for i, line in enumerate(content_lines):
            if first_line[:20] in line:
                context_start = max(0, i - 1)
                context_end = min(len(content_lines), i + len(target_lines) + 1)
                snippet = "\n".join(content_lines[context_start:context_end])
                suggestions.append(f"line {i+1}: {snippet[:100]}...")
                if len(suggestions) >= max_suggestions:
                    break
        
        return suggestions
    
    def get_stats(self) -> dict:
        """编辑统计"""
        snapshots = list(Path(self.snapshot_dir).glob("*.bak"))
        return {
            "read_files": len(self._read_cache),
            "snapshots": len(snapshots),
            "config": {
                "allow_whole_file_write": self.config.allow_whole_file_write,
                "max_edit_span_ratio": self.config.max_edit_span_ratio,
                "forbid_omission_markers": self.config.forbid_omission_markers,
                "require_read_before_edit": self.config.require_read_before_edit,
                "atomic_write": self.config.atomic_write,
                "max_edit_lines": self.config.max_edit_lines,
                "verify_after_write": self.config.verify_after_write,
            },
        }


# ═══════════════════════════════════════════════════════════════════
# 受保护文件基线（Pi 取长补短 #8：manifest+sha256 副本、settle 前还原）
# ═══════════════════════════════════════════════════════════════════
#
# 来源：Pi/OpenBidKit piWorkspaceBaseline——程序写入工作区的输入文件登记
# manifest.json + files/<sha256> 内容副本（基线放**工作区外**），提交校验前/
# settle 前 restore() 还原被 Agent 改动的输入——**Agent 只许动产物**。项目有
# 文件截断/误改历史痛点（agent-file-editing-safety 教训），基线比对是兜底。
#
# 落地（既有 edit_safety 编辑安全面扩展——写前快照/原子写/恢复基建同一模块）：
# - register：登记+内容快照（sha256 内容寻址副本，重复登记幂等）；
# - check：基线比对（modified/deleted/matched，确定性 sha256）；
# - restore：还原被改动/删除的受保护文件（幂等，还原清单可见禁静默）；
# - baseline_restore_hook：settle 挂点适配（#2 agent_before_settle/settled 钩子
#   形态）——settle 前还原+终态兜底。

BASELINE_ROOT = Path.home() / ".hermes" / "soulmate" / "baselines"
MANIFEST_NAME = "manifest.json"


class FileBaseline:
    """受保护文件基线（manifest + sha256 内容副本，基线目录在工作区外）。"""

    def __init__(self, baseline_dir: str | Path | None = None, ns: str = "default"):
        root = Path(baseline_dir) if baseline_dir else (BASELINE_ROOT / _safe_ns(ns))
        self.baseline_dir = root
        self.files_dir = root / "files"
        self.manifest_path = root / MANIFEST_NAME
        self._manifest = self._load_manifest()

    # ── manifest（单一真源，落盘持久） ─────────────────────────────

    def _load_manifest(self) -> dict:
        if self.manifest_path.exists():
            try:
                data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and isinstance(data.get("files"), dict):
                    return data
            except Exception as e:
                logger.warning(f"[baseline] manifest 损坏，重建（副本保留）: {e}")
        return {"created": time.time(), "files": {}}

    def _save_manifest(self) -> None:
        self.baseline_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.baseline_dir / (MANIFEST_NAME + ".tmp")
        tmp.write_text(json.dumps(self._manifest, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(tmp, self.manifest_path)   # 原子写（同 utils.file_safety 纪律）

    @staticmethod
    def _sha256(path: Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()

    # ── 登记（分组） ───────────────────────────────────────────────

    def register(self, paths, group: str = "input") -> dict:
        """登记受保护文件 + 内容快照（sha256 内容寻址副本）。

        重复登记同路径同内容=幂等跳过；内容变了=覆盖登记（新副本+manifest 更新）。
        文件不存在=显式报因进 errors（禁静默跳过）。
        """
        if isinstance(paths, (str, Path)):
            paths = [paths]
        registered, unchanged, errors = [], [], []
        for raw in paths:
            p = Path(raw).expanduser()
            try:
                p_res = p.resolve()
            except Exception:
                p_res = p
            if not p_res.is_file():
                errors.append({"path": str(raw), "error": "基线登记失败：文件不存在"})
                continue
            sha = self._sha256(p_res)
            key = str(p_res)
            old = self._manifest["files"].get(key)
            if old and old.get("sha256") == sha:
                unchanged.append(key)
                continue
            self.files_dir.mkdir(parents=True, exist_ok=True)
            stored = self.files_dir / sha
            if not stored.exists():
                shutil.copy2(p_res, stored)   # 内容寻址：同内容共享一份副本
            self._manifest["files"][key] = {
                "sha256": sha,
                "size": p_res.stat().st_size,
                "group": str(group or "input"),
                "stored": f"files/{sha}",
                "registered_at": time.time(),
            }
            registered.append(key)
        if registered:
            self._save_manifest()
        return {"registered": registered, "unchanged": unchanged,
                "errors": errors, "total": len(self._manifest["files"])}

    def unregister(self, path: str) -> dict:
        """解除登记（副本保留到 GC，manifest 移除）。"""
        key = str(Path(path).expanduser().resolve())
        existed = self._manifest["files"].pop(key, None)
        if existed:
            self._save_manifest()
        return {"unregistered": bool(existed), "path": key}

    # ── 基线比对 ───────────────────────────────────────────────────

    def check(self, group: str = "") -> dict:
        """比对当前文件与基线 → {"ok", "matched", "modified", "deleted", "total"}。

        modified=内容 sha256 与基线不一致（Agent 动了输入）；deleted=文件消失。
        """
        modified, deleted, matched = [], [], 0
        for key, meta in self._manifest["files"].items():
            if group and meta.get("group") != group:
                continue
            p = Path(key)
            if not p.is_file():
                deleted.append(key)
                continue
            if self._sha256(p) != meta.get("sha256"):
                modified.append(key)
                continue
            matched += 1
        return {
            "ok": not modified and not deleted,
            "matched": matched,
            "modified": modified,
            "deleted": deleted,
            "total": sum(1 for _m in self._manifest["files"].values()
                         if not group or _m.get("group") == group),
        }

    # ── settle 前还原（Agent 只许动产物） ──────────────────────────

    def restore(self, group: str = "") -> dict:
        """还原被改动/删除的受保护文件（幂等；还原清单可见禁静默）。

        返回 {"restored_modified", "restored_deleted", "errors", "unchanged"}。
        """
        restored_modified, restored_deleted, errors = [], [], []
        for key, meta in self._manifest["files"].items():
            if group and meta.get("group") != group:
                continue
            stored = self.baseline_dir / meta.get("stored", "")
            if not stored.is_file():
                errors.append({"path": key, "error": "基线副本缺失（无法还原）"})
                continue
            p = Path(key)
            try:
                p.parent.mkdir(parents=True, exist_ok=True)
                if not p.is_file():
                    shutil.copy2(stored, p)
                    restored_deleted.append(key)
                elif self._sha256(p) != meta.get("sha256"):
                    shutil.copy2(stored, p)   # 覆盖还原被改动的输入
                    restored_modified.append(key)
            except Exception as e:
                errors.append({"path": key, "error": f"还原失败: {e}"})
        chk = self.check(group=group)
        return {
            "restored_modified": restored_modified,
            "restored_deleted": restored_deleted,
            "errors": errors,
            "unchanged": chk["matched"],
            "ok": chk["ok"] and not errors,
        }

    def status(self, group: str = "") -> dict:
        """状态快照（登记面+比对面合一）。"""
        groups: dict[str, int] = {}
        for meta in self._manifest["files"].values():
            g = str(meta.get("group") or "input")
            groups[g] = groups.get(g, 0) + 1
        return {
            "baseline_dir": str(self.baseline_dir),
            "total": len(self._manifest["files"]),
            "groups": groups,
            "check": self.check(group=group),
        }


def baseline_restore_hook(provider) -> callable:
    """settle 挂点适配（#2×#8）：settle 前还原被 Agent 改动的受保护输入。

    provider：FileBaseline 实例，或 callable()->FileBaseline|None（延迟解析——
    基线可能在工具执行中途才登记）。钩子形态=SettleGate before/settled 双相通用：
    restore 幂等，真还原了文件才带 reason 留痕（静默=no-op 不记账）。
    """

    def baseline_restore(ctx: dict):
        if callable(provider):
            baseline: Any = provider(ctx)
        else:
            baseline = provider
        if baseline is None:
            return None
        result = baseline.restore()
        touched = (result.get("restored_modified") or []) + (result.get("restored_deleted") or [])
        if not touched and not result.get("errors"):
            return None
        names = ", ".join(Path(p).name for p in touched) or "（无）"
        return {"continue": False,
                "reason": f"文件基线还原 {len(touched)} 个受保护输入: {names}"
                          + (f"；错误: {result['errors']}" if result.get("errors") else "")}

    baseline_restore.__name__ = "baseline_restore"
    return baseline_restore


def _safe_ns(ns: str) -> str:
    """命名空间消毒（防路径穿越）——只留字母数字 ._-。"""
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(ns or "default"))[:64] or "default"

"""公共胶水基础设施模块。

本模块为纯抽取自 image_generator / voice_generator / doc_generator 的公共胶水，
不含业务逻辑：仅提供 MEDIA 卡片构造、产物原子落盘、输出路径校验三类通用能力。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

# 默认允许的输出根目录：用户主目录与系统临时目录。
_DEFAULT_ALLOWED_ROOTS: tuple[str, ...] = (
    str(Path.home()),
    "/tmp",
)


def build_media_card(path: str) -> str:
    """构造 MEDIA 卡片标签。

    前端渲染约定：当工具结果以 ``MEDIA:`` 前缀输出时，客户端应剥离前缀，
    将剩余部分作为本地媒体路径进行渲染：
        - 后缀为图片扩展名 -> 渲染为 <img> 卡片；
        - 后缀为音频扩展名 -> 渲染为 <audio> 播放器卡片；
        - 后缀为文档/其他   -> 渲染为可下载的文件卡片。

    Args:
        path: 产物的本地绝对路径或客户端可访问的路径字符串。

    Returns:
        形如 ``MEDIA:/abs/path/to/file.png`` 的标签字符串。
    """
    return f"MEDIA:{path}"


def save_artifact(data: bytes, dest: Path) -> Path:
    """将产物字节原子落盘到目标路径。

    先用 ``tempfile.NamedTemporaryFile(dir=dest.parent, delete=False)`` 在目标
    同级目录写入临时文件，再用 ``os.replace`` 原子替换到最终位置，避免出现
    半写状态的产物文件。

    Args:
        data: 待写入的字节内容。
        dest: 最终产物路径（父目录需已存在）。

    Returns:
        落盘后的最终目标路径（与 ``dest`` 等价的 ``Path``）。

    Raises:
        IOError: 父目录不存在或任意 I/O 步骤失败；此时会清理临时文件。
    """
    dest = Path(dest)
    parent = dest.parent

    if not parent.exists():
        raise IOError(f"目标目录不存在，无法落盘产物: {parent}")

    tmp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=parent,
            delete=False,
            prefix=f".{dest.name}.",
            suffix=".tmp",
        ) as tmp:
            tmp_name = tmp.name
            tmp.write(data)
            tmp.flush()
            os.fsync(tmp.fileno())

        os.replace(tmp_name, dest)
        tmp_name = None
    except OSError as exc:
        raise IOError(f"产物落盘失败: {dest} -> {exc}") from exc
    finally:
        if tmp_name is not None and os.path.exists(tmp_name):
            try:
                os.unlink(tmp_name)
            except OSError:
                pass

    return dest


def validate_output_path(path: str, allowed_roots: list[str] | None = None) -> Path:
    """校验输出路径位于允许的根目录之内，并返回解析后的绝对路径。

    Args:
        path: 待校验的输出路径字符串，可包含 ``~``。
        allowed_roots: 允许的根目录列表；为 ``None`` 时默认为 ``$HOME`` 与 ``/tmp``。

    Returns:
        ``path`` 经 ``expanduser().resolve()`` 之后的绝对路径。

    Raises:
        ValueError: 路径为空，或解析后的路径不在任何允许根目录内；异常消息中
            会列出全部允许根目录。
    """
    if path is None or not str(path).strip():
        raise ValueError("输出路径不能为空")

    roots_raw: list[str] = (
        list(allowed_roots) if allowed_roots is not None else list(_DEFAULT_ALLOWED_ROOTS)
    )
    if not roots_raw:
        raise ValueError("允许根目录列表为空，无法校验输出路径")

    roots = [Path(root).expanduser().resolve() for root in roots_raw]
    resolved = Path(path).expanduser().resolve()

    for root in roots:
        if resolved == root or root in resolved.parents:
            return resolved

    roots_display = ", ".join(str(root) for root in roots)
    raise ValueError(
        f"输出路径越界: {resolved} 不在允许根目录内。允许根目录: [{roots_display}]"
    )
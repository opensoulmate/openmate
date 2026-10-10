"""send_file 文件下发工具（路线图 9.1）。

对标 Hermes send_file 语义（path + message → 📎 文件卡片），与既有 MEDIA 标签
下发通道（fileviewer-media-tag-pattern）**对齐不双轨**：本工具只产出
``MEDIA:<abs path>`` 标签行，提取/校验/渲染走网关既有后处理面（前端三键卡片）。

诚实多态（显式报因禁静默）：
- 空 path / 文件不存在 / 目录 / 非普通文件 → 明确错误文本
- 越界（realpath 不在允许的下发根目录内）→ 明确拒绝并列出允许根目录
- 成功 → 附言 + 📎 信息行（文件名+字节数，0 字节如实报 0）+ MEDIA 标签**末行**
  （周期#39 输出契约：MEDIA: 保持末行供网关按行提取）。

允许的下发根目录默认 = 用户主目录 + /tmp，可用 SOULMATE_SEND_FILE_ROOTS
（os.pathsep 分隔）覆盖。realpath 收口 .. 与软链穿越。
"""

from __future__ import annotations

import os
from pathlib import Path


class SendFileTools:
    """本地文件 → MEDIA 标签下发（确定性校验，无 LLM 参与）。"""

    def __init__(self, allowed_roots: list | tuple | None = None):
        if allowed_roots is not None:
            roots = [str(r) for r in allowed_roots]
        else:
            env = os.environ.get("SOULMATE_SEND_FILE_ROOTS", "")
            roots = [r.strip() for r in env.split(os.pathsep) if r.strip()]
            if not roots:
                roots = [str(Path.home()), "/tmp"]
        self._roots = [os.path.realpath(os.path.expanduser(r)) for r in roots]

    @property
    def roots(self) -> list:
        return list(self._roots)

    def _in_roots(self, real: str) -> bool:
        return any(real == root or real.startswith(root + os.sep)
                   for root in self._roots)

    def run(self, params: dict) -> str:
        """执行下发：返回工具结果文本（成功时 MEDIA: 标签为末行）。"""
        path = str(params.get("path", "") or "").strip()
        message = str(params.get("message", "") or "").strip()
        if not path:
            return "错误: path 参数不能为空"

        expanded = os.path.expanduser(path)
        if not os.path.isabs(expanded):
            expanded = os.path.abspath(expanded)
        real = os.path.realpath(expanded)

        # 越界显式报因（realpath 已收口 .. 与软链穿越）
        if not self._in_roots(real):
            return (f"错误: 路径越界拒绝（不在允许的下发根目录内）: {real}\n"
                    f"允许的根目录: {', '.join(self._roots)}")
        if not os.path.exists(real):
            return f"错误: 文件不存在: {real}"
        if os.path.isdir(real):
            return f"错误: 不支持发送目录，请指定具体文件: {real}"
        if not os.path.isfile(real):
            return f"错误: 不是普通文件: {real}"

        size = os.path.getsize(real)
        name = os.path.basename(real)
        lines = []
        if message:
            lines.append(message)
        lines.append(f"📎 文件已准备好: {name} ({size} 字节)")
        lines.append(f"MEDIA:{real}")
        return "\n".join(lines)

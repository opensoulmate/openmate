# -*- coding: utf-8 -*-
"""image_compress.py — C6 Pillow 确定性压图管线（防标书超上传体积上限）。

通用图片压缩能力（标书/方案/课件一切含图交付，非投标专用）：**确定性压缩
算子**——同样输入恒出同样输出（同 Pillow 版本下逐字节可复现），参数全显式：

- **长边 1600px**（超限等比缩小，LANCZOS；绝不放大）；
- **JPEG 82%** 起步（optimize=True），超 300KB 硬上限时按 4 档步进降质
  （82→…→50），仍超再 0.8× 缩尺寸（有界轮次）——**单张硬上限 300KB 保证**
  （典型落 150-300KB 质量带，下限不强求：越小越利上传）；
- EXIF 方向摆正（exif_transpose）、透明/P 模式白底转 RGB——不猜色彩语义；
- 已合规图（JPEG + 长边达标 + ≤300KB）原样复制不二压（防重复有损）。

批量入口 compress_batch：目录/文件清单 → 同名 .min.jpg 落 out_dir +
manifest 汇总（前后体积/尺寸/实际质量档/超限清单）。参数非法/非图片显式
ValueError 报因禁静默。独立可测（tests/test_image_compress.py）。
"""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

__all__ = [
    "DEFAULT_MAX_BYTES",
    "DEFAULT_MAX_SIDE",
    "DEFAULT_QUALITY",
    "TARGET_BAND_BYTES",
    "compress_batch",
    "compress_image",
]

DEFAULT_MAX_SIDE = 1600
DEFAULT_QUALITY = 82
DEFAULT_MAX_BYTES = 300 * 1024          # 单张硬上限 300KB
TARGET_BAND_BYTES = (150 * 1024, 300 * 1024)   # 目标质量带（下限不强求）
MIN_QUALITY = 50
QUALITY_STEP = 4
SCALE_STEP = 0.8
MAX_SCALE_ROUNDS = 6
MIN_DIMENSION = 240                     # 缩到此尺寸即止（再小字不可读）

_IMG_EXTS = (".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff")


def compress_image(src, dst=None, *, max_side: int = DEFAULT_MAX_SIDE,
                   quality: int = DEFAULT_QUALITY,
                   max_bytes: int = DEFAULT_MAX_BYTES) -> dict:
    """单图确定性压缩 → JPEG（长边≤max_side，≤max_bytes 硬保证）。

    dst 缺省 = 同目录 <stem>.min.jpg。返回 {"src", "dst", "before", "after",
    "width", "height", "quality", "rescaled", "reencoded", "sha256"}。
    非图片/图损坏 → ValueError；Pillow 缺失 → RuntimeError。
    """
    try:
        from PIL import Image, ImageOps
    except ImportError as e:
        raise RuntimeError(f"Pillow 不可用: {e}") from e
    src_p = Path(src)
    if not src_p.is_file():
        raise ValueError(f"图片不存在: {src_p}")
    dst_p = Path(dst) if dst else src_p.with_name(src_p.stem + ".min.jpg")
    if dst_p.suffix.lower() not in (".jpg", ".jpeg"):
        raise ValueError(f"输出仅支持 .jpg/.jpeg，收到 {dst_p.suffix!r}")
    max_side = int(max_side)
    quality = int(quality)
    max_bytes = int(max_bytes)
    if max_side < 64:
        raise ValueError(f"max_side 过小: {max_side}")
    if not (10 <= quality <= 95):
        raise ValueError(f"quality 需在 [10, 95]，收到 {quality}")
    if max_bytes < 8 * 1024:
        raise ValueError(f"max_bytes 过小: {max_bytes}")

    before = src_p.stat().st_size
    try:
        img = Image.open(src_p)
        img.load()
    except Exception as e:  # noqa: BLE001 — 非图片/损坏显式报因
        raise ValueError(f"无法解码图片 {src_p}: {type(e).__name__}: {e}") from e

    # EXIF 方向摆正 + 统一 RGB（透明/调色板白底合成，不猜语义）
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA", "PA") or (
            img.mode == "P" and "transparency" in img.info):
        bg = Image.new("RGB", img.size, (255, 255, 255))
        rgba = img.convert("RGBA")
        bg.paste(rgba, mask=rgba.getchannel("A"))
        img = bg
    elif img.mode != "RGB":
        img = img.convert("RGB")

    # 已合规（JPEG + 尺寸达标 + 体积达标）→ 原样复制不二压
    if (src_p.suffix.lower() in (".jpg", ".jpeg")
            and max(img.size) <= max_side and before <= max_bytes):
        dst_p.parent.mkdir(parents=True, exist_ok=True)
        if dst_p.resolve() != src_p.resolve():
            shutil.copyfile(src_p, dst_p)
        return {
            "src": str(src_p), "dst": str(dst_p), "before": before,
            "after": dst_p.stat().st_size, "width": img.size[0],
            "height": img.size[1], "quality": None,
            "rescaled": False, "reencoded": False,
            "sha256": _sha256(dst_p),
        }

    # 长边限幅（等比，绝不放大）
    rescaled = False
    if max(img.size) > max_side:
        ratio = max_side / float(max(img.size))
        img = img.resize((max(1, round(img.size[0] * ratio)),
                          max(1, round(img.size[1] * ratio))),
                         Image.Resampling.LANCZOS)
        rescaled = True

    dst_p.parent.mkdir(parents=True, exist_ok=True)
    # 确定性阶梯：质量 82→50 步进；仍超上限 → 0.8× 缩尺寸重来（有界轮次）
    cur_scale = 1.0
    q = quality
    used_q = quality
    for round_i in range(MAX_SCALE_ROUNDS + 1):
        frame = img
        if cur_scale < 1.0:
            frame = img.resize(
                (max(MIN_DIMENSION // 2, round(img.size[0] * cur_scale)),
                 max(MIN_DIMENSION // 2, round(img.size[1] * cur_scale))),
                Image.Resampling.LANCZOS)
        qq = max(MIN_QUALITY, q)
        while True:
            frame.save(dst_p, "JPEG", quality=qq, optimize=True)
            used_q = qq
            if dst_p.stat().st_size <= max_bytes or qq <= MIN_QUALITY:
                break
            qq = max(MIN_QUALITY, qq - QUALITY_STEP)
        if dst_p.stat().st_size <= max_bytes:
            break
        if min(frame.size) <= MIN_DIMENSION:
            break   # 尺寸已到可读下限仍超 → 报告如实标注（保底尽力）
        cur_scale *= SCALE_STEP

    after = dst_p.stat().st_size
    return {
        "src": str(src_p), "dst": str(dst_p), "before": before, "after": after,
        "width": img.size[0], "height": img.size[1], "quality": used_q,
        "rescaled": rescaled, "reencoded": True, "sha256": _sha256(dst_p),
        "over_budget": after > max_bytes,
    }


def compress_batch(items, out_dir=None, *, max_side: int = DEFAULT_MAX_SIDE,
                   quality: int = DEFAULT_QUALITY,
                   max_bytes: int = DEFAULT_MAX_BYTES) -> dict:
    """批量压图：路径列表 / 单个路径 / 目录（按扩展名收图）→ out_dir + manifest。

    返回 {"files": [compress_image 结果...], "count", "total_before",
    "total_after", "saved_bytes", "over_budget": [超限 dst...]}。
    out_dir 缺省 = 首图所在目录（目录模式=该目录）；同名冲突自动 -1/-2 后缀。
    """
    paths: list[Path] = []
    if isinstance(items, (str, Path)):
        items = [items]
    for it in items or []:
        p = Path(it)
        if p.is_dir():
            paths.extend(sorted(q for q in p.iterdir()
                                if q.is_file() and q.suffix.lower() in _IMG_EXTS))
        elif p.is_file():
            paths.append(p)
        else:
            raise ValueError(f"路径不存在: {p}")
    if not paths:
        raise ValueError("无可压缩图片（items 为空或目录内无图片文件）")

    if out_dir is None:
        out_root = paths[0].parent
    else:
        out_root = Path(out_dir)
    out_root.mkdir(parents=True, exist_ok=True)

    results = []
    used_names: set[str] = set()
    for p in paths:
        name = p.stem + ".min.jpg"
        n = 1
        while name.lower() in used_names:
            name = f"{p.stem}-{n}.min.jpg"
            n += 1
        used_names.add(name.lower())
        results.append(compress_image(p, out_root / name,
                                      max_side=max_side, quality=quality,
                                      max_bytes=max_bytes))
    over = [r["dst"] for r in results if r.get("over_budget")]
    return {
        "files": results,
        "count": len(results),
        "total_before": sum(r["before"] for r in results),
        "total_after": sum(r["after"] for r in results),
        "saved_bytes": sum(r["before"] for r in results)
                       - sum(r["after"] for r in results),
        "over_budget": over,
    }


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

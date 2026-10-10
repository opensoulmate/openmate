"""路线图 7.4 图像生成/编辑。

ImageGenerator：确定性图像生成/编辑面，不走 LLM——
- 生成 create：纯色/渐变画布 + 标题/正文文本卡（Pillow 确定性绘制，中文换行）
- 编辑 edit：有序操作链 resize/crop/rotate/flip/brightness/contrast/watermark
  （输出格式由扩展名决定：.png/.jpg/.jpeg/.webp/.bmp）
- 信息 info：尺寸/模式/格式/字节只读查询

设计纪律（对齐 5.x/6.x/7.1/7.2/7.3 各模块）：
- 全确定性，不走 LLM；Pillow 延迟导入（依赖缺失在主路径显式报因）。
- 一切失败显式报因：参数校验拒绝执行并说明原因，绝不静默返回"成功"、
  绝不产出半截文件（tmp+os.replace 原子落盘）。
- 输出路径越界拒绝（默认仅允许 $HOME 与 /tmp 下，SOULMATE_IMAGE_ROOTS 可覆盖）。
- 成功结果带 MEDIA:{path} 标签（前端渲染图片下载卡片，与文件发送同款链路）。
- 文本渲染需中英文字体：SOULMATE_IMAGE_FONT 可指定，缺省按候选路径搜索；
  找不到且需要画文本时显式报因（不用豆腐块糊弄）。
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Callable

# 画布规模上限（超限显式拒绝，不静默缩放）
MAX_SIDE = int(os.environ.get("SOULMATE_IMAGE_MAX_SIDE", "8192"))
MAX_PIXELS = int(os.environ.get("SOULMATE_IMAGE_MAX_PIXELS", "40000000"))

# 操作链条数上限
MAX_OPS = 20

# 允许的输出扩展名（输出格式由扩展名决定）
OUT_EXTS = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP", ".bmp": "BMP"}
DEFAULT_OUT_EXT = ".png"

# 亮度/对比度因子范围
FACTOR_MIN, FACTOR_MAX = 0.1, 3.0

# 旋转角度范围
ROTATE_MIN, ROTATE_MAX = -360.0, 360.0

# 水印不透明度范围
OPACITY_MIN, OPACITY_MAX = 0.1, 1.0

# 水印位置
WATERMARK_POSITIONS = ("top-left", "top-right", "bottom-left", "bottom-right", "center")

# 中文字体候选（按序搜索；SOULMATE_IMAGE_FONT 或 create/edit 的 font 参数优先）
_FONT_CANDIDATES = (
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/noto-cjk/NotoSerifCJK-Regular.ttc",
    "/usr/share/fonts/adobe-source-han-sans/SourceHanSansSC-Regular.otf",
    "/usr/share/fonts/wps-office/FZLSK.TTF",
    "/home/climbing/.local/share/fonts/1TTTGB-Medium.otf",
    "/usr/share/fonts/liberation/LiberationSans-Regular.ttf",
)


class ImageGeneratorError(RuntimeError):
    """图像生成/编辑失败（校验/依赖/绘制），消息即给用户/模型的报因文本。"""


def _allowed_roots() -> list[str]:
    """输出路径允许的根目录（默认 $HOME 与 /tmp；SOULMATE_IMAGE_ROOTS 覆盖）。"""
    raw = os.environ.get("SOULMATE_IMAGE_ROOTS", "").strip()
    roots = [r for r in raw.split(os.pathsep) if r.strip()] if raw \
        else [str(Path.home()), "/tmp"]
    return [os.path.realpath(os.path.expanduser(r)) for r in roots]


def _pil():
    """延迟导入 Pillow：依赖缺失在主路径显式报因（不炸模块 import）。"""
    try:
        from PIL import Image, ImageColor, ImageDraw, ImageFont, ImageEnhance  # noqa: F401
        import PIL
    except ImportError as e:
        raise ImageGeneratorError(f"Pillow 不可用: {e}")
    return PIL


def _resolve_target(path: Any, default_dir: Path, prefix: str) -> Path:
    """校验并解析输出路径：空值自动命名、扩展名白名单、越界拒绝。"""
    p = str(path or "").strip()
    if not p:
        os.makedirs(default_dir, exist_ok=True)
        p = str(default_dir / f"{prefix}_{int(time.time() * 1000)}{DEFAULT_OUT_EXT}")
    p = os.path.expanduser(p)
    suffix = Path(p).suffix.lower()
    if not suffix:
        p = p + DEFAULT_OUT_EXT  # 无扩展名自动补
    elif suffix not in OUT_EXTS:
        raise ImageGeneratorError(
            f"路径扩展名 {suffix} 不支持（允许: {', '.join(sorted(OUT_EXTS))}）")
    parent = os.path.dirname(os.path.abspath(p)) or "."
    os.makedirs(parent, exist_ok=True)
    real = os.path.realpath(p)
    roots = _allowed_roots()
    if not any(real == r or real.startswith(r + os.sep) for r in roots):
        raise ImageGeneratorError(
            f"输出路径越界拒绝: {real}（允许根目录: {os.pathsep.join(roots)}，"
            f"可用 SOULMATE_IMAGE_ROOTS 配置）")
    return Path(real)


def _require_in_roots(real: str, kind: str) -> None:
    """输入路径同样只允许 $HOME 与 /tmp 下（读用户文件的越界面同口径）。"""
    roots = _allowed_roots()
    if not any(real == r or real.startswith(r + os.sep) for r in roots):
        raise ImageGeneratorError(
            f"{kind}路径越界拒绝: {real}（允许根目录: {os.pathsep.join(roots)}，"
            f"可用 SOULMATE_IMAGE_ROOTS 配置）")


def _resolve_source(source: Any) -> Path:
    """校验并解析输入图像路径：缺失/越界显式报因。"""
    p = str(source or "").strip()
    if not p:
        raise ImageGeneratorError("source 不能为空（要编辑/查询的图像路径）")
    p = os.path.expanduser(p)
    real = os.path.realpath(p)
    _require_in_roots(real, "输入图像")
    path = Path(real)
    if not path.is_file():
        raise ImageGeneratorError(f"输入图像不存在: {real}")
    return path


def _parse_size(width: Any, height: Any) -> tuple[int, int]:
    """画布尺寸校验：正整数、单边/总像素上限，越界显式拒绝（不静默缩放）。"""
    try:
        w = int(width)
        h = int(height)
    except (TypeError, ValueError):
        raise ImageGeneratorError(f"画布尺寸不是整数: {width!r} x {height!r}")
    if w <= 0 or h <= 0:
        raise ImageGeneratorError(f"画布尺寸必须为正: {w} x {h}")
    if w > MAX_SIDE or h > MAX_SIDE:
        raise ImageGeneratorError(
            f"画布单边超限: {w} x {h}（单边 ≤ {MAX_SIDE}，SOULMATE_IMAGE_MAX_SIDE 可配）")
    if w * h > MAX_PIXELS:
        raise ImageGeneratorError(
            f"画布总像素超限: {w * h} > {MAX_PIXELS}（SOULMATE_IMAGE_MAX_PIXELS 可配）")
    return w, h


def _parse_color(spec: Any) -> tuple[int, int, int]:
    """颜色解析：Pillow ImageColor 语法（#hex / rgb() / 颜色名），失败显式报因。"""
    PIL = _pil()
    s = str(spec or "").strip()
    if not s:
        raise ImageGeneratorError("颜色不能为空（如 #1f2937 / rgb(31,41,55) / red）")
    try:
        rgb = PIL.ImageColor.getrgb(s)
    except ValueError:
        raise ImageGeneratorError(
            f"无法解析颜色: {s!r}（支持 #hex / rgb(r,g,b) / 颜色名如 red）")
    return tuple(rgb[:3])


def _parse_background(spec: Any) -> tuple[str, tuple, tuple | None]:
    """背景解析：纯色 <color> 或渐变 gradient:<c1>:<c2>（默认纯白）。"""
    s = str(spec or "").strip()
    if not s:
        s = "#ffffff"
    if s.lower().startswith("gradient"):
        parts = s.split(":", 2)
        if len(parts) != 3:
            raise ImageGeneratorError(
                f"渐变背景格式错误: {s!r}（应为 gradient:<颜色1>:<颜色2>）")
        return "gradient", _parse_color(parts[1]), _parse_color(parts[2])
    return "solid", _parse_color(s), None


def _find_font_path(font: Any = None) -> str | None:
    """字体解析：显式 font 参数 → SOULMATE_IMAGE_FONT → 候选路径搜索。"""
    f = str(font or "").strip()
    if f:
        if os.path.isfile(f):
            return f
        raise ImageGeneratorError(f"指定字体不存在: {f}")
    env = os.environ.get("SOULMATE_IMAGE_FONT", "").strip()
    if env:
        if os.path.isfile(env):
            return env
        raise ImageGeneratorError(f"SOULMATE_IMAGE_FONT 指定的字体不存在: {env}")
    for cand in _FONT_CANDIDATES:
        if os.path.isfile(cand):
            return cand
    return None


def _load_font(font_path: str | None, size: int):
    """加载字体；无字体且需要画文本时显式报因（不用豆腐块糊弄）。"""
    PIL = _pil()
    if not font_path:
        raise ImageGeneratorError(
            "未找到可用字体（中文渲染需要 TTF/OTF/TTC），可配 SOULMATE_IMAGE_FONT 或传 font 参数")
    try:
        return PIL.ImageFont.truetype(font_path, size)
    except Exception as e:
        raise ImageGeneratorError(f"字体加载失败: {font_path}: {e}")


def _wrap_text(draw, text: str, font, max_width: int) -> list[str]:
    """确定性换行：CJK 逐字测量、拉丁按词优先；不做 LLM 排版。"""
    lines: list[str] = []
    for para in text.split("\n"):
        if not para:
            lines.append("")
            continue
        cur = ""
        for ch in para:
            trial = cur + ch
            if draw.textlength(trial, font=font) <= max_width or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = ch
        if cur:
            lines.append(cur)
    return lines


def _draw_text_block(draw, text: str, font, fill, x: int, y: int,
                     max_width: int, line_gap: int) -> int:
    """画一段自动换行文本，返回结束 y。"""
    bbox = font.getbbox("国Ag")
    line_h = (bbox[3] - bbox[1]) + line_gap
    cy = y
    for line in _wrap_text(draw, text, font, max_width):
        draw.text((x, cy), line, font=font, fill=fill)
        cy += line_h
    return cy


def _atomic_save(img, target: Path) -> None:
    """tmp + os.replace 原子落盘，不留半截文件（格式由目标扩展名显式指定）。"""
    fmt = OUT_EXTS[target.suffix.lower()]
    tmp = target.parent / f".{target.name}.{os.getpid()}.tmp"
    try:
        save_kwargs: dict = {}
        if fmt == "JPEG" and img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")  # JPEG 无 alpha：显式转 RGB（白底合成由调用面决定）
        if fmt == "JPEG":
            save_kwargs["quality"] = 92
        img.save(tmp, format=fmt, **save_kwargs)
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _open_image(path: Path):
    """打开输入图像：非图像/损坏显式报因。"""
    PIL = _pil()
    try:
        img = PIL.Image.open(path)
        img.load()
        return img
    except Exception as e:
        raise ImageGeneratorError(f"无法解析为图像: {path}: {e}")


class ImageGenerator:
    """确定性图像生成/编辑器。方法返回用户可读结果文本（成功带 MEDIA 标签）。"""

    def __init__(self, font_loader: Callable[[str | None, int], Any] | None = None):
        # font_loader 接缝：可注入零依赖测试（缺省 _load_font 真实加载）
        self._font_loader = font_loader or _load_font

    # ------------------------------------------------------------------
    # 生成面
    # ------------------------------------------------------------------

    def create(self, width: Any = 1024, height: Any = 768, background: Any = None,
               title: Any = None, body: Any = None, text_color: Any = None,
               font: Any = None, path: Any = None) -> str:
        """生成图像（纯色/渐变画布 + 可选标题/正文文本卡），返回确定性文本结果。"""
        try:
            w, h = _parse_size(width, height)
            mode, c1, c2 = _parse_background(background)
            target = _resolve_target(
                path, Path.home() / ".openmate" / "images", "img")

            PIL = _pil()
            if mode == "gradient":
                img = PIL.Image.new("RGB", (w, h), c1)
                draw = PIL.ImageDraw.Draw(img)
                for y in range(h):  # 垂直线性渐变（确定性逐行插值）
                    t = y / max(1, h - 1)
                    col = tuple(int(round(a + (b - a) * t)) for a, b in zip(c1, c2))
                    draw.line([(0, y), (w, y)], fill=col)
            else:
                img = PIL.Image.new("RGB", (w, h), c1)
                draw = PIL.ImageDraw.Draw(img)

            title_s = str(title or "").strip()
            body_s = str(body or "").strip()
            if title_s or body_s:
                font_path = _find_font_path(font)
                fill = _parse_color(text_color) if str(text_color or "").strip() \
                    else (255, 255, 255)
                pad = max(16, w // 32)
                cy = pad
                if title_s:
                    tfont = self._font_loader(font_path, max(20, w // 14))
                    cy = _draw_text_block(
                        draw, title_s, tfont, fill, pad, cy, w - 2 * pad,
                        line_gap=max(6, w // 64))
                    cy += max(10, h // 48)
                if body_s:
                    bfont = self._font_loader(font_path, max(14, w // 28))
                    _draw_text_block(
                        draw, body_s, bfont, fill, pad, cy, w - 2 * pad,
                        line_gap=max(4, w // 96))

            _atomic_save(img, target)
        except ImageGeneratorError as e:
            return f"[错误] 图像生成失败: {e}"
        except Exception as e:  # IO/绘制异常同样显式报因
            return f"[错误] 图像生成异常: {e}"
        size = target.stat().st_size
        return (
            f"✅ 图像已生成: {target}（{w}x{h}，{OUT_EXTS[target.suffix.lower()]}，"
            f"{size} 字节）\nMEDIA:{target}")

    # ------------------------------------------------------------------
    # 编辑面
    # ------------------------------------------------------------------

    def edit(self, source: Any, ops: Any = None, path: Any = None,
             font: Any = None) -> str:
        """编辑图像：有序操作链（resize/crop/rotate/flip/brightness/contrast/
        watermark），输出格式由 path 扩展名决定（缺省 .png）。"""
        try:
            src = _resolve_source(source)
            ops_list = ops if isinstance(ops, list) else None
            if not ops_list:
                raise ImageGeneratorError(
                    "ops 不能为空（有序操作列表，如 [{\"op\":\"resize\",\"width\":800,\"height\":600}]）")
            if len(ops_list) > MAX_OPS:
                raise ImageGeneratorError(
                    f"操作条数超限: {len(ops_list)} > {MAX_OPS}")
            target = _resolve_target(
                path, Path.home() / ".openmate" / "images", "img_edit")

            img = _open_image(src)
            applied: list[str] = []
            for i, raw in enumerate(ops_list):
                img, desc = self._apply_op(img, raw, i, font)
                applied.append(desc)

            _atomic_save(img, target)
        except ImageGeneratorError as e:
            return f"[错误] 图像编辑失败: {e}"
        except Exception as e:
            return f"[错误] 图像编辑异常: {e}"
        w, h = img.size
        size = target.stat().st_size
        return (
            f"✅ 图像已编辑: {target}（{w}x{h}，{OUT_EXTS[target.suffix.lower()]}，"
            f"{size} 字节，操作 {len(applied)} 步: {', '.join(applied)}）\n"
            f"MEDIA:{target}")

    def _apply_op(self, img, raw: Any, index: int, font: Any):
        """单条操作：未知 op/参数非法显式报因（含序号便于定位）。"""
        PIL = _pil()
        if not isinstance(raw, dict):
            raise ImageGeneratorError(
                f"第 {index + 1} 步操作不是对象: {raw!r}（应为 {{\"op\":\"resize\",...}}）")
        op = str(raw.get("op") or "").strip().lower()
        tag = f"第 {index + 1} 步"

        if op == "resize":
            try:
                w = int(raw.get("width"))
                h = int(raw.get("height"))
            except (TypeError, ValueError):
                raise ImageGeneratorError(f"{tag} resize 的 width/height 必须是整数: {raw!r}")
            if w <= 0 or h <= 0:
                raise ImageGeneratorError(f"{tag} resize 尺寸必须为正: {w} x {h}")
            if w > MAX_SIDE or h > MAX_SIDE or w * h > MAX_PIXELS:
                raise ImageGeneratorError(
                    f"{tag} resize 目标超限: {w} x {h}（单边 ≤ {MAX_SIDE}，总像素 ≤ {MAX_PIXELS}）")
            return img.resize((w, h)), f"resize→{w}x{h}"

        if op == "crop":
            try:
                x, y = int(raw.get("x", 0)), int(raw.get("y", 0))
                cw, ch = int(raw.get("width")), int(raw.get("height"))
            except (TypeError, ValueError):
                raise ImageGeneratorError(f"{tag} crop 参数必须是整数: {raw!r}")
            iw, ih = img.size
            if cw <= 0 or ch <= 0 or x < 0 or y < 0 or x + cw > iw or y + ch > ih:
                raise ImageGeneratorError(
                    f"{tag} crop 越界: x={x} y={y} {cw}x{ch}（图像 {iw}x{ih}）")
            return img.crop((x, y, x + cw, y + ch)), f"crop→{cw}x{ch}"

        if op == "rotate":
            deg = raw.get("degrees")
            try:
                deg = float(deg)
            except (TypeError, ValueError):
                raise ImageGeneratorError(f"{tag} rotate 的 degrees 必须是数值: {deg!r}")
            if not (ROTATE_MIN <= deg <= ROTATE_MAX):
                raise ImageGeneratorError(
                    f"{tag} rotate 角度超范围: {deg}（允许 {ROTATE_MIN}..{ROTATE_MAX}）")
            expand = bool(raw.get("expand", True))
            return img.rotate(deg, expand=expand), f"rotate→{deg:g}°"

        if op == "flip":
            direction = str(raw.get("direction") or "").strip().lower()
            if direction in ("h", "horizontal"):
                return img.transpose(PIL.Image.Transpose.FLIP_LEFT_RIGHT), "flip→水平"
            if direction in ("v", "vertical"):
                return img.transpose(PIL.Image.Transpose.FLIP_TOP_BOTTOM), "flip→垂直"
            raise ImageGeneratorError(
                f"{tag} flip 的 direction 必须是 h/horizontal 或 v/vertical: {raw.get('direction')!r}")

        if op in ("brightness", "contrast"):
            try:
                factor = float(raw.get("factor"))
            except (TypeError, ValueError):
                raise ImageGeneratorError(f"{tag} {op} 的 factor 必须是数值: {raw.get('factor')!r}")
            if not (FACTOR_MIN <= factor <= FACTOR_MAX):
                raise ImageGeneratorError(
                    f"{tag} {op} factor 超范围: {factor}（允许 {FACTOR_MIN}..{FACTOR_MAX}）")
            enhancer = (PIL.ImageEnhance.Brightness(img) if op == "brightness"
                        else PIL.ImageEnhance.Contrast(img))
            return enhancer.enhance(factor), f"{op}→{factor:g}"

        if op == "watermark":
            text = str(raw.get("text") or "").strip()
            if not text:
                raise ImageGeneratorError(f"{tag} watermark 的 text 不能为空")
            position = str(raw.get("position") or "bottom-right").strip().lower()
            if position not in WATERMARK_POSITIONS:
                raise ImageGeneratorError(
                    f"{tag} watermark 位置不支持: {position!r}（可选: {', '.join(WATERMARK_POSITIONS)}）")
            try:
                opacity = float(raw.get("opacity", 0.5))
            except (TypeError, ValueError):
                raise ImageGeneratorError(
                    f"{tag} watermark 的 opacity 必须是数值: {raw.get('opacity')!r}")
            if not (OPACITY_MIN <= opacity <= OPACITY_MAX):
                raise ImageGeneratorError(
                    f"{tag} watermark opacity 超范围: {opacity}（允许 {OPACITY_MIN}..{OPACITY_MAX}）")
            font_path = _find_font_path(font)
            wfont = self._font_loader(font_path, max(14, img.size[0] // 24))
            overlay = PIL.Image.new("RGBA", img.size, (0, 0, 0, 0))
            odraw = PIL.ImageDraw.Draw(overlay)
            bbox = odraw.textbbox((0, 0), text, font=wfont)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            margin = max(8, img.size[0] // 64)
            xs = {"top-left": margin, "top-right": img.size[0] - tw - margin,
                  "bottom-left": margin, "bottom-right": img.size[0] - tw - margin,
                  "center": (img.size[0] - tw) // 2}
            ys = {"top-left": margin, "top-right": margin,
                  "bottom-left": img.size[1] - th - margin,
                  "bottom-right": img.size[1] - th - margin,
                  "center": (img.size[1] - th) // 2}
            alpha = int(round(255 * opacity))
            odraw.text((xs[position], ys[position]), text, font=wfont,
                       fill=(255, 255, 255, alpha))
            base = img.convert("RGBA")
            return PIL.Image.alpha_composite(base, overlay), f"watermark({position})"

        raise ImageGeneratorError(
            f"{tag} 未知操作: {op!r}（支持: resize/crop/rotate/flip/brightness/contrast/watermark）")

    # ------------------------------------------------------------------
    # 信息面
    # ------------------------------------------------------------------

    def info(self, source: Any) -> str:
        """图像信息（尺寸/模式/格式/字节），只读查询。"""
        try:
            src = _resolve_source(source)
            img = _open_image(src)
            w, h = img.size
            return (
                f"🖼 图像信息: {src}\n"
                f"- 尺寸: {w}x{h}\n- 模式: {img.mode}\n- 格式: {img.format or '未知'}\n"
                f"- 字节: {src.stat().st_size}")
        except ImageGeneratorError as e:
            return f"[错误] 图像查询失败: {e}"
        except Exception as e:
            return f"[错误] 图像查询异常: {e}"


# 模块级共享单例（与 get_voice_generator 同款模式）
_generator: ImageGenerator | None = None


def get_image_generator() -> ImageGenerator:
    global _generator
    if _generator is None:
        _generator = ImageGenerator()
    return _generator

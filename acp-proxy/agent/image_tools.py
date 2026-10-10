"""image_tools.py — 图像生成/编辑工具面（路线图 7.4）。

ImageTools = 图像生成 + 确定性图像编辑（Pillow），输出落盘 ~/.hermes/soulmate/images/，
返回 MEDIA: 路径可直接下发。
- 编辑（确定性，不走 LLM 猜测）：resize/crop/rotate/flip/convert/annotate/compose/thumbnail；
- 生成（三级后端分层，对标 7.3 TTS 诚实多态纪律）：
  1) SOULMATE_IMAGEGEN_API_URL 指向 OpenAI 兼容 images API 时真实生成（自有/本地后端优先）；
  2) 未配置 API → Pollinations 免费真实 AI 生成（默认启用，prompt 文本会发给
     pollinations.ai 云服务；SOULMATE_IMAGEGEN_POLLINATIONS=off 关闭，
     SOULMATE_IMAGEGEN_POLLINATIONS_URL/MODEL 可覆盖）；
  3) 都关闭 → PIL 程序化绘制兜底（渐变底+文字卡），输出显式标注
     "⚠️ 程序化绘制（非AI生成）" 禁假称 AI 生成 + 文档化安装/配置方法。

诚实状态语义（对标 browser_tools/docgen_tools 纪律，禁静默）：
- Pillow 缺失 → 显式"⚠️ 图像后端不可用"+安装提示；
- 输入文件不存在/不可解码 → 显式报因；
- 参数非法（未知 action/越界文件名/非法数值）→ 显式拒绝+正确用法；
- 生成 API 失败/超时 → 显式报因（兜底绘制须显式标注降级，禁假称 AI 成功）；
- 0 字节输出不算成功。
"""
import base64
import colorsys
import json
import logging
import os
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

logger = logging.getLogger("acp-agent.image-tools")

EDIT_ACTIONS = ("resize", "crop", "rotate", "flip", "convert", "annotate", "compose", "thumbnail")
ACTIONS = ("generate",) + EDIT_ACTIONS

DEFAULT_OUT_DIR = os.path.expanduser("~/.hermes/soulmate/images")
DEFAULT_GEN_SIZE = 512
MAX_DIM = 8192
GEN_TIMEOUT_S = 90.0

_PIL_HINT = "安装方法：服务 venv 内 `pip install Pillow`（acp-proxy 服务所用 interpreter）。"
_GEN_HINT = (
    "AI 图像生成后端配置方法（优先级从高到低）："
    "①SOULMATE_IMAGEGEN_API_URL 指向 OpenAI 兼容 images API"
    "（POST {url}/v1/images/generations 或完整端点，body {{prompt, size, n:1}}，"
    "key 用 SOULMATE_IMAGEGEN_API_KEY），或本地部署 ComfyUI/SD WebUI 后填其 API 地址；"
    "②未配置时默认走 Pollinations 免费真实 AI 生成（prompt 文本发给 pollinations.ai 云服务，"
    "免费匿名额度间歇 402 限流属服务端配额非本机故障；SOULMATE_IMAGEGEN_POLLINATIONS=off 关闭，"
    "SOULMATE_IMAGEGEN_POLLINATIONS_URL/MODEL/REFERRER 可覆盖，REFERRER 提额）；"
    "③两级都不可用时程序化绘制兜底（显式标注非AI生成）。"
)
_POLLINATIONS_DEFAULT_URL = "https://image.pollinations.ai"
_POLLINATIONS_OFF = ("off", "false", "0", "no")

_SAFE_NAME_RE = re.compile(r"^[\w\u4e00-\u9fff][\w\u4e00-\u9fff.\-]*$")

# annotate 字体查找顺序：显式配置 → Noto CJK → DejaVu（确定性，不猜）
_FONT_GLOBS = (
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Medium.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Light.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
)


class ImageTools:
    """图像生成/编辑（Pillow），输出落盘 ~/.hermes/soulmate/images。"""

    def __init__(self, out_dir: str = "", font_path: str = "",
                 generator=None, opener=None, now=None):
        self.out_dir = Path(out_dir or DEFAULT_OUT_DIR)
        self.font_path = font_path or os.environ.get("SOULMATE_IMAGE_FONT", "")
        # generator 可注入（测试 fake）；缺省走 HTTP API 后端
        self._generator = generator or self._api_generate
        # opener 可注入（测试 fake）：(path)->PIL.Image（解码统一入口）
        self._opener = opener
        self._now = now or time.time

    # ── 入口 ─────────────────────────────────────────────────────────

    def run(self, action: str, params: dict) -> str:
        action = str(action or "").strip().lower()
        params = dict(params or {})
        if action not in ACTIONS:
            return f"❌ 未知 action: {action!r}。支持: {', '.join(ACTIONS)}"
        try:
            if action == "generate":
                return self._generate(params)
            return self._edit(action, params)
        except ValueError as e:
            return f"❌ 参数错误: {e}"
        except Exception as e:
            logger.exception("image op failed: action=%s", action)
            return f"❌ 图像处理失败（{action}）: {type(e).__name__}: {e}"

    # ── 生成 ─────────────────────────────────────────────────────────

    def _generate(self, params: dict) -> str:
        prompt = str(params.get("prompt", "") or "").strip()
        if not prompt:
            raise ValueError("generate 需要 prompt（画面描述）")
        width, height = self._size(params, DEFAULT_GEN_SIZE, DEFAULT_GEN_SIZE)
        out_path = self._resolve_out_path(params, "png")
        try:
            from PIL import Image, ImageDraw  # noqa: F401
        except ImportError as e:
            return f"⚠️ 图像后端不可用：Pillow 缺失（{e}）\n{_PIL_HINT}"

        api_url = str(params.get("api_url", "") or os.environ.get("SOULMATE_IMAGEGEN_API_URL", "") or "").strip()
        # AI 后端三级链：①OpenAI 兼容 API（显式配置）②Pollinations 真实 AI（默认开）③PIL 兜底
        if api_url or params.get("_force_generator"):
            backend, gen = "generate(AI)", lambda: self._generator(prompt, width, height)
        elif self._pollinations_enabled():
            backend, gen = "generate(AI·Pollinations)", lambda: self._pollinations_generate(prompt, width, height)
        else:
            backend, gen = "", lambda: b""  # 占位：backend 为空时不会被调用
        if backend:
            try:
                data = gen()
            except Exception as e:
                # AI 后端失败 → 显式报因；不悄悄换兜底冒充 AI 生成
                return (f"❌ AI 图像生成失败：{type(e).__name__}: {e}\n"
                        f"（未降级假称成功；如需程序化绘制请明确要求）\n{_GEN_HINT}")
            if not data or not isinstance(data, (bytes, bytearray)):
                return f"❌ AI 图像生成返回空内容（后端: {api_url or backend}）\n{_GEN_HINT}"
            out_path.write_bytes(bytes(data))
            if not self._valid_output(out_path):
                return f"❌ AI 图像生成产物无效（0字节/不可解码）: {out_path}"
            img = self._open(out_path)
            return self._success(backend, self._align_ext(out_path, img), img)

        # AI 后端全关闭 → 程序化绘制兜底（诚实标注，禁假称 AI 生成）
        img = self._procedural_render(prompt, width, height)
        img.save(str(out_path), format="PNG")
        if not self._valid_output(out_path):
            return f"❌ 程序化绘制产物无效（0字节/不可解码）: {out_path}"
        return (
            "⚠️ 未配置 AI 图像生成后端，本次为程序化绘制（非AI生成：渐变底+文字排版，"
            "不理解画面语义）。\n" + _GEN_HINT + "\n"
            + self._success("generate(程序化绘制)", out_path, img)
        )

    def _procedural_render(self, prompt: str, width: int, height: int):
        """确定性程序化绘制：prompt 哈希选色渐变底 + 文字卡（诚实兜底）。"""
        from PIL import Image, ImageDraw, ImageFont
        hue = (sum(prompt.encode("utf-8")) * 7) % 360
        top = tuple(int(c * 255) for c in colorsys.hsv_to_rgb(hue / 360.0, 0.35, 0.85))
        bottom = tuple(int(c * 255) for c in colorsys.hsv_to_rgb(((hue + 40) % 360) / 360.0, 0.45, 0.55))
        img = Image.new("RGB", (width, height))
        draw = ImageDraw.Draw(img)
        for y in range(height):
            t = y / max(height - 1, 1)
            draw.line([(0, y), (width, y)], fill=tuple(
                int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
        font = self._load_font(max(18, min(width, height) // 16))
        fsize = int(getattr(font, "size", 0) or 24)
        # 简单按像素宽度折行（确定性）
        max_w = int(width * 0.86)
        lines, cur = [], ""
        for ch in prompt:
            if ch == "\n" or self._text_w(draw, cur + ch, font) > max_w:
                lines.append(cur)
                cur = "" if ch == "\n" else ch
            else:
                cur += ch
        if cur:
            lines.append(cur)
        lines = lines[:24]
        line_h = fsize + 6
        block_h = line_h * len(lines)
        y = max((height - block_h) // 2, 8)
        for ln in lines:
            w = self._text_w(draw, ln, font)
            draw.text(((width - w) // 2, y), ln, font=font, fill=(255, 255, 255))
            y += line_h
        draw.text((10, height - 24), "[procedural render / 程序化绘制]",
                  font=self._load_font(14), fill=(255, 255, 255, 180))
        return img

    def _api_generate(self, prompt: str, width: int, height: int) -> bytes:
        """OpenAI 兼容 images API（b64_json 或 url）。有界超时禁挂死。"""
        base = os.environ.get("SOULMATE_IMAGEGEN_API_URL", "").strip()
        if not base:
            raise RuntimeError("SOULMATE_IMAGEGEN_API_URL 未配置")
        url = base.rstrip("/")
        if not url.endswith("/images/generations"):
            url = url + "/v1/images/generations" if "/v1" not in url else url + "/images/generations"
        key = os.environ.get("SOULMATE_IMAGEGEN_API_KEY", "").strip()
        body = json.dumps({"prompt": prompt, "size": f"{width}x{height}", "n": 1}).encode("utf-8")
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        if key:
            req.add_header("Authorization", f"Bearer {key}")
        with urllib.request.urlopen(req, timeout=GEN_TIMEOUT_S) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
        item = (payload.get("data") or [{}])[0]
        if item.get("b64_json"):
            return base64.b64decode(item["b64_json"])
        if item.get("url"):
            with urllib.request.urlopen(item["url"], timeout=GEN_TIMEOUT_S) as r2:
                return r2.read()
        raise RuntimeError("images API 响应无 b64_json/url")

    @staticmethod
    def _align_ext(out_path: Path, img) -> Path:
        """AI 后端产物实际格式与扩展名不符时改名对齐（FileViewer 按扩展名定 mimeType）。

        实证（周期#52 e2e）：Pollinations 返回 JPEG 字节却落成 .png 文件名——预览按
        image/png 嗅探会错。按 PIL 解码出的真实 format 改后缀，其余不动。
        """
        fmt = str(getattr(img, "format", "") or "").lower()
        if not fmt:  # _open 返回的副本可能丢 format → 重新探文件头（确定性）
            try:
                from PIL import Image
                with Image.open(str(out_path)) as probe:
                    fmt = str(probe.format or "").lower()
            except Exception:
                fmt = ""
        want = {"jpeg": ".jpg", "png": ".png", "gif": ".gif",
                "webp": ".webp", "bmp": ".bmp"}.get(fmt, "")
        if not want or out_path.suffix.lower() == want:
            return out_path
        new_path = out_path.with_suffix(want)
        out_path.replace(new_path)
        return new_path

    @staticmethod
    def _pollinations_enabled() -> bool:
        """Pollinations 真实 AI 后端开关（默认启用；=off/false/0/no 关闭）。"""
        return os.environ.get("SOULMATE_IMAGEGEN_POLLINATIONS", "on").strip().lower() not in _POLLINATIONS_OFF

    def _pollinations_generate(self, prompt: str, width: int, height: int) -> bytes:
        """Pollinations 免费真实 AI 生成（GET /prompt/{q} 返回图像字节）。有界超时禁挂死。

        隐私边界：prompt 文本会发给 pollinations.ai 云服务；自有/本地后端请走
        SOULMATE_IMAGEGEN_API_URL（优先级更高），不要外发可改 SOULMATE_IMAGEGEN_POLLINATIONS=off。
        """
        base = os.environ.get("SOULMATE_IMAGEGEN_POLLINATIONS_URL", _POLLINATIONS_DEFAULT_URL).strip().rstrip("/")
        if not base:
            raise RuntimeError("SOULMATE_IMAGEGEN_POLLINATIONS_URL 为空")
        url = f"{base}/prompt/{urllib.parse.quote(prompt)}?width={int(width)}&height={int(height)}&nologo=true"
        model = os.environ.get("SOULMATE_IMAGEGEN_POLLINATIONS_MODEL", "").strip()
        if model:
            url += f"&model={urllib.parse.quote(model)}"
        referrer = os.environ.get("SOULMATE_IMAGEGEN_POLLINATIONS_REFERRER", "").strip()
        if referrer:  # Pollinations 免费匿名额度间歇 402（实测 1/3），referrer/token 可提额
            url += f"&referrer={urllib.parse.quote(referrer)}"
        # 公共后端偶发 5xx（实测 500）：有界重试 2 次（对齐 MiMo 400 重试纪律），仍失败显式报因
        last_err: Exception = RuntimeError("Pollinations 无响应")
        for attempt in range(2):
            try:
                with urllib.request.urlopen(url, timeout=GEN_TIMEOUT_S) as resp:
                    data = resp.read()
                if not data:
                    raise RuntimeError("Pollinations 返回空内容")
                return data
            except Exception as e:  # noqa: PERF203 — 重试边界即在此
                last_err = e
                if attempt == 0:
                    time.sleep(2.0)
        raise last_err

    # ── 编辑（确定性）─────────────────────────────────────────────────

    def _edit(self, action: str, params: dict) -> str:
        src = self._require_source(params)
        img = self._open(src)
        from PIL import Image, ImageDraw, ImageOps  # Pillow 缺失已在 _open 显式报因
        ext = self._default_ext(img)
        out_path = self._resolve_out_path(params, ext, stem_hint=f"{src.stem}-{action}")

        if action == "resize":
            w, h = self._size(params, img.width, img.height)
            img = img.resize((w, h), self._resampling())
            detail = f"尺寸 {w}x{h}"
        elif action == "crop":
            box = params.get("box")
            if not isinstance(box, (list, tuple)) or len(box) != 4:
                raise ValueError("crop 需要 box=[x, y, width, height]")
            x, y, cw, ch = (int(v) for v in box)
            if x < 0 or y < 0 or cw <= 0 or ch <= 0:
                raise ValueError(f"crop box 非法: {list(box)}（x/y≥0，宽高>0）")
            if x + cw > img.width or y + ch > img.height:
                raise ValueError(
                    f"crop box 越界: {list(box)} 超出图像 {img.width}x{img.height}")
            img = img.crop((x, y, x + cw, y + ch))
            detail = f"裁剪 box={list(box)}"
        elif action == "rotate":
            angle = float(params.get("angle", 0))
            if not -360 <= angle <= 360:
                raise ValueError(f"rotate angle 非法: {angle}（-360~360）")
            expand = bool(params.get("expand", True))
            img = img.rotate(angle, expand=expand)
            detail = f"旋转 {angle}° expand={expand}"
        elif action == "flip":
            direction = str(params.get("direction", "") or "horizontal").strip().lower()
            if direction in ("horizontal", "h"):
                img = ImageOps.mirror(img)
            elif direction in ("vertical", "v"):
                img = ImageOps.flip(img)
            else:
                raise ValueError(f"flip direction 非法: {direction!r}（horizontal|vertical）")
            detail = f"翻转 {direction}"
        elif action == "convert":
            fmt = str(params.get("format", "") or "").strip().lower()
            fmt = {"jpeg": "jpg"}.get(fmt, fmt)
            if fmt not in ("png", "jpg", "webp", "bmp", "gif"):
                raise ValueError(f"convert format 非法: {fmt!r}（png|jpg|webp|bmp|gif）")
            out_path = self._resolve_out_path(params, fmt, stem_hint=f"{src.stem}-convert")
            if fmt == "jpg" and img.mode in ("RGBA", "P", "LA"):
                bg = Image.new("RGB", img.size, (255, 255, 255))
                bg.paste(img.convert("RGBA"), mask=img.convert("RGBA").split()[-1])
                img = bg
            detail = f"格式 {fmt}"
        elif action == "annotate":
            text = str(params.get("text", "") or "").strip()
            if not text:
                raise ValueError("annotate 需要 text（叠加文字）")
            img = img.convert("RGB") if img.mode not in ("RGB", "RGBA") else img
            draw = ImageDraw.Draw(img)
            font = self._load_font(int(params.get("font_size", 0) or max(16, img.width // 24)))
            position = str(params.get("position", "") or "bottom").strip().lower()
            color = str(params.get("color", "") or "#FFFFFF").strip()
            fill = self._parse_color(color)
            stroke = (0, 0, 0) if img.mode == "RGB" else (0, 0, 0, 255)
            if position in ("top", "bottom", "center"):
                tw = self._text_w(draw, text, font)
                th = int(getattr(font, "size", 0) or 20) + 8
                x = (img.width - tw) // 2
                y = {"top": 8, "bottom": img.height - th - 8, "center": (img.height - th) // 2}[position]
            else:
                m = re.match(r"^(\d+)[,:](\d+)$", position)
                if not m:
                    raise ValueError(
                        f"annotate position 非法: {position!r}（top|bottom|center|x,y）")
                x, y = int(m.group(1)), int(m.group(2))
            draw.text((x, y), text, font=font, fill=fill,
                      stroke_width=2, stroke_fill=stroke)
            detail = f"文字叠加 {text[:20]!r} @({x},{y})"
        elif action == "compose":
            src2 = self._require_source(params, key="path2")
            img2 = self._open(src2)
            layout = str(params.get("layout", "") or "horizontal").strip().lower()
            if layout in ("horizontal", "h", "vertical", "v"):
                if layout in ("horizontal", "h"):
                    h = max(img.height, img2.height)
                    canvas = Image.new("RGB", (img.width + img2.width, h), (255, 255, 255))
                    canvas.paste(img.convert("RGB"), (0, (h - img.height) // 2))
                    canvas.paste(img2.convert("RGB"), (img.width, (h - img2.height) // 2))
                else:
                    w = max(img.width, img2.width)
                    canvas = Image.new("RGB", (w, img.height + img2.height), (255, 255, 255))
                    canvas.paste(img.convert("RGB"), ((w - img.width) // 2, 0))
                    canvas.paste(img2.convert("RGB"), ((w - img2.width) // 2, img.height))
                img = canvas
                detail = f"拼接 {layout}"
            elif layout == "overlay":
                pos = params.get("position", [0, 0])
                if not isinstance(pos, (list, tuple)) or len(pos) != 2:
                    raise ValueError("compose overlay 需要 position=[x, y]")
                x, y = int(pos[0]), int(pos[1])
                if x < 0 or y < 0 or x + img2.width > img.width or y + img2.height > img.height:
                    raise ValueError(f"compose overlay position 越界: {[x, y]}（底图 {img.width}x{img.height}）")
                img = img.convert("RGBA")
                img.paste(img2.convert("RGBA"), (x, y), img2.convert("RGBA"))
                img = img.convert("RGB")
                detail = f"叠加 @({x},{y})"
            else:
                raise ValueError(f"compose layout 非法: {layout!r}（horizontal|vertical|overlay）")
        elif action == "thumbnail":
            mw, mh = self._size(params, img.width, img.height)
            img.thumbnail((mw, mh), self._resampling())
            detail = f"缩略 ≤{mw}x{mh}（实际 {img.width}x{img.height}）"
        else:  # pragma: no cover — run() 已挡
            return f"❌ 未知 action: {action!r}"

        img.save(str(out_path))
        if not self._valid_output(out_path):
            return f"❌ 输出产物无效（0字节/不可解码）: {out_path}"
        return self._success(action, out_path, img, extra=detail)

    # ── 基础设施 ─────────────────────────────────────────────────────

    def _require_source(self, params: dict, key: str = "path") -> Path:
        raw = str(params.get(key, "") or "").strip()
        if not raw:
            raise ValueError(f"需要 {key} 参数（输入图像路径）")
        p = Path(os.path.expanduser(raw))
        if not p.is_file():
            raise ValueError(f"输入文件不存在: {raw}")
        return p

    def _open(self, path: Path):
        try:
            from PIL import Image
        except ImportError as e:
            raise RuntimeError(f"图像后端不可用：Pillow 缺失（{e}）\n{_PIL_HINT}") from e
        try:
            if self._opener is not None:
                return self._opener(str(path))
            with Image.open(str(path)) as im:
                im.load()
                return im.copy()
        except RuntimeError:
            raise
        except Exception as e:
            raise ValueError(f"图像不可解码: {path}（{type(e).__name__}: {e}）") from e

    def _size(self, params: dict, def_w: int, def_h: int):
        """width/height 均未传 → 用默认（def_w/def_h）；单边未传 → 用对应默认。"""
        w = params.get("width")
        h = params.get("height")
        if w is None and h is None:
            return int(def_w), int(def_h)
        w = int(w) if w is not None else 0
        h = int(h) if h is not None else 0
        if w < 0 or h < 0 or w > MAX_DIM or h > MAX_DIM:
            raise ValueError(f"尺寸非法: width={w} height={h}（1~{MAX_DIM}）")
        return (w or int(def_w)), (h or int(def_h))

    def _resolve_out_path(self, params: dict, ext: str, stem_hint: str = "image") -> Path:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        name = str(params.get("filename", "") or "").strip()
        if not name:
            name = f"{stem_hint}-{int(self._now() * 1000)}.{ext}"
        if not name.lower().endswith(f".{ext}"):
            name = f"{name}.{ext}"
        if "/" in name or "\\" in name or not _SAFE_NAME_RE.match(name):
            raise ValueError(
                f"filename 非法: {name!r}（只允许文件名本身，不含路径分隔符）")
        out_path = (self.out_dir / name).resolve()
        if out_path.parent != self.out_dir.resolve():
            raise ValueError("输出路径越界（必须落在图像目录内）")
        return out_path

    def _load_font(self, size: int):
        from PIL import ImageFont
        candidates = ([self.font_path] if self.font_path else []) + list(_FONT_GLOBS)
        for cand in candidates:
            if cand and Path(cand).exists():
                try:
                    return ImageFont.truetype(cand, size)
                except Exception:
                    continue
        return ImageFont.load_default(size=size)

    @staticmethod
    def _text_w(draw, text: str, font) -> int:
        try:
            return int(draw.textlength(text, font=font))
        except Exception:
            return len(text) * 10

    @staticmethod
    def _parse_color(color: str):
        m = re.match(r"^#?([0-9a-fA-F]{6})$", color.strip())
        if m:
            v = int(m.group(1), 16)
            return ((v >> 16) & 255, (v >> 8) & 255, v & 255)
        named = {"white": (255, 255, 255), "black": (0, 0, 0), "red": (220, 38, 38),
                 "yellow": (250, 204, 21), "green": (22, 163, 74), "blue": (37, 99, 235)}
        key = color.strip().lower()
        if key in named:
            return named[key]
        raise ValueError(f"color 非法: {color!r}（#RRGGBB 或 white/black/red/yellow/green/blue）")

    @staticmethod
    def _resampling():
        from PIL import Image
        return getattr(Image, "Resampling", Image).LANCZOS

    @staticmethod
    def _default_ext(img) -> str:
        fmt = (getattr(img, "format", "") or "").lower()
        return {"jpeg": "jpg", "mpo": "jpg"}.get(fmt, fmt or "png")

    @staticmethod
    def _valid_output(path: Path) -> bool:
        if not path.exists() or path.stat().st_size <= 0:
            return False
        try:
            from PIL import Image
            with Image.open(str(path)) as im:
                im.load()
            return True
        except Exception:
            return False

    @staticmethod
    def _success(action: str, out_path: Path, img, extra: str = "") -> str:
        size = out_path.stat().st_size if out_path.exists() else 0
        lines = [f"✅ 图像已处理（{action}）: {out_path}（{size} 字节，{img.width}x{img.height}）"]
        if extra:
            lines.append(f"处理: {extra}")
        lines.append(f"MEDIA:{out_path}")
        return "\n".join(lines)

"""路线图 7.3 TTS/语音（文本→语音音频）。

VoiceGenerator：确定性语音合成面，不走 LLM——
- 主引擎 edge-tts（微软神经语音，mp3 输出，多语种/多音色）
- 离线兜底 espeak-ng（edge 失败时自动接管，wav→ffmpeg 转 mp3，同款输出格式）
- 输出统一 mp3：路径缺扩展名自动补 .mp3，其余扩展名显式拒绝（路径处理确定性）

设计纪律（对齐 5.x/6.x/7.1/7.2 各模块）：
- 全确定性，不走 LLM；合成器可注入（edge_runner / fallback_runner 接缝），
  测试零外部依赖；edge-tts 为异步 API，统一走独立线程 asyncio.run（宿主
  正处于事件循环内时同样安全，不炸宿主 loop）。
- 一切失败显式报因：文本/音色/语速/路径校验拒绝执行并说明原因；edge 与
  兜底都失败时带双方报因，绝不静默返回"成功"、绝不产出半截文件
  （tmp+os.replace 原子落盘）。
- 输出路径越界拒绝（默认仅允许 $HOME 与 /tmp 下，SOULMATE_VOICE_ROOTS 可覆盖）。
- 成功结果带 MEDIA:{path} 标签（前端渲染音频下载卡片，与文件发送同款链路）。
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

# 音色预设（友好名 → edge-tts 神经语音；覆盖中英日韩，与 Hermes TTS 场景对齐）
VOICE_PRESETS: dict[str, str] = {
    "xiaoxiao": "zh-CN-XiaoxiaoNeural",   # 中文女声（默认）
    "yunxi": "zh-CN-YunxiNeural",         # 中文男声
    "xiaoyi": "zh-CN-XiaoyiNeural",       # 中文女声2
    "yunjian": "zh-CN-YunjianNeural",     # 中文男声2
    "jenny": "en-US-JennyNeural",         # 英文女声
    "aria": "en-US-AriaNeural",           # 英文女声2
    "nanami": "ja-JP-NanamiNeural",       # 日语女声
    "sunhi": "ko-KR-SunHiNeural",         # 韩语女声
}
DEFAULT_VOICE = "xiaoxiao"

# 文本规模上限（超限显式拒绝，不静默截断）
MAX_TEXT_CHARS = int(os.environ.get("SOULMATE_TTS_MAX_CHARS", "5000"))

# 合成超时（秒），env 可覆盖
TTS_TIMEOUT_S = float(os.environ.get("SOULMATE_TTS_TIMEOUT_S", "60"))

# 语速夹取（相对基准 ±50%）
RATE_MIN, RATE_MAX = -50, 50

# 唯一输出容器
OUT_EXT = ".mp3"

# espeak-ng 兜底：edge 音色 locale → espeak 语言
_ESPEAK_LANG_OF = {
    "zh": "zh", "en": "en-us", "ja": "ja", "ko": "ko",
}


class VoiceGeneratorError(RuntimeError):
    """语音合成失败（校验/依赖/合成），消息即给用户/模型的报因文本。"""


def _allowed_roots() -> list[str]:
    """输出路径允许的根目录（默认 $HOME 与 /tmp；SOULMATE_VOICE_ROOTS 覆盖）。"""
    raw = os.environ.get("SOULMATE_VOICE_ROOTS", "").strip()
    roots = [r for r in raw.split(os.pathsep) if r.strip()] if raw \
        else [str(Path.home()), "/tmp"]
    return [os.path.realpath(os.path.expanduser(r)) for r in roots]


def _resolve_voice(voice: Any) -> str:
    """音色归一：预设友好名或 *Neural 原始名；未知显式拒绝并列出预设。"""
    v = str(voice or "").strip()
    if not v:
        return VOICE_PRESETS[DEFAULT_VOICE]
    key = v.lower()
    if key in VOICE_PRESETS:
        return VOICE_PRESETS[key]
    if v.endswith("Neural"):  # 原始 edge-tts 音色名直接透传
        return v
    raise VoiceGeneratorError(
        f"未知音色: {v!r}（预设: {', '.join(sorted(VOICE_PRESETS))}，"
        f"或传 *Neural 原始名如 zh-CN-XiaoxiaoNeural）")


def _resolve_rate(rate: Any) -> tuple[str, int]:
    """语速归一：数值或字符串数值 -50..+50 → (edge格式'+N%', espeak语速)。

    空值 = 基准语速；非数值/越界显式拒绝。
    """
    if rate is None or str(rate).strip() == "":
        return "+0%", 160
    try:
        val = float(str(rate).strip().rstrip("%"))
    except ValueError:
        raise VoiceGeneratorError(f"语速参数不是数值: {rate!r}（-50..+50，相对基准百分比）")
    if not (RATE_MIN <= val <= RATE_MAX):
        raise VoiceGeneratorError(f"语速超出范围: {val}（允许 {RATE_MIN}..{RATE_MAX}）")
    pct = int(round(val))
    edge_rate = f"{pct:+d}%"
    espeak_speed = int(round(160 * (1 + pct / 100)))
    return edge_rate, max(80, min(450, espeak_speed))


def _resolve_target(path: Any) -> Path:
    """校验并解析输出路径：空值自动命名、扩展名仅允许 .mp3、越界拒绝。"""
    p = str(path or "").strip()
    if not p:
        base = Path.home() / ".openmate" / "voice"
        os.makedirs(base, exist_ok=True)
        p = str(base / f"tts_{int(time.time() * 1000)}{OUT_EXT}")
    p = os.path.expanduser(p)
    suffix = Path(p).suffix.lower()
    if not suffix:
        p = p + OUT_EXT  # 无扩展名自动补
    elif suffix != OUT_EXT:
        raise VoiceGeneratorError(
            f"路径扩展名 {suffix} 不支持（语音输出统一 {OUT_EXT}）")
    parent = os.path.dirname(os.path.abspath(p)) or "."
    os.makedirs(parent, exist_ok=True)
    real = os.path.realpath(p)
    roots = _allowed_roots()
    if not any(real == r or real.startswith(r + os.sep) for r in roots):
        raise VoiceGeneratorError(
            f"输出路径越界拒绝: {real}（允许根目录: {os.pathsep.join(roots)}，"
            f"可用 SOULMATE_VOICE_ROOTS 配置）")
    return Path(real)


def _run_in_thread(fn: Callable[[], Any], timeout: float) -> Any:
    """独立线程执行（edge-tts 异步 API 统一 asyncio.run）。

    宿主正处于事件循环内（ACP/stdio 常态）时同样安全——不碰宿主 loop。
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(fn).result(timeout=timeout)


def _default_edge_runner(text: str, voice: str, rate: str, timeout: float) -> bytes:
    """缺省 edge-tts 执行体：合成 mp3 字节流（异步 API 线程内 asyncio.run）。"""
    def _run() -> bytes:
        import edge_tts  # 延迟导入：依赖缺失在主路径显式报因

        async def _go() -> bytes:
            com = edge_tts.Communicate(text, voice, rate=rate)
            buf = bytearray()
            async for chunk in com.stream():
                if chunk.get("type") == "audio":
                    buf.extend(chunk.get("data") or b"")
            return bytes(buf)

        return asyncio.run(_go())

    try:
        return _run_in_thread(_run, timeout)
    except ImportError as e:
        raise VoiceGeneratorError(f"edge-tts 不可用: {e}")


def _default_fallback_runner(text: str, voice: str, espeak_speed: int,
                             timeout: float) -> bytes:
    """离线兜底执行体：espeak-ng 合成 wav + ffmpeg 转 mp3（同款输出格式）。"""
    espeak = shutil.which("espeak-ng") or shutil.which("espeak")
    if not espeak:
        raise VoiceGeneratorError("espeak-ng 不可用（离线兜底缺失）")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise VoiceGeneratorError("ffmpeg 不可用（兜底 wav→mp3 转换缺失）")
    lang = _ESPEAK_LANG_OF.get(voice.split("-")[0].lower(), "en-us")
    import tempfile
    with tempfile.TemporaryDirectory(prefix="soulmate_tts_") as td:
        wav = Path(td) / "tts.wav"
        mp3 = Path(td) / "tts.mp3"
        try:
            proc = subprocess.run(
                [espeak, "-v", lang, "-s", str(espeak_speed), "-w", str(wav), text],
                capture_output=True, text=True, errors="replace", timeout=timeout)
        except subprocess.TimeoutExpired:
            raise VoiceGeneratorError(f"espeak-ng 合成超时（>{timeout:.0f}s）")
        if proc.returncode != 0 or not wav.is_file() or wav.stat().st_size == 0:
            err = str(proc.stderr or "").strip()
            raise VoiceGeneratorError(
                f"espeak-ng 合成失败 rc={proc.returncode}: {err[-200:] or '(无stderr)'}")
        try:
            proc2 = subprocess.run(
                [ffmpeg, "-y", "-i", str(wav), "-codec:a", "libmp3lame",
                 "-qscale:a", "4", str(mp3)],
                capture_output=True, text=True, errors="replace", timeout=timeout)
        except subprocess.TimeoutExpired:
            raise VoiceGeneratorError(f"ffmpeg 转换超时（>{timeout:.0f}s）")
        if proc2.returncode != 0 or not mp3.is_file() or mp3.stat().st_size == 0:
            err = str(proc2.stderr or "").strip()
            raise VoiceGeneratorError(
                f"ffmpeg 转换失败 rc={proc2.returncode}: {err[-200:] or '(无stderr)'}")
        return mp3.read_bytes()


def _atomic_write(target: Path, data: bytes) -> None:
    """tmp + os.replace 原子落盘，不留半截文件。"""
    tmp = target.parent / f".{target.name}.{os.getpid()}.tmp"
    try:
        tmp.write_bytes(data)
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


class VoiceGenerator:
    """确定性语音合成器。生成方法返回用户可读结果文本（成功带 MEDIA 标签）。"""

    def __init__(
        self,
        edge_runner: Callable[..., bytes] | None = None,
        fallback_runner: Callable[..., bytes] | None = None,
        timeout: float | None = None,
    ):
        self._edge_runner = edge_runner or _default_edge_runner
        self._fallback_runner = fallback_runner or _default_fallback_runner
        self._timeout = float(timeout or TTS_TIMEOUT_S)

    # ------------------------------------------------------------------
    # 合成面
    # ------------------------------------------------------------------

    def synthesize(self, text: Any, path: Any = None, voice: Any = None,
                   rate: Any = None, engine: Any = "auto") -> str:
        """文本→mp3 语音，返回确定性文本结果（失败返回 "[错误] …" 文本）。

        engine: auto（默认：edge→失败换离线兜底）/ edge（只用 edge，失败不兜底）
        / espeak（只用离线兜底）。
        """
        try:
            body = str(text or "").strip()
            if not body:
                raise VoiceGeneratorError("text 不能为空")
            if len(body) > MAX_TEXT_CHARS:
                raise VoiceGeneratorError(
                    f"文本超限: {len(body)} 字 > {MAX_TEXT_CHARS}（SOULMATE_TTS_MAX_CHARS 可配）")
            eng = str(engine or "auto").strip().lower()
            if eng not in ("auto", "edge", "espeak"):
                raise VoiceGeneratorError(
                    f"engine 不支持: {engine!r}（可选: auto/edge/espeak）")
            v = _resolve_voice(voice)
            edge_rate, espeak_speed = _resolve_rate(rate)
            target = _resolve_target(path)

            edge_err = ""
            if eng in ("auto", "edge"):
                try:
                    data = self._edge_runner(body, v, edge_rate, self._timeout)
                    if not data:
                        raise VoiceGeneratorError("edge-tts 返回空音频")
                    used = "edge-tts"
                except Exception as e:  # 依赖缺失/网络失败进入兜底（auto）
                    edge_err = str(e)
                    if eng == "edge":
                        return f"[错误] 语音合成失败: edge-tts: {edge_err}"
                    data, used = None, ""
            else:
                data, used = None, ""

            if data is None:  # 兜底（auto 的 edge 失败 / engine=espeak）
                try:
                    data = self._fallback_runner(body, v, espeak_speed, self._timeout)
                    if not data:
                        raise VoiceGeneratorError("espeak-ng 兜底返回空音频")
                    used = "espeak-ng(离线兜底)" if eng == "auto" else "espeak-ng"
                except Exception as e:
                    fb_err = str(e)
                    if edge_err:
                        return (
                            f"[错误] 语音合成失败: edge-tts: {edge_err}；"
                            f"离线兜底: {fb_err}")
                    return f"[错误] 语音合成失败: {fb_err}"

            _atomic_write(target, data)
        except VoiceGeneratorError as e:
            return f"[错误] 语音合成失败: {e}"
        except Exception as e:  # IO 异常同样显式报因
            return f"[错误] 语音合成异常: {e}"
        size = target.stat().st_size
        return (
            f"✅ 语音已生成: {target}（{size} 字节，引擎: {used}）\n"
            f"MEDIA:{target}")

    def list_voices(self) -> str:
        """音色预设清单（确定性文本，供用户/模型选择）。"""
        lines = ["可用音色预设："]
        for k, v in VOICE_PRESETS.items():
            mark = "（默认）" if k == DEFAULT_VOICE else ""
            lines.append(f"- {k}: {v}{mark}")
        lines.append("也可传 *Neural 原始名（如 zh-CN-YunyangNeural）。")
        return "\n".join(lines)


# 模块级共享单例（与 get_doc_generator 同款模式）
_generator: VoiceGenerator | None = None


def get_voice_generator() -> VoiceGenerator:
    global _generator
    if _generator is None:
        _generator = VoiceGenerator()
    return _generator

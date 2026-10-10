"""tts_tools.py — 语音合成工具面（路线图 7.3：TTS/语音）。

TTSTools = 文本 → 真实音频文件（输出落盘 ~/.hermes/soulmate/audio/，返回 MEDIA: 路径
可直接下发播放），不走 LLM 猜测。双后端分层：
- 主后端 edge-tts（在线，音质好，中英日多语种神经语音）——独立子进程 worker
  （agent/tts_worker.py，服务 venv 实测带 edge_tts）+ 有界超时；
- 离线兜底 espeak-ng（系统实测 /usr/bin/espeak-ng 可用）——在线语音不可用
  （断网/超时/微软服务不可达）时降级本地合成，音质较机械但真实可播。

诚实状态语义（对标 browser_tools/docgen_tools 三态纪律，禁静默）：
- 在线合成失败但离线兜底成功 → 显式"⚠️ 在线语音不可用：<原因>"+"已用本地
  espeak-ng 离线合成（音质较机械）"，不假称 edge-tts 成功；
- 双后端全失败 → 显式列出两后端各自原因；
- 超时 → 显式"超时（上限 Ns）"；
- 参数非法（空 text/text 过长/非法 voice/rate/越界文件名）→ 显式拒绝+正确用法。
"""
import logging
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

logger = logging.getLogger("acp-agent.tts-tools")

DEFAULT_OUT_DIR = os.path.expanduser("~/.hermes/soulmate/audio")
DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"
MAX_TEXT_CHARS = 5000
EDGE_TIMEOUT_S = 60.0
ESPEAK_TIMEOUT_S = 20.0

# edge-tts voice 形如 zh-CN-XiaoxiaoNeural / en-US-AriaNeural（字母数字-）
_VOICE_RE = re.compile(r"^[A-Za-z]{2,3}-[A-Za-z0-9]+-[A-Za-z0-9]+$")
# edge-tts rate 形如 +0% / -10% / +20%
_RATE_RE = re.compile(r"^[+-]\d{1,3}%$")
_SAFE_NAME_RE = re.compile(r"^[\w\u4e00-\u9fff][\w\u4e00-\u9fff.\-]*$")

_TTS_HINT = (
    "安装方法：服务 venv 内 `pip install edge-tts`（acp-proxy 服务所用 interpreter）"
    "或系统安装 espeak-ng（如 `pacman -S espeak-ng`）。"
)


class TTSTools:
    """文本→语音合成（edge-tts 主后端 + espeak-ng 离线兜底），输出落盘音频目录。"""

    def __init__(self, out_dir: str = "", python: str = "", espeak: str = "",
                 edge_runner=None, espeak_runner=None, now=None):
        self.out_dir = Path(out_dir or DEFAULT_OUT_DIR)
        self.python = python or os.environ.get("SOULMATE_TTS_PYTHON") or sys.executable
        self.espeak = espeak or os.environ.get("SOULMATE_ESPEAK", "espeak-ng")
        # runner 均可注入（测试 fake）；缺省走真实子进程
        self._edge_runner = edge_runner or self._run_edge_worker
        self._espeak_runner = espeak_runner or self._run_espeak
        self._now = now or time.time

    # ── 入口 ─────────────────────────────────────────────────────────

    def run(self, params: dict) -> str:
        params = dict(params or {})
        text = str(params.get("text", "") or "").strip()
        if not text:
            return "❌ 参数错误: text 为空（需要要朗读的文本）"
        if len(text) > MAX_TEXT_CHARS:
            return (f"❌ 参数错误: text 过长（{len(text)} 字符，上限 {MAX_TEXT_CHARS}）"
                    f"——请分段合成")
        voice = str(params.get("voice", "") or "").strip() or DEFAULT_VOICE
        if not _VOICE_RE.match(voice):
            return (f"❌ 参数错误: voice 非法: {voice!r}"
                    f"（形如 zh-CN-XiaoxiaoNeural / en-US-AriaNeural）")
        rate = str(params.get("rate", "") or "").strip() or "+0%"
        if not _RATE_RE.match(rate):
            return f"❌ 参数错误: rate 格式应为 +/-N%（如 +0%/-10%/+20%），收到 {rate!r}"

        try:
            mp3_path = self._resolve_out_path("mp3", params)
        except ValueError as e:
            return f"❌ 参数错误: {e}"

        # ── 主后端：edge-tts（在线）──────────────────────────────────
        try:
            ok, detail = self._edge_runner(text, str(mp3_path), voice, rate)
        except subprocess.TimeoutExpired:
            ok, detail = False, f"超时（上限 {EDGE_TIMEOUT_S:g}s）"
        except Exception as e:  # noqa: BLE001 — 合成失败必须如实报因
            ok, detail = False, f"{type(e).__name__}: {e}"
        if ok and mp3_path.exists() and mp3_path.stat().st_size > 0:
            return self._success(mp3_path, f"edge-tts（voice={voice} rate={rate}）")
        edge_reason = detail or "未知原因"
        logger.warning("edge-tts failed: %s", edge_reason)

        # ── 离线兜底：espeak-ng（本地）───────────────────────────────
        wav_path = self._resolve_out_path("wav", params)
        try:
            ok2, detail2 = self._espeak_runner(str(wav_path), text, voice)
        except subprocess.TimeoutExpired:
            ok2, detail2 = False, f"超时（上限 {ESPEAK_TIMEOUT_S:g}s）"
        except Exception as e:  # noqa: BLE001
            ok2, detail2 = False, f"{type(e).__name__}: {e}"
        if ok2 and wav_path.exists() and wav_path.stat().st_size > 0:
            return (
                f"⚠️ 在线语音(edge-tts)不可用：{edge_reason}\n"
                f"已用本地 espeak-ng 离线合成（音质较机械）: {wav_path}"
                f"（{wav_path.stat().st_size} 字节）\n"
                f"MEDIA:{wav_path}"
            )
        espeak_reason = detail2 or "未知原因"
        return (
            f"❌ 语音合成失败（双后端均不可用）:\n"
            f"- edge-tts: {edge_reason}\n"
            f"- espeak-ng: {espeak_reason}\n"
            f"{_TTS_HINT}"
        )

    # ── 后端 runner ─────────────────────────────────────────────────

    def _run_edge_worker(self, text: str, out_path: str, voice: str, rate: str):
        """真实 edge-tts 子进程合成。返回 (ok, detail)。"""
        worker = Path(__file__).resolve().parent / "tts_worker.py"
        if not worker.exists():
            return False, f"worker 脚本不存在: {worker}"
        proc = subprocess.run(
            [self.python, str(worker), text, out_path, voice, rate],
            capture_output=True, text=True, errors="replace", timeout=EDGE_TIMEOUT_S,
        )
        parsed = _parse_last_json(proc.stdout or "")
        if parsed is not None and parsed.get("ok"):
            return True, ""
        if parsed is not None:
            return False, str(parsed.get("error") or "worker 报 ok=false 无原因")
        tail = (proc.stderr or proc.stdout or "").strip()[-300:]
        return False, f"worker 退出码 {proc.returncode}: {tail}"

    def _run_espeak(self, out_path: str, text: str, voice: str):
        """真实 espeak-ng 本地合成。返回 (ok, detail)。"""
        exe = self.espeak if Path(self.espeak).exists() else _which(self.espeak)
        if not exe:
            return False, f"espeak-ng 不存在: {self.espeak}"
        lang = _espeak_lang(voice)
        proc = subprocess.run(
            [exe, "-v", lang, "-s", "160", "-w", out_path, text],
            capture_output=True, text=True, errors="replace", timeout=ESPEAK_TIMEOUT_S,
        )
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "").strip()[-300:]
            return False, f"espeak-ng 退出码 {proc.returncode}: {tail}"
        return True, ""

    # ── 路径/输出 ────────────────────────────────────────────────────

    def _resolve_out_path(self, ext: str, params: dict) -> Path:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        name = str(params.get("filename", "") or "").strip()
        if name:
            # 统一剥掉旧后缀再按实际后端补后缀（在线 mp3 / 离线 wav）
            stem = name.rsplit(".", 1)[0] if "." in name else name
            name = f"{stem}.{ext}"
        else:
            name = f"tts-{int(self._now() * 1000)}.{ext}"
        if "/" in name or "\\" in name or not _SAFE_NAME_RE.match(name):
            raise ValueError(
                f"filename 非法: {name!r}（只允许文件名本身，不含路径分隔符）")
        out_path = (self.out_dir / name).resolve()
        if out_path.parent != self.out_dir.resolve():
            raise ValueError("输出路径越界（必须落在音频目录内）")
        return out_path

    @staticmethod
    def _success(out_path: Path, backend: str) -> str:
        size = out_path.stat().st_size if out_path.exists() else 0
        return (
            f"✅ 语音已合成: {out_path}（{size} 字节）\n"
            f"后端: {backend}\n"
            f"MEDIA:{out_path}"
        )


def _espeak_lang(voice: str) -> str:
    """edge-tts voice → espeak-ng 语言（确定性映射，不猜）。"""
    prefix = (voice or "").split("-", 1)[0].lower()
    return {"zh": "cmn", "ja": "ja", "en": "en", "ko": "ko"}.get(prefix, "en")


def _parse_last_json(stdout: str):
    for line in reversed((stdout or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                return json.loads(line)
            except ValueError:
                continue
    return None


def _which(cmd: str):
    for d in os.environ.get("PATH", "").split(os.pathsep):
        cand = Path(d) / cmd
        if cand.exists():
            return str(cand)
    return None

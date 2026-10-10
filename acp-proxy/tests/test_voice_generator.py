"""路线图 7.3 TTS/语音（文本→mp3）测试。

覆盖：校验面（空文本/超长/未知音色/语速非法/扩展名错配/越界拒绝）、
合成面（edge 主引擎 happy path、edge 失败自动换离线兜底、engine=edge 不兜底、
双失败报因叠加、engine=espeak 只走兜底、空音频视同失败）、
原子落盘不留半截文件、路径缺省命名与自动补扩展名、单例、
soulmate_agent 接线护栏 + __new__ 真实 dispatch 烟测。
真实 edge-tts/espeak-ng 合成属运行时烟测（需网络/系统二进制），不在本单内。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agent.voice_generator import (
    DEFAULT_VOICE,
    MAX_TEXT_CHARS,
    OUT_EXT,
    VOICE_PRESETS,
    VoiceGenerator,
    VoiceGeneratorError,
    _resolve_rate,
    _resolve_target,
    _resolve_voice,
    _run_in_thread,
    get_voice_generator,
)


class FakeProc:
    """可注入 runner 的统一签名：(*args) -> bytes，可记调用。"""

    def __init__(self, out: bytes = b"MP3DATA", exc: Exception | None = None):
        self.out = out
        self.exc = exc
        self.calls: list[tuple] = []

    def __call__(self, *args):
        self.calls.append(args)
        if self.exc is not None:
            raise self.exc
        return self.out


# ── 校验面 ─────────────────────────────────────────────────────


def test_empty_text_rejected(tmp_path):
    gen = VoiceGenerator(edge_runner=FakeProc(), fallback_runner=FakeProc())
    for bad in ("", "   ", None):
        out = gen.synthesize(bad, path=str(tmp_path / "a.mp3"))
        assert out.startswith("[错误]") and "text 不能为空" in out


def test_overlong_text_rejected():
    gen = VoiceGenerator(edge_runner=FakeProc(), fallback_runner=FakeProc())
    out = gen.synthesize("x" * (MAX_TEXT_CHARS + 1))
    assert out.startswith("[错误]") and "文本超限" in out


def test_unknown_voice_rejected_lists_presets():
    gen = VoiceGenerator(edge_runner=FakeProc(), fallback_runner=FakeProc())
    out = gen.synthesize("hi", voice="不存在的音色")
    assert out.startswith("[错误]") and "未知音色" in out and "xiaoxiao" in out


def test_voice_preset_and_raw_neural_resolve():
    assert _resolve_voice(None) == VOICE_PRESETS[DEFAULT_VOICE]
    assert _resolve_voice("YUNXI") == VOICE_PRESETS["yunxi"]  # 大小写不敏感
    assert _resolve_voice("zh-CN-YunyangNeural") == "zh-CN-YunyangNeural"  # 原始名透传
    with pytest.raises(VoiceGeneratorError):
        _resolve_voice("zh-CN-Yunyang")  # 非 Neural 非预设


def test_rate_validation_and_mapping():
    assert _resolve_rate(None) == ("+0%", 160)
    assert _resolve_rate("") == ("+0%", 160)
    assert _resolve_rate("20") == ("+20%", 192)
    assert _resolve_rate("-25%") == ("-25%", 120)
    for bad in ("abc", "200", -80):
        with pytest.raises(VoiceGeneratorError):
            _resolve_rate(bad)


def test_path_ext_mismatch_rejected():
    with pytest.raises(VoiceGeneratorError) as ei:
        _resolve_target(str(Path.home() / "tts.txt"))
    assert "不支持" in str(ei.value)


def test_path_missing_ext_auto_appended(tmp_path):
    p = _resolve_target(str(tmp_path / "voice_out"))
    assert p.name == f"voice_out{OUT_EXT}"


def test_path_default_naming_under_home():
    p = _resolve_target(None)
    assert p.name.startswith("tts_") and p.suffix == OUT_EXT
    assert str(p).startswith(str(Path.home()))
    assert p.parent.is_dir()  # 目录已备好，音频由合成落盘


def test_path_boundary_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv("SOULMATE_VOICE_ROOTS", str(tmp_path / "only"))
    with pytest.raises(VoiceGeneratorError) as ei:
        _resolve_target("/etc/tts.mp3")
    assert "越界拒绝" in str(ei.value)


# ── 合成面（注入 fake runner，零外部依赖）──────────────────────


def test_edge_happy_path(tmp_path):
    edge, fb = FakeProc(b"EDGE_MP3"), FakeProc()
    gen = VoiceGenerator(edge_runner=edge, fallback_runner=fb)
    out = gen.synthesize("你好世界", path=str(tmp_path / "hi.mp3"))
    assert "✅" in out and f"MEDIA:{tmp_path / 'hi.mp3'}" in out
    assert "edge-tts" in out and "兜底" not in out
    assert (tmp_path / "hi.mp3").read_bytes() == b"EDGE_MP3"
    assert len(edge.calls) == 1 and not fb.calls
    # runner 收到归一后的 voice/rate
    _text, voice, rate, _timeout = edge.calls[0]
    assert voice == VOICE_PRESETS[DEFAULT_VOICE] and rate == "+0%"


def test_edge_failure_auto_fallback(tmp_path):
    edge = FakeProc(exc=RuntimeError("网络不通"))
    fb = FakeProc(b"FB_MP3")
    gen = VoiceGenerator(edge_runner=edge, fallback_runner=fb)
    out = gen.synthesize("hi", path=str(tmp_path / "hi.mp3"))
    assert "✅" in out and "兜底" in out
    assert (tmp_path / "hi.mp3").read_bytes() == b"FB_MP3"
    assert len(edge.calls) == 1 and len(fb.calls) == 1
    # 兜底收到 espeak 语速（数值），不是 edge 的 '+N%'
    _t, _v, speed, _to = fb.calls[0]
    assert isinstance(speed, int)


def test_engine_edge_no_fallback(tmp_path):
    edge, fb = FakeProc(exc=RuntimeError("edge炸了")), FakeProc()
    gen = VoiceGenerator(edge_runner=edge, fallback_runner=fb)
    out = gen.synthesize("hi", path=str(tmp_path / "hi.mp3"), engine="edge")
    assert out.startswith("[错误]") and "edge炸了" in out
    assert not fb.calls and not (tmp_path / "hi.mp3").exists()


def test_both_fail_reports_both_causes(tmp_path):
    edge = FakeProc(exc=RuntimeError("edge炸了"))
    fb = FakeProc(exc=RuntimeError("espeak也炸了"))
    gen = VoiceGenerator(edge_runner=edge, fallback_runner=fb)
    out = gen.synthesize("hi", path=str(tmp_path / "hi.mp3"))
    assert out.startswith("[错误]") and "edge炸了" in out and "espeak也炸了" in out


def test_engine_espeak_only_fallback(tmp_path):
    edge, fb = FakeProc(), FakeProc(b"FB_MP3")
    gen = VoiceGenerator(edge_runner=edge, fallback_runner=fb)
    out = gen.synthesize("hi", path=str(tmp_path / "hi.mp3"), engine="espeak")
    assert "✅" in out and (tmp_path / "hi.mp3").read_bytes() == b"FB_MP3"
    assert not edge.calls and len(fb.calls) == 1
    assert "espeak-ng" in out


def test_engine_invalid_rejected():
    gen = VoiceGenerator(edge_runner=FakeProc(), fallback_runner=FakeProc())
    out = gen.synthesize("hi", engine="google")
    assert out.startswith("[错误]") and "engine 不支持" in out


def test_empty_audio_treated_as_failure(tmp_path):
    edge, fb = FakeProc(b""), FakeProc(exc=RuntimeError("兜底炸"))
    gen = VoiceGenerator(edge_runner=edge, fallback_runner=fb)
    out = gen.synthesize("hi", path=str(tmp_path / "hi.mp3"))
    assert out.startswith("[错误]") and "空音频" in out and "兜底炸" in out


def test_runner_exception_no_partial_file(tmp_path):
    edge = FakeProc(exc=RuntimeError("合成中炸"))
    gen = VoiceGenerator(edge_runner=edge, fallback_runner=FakeProc(exc=OSError("x")))
    target = tmp_path / "hi.mp3"
    out = gen.synthesize("hi", path=str(target))
    assert out.startswith("[错误]")
    assert not target.exists()
    assert not list(tmp_path.glob(".*tmp*"))  # 无半截残留


def test_synthesize_inside_running_event_loop(tmp_path):
    """宿主在事件循环内（ACP/stdio 常态）合成不炸宿主 loop。"""
    gen = VoiceGenerator(edge_runner=FakeProc(b"IN_LOOP"), fallback_runner=FakeProc())

    async def _go():
        return gen.synthesize("hi", path=str(tmp_path / "loop.mp3"))

    out = asyncio.run(_go())
    assert "✅" in out and (tmp_path / "loop.mp3").read_bytes() == b"IN_LOOP"


def test_run_in_thread_inside_loop():
    """_run_in_thread 线程内 asyncio.run：事件循环内同样安全。"""

    async def _go():
        def job():
            async def coro():
                return 42
            return asyncio.run(coro())
        return _run_in_thread(job, 5.0)

    assert asyncio.run(_go()) == 42


def test_atomic_write_failure_no_residue(tmp_path, monkeypatch):
    import agent.voice_generator as vg

    target = tmp_path / "x.mp3"

    def boom(t, data):
        raise OSError("磁盘炸了")

    monkeypatch.setattr(vg, "_atomic_write", boom)
    gen = VoiceGenerator(edge_runner=FakeProc(b"D"), fallback_runner=FakeProc())
    out = gen.synthesize("hi", path=str(target))
    assert out.startswith("[错误]") and "磁盘炸了" in out


def test_list_voices_lists_presets():
    gen = VoiceGenerator()
    text = gen.list_voices()
    for key in VOICE_PRESETS:
        assert key in text
    assert "Neural" in text


def test_singleton():
    assert get_voice_generator() is get_voice_generator()
    assert isinstance(get_voice_generator(), VoiceGenerator)


# ── soulmate_agent 接线护栏 + 真实 dispatch 烟测 ────────────────


def test_soulmate_agent_wiring_guards():
    src = Path(__file__).resolve().parent.parent.joinpath(
        "agent/soulmate_agent.py").read_text(encoding="utf-8")
    # 工具定义
    assert '"name": "text_to_speech"' in src
    # 两处dispatch点：主循环 + code-mode内层
    assert src.count('func_name == "text_to_speech"') >= 2
    # 分发helper + lazy-init + 顶部import
    assert "def _tts_dispatch" in src
    assert "_get_voice_generator" in src
    assert "from agent.voice_generator import" in src
    # 系统提示工具清单
    assert "text_to_speech: 语音合成" in src


def test_tts_dispatch_smoke(monkeypatch, tmp_path):
    """__new__构造+lazy-init真实dispatch烟测：成功/兜底/错误三态。"""
    import agent.soulmate_agent as sa

    edge, fb = FakeProc(b"EDGE"), FakeProc(b"FB")
    gen = VoiceGenerator(edge_runner=edge, fallback_runner=fb)
    monkeypatch.setattr(sa, "_get_voice_generator", lambda: gen)
    agent = sa.SoulMateAgent.__new__(sa.SoulMateAgent)  # 不过__init__

    out = agent._tts_dispatch({
        "text": "烟测语音",
        "path": str(tmp_path / "烟测.mp3"),
    })
    assert "✅" in out and "MEDIA:" in out and (tmp_path / "烟测.mp3").is_file()

    edge.exc = RuntimeError("edge炸")
    out = agent._tts_dispatch({
        "text": "兜底烟测",
        "path": str(tmp_path / "兜底.mp3"),
    })
    assert "✅" in out and "兜底" in out

    fb.exc = RuntimeError("全炸")
    out = agent._tts_dispatch({"text": "错误烟测", "path": str(tmp_path / "e.mp3")})
    assert out.startswith("[错误]")
    # lazy-init 已缓存实例
    assert getattr(agent, "_voice_generator", None) is gen


def test_tts_dispatch_prompt_fstring_builds():
    """系统提示是 f-string：text_to_speech 行构造不炸、花括号转义正确。"""
    import agent.soulmate_agent as sa
    agent = sa.SoulMateAgent.__new__(sa.SoulMateAgent)
    prompt = agent._build_base_system_prompt("/tmp")
    assert "text_to_speech: 语音合成" in prompt
    assert "{{" not in prompt  # 转义被正确还原

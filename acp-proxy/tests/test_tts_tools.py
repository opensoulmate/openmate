"""test_tts_tools.py — 路线图 7.3 语音合成工具面单测（确定性 fake runner，不打网络）。

覆盖诚实多态：成功/参数拒绝/在线失败离线兜底/双后端失败/超时/文件名白名单/
空文件不算成功/语音语言映射。
"""
import subprocess

import pytest

from agent.tts_tools import (
    DEFAULT_VOICE,
    MAX_TEXT_CHARS,
    TTSTools,
    _espeak_lang,
    _parse_last_json,
)


def _mk_tools(tmp_path, edge=None, espeak=None, now=None):
    return TTSTools(out_dir=str(tmp_path), edge_runner=edge, espeak_runner=espeak,
                    now=now or (lambda: 1700000000.0))


def _edge_ok(text, out, voice, rate):
    with open(out, "wb") as fp:
        fp.write(b"ID3fakemp3data")
    return True, ""


def _espeak_ok(out, text, voice):
    with open(out, "wb") as fp:
        fp.write(b"RIFFfakewavdata")
    return True, ""


class TestSuccess:
    def test_edge_success_media_line(self, tmp_path):
        tools = _mk_tools(tmp_path, edge=_edge_ok, espeak=_espeak_ok)
        result = tools.run({"text": "你好世界"})
        assert "✅ 语音已合成" in result
        assert "edge-tts" in result
        assert f"MEDIA:{tmp_path}" in result
        files = list(tmp_path.iterdir())
        assert len(files) == 1 and files[0].suffix == ".mp3"

    def test_custom_filename_kept(self, tmp_path):
        tools = _mk_tools(tmp_path, edge=_edge_ok, espeak=_espeak_ok)
        result = tools.run({"text": "hi", "filename": "我的语音.mp3"})
        assert "我的语音.mp3" in result
        assert (tmp_path / "我的语音.mp3").exists()

    def test_voice_rate_forwarded(self, tmp_path):
        seen = {}

        def edge(text, out, voice, rate):
            seen.update(voice=voice, rate=rate)
            return _edge_ok(text, out, voice, rate)

        tools = _mk_tools(tmp_path, edge=edge, espeak=_espeak_ok)
        tools.run({"text": "hi", "voice": "en-US-AriaNeural", "rate": "-10%"})
        assert seen == {"voice": "en-US-AriaNeural", "rate": "-10%"}

    def test_default_voice_rate(self, tmp_path):
        seen = {}

        def edge(text, out, voice, rate):
            seen.update(voice=voice, rate=rate)
            return _edge_ok(text, out, voice, rate)

        tools = _mk_tools(tmp_path, edge=edge, espeak=_espeak_ok)
        tools.run({"text": "hi"})
        assert seen == {"voice": DEFAULT_VOICE, "rate": "+0%"}


class TestParamReject:
    def test_empty_text(self, tmp_path):
        tools = _mk_tools(tmp_path, edge=_edge_ok, espeak=_espeak_ok)
        result = tools.run({"text": "   "})
        assert result.startswith("❌ 参数错误")
        assert "text 为空" in result

    def test_text_too_long(self, tmp_path):
        tools = _mk_tools(tmp_path, edge=_edge_ok, espeak=_espeak_ok)
        result = tools.run({"text": "啊" * (MAX_TEXT_CHARS + 1)})
        assert result.startswith("❌ 参数错误")
        assert "过长" in result

    def test_bad_voice(self, tmp_path):
        tools = _mk_tools(tmp_path, edge=_edge_ok, espeak=_espeak_ok)
        result = tools.run({"text": "hi", "voice": "bad voice; rm -rf /"})
        assert result.startswith("❌ 参数错误")
        assert "voice" in result

    def test_bad_rate(self, tmp_path):
        tools = _mk_tools(tmp_path, edge=_edge_ok, espeak=_espeak_ok)
        result = tools.run({"text": "hi", "rate": "fast"})
        assert result.startswith("❌ 参数错误")
        assert "rate" in result

    def test_filename_traversal_rejected(self, tmp_path):
        tools = _mk_tools(tmp_path, edge=_edge_ok, espeak=_espeak_ok)
        result = tools.run({"text": "hi", "filename": "../evil.mp3"})
        assert result.startswith("❌ 参数错误")
        assert not (tmp_path.parent / "evil.mp3").exists()

    def test_no_output_when_rejected(self, tmp_path):
        tools = _mk_tools(tmp_path, edge=_edge_ok, espeak=_espeak_ok)
        tools.run({"text": ""})
        assert list(tmp_path.iterdir()) == []


class TestFallback:
    def test_edge_fail_espeak_fallback(self, tmp_path):
        def edge(text, out, voice, rate):
            return False, "网络不可达"

        tools = _mk_tools(tmp_path, edge=edge, espeak=_espeak_ok)
        result = tools.run({"text": "你好"})
        assert "⚠️ 在线语音(edge-tts)不可用" in result
        assert "网络不可达" in result
        assert "espeak-ng 离线合成" in result
        assert "MEDIA:" in result
        wavs = [p for p in tmp_path.iterdir() if p.suffix == ".wav"]
        assert len(wavs) == 1

    def test_edge_timeout_espeak_fallback(self, tmp_path):
        def edge(text, out, voice, rate):
            raise subprocess.TimeoutExpired(cmd="tts_worker", timeout=60)

        tools = _mk_tools(tmp_path, edge=edge, espeak=_espeak_ok)
        result = tools.run({"text": "你好"})
        assert "⚠️ 在线语音(edge-tts)不可用" in result
        assert "超时" in result
        assert "MEDIA:" in result

    def test_both_fail_explicit_reasons(self, tmp_path):
        def edge(text, out, voice, rate):
            return False, "edge挂了"

        def espeak(out, text, voice):
            return False, "espeak挂了"

        tools = _mk_tools(tmp_path, edge=edge, espeak=espeak)
        result = tools.run({"text": "你好"})
        assert result.startswith("❌ 语音合成失败")
        assert "edge挂了" in result and "espeak挂了" in result
        assert list(tmp_path.iterdir()) == []

    def test_zero_byte_file_not_success(self, tmp_path):
        def edge(text, out, voice, rate):
            open(out, "wb").close()  # 0 字节
            return True, ""

        def espeak(out, text, voice):
            return False, "espeak挂了"

        tools = _mk_tools(tmp_path, edge=edge, espeak=espeak)
        result = tools.run({"text": "你好"})
        assert result.startswith("❌ 语音合成失败")

    def test_espeak_timeout_explicit(self, tmp_path):
        def edge(text, out, voice, rate):
            return False, "edge挂了"

        def espeak(out, text, voice):
            raise subprocess.TimeoutExpired(cmd="espeak", timeout=20)

        tools = _mk_tools(tmp_path, edge=edge, espeak=espeak)
        result = tools.run({"text": "你好"})
        assert result.startswith("❌ 语音合成失败")
        assert "超时" in result

    def test_fallback_wav_swaps_extension(self, tmp_path):
        def edge(text, out, voice, rate):
            return False, "edge挂了"

        tools = _mk_tools(tmp_path, edge=edge, espeak=_espeak_ok)
        result = tools.run({"text": "hi", "filename": "播报.mp3"})
        # 在线失败→离线 wav：同名去 mp3 换 wav，不落空壳 mp3
        assert (tmp_path / "播报.wav").exists()
        assert not (tmp_path / "播报.mp3").exists()
        assert "播报.wav" in result


class TestHelpers:
    @pytest.mark.parametrize("voice,expected", [
        ("zh-CN-XiaoxiaoNeural", "cmn"),
        ("en-US-AriaNeural", "en"),
        ("ja-JP-NanamiNeural", "ja"),
        ("ko-KR-SunHiNeural", "ko"),
    ])
    def test_espeak_lang_mapping(self, voice, expected):
        assert _espeak_lang(voice) == expected

    def test_parse_last_json_takes_tail(self):
        stdout = 'noise\n{"ok": false, "error": "boom"}\n'
        assert _parse_last_json(stdout) == {"ok": False, "error": "boom"}

    def test_parse_last_json_none_on_garbage(self):
        assert _parse_last_json("garbage only") is None

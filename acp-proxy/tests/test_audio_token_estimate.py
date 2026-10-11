"""19.1 音频/多模态 token 估算测试（17.1 诚实边界③销账）。

覆盖：WAV 头时长→秒×6.25 精确折算、非 WAV 字节保守下界、data URL 前缀容噪、
块语义估算（text/input_audio/image/未知块不计 blob 载荷）、str 回归不变、
双消费点接线（context_budget._calibrated 与归因循环同款 helper）。
"""
from __future__ import annotations

import base64

from agent.token_attribution import (
    AUDIO_TOKENS_PER_SECOND,
    IMAGE_TOKENS_FALLBACK,
    estimate_audio_tokens,
    estimate_content_tokens,
    estimate_tokens,
)


def _make_wav(total_seconds: float, byte_rate: int = 32000) -> bytes:
    """构造最小 WAV 字节（RIFF/WAVE + 标准 44 字节头，data 填零）。"""
    data_size = int(total_seconds * byte_rate)
    header = bytearray(44)
    header[0:4] = b"RIFF"
    header[4:8] = (36 + data_size).to_bytes(4, "little")
    header[8:12] = b"WAVE"
    header[12:16] = b"fmt "
    header[16:20] = (16).to_bytes(4, "little")
    header[20:22] = (1).to_bytes(2, "little")  # PCM
    header[22:24] = (1).to_bytes(2, "little")  # mono
    header[24:28] = (16000).to_bytes(4, "little")  # sample rate
    header[28:32] = byte_rate.to_bytes(4, "little")
    header[32:34] = (2).to_bytes(4, "little")[:2]  # block align
    header[34:36] = (16).to_bytes(2, "little")  # bits per sample
    header[36:40] = b"data"
    header[40:44] = data_size.to_bytes(4, "little")
    return bytes(header) + b"\x00" * data_size


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


# ── estimate_audio_tokens ──────────────────────────────────────────

class TestEstimateAudioTokens:
    def test_wav_header_duration_exact(self):
        # 2.88s × 32000 B/s（17.1 真实样本口径），期望 token=秒×6.25
        raw = _make_wav(2.88)
        tok = estimate_audio_tokens(_b64(raw))
        assert tok == int(2.88 * AUDIO_TOKENS_PER_SECOND)  # = 18

    def test_wav_longer_recording(self):
        raw = _make_wav(60.0)
        tok = estimate_audio_tokens(_b64(raw))
        assert tok == int(60.0 * AUDIO_TOKENS_PER_SECOND)  # = 375

    def test_data_url_prefix_tolerated(self):
        raw = _make_wav(2.88)
        tok = estimate_audio_tokens("data:audio/wav;base64," + _b64(raw))
        assert tok == int(2.88 * AUDIO_TOKENS_PER_SECOND)

    def test_non_wav_falls_back_to_byte_lower_bound(self):
        # mp3 形态：无 RIFF 头 → 字节/32000 秒×6.25（保守下界口径）
        payload = base64.b64encode(b"\xff\xfb" + b"\x00" * 32000).decode()
        tok = estimate_audio_tokens(payload)
        expected = int((32002 / 32000) * AUDIO_TOKENS_PER_SECOND)
        assert tok == expected

    def test_empty_payload_zero(self):
        assert estimate_audio_tokens("") == 0
        assert estimate_audio_tokens("data:audio/wav;base64,") == 0

    def test_corrupt_base64_falls_back_not_crash(self):
        # 解码失败/非 WAV 必须走字节下界，绝不抛异常
        s = "!!!!not-base64!!!!" * 100
        tok = estimate_audio_tokens(s)
        expected = int((((len(s) * 3) // 4) / 32000) * AUDIO_TOKENS_PER_SECOND)
        assert tok == expected


# ── estimate_content_tokens（块语义）────────────────────────────────

class TestEstimateContentTokens:
    def test_str_regression_same_as_estimate_tokens(self):
        s = "上下文归因" * 20
        assert estimate_content_tokens(s) == estimate_tokens(s)
        assert estimate_content_tokens(None) == 0
        assert estimate_content_tokens("") == 0

    def test_list_text_and_audio(self):
        raw = _make_wav(2.88)
        content = [
            {"type": "text", "text": "复述你听到的内容"},
            {"type": "input_audio", "input_audio": {"data": _b64(raw), "format": "wav"}},
        ]
        expected = estimate_tokens("复述你听到的内容") + int(2.88 * AUDIO_TOKENS_PER_SECOND)
        assert estimate_content_tokens(content) == expected

    def test_blob_never_counted_as_text(self):
        # 旧 str(list) 形态把整段 base64 当文本计（0.4/字符）→ 数万 token；
        # 新形态音频只按秒折算，估算必须远小于 str 形态
        raw = _make_wav(2.88)
        content = [
            {"type": "text", "text": "ok"},
            {"type": "input_audio", "input_audio": {"data": _b64(raw), "format": "wav"}},
        ]
        new_tok = estimate_content_tokens(content)
        old_tok = estimate_tokens(str(content))
        assert new_tok < old_tok / 100
        assert new_tok < 100

    def test_image_block_constant_not_blob(self):
        big_b64 = "A" * 400000  # ~300KB 载荷
        content = [{"type": "image", "source": {"type": "base64", "data": big_b64}}]
        assert estimate_content_tokens(content) == IMAGE_TOKENS_FALLBACK

    def test_unknown_block_counts_metadata_not_blob(self):
        content = [{"type": "resource", "resource": {"uri": "file:///x", "blob": "A" * 100000}}]
        tok = estimate_content_tokens(content)
        assert tok < 200  # 只计元数据文本

    def test_sdk_object_blocks(self):
        class FakeAudioBlock:
            type = "input_audio"
            data = _b64(_make_wav(2.88))

        class FakeTextBlock:
            type = "text"
            text = "你好"

        content = [FakeTextBlock(), FakeAudioBlock()]
        expected = estimate_tokens("你好") + int(2.88 * AUDIO_TOKENS_PER_SECOND)
        assert estimate_content_tokens(content) == expected


# ── 双消费点接线 ───────────────────────────────────────────────────

class TestConsumersWired:
    def test_context_budget_calibrated_uses_block_semantics(self):
        from agent.context_budget import ContextBudgetManager

        mgr = ContextBudgetManager()
        mgr.calibration_factor = 1.0
        raw = _make_wav(2.88)
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": [
                {"type": "text", "text": "复述你听到的内容"},
                {"type": "input_audio", "input_audio": {"data": _b64(raw), "format": "wav"}},
            ]},
        ] * 11  # >20 条触发 manage
        trimmed = mgr.manage(messages, max_tokens=100000)
        assert trimmed  # 正常返回不炸
        # 块语义估算下 22 条消息总估算远小于 blob 虚高形态 → 不因音频 blob 被误裁
        assert len(trimmed) == len(messages)

    def test_attribution_import_wired(self):
        # 归因循环消费同一 helper（写了≠接线了：import 面真实存在）
        import inspect

        import agent.soulmate_agent as sa
        src = inspect.getsource(sa.SoulMateAgent)
        assert "estimate_content_tokens(_m.get(\"content\"))" in src
        assert sa.estimate_content_tokens is estimate_content_tokens

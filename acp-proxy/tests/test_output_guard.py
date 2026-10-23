"""P0 输出侧护栏：流式密钥脱敏（Warp secret_redaction 20正则移植）测试

覆盖：
1. 20正则逐字移植的样例命中（credential/identifier两级）
2. 全量脱敏 redact_secrets（marker/防重复/findings无秘密本体）
3. 流式 SecretStreamRedactor（chunk劈开秘密·逐切点穷举·runaway token·flush）
4. 防死接线（inspect断言 _run_llm_with_tools 真实消息路径的feed/flush调用点）
5. 真实 _run_llm_with_tools 路径E2E（FakeLLM脚本化流→fake client捕获外发文本）
"""

import inspect
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.output_guard import (  # noqa: E402
    MAX_HOLDBACK,
    OVERLAP,
    SecretStreamRedactor,
    redact_secrets,
)
import agent.soulmate_agent as sma  # noqa: E402
from agent.soulmate_agent import SoulMateAgent  # noqa: E402
from tests.test_steering import FakeLLM, make_agent, run  # noqa: E402

# ── 样例秘密（格式合法但纯属伪造，绝不对应任何真实凭据）──
OPENAI_KEY = "sk-" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8s9T0u1V2w3X4"  # 48位
GENERIC_SK = "sk-Abcdefghij-KLMNOP"
ANTHROPIC_KEY = "sk-ant-api02-" + "a" * 90
GH_CLASSIC = "ghp_" + "A" * 36
GH_FINE = "github_pat_" + "B" * 82
GH_OAUTH = "gho_" + "C" * 36
GH_US2S = "ghu_" + "D" * 36
GH_S2S = "ghs_" + "E" * 36
AWS_ID = "AKIA" + "A1B2C3D4E5F6G7H8"
SLACK_TOKEN = "xapp-1-A1B2C3-2-deadbeefcafe"
STRIPE_KEY = "sk_live_" + "a1B2c3D4e5F6g7H8i9J0k1L2"
GOOGLE_KEY = "AIza" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6qRs"  # 35位
FIREWORKS_KEY = "fw_" + "a1B2c3D4e5F6g7H8i9J0k1L2"
WARP_KEY = "wk-1.abcdef0123456789"
JWT_TOKEN = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NSJ9.abcdefghijABCDEFGHIJ"

CRED_SAMPLES = [
    (OPENAI_KEY, "OpenAI API Key"),
    (GENERIC_SK, "Generic SK API Key"),
    (ANTHROPIC_KEY, "Anthropic API Key"),
    (GH_CLASSIC, "GitHub Classic Personal Access Token"),
    (GH_FINE, "GitHub Fine-Grained Personal Access Token"),
    (GH_OAUTH, "GitHub OAuth Access Token"),
    (GH_US2S, "GitHub User-to-Server Token"),
    (GH_S2S, "GitHub Server-to-Server Token"),
    (AWS_ID, "AWS Access ID"),
    (SLACK_TOKEN, "Slack App Token"),
    (STRIPE_KEY, "Stripe Key"),
    (GOOGLE_KEY, "Google API Key"),
    (FIREWORKS_KEY, "Fireworks API Key"),
    (WARP_KEY, "Warp API Key"),
    (JWT_TOKEN, "JWT"),
]


class FakeClient:
    """捕获 session_update 外发文本（AgentMessageChunk.content.text）"""

    def __init__(self):
        self.texts = []

    async def session_update(self, session_id, update):
        try:
            self.texts.append(update.content.text)
        except Exception:
            self.texts.append(str(update))

# ════════════════════════════════════════════════════════════════
# 1. 模式覆盖 — Warp 20正则移植样例
# ════════════════════════════════════════════════════════════════

class TestPatternCoverage:
    def test_credential_samples_masked(self):
        for secret, kind in CRED_SAMPLES:
            out, findings = redact_secrets(f"leak: {secret} end")
            assert secret not in out, f"{kind} 未被脱敏"
            assert f"[REDACTED:{kind}]" in out, f"{kind} marker缺失: {out}"
            assert findings and findings[0]["kind"] == kind

    def test_anthropic_wins_over_generic(self):
        """specific先于generic（Warp原序）：sk-ant-…归因到Anthropic而非Generic"""
        _, findings = redact_secrets(ANTHROPIC_KEY)
        assert findings[0]["kind"] == "Anthropic API Key"

    def test_identifier_tier_off_by_default(self):
        """适配披露：默认credential级不遮IP/端口——开发输出大量合法127.0.0.1"""
        text = "健康检查: curl http://127.0.0.1:8090/health 返回200"
        out, _ = redact_secrets(text)
        assert out == text  # 默认不遮
        out2, f2 = redact_secrets(text, level="identifier")
        assert "127.0.0.1" not in out2 and f2

    def test_identifier_samples_masked_when_enabled(self):
        for secret, kind in [
            ("127.0.0.1", "IPv4 Address"),
            ("aa:bb:cc:dd:ee:ff", "MAC Address"),
            ("555-123-4567", "Phone Number"),
        ]:
            out, findings = redact_secrets(f"->{secret}<-", level="identifier")
            assert secret not in out and findings[0]["kind"] == kind

    def test_off_level_passthrough(self):
        out, findings = redact_secrets(OPENAI_KEY, level="off")
        assert out == OPENAI_KEY and findings == []


# ════════════════════════════════════════════════════════════════
# 2. 全量脱敏
# ════════════════════════════════════════════════════════════════

class TestFullTextRedaction:
    def test_findings_never_contain_secret(self):
        """findings只含种类/级别/长度——秘密本体绝不进审计记录"""
        _, findings = redact_secrets(f"x {OPENAI_KEY} y {GH_CLASSIC} z")
        for f in findings:
            for secret, _kind in CRED_SAMPLES:
                assert secret[:8] not in json.dumps(f, ensure_ascii=False)

    def test_multiple_secrets_all_masked(self):
        out, findings = redact_secrets(f"{OPENAI_KEY} {AWS_ID} {JWT_TOKEN}")
        for secret, _ in CRED_SAMPLES:
            assert secret not in out
        assert len(findings) == 3

    def test_plain_text_untouched(self):
        text = "这是普通中文回答，包含代码 `pip install foo` 与数字 42。"
        out, findings = redact_secrets(text)
        assert out == text and findings == []

    def test_none_input(self):
        assert redact_secrets(None) == ("", [])

    def test_no_duplicate_findings_on_overlap(self):
        """重叠区域只脱敏一次（Warp retain防重复条目）"""
        out, findings = redact_secrets(ANTHROPIC_KEY)
        assert out.count("[REDACTED:") == 1 and len(findings) == 1


# ════════════════════════════════════════════════════════════════
# 3. 流式脱敏（Warp三缓冲同构）
# ════════════════════════════════════════════════════════════════

class TestStreamRedactor:
    def test_chunk_split_at_every_position(self, ):
        """秘密被chunk从任意字节劈开：输出流里永远不出现完整秘密、
        也不出现≥6字符的秘密碎片（前半段一直压在holdback里）"""
        whole = f"前缀文本 {OPENAI_KEY} 后缀文本"
        expected, _ = redact_secrets(whole)
        for i in range(len(whole) + 1):
            r = SecretStreamRedactor()
            out = r.feed(whole[:i]) + r.feed(whole[i:]) + r.flush()
            assert OPENAI_KEY not in out, f"split@{i} 泄漏完整秘密"
            for j in range(len(OPENAI_KEY) - 5):
                frag = OPENAI_KEY[j:j + 6]
                assert frag not in out, f"split@{i} 泄漏碎片 {frag}"
            assert out == expected, f"split@{i} 输出不一致: {out!r}"

    def test_two_chunk_normal_text_identity(self):
        r = SecretStreamRedactor()
        assert r.feed("hello ") == "hello "  # 有空白边界即刻外发
        assert r.feed("world") == ""  # 尾部token压住
        assert r.flush() == "world"

    def test_flush_required_for_tail(self):
        r = SecretStreamRedactor()
        assert r.feed("tail-without-space") == ""
        assert r.flush() == "tail-without-space"

    def test_runaway_token_force_cut(self):
        """超长单token（>MAX_HOLDBACK）强制切分：秘密完整仍被捕获、进度不卡死"""
        secret = OPENAI_KEY
        token = "A" * (MAX_HOLDBACK + 200) + "=" + secret + "=" + "B" * (MAX_HOLDBACK + 200)
        r = SecretStreamRedactor()
        out = ""
        for i in range(0, len(token), 100):
            out += r.feed(token[i:i + 100])
        out += r.flush()
        assert secret not in out
        for j in range(len(secret) - 5):
            assert secret[j:j + 6] not in out, f"碎片泄漏: {secret[j:j+6]}"
        assert len(out) > MAX_HOLDBACK  # 不是把整个token都吞了
        assert "[REDACTED:" in out
        assert r.findings and r.findings[0]["kind"] == "OpenAI API Key"

    def test_force_cut_keeps_overlap_for_partial_secret(self):
        """强制切分点之后的未完结秘密头部（≤MAX_SECRET_LEN）完整留在holdback里"""
        prefix = "Z" * (MAX_HOLDBACK + 300) + "="
        partial = OPENAI_KEY[:30]  # 只到了30字符
        r = SecretStreamRedactor()
        out = r.feed(prefix + partial)  # 触发强制切分
        assert OPENAI_KEY[:20] not in out  # 秘密头部没有被外发
        out += r.feed(OPENAI_KEY[30:] + " end")  # 补全
        out += r.flush()
        assert OPENAI_KEY not in out
        for j in range(len(OPENAI_KEY) - 5):
            assert OPENAI_KEY[j:j + 6] not in out
        assert "[REDACTED:" in out

    def test_force_cut_no_false_b_match_at_boundary(self):
        """强制切分区末尾的假\\b命中不得提前判完整（否则后半段碎片外发）：
        切分点恰好落在秘密中段时，秘密整体留在holdback、最终整体替换"""
        secret = OPENAI_KEY
        token = "Q" * (MAX_HOLDBACK + 100) + "=" + secret + "=" + "W" * (MAX_HOLDBACK + 100)
        for cut_at in range(20, 60, 4):
            r = SecretStreamRedactor()
            out = r.feed(token[:cut_at])
            out += r.feed(token[cut_at:])
            out += r.flush()
            assert secret not in out, f"cut@{cut_at} 泄漏完整秘密"
            for j in range(len(secret) - 5):
                assert secret[j:j + 6] not in out, f"cut@{cut_at} 泄漏碎片"
            assert "[REDACTED:" in out

    def test_word_boundary_semantics_faithful_to_warp(self):
        """Warp \\b语义逐字保留：秘密与词字符直接粘连（ZZZsk-…）不命中——上游
        15个模式全部带\\b，真实场景秘密前是 =/:/空格 等非词字符。显式记录此边界，
        防将来误"修"成过度匹配。"""
        glued = "ZZZ" + OPENAI_KEY
        out, findings = redact_secrets(glued)
        assert out == glued and findings == []
        separated = "ZZZ=" + OPENAI_KEY
        out2, f2 = redact_secrets(separated)
        assert OPENAI_KEY not in out2 and f2

    def test_summary_no_secret_body(self):
        r = SecretStreamRedactor()
        r.feed(f"{OPENAI_KEY} {AWS_ID}")
        r.flush()
        s = r.summary()
        assert "OpenAI API Key" in s and "AWS Access ID" in s
        assert OPENAI_KEY[:8] not in s and AWS_ID[:8] not in s

    def test_off_level_stream_passthrough(self):
        r = SecretStreamRedactor(level="off")
        assert r.feed(OPENAI_KEY) == OPENAI_KEY
        assert r.flush() == ""


# ════════════════════════════════════════════════════════════════
# 4. 防死接线（inspect断言调用点真实存在于 _run_llm_with_tools）
# ════════════════════════════════════════════════════════════════

class TestWiring:
    def test_import_wired(self):
        assert sma.SecretStreamRedactor is SecretStreamRedactor
        assert sma.redact_secrets is redact_secrets

    def test_stream_feed_flush_callsites(self):
        src = inspect.getsource(SoulMateAgent._run_llm_with_tools)
        # 纯文本chunk路径 + 自评轮执行旁白路由的正文余量（evo目标 2026-10-23：
        # 两处外发都必须走 redactor.feed 脱敏——旁白路由不给裸文本开口子）
        assert src.count("redactor.feed(") == 2
        # 终局return + FORCE_STOP早退 + 注入审计TERMINATE早退 + round边界收口
        # （round=本轮LLM流的生命周期终点：不flush则CJK无空白尾部token压在
        # holdback里跨轮串扰、round span内容为空——round级span嵌套轮新增）
        assert src.count("redactor.flush()") == 4
        assert "SecretStreamRedactor()" in src
        # full_response只累加脱敏后文本（落库/记忆/缓存一并干净）
        assert "full_response += safe_text" in src
        assert "full_response += chunk_text" not in src

    def test_preview_and_cache_callsites(self):
        src = inspect.getsource(SoulMateAgent._run_llm_with_tools)
        assert src.count('redact_secrets(str(result))[0][:200]') == 2  # 两处result_preview
        ps_src = inspect.getsource(SoulMateAgent._prompt_inner)
        # 语义缓存重放脱敏：展示+落库统一走 _cache_replay（内部 redact_secrets(cached)[0]），
        # 命中分支必须调用它——防止退回"只推客户端不脱敏/不落库"的静默丢回复
        assert "self._cache_replay(" in ps_src
        cr_src = inspect.getsource(SoulMateAgent._cache_replay)
        assert "redact_secrets(cached)[0]" in cr_src


# ════════════════════════════════════════════════════════════════
# 5. 真实 _run_llm_with_tools 路径E2E（脚本化流→fake client捕获外发）
# ════════════════════════════════════════════════════════════════

class TestRunLlmStreamRedaction:
    def _run(self, tmp_path, chunks):
        agent = make_agent(tmp_path, acquired=True, scripts=[chunks])
        client = FakeClient()
        agent._client = client
        return agent, client

    def test_streamed_secret_redacted_to_client(self, tmp_path):
        """决定性：真实流式路径外发文本中完整秘密不存在、marker可见"""
        secret = OPENAI_KEY
        chunks = ["这是回答开头。", f"密钥是 {secret[:10]}", f"{secret[10:]} 请勿外传。"]
        agent, client = self._run(tmp_path, chunks)
        resp, _ = run(agent._run_llm_with_tools(
            messages=[{"role": "user", "content": "输出密钥"}], session_id="s1"))
        emitted = "".join(client.texts)
        assert secret not in emitted, f"外发文本泄漏完整秘密: {emitted!r}"
        for j in range(len(secret) - 5):
            assert secret[j:j + 6] not in emitted, f"外发文本泄漏碎片: {emitted!r}"
        assert "[REDACTED:OpenAI API Key]" in emitted
        assert secret not in resp and "[REDACTED:" in resp  # full_response同样干净

    def test_no_secret_stream_without_flush_loss(self, tmp_path):
        """尾部token（无空白结尾）不因holdback永久丢失——flush后完整可见"""
        agent, client = self._run(tmp_path, ["回答内容尾部token"])
        resp, _ = run(agent._run_llm_with_tools(
            messages=[{"role": "user", "content": "hi"}], session_id="s1"))
        assert "回答内容尾部token" in resp
        assert "回答内容尾部token" in "".join(client.texts)

    def test_clean_text_passes_unchanged(self, tmp_path):
        """负控制：普通回答零变化（脱敏不改写正常输出）"""
        agent, client = self._run(tmp_path, ["正常回答第一段。", "正常回答第二段。"])
        resp, _ = run(agent._run_llm_with_tools(
            messages=[{"role": "user", "content": "hi"}], session_id="s1"))
        assert resp == "正常回答第一段。正常回答第二段。"
        assert "".join(client.texts) == resp
        assert "[REDACTED" not in resp

    def test_tool_result_preview_redacted(self, tmp_path):
        """工具结果preview（UI展示边界）先脱敏后截断"""
        tc = {"tool_calls": [{
            "id": "t1",
            "function": {"name": "read_file", "arguments": json.dumps({"path": "/tmp/x"})},
        }]}
        agent = make_agent(tmp_path, acquired=True, scripts=[[tc], ["完成"]])
        agent._client = FakeClient()

        class _Deny:
            behavior = "deny"

            def __call__(self, *a, **kw):
                return _Deny()

        # gate deny路径的合成结果不含秘密，这里直接验证preview函数链：
        out, _ = redact_secrets(f"工具输出 {GH_CLASSIC} 尾")
        assert GH_CLASSIC not in out
        src = inspect.getsource(SoulMateAgent._run_llm_with_tools)
        assert src.count("redact_secrets(str(result))[0][:200]") == 2

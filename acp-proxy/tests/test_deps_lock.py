# -*- coding: utf-8 -*-
"""Pi 取长补短 #15——依赖锁版本防御 + 供应商错误文案兼容层。

- deps_lock：锁文件精确锁纪律（只许 ==）、verify 漂移/缺失/违规三态可见、
  require 可选依赖校验；
- error_codes.vendor_compat：供应商错误边界归一一次 → 结构化码 + retryable
  判据（重试决策用码不用文案正则——抄 piRetryErrorNormalizer 功能不抄机制）。

通用工程纪律能力（非投标专用）。
"""
import textwrap

import pytest

from agent import deps_lock
from error_codes import ErrorCode, is_retryable
from error_codes import vendor_compat


# ── deps_lock 锁面 ────────────────────────────────────────────────

GOOD_LOCK = textwrap.dedent("""\
    # 注释行
    # ── [core] 直接依赖 ──
    httpx==0.28.1
    pydantic==2.14.1  # 带行内注释
    # ── [optional] 可选 ──
    playwright==1.60.0
    """)


class TestParseLock:
    def test_exact_pins_parsed_by_group(self):
        spec = deps_lock.parse_lock(GOOD_LOCK)
        assert spec.pins["core"] == {"httpx": "0.28.1", "pydantic": "2.14.1"}
        assert spec.pins["optional"] == {"playwright": "1.60.0"}
        assert spec.invalid == []

    def test_non_exact_pin_is_invalid(self):
        spec = deps_lock.parse_lock("httpx>=0.28\npydantic~=2.14\nfoo==1.0\n")
        assert {i["line"] for i in spec.invalid} == {1, 2}
        assert spec.pins["core"] == {"foo": "1.0"}

    def test_bare_name_is_invalid(self):
        spec = deps_lock.parse_lock("httpx\n")
        assert spec.invalid and spec.invalid[0]["line"] == 1


class TestVerify:
    def test_drift_and_missing_reported(self, tmp_path, monkeypatch):
        lock = tmp_path / "requirements.lock.txt"
        lock.write_text("httpx==0.0.1\nno-such-dist-xyz==1.0\n", encoding="utf-8")

        def fake_version(name):
            if name == "httpx":
                return "0.28.1"      # 与锁 0.0.1 不一致 → drift
            raise deps_lock._im.PackageNotFoundError(name)

        monkeypatch.setattr(deps_lock, "_im", _FakeIM(fake_version))
        rep = deps_lock.verify(lock)
        assert rep["ok"] is False
        assert rep["drifted"] == [{"name": "httpx", "pinned": "0.0.1",
                                   "installed": "0.28.1", "group": "core"}]
        assert rep["missing"][0]["name"] == "no-such-dist-xyz"

    def test_match_is_ok(self, tmp_path, monkeypatch):
        lock = tmp_path / "requirements.lock.txt"
        lock.write_text("httpx==0.28.1\n", encoding="utf-8")
        monkeypatch.setattr(deps_lock, "_im",
                            _FakeIM(lambda n: "0.28.1" if n == "httpx" else _no(n)))
        rep = deps_lock.verify(lock)
        assert rep["ok"] is True and rep["checked"] == 1

    def test_optional_group_excluded_by_default(self, tmp_path, monkeypatch):
        lock = tmp_path / "requirements.lock.txt"
        lock.write_text("# ── [core] ──\nhttpx==0.28.1\n# ── [optional] ──\nplaywright==1.60.0\n",
                        encoding="utf-8")
        monkeypatch.setattr(deps_lock, "_im",
                            _FakeIM(lambda n: "0.28.1" if n == "httpx" else _no(n)))
        assert deps_lock.verify(lock)["ok"] is True
        rep = deps_lock.verify(lock, include_optional=True)
        assert rep["ok"] is False and rep["missing"][0]["name"] == "playwright"

    def test_invalid_pin_makes_verify_not_ok(self, tmp_path, monkeypatch):
        lock = tmp_path / "requirements.lock.txt"
        lock.write_text("httpx==0.28.1\npydantic>=2\n", encoding="utf-8")
        monkeypatch.setattr(deps_lock, "_im",
                            _FakeIM(lambda n: "0.28.1" if n == "httpx" else _no(n)))
        rep = deps_lock.verify(lock)
        assert rep["ok"] is False and rep["invalid_pins"]

    def test_missing_lock_file_visible(self, tmp_path):
        rep = deps_lock.verify(tmp_path / "absent.lock.txt")
        assert rep["ok"] is False and rep["error"]

    def test_real_lock_file_matches_runtime(self):
        """真实验证：acp-proxy/requirements.lock.txt 与运行 interpreter 一致。"""
        rep = deps_lock.verify()
        assert rep["ok"], deps_lock.format_report(rep)
        assert rep["checked"] >= 8

    def test_real_lock_file_has_no_unexact_pins(self):
        spec = deps_lock.parse_lock(deps_lock.LOCK_FILE.read_text(encoding="utf-8"))
        assert spec.invalid == []

    def test_format_report_human_readable(self):
        rep = {"ok": False, "lock_file": "L", "checked": 2,
               "missing": [{"name": "a", "pinned": "1"}],
               "drifted": [{"name": "b", "pinned": "1", "installed": "2"}],
               "invalid_pins": [{"line": 3, "text": "c>=1", "operator": ">="}]}
        txt = deps_lock.format_report(rep)
        assert "缺失: a" in txt and "漂移: b" in txt and "锁文件违规" in txt


class _FakeIM:
    """importlib.metadata 替身（版本注入）。"""
    class PackageNotFoundError(Exception):
        pass

    def __init__(self, fn):
        self._fn = fn

    def version(self, name):
        return self._fn(name)


def _no(name):
    raise _FakeIM.PackageNotFoundError(name)


class TestRequire:
    def test_require_reports_missing(self, monkeypatch):
        monkeypatch.setattr(deps_lock, "_im",
                            _FakeIM(lambda n: "1.0" if n == "httpx" else _no(n)))
        r = deps_lock.require("httpx", "no-such")
        assert r == {"ok": False, "missing": ["no-such"], "versions": {"httpx": "1.0"}}


# ── 供应商错误文案兼容层（error_codes.vendor_compat）──────────────

class TestVendorCompat:
    def test_openai_rate_limit_payload(self):
        n = vendor_compat.normalize(
            "openai", status=429,
            payload={"error": {"message": "Rate limit reached for requests",
                               "type": "rate_limit_error", "code": "rate_limit_exceeded"}})
        assert n.code == ErrorCode.LLM_RATE_LIMITED
        assert n.retryable is True
        assert n.kind == "rate_limit"
        assert "Rate limit" in n.raw                       # 原文保留供排查
        assert is_retryable(n.code) is True                # 决策只看码

    def test_anthropic_overloaded_payload(self):
        n = vendor_compat.normalize(
            "anthropic", status=529,
            payload={"type": "error", "error": {"type": "overloaded_error",
                                                "message": "Overloaded"}})
        assert n.code == ErrorCode.LLM_SERVER_ERROR and n.retryable is True

    def test_auth_failure_not_retryable(self):
        n = vendor_compat.normalize(
            "openai", status=401,
            payload={"error": {"message": "Incorrect API key provided",
                               "type": "invalid_request_error",
                               "code": "invalid_api_key"}})
        assert n.code == ErrorCode.LLM_AUTH_FAILED and n.retryable is False

    def test_quota_exceeded_not_retryable(self):
        n = vendor_compat.normalize(
            "deepseek", status=402,
            payload={"error": {"message": "Insufficient Balance",
                               "type": "insufficient_quota"}})
        assert n.code == ErrorCode.LLM_QUOTA_EXCEEDED and n.retryable is False

    def test_context_length_maps_to_invalid_request(self):
        n = vendor_compat.normalize(
            "qwen", status=400,
            payload={"error": {"message": "This model's maximum context length is 8192 tokens",
                               "type": "invalid_request_error",
                               "code": "context_length_exceeded"}})
        assert n.code == ErrorCode.LLM_INVALID_REQUEST and n.retryable is False

    def test_content_filter(self):
        n = vendor_compat.normalize(
            "azure", status=400,
            payload={"error": {"message": "The response was filtered",
                               "code": "content_filter"}})
        assert n.code == ErrorCode.LLM_CONTENT_FILTERED and n.retryable is False

    def test_message_only_payload_token_scan(self):
        """无结构 type/code，message 语义词仍被边界归一（一次性）。"""
        n = vendor_compat.normalize(
            "ollama", payload="Error: rate limit exceeded, slow down")
        assert n.code == ErrorCode.LLM_RATE_LIMITED

    def test_transport_exception_reuses_llm_retry_taxonomy(self):
        import httpx
        exc = httpx.ConnectError("connection refused")
        n = vendor_compat.normalize("openai", exc=exc)
        assert n.code == ErrorCode.LLM_NETWORK_ERROR and n.retryable is True
        assert n.kind == "connect_error"          # 九态分类复用 utils.llm_retry

    def test_status_only_mapping(self):
        assert vendor_compat.normalize("x", status=503).code == ErrorCode.LLM_SERVER_ERROR
        assert vendor_compat.normalize("x", status=408).code == ErrorCode.LLM_TIMEOUT
        n429 = vendor_compat.normalize("x", status=429)
        assert n429.code == ErrorCode.LLM_RATE_LIMITED and n429.retryable is True

    def test_empty_output_independent_failure(self):
        n = vendor_compat.normalize("openai", status=200, empty_output=True)
        assert n.code == ErrorCode.LLM_EMPTY_OUTPUT
        assert n.kind == "empty_output" and n.retryable is True

    def test_unknown_never_auto_retries(self):
        n = vendor_compat.normalize("weird-vendor", payload={"error": {"message": "blip"}})
        assert n.code == ErrorCode.LLM_UNKNOWN and n.retryable is False

    def test_normalized_error_to_open_soul_error(self):
        n = vendor_compat.normalize("openai", status=429)
        err = n.to_error()
        assert err.code == ErrorCode.LLM_RATE_LIMITED
        assert err.retryable is True
        d = err.to_dict()
        assert set(d) == {"code", "message", "desc", "retryable", "data"}

    def test_all_token_codes_registered(self):
        """TOKEN_TO_CODE 全部映射到已注册错误码（禁悬空码）。"""
        from error_codes import get_error_info
        for tok, code in vendor_compat.TOKEN_TO_CODE.items():
            assert get_error_info(code) is not None, tok

# -*- coding: utf-8 -*-
"""路线图 1.5 用户画像审计/清理契约测试。

覆盖：
1. audit_reasons 噪音识别：截断key/任务请求/疑问句/否定语义反转
2. record_fact 规范条目不被句式启发式误伤
3. audit_entries 审计视图（suspect+原因+统计+only_suspect过滤）
4. delete_facts 无 confirm 只预览不落库（禁擅自清库的核心护栏）
5. delete_facts confirm=true 按 pref_ids/keys/only_suspect 真实删除
6. delete_facts 无删除范围 → 拒绝（禁止无条件清空画像库）
7. 接线护栏：soulmate_agent 工具定义 + 主dispatch + code-mode dispatch
   + 系统提示工具清单 + 共享分发 helper
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.user_preferences import UserPreferenceLearner  # noqa: E402


class _Tmp:
    """免 pytest fixture 的临时目录。"""
    def __init__(self):
        self._d = tempfile.mkdtemp(prefix="profile_audit_test_")

    def __truediv__(self, name):
        return os.path.join(self._d, name)


def _learner(tmp_path):
    return UserPreferenceLearner(db_path=str(tmp_path / "profile.db"))


def _seed_noise_and_real(tmp_path):
    """造库：2条旧裸'我'噪音（_extract_key截断特征）+ 2条规范事实。"""
    l = _learner(tmp_path)
    # 旧噪音1：任务请求整句被存成偏好（key=value[:20]截断）
    l._upsert_preference(
        category="general", key="帮我写一个Python快速排",
        value="帮我写一个Python快速排序", confidence=0.9,
        source="explicit", evidence=["帮我写一个Python快速排序"])
    # 旧噪音2：否定偏好丢"不要"前缀（语义反转）
    l._upsert_preference(
        category="general", key="使用terminal",
        value="使用terminal", confidence=0.3,
        source="explicit_negative", evidence=["不要使用terminal"])
    # 规范事实：record_fact 显式记录（语义key）
    l.record_fact("environment", "操作系统", "Arch Linux")
    l.record_fact("preference", "响应风格", "测试驱动开发的简洁风格")
    return l


# ── 1. audit_reasons 噪音识别 ─────────────────────────────────────

def test_audit_flags_truncated_task_request():
    l = _learner(_Tmp())
    row = {"key": "帮我写一个Python快速排", "value": "帮我写一个Python快速排序", "source": "explicit"}
    reasons = l.audit_reasons(row)
    assert any("截断" in r for r in reasons)
    assert any("任务请求" in r for r in reasons)


def test_audit_flags_question_sentence():
    l = _learner(_Tmp())
    row = {"key": "很长时间没发消息你是如何保", "value": "很长时间没发消息你是如何保持连接的呢", "source": "explicit"}
    reasons = l.audit_reasons(row)
    assert any("语气词" in r or "任务请求" in r or "截断" in r for r in reasons)


def test_audit_flags_semantic_inversion_negative():
    l = _learner(_Tmp())
    row = {"key": "使用terminal", "value": "使用terminal", "source": "explicit_negative"}
    reasons = l.audit_reasons(row)
    assert any("语义反转" in r for r in reasons)


def test_audit_clean_for_record_fact_entries():
    """record_fact 规范条目（语义key+事实value）不受启发式误伤。"""
    l = _learner(_Tmp())
    for row in [
        {"key": "操作系统", "value": "Arch Linux", "source": "explicit"},
        {"key": "响应风格", "value": "测试驱动开发的简洁风格", "source": "explicit"},
        {"key": "preferred_language", "value": "TypeScript", "source": "inferred"},
    ]:
        assert l.audit_reasons(row) == [], f"误伤: {row}"


# ── 2/3. audit_entries 审计视图 ──────────────────────────────────

def test_audit_entries_marks_noise_and_clean_counts(tmp_path):
    l = _seed_noise_and_real(tmp_path)
    audit = l.audit_entries()
    assert audit["total"] == 4
    assert audit["suspect_count"] == 2
    assert audit["clean_count"] == 2
    suspects = [e for e in audit["entries"] if e["suspect"]]
    cleans = [e for e in audit["entries"] if not e["suspect"]]
    assert all(e["reasons"] for e in suspects)
    assert {e["key"] for e in cleans} == {"操作系统", "响应风格"}


def test_audit_entries_only_suspect_filter(tmp_path):
    l = _seed_noise_and_real(tmp_path)
    audit = l.audit_entries(only_suspect=True)
    assert len(audit["entries"]) == 2
    assert all(e["suspect"] for e in audit["entries"])
    assert audit["total"] == 4  # total 仍是全量


# ── 4. delete 无 confirm 只预览（禁擅自清库核心护栏）─────────────

def test_delete_without_confirm_is_preview_only(tmp_path):
    l = _seed_noise_and_real(tmp_path)
    before = l.get_stats()["total_preferences"]
    ok, msg = l.delete_facts(only_suspect=True, confirm=False)
    assert not ok
    assert "删除未执行" in msg and "预览" in msg
    assert "confirm=true" in msg
    assert l.get_stats()["total_preferences"] == before  # 一条都没删


def test_delete_default_confirm_false(tmp_path):
    """confirm 缺省=不删（工具默认参数方向安全）。"""
    l = _seed_noise_and_real(tmp_path)
    ok, _msg = l.delete_facts(only_suspect=True)
    assert not ok
    assert l.get_stats()["total_preferences"] == 4


# ── 5. confirm=true 真实删除 ─────────────────────────────────────

def test_delete_confirm_by_pref_ids(tmp_path):
    l = _seed_noise_and_real(tmp_path)
    audit = l.audit_entries(only_suspect=True)
    ids = [e["pref_id"] for e in audit["entries"][:1]]
    ok, msg = l.delete_facts(pref_ids=ids, confirm=True)
    assert ok and "✅" in msg and "已删除 1 条" in msg
    assert l.get_stats()["total_preferences"] == 3
    remaining = {e["pref_id"] for e in l.audit_entries()["entries"]}
    assert not (set(ids) & remaining)


def test_delete_confirm_by_keys(tmp_path):
    l = _seed_noise_and_real(tmp_path)
    ok, msg = l.delete_facts(keys=["操作系统"], confirm=True)
    assert ok and "已删除 1 条" in msg
    keys = {e["key"] for e in l.audit_entries()["entries"]}
    assert "操作系统" not in keys


def test_delete_confirm_only_suspect_keeps_clean(tmp_path):
    l = _seed_noise_and_real(tmp_path)
    ok, msg = l.delete_facts(only_suspect=True, confirm=True)
    assert ok
    audit = l.audit_entries()
    assert audit["suspect_count"] == 0
    assert audit["total"] == audit["clean_count"] == 2  # 规范条目全部保留


# ── 6. 安全边界 ──────────────────────────────────────────────────

def test_delete_requires_scope():
    l = _learner(_Tmp())
    ok, msg = l.delete_facts(confirm=True)
    assert not ok and "禁止无条件清空" in msg


def test_delete_no_match_is_noop(tmp_path):
    l = _seed_noise_and_real(tmp_path)
    ok, msg = l.delete_facts(keys=["不存在的key"], confirm=True)
    assert ok and "无匹配条目" in msg
    assert l.get_stats()["total_preferences"] == 4


def test_delete_invalid_fact_type(tmp_path):
    l = _seed_noise_and_real(tmp_path)
    ok, msg = l.delete_facts(fact_type="bogus", confirm=True)
    assert not ok and msg.startswith("错误")


# ── 7. 接线护栏（soulmate_agent 真实路径）────────────────────────

def _agent_src():
    return (Path(__file__).resolve().parents[1] / "agent" / "soulmate_agent.py").read_text(encoding="utf-8")


def test_wiring_tool_defined_in_builtin_tools():
    src = _agent_src()
    assert '"name": "profile_audit"' in src
    assert "builtin_tools.extend(profile_tools)" in src


def test_wiring_shared_dispatch_helper():
    src = _agent_src()
    assert "def _profile_audit_dispatch(self, func_args: dict) -> str:" in src
    assert "self._pref_learner.delete_facts(" in src
    assert "self._pref_learner.audit_entries(" in src


def test_wiring_main_dispatch_includes_profile_audit():
    src = _agent_src()
    assert 'elif func_name in ("profile_record", "profile_read", "profile_audit"):' in src
    m_start = src.index("_run_llm_with_tools")
    assert "self._profile_audit_dispatch(func_args)" in src[m_start:]


def test_wiring_code_mode_dispatch_includes_profile_audit():
    src = _agent_src()
    cm_start = src.index("async def _code_mode_tool_call")
    cm_end = src.index("async def _traj_record_round")
    cm_src = src[cm_start:cm_end]
    assert '"profile_record", "profile_read", "profile_audit"' in cm_src
    assert "self._profile_audit_dispatch(func_args)" in cm_src


def test_wiring_system_prompt_tool_list_mentions_audit():
    src = _agent_src()
    assert "profile_audit: 用户画像审计/清理" in src
    assert "禁止擅自清库" in src

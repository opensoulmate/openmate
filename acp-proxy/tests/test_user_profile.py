# -*- coding: utf-8 -*-
"""路线图 1.4 用户画像契约测试。

覆盖：
1. record_fact 显式记录三类型（preference/environment/identity）
2. record_fact 参数校验（非法 fact_type / 空 key/value）
3. upsert：同 fact_type+key 更新旧值不重复
4. 环境事实自动学习（我的系统是…/我跑在…/我用…）
5. 裸"我"噪音不再生成伪偏好
6. get_context_prompt 分组注入（偏好/环境事实/身份）
7. get_profile_summary 摘要 + fact_type 过滤
8. 接线护栏：soulmate_agent 真实路径含两工具 + 主dispatch + code-mode dispatch
   + 系统提示注入 + 工具清单说明（含 1.1 遗留 code-mode memory 工具补齐）
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.user_preferences import UserPreferenceLearner  # noqa: E402


def _learner(tmp_path):
    return UserPreferenceLearner(db_path=str(tmp_path / "profile.db"))


# ── 1. record_fact 显式记录 ──────────────────────────────────────

def test_record_fact_three_types(tmp_path):
    l = _learner(tmp_path)
    for ft, key, value in [
        ("preference", "响应风格", "简洁"),
        ("environment", "操作系统", "Arch Linux"),
        ("identity", "角色", "全栈开发者"),
    ]:
        ok, msg = l.record_fact(ft, key, value)
        assert ok, msg
        assert "✅" in msg
    stats = l.get_stats()
    assert stats["total_preferences"] == 3
    assert stats["by_category"] == {"preference": 1, "environment": 1, "identity": 1}


def test_record_fact_rejects_bad_fact_type(tmp_path):
    l = _learner(tmp_path)
    ok, msg = l.record_fact("bogus", "a", "b")
    assert not ok
    assert "fact_type" in msg
    assert l.get_stats()["total_preferences"] == 0


def test_record_fact_rejects_empty_key_or_value(tmp_path):
    l = _learner(tmp_path)
    ok, msg = l.record_fact("preference", "", "简洁")
    assert not ok and "不能为空" in msg
    ok, msg = l.record_fact("preference", "风格", "   ")
    assert not ok and "不能为空" in msg


# ── 3. upsert 不重复 ────────────────────────────────────────────

def test_record_fact_upserts_same_key(tmp_path):
    l = _learner(tmp_path)
    l.record_fact("environment", "操作系统", "Arch Linux")
    ok, msg = l.record_fact("environment", "操作系统", "Ubuntu 24.04")
    assert ok
    prefs = l.get_preferences(min_confidence=0.0)
    os_rows = [p for p in prefs if p["key"] == "操作系统"]
    assert len(os_rows) == 1  # 不产生重复条目
    assert os_rows[0]["value"] == "Ubuntu 24.04"  # 新值覆盖旧值


# ── 4. 环境事实自动学习 ──────────────────────────────────────────

def test_env_learning_subject_key_upsert(tmp_path):
    l = _learner(tmp_path)
    learned = l.learn_from_message("我的系统是Arch Linux，用hyprland", "s1")
    env = [p for p in learned if p.category == "environment"]
    assert env, f"应学到环境事实: {learned}"
    assert any(p.key == "系统" for p in env)
    # 同主题新值覆盖
    l.learn_from_message("我的系统是Ubuntu 24.04", "s1")
    prefs = l.get_preferences(min_confidence=0.0)
    sys_rows = [p for p in prefs if p["category"] == "environment" and p["key"] == "系统"]
    assert len(sys_rows) == 1
    assert sys_rows[0]["value"] == "Ubuntu 24.04"


def test_env_learning_fixed_keys(tmp_path):
    l = _learner(tmp_path)
    l.learn_from_message("我跑在一台腾讯云服务器上。", "s1")
    l.learn_from_message("我在用vim写代码。", "s1")
    keys = {p["key"] for p in l.get_preferences(min_confidence=0.0)}
    assert "运行环境" in keys
    assert "常用工具" in keys


# ── 5. 裸"我"噪音抑制 ───────────────────────────────────────────

def test_bare_wo_no_longer_creates_noise_preference(tmp_path):
    """"我觉得这个不对"这类句子不得生成伪偏好污染画像。"""
    l = _learner(tmp_path)
    learned = l.learn_from_message("我觉得这个不对，帮我看看", "s1")
    assert learned == []
    assert l.get_stats()["total_preferences"] == 0
    # 显式偏好仍然工作
    learned = l.learn_from_message("我喜欢简洁的回答", "s1")
    assert any(p.category != "environment" for p in learned)


# ── 6. get_context_prompt 分组注入 ────────────────────────────────

def test_context_prompt_grouped_sections(tmp_path):
    l = _learner(tmp_path)
    l.record_fact("preference", "响应风格", "简洁")
    l.record_fact("environment", "操作系统", "Arch Linux")
    l.record_fact("identity", "角色", "全栈开发者")
    ctx = l.get_context_prompt()
    assert "### 偏好" in ctx
    assert "### 环境事实" in ctx
    assert "### 身份" in ctx
    assert "响应风格: 简洁" in ctx
    assert "操作系统: Arch Linux" in ctx
    assert "角色: 全栈开发者" in ctx


def test_context_prompt_empty_when_no_facts(tmp_path):
    assert _learner(tmp_path).get_context_prompt() == ""


# ── 7. get_profile_summary ─────────────────────────────────────

def test_profile_summary_and_filter(tmp_path):
    l = _learner(tmp_path)
    l.record_fact("preference", "响应风格", "简洁")
    l.record_fact("environment", "操作系统", "Arch Linux")
    full = l.get_profile_summary()
    assert "# 用户画像" in full
    assert "响应风格" in full and "操作系统" in full
    only_env = l.get_profile_summary(fact_type="environment")
    assert "操作系统" in only_env and "响应风格" not in only_env
    bad = l.get_profile_summary(fact_type="bogus")
    assert bad.startswith("错误")


# ── 8. 接线护栏（soulmate_agent 真实路径）────────────────────────

def _agent_src():
    return (Path(__file__).resolve().parents[1] / "agent" / "soulmate_agent.py").read_text(encoding="utf-8")


def test_wiring_tools_registered_in_builtin_tools():
    src = _agent_src()
    assert '"name": "profile_record"' in src
    assert '"name": "profile_read"' in src
    assert "builtin_tools.extend(profile_tools)" in src  # 真的进了工具面


def test_wiring_main_dispatch_branch_executes_profile_tools():
    src = _agent_src()
    assert 'elif func_name in ("profile_record", "profile_read", "profile_audit"):' in src
    assert "self._pref_learner.record_fact(" in src
    assert "self._pref_learner.get_profile_summary(" in src


def test_wiring_code_mode_dispatch_includes_profile_and_memory():
    """code-mode内层dispatch（_code_mode_tool_call）也要接线（含1.1遗留memory补齐）。"""
    src = _agent_src()
    cm_start = src.index("async def _code_mode_tool_call")
    cm_end = src.index("async def _traj_record_round")
    cm_src = src[cm_start:cm_end]
    assert 'if func_name in ("profile_record", "profile_read", "profile_audit"):' in cm_src
    assert "self._pref_learner.record_fact(" in cm_src
    assert 'if func_name in ("memory_read", "memory_write", "memory_append"):' in cm_src
    assert "self._persistent_memory.read(" in cm_src


def test_wiring_system_prompt_injects_profile():
    src = _agent_src()
    assert "self._pref_learner.get_context_prompt()" in src
    assert "用户画像（偏好/环境事实/身份，请遵守）" in src
    assert 'name="user_profile", source="pref_learner"' in src  # token归因在场


def test_wiring_prompt_tool_list_mentions_profile():
    src = _agent_src()
    assert "profile_record / profile_read" in src

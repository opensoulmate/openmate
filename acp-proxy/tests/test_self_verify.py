# -*- coding: utf-8 -*-
"""路线图 2.1 计划-执行-验证循环——需求自验契约测试。

覆盖：
1. 需求提取：数字列表 / 圆圈序号 / 顺序连接词 / 分号分句 / 闲聊→空 / 长任务兜底单条
2. 自验三态：met（有工具/完成证据）/ unmet（未覆盖）/ uncertain（说了≠做了，无证据封顶）
3. 覆盖率与补做清单：missing_requirements 只含 unmet
4. 报告渲染：✅/⚠️/❌ + 覆盖计数 + 补做提示词含未覆盖项
5. 接线护栏：soulmate_agent 真实路径含 SelfVerifier 初始化 + 自验调用 + 补做循环 + 结论推送
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.self_verify import (  # noqa: E402
    SelfVerifier,
    extract_requirements,
)


# ── 1. 需求提取 ──────────────────────────────────────────────────

def test_extract_numbered_list():
    reqs = extract_requirements("帮我完成三件事：1. 修复登录bug 2. 生成月报 3. 更新文档")
    assert reqs == ["修复登录bug", "生成月报", "更新文档"], reqs


def test_extract_circled_list():
    reqs = extract_requirements("①写一个爬虫 ②跑通测试")
    assert reqs == ["写一个爬虫", "跑通测试"], reqs


def test_extract_seq_words():
    reqs = extract_requirements("首先清理缓存，然后重启服务，最后验证接口")
    assert len(reqs) == 3, reqs
    assert "清理缓存" in reqs[0]
    assert "重启服务" in reqs[1]
    assert "验证接口" in reqs[2]


def test_extract_clause_split():
    reqs = extract_requirements("把配置里的端口改成8090；同时把日志级别调成INFO")
    assert len(reqs) == 2, reqs


def test_extract_chit_chat_empty():
    assert extract_requirements("你好") == []
    assert extract_requirements("") == []
    assert extract_requirements("在吗") == []


def test_extract_long_single_task_fallback():
    text = "请分析这份招标文件的评分标准并给出品牌推荐建议"
    reqs = extract_requirements(text)
    assert reqs == [text], reqs


# ── 2. 自验三态 ──────────────────────────────────────────────────

def _verifier():
    return SelfVerifier()


def test_verify_met_with_tools():
    v = _verifier()
    report = v.verify(
        "1. 修复登录bug 2. 生成月报",
        "登录bug已修复，月报已生成并保存到 /tmp/report.md",
        tool_calls_log=[{"function": {"name": "patch", "arguments": "{}"}}],
    )
    assert report.ok, [(i.requirement, i.status, i.score) for i in report.items]
    assert report.coverage == 1.0


def test_verify_unmet_missing_item():
    v = _verifier()
    report = v.verify(
        "1. 修复登录bug 2. 生成月报",
        "登录bug已修复，测试通过",
        tool_calls_log=[{"function": {"name": "patch", "arguments": "{}"}}],
    )
    assert not report.ok
    missing = report.missing_requirements()
    assert len(missing) == 1, missing
    assert "生成月报" in missing[0]
    # 另一项应已覆盖
    statuses = {i.requirement: i.status for i in report.items}
    assert statuses["修复登录bug"] == "met"


def test_verify_said_not_done_capped_uncertain():
    """"说了≠做了"：动作类需求无工具执行也无完成证据 → 证据不足，不给 met。"""
    v = _verifier()
    report = v.verify(
        "帮我写一个数据同步脚本用于每天晚上同步订单数据",
        "好的，我来帮你写一个数据同步脚本用于每天晚上同步订单数据，请稍等",
        tool_calls_log=[],
    )
    assert len(report.items) == 1
    assert report.items[0].status == "uncertain", report.items[0]
    assert not report.ok
    assert report.missing_requirements() == []  # uncertain 不进补做清单


def test_verify_chit_chat_skipped():
    v = _verifier()
    report = v.verify("你好", "你好，有什么可以帮你？", tool_calls_log=[])
    assert report.items == []
    assert report.ok


# ── 3. 报告渲染与补做提示词 ─────────────────────────────────────

def test_format_report_renders_all_items():
    v = _verifier()
    report = v.verify(
        "1. 修复登录bug 2. 生成月报",
        "登录bug已修复",
        tool_calls_log=[{"function": {"name": "patch", "arguments": "{}"}}],
    )
    text = v.format_report(report)
    assert "🔍" in text
    assert "✅ 1. 修复登录bug" in text
    assert "❌ 2. 生成月报（未覆盖）" in text
    assert "覆盖 1/2" in text


def test_remediation_prompt_lists_missing_only():
    v = _verifier()
    prompt = v.build_remediation_prompt(["生成月报", "更新文档"])
    assert "生成月报" in prompt
    assert "更新文档" in prompt
    assert "只做这些" in prompt


# ── 4. 接线护栏（写了≠接线了）──────────────────────────────────

def test_soulmate_agent_wiring():
    src = Path(__file__).resolve().parents[1] / "agent" / "soulmate_agent.py"
    text = src.read_text(encoding="utf-8")
    assert "from agent.self_verify import SelfVerifier" in text
    assert "self._self_verifier = SelfVerifier()" in text
    assert "self._self_verifier.verify(" in text          # 验证环节真实调用
    assert "build_remediation_prompt(" in text            # 补做循环接线
    assert "self._self_verifier.format_report(" in text   # 结论推送接线
    # 补做必须有上限（防失控循环）
    assert "上限1轮" in text

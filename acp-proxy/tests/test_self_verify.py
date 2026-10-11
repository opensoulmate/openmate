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
    is_perception_meta,
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


# ── 1b. 系统附件锚点注记不进需求（周期#102 audio e2e 实证回归） ──────────

def test_extract_strips_file_anchor_note():
    """"[附件已保存到: …]"是管道元信息，不得拆成需求项（否则必然 unmet→必触发补做）。"""
    text = "帮我分析这份招标文件的评分标准并给出品牌推荐建议\n[附件已保存到: /home/climbing/opensoul/data/attachments/om-x/file.pdf]"
    reqs = extract_requirements(text)
    assert all("已保存到" not in r for r in reqs), reqs
    assert reqs == ["帮我分析这份招标文件的评分标准并给出品牌推荐建议"], reqs


def test_extract_pure_voice_message_no_requirements():
    """纯语音消息=正文只有锚点注记，剥掉后无正文→闲聊不自验，零噪音。"""
    text = (
        "\n[附件已保存到: /home/climbing/opensoul/data/attachments/om-828b6d70542f/file.mpeg]"
        "\n[语音附件已随消息直发：你已直接听到 /home/climbing/opensoul/data/attachments/"
        "om-828b6d70542f/file.mpeg 的音频内容，请基于听到的内容作答，无需读取该文件]"
    )
    assert extract_requirements(text) == []


def test_extract_anchor_not_item_in_list():
    """编号列表里混入锚点行，锚点不得成为第 N 项。"""
    text = "1. 生成月度报表 2. 发送邮件给团队\n[附件已保存到: /tmp/a.xlsx]"
    reqs = extract_requirements(text)
    assert len(reqs) == 2, reqs
    assert all("已保存到" not in r for r in reqs), reqs


def test_extract_image_and_file_anchor_variants():
    """ws_chat/proxy 侧的"[文件/图片已保存到: …]"同族注记同样剥离。"""
    text = "帮我看看这张截图里有什么UI问题并给出修复建议\n[图片已保存到: /tmp/img.png]"
    assert extract_requirements(text) == ["帮我看看这张截图里有什么UI问题并给出修复建议"]
    text2 = "转换这个文件的格式；顺便按日期重命名\n[文件已保存到: /tmp/f.doc]"
    reqs2 = extract_requirements(text2)
    assert all("已保存到" not in r for r in reqs2), reqs2
    assert len(reqs2) == 2, reqs2


def test_extract_user_bracket_text_untouched():
    """普通方括号文本（非锚点）不受影响——只剥"已保存到/已随消息直发"族。"""
    text = "请按照[设计规范]更新这份开发文档的接口章节"
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


# ── 5. 感知类元指令（18.3 覆盖率假阴性治理）────────────────────

# 真实残留形态（周期#102 audio e2e）：探针元指令+防缓存标记行——标记行自否定
# 被剔除后剩"复述你听到的内容"单条需求
_PROBE_META_TEXT = "复述你听到的内容\n（此行仅为防缓存标记，忽略其语义）"


def test_perception_meta_detection():
    assert is_perception_meta("复述你听到的内容")
    assert is_perception_meta("说说音频里讲了什么")
    assert is_perception_meta("repeat what you heard")
    # 带动作动词的媒体任务 = 真实可验需求，不降级
    assert not is_perception_meta("把音频里的内容写入文件")
    # 无感知引用 / 只是提到"录音"二字的普通需求
    assert not is_perception_meta("生成月报")
    assert not is_perception_meta("介绍录音转文字的流程")


def test_perception_meta_extracted_then_not_remediated():
    # 答对仍判未覆盖 → 补做1轮 = 18.1 复测残留根因；修复后不进补做清单
    reqs = extract_requirements(_PROBE_META_TEXT)
    assert reqs == ["复述你听到的内容"], reqs
    v = _verifier()
    report = v.verify(
        _PROBE_META_TEXT,
        "我听到的内容是：今天下午三点我们去公园放风筝，记得带上橘子和香蕉",
        tool_calls_log=[],
    )
    assert len(report.items) == 1
    assert report.items[0].perception
    assert report.missing_requirements() == []
    # 单条感知元指令 = ok → soulmate 侧"not ok or >=2"不触发自检横幅
    assert report.ok


def test_perception_meta_unmet_capped_uncertain():
    v = _verifier()
    report = v.verify(_PROBE_META_TEXT, "好的，马上处理", tool_calls_log=[])
    item = report.items[0]
    assert item.perception
    assert item.status == "uncertain"  # unmet 封顶，绝不进补做
    assert report.missing_requirements() == []


def test_perception_with_action_still_verifiable():
    v = _verifier()
    report = v.verify(
        "请把音频里的内容写入 notes 文件并保存下来", "好的", tool_calls_log=[]
    )
    assert report.items
    assert not report.items[0].perception
    assert report.missing_requirements()  # 真实任务未做 → 照常进补做


def test_coverage_denominator_excludes_perception():
    v = _verifier()
    report = v.verify(
        "1. 修复登录bug 2. 复述你听到的内容",
        "登录bug已修复",
        tool_calls_log=[{"function": {"name": "patch", "arguments": "{}"}}],
    )
    assert [i.perception for i in report.items] == [False, True]
    assert report.coverage == 1.0  # 感知项不计分母（假阴性根因）
    assert report.missing_requirements() == []
    text = v.format_report(report)
    assert "覆盖 1/1" in text
    assert "感知类元指令不计覆盖" in text

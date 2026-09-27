"""任务延续判定链路复活 — 回归测试（journal live实锤的P0死路径）

发现经过（本轮生产journal实锤）：
  Sep 24 08:19:48 WARNING: [TaskState] LLM judge failed: 'SoulMateAgent' object
  has no attribute '_llm_call', defaulting new

两个静默失效叠加（同一条任务延续链路）：
1. P0：_prompt_inner任务判断块调用**不存在的** self._llm_call → AttributeError被
   try/except吞掉 → LLM judge从未真实执行，规则不明时一律"new"→旧任务被误完成、
   任务延续失效（"写了≠接线了"：调用点符号根本没定义）。
2. P1：实体提取 `[w for w in user_text if len(w) > 1 ...]` 按**字符**迭代字符串，
   len(w)>1 恒False → entities恒为空 → 规则C（实体→continue）与规则F的
   has_old_entity 永不命中 → 全部推向llm_judge（恰是死路径）→ 双重失效。

本文件锁死：LLM judge真实调用llm_engine.chat（含fenced JSON/未知action归一/
失败上抛给调用点兜底）、extract_entities非恒空且接线到create_task两分支、
以及**全类AST护栏**（self.X( 调用必须解析到方法或已赋值属性——本bug类绝迹）。
"""
import ast
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.soulmate_agent import SoulMateAgent
from utils.task_state_manager import (
    TaskState,
    extract_entities,
    judge_task_continuation,
)

HERE = Path(__file__).resolve().parent.parent


def run(coro):
    return asyncio.run(coro)


def _src(rel):
    return (HERE / rel).read_text(encoding="utf-8")


# ══════════════ 测试替身 ══════════════

class FakeChatLLM:
    """脚本化非流式LLM：记录真实请求消息，返回预设文本"""

    def __init__(self, reply: str):
        self.reply = reply
        self.seen: list[list[dict]] = []

    async def chat(self, messages):
        self.seen.append([dict(m) for m in messages])
        return self.reply


def _make_agent(reply: str) -> SoulMateAgent:
    # 与 tests/test_steering.py make_agent 同款轻量构造惯例：__new__跳过重依赖
    agent = SoulMateAgent.__new__(SoulMateAgent)
    agent.llm_engine = FakeChatLLM(reply)
    return agent


def _task(goal="调整部署服务配置", entities=None) -> TaskState:
    return TaskState(
        task_id="t1", session_id="s1", goal=goal,
        entities=list(entities) if entities is not None else [],
    )


# ══════════════ 守护1：LLM judge真实调用（P0死路径回归钉） ══════════════

class TestLLMJudgeRealWiring:
    def test_judge_calls_llm_engine_chat_and_returns_continue(self):
        """P0回归钉：judge必须真实走到llm_engine.chat（旧self._llm_call=AttributeError）"""
        agent = _make_agent('{"action": "continue", "reason": "同一任务"}')
        action = run(agent._judge_task_continuation_llm(
            _task(goal="调整部署服务配置"), "部署服务配置再优化一下"))
        assert action == "continue"
        # 真实请求形状：system=任务判断器，user=当前任务goal+用户消息
        msgs = agent.llm_engine.seen[0]
        assert msgs[0]["role"] == "system" and "任务判断器" in msgs[0]["content"]
        assert "调整部署服务配置" in msgs[1]["content"]
        assert "部署服务配置再优化一下" in msgs[1]["content"]

    def test_judge_returns_new_when_llm_says_new(self):
        agent = _make_agent('{"action": "new", "reason": "换话题了"}')
        action = run(agent._judge_task_continuation_llm(_task(), "做个新东西"))
        assert action == "new"

    def test_fenced_json_stripped(self):
        """模型偶发markdown围栏输出不能判失败"""
        agent = _make_agent('```json\n{"action": "continue", "reason": "x"}\n```')
        action = run(agent._judge_task_continuation_llm(_task(), "继续这个"))
        assert action == "continue"

    @pytest.mark.parametrize("raw", [
        '{"action": "ask", "reason": "?"}',
        '{"action": "maybe"}',
        '{"reason": "缺action字段"}',
        '{"action": "CONTINUE"}',
    ])
    def test_unknown_action_normalized_to_new(self, raw):
        """未知/缺失/大小写漂移的action一律归一"new"，不许透传进状态机分支"""
        agent = _make_agent(raw)
        action = run(agent._judge_task_continuation_llm(_task(), "xx"))
        assert action == "new"

    def test_invalid_json_raises_for_caller_fallback(self):
        """解析失败必须上抛（调用点except兜底"new"+warning留痕，失败必须可见）"""
        agent = _make_agent("这不是JSON")
        with pytest.raises(Exception):
            run(agent._judge_task_continuation_llm(_task(), "xx"))

    def test_llm_error_propagates_not_swallowed(self):
        """LLM传输错误同样上抛——绝不允许再出现"吞掉异常→静默降级"的死路径"""
        class BoomLLM:
            async def chat(self, messages):
                raise RuntimeError("LLM API error 402")
        agent = SoulMateAgent.__new__(SoulMateAgent)
        agent.llm_engine = BoomLLM()
        with pytest.raises(RuntimeError):
            run(agent._judge_task_continuation_llm(_task(), "xx"))


# ══════════════ 守护2：调用点接线（源码级钉） ══════════════

class TestCallSiteWiring:
    def test_llm_judge_branch_calls_extracted_method(self):
        src = _src("agent/soulmate_agent.py")
        assert "action = await self._judge_task_continuation_llm(current_task, user_text)" in src
        # 旧死调用绝迹（docstring引用历史不带括号，不误伤）
        assert "await self._llm_call(" not in src
        # 失败兜底契约在场：except → warning + "new"
        assert '[TaskState] LLM judge failed' in src

    def test_no_missing_self_call_attrs_in_soulmate_agent(self):
        """全类AST护栏：self.X( 的X必须是方法或self已赋值属性——本bug类绝迹。

        旧实现漏网的就是 _llm_call（调用面存在、定义不存在、异常被吞）。
        若未来合法引入动态属性（setattr），在 _DYNAMIC_ATTRS 白名单登记。
        """
        src = _src("agent/soulmate_agent.py")
        tree = ast.parse(src)
        cls = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.ClassDef) and n.name == "SoulMateAgent"
        )
        defined = {
            n.name for n in cls.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        # 属性赋值（含property setter等方法内 self.X = ...）
        for n in ast.walk(cls):
            if (isinstance(n, ast.Attribute) and isinstance(n.ctx, ast.Store)
                    and isinstance(n.value, ast.Name) and n.value.id == "self"):
                defined.add(n.attr)
        # 类级赋值
        for n in cls.body:
            if isinstance(n, ast.Assign):
                for t in n.targets:
                    if isinstance(t, ast.Name):
                        defined.add(t.id)
            elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
                defined.add(n.target.id)
        called = set()
        for n in ast.walk(cls):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and isinstance(n.func.value, ast.Name)
                    and n.func.value.id == "self"):
                called.add(n.func.attr)
        missing = sorted(called - defined - _DYNAMIC_ATTRS)
        assert not missing, (
            f"SoulMateAgent存在调用但未定义的self属性（死调用，异常被吞=静默失效）: {missing}"
        )


# 合法动态属性白名单（当前为空——出现setattr动态派发时在此登记并注释原因）
_DYNAMIC_ATTRS: set = set()


# ══════════════ 守护3：extract_entities 非恒空 + 接线 ══════════════

class TestExtractEntities:
    def test_chinese_sentence_non_empty(self):
        """P1回归钉：旧字符迭代实现恒返回[]——典型中文句子必须产出实体"""
        ents = extract_entities("调整部署服务配置")
        assert ents, "实体提取恒空回归（旧 [w for w in user_text if len(w)>1] bug）"
        assert "部署" in ents and "服务" in ents and "配置" in ents

    def test_latin_word_kept_with_case(self):
        ents = extract_entities("修复Python服务的Bug")
        assert "Python" in ents and "Bug" in ents

    def test_cjk_bigram_and_short_segment(self):
        # "部署"整体=1个CJK段→bigram="部署"；单字段"嗯"不足2字取原字但被长度过滤
        assert extract_entities("部署") == ["部署"]
        assert extract_entities("嗯") == []

    def test_stopword_only_tokens_dropped(self):
        # "的了"两个停用字组成bigram→整token纯停用词→丢弃；"目的"含实字保留
        ents = extract_entities("我的了 目的")
        assert "的了" not in ents
        assert "目的" in ents

    def test_dedupe_preserves_order_and_limit(self):
        # 去重只删完全相同token（滑动bigram重叠是切词语义，"服务服务"→服务/务服）
        assert extract_entities("Python 部署 Python") == ["Python", "部署"]
        long_text = "部署配置服务监控日志告警备份恢复扩容"
        assert len(extract_entities(long_text, limit=3)) == 3

    def test_empty_and_pure_stopword_input(self):
        assert extract_entities("") == []
        assert extract_entities("的了是在") == []


class TestEntityExtractionWiring:
    def test_create_task_sites_use_extract_entities(self):
        src = _src("agent/soulmate_agent.py")
        # new/ask 两个分支都必须走单一真源
        assert src.count("entities = extract_entities(user_text)") == 2
        # 旧恒空字符迭代绝迹
        assert "for w in user_text" not in src

    def test_extracted_entities_drive_rule_c_continue(self):
        """端到端语义：extract_entities产出的实体真的能让规则C判continue（旧恒空时同消息落ask）"""
        goal = "调整部署服务配置"
        task = _task(goal=goal, entities=extract_entities(goal))
        judgment = judge_task_continuation(task, "部署服务配置再优化一下")
        assert judgment["action"] == "continue"
        # 对照：实体为空（旧bug状态）同消息不判continue（落到短消息ask）
        judgment_empty = judge_task_continuation(_task(goal=goal, entities=[]), "部署服务配置再优化一下")
        assert judgment_empty["action"] != "continue"

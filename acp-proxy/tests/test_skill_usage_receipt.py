"""gene技能消费回执接线 — 回归测试（真实 _prompt_inner 路径）

背景（04:19轮遗留#1销账）：opensoul POST /api/gene/skill/{id}/usage 数据面已通，
但真实消费方（acp-proxy soulmate 的 recommend→注入链）没调用——技能"被使用"
只有extract FAIL分支一个记账点，消费结局（成功/失败）永远回不到success_rate，
find_relevant排序拿不到真实成败信号。

本轮接线（CAMEL WorkflowMemoryManager §1.6"复用必须带真实成败信号"）：
1. 注入点（_prompt_inner 9模块注入"4. 技能推荐"）：本回合注入的技能id记入
   _skill_injected 归因清单（先清上回合残留）；
2. 回执点（_prompt_inner 后处理"2.5 技能使用回执"）：回合正常收尾才回执
   （interrupted/superseded结局不完整不计，与TurnClose"只有完整turn才digest"
   同门），success=本回合无错误标记；清单一次性消费不留跨回合残留。

三层锁死：
① 归因清单bookkeeping（记/清/一次性/lazy init）
② _report_skill_usage 回执行为（逐个POST /usage、非200不计、异常不抛）
③ _prompt_inner 真实路径接线钉（httpx替身捕获真实POST URL+载荷）
"""

import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent.parent))

from tests.test_plan_execution import JUDGE_CONTINUE, build_env, run_prompt
from tests.test_steering import make_agent, run


class FakeResp:
    def __init__(self, status_code=200, data=None):
        self.status_code = status_code
        self._data = data if data is not None else {}

    def json(self):
        return self._data


class RecordingClient:
    """httpx.AsyncClient替身：recommend按脚本回技能，/usage全捕获。"""

    recommend_skills: list = []  # 类级脚本：下一个recommend响应给的技能
    usage_posts: list = []       # (url, json) 捕获
    calls: list = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, timeout=None, **kw):
        RecordingClient.calls.append(url)
        if "/api/gene/skill/recommend" in url:
            return FakeResp(200, {"skills": list(RecordingClient.recommend_skills)})
        if "/usage" in url:
            RecordingClient.usage_posts.append((url, json))
            return FakeResp(200, {"updated": True})
        return FakeResp(200, {})

    async def get(self, url, timeout=None, **kw):
        RecordingClient.calls.append(url)
        return FakeResp(200, {})

    async def patch(self, url, json=None, timeout=None, **kw):
        RecordingClient.calls.append(url)
        return FakeResp(200, {})


class RaisingClient:
    """/usage一发就炸的替身：回执失败绝不能打断回合收尾。"""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, timeout=None, **kw):
        if "/usage" in url:
            raise RuntimeError("opensoul down")
        return FakeResp(200, {})


def skill(skill_id, name="测试技能"):
    return {"skill_id": skill_id, "name": name, "description": "d"}


# ════════════════════════════════════════════════════════════════
# ① 归因清单 bookkeeping
# ════════════════════════════════════════════════════════════════

class TestInjectedLedger:
    def test_note_records_top3_ids_only(self, tmp_path):
        """注入清单只记前3个且只记有skill_id的（与注入上限[:3]同口径）"""
        agent = make_agent(tmp_path)
        agent._note_skill_injected("s1", [
            skill("a"), skill("b"), skill("c"), skill("d"),
            {"name": "无id技能"},
        ])
        assert agent._take_injected_skills("s1") == ["a", "b", "c"]

    def test_note_empty_clears_stale(self, tmp_path):
        """传空=清上回合残留：recommend失败/空结果不留脏清单"""
        agent = make_agent(tmp_path)
        agent._note_skill_injected("s1", [skill("a")])
        agent._note_skill_injected("s1", [])
        assert agent._take_injected_skills("s1") == []

    def test_take_is_one_shot(self, tmp_path):
        """一次性消费：取走即清，第二次take空——不留跨回合残留"""
        agent = make_agent(tmp_path)
        agent._note_skill_injected("s1", [skill("a")])
        assert agent._take_injected_skills("s1") == ["a"]
        assert agent._take_injected_skills("s1") == []

    def test_sessions_isolated(self, tmp_path):
        agent = make_agent(tmp_path)
        agent._note_skill_injected("s1", [skill("a")])
        agent._note_skill_injected("s2", [skill("b")])
        assert agent._take_injected_skills("s1") == ["a"]
        assert agent._take_injected_skills("s2") == ["b"]

    def test_lazy_init_on_new_built_agent(self, tmp_path):
        """__new__搭建的测试agent没有__init__属性——lazy init不炸"""
        agent = make_agent(tmp_path)
        assert not hasattr(agent, "_skill_injected")
        agent._note_skill_injected("s1", [skill("a")])
        assert agent._take_injected_skills("s1") == ["a"]
        # take在属性不存在时也不炸
        agent2 = make_agent(tmp_path)
        assert agent2._take_injected_skills("s1") == []


# ════════════════════════════════════════════════════════════════
# ② _report_skill_usage 回执行为
# ════════════════════════════════════════════════════════════════

class TestReportUsage:
    def test_posts_usage_per_skill_with_outcome(self, tmp_path):
        """逐个POST /api/gene/skill/{id}/usage，载荷带真实成败"""
        agent = make_agent(tmp_path)
        RecordingClient.usage_posts = []
        ok = run(agent._report_skill_usage(
            RecordingClient(), ["skill_a", "skill_b"], success=False))
        assert ok == 2
        urls = [u for u, _ in RecordingClient.usage_posts]
        assert urls == [
            "http://127.0.0.1:8090/api/gene/skill/skill_a/usage",
            "http://127.0.0.1:8090/api/gene/skill/skill_b/usage",
        ]
        assert all(p == {"success": False} for _, p in RecordingClient.usage_posts)

    def test_non_200_not_counted_no_raise(self, tmp_path):
        """404（技能已被删）不计数也不抛——回执失败不阻塞回合收尾"""
        agent = make_agent(tmp_path)

        class NotFoundClient(RecordingClient):
            async def post(self, url, json=None, timeout=None, **kw):
                if "/usage" in url:
                    return FakeResp(404, {})
                return FakeResp(200, {})

        ok = run(agent._report_skill_usage(NotFoundClient(), ["x"], success=True))
        assert ok == 0

    def test_exception_swallowed_returns_zero(self, tmp_path):
        """opensoul不可达→debug级吞掉返回0（CowAgent记账哲学）"""
        agent = make_agent(tmp_path)
        ok = run(agent._report_skill_usage(RaisingClient(), ["a", "b"], success=True))
        assert ok == 0

    def test_empty_list_noop(self, tmp_path):
        agent = make_agent(tmp_path)
        RecordingClient.usage_posts = []
        ok = run(agent._report_skill_usage(RecordingClient(), [], success=True))
        assert ok == 0
        assert RecordingClient.usage_posts == []


# ════════════════════════════════════════════════════════════════
# ③ _prompt_inner 真实路径接线钉
# ════════════════════════════════════════════════════════════════

class TestPromptInnerWiring:
    def _build(self, tmp_path, monkeypatch):
        # 复杂任务路径（plan.subtasks非空+status=active）：JUDGE_CONTINUE让
        # TaskPlanner.plan判定"继续旧任务"回活跃计划——走真实逐步执行环
        agent, plan = build_env(
            tmp_path, monkeypatch,
            steps=[("step_1", "A"), ("step_2", "B")],
            llm_scripts=[["ok A"], ["ok B"]],
            llm_call_responses=[JUDGE_CONTINUE],
        )
        monkeypatch.setattr(httpx, "AsyncClient", RecordingClient)
        return agent

    def test_injected_skill_usage_receipted_on_turn_close(self, tmp_path, monkeypatch):
        """recommend注入→回合正常收尾→真实POST /usage（success=True）"""
        RecordingClient.recommend_skills = [
            skill("skill_w1"), skill("skill_w2"), skill("skill_w3"), skill("skill_w4"),
        ]
        RecordingClient.usage_posts = []
        RecordingClient.calls = []
        agent = self._build(tmp_path, monkeypatch)
        run_prompt(agent)
        # 注入真的发生了（recommend被调用）
        assert any("/api/gene/skill/recommend" in u for u in RecordingClient.calls)
        # 回执=前3个注入技能（[:3]口径），成功结局
        urls = [u for u, _ in RecordingClient.usage_posts]
        assert urls == [
            "http://127.0.0.1:8090/api/gene/skill/skill_w1/usage",
            "http://127.0.0.1:8090/api/gene/skill/skill_w2/usage",
            "http://127.0.0.1:8090/api/gene/skill/skill_w3/usage",
        ]
        assert all(p == {"success": True} for _, p in RecordingClient.usage_posts)

    def test_interrupted_turn_not_receipted(self, tmp_path, monkeypatch):
        """真实steer中断回合→不计usage（TurnClose"只有完整turn"同门）"""
        RecordingClient.recommend_skills = [skill("skill_i1")]
        RecordingClient.usage_posts = []
        agent = self._build(tmp_path, monkeypatch)
        # 真实中断路径：/abort进插话队列→_run_llm_with_tools循环drain到abort
        # →close_reason=INTERRUPTED（soulmate_agent.py:1802）→后处理按此跳过回执
        agent._steering.enqueue("s1", "/abort")
        run_prompt(agent)
        # 中断真的发生（真实drain路径产出中断标记），且close_reason被aclose_turn
        # 正常消费（INTERRUPTED随收口pop，不留残键）
        joined = " ".join(
            m.get("content", "") for m in agent.sessions["s1"]["messages"]
        )
        assert "插话中断" in joined
        assert "s1" not in agent._turn_close_reason
        assert RecordingClient.usage_posts == []
        # 清单仍被一次性消费（不留跨回合残留）
        assert agent._take_injected_skills("s1") == []

    def test_no_recommend_hit_no_receipt(self, tmp_path, monkeypatch):
        """recommend空结果→无注入→零回执"""
        RecordingClient.recommend_skills = []
        RecordingClient.usage_posts = []
        agent = self._build(tmp_path, monkeypatch)
        run_prompt(agent)
        assert RecordingClient.usage_posts == []

    def test_no_cross_turn_stale_attribution(self, tmp_path, monkeypatch):
        """回合1注入并回执；回合2 recommend空→不得再回执回合1的技能"""
        RecordingClient.recommend_skills = [skill("skill_t1")]
        RecordingClient.usage_posts = []
        agent = self._build(tmp_path, monkeypatch)
        run_prompt(agent)
        assert len(RecordingClient.usage_posts) == 1
        RecordingClient.recommend_skills = []
        run_prompt(agent)
        # 总数仍是1：回合2零回执
        assert len(RecordingClient.usage_posts) == 1
        assert agent._take_injected_skills("s1") == []

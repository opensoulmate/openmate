"""gene技能提炼wire格式（P0 wire bug回归）—— execution_log契约序列化

缺口后果：soulmate_agent 发给 /api/gene/skill/extract 的 execution_log 是
`str(tool_calls_log)[:1000]`（Python repr）：
1. opensoul json.loads失败 → tool_calls=[] → extract_from_execution空契约
   直接None → 每个真实回合的技能提炼都400"没提炼出技能"（生产学习链全灭）；
2. 即便能解析，扁平形态{name,arguments,result_preview}没有function包装 →
   tools_used全空名。
另：`[:1000]`静默截断违反AIHawk SHOWN/SENT"截断必须显式标记"。

修复：agent/gene_wire.tool_calls_for_extract 单一序列化真源——
契约形态 [{"function": {"name", "arguments"}}, ...] + 预算化截断显式记账
（__TRUNCATED__哨兵，消费方skill_learner按dunder名过滤）。
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.gene_wire import SENTINEL_TRUNCATED, tool_calls_for_extract
from tests.test_plan_execution import FakeAsyncClient, build_env, run_prompt
from tests.test_plan_tool_ledger import _tool_call, _wire_tool_side

EXTRACT_URL = "http://127.0.0.1:8090/api/gene/skill/extract"


def _flat(name, args, preview="ok"):
    """轨迹账本扁平条目（soulmate真实形态）"""
    return {"name": name, "arguments": args, "result_preview": preview}


# ════════════════════════════════════════════════════════════════
# 1. 序列化契约（可被opensoul json.loads + extract_from_execution消费）
# ════════════════════════════════════════════════════════════════

class TestSerializeContract:
    def test_roundtrip_function_shape(self):
        """扁平账本→契约形态function包装，json.loads可解析（旧repr不可）"""
        log = [_flat("read_file", {"path": "/tmp/x.txt", "limit": 5}),
               _flat("write_file", {"path": "/tmp/o.txt", "content": "hi"})]
        wire = tool_calls_for_extract(log)
        parsed = json.loads(wire)  # 旧 str(list) 在这里必炸
        assert [t["function"]["name"] for t in parsed] == ["read_file", "write_file"]
        assert parsed[0]["function"]["arguments"] == {"path": "/tmp/x.txt", "limit": 5}

    def test_arguments_str_and_odd_types_survive(self):
        """arguments是字符串/奇异值不炸、可解析（LLM侧arguments常是json字符串）"""
        log = [_flat("terminal", "echo hi"), _flat("browser_exec", {"code": object()})]
        parsed = json.loads(tool_calls_for_extract(log))
        assert parsed[0]["function"]["arguments"] == "echo hi"
        assert isinstance(parsed[1]["function"]["arguments"], dict)

    def test_empty_and_non_dict_entries(self):
        """空账本→'[]'；非dict条目跳过不炸"""
        assert tool_calls_for_extract([]) == "[]"
        assert tool_calls_for_extract(None) == "[]"
        parsed = json.loads(tool_calls_for_extract([_flat("read_file", {"path": "a"}),
                                                    "garbage", None]))
        assert [t["function"]["name"] for t in parsed] == ["read_file"]

    def test_long_values_clipped(self):
        """长参数值按arg_chars裁剪（载荷不随content无限膨胀）"""
        log = [_flat("write_file", {"path": "p", "content": "x" * 5000})]
        parsed = json.loads(tool_calls_for_extract(log, arg_chars=50))
        assert parsed[0]["function"]["arguments"]["content"] == "x" * 50

    def test_no_truncation_within_budget(self):
        """预算内：完整序列、无哨兵"""
        log = [_flat(f"t{i}", {"path": f"/p/{i}"}) for i in range(5)]
        wire = tool_calls_for_extract(log, budget=8000)
        parsed = json.loads(wire)
        assert len(parsed) == 5
        assert SENTINEL_TRUNCATED not in wire


# ════════════════════════════════════════════════════════════════
# 2. 预算化截断（AIHawk SHOWN/SENT：截断必须显式标记，不静默）
# ════════════════════════════════════════════════════════════════

class TestBudgetedTruncation:
    def _big_log(self, n=30):
        return [_flat(f"tool_{i:02d}", {"path": f"/p/{i}", "content": "y" * 200})
                for i in range(n)]

    def test_truncation_explicit_marker(self):
        """超预算：__TRUNCATED__哨兵在场且dropped/total如实"""
        log = self._big_log(30)
        wire = tool_calls_for_extract(log, budget=2000)
        parsed = json.loads(wire)
        names = [t["function"]["name"] for t in parsed]
        assert SENTINEL_TRUNCATED in names  # 截断显式标记，非静默丢
        marker = parsed[names.index(SENTINEL_TRUNCATED)]
        assert marker["function"]["arguments"]["total"] == 30
        assert marker["function"]["arguments"]["dropped"] == (
            30 - (len(parsed) - 1))

    def test_head_and_tail_kept(self):
        """保头保尾丢中段（kilocode Truncate双端保留）"""
        wire = tool_calls_for_extract(self._big_log(30), budget=2000)
        names = [t["function"]["name"] for t in json.loads(wire)]
        assert names[0] == "tool_00"           # 头部入口保留
        assert names[-1] == "tool_29"          # 尾部产出步保留
        assert names.index(SENTINEL_TRUNCATED) not in (0, len(names) - 1)

    def test_payload_within_budget(self):
        """截断后载荷仍在预算附近（不超发）"""
        wire = tool_calls_for_extract(self._big_log(100), budget=2000)
        assert len(wire) <= 2000 + 200  # 哨兵/边界容差，量级不破

    def test_single_huge_entry_fallback(self):
        """单条巨条目：至少保头1条+哨兵，账不悬空不抛"""
        log = [_flat("mega", {"content": "z" * 10000})] + self._big_log(2)
        wire = tool_calls_for_extract(log, budget=500, arg_chars=200)
        parsed = json.loads(wire)
        assert parsed[0]["function"]["name"] == "mega"
        assert any(t["function"]["name"] == SENTINEL_TRUNCATED for t in parsed)


# ════════════════════════════════════════════════════════════════
# 3. 接线：真实 _prompt_inner 提炼POST的execution_log是合法契约JSON
# ════════════════════════════════════════════════════════════════

class _CaptureClient(FakeAsyncClient):
    """记录(url, json)载荷（FakeAsyncClient只记url）"""
    payloads = []

    async def post(self, url, json=None, timeout=None, **kw):
        _CaptureClient.payloads.append((url, json))
        return await super().post(url, json=json, timeout=timeout, **kw)


class TestExtractPostWiring:
    def test_prompt_inner_sends_contract_json(self, tmp_path, monkeypatch):
        """真实复杂路径回合→extract POST载荷可json.loads且是function包装形态
        （修复前是Python repr：本断言在旧代码上必失败=json.loads炸）"""
        import httpx
        from tests.test_plan_execution import JUDGE_CONTINUE
        f = tmp_path / "src.txt"
        f.write_text("alpha-content\n")
        out = tmp_path / "out.txt"
        FakeAsyncClient.calls.clear()
        _CaptureClient.payloads.clear()
        agent, plan = build_env(
            tmp_path, monkeypatch,
            steps=[("step_1", "A"), ("step_2", "B")],
            llm_scripts=[
                [_tool_call("read_file", {"path": str(f), "limit": 5})], ["ok A"],
                [_tool_call("write_file", {"path": str(out), "content": "hello"})],
                ["ok B"],
            ],
            llm_call_responses=[JUDGE_CONTINUE],
        )
        monkeypatch.setattr(httpx, "AsyncClient", _CaptureClient)
        _wire_tool_side(agent, tmp_path)
        run_prompt(agent)
        extract_posts = [p for u, p in _CaptureClient.payloads if u == EXTRACT_URL]
        assert extract_posts, "extract POST未触发（tool_calls_log为空或结局被门拦）"
        wire = extract_posts[0]["execution_log"]
        parsed = json.loads(wire)  # 修复前 str(list) 在此炸
        names = [t["function"]["name"] for t in parsed]
        assert names == ["read_file", "write_file"]  # 真实工具序列、契约形态
        # 真实参数随行（读的是真文件路径）
        assert parsed[0]["function"]["arguments"]["path"] == str(f)
        # 旧repr形态对照：确认修复对象确实不可解析（防伪测）
        old_style = str([{"name": "read_file", "arguments": {"path": "a"}}])
        try:
            json.loads(old_style)
            raise AssertionError("旧repr竟然被解析——对照失效")
        except json.JSONDecodeError:
            pass

    def test_no_str_repr_left_in_source(self):
        """防回退护栏：str(tool_calls_log)写法在提炼路径清零（注释行不算代码）"""
        src = (Path(__file__).parent.parent / "agent" / "soulmate_agent.py").read_text()
        code = "\n".join(
            l for l in src.splitlines() if not l.strip().startswith("#"))
        assert "str(tool_calls_log)" not in code
        assert "gene_wire.tool_calls_for_extract" in code

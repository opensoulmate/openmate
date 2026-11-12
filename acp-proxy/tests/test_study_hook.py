"""study_hook 单测（33-StudyLoop §7 evo 钩子）— mock HTTP，不真调 opensoul

覆盖：目标清单解析 / 功能发现提纯（证据 id 支撑+可搬价值评分+inferred 不采信）/
频控（≤3目标、截图预算）/ 超时与不可达静默降级 / _plan prompt 注入 study 发现段落。
"""
import asyncio
import json
import time

import study_hook
from study_hook import (
    StudyClient,
    StudyClientError,
    extract_findings,
    format_study_findings,
    load_targets,
    run_study_round,
)


def make_evidence(eid="ev_test1", operation="web_study", authority="observed",
                  confidence="observed", result=None, params=None):
    return {
        "id": eid,
        "operation": operation,
        "authority": authority,
        "confidence": confidence,
        "limitations": [],
        "params": params if params is not None else {},
        "result": result if result is not None else {},
        "created_at": "2026-11-12T00:00:00+00:00",
    }


class MockStudyClient(StudyClient):
    """mock HTTP 客户端：记录调用、返回预置证据；fail/delay 模拟不可达与慢响应"""

    def __init__(self, records=None, fail=False, delay=0.0):
        self.calls = []
        self.records = records or {}
        self.fail = fail
        self.delay = delay

    async def _respond(self, name, params, default_op, default_result):
        self.calls.append((name, params))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise StudyClientError(f"mock {name} unreachable")
        record = self.records.get(name)
        if record is None:
            record = make_evidence(
                eid=f"ev_{name}_{len(self.calls)}",
                operation=default_op,
                params=params,
                result=default_result,
            )
        return record

    async def web_study(self, url, widths=None):
        return await self._respond(
            "web_study", {"url": url, "widths": widths}, "web_study",
            {"structure": {"headings": ["登录", "注册"]}, "tech_fingerprint": {"framework": "next.js"}},
        )

    async def binary_triage(self, path):
        return await self._respond(
            "binary_triage", {"path": path}, "binary_triage",
            {"format": "PE32+", "dependencies": ["kernel32.dll", "user32.dll"]},
        )

    async def strings_scan(self, path, pattern=None):
        return await self._respond(
            "strings_scan", {"path": path, "pattern": pattern}, "strings_scan",
            {"urls": ["https://api.example.com/v1"]},
        )

    async def image_diff(self, image_a, image_b):
        return await self._respond(
            "image_diff", {"image_a": image_a, "image_b": image_b}, "image_diff",
            {"similarity": 87.5},
        )

    async def get_evidence(self, op=None, limit=20):
        self.calls.append(("get_evidence", {"op": op, "limit": limit}))
        return [make_evidence()]

    async def get_evidence_by_id(self, evidence_id):
        self.calls.append(("get_evidence_by_id", {"id": evidence_id}))
        return make_evidence(eid=evidence_id)


def write_targets(tmp_path, targets):
    path = tmp_path / "study_targets.json"
    path.write_text(json.dumps({"targets": targets}), encoding="utf-8")
    return path


URL_TARGET = {"url": "http://127.0.0.1:3000/login", "learn": "登录页表单布局与校验交互", "mode": "clean_room"}
PATH_DEEP_TARGET = {"path": "/tmp/app.bin", "learn": "依赖库清单", "mode": "deep"}
PATH_CLEAN_TARGET = {"path": "/tmp/site.html", "learn": "页面结构", "mode": "clean_room"}


# ── 目标清单解析 ──

def test_load_targets_parses_valid_entries(tmp_path):
    path = write_targets(tmp_path, [URL_TARGET, PATH_DEEP_TARGET])
    targets, errors = load_targets(path)
    assert errors == []
    assert len(targets) == 2
    assert targets[0]["url"] == URL_TARGET["url"]
    assert targets[0]["learn"] == "登录页表单布局与校验交互"
    assert targets[0]["mode"] == "clean_room"
    assert targets[1]["path"] == "/tmp/app.bin"
    assert targets[1]["mode"] == "deep"


def test_load_targets_skips_invalid_entries(tmp_path):
    path = write_targets(tmp_path, [
        {"url": "http://x/", "path": "/tmp/y", "learn": "z", "mode": "deep"},   # url+path 同给
        {"url": "http://x/", "learn": "", "mode": "deep"},                       # learn 空
        {"url": "http://x/", "learn": "z", "mode": "hacker"},                    # mode 非法
        {"url": "ftp://x/", "learn": "z", "mode": "deep"},                       # url 协议非法
        {"learn": "z", "mode": "deep"},                                          # 缺 url/path
        URL_TARGET,                                                              # 合法
    ])
    targets, errors = load_targets(path)
    assert len(targets) == 1
    assert targets[0]["url"] == URL_TARGET["url"]
    assert len(errors) == 5


def test_load_targets_missing_or_broken_file_never_raises(tmp_path):
    targets, errors = load_targets(tmp_path / "nope.json")
    assert targets == [] and errors
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    targets, errors = load_targets(broken)
    assert targets == [] and errors


def test_example_targets_file_parses():
    targets, errors = load_targets(study_hook.DEFAULT_TARGETS_PATH)
    assert errors == []
    assert len(targets) >= 1
    assert any(t.get("url") == "http://127.0.0.1:3000/login" for t in targets)


# ── 频控 ──

def test_round_caps_targets_at_three(tmp_path):
    targets = [{"url": f"http://127.0.0.1:3000/p{i}", "learn": "页面结构", "mode": "clean_room"} for i in range(5)]
    path = write_targets(tmp_path, targets)
    client = MockStudyClient()
    result = asyncio.run(run_study_round(path, client=client))
    assert len(result["targets_run"]) == 3          # 单轮 ≤3 目标
    assert len(client.calls) == 3
    assert result["status"] == "ok"


def test_screenshot_budget_stops_web_study(tmp_path, monkeypatch):
    monkeypatch.setattr(study_hook, "MAX_SCREENSHOTS_PER_ROUND", 3)
    targets = [dict(URL_TARGET), {"url": "http://127.0.0.1:3000/p2", "learn": "页面结构", "mode": "clean_room"}]
    path = write_targets(tmp_path, targets)
    client = MockStudyClient()
    result = asyncio.run(run_study_round(path, client=client))
    web_calls = [c for c in client.calls if c[0] == "web_study"]
    assert len(web_calls) == 1                       # 第二个目标截图预算耗尽被跳过
    assert any("截图预算" in e for e in result["errors"])


def test_route_url_web_path_triage_deep_adds_strings(tmp_path):
    path = write_targets(tmp_path, [URL_TARGET, PATH_DEEP_TARGET, PATH_CLEAN_TARGET])
    client = MockStudyClient()
    result = asyncio.run(run_study_round(path, client=client))
    names = [c[0] for c in client.calls]
    assert names == ["web_study", "binary_triage", "strings_scan", "binary_triage"]
    assert result["status"] == "ok"


# ── 功能发现提纯 ──

def test_extract_findings_from_web_evidence():
    record = make_evidence(
        eid="ev_abc123", operation="web_study",
        params={"url": "http://127.0.0.1:3000/login"},
        result={"structure": {"headings": ["登录", "注册"]}, "tech_fingerprint": {"framework": "next.js"}},
    )
    findings = extract_findings([record], [URL_TARGET])
    assert findings
    for finding in findings:
        assert finding["finding"]                      # 发现描述
        assert finding["evidence_ids"] == ["ev_abc123"]  # 证据 id 支撑
        assert 0.0 <= finding["portability_score"] <= 5.0  # 可搬价值评分
        assert finding["source"] == "study"
        assert finding["learn"] == URL_TARGET["learn"]
        assert finding["mode"] == "clean_room"
    joined = " ".join(f["finding"] for f in findings)
    assert "next.js" in joined and "登录" in joined


def test_extract_findings_rejects_inferred_authority():
    inferred = make_evidence(eid="ev_inf", authority="inferred",
                             params={"url": "http://127.0.0.1:3000/login"},
                             result={"features": ["神奇功能"]})
    observed = make_evidence(eid="ev_obs", authority="observed",
                             params={"url": "http://127.0.0.1:3000/login"},
                             result={"features": ["真实功能"]})
    findings = extract_findings([inferred, observed], [URL_TARGET])
    all_ids = [i for f in findings for i in f["evidence_ids"]]
    assert "ev_inf" not in all_ids                     # inferred 不得进 findings（§3 铁律）
    assert "ev_obs" in all_ids


def test_extract_findings_dedup_merges_evidence_ids():
    result = {"features": ["双因素登录"]}
    records = [
        make_evidence(eid="ev_1", params={"url": "http://127.0.0.1:3000/login"}, result=result),
        make_evidence(eid="ev_2", params={"url": "http://127.0.0.1:3000/login"}, result=result),
    ]
    findings = extract_findings(records, [URL_TARGET])
    assert len(findings) == 1
    assert sorted(findings[0]["evidence_ids"]) == ["ev_1", "ev_2"]


def test_extract_findings_fallback_when_result_empty():
    record = make_evidence(eid="ev_plain", operation="web_study",
                           params={"url": "http://127.0.0.1:3000/login"}, result={})
    findings = extract_findings([record], [URL_TARGET])
    assert len(findings) == 1
    assert findings[0]["evidence_ids"] == ["ev_plain"]
    assert "web_study" in findings[0]["finding"]


def test_extract_findings_skips_empty_and_noisy_subfields():
    record = make_evidence(
        eid="ev_noise", operation="web_study",
        params={"url": "http://127.0.0.1:3000/login"},
        result={"headings": {"h1": ["OpenMate"], "h2": [], "h3": []},
                "structure_tree": {"tag": "body", "children": [{"tag": "div"} for _ in range(50)]}},
    )
    findings = extract_findings([record], [URL_TARGET])
    joined = " ".join(f["finding"] for f in findings)
    assert "h1" in joined and "OpenMate" in joined
    assert "h2" not in joined and "h3" not in joined   # 空子字段不构成发现
    assert "children" not in joined                    # 噪声字段不进 findings


def test_format_study_findings_marks_source_and_evidence():
    findings = [{
        "finding": "登录页具备双因素登录", "evidence_ids": ["ev_x1"],
        "portability_score": 4.2, "source": "study",
    }]
    text = format_study_findings(findings)
    assert "study 发现" in text
    assert "来源：study" in text
    assert "ev_x1" in text
    assert "4.2/5" in text
    assert format_study_findings([]) == ""


# ── 超时/容错静默降级 ──

def test_unreachable_opensoul_degrades_silently(tmp_path):
    path = write_targets(tmp_path, [URL_TARGET])
    client = MockStudyClient(fail=True)
    result = asyncio.run(run_study_round(path, client=client))   # 不抛异常
    assert result["status"] == "degraded"
    assert result["findings"] == []
    assert any("unreachable" in e for e in result["errors"])


def test_round_timeout_degrades_and_bounded(tmp_path):
    path = write_targets(tmp_path, [URL_TARGET])
    client = MockStudyClient(delay=2.0)
    started = time.monotonic()
    result = asyncio.run(run_study_round(path, client=client, round_timeout=0.2))
    elapsed = time.monotonic() - started
    assert elapsed < 5.0                            # 单轮超时预算生效，不挂死
    assert result["status"] == "degraded"
    assert any("超时" in e for e in result["errors"])


def test_run_study_round_never_raises_on_broken_targets(tmp_path):
    broken = tmp_path / "study_targets.json"
    broken.write_text("{{{", encoding="utf-8")
    result = asyncio.run(run_study_round(broken, client=MockStudyClient()))
    assert result["status"] == "degraded"
    assert result["findings"] == []
    assert result["errors"]


# ── evolution_pipeline 接线 ──

class FakeStrand:
    def __init__(self):
        self.prompts = []

    async def _call_llm(self, prompt):
        self.prompts.append(prompt)
        return json.dumps({"changes": [], "risk_assessment": "", "test_plan": ""})

    def _log(self, *args, **kwargs):
        pass


def test_plan_prompt_contains_study_findings_section(tmp_path):
    import evolution_pipeline

    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "evo_feedback.json").write_text(
        json.dumps([{"verdict": "reject", "reason": "反馈样例"}]), encoding="utf-8",
    )
    strand = FakeStrand()
    pipeline = evolution_pipeline.EvolutionPipeline(strand, tmp_path, "test")
    pipeline._study_findings = [{
        "finding": "[web_study] http://127.0.0.1:3000/login｜双因素登录",
        "evidence_ids": ["ev_real1"],
        "portability_score": 4.2,
        "source": "study",
    }]
    asyncio.run(pipeline._plan({"root_cause": "测试根因", "affected_files": [], "fix_strategy": ""}))
    prompt = strand.prompts[0]
    assert "study 发现" in prompt                    # study 发现段落
    assert "来源：study" in prompt                    # 标明来源 study
    assert "双因素登录" in prompt and "ev_real1" in prompt
    assert "Hermes最近审核反馈" in prompt            # 与 _load_recent_feedback 并列（不替换）
    assert "测试根因" in prompt


def test_plan_prompt_omits_study_section_without_findings(tmp_path):
    import evolution_pipeline

    strand = FakeStrand()
    pipeline = evolution_pipeline.EvolutionPipeline(strand, tmp_path, "test")
    asyncio.run(pipeline._plan({"root_cause": "x", "affected_files": [], "fix_strategy": ""}))
    assert "study 发现" not in strand.prompts[0]


def test_observe_study_degrades_instead_of_breaking(tmp_path, monkeypatch):
    import evolution_pipeline
    import study_hook as sh

    async def async_boom(*args, **kwargs):
        raise RuntimeError("opensoul connection refused")

    monkeypatch.setattr(sh, "run_study_round", async_boom)
    pipeline = evolution_pipeline.EvolutionPipeline(FakeStrand(), tmp_path, "test")
    result = asyncio.run(pipeline._observe_study())   # 不抛异常
    assert result["status"] == "degraded"
    assert result["findings"] == []
    assert result["errors"]


def test_observe_study_wires_findings_into_pipeline_state(tmp_path, monkeypatch):
    import evolution_pipeline
    import study_hook as sh

    canned = {"status": "ok", "findings": [{"finding": "f", "evidence_ids": ["ev_1"], "portability_score": 3.0, "source": "study"}],
              "targets_run": ["x"], "evidence_ids": ["ev_1"], "errors": []}

    async def fake_round(*args, **kwargs):
        return dict(canned)

    monkeypatch.setattr(sh, "run_study_round", fake_round)
    pipeline = evolution_pipeline.EvolutionPipeline(FakeStrand(), tmp_path, "test")
    result = asyncio.run(pipeline._observe_study())
    assert result["findings"] == canned["findings"]
    assert pipeline._format_study_section() != ""

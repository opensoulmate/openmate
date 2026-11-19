"""P0 技能装载验证（skill_load_verify）测试。

覆盖：四态判定（all/partial/none/not_applicable）、worst-of 汇总、收据 jsonl
追加与读回、缺文件容缺、纯函数不猜（查不到就是 none_loaded）。
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.skill_load_verify import (  # noqa: E402
    STATE_ALL_LOADED,
    STATE_NONE_LOADED,
    STATE_NOT_APPLICABLE,
    STATE_PARTIALLY_LOADED,
    _TRUNCATED_MARK,
    overall_state,
    read_receipts,
    verify_loaded,
    write_receipt,
)

SKILL = {"id": "s1", "name": "pdf-skill", "content": "用read_file解析PDF"}
BODY = "\n### pdf-skill\n用read_file解析PDF\n"


class TestVerifyLoaded:
    def test_no_skills_not_applicable(self):
        assert verify_loaded([], "任何段") == []

    def test_all_loaded_with_bodies(self):
        states = verify_loaded([SKILL], "\n\n## 相关技能\n" + BODY, [("pdf-skill", BODY)])
        assert states[0]["state"] == STATE_ALL_LOADED
        assert overall_state(states) == STATE_ALL_LOADED

    def test_partial_when_truncated_mark(self):
        body = "\n### pdf-skill\n用read" + _TRUNCATED_MARK + "\n"
        states = verify_loaded([SKILL], body, [("pdf-skill", body)])
        assert states[0]["state"] == STATE_PARTIALLY_LOADED

    def test_none_when_absent_from_section(self):
        states = verify_loaded([SKILL], "\n\n## 相关技能\n### 别的技能\nxxx\n", [])
        assert states[0]["state"] == STATE_NONE_LOADED

    def test_none_on_empty_section(self):
        states = verify_loaded([SKILL], "", [])
        assert states[0]["state"] == STATE_NONE_LOADED

    def test_marker_fallback_without_bodies(self):
        section = "\n\n## 相关技能\n" + BODY
        states = verify_loaded([SKILL], section, None)
        assert states[0]["state"] == STATE_ALL_LOADED

    def test_overall_worst_of(self):
        states = [
            {"state": STATE_ALL_LOADED},
            {"state": STATE_NONE_LOADED},
            {"state": STATE_PARTIALLY_LOADED},
        ]
        assert overall_state(states) == STATE_NONE_LOADED
        assert overall_state([{"state": STATE_ALL_LOADED}]) == STATE_ALL_LOADED
        assert overall_state([]) == STATE_NOT_APPLICABLE


class TestReceipt:
    def test_write_and_read_receipt(self, tmp_path):
        path = str(tmp_path / "receipts.jsonl")
        states = [{"skill_id": "s1", "name": "pdf-skill", "state": STATE_ALL_LOADED}]
        receipt = write_receipt("sess-1", states, section_tokens=42, path=path)
        assert receipt["overall"] == STATE_ALL_LOADED
        rows = read_receipts(path=path)
        assert len(rows) == 1
        assert rows[0]["session_id"] == "sess-1"
        assert rows[0]["skills"][0]["name"] == "pdf-skill"
        assert rows[0]["section_tokens"] == 42

    def test_append_multiple(self, tmp_path):
        path = str(tmp_path / "receipts.jsonl")
        for i in range(3):
            write_receipt(f"s{i}", [], path=path)
        assert len(read_receipts(path=path)) == 3

    def test_extra_fields_merge(self, tmp_path):
        path = str(tmp_path / "receipts.jsonl")
        write_receipt("s", [], extra={"over_budget": [{"name": "x"}]}, path=path)
        row = read_receipts(path=path)[0]
        assert row["over_budget"] == [{"name": "x"}]

    def test_missing_file_empty(self, tmp_path):
        assert read_receipts(path=str(tmp_path / "none.jsonl")) == []

    def test_receipt_is_json_lines(self, tmp_path):
        path = tmp_path / "receipts.jsonl"
        write_receipt("s", [], path=str(path))
        for line in path.read_text(encoding="utf-8").splitlines():
            json.loads(line)  # 每行都是合法 JSON

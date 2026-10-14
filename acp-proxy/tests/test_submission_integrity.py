"""提交物打包层自验证测试（周期#347 拍板"自验证修复待办"验收）

验证标准（用户拍板原文）：Grader 收到完整 3 项变更且逐项可验证。
根因（已修）：打包/序列化层 content[:500] 截断（非 evolution_v2 采集层）。
修法：①打包层全量保留（package_applied_record）②提交前完整性校验+截断检测函数
（verify_submission_integrity）③Grader 视图优先完整增量 edits，禁静默截断冒充完整。
"""
import asyncio

import evolution_grader
from evolution_v2 import package_applied_record, verify_submission_integrity


def _make_change(i: int) -> dict:
    return {
        "target_file": f"pkg_mod_{i}.py",
        "description": f"变更{i}：修复某函数逻辑",
        "requirements": f"要求{i}",
    }


def _make_content(i: int, size: int = 1200) -> str:
    # 每项内容远超旧打包上限500字，确保旧形态必然截断
    return f"# mod {i}\n" + (f"VALUE_{i} = '{'x' * size}'\n") + f"def f{i}():\n    return {i}\n"


class TestPackageAppliedRecord:
    def test_full_content_no_truncation(self):
        """打包层全量保留：3项变更每项>500字全部进记录（旧content[:500]回归）"""
        recs = []
        for i in range(1, 4):
            content = _make_content(i)
            rec = package_applied_record(_make_change(i), content)
            recs.append(rec)
            assert rec["content"] == content, f"变更{i} content 被截断"
            assert rec["content_len"] == len(content)
            assert rec["truncated"] is False
            assert len(rec["content"]) > 500, "样本必须大于旧截断上限500"
            assert rec["content_sha256"]
        assert len(recs) == 3

    def test_edits_recorded(self):
        """增量edits（old/new成对）完整进记录=逐项可核的真diff"""
        edits = [{"old_string": "return a - b", "new_string": "return a + b"}]
        rec = package_applied_record(_make_change(1), _make_content(1), edits)
        assert rec["edits"] == edits


class TestVerifySubmissionIntegrity:
    def _write_disk(self, tmp_path, i, content):
        p = tmp_path / f"pkg_mod_{i}.py"
        p.write_text(content, encoding="utf-8")
        return p

    def test_three_complete_items_pass(self, tmp_path):
        """验证标准：完整3项变更且逐项可验证"""
        recs = []
        for i in range(1, 4):
            content = _make_content(i)
            self._write_disk(tmp_path, i, content)
            recs.append(package_applied_record(
                _make_change(i), content,
                [{"old_string": f"old{i}", "new_string": f"new{i}"}],
            ))
        report = verify_submission_integrity(recs, tmp_path)
        assert report["complete"] is True
        assert len(report["items"]) == 3
        assert all(item["ok"] for item in report["items"]), report
        assert report["truncated_items"] == []
        assert report["mismatched_items"] == []
        assert "3/3项完整" in report["summary"]

    def test_legacy_500_truncation_detected(self, tmp_path):
        """旧content[:500]截断打包形态必须被截断检测函数识破"""
        content = _make_content(1)
        self._write_disk(tmp_path, 1, content)
        legacy_rec = {**_make_change(1), "status": "applied", "content": content[:500]}
        report = verify_submission_integrity([legacy_rec], tmp_path)
        assert report["complete"] is False
        assert report["truncated_items"] == ["pkg_mod_1.py"]
        assert report["items"][0]["checks"]["len_matches"] is False

    def test_truncation_flag_detected(self, tmp_path):
        content = _make_content(1)
        self._write_disk(tmp_path, 1, content)
        rec = package_applied_record(_make_change(1), content)
        rec["truncated"] = True
        report = verify_submission_integrity([rec], tmp_path)
        assert report["complete"] is False
        assert report["items"][0]["checks"]["no_truncation_flag"] is False

    def test_disk_mismatch_detected(self, tmp_path):
        rec = package_applied_record(_make_change(1), _make_content(1))
        self._write_disk(tmp_path, 1, _make_content(1) + "# 磁盘上多了一行\n")
        report = verify_submission_integrity([rec], tmp_path)
        assert report["complete"] is False
        assert report["mismatched_items"] == ["pkg_mod_1.py"]

    def test_incomplete_edit_detected(self, tmp_path):
        content = _make_content(1)
        self._write_disk(tmp_path, 1, content)
        rec = package_applied_record(_make_change(1), content, [{"old_string": "a", "new_string": ""}])
        report = verify_submission_integrity([rec], tmp_path)
        assert report["complete"] is False
        assert report["items"][0]["checks"]["edits_complete"] is False

    def test_empty_applied_not_complete(self, tmp_path):
        report = verify_submission_integrity([], tmp_path)
        assert report["complete"] is False


class _FakeStrand:
    def __init__(self):
        self.prompts = []

    async def _call_llm(self, prompt):
        self.prompts.append(prompt)
        return '{"verdict": "satisfied", "reason": "ok", "issues": []}'


class TestGraderReceivesCompleteSubmission:
    def test_grader_gets_all_three_items_verifiable(self, tmp_path):
        """Grader 收到完整 3 项变更且逐项可验证（用户验证标准原样断言）"""
        recs = []
        for i in range(1, 4):
            content = _make_content(i)
            (tmp_path / f"pkg_mod_{i}.py").write_text(content, encoding="utf-8")
            recs.append(package_applied_record(
                _make_change(i), content,
                [{"old_string": f"def f{i}():", "new_string": f"def f{i}_fixed():"}],
            ))
        report = verify_submission_integrity(recs, tmp_path)
        assert report["complete"] is True

        strand = _FakeStrand()
        result = asyncio.run(evolution_grader.grade_code(strand, recs, {"changes": [_make_change(i) for i in (1, 2, 3)]}))
        assert result["verdict"] == "satisfied"
        prompt = strand.prompts[0]
        for i in range(1, 4):
            # 每项变更的完整增量diff（old/new 全文）都进了 Grader 输入
            assert f"def f{i}():" in prompt, f"变更{i} old_string 未完整到达 Grader"
            assert f"def f{i}_fixed():" in prompt, f"变更{i} new_string 未完整到达 Grader"
            assert f"pkg_mod_{i}.py" in prompt
        assert "提交物不完整" not in prompt

    def test_grader_flags_truncated_submission(self):
        """截断记录进 Grader 时必须显式标注，禁静默截断冒充完整提交物"""
        content = _make_content(1)
        legacy_rec = {**_make_change(1), "status": "applied", "content": content[:500], "content_len": len(content)}
        strand = _FakeStrand()
        asyncio.run(evolution_grader.grade_code(strand, [legacy_rec], {"changes": [_make_change(1)]}))
        assert "提交物不完整" in strand.prompts[0]

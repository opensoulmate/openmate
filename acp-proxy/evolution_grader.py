"""独立Grader — 三态判定，替代自评自审

借鉴：
- deepagents RubricMiddleware: 独立grader子agent，三态判定（satisfied/needs_revision/failed）
- SWE-agent: 评审只看提交物不看过程
- CAMEL: 程序化验证器优先于LLM裁判

核心改变：评审用独立上下文（不是生成代码的那个LLM调用），
且评审失败=拒绝（去掉default-approve）。
"""
import json
import logging

logger = logging.getLogger("evolution-grader")

# 三态判定
VERDICT_SATISFIED = "satisfied"        # 通过
VERDICT_NEEDS_REVISION = "needs_revision"  # 需要修改（可重试）
VERDICT_FAILED = "failed"              # 失败（不可重试）

GRADER_SYSTEM_PROMPT = """你是一个严格的代码审查专家。你的职责是独立评审代码变更。

你必须：
1. 只看提交物（diff/代码变更），不看生成过程
2. 严格按照评审标准打分
3. 给出三态判定：satisfied / needs_revision / failed
4. 如果无法解析输入或信息不足，判定为failed（不能默认通过）

评审标准：
- 正确性：代码逻辑是否正确，是否引入bug
- 安全性：是否有注入、越权、敏感信息泄露风险
- 最小改动：是否只改了必要的部分，没有过度重构
- 协议对齐：跨系统通信的字段名/格式是否与对端一致
"""

# 提案级评审（proposal_review阶段）— 评审文字提案，不是代码diff
# 修复evo停滞根因：plan阶段产物是描述性提案，不能拿代码diff标准审它（46/46全拒的死锁根源）
PROPOSAL_REVIEW_SYSTEM_PROMPT = """你是一个严格的改进提案评审专家。当前评审阶段是proposal_review——
评审对象是【修复/改进提案】（文字描述），不是代码diff。代码实现尚未发生，将在提案通过后的implement阶段进行。

⚠️ 禁止以"缺少代码diff/实际代码变更"为由拒绝提案——提案阶段本来就没有代码。
⚠️ 代码正确性/安全性由后续code_review阶段对实际diff评审，不是本阶段职责。

提案评审标准：
- 目标明确性：问题描述和改进目标是否清晰，是否对应具体失败模式或改进机会
- 可行性：提出的改法在技术上是否合理，步骤是否可执行
- 最小改动：方案是否只改必要的部分，没有过度设计
- 具体性：是否指明了target_file和具体改动点（不允许空泛的"优化系统"式提案）
- 风险意识：risk_assessment是否有效，是否识别了主要风险
- 协议对齐：涉及跨系统的改动是否考虑了对端契约

三态判定：satisfied / needs_revision / failed
"""


async def grade_plan(strand, plan: dict, failure_context: str = "") -> dict:
    """评审修复方案（proposal_review阶段）— 评审文字提案用提案级标准，修复评审门错位死锁

    返回: {"verdict": "satisfied"|"needs_revision"|"failed", "reason": "...", "issues": [...]}
    """
    changes = plan.get("changes", [])
    
    if not changes:
        return {
            "verdict": VERDICT_FAILED,
            "reason": "方案没有提供任何代码修改（changes为空）",
            "issues": ["empty_changes"],
        }

    if not plan.get("risk_assessment") or plan["risk_assessment"] == "parse error":
        return {
            "verdict": VERDICT_FAILED,
            "reason": "风险评估无效或缺失",
            "issues": ["invalid_risk_assessment"],
        }

    prompt = f"""{PROPOSAL_REVIEW_SYSTEM_PROMPT}

评审以下修复方案（提案阶段产物）：

方案：
{json.dumps(plan, ensure_ascii=False, indent=2)[:3000]}

{"历史失败上下文（必须考虑）：" + failure_context if failure_context else ""}

输出JSON：
{{"verdict": "satisfied"|"needs_revision"|"failed", "reason": "判定原因", "issues": ["问题1", "问题2"], "suggestions": ["建议1"]}}"""

    result = await strand._call_llm(prompt)
    return _parse_grader_output(result, stage="plan")


async def grade_code(strand, applied: list[dict], original_plan: dict) -> dict:
    """评审代码质量 — 独立grader，只看提交物
    
    返回: {"verdict": "satisfied"|"needs_revision"|"failed", "reason": "...", "issues": [...]}
    """
    if not applied:
        return {
            "verdict": VERDICT_FAILED,
            "reason": "没有应用任何补丁",
            "issues": ["no_patches"],
        }

    # 只展示diff（提交物），不展示生成过程（借鉴SWE-agent ReviewSubmission）
    # code_review评审输入扩容(evo修复④): 每补丁800字截断只给评审者看文件开头几行→
    # "代码变更信息不完整"误拒(cycle237实证)。补丁展示2500字/8个，评审总预算12000。
    # 自验证修复(#347拍板): 提交物视图优先给完整增量edits(old/new成对=真diff逐项可核)，
    # 整文件content超预算时显式标注"仅展示前N字/共M字"（禁静默截断冒充完整提交物）。
    def _submission_view(p: dict) -> str:
        edits = p.get("edits") or []
        target = p.get("target_file", "?")
        if edits:
            parts = [f"--- {target} (增量修改 {len(edits)} 处，完整diff) ---"]
            for i, e in enumerate(edits, 1):
                parts.append(
                    f"[edit {i}] old_string:\n{e.get('old_string', '')}\n"
                    f"[edit {i}] new_string:\n{e.get('new_string', '')}"
                )
            return "\n".join(parts)
        content = p.get("content", "")
        total = p.get("content_len", len(content))
        if len(content) < total:
            # 记录本身被截断（旧打包形态）——如实标注，不冒充完整
            return (
                f"--- {target} (⚠️ 提交物不完整：仅{len(content)}/共{total}字，"
                f"打包层截断) ---\n{content}"
            )
        if len(content) > 2500:
            return f"--- {target} (新文件，共{total}字，仅展示前2500字) ---\n{content[:2500]}"
        return f"--- {target} (新文件，共{total}字，完整) ---\n{content}"

    diff_summary = "\n".join([_submission_view(p) for p in applied[:8]])

    prompt = f"""{GRADER_SYSTEM_PROMPT}

评审以下代码变更（只看提交物）：

变更：
{diff_summary[:12000]}

原始方案要求：
{json.dumps(original_plan.get('changes', []), ensure_ascii=False)[:1000]}

检查项：
1. 代码是否实现了方案要求的功能
2. 是否引入明显bug或安全风险
3. 是否符合最小改动原则
4. 跨系统协议字段是否与对端一致

输出JSON：
{{"verdict": "satisfied"|"needs_revision"|"failed", "reason": "判定原因", "issues": ["问题1"], "suggestions": ["建议1"]}}"""

    result = await strand._call_llm(prompt)
    return _parse_grader_output(result, stage="code")


async def grade_integration_test_result(strand, test_result: dict) -> dict:
    """评审集成测试结果 — 程序化验证优先（借鉴CAMEL）
    
    集成测试结果是程序化的（通过/不通过），不需要LLM裁判。
    这里只做汇总和判定。
    """
    passed = test_result.get("passed", False)
    score = test_result.get("score", 0)
    tests = test_result.get("tests", [])

    failed_tests = [t for t in tests if not t["passed"]]

    if passed:
        return {
            "verdict": VERDICT_SATISFIED,
            "reason": f"集成测试全部通过 ({sum(1 for t in tests if t['passed'])}/{len(tests)})",
            "failed_tests": [],
        }
    else:
        # 有失败的测试 → needs_revision（可以重试）
        # 但如果核心测试（ws-send-receive）失败 → failed（不可重试）
        core_failed = any(
            t["name"] == "ws-send-receive" and not t["passed"]
            for t in tests
        )
        
        if core_failed:
            return {
                "verdict": VERDICT_FAILED,
                "reason": f"核心功能测试失败（WS消息收发）",
                "failed_tests": [{"name": t["name"], "detail": t["detail"]} for t in failed_tests],
            }
        
        return {
            "verdict": VERDICT_NEEDS_REVISION,
            "reason": f"{len(failed_tests)}个测试失败",
            "failed_tests": [{"name": t["name"], "detail": t["detail"]} for t in failed_tests],
        }


def _parse_grader_output(result: str, stage: str) -> dict:
    """解析grader输出 — 解析失败=拒绝（去掉default-approve）"""
    try:
        clean = result.strip()
        if clean.startswith("```"):
            lines = clean.split("\n")
            clean = "\n".join(lines[1:-1]) if len(lines) > 2 else clean
        
        parsed = json.loads(clean)
        
        verdict = parsed.get("verdict", "")
        if verdict not in (VERDICT_SATISFIED, VERDICT_NEEDS_REVISION, VERDICT_FAILED):
            # 无效verdict → failed
            logger.warning(f"Grader returned invalid verdict '{verdict}' for {stage}")
            return {
                "verdict": VERDICT_FAILED,
                "reason": f"Grader返回无效verdict: {verdict}",
                "issues": ["invalid_verdict"],
            }
        
        return {
            "verdict": verdict,
            "reason": parsed.get("reason", "")[:300],
            "issues": parsed.get("issues", []),
            "suggestions": parsed.get("suggestions", []),
        }
    
    except json.JSONDecodeError:
        # 解析失败 = 拒绝（核心改进！之前是default-approve）
        logger.warning(f"Grader output parse failed for {stage}: {result[:200]}")
        return {
            "verdict": VERDICT_FAILED,
            "reason": f"Grader输出解析失败（不能默认通过）: {result[:100]}",
            "issues": ["parse_failed"],
        }

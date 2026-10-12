"""进化引擎 v2 — 整合失败记忆+集成测试+独立grader

核心改进（基于100-agent调研）：
1. 真实集成测试：WS发消息→必须收到AI回复（不再是health check ping）
2. 失败记忆：记录每次失败，规划时注入历史失败上下文（借鉴mem0审计表+CAMEL工作流记忆）
3. 独立grader：三态判定，解析失败=拒绝（借鉴DeepAgents RubricMiddleware）
4. 错误风暴熔断：连续同错即停（借鉴agno）
5. 程序化验证优先：集成测试结果不需要LLM裁判（借鉴CAMEL）
"""
import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import failure_memory
import evolution_grader
import integration_test
from contract_registry import check_contracts, get_protected_files
from branch_evolution import BranchManager
from global_awareness import get_global_awareness

logger = logging.getLogger("evolution-v2")

# 配置
MAX_RETRIES = 2                # 重试次数（从3降到2，因为有失败记忆了）
PLATEAU_ROUNDS = 3


class EvolutionV2:
    """进化引擎 v2"""
    
    def __init__(self, strand, repo_root: Path, instance_id: str):
        self.strand = strand
        self.repo_root = repo_root
        self.instance_id = instance_id
        self.data_dir = repo_root / "acp-proxy" / "data"
        
        # 收敛追踪
        self._score_history: list[float] = []
        self._consecutive_no_improve = 0

        # ── ALPHA_DONE 状态追踪：原子写锁（状态读写串行化）──
        self._state_lock = asyncio.Lock()  # 所有 status 读写必须持锁
        self._verify_sub_items = (
            "code_change_exists",        # 代码变更存在性检查
            "tests_passed",              # 测试通过检查
            "non_doc_file_changed",      # 非文档文件检查
        )

    # ══════════════════════════════════════════════════════════════
    # ALPHA_DONE 状态一致性保障（增量新增）
    # ══════════════════════════════════════════════════════════════

    async def sync_verification_state(self, item: dict, verdict: str,
                                      sub_results: dict | None = None) -> dict:
        """(a) 验证结果 → status 原子性同步 + (b) ALPHA_DONE 前置断言

        verdict: "PASS" | "FAIL" | "BLOCKED"
        sub_results: {子项名: "PASS"|"FAIL"|"BLOCKED"}
        返回 {"synced": bool, "deviation": bool, "detail": str}
        """
        sub_results = sub_results or {}
        async with self._state_lock:          # (d) 事务语义：持锁读改写
            old_status = item.get("status", "UNKNOWN")

            # ── (b) 状态一致性断言：任一子项 FAIL ⇒ 禁止 ALPHA_DONE ──
            failing = [
                name for name in self._verify_sub_items
                if sub_results.get(name, "PASS") != "PASS"
            ]
            want_done = (verdict == "PASS" and not failing)

            if verdict == "PASS" and failing:
                # 判定 PASS 但子项含 FAIL → 视为 FAIL（消除第3次复现的偏差）
                verdict = "FAIL"
                want_done = False
                self._record_state_deviation(
                    item, old_status,
                    f"verdict=PASS 但子项FAIL: {failing}",
                )

            if want_done:
                item["status"] = "ALPHA_DONE"
            elif verdict in ("FAIL", "BLOCKED"):
                item["status"] = "FAILED" if verdict == "FAIL" else "BLOCKED"

            # ── (c) 状态回滚：已 ALPHA_DONE 但验证含 FAIL → 强制回退 ──
            deviation = False
            if old_status == "ALPHA_DONE" and (verdict != "PASS" or failing):
                item["status"] = "FAILED"
                self._record_state_deviation(
                    item, old_status,
                    f"验证={verdict}, failing_sub_items={failing or 'none'} → 回退 FAILED",
                )
                deviation = True

            item["verify_verdict"] = verdict
            item["verify_sub_results"] = sub_results
            item["status_updated_at"] = datetime.now(timezone.utc).isoformat()

            return {
                "synced": True,
                "deviation": deviation,
                "detail": f"{old_status} → {item['status']} (verdict={verdict})",
            }

    async def verify_and_mark_done(self, item: dict, verify_fn) -> dict:
        """验证器包装：执行 verify_fn 后统一走一致性同步。
        verify_fn 返回 {"verdict": str, "sub_results": dict} 或 (verdict, sub_results)
        """
        try:
            raw = await verify_fn(item) if asyncio.iscoroutinefunction(verify_fn) else verify_fn(item)
        except Exception as exc:
            raw = {"verdict": "BLOCKED", "sub_results": {}, "error": str(exc)}

        if isinstance(raw, tuple):
            verdict, sub_results = raw
        else:
            verdict = raw.get("verdict", "FAIL")
            sub_results = raw.get("sub_results", {})

        sync = await self.sync_verification_state(item, verdict, sub_results)
        sync["verify_raw"] = raw if isinstance(raw, dict) else {"verdict": verdict}
        return sync

    def _record_state_deviation(self, item: dict, old_status: str, reason: str):
        """(3) 记录 STATE_DEVIATION 事件（写入失败记忆 + 本地审计）"""
        event = {
            "type": "STATE_DEVIATION",
            "ts": datetime.now(timezone.utc).isoformat(),
            "instance_id": self.instance_id,
            "item_id": item.get("id", "unknown"),
            "old_status": old_status,
            "new_status": item.get("status"),
            "reason": reason,
        }
        try:
            failure_memory.record(event)
        except Exception:
            pass
        try:
            audit_path = self.data_dir / "state_deviation.jsonl"
            self.data_dir.mkdir(parents=True, exist_ok=True)
            with audit_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.warning("state deviation audit write failed: %s", exc)
        self.strand._log("deviation", f"⚠️ STATE_DEVIATION: {reason[:80]}")

    async def rollback_alpha_done_if_invalid(self, item: dict) -> bool:
        """事后校验入口：对已标记 ALPHA_DONE 的条目做一致性复查并必要时回退。"""
        async with self._state_lock:
            if item.get("status") != "ALPHA_DONE":
                return False
            sub = item.get("verify_sub_results", {}) or {}
            failing = [n for n in self._verify_sub_items if sub.get(n, "PASS") != "PASS"]
            if item.get("verify_verdict") == "PASS" and not failing:
                return False
            item["status"] = "FAILED"
            self._record_state_deviation(
                item, "ALPHA_DONE",
                f"事后复查不一致: verdict={item.get('verify_verdict')}, failing={failing or 'none'}",
            )
            return True
    
    async def run(self, failure_batch: list[dict]) -> dict:
        """执行完整的进化流程"""
        round_id = f"evo_v2_{int(time.time())}_{self.instance_id}"
        result = {"round_id": round_id, "stages": {}, "success": False}
        
        start_time = time.time()
        
        try:
            # ── 阶段0: 错误风暴熔断检查 ──
            for f in failure_batch[:3]:
                sig = self._error_signature(f)
                if failure_memory.check_error_storm(sig):
                    result["stages"]["storm_check"] = {
                        "status": "blocked",
                        "reason": f"错误风暴熔断: {sig[:80]}",
                    }
                    self.strand._log("storm", f"🛑 错误风暴熔断: {sig[:60]}")
                    return result
            
            result["stages"]["storm_check"] = {"status": "ok"}
            
            # ── 阶段0.5: 全局感知 ──
            ga = get_global_awareness(self.repo_root)
            system_state = ga.get_system_state()
            suggestions = ga.suggest_target(system_state)
            result["stages"]["global_awareness"] = {
                "modules": len(system_state.get("modules", [])),
                "dependencies": len(system_state.get("dependencies", {})),
                "tech_debt": len(system_state.get("tech_debt", [])),
                "suggestions": suggestions.get("suggestions", [])[:5],
            }
            self.strand._log("global", f"🌍 全局感知: {len(system_state.get('modules', []))}模块, {len(system_state.get('tech_debt', []))}个技术债务")
            
            # ── 阶段1: 诊断 ──
            diagnosis = await self._locate(failure_batch)
            result["stages"]["locate"] = diagnosis
            self.strand._log("locate", f"🔍 诊断: {diagnosis.get('root_cause', 'unknown')[:80]}")
            
            if not diagnosis.get("root_cause"):
                result["stages"]["locate"]["status"] = "no_root_cause"
                return result
            
            # ── 阶段2: 规划（注入失败记忆+全局视角）──
            affected_files = diagnosis.get("affected_files", [])
            failure_context = failure_memory.get_failure_context_for_planning(affected_files)
            
            # 注入全局视角：让规划时知道系统整体状态
            global_context = f"\n\n【全局系统状态】\n"
            global_context += f"- 系统共{len(system_state.get('modules', []))}个模块\n"
            global_context += f"- 技术债务: {len(system_state.get('tech_debt', []))}个标记\n"
            if suggestions.get("suggestions"):
                global_context += f"- 推荐关注: {suggestions['suggestions'][0].get('target', '')} ({suggestions['suggestions'][0].get('reason', '')})\n"
            
            plan = await self._plan(diagnosis, failure_context + global_context)
            result["stages"]["plan"] = plan
            self.strand._log("plan", f"📋 方案: {len(plan.get('changes', []))}个文件")
            
            # ── 阶段3: 独立grader评审方案（三态判定）──
            plan_review = await evolution_grader.grade_plan(self.strand, plan, failure_context)
            result["stages"]["plan_review"] = plan_review
            self.strand._log("plan_review", f"{'✅' if plan_review['verdict'] == 'satisfied' else '❌'} 方案评审: {plan_review.get('reason', '')[:60]}")
            
            if plan_review["verdict"] == evolution_grader.VERDICT_FAILED:
                self._record_failure(round_id, "plan_review", affected_files, plan, plan_review)
                return result
            
            if plan_review["verdict"] == evolution_grader.VERDICT_NEEDS_REVISION:
                # 可以重试
                if failure_context:
                    plan = await self._plan(diagnosis, failure_context + f"\n\n上次评审意见: {plan_review.get('reason', '')}")
                    plan_review = await evolution_grader.grade_plan(self.strand, plan, failure_context)
                    result["stages"]["plan_review_v2"] = plan_review
                    if plan_review["verdict"] != evolution_grader.VERDICT_SATISFIED:
                        self._record_failure(round_id, "plan_review", affected_files, plan, plan_review)
                        return result
            
            # ── 阶段3.5: 契约注册表检查 ──
            target_files = [c.get("target_file", "") for c in plan.get("changes", [])]
            protected = get_protected_files()
            touching_protected = [f for f in target_files if f in protected]
            if touching_protected:
                contract_check = check_contracts(self.repo_root, target_files)
                result["stages"]["contract_check"] = contract_check
                if not contract_check["passed"]:
                    violations = "; ".join(v["reason"] for v in contract_check["violations"][:3])
                    self.strand._log("contract", f"🚫 契约违规: {violations[:80]}")
                    self._record_failure(round_id, "contract", target_files, plan, {"reason": violations})
                    return result
                self.strand._log("contract", f"✅ 契约检查通过({contract_check['checked']}个)")
            
            # ── 阶段3.8: 创建实验分支 ──
            bm = BranchManager(self.repo_root)
            exp = bm.create_experiment_branch(f"round-{round_id}")
            if not exp:
                result["stages"]["branch"] = {"status": "failed", "reason": "无法创建实验分支"}
                return result
            result["stages"]["branch"] = {"status": "created", "name": exp.branch_name}
            self.strand._log("branch", f"🌿 实验分支: {exp.branch_name}")
            
            # ── 阶段4: 实现（在实验分支上）──
            patches = await self._implement(plan)
            result["stages"]["implement"] = patches
            applied = [p for p in patches if p["status"] == "applied"]
            self.strand._log("implement", f"🔧 {len(applied)}个补丁")
            
            if not applied:
                bm.rollback_experiment()
                self._record_failure(round_id, "implement", affected_files, plan, {"reason": "no patches applied"})
                return result
            
            # 提交到实验分支
            changed_files = [p["target_file"] for p in applied]
            bm.commit_changes(f"evo: round-{round_id}", changed_files)
            
            # ── 阶段5: 独立grader评审代码 ──
            code_review = await evolution_grader.grade_code(self.strand, applied, plan)
            result["stages"]["code_review"] = code_review
            self.strand._log("code_review", f"{'✅' if code_review['verdict'] == 'satisfied' else '❌'} 代码评审: {code_review.get('reason', '')[:60]}")
            
            if code_review["verdict"] == evolution_grader.VERDICT_FAILED:
                bm.rollback_experiment()
                self._record_failure(round_id, "code_review", affected_files, plan, code_review)
                return result
            
            # ── 阶段6: 真实集成测试 ──
            self.strand._log("test", "🧪 运行集成测试...")
            test_result = await integration_test.run_integration_tests(
                self.repo_root,
                changed_files=[p["target_file"] for p in applied],
                include_ws_test=True,
                include_build=True,
            )
            result["stages"]["integration_test"] = test_result.to_dict()
            self.strand._log("test", f"{'✅' if test_result.passed else '❌'} 集成测试: {sum(1 for t in test_result.tests if t['passed'])}/{len(test_result.tests)} score={test_result.score}")
            
            # 程序化验证判定（不需要LLM裁判）
            test_grade = await evolution_grader.grade_integration_test_result(self.strand, test_result.to_dict())
            result["stages"]["test_grade"] = test_grade
            
            if test_grade["verdict"] == evolution_grader.VERDICT_FAILED:
                bm.rollback_experiment()
                self._record_failure(round_id, "integration_test", affected_files, plan, test_grade)
                return result
            
            if test_grade["verdict"] == evolution_grader.VERDICT_NEEDS_REVISION:
                bm.rollback_experiment()
                self._record_failure(round_id, "integration_test", affected_files, plan, test_grade)
                return result
            
            # ── 阶段6.5: P0-5评估闭环（langfuse LLM-as-Judge + agno experiment模式）──
            try:
                eval_result = await self._run_eval_loop(plan, applied, test_result.score)
                result["stages"]["eval_loop"] = eval_result
                self.strand._log("eval", f"📊 评估闭环: judge_score={eval_result.get('judge_score', 'N/A')} verdict={eval_result.get('verdict', 'N/A')}")
            except Exception as eval_exc:
                result["stages"]["eval_loop"] = {"status": "error", "detail": str(eval_exc)[:200]}
                self.strand._log("eval", f"⚠️ 评估闭环异常(不阻塞): {eval_exc}")
            
            # ── 阶段7: 判定 + 提交 ──
            verdict = self._verdict(test_result.score)
            result["stages"]["verdict"] = verdict
            
            if verdict["converged"]:
                # Merge实验分支到main
                merged = bm.merge_experiment()
                result["stages"]["merge"] = {"merged": merged, "branch": exp.branch_name}
                
                if merged:
                    # 记录成功模式
                    for p in applied:
                        failure_memory.record_success(
                            round_id, p["target_file"],
                            p.get("description", ""), "bugfix", "integration_test",
                            test_result.score,
                        )
                    
                    # 重置错误风暴
                    failure_memory.reset_storm()
                    
                    # 更新全局感知缓存
                    ga.update_after_change(changed_files, result)
                    
                    result["success"] = True
                    self.strand._log("verdict", f"🎉 进化成功! 分支已merge. score={test_result.score}")
                else:
                    self.strand._log("verdict", f"⚠️ 测试通过但merge失败")
            else:
                bm.rollback_experiment()
                self._record_failure(round_id, "verdict", affected_files, plan, verdict)
                self.strand._log("verdict", f"↩️ 分支回滚: {verdict.get('reason', '')[:60]}")
        
        except Exception as e:
            logger.error(f"Evolution v2 error: {e}")
            result["error"] = str(e)
            # 异常时也要回滚分支
            try:
                _bm = locals().get("bm")
                if _bm and _bm.get_current_branch().startswith("evo/exp-"):
                    _bm.rollback_experiment()
            except Exception:
                pass
        
        result["duration"] = round(time.time() - start_time, 1)
        return result
    
    # ── 内部方法 ──
    
    async def _locate(self, failure_batch: list[dict]) -> dict:
        """诊断故障根因"""
        failure_summary = "\n".join([
            f"- [{f.get('type', 'error')}] {f.get('description', str(f))[:200]}"
            for f in failure_batch[:10]
        ])
        
        code_context = await self._read_relevant_code(failure_batch)
        
        prompt = f"""你是一个代码诊断专家。分析以下失败样本，找出根因。

失败样本：
{failure_summary}

相关代码：
{code_context[:3000]}

输出JSON格式：
{{"root_cause": "一句话描述根因", "affected_files": ["file1.py", "file2.py"], "severity": "low/medium/high", "fix_strategy": "修复策略描述"}}"""
        
        result = await self.strand._call_llm(prompt)
        try:
            clean = result.strip()
            if clean.startswith("```"):
                clean = "\n".join(clean.split("\n")[1:-1])
            return json.loads(clean)
        except json.JSONDecodeError:
            return {"root_cause": result[:200], "affected_files": [], "severity": "medium"}
    
    async def _plan(self, diagnosis: dict, failure_context: str = "") -> dict:
        """生成修复方案（注入失败记忆）"""
        affected = diagnosis.get("affected_files", [])
        code_context = ""
        for f in affected[:5]:
            fpath = self.repo_root / f
            if fpath.exists():
                try:
                    content = fpath.read_text(encoding="utf-8")[:2000]
                    code_context += f"\n--- {f} ---\n{content}\n"
                except Exception:
                    pass
        
        prompt = f"""你是一个代码修复专家。根据诊断结果生成修复方案。

根因：{diagnosis.get('root_cause', '')}
影响文件：{', '.join(affected)}
修复策略：{diagnosis.get('fix_strategy', '')}

{failure_context}

相关代码：
{code_context[:4000]}

⚠️ 重要：必须考虑上面的历史失败记录，不要重复已经失败过的修改方式。

📖 工作方法论参考（主会话开发经验总结）：
请阅读 docs/main-session-methodology.md（如存在）了解诊断方法和验证标准。
核心原则：
1. 诊断链条：现象→假设→工具验证→数据确认根因——不猜测，用数据说话
2. 增量修复：精确old_string/new_string替换，禁止全量重写
3. 验证三关：完整性(grep确认改动存在)→集成(grep调用点证据)→行为(API/pytest实测)
4. 诚实报告：校验不过禁止声称完成，失败如实记录原因

输出JSON格式：
{{"changes": [
  {{"target_file": "acp-proxy/xxx.py", "description": "修改描述", "requirements": "具体要求"}}
],
 "risk_assessment": "风险评估",
 "test_plan": "测试计划"}}"""
        
        result = await self.strand._call_llm(prompt)
        try:
            clean = result.strip()
            if clean.startswith("```"):
                clean = "\n".join(clean.split("\n")[1:-1])
            return json.loads(clean)
        except json.JSONDecodeError:
            return {"changes": [], "risk_assessment": "parse error", "test_plan": ""}
    
    async def _implement(self, plan: dict) -> list[dict]:
        """实现修复 — 使用增量edit而非全量overwrite"""
        results = []
        for change in plan.get("changes", []):
            target = change.get("target_file", "")
            full_path = self.repo_root / target
            
            existing = ""
            if full_path.exists():
                existing = full_path.read_text(encoding="utf-8")
            
            # 增量edit协议（借鉴DNAStrand._generate_code的安全路径）
            if existing:
                prompt = f"""你是一个代码修改专家。对以下文件进行增量修改。

目标文件：{target}
现有代码（前3000行）：
{existing[:3000]}

修改要求：{change.get('description', '')}
具体要求：{change.get('requirements', '无')}

⚠️ 必须使用增量修改格式，不要重写整个文件。输出格式：
```json
{{"edits": [
  {{"old_string": "要替换的原文", "new_string": "替换后的内容"}}
]}}
```

如果需要新增文件，直接输出完整内容。"""
            else:
                prompt = f"""创建新文件 {target}。

要求：{change.get('description', '')}
具体要求：{change.get('requirements', '无')}

只输出文件内容，不要解释。"""
            
            content = await self.strand._call_llm(prompt)
            if not content:
                results.append({**change, "status": "failed", "reason": "LLM generation empty"})
                continue
            
            # 清理markdown代码块
            if content.startswith("```"):
                lines = content.split("\n")
                content = "\n".join(lines[1:-1]) if len(lines) > 2 else content
            
            # 应用修改
            try:
                if existing and '"edits"' in content:
                    # 增量edit模式
                    edits = json.loads(content).get("edits", [])
                    new_content = existing
                    for edit in edits:
                        old = edit.get("old_string", "")
                        new = edit.get("new_string", "")
                        if old and old in new_content:
                            new_content = new_content.replace(old, new, 1)
                        else:
                            results.append({**change, "status": "failed", "reason": f"old_string not found: {old[:50]}"})
                            continue
                    content = new_content
                elif not existing:
                    pass  # 新文件，直接用content
                else:
                    # 有existing但不是edit格式 → 拒绝（防止全量overwrite）
                    results.append({**change, "status": "failed", "reason": "expected incremental edits format, got full content"})
                    continue
                
                # 语法检查
                if target.endswith(".py"):
                    import ast
                    try:
                        ast.parse(content)
                    except SyntaxError as e:
                        results.append({**change, "status": "failed", "reason": f"syntax error: {e}"})
                        continue
                
                # 原子写入
                full_path.parent.mkdir(parents=True, exist_ok=True)
                import tempfile, os
                with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8",
                    dir=str(full_path.parent), suffix=".tmp", delete=False,
                ) as tmp:
                    tmp.write(content)
                    tmp_path = tmp.name
                os.rename(tmp_path, str(full_path))
                results.append({**change, "status": "applied", "content": content[:500]})
            
            except Exception as e:
                results.append({**change, "status": "failed", "reason": f"apply failed: {e}"})
        
        return results
    
    # ── P0-5 评估闭环（langfuse LLM-as-Judge + agno experiment模式）──
    
    async def _run_eval_loop(self, plan: dict, applied: list[dict], test_score: float) -> dict:
        """P0-5: LLM裁判评估改进质量。
        
        参照: langfuse LLM-as-Judge + agno environments + deepagents RubricMiddleware。
        用独立LLM调用对改进方案+实施结果进行裁判评分，与程序化测试分数对比。
        """
        # 构建评估prompt（rubric格式）
        plan_desc = plan.get("description", plan.get("title", ""))
        changes_desc = "\n".join([
            f"- {p.get('target_file', '?')}: {p.get('description', p.get('change', ''))[:200]}"
            for p in applied[:5]
        ])
        
        judge_prompt = f"""你是独立的代码改进评审员（LLM-as-Judge）。请评估以下进化改进的质量。

## 改进方案
{plan_desc[:500]}

## 实施的变更
{changes_desc}

## 程序化测试得分
{test_score}

## 评估维度（每项1-5分）
1. **有效性**: 变更是否真正解决了声明的问题？
2. **安全性**: 变更是否引入新风险？
3. **最小性**: 变更是否过度（超出解决问题所需）？
4. **可验证性**: 变更效果是否可测量？

请严格以JSON格式回复：
{{"effectiveness": <1-5>, "safety": <1-5>, "minimality": <1-5>, "verifiability": <1-5>, "overall": <1.0-5.0>, "verdict": "satisfied|needs_revision|failed", "reason": "<一句话>"}}"""

        try:
            judge_response = await self.strand._call_llm(judge_prompt)
            # 解析JSON（宽容提取）
            import json as _json
            import re as _re
            json_match = _re.search(r'\{[^{}]+\}', judge_response, _re.DOTALL)
            if json_match:
                scores = _json.loads(json_match.group())
            else:
                return {"status": "parse_failed", "raw": judge_response[:200], "judge_score": None}
            
            judge_score = scores.get("overall", 0)
            verdict = scores.get("verdict", "unknown")
            
            # 记录到eval store（如果opensoul benchmark可用）
            try:
                import httpx as _httpx
                async with _httpx.AsyncClient(timeout=5.0) as client:
                    await client.post(
                        "http://127.0.0.1:8090/api/benchmark/experiments",
                        json={
                            "dataset_id": "evo_v2_judge",
                            "scores": scores,
                            "test_score": test_score,
                            "plan": plan_desc[:200],
                        },
                    )
            except Exception:
                pass  # benchmark API不可用时不阻塞
            
            return {
                "status": "ok",
                "judge_score": judge_score,
                "verdict": verdict,
                "scores": scores,
                "test_score": test_score,
            }
        except Exception as e:
            return {"status": "error", "detail": str(e)[:200], "judge_score": None}
    
    def _verdict(self, score: float) -> dict:
        """判定是否收敛"""
        self._score_history.append(score)
        
        converged = score >= 0.8  # 从0.7提高到0.8
        
        if len(self._score_history) >= PLATEAU_ROUNDS:
            recent = self._score_history[-PLATEAU_ROUNDS:]
            if all(abs(recent[i] - recent[i-1]) < 0.01 for i in range(1, len(recent))):
                return {
                    "converged": False, "score": score,
                    "reason": f"平台期: 近{PLATEAU_ROUNDS}轮分数无变化",
                    "plateau": True,
                }
        
        return {
            "converged": converged, "score": score,
            "reason": "达标" if converged else f"分数{score}未达标(需>=0.8)",
        }
    
    def _record_failure(self, round_id: str, stage: str, files: list[str], plan: dict, review: dict):
        """记录失败到记忆系统。
        plan_review评审拒绝不是代码执行失败，不喂failure_memory（防评审签名污染风暴检测）。"""
        if stage == "plan_review":
            self.strand._log("plan_review", f"📝 提案评审意见留存(不计入失败样本): {review.get('reason', '')[:60]}")
            return
        for f in files[:3]:
            failure_memory.record_failure(
                round_id=round_id,
                stage=stage,
                target_file=f,
                change_description=json.dumps(plan.get("changes", []), ensure_ascii=False)[:500],
                failure_reason=review.get("reason", str(review))[:500],
                error_type=self._classify_error(review),
            )
    
    def _classify_error(self, review: dict) -> str:
        """分类错误类型"""
        reason = review.get("reason", "").lower()
        if "syntax" in reason or "语法" in reason:
            return "syntax"
        if "import" in reason or "导入" in reason:
            return "import"
        if "protocol" in reason or "协议" in reason:
            return "protocol"
        if "security" in reason or "安全" in reason:
            return "security"
        if "ws" in reason or "websocket" in reason:
            return "ws"
        return "logic"
    
    def _error_signature(self, failure: dict) -> str:
        """生成错误签名（用于风暴检测）"""
        desc = failure.get("description", str(failure))
        # 取前100字符作为签名
        return desc[:100]
    
    async def _read_relevant_code(self, failure_batch: list[dict]) -> str:
        """读取失败样本相关的代码"""
        import subprocess
        code = ""
        files = set()
        for f in failure_batch:
            if "file" in f:
                files.add(f["file"])
        
        for fpath in list(files)[:5]:
            full = self.repo_root / fpath
            if full.exists():
                try:
                    content = full.read_text(encoding="utf-8")[:1500]
                    code += f"\n--- {fpath} ---\n{content}\n"
                except Exception:
                    pass
        
        if not code:
            try:
                result = subprocess.run(
                    ["git", "log", "--oneline", "-5", "--name-only"],
                    capture_output=True, text=True, cwd=str(self.repo_root),
                )
                code = f"Recent git changes:\n{result.stdout[:2000]}"
            except Exception:
                code = "No relevant code context available"
        
        return code
    
    def _git_commit(self, target: str, message: str):
        """Git提交"""
        import subprocess
        try:
            subprocess.run(["git", "add", target], cwd=str(self.repo_root), capture_output=True, timeout=10)
            subprocess.run(["git", "commit", "-m", message], cwd=str(self.repo_root), capture_output=True, timeout=10)
        except Exception as e:
            logger.warning(f"Git commit failed: {e}")
    
    def _git_revert_file(self, target: str):
        """Git回滚单个文件"""
        import subprocess
        try:
            subprocess.run(["git", "checkout", "HEAD", "--", target], cwd=str(self.repo_root), capture_output=True, timeout=10)
        except Exception as e:
            logger.warning(f"Git revert failed: {e}")

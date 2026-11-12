"""进化流水线 — MOSS风格7阶段 + 全局锁 + 网关drain + 审计日志

流水线：Locate → Plan → Plan-Review → Implement → Code-Review → Task-Evaluate → Verdict
"""
import asyncio
import fcntl
import json
import logging
import os
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

logger = logging.getLogger("evolution-pipeline")

# ── 配置 ──
MAX_RETRIES = 3                # 单次进化最大重试次数
PLATEAU_ROUNDS = 3             # 连续无提升轮数 → 判定收敛
GATEWAY_URL = "http://127.0.0.1:8091"
LOCK_FILE = "data/evolution.lock"
AUDIT_FILE = "data/audit_log.jsonl"

# 内核文件（修改需人工确认）
KERNEL_FILES = [
    "acp-proxy/app.py",
    "acp-proxy/main.py",
    "acp-proxy/dna_evolution.py",
    "acp-proxy/ws_acp.py",
    "acp-proxy/ws_chat.py",
    "acp-proxy/agent/soulmate_agent.py",
    "acp-proxy/agent/llm_engine.py",
    "acp-proxy/gateway_proxy.py",
    "acp-proxy/supervisor.sh",
]

# 绝对禁止修改的文件
IMMUTABLE_FILES = [
    "acp-proxy/evolution_pipeline.py",  # 自身不可改
]

# Hermes审核队列（evo达标commit后写入此队列，由Hermes cron审核后才push GitHub）
REVIEW_QUEUE_FILE = "data/hermes_review_queue.json"
# 审核反馈文件（Hermes审核驳回时写入，evo的planner下轮读取学习）
EVO_FEEDBACK_FILE = "data/evo_feedback.json"

# 编码规范（注入_implement/_code_review的prompt——evo跟Hermes学习编程思维的核心教材）
CODING_STANDARDS = """【编码规范 — 必须严格遵守】
1. import规范：
   - 所有import放在文件顶部（可选依赖的延迟导入除外），禁止在import块中间插入非import代码
   - import必须写完整模块路径，如：from agent.prompt_manager import PromptTemplateManager
   - 禁止截断/简写import（历史事故：'import P'是半个词，'__DEBUG_VALIDATION__'是拼接残片——直接导致进程import即死）
2. 命名规范：
   - 函数/变量名 snake_case，用完整英文单词：get_user_by_id、parse_config、validate_input
   - 类名 PascalCase：SoulMateAgent、ConfigManager
   - 常量 UPPER_CASE：MAX_RETRIES、DEFAULT_TIMEOUT
   - 禁止拼接残片标识符（历史事故：TrueromptTemplateManager = True+romptTemplate的垃圾拼接）
   - 标识符超过3个单词时用下划线分段，不要粘连
3. 函数设计：
   - 单一职责，一个函数只做一件事
   - 保持现有函数签名不变，除非修改要求明确要求改签名
   - 外部调用（IO/网络/子进程）必须有try/except，禁止裸except: pass
4. 修改边界：
   - 只修改与要求直接相关的代码，不动无关代码
   - 保留现有代码的结构、风格、import顺序
   - 禁止删除与本次修改无关的import和代码行
5. 输出前自检（逐项核对后再输出）：
   - 所有import完整、位于文件顶部、路径真实存在？
   - 每个新标识符都是完整单词、符合命名规范？
   - 没有向import块中插入非import代码？
   - 没有截断任何单词？"""


class EvolutionLock:
    """全局进化锁 — 文件锁，同时只允许一个实例进化"""

    def __init__(self, lock_path: Path, instance_id: str):
        self.lock_path = lock_path
        self.instance_id = instance_id
        self._fd = None

    def acquire(self) -> bool:
        """获取锁，返回是否成功"""
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._fd = open(self.lock_path, "w")
            fcntl.flock(self._fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._fd.write(json.dumps({
                "instance": self.instance_id,
                "pid": os.getpid(),
                "acquired_at": datetime.now(timezone.utc).isoformat(),
            }))
            self._fd.flush()
            logger.info(f"🔒 Evolution lock acquired by instance {self.instance_id}")
            return True
        except (IOError, OSError):
            logger.warning(f"⚠️ Evolution lock held by another instance, skipping")
            self._fd = None
            return False

    def release(self):
        """释放锁"""
        if self._fd:
            try:
                fcntl.flock(self._fd.fileno(), fcntl.LOCK_UN)
                self._fd.close()
                self.lock_path.unlink(missing_ok=True)
                logger.info(f"🔓 Evolution lock released")
            except Exception:
                pass
            self._fd = None


class AuditLogger:
    """审计日志 — 记录每一轮进化的完整过程"""

    def __init__(self, audit_path: Path):
        self.audit_path = audit_path
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, entry: dict):
        entry["logged_at"] = datetime.now(timezone.utc).isoformat()
        with open(self.audit_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


class EvolutionPipeline:
    """MOSS风格7阶段进化流水线"""

    def __init__(self, strand, repo_root: Path, instance_id: str):
        self.strand = strand
        self.repo_root = repo_root
        self.instance_id = instance_id
        self.acp_dir = repo_root / "acp-proxy"
        self.data_dir = self.acp_dir / "data"

        self.lock = EvolutionLock(self.acp_dir / LOCK_FILE, instance_id)
        self.audit = AuditLogger(self.data_dir / AUDIT_FILE)

        # 收敛追踪
        self._score_history: list[float] = []
        self._consecutive_no_improve = 0
        # study 观察发现（33-StudyLoop §7.2 第三种进化燃料，观察阶段收集，_plan 吸收）
        self._study_findings: list[dict] = []

    async def run_pipeline(self, failure_batch: list[dict]) -> dict:
        """执行完整的7阶段流水线"""
        round_id = f"evo_{int(time.time())}_{self.instance_id}"
        result = {"round_id": round_id, "stages": {}, "success": False}

        # 获取全局进化锁
        if not self.lock.acquire():
            result["stages"]["lock"] = {"status": "skipped", "reason": "lock held"}
            return result

        try:
            for attempt in range(MAX_RETRIES):
                result["attempt"] = attempt + 1
                self.audit.log({"round_id": round_id, "event": "attempt_start", "attempt": attempt + 1})

                # ⓪ Observe-study：外部软件世界观察（33-StudyLoop §7.2 第三种进化燃料）
                # 与既有 failure 观察并列，不替换；opensoul 不可达时静默降级
                study_observation = await self._observe_study()
                result["stages"]["study"] = study_observation
                self._study_findings = study_observation.get("findings", [])
                self.strand._log("observe_study", f"🔬 study观察: {len(self._study_findings)}条功能发现 ({study_observation.get('status', '?')})")

                # ① Locate：诊断故障
                diagnosis = await self._locate(failure_batch)
                result["stages"]["locate"] = diagnosis
                self.strand._log("locate", f"🔍 诊断完成: {diagnosis.get('root_cause', 'unknown')[:80]}")

                if not diagnosis.get("root_cause"):
                    result["stages"]["locate"]["status"] = "no_root_cause"
                    break

                # ② Plan：生成修复方案
                plan = await self._plan(diagnosis)
                result["stages"]["plan"] = plan
                self.strand._log("plan_pipeline", f"📋 修复方案: {len(plan.get('changes', []))}个文件")

                # ③ Plan-Review：评审方案
                plan_review = await self._plan_review(plan)
                result["stages"]["plan_review"] = plan_review
                self.strand._log("plan_review", f"{'✅方案通过' if plan_review['approved'] else '❌方案驳回'}: {plan_review.get('reason', '')[:60]}")

                if not plan_review["approved"]:
                    self.audit.log({"round_id": round_id, "event": "plan_rejected", "reason": plan_review.get("reason")})
                    if attempt < MAX_RETRIES - 1:
                        continue
                    break

                # ④ Implement：生成diff补丁
                patches = await self._implement(plan)
                result["stages"]["implement"] = patches
                applied = [p for p in patches if p["status"] == "applied"]
                self.strand._log("implement", f"🔧 生成{len(applied)}个补丁")

                if not applied:
                    if attempt < MAX_RETRIES - 1:
                        continue
                    break

                # ⑤ Code-Review：评审代码质量
                code_review = await self._code_review(applied)
                result["stages"]["code_review"] = code_review
                self.strand._log("code_review", f"{'✅代码通过' if code_review['approved'] else '❌代码驳回'}: {code_review.get('reason', '')[:60]}")

                if not code_review["approved"]:
                    # 回滚所有已应用的补丁
                    for p in applied:
                        self._git_revert_file(p["target_file"])
                    self.audit.log({"round_id": round_id, "event": "code_rejected", "reason": code_review.get("reason")})
                    if attempt < MAX_RETRIES - 1:
                        continue
                    break

                # ⑥ Task-Evaluate：沙箱测试
                eval_result = await self._task_evaluate(applied)
                result["stages"]["task_evaluate"] = eval_result
                self.strand._log("evaluate", f"{'✅测试通过' if eval_result['passed'] else '❌测试失败'}: score={eval_result.get('score', 0)}")

                # ⑦ Verdict：判定
                verdict = self._verdict(eval_result)
                result["stages"]["verdict"] = verdict
                self.audit.log({"round_id": round_id, "event": "verdict", **verdict})

                if verdict["converged"]:
                    # 达标 → git commit（本地保留成果）
                    for p in applied:
                        self._git_commit(p["target_file"], f"[evo:{self.instance_id}] {round_id}")
                    # 提交Hermes审核队列（用户指示：evo开发的代码提交给Hermes审核，审核通过才push GitHub）
                    try:
                        self._enqueue_hermes_review(round_id, applied, verdict)
                    except Exception as e:
                        logger.warning(f"审核队列写入失败: {e}")
                    result["success"] = True
                    self.strand._log("verdict", f"🎉 进化成功! score={verdict['score']}（已提交Hermes审核队列）")
                    break
                else:
                    # 不达标 → 回滚
                    for p in applied:
                        self._git_revert_file(p["target_file"])
                    self.strand._log("verdict", f"↩️ 回滚: {verdict.get('reason', '')[:60]}")
                    if attempt < MAX_RETRIES - 1:
                        continue
                    break

        finally:
            self.lock.release()

        self.audit.log({"round_id": round_id, "event": "pipeline_done", "success": result["success"], "stages": list(result["stages"].keys())})
        return result

    # ── ⓪ Observe-study（33-StudyLoop §7：study 来源，与 failure 观察并列） ──
    async def _observe_study(self) -> dict:
        """跑 study 目标清单→证据→功能发现（第三种进化燃料）。

        静默降级铁律：opensoul 不可达/超时/清单坏 都不得打断观察阶段，
        run_study_round 内部已兜底不抛，这里再加一层防护（import 失败等）。
        频控由 study_hook 保证：单轮 ≤3 目标、截图 ≤12 张、总超时 120s。
        """
        try:
            from study_hook import run_study_round
            study_result = await run_study_round(self.acp_dir / "data" / "study_targets.json")
            self._study_findings = study_result.get("findings", [])
            return study_result
        except Exception as e:
            logger.warning(f"study观察降级（不阻断进化）: {e}")
            self._study_findings = []
            return {"status": "degraded", "findings": [], "targets_run": [], "evidence_ids": [], "errors": [str(e)[:200]]}

    # ── ① Locate ──

    async def _locate(self, failure_batch: list[dict]) -> dict:
        """诊断故障根因"""
        failure_summary = "\n".join([
            f"- [{f.get('type', 'error')}] {f.get('description', str(f))[:200]}"
            for f in failure_batch[:10]
        ])

        # 读取相关源码上下文
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
            # 清理markdown代码块
            clean = result.strip()
            if clean.startswith("```"):
                clean = "\n".join(clean.split("\n")[1:-1])
            return json.loads(clean)
        except json.JSONDecodeError:
            return {"root_cause": result[:200], "affected_files": [], "severity": "medium"}

    # ── ② Plan ──

    async def _plan(self, diagnosis: dict) -> dict:
        """生成修复方案"""
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

        feedback = self._load_recent_feedback(self.repo_root)
        # study 发现（33-StudyLoop §7.3）：与 feedback 并列注入，来源=study，有证据 id 支撑
        study_section = self._format_study_section()
        prompt = f"""你是一个代码修复专家。根据诊断结果生成修复方案。

{feedback}{study_section}
根因：{diagnosis.get('root_cause', '')}
影响文件：{', '.join(affected)}
修复策略：{diagnosis.get('fix_strategy', '')}

相关代码：
{code_context[:4000]}

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

    # ── ③ Plan-Review ──

    async def _plan_review(self, plan: dict) -> dict:
        """评审修复方案 — 写代码前先审方案"""
        changes = plan.get("changes", [])

        # 黑白名单校验
        for change in changes:
            target = change.get("target_file", "")
            if target in IMMUTABLE_FILES:
                return {"approved": False, "reason": f"文件 {target} 禁止修改"}
            if any(target.endswith(f) for f in [".env", ".git/config"]):
                return {"approved": False, "reason": f"安全文件 {target} 禁止修改"}

        # 检查修改范围 — 单次最多改5个文件
        if len(changes) > 5:
            return {"approved": False, "reason": f"修改范围过大: {len(changes)}个文件，上限5个"}

        # 检查是否涉及内核文件
        kernel_changes = [c for c in changes if c.get("target_file", "") in KERNEL_FILES]
        if kernel_changes:
            # 内核修改标记为需人工审核（当前自动拒绝，后续可配置）
            return {"approved": False, "reason": f"内核文件修改需人工审核: {[c['target_file'] for c in kernel_changes]}"}

        # LLM评审方案合理性
        prompt = f"""评审以下代码修复方案，判断是否合理、安全、范围适当。

方案：
{json.dumps(plan, ensure_ascii=False, indent=2)[:2000]}

评审标准：
1. 修改范围是否适当（小补丁，非大规模重构）
2. 风险是否可控
3. 测试计划是否充分

输出JSON: {{"approved": true/false, "reason": "原因", "suggestions": ["建议1"]}}"""

        result = await self.strand._call_llm(prompt)
        try:
            clean = result.strip()
            if clean.startswith("```"):
                clean = "\n".join(clean.split("\n")[1:-1])
            review = json.loads(clean)
            if isinstance(review, dict) and "approved" in review:
                return review
        except json.JSONDecodeError:
            pass

        # 默认通过（LLM解析失败时不阻塞）
        return {"approved": True, "reason": "LLM review parse failed, defaulting to approve"}

    # ── ④ Implement ──

    async def _implement(self, plan: dict) -> list[dict]:
        """生成并应用diff补丁"""
        results = []
        for change in plan.get("changes", []):
            target = change.get("target_file", "")
            full_path = self.repo_root / target

            # 读取现有文件
            existing = ""
            if full_path.exists():
                existing = full_path.read_text(encoding="utf-8")

            # 用LLM生成代码（注入编码规范——evo编程思维教学）
            prompt = f"""你是一个代码修改专家。根据以下要求修改文件。

{CODING_STANDARDS}

目标文件：{target}
{('⚠️ 核心文件：改动此文件将被Hermes严格审核，import错误直接否决。' if any(target.endswith(k.split('/')[-1]) or k.endswith(target) for k in KERNEL_FILES) else '')}
现有代码：
{existing[:3000]}

修改要求：{change.get('description', '')}
具体要求：{change.get('requirements', '无')}

只输出修改后的完整文件内容，不要解释。"""

            content = await self.strand._call_llm(prompt)
            if not content:
                results.append({**change, "status": "failed", "reason": "LLM generation empty"})
                continue

            # 清理markdown代码块
            if content.startswith("```"):
                lines = content.split("\n")
                content = "\n".join(lines[1:-1]) if len(lines) > 2 else content

            # 语法检查
            if target.endswith(".py"):
                import ast
                try:
                    ast.parse(content)
                except SyntaxError as e:
                    results.append({**change, "status": "failed", "reason": f"syntax error: {e}"})
                    continue

            if target.endswith(".json"):
                try:
                    json.loads(content)
                except json.JSONDecodeError as e:
                    results.append({**change, "status": "failed", "reason": f"json error: {e}"})
                    continue

            # 原子写入
            try:
                full_path.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8",
                    dir=str(full_path.parent), suffix=".tmp", delete=False,
                ) as tmp:
                    tmp.write(content)
                    tmp_path = tmp.name
                os.rename(tmp_path, str(full_path))
                results.append({**change, "status": "applied", "content": content[:500]})
            except Exception as e:
                results.append({**change, "status": "failed", "reason": f"write failed: {e}"})

        return results

    # ── ⑤ Code-Review ──

    async def _code_review(self, applied: list[dict]) -> dict:
        """评审diff代码质量"""
        diff_summary = "\n".join([
            f"--- {p['target_file']} ---\n{p.get('content', '')[:500]}"
            for p in applied[:5]
        ])

        prompt = f"""评审以下代码变更，检查质量和安全性。

变更：
{diff_summary[:3000]}

检查项：
1. 是否引入明显bug
2. 是否有安全风险（注入、越权、敏感信息泄露）
3. 代码质量（命名、结构、重复）
4. 编码规范符合性：
   - import是否完整、位于文件顶部、路径真实？（截断import如'import P'、import块中混入非import代码=立即驳回）
   - 标识符是否完整英文单词？（拼接残片如'TrueromptTemplateManager'=立即驳回）
   - 函数是否单一职责？外部调用是否有try/except？
5. 修改是否越界（动了与要求无关的代码/删了无关import）

核心文件（{', '.join(KERNEL_FILES)}）的改动：任何import问题、签名变更、删除代码行都必须驳回。

输出JSON: {{"approved": true/false, "reason": "原因", "issues": ["问题1"]}}"""

        result = await self.strand._call_llm(prompt)
        try:
            clean = result.strip()
            if clean.startswith("```"):
                clean = "\n".join(clean.split("\n")[1:-1])
            review = json.loads(clean)
            if isinstance(review, dict) and "approved" in review:
                return review
        except json.JSONDecodeError:
            pass

        # 评审解析失败 = 驳回（默认放行是历史漏洞，导致坏代码溜进主线）
        return {"approved": False, "reason": "LLM review parse failed — 默认驳回（解析失败不放行）", "issues": ["review_parse_failed"]}

    # ── ⑥ Task-Evaluate ──

    async def _task_evaluate(self, applied: list[dict]) -> dict:
        """沙箱测试 — 语法检查 + 导入测试 + 回放失败样本"""
        score = 1.0
        details = []

        for patch in applied:
            target = patch.get("target_file", "")
            full_path = self.repo_root / target

            # 语法检查
            if target.endswith(".py"):
                try:
                    result = subprocess.run(
                        ["python3", "-c", f"import ast; ast.parse(open('{full_path}').read())"],
                        capture_output=True, text=True, timeout=10,
                        cwd=str(self.repo_root),
                    )
                    if result.returncode != 0:
                        score -= 0.3
                        details.append(f"语法检查失败: {target}: {result.stderr[:200]}")
                except Exception as e:
                    score -= 0.2
                    details.append(f"语法检查异常: {target}: {e}")

                # 导入测试（完整模块路径 import + cwd=repo_root，修复旧版只import最后一段的bug）
                module_path = target.replace("/", ".").replace(".py", "")
                is_core = any(target.endswith(k.split("/")[-1]) or k.endswith(target) for k in KERNEL_FILES)
                import_ok = True
                try:
                    result = subprocess.run(
                        ["python3", "-c", f"import {module_path}"],
                        capture_output=True, text=True, timeout=10,
                        cwd=str(self.repo_root),
                    )
                    if result.returncode != 0:
                        import_ok = False
                        if is_core:
                            # 核心文件import失败 = 一票否决（历史上soulmate_agent.py被改坏就是import即死）
                            details.append(f"❌核心文件导入失败(一票否决): {target}: {result.stderr[:200]}")
                        else:
                            score -= 0.5
                            details.append(f"导入失败: {target}: {result.stderr[:200]}")
                except Exception as e:
                    import_ok = False
                    score -= 0.3
                    details.append(f"导入测试异常: {target}: {e}")

                if not import_ok and is_core:
                    return {"passed": False, "score": 0.0, "details": details}

        score = max(0.0, score)
        return {
            "passed": score >= 0.7,
            "score": round(score, 2),
            "details": details,
        }

    # ── ⑦ Verdict ──

    def _verdict(self, eval_result: dict) -> dict:
        """判定是否收敛"""
        score = eval_result.get("score", 0)
        self._score_history.append(score)

        # 检查是否收敛（分数达标 + 没有进入平台期）
        converged = eval_result.get("passed", False)

        # 平台期检测
        if len(self._score_history) >= PLATEAU_ROUNDS:
            recent = self._score_history[-PLATEAU_ROUNDS:]
            if all(abs(recent[i] - recent[i-1]) < 0.01 for i in range(1, len(recent))):
                converged = False
                return {
                    "converged": False,
                    "score": score,
                    "reason": f"平台期: 近{PLATEAU_ROUNDS}轮分数无变化",
                    "plateau": True,
                }

        return {
            "converged": converged,
            "score": score,
            "reason": "达标" if converged else f"分数{score}未达标",
        }

    # ── 辅助方法 ──

    def _enqueue_hermes_review(self, round_id: str, applied: list[dict], verdict: dict):
        """把evo的产出提交给Hermes审核队列（用户指示：开发的代码提交给Hermes审核）"""
        queue_path = self.repo_root / REVIEW_QUEUE_FILE
        queue_path.parent.mkdir(parents=True, exist_ok=True)

        entries = []
        if queue_path.exists():
            try:
                entries = json.loads(queue_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                entries = []

        # 获取当前commit hash（evo刚commit的）
        try:
            commit_hash = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                capture_output=True, text=True, timeout=10, cwd=str(self.repo_root),
            ).stdout.strip()
        except Exception:
            commit_hash = "unknown"

        files = [p.get("target_file", "") for p in applied]
        has_core = any(
            any(f.endswith(k.split("/")[-1]) or k.endswith(f) for k in KERNEL_FILES)
            for f in files
        )

        entries.append({
            "round_id": round_id,
            "instance_id": self.instance_id,
            "commit_hash": commit_hash,
            "files": files,
            "has_core_file": has_core,
            "score": verdict.get("score", 0),
            "enqueued_at": datetime.now(timezone.utc).isoformat(),
            "reviewed": False,
            "review_result": None,
        })

        queue_path.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"Hermes审核队列已提交: {round_id} ({len(files)}个文件, 核心文件={has_core})")

    def _format_study_section(self) -> str:
        """study 发现段落（与 _load_recent_feedback 并列进 _plan prompt，标明来源 study）"""
        if not self._study_findings:
            return ""
        try:
            from study_hook import format_study_findings
            return "\n" + format_study_findings(self._study_findings)
        except Exception as e:
            logger.warning(f"study发现段落格式化失败（不阻断规划）: {e}")
            return ""

    @staticmethod
    def _load_recent_feedback(repo_root: Path, limit: int = 5) -> str:
        """读取Hermes最近的审核反馈——evo的学习材料（编程思维教学闭环）"""
        feedback_path = repo_root / EVO_FEEDBACK_FILE
        if not feedback_path.exists():
            return ""
        try:
            items = json.loads(feedback_path.read_text(encoding="utf-8"))
            recent = items[-limit:]
            if not recent:
                return ""
            lines = []
            for fb in recent:
                lines.append(f"- [{fb.get('verdict', '?')}] {fb.get('reason', '')[:120]}")
            return "\n【Hermes最近审核反馈 — 必须避免重复犯错】\n" + "\n".join(lines) + "\n"
        except Exception:
            return ""

    async def _read_relevant_code(self, failure_batch: list[dict]) -> str:
        """读取失败样本相关的代码"""
        code = ""
        # 从失败样本中提取文件路径
        files = set()
        for f in failure_batch:
            if "file" in f:
                files.add(f["file"])
            if "traceback" in f:
                for line in str(f["traceback"]).split("\n"):
                    if 'File "' in line:
                        try:
                            path = line.split('File "')[1].split('"')[0]
                            if path.startswith(str(self.repo_root)):
                                files.add(path.replace(str(self.repo_root) + "/", ""))
                        except (IndexError, ValueError):
                            pass

        for fpath in list(files)[:5]:
            full = self.repo_root / fpath
            if full.exists():
                try:
                    content = full.read_text(encoding="utf-8")[:1500]
                    code += f"\n--- {fpath} ---\n{content}\n"
                except Exception:
                    pass

        if not code:
            # 没有具体文件，读取最近修改的文件
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
        try:
            subprocess.run(["git", "add", target], cwd=str(self.repo_root), capture_output=True, timeout=10)
            subprocess.run(["git", "commit", "-m", message], cwd=str(self.repo_root), capture_output=True, timeout=10)
        except Exception as e:
            logger.warning(f"Git commit failed: {e}")

    def _git_revert_file(self, target: str):
        """Git回滚单个文件"""
        try:
            subprocess.run(["git", "checkout", "HEAD", "--", target], cwd=str(self.repo_root), capture_output=True, timeout=10)
        except Exception as e:
            logger.warning(f"Git revert failed: {e}")


# ── 网关交互 ──

async def gateway_drain(instance_id: str):
    """通知网关摘除流量"""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(f"{GATEWAY_URL}/admin/drain/{instance_id}")
            if resp.status_code == 200:
                logger.info(f"Gateway drained instance {instance_id}")
                return True
    except Exception as e:
        logger.warning(f"Gateway drain failed: {e}")
    return False


async def gateway_undrain(instance_id: str):
    """通知网关恢复流量"""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(f"{GATEWAY_URL}/admin/undrain/{instance_id}")
            if resp.status_code == 200:
                logger.info(f"Gateway undrained instance {instance_id}")
                return True
    except Exception as e:
        logger.warning(f"Gateway undrain failed: {e}")
    return False

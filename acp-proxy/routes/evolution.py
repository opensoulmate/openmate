"""进化引擎 REST API — 支持DNA双螺旋架构

路由前缀: /api/evolution
"""

from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional
from pathlib import Path

router = APIRouter(prefix="/api/evolution", tags=["evolution"])

# DNAEvolutionEngine 实例由 app.py 注入
_engine = None


def set_engine(engine):
    global _engine
    _engine = engine


def get_engine():
    return _engine


@router.get("/status")
async def evolution_status():
    """获取DNA双螺旋进化引擎状态"""
    engine = get_engine()
    if not engine:
        return {"error": "Evolution engine not initialized"}
    
    # 从心跳文件读取实时strand状态
    import json as _json
    import time as _time
    from pathlib import Path as _Path
    data_root = _Path(engine.repo_root) / "acp-proxy" / "data"
    now_ts = _time.time()
    
    # 先读所有心跳
    heartbeats = {}
    for sid in ["strand_a", "strand_b"]:
        hb_file = data_root / f"dna_heartbeat_{sid}.json"
        if hb_file.exists():
            try:
                heartbeats[sid] = _json.loads(hb_file.read_text())
            except Exception:
                pass
    
    # 更新strand状态
    for strand, sid in [(engine.strand_a, "strand_a"), (engine.strand_b, "strand_b")]:
        hb = heartbeats.get(sid)
        if hb:
            elapsed = now_ts - hb.get("timestamp", 0)
            strand._running = elapsed < 120  # 120秒内有心跳=运行中
            strand._cycle_count = hb.get("cycle_count", strand._cycle_count)
        
        # partner_alive: 检查对方心跳是否在60秒内
        partner_id = "strand_b" if sid == "strand_a" else "strand_a"
        partner_hb = heartbeats.get(partner_id)
        if partner_hb:
            partner_elapsed = now_ts - partner_hb.get("timestamp", 0)
            strand._partner_alive = partner_elapsed < 60
            strand._partner_cycle_count = partner_hb.get("cycle_count", 0)
        else:
            strand._partner_alive = False
    
    return engine.get_status()


@router.get("/strands")
async def evolution_strands():
    """获取两条进化链的详细状态"""
    engine = get_engine()
    if not engine:
        return {"error": "Evolution engine not initialized"}
    return {
        "strand_a": engine.strand_a.get_status(),
        "strand_b": engine.strand_b.get_status(),
    }


@router.post("/observe")
async def observe(request: dict):
    """手动注入观察数据（同时注入两条链）"""
    engine = get_engine()
    if not engine:
        return {"error": "Evolution engine not initialized"}

    obs_type = request.get("type", "manual")
    content = request.get("content", "")
    metadata = request.get("metadata", {})

    engine.observe(obs_type, content, metadata)
    return {"ok": True, "message": "Observation injected to both strands"}


@router.post("/trigger")
async def trigger_evolution():
    """手动触发一次进化周期（在两条链上同时触发）"""
    engine = get_engine()
    if not engine:
        return {"error": "Evolution engine not initialized"}

    # 触发两条链的进化
    import asyncio
    results = await asyncio.gather(
        engine.strand_a._evolve(),
        engine.strand_b._evolve(),
        return_exceptions=True,
    )

    return {
        "ok": True,
        "strand_a_result": "success" if results[0] is None else str(results[0]),
        "strand_b_result": "success" if results[1] is None else str(results[1]),
    }


@router.post("/strands/{strand_id}/reset-breaker")
async def reset_strand_breaker(strand_id: str):
    """人工复位LLM错误风暴熔断（job_queue.requeue同款语义：人工意图不受熔断拦截）。

    场景：充值/换key修复LLM后立即恢复自动进化，无需等冷却过期。
    """
    engine = get_engine()
    if not engine:
        return {"error": "Evolution engine not initialized"}
    strand = {"strand_a": engine.strand_a, "strand_b": engine.strand_b}.get(strand_id)
    if strand is None:
        return {"error": f"Unknown strand: {strand_id}", "known": ["strand_a", "strand_b"]}
    status = strand.reset_breaker()
    return {
        "ok": True,
        "strand_id": strand_id,
        "breaker": status.get("breaker"),
        "llm_failure_streak": status.get("llm_failure_streak"),
    }


@router.get("/checkpoint")
async def get_checkpoint():
    """获取共享检查点（成功的进化经验）"""
    engine = get_engine()
    if not engine:
        return {"error": "Evolution engine not initialized"}

    checkpoint_file = Path(engine.repo_root) / "acp-proxy" / "data" / "dna_checkpoint.json"
    if checkpoint_file.exists():
        import json
        with open(checkpoint_file, "r") as f:
            return json.load(f)
    return {"message": "No checkpoint yet"}


@router.get("/results")
async def get_results(limit: int = 10):
    """获取最近的进化结果"""
    engine = get_engine()
    if not engine:
        return {"error": "Evolution engine not initialized"}
    return {"results": engine.get_recent_results(limit)}


@router.get("/skills")
async def get_created_skills():
    """获取进化引擎创建的技能列表"""
    engine = get_engine()
    if not engine:
        return {"error": "Evolution engine not initialized"}
    return {"skills": engine.get_created_skills()}


@router.get("/memory")
async def get_memory():
    """获取进化引擎的学习记忆"""
    engine = get_engine()
    if not engine:
        return {"error": "Evolution engine not initialized"}
    return {
        "strand_a_memories": engine.strand_a._memory[-10:],
        "strand_b_memories": engine.strand_b._memory[-10:],
    }


@router.get("/dashboard")
async def get_dashboard():
    """获取进化引擎仪表盘数据"""
    engine = get_engine()
    if not engine:
        return {"error": "Evolution engine not initialized"}

    import json
    from pathlib import Path

    # 检查点
    checkpoint_file = Path(engine.repo_root) / "acp-proxy" / "data" / "dna_checkpoint.json"
    checkpoint = None
    if checkpoint_file.exists():
        try:
            with open(checkpoint_file, "r") as f:
                checkpoint = json.load(f)
        except Exception:
            pass

    return {
        "status": engine.get_status(),
        "recent_results": engine.get_recent_results(5),
        "created_skills": engine.get_created_skills(),
        "checkpoint": checkpoint,
        "strand_a_memories": len(engine.strand_a._memory),
        "strand_b_memories": len(engine.strand_b._memory),
        "quality": engine.get_evolution_quality(),
    }


@router.get("/history")
async def get_history(limit: int = 50):
    """获取进化历史（最近N个周期）"""
    engine = get_engine()
    if not engine:
        return {"error": "Evolution engine not initialized"}
    return {"history": engine.get_history(limit)}


@router.get("/goals")
async def get_goals():
    """获取进化目标列表"""
    engine = get_engine()
    if not engine:
        return {"error": "Evolution engine not initialized"}
    return {"goals": engine.get_goals()}


class AddGoalRequest(BaseModel):
    title: str
    description: str
    priority: str = "medium"


@router.post("/goals")
async def add_goal(req: AddGoalRequest):
    """添加新的进化目标"""
    engine = get_engine()
    if not engine:
        return {"error": "Evolution engine not initialized"}
    goal = engine.add_goal(req.title, req.description, req.priority)
    return {"ok": True, "goal": goal}


class UpdateGoalRequest(BaseModel):
    title: str | None = None
    description: str | None = None
    priority: str | None = None
    status: str | None = None
    progress: float | None = None


@router.put("/goals/{goal_id}")
async def update_goal(goal_id: str, req: UpdateGoalRequest):
    """更新进化目标"""
    engine = get_engine()
    if not engine:
        return {"error": "Evolution engine not initialized"}
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    result = engine.update_goal(goal_id, updates)
    if not result:
        return {"error": "Goal not found"}
    return {"ok": True, "goal": result}


@router.delete("/goals/{goal_id}")
async def delete_goal(goal_id: str):
    """删除进化目标"""
    engine = get_engine()
    if not engine:
        return {"error": "Evolution engine not initialized"}
    engine.delete_goal(goal_id)
    return {"ok": True}


@router.get("/stream")
async def evolution_log_stream(lines: int = 100):
    """SSE实时日志流 — 类似 tail -f"""
    from fastapi.responses import StreamingResponse
    from pathlib import Path
    import asyncio
    import json as _json

    engine = get_engine()
    if not engine:
        return {"error": "Evolution engine not initialized"}

    data_dir = Path(engine.repo_root) / "acp-proxy" / "data"
    log_files = sorted(data_dir.glob("dna_log_*.jsonl"))

    async def stream_generator():
        # 先发送最近N条历史日志
        for lf in log_files:
            try:
                all_lines = lf.read_text().strip().split("\n")
                for line in all_lines[-lines:]:
                    if line.strip():
                        yield f"data: {line}\n\n"
            except Exception:
                pass

        # 然后持续tail新日志
        sent_counts = {lf.name: 0 for lf in log_files}
        for lf in log_files:
            try:
                sent_counts[lf.name] = len(lf.read_text().strip().split("\n"))
            except Exception:
                pass

        while True:
            await asyncio.sleep(1)
            for lf in log_files:
                try:
                    all_lines = lf.read_text().strip().split("\n")
                    current_count = len(all_lines)
                    if current_count > sent_counts.get(lf.name, 0):
                        for line in all_lines[sent_counts[lf.name]:]:
                            if line.strip():
                                yield f"data: {line}\n\n"
                        sent_counts[lf.name] = current_count
                except Exception:
                    pass

    return StreamingResponse(
        stream_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/timeline")
async def get_timeline(days: int = 7):
    """获取进化时间线数据 — 按进化周期聚合为甘特图任务"""
    import json as _json
    from datetime import datetime, timedelta, timezone

    engine = get_engine()
    if not engine:
        return {"error": "Evolution engine not initialized"}

    data_dir = Path(engine.repo_root) / "acp-proxy" / "data"
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # 读取两条链的日志
    all_entries = []
    for lf in sorted(data_dir.glob("dna_log_*.jsonl")):
        try:
            for line in lf.read_text().strip().split("\n"):
                if not line.strip():
                    continue
                entry = _json.loads(line)
                ts = datetime.fromisoformat(entry["ts"])
                if ts >= cutoff:
                    all_entries.append(entry)
        except Exception:
            pass

    all_entries.sort(key=lambda e: e["ts"])

    # 按 cycle 分组为进化任务
    tasks = {}
    for entry in all_entries:
        cycle = entry.get("cycle", 0)
        strand = entry.get("strand", "unknown")
        task_key = f"{strand}_cycle_{cycle}"
        if task_key not in tasks:
            tasks[task_key] = {
                "taskId": task_key,
                "strand": strand,
                "cycle": cycle,
                "startTime": entry["ts"],
                "endTime": None,
                "status": "running",
                "steps": [],
                "summary": "",
            }
        task = tasks[task_key]
        task["endTime"] = entry["ts"]

        stage = entry.get("stage", "")
        msg = entry.get("msg", "")
        details = entry.get("details", {})

        task["steps"].append({
            "stage": stage,
            "ts": entry["ts"],
            "msg": msg[:200],
            "details": {k: str(v)[:100] for k, v in (details or {}).items()},
        })

        # 推断状态
        if stage == "verdict":
            verdict = details.get("verdict", "") if details else ""
            if verdict == "success" or "success" in str(verdict).lower():
                task["status"] = "success"
            elif verdict == "failed" or "fail" in str(verdict).lower():
                task["status"] = "error"
            else:
                task["status"] = "success"
        elif stage == "error":
            task["status"] = "error"

        # 生成摘要
        if stage == "turn" and not task["summary"]:
            task["summary"] = msg[:100]
        elif stage == "plan" and msg:
            task["summary"] = msg[:100]

    task_list = sorted(tasks.values(), key=lambda t: t["startTime"])

    # 读取目标
    goals = []
    goals_file = data_dir / "dna_goals.json"
    if goals_file.exists():
        try:
            goals = _json.loads(goals_file.read_text())
        except Exception:
            pass

    return {
        "tasks": task_list,
        "goals": goals,
        "totalTasks": len(task_list),
        "successCount": sum(1 for t in task_list if t["status"] == "success"),
        "errorCount": sum(1 for t in task_list if t["status"] == "error"),
    }


# ── 进化间隔配置 ──────────────────────────────────────

@router.get("/config")
async def get_evolution_config():
    """获取进化引擎配置"""
    import dna_evolution
    import evolution
    import dna_strand_runner
    return {
        "evolution_interval": dna_evolution.EVOLUTION_INTERVAL,
        "heartbeat_interval": dna_evolution.HEARTBEAT_INTERVAL,
        "heartbeat_timeout": dna_evolution.HEARTBEAT_TIMEOUT,
        "observe_interval": dna_strand_runner.OBSERVE_INTERVAL,
        "reflection_interval": evolution.REFLECTION_INTERVAL,
        "batch_analysis_interval": evolution.BATCH_ANALYSIS_INTERVAL,
        "llm_breaker_storm_window": dna_evolution.LLM_BREAKER_STORM_WINDOW,
        "llm_breaker_cooldown_s": dna_evolution.LLM_BREAKER_COOLDOWN_S,
    }


class UpdateConfigRequest(BaseModel):
    evolution_interval: Optional[int] = None
    heartbeat_interval: Optional[int] = None
    observe_interval: Optional[int] = None
    reflection_interval: Optional[int] = None
    batch_analysis_interval: Optional[int] = None


@router.post("/config")
async def update_evolution_config(req: UpdateConfigRequest):
    """动态修改进化引擎配置"""
    import dna_evolution
    import evolution
    import dna_strand_runner
    changes = {}
    if req.evolution_interval is not None:
        if req.evolution_interval < 1800:
            return {"error": "进化间隔不能小于30分钟（1800秒）"}
        dna_evolution.EVOLUTION_INTERVAL = req.evolution_interval
        changes["evolution_interval"] = req.evolution_interval
    if req.heartbeat_interval is not None:
        if req.heartbeat_interval < 3:
            return {"error": "心跳间隔不能小于3秒"}
        dna_evolution.HEARTBEAT_INTERVAL = req.heartbeat_interval
        changes["heartbeat_interval"] = req.heartbeat_interval
    if req.observe_interval is not None:
        if req.observe_interval < 30:
            return {"error": "自省间隔不能小于30秒"}
        dna_strand_runner.OBSERVE_INTERVAL = req.observe_interval
        changes["observe_interval"] = req.observe_interval
    if req.reflection_interval is not None:
        if req.reflection_interval < 120:
            return {"error": "反思间隔不能小于2分钟"}
        evolution.REFLECTION_INTERVAL = req.reflection_interval
        changes["reflection_interval"] = req.reflection_interval
    if req.batch_analysis_interval is not None:
        if req.batch_analysis_interval < 600:
            return {"error": "批量分析间隔不能小于10分钟"}
        evolution.BATCH_ANALYSIS_INTERVAL = req.batch_analysis_interval
        changes["batch_analysis_interval"] = req.batch_analysis_interval
    return {"ok": True, "changes": changes}


# ── 自我改进 API ──────────────────────────────────────────

DANGEROUS_PATTERNS = [
    "rm -rf", "rm -r /", "shutdown", "reboot", "drop table", "delete database",
    "format disk", "mkfs", "dd if=", "> /dev/", "chmod 777", "chown root",
    "disable auth", "remove password", "delete user", "kill -9", "pkill",
    "self_destruct", "self-destruct", "自毁", "删除自己", "删除所有",
    "关闭认证", "禁用认证", "去掉密码", "remove auth", "disable login",
    "drop_all", "truncate", "git push --force", "git reset --hard",
]

DANGEROUS_FILES = [
    "/etc/passwd", "/etc/shadow", ".env", "docker-compose.yml",
    "package.json", "requirements.txt", "main.py", "dna_evolution.py",
]


def _check_safety(description: str, target_file: str = "") -> tuple[bool, str]:
    """安全检查：拒绝危险操作"""
    desc_lower = description.lower()
    for pattern in DANGEROUS_PATTERNS:
        if pattern.lower() in desc_lower:
            return False, f"拒绝：检测到危险操作 '{pattern}'"
    if target_file:
        for f in DANGEROUS_FILES:
            if f in target_file:
                return False, f"拒绝：不允许修改核心文件 '{f}'"
    # 检查是否试图修改进化引擎自身
    if "dna_evolution" in desc_lower or "strand_runner" in desc_lower:
        return False, "拒绝：不允许修改进化引擎核心模块"
    return True, "ok"


class ImproveRequest(BaseModel):
    description: str
    target_file: str = ""
    requirements: str = ""


@router.post("/improve")
async def request_improvement(req: ImproveRequest):
    """Soulmate请求自我改进：描述需求 → 安全检查 → 生成代码 → 测试 → 提交"""
    # 安全检查
    safe, reason = _check_safety(req.description, req.target_file)
    if not safe:
        return {"ok": False, "error": reason}

    engine = get_engine()
    if not engine:
        return {"ok": False, "error": "进化引擎未初始化"}

    try:
        # 用进化引擎strand_a的LLM生成代码
        strand = engine.strand_a
        improvement = {
            "type": "skill" if not req.target_file else "code",
            "description": req.description,
            "target_file": req.target_file,
            "requirements": req.requirements,
        }

        code = await strand._generate_code(improvement)
        if not code or len(code.strip()) < 10:
            return {"ok": False, "error": "代码生成失败或内容过短"}

        # 语法检查（Python文件）
        if req.target_file.endswith(".py"):
            import ast
            try:
                ast.parse(code)
            except SyntaxError as e:
                return {"ok": False, "error": f"语法错误: {e}"}

        # 沙箱测试（仅Python文件）
        if req.target_file.endswith(".py"):
            sandbox_ok, sandbox_msg = await strand._sandbox_test(code)
            if not sandbox_ok:
                return {"ok": False, "error": f"沙箱测试失败: {sandbox_msg}"}

        # 自动生成目标文件名（如果未指定）
        if not req.target_file:
            # 根据描述生成文件名
            import re
            slug = re.sub(r'[^a-zA-Z0-9\u4e00-\u9fff]+', '_', req.description[:30]).strip('_').lower()
            req.target_file = f"src/lib/soulmate_{slug}.py"

        # 写入文件（统一防护入口）
        from pathlib import Path
        from utils.file_safety import safe_write
        target_path = Path(engine.repo_root) / req.target_file
        
        ok, err = safe_write(target_path, code, strand_id=strand.strand_id)
        if not ok:
            return {"ok": False, "error": err}

        # Git commit + push
        commit_msg = f"feat(soulmate): {req.description[:60]}"
        committed = strand._git_commit(req.target_file, commit_msg)

        return {
            "ok": True,
            "file": req.target_file,
            "committed": committed,
            "code_preview": code[:500],
        }

    except Exception as e:
        return {"ok": False, "error": str(e)}

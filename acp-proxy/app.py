"""FastAPI app for ACP Proxy service — 双实例交叉进化架构

每个实例运行一个进化strand作为asyncio task（不是子进程）。
实例A = strand_a (conservative), 实例B = strand_b (aggressive)
代码变更后通过exit code 42请求supervisor重启。
"""
import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from proxy import get_acp_process
from ws_chat import router as ws_router
from ws_group import router as ws_group_router
from ws_acp import ws_acp_endpoint
from mcp.server import router as mcp_router
from gateway.router import router as gateway_router
from model_router import get_model_router
from routes.skills import router as skills_router
from routes.evolution import router as evolution_router
from routes.gene_loop import router as gene_loop_router
from routes.architecture import router as architecture_router
from routes.routing import router as routing_router

logger = logging.getLogger("acp-proxy.app")

PLUGINS_DIR = Path(__file__).parent.parent / "plugins"

# 重启退出码（与supervisor.sh一致）
RESTART_EXIT_CODE = 42


def load_plugins(app: FastAPI) -> list[str]:
    """动态加载所有插件"""
    loaded = []
    if not PLUGINS_DIR.exists():
        return loaded
    plugins_str = str(PLUGINS_DIR)
    if plugins_str not in sys.path:
        sys.path.insert(0, plugins_str)
    for plugin_dir in sorted(PLUGINS_DIR.iterdir()):
        if not plugin_dir.is_dir():
            continue
        plugin_json = plugin_dir / "plugin.json"
        init_py = plugin_dir / "__init__.py"
        if not plugin_json.exists() or not init_py.exists():
            continue
        plugin_name = plugin_dir.name
        try:
            import json
            with open(plugin_json, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            plugin_module = __import__(plugin_name)
            if hasattr(plugin_module, "register_routes"):
                plugin_module.register_routes(app)
                logger.info("✅ 插件已加载: %s v%s - %s",
                    manifest.get("name", plugin_name),
                    manifest.get("version", "0.0.0"),
                    manifest.get("description", ""))
                loaded.append(plugin_name)
        except Exception as e:
            logger.error("❌ 插件 %s 加载失败: %s", plugin_name, e)
    return loaded


async def _run_evolution_strand(instance_id: str, repo_root: str):
    """在主进程中运行一个进化strand（asyncio task，不是子进程）"""
    from dna_evolution import DNAStrand, StrandRole

    strand_id = f"strand_{instance_id}"
    strategy = "conservative" if instance_id == "a" else "aggressive"
    partner_id = "strand_b" if instance_id == "a" else "strand_a"

    logger.info(f"🧬 Starting evolution strand {strand_id} ({strategy})")

    # 统一LLM配置解析（utils/llm_config单一真源）：禁止硬编码provider URL/模型名。
    # 历史教训：曾硬编码token-plan-cn.xiaomimimo.com + MIMO_API_KEY（从未设置）→
    # 无鉴权头401每小时空转。key缺失显式告警，不静默发无鉴权请求。
    from utils.llm_config import describe, resolve_llm_config
    llm_cfg = resolve_llm_config()
    if not llm_cfg["api_key"]:
        logger.warning(
            "🧬 进化strand无API key（MIMO_API_KEY/LLM_API_KEY均未配置）——"
            "LLM调用将失败并按'LLM失败'留痕（不会伪装成'无改进'）"
        )
    logger.info(f"🧬 进化LLM配置: {describe(llm_cfg)}")

    strand = DNAStrand(
        strand_id=strand_id,
        role=StrandRole.PRIMARY if instance_id == "a" else StrandRole.SHADOW,
        repo_root=repo_root,
        llm_base_url=llm_cfg["base_url"],
        strategy=strategy,
        llm_api_key=llm_cfg["api_key"],
        llm_model=llm_cfg["model"],
    )

    # 自观察循环
    async def self_observe():
        import subprocess
        while True:
            await asyncio.sleep(120)
            try:
                acp_dir = Path(repo_root) / "acp-proxy"
                py_files = list(acp_dir.rglob("*.py"))
                ts_files = list((Path(repo_root) / "src").rglob("*.ts")) if (Path(repo_root) / "src").exists() else []
                strand.observe("self_scan", f"System: {len(py_files)} Python, {len(ts_files)} TypeScript files")

                skills_dir = acp_dir / "skills"
                if skills_dir.exists():
                    skill_files = list(skills_dir.glob("*.json"))
                    strand.observe("self_scan", f"Skills: {len(skill_files)} registered")

                try:
                    result = subprocess.run(["git", "log", "--oneline", "-5"],
                        capture_output=True, text=True, cwd=str(acp_dir))
                    if result.returncode == 0 and result.stdout.strip():
                        strand.observe("self_scan", f"Recent git:\n{result.stdout.strip()}")
                except Exception:
                    pass

                status = strand.get_status()
                strand.observe("self_introspect",
                    f"Status: cycle={status['cycle_count']}, mem={status['memories']}, "
                    f"unanalyzed={status['observations_unanalyzed']}")
            except Exception as e:
                logger.warning(f"[{strand_id}] Self-observe error: {e}")

    # 文件变更监控
    async def file_watcher():
        from file_watcher import FileWatcher
        data_dir = Path(repo_root) / "acp-proxy" / "data"
        watcher = FileWatcher(data_dir, instance_id)
        await watcher.start()

    # 启动所有任务
    evo_task = asyncio.create_task(strand.run(partner_id))
    observe_task = asyncio.create_task(self_observe())
    watcher_task = asyncio.create_task(file_watcher())

    # 设置engine供API使用
    from routes.evolution import set_engine
    from dna_evolution import DNAEvolutionEngine
    engine = DNAEvolutionEngine(
        repo_root=repo_root,
        llm_base_url=llm_cfg["base_url"],
        llm_api_key=llm_cfg["api_key"],
        llm_model=llm_cfg["model"],
    )
    engine._running = True
    # 替换本实例的strand为真实的运行strand
    if instance_id == "a":
        engine.strand_a = strand
    else:
        engine.strand_b = strand
    set_engine(engine)

    logger.info(f"🧬 Evolution strand {strand_id} active in main process")

    # 永远等待（直到进程被杀或收到重启信号）
    try:
        await asyncio.gather(evo_task, observe_task, watcher_task)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error(f"Evolution strand error: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """ACP Proxy生命周期"""
    instance_id = os.environ.get("INSTANCE_ID", "a")
    logger.info(f"ACP Proxy instance {instance_id} starting")

    loaded_plugins = load_plugins(app)
    if loaded_plugins:
        logger.info("已加载 %d 个插件: %s", len(loaded_plugins), ", ".join(loaded_plugins))

    # 启动时探测模型能力（只执行一次）——配置走 utils.llm_config 单一真源
    # （旧实现自扫 os.environ：key 只写在 opensoul/.env 时探测被静默跳过，
    #   且与 resolve_llm_config"文件>env"语义不一致）
    try:
        from utils.llm_config import resolve_llm_config
        from utils.token_manager import probe_model_capabilities
        probe_cfg = resolve_llm_config()
        if probe_cfg["base_url"] and probe_cfg["api_key"]:
            probe_model_capabilities(
                probe_cfg["base_url"],
                probe_cfg["api_key"],
                probe_cfg["model"],
                max_tokens=probe_cfg["max_tokens"],
            )
        else:
            logger.warning(
                "Token probe skipped: LLM配置缺失（key_source=%s）",
                probe_cfg.get("key_source") or "<unset>",
            )
    except Exception as e:
        logger.warning(f"Token probe failed: {e}")

    # 启动进化strand（主进程asyncio task，不是子进程）
    evolution_task = None
    try:
        repo_root = str(Path(__file__).parent.parent)
        evolution_task = asyncio.create_task(
            _run_evolution_strand(instance_id, repo_root)
        )
    except Exception as e:
        logger.warning(f"Evolution strand failed to start: {e}")

    # 初始化架构增强系统（P0组件）
    try:
        from agent.architecture_enhanced import EnhancedArchitecture
        from agent import arch_monitor
        arch = EnhancedArchitecture()
        await arch.start()
        arch_monitor.set_architecture(arch)
        logger.info("Enhanced architecture initialized")
    except Exception as e:
        logger.warning(f"Architecture init failed: {e}")

    # P0-4 ScheduleWakeup执行侧闭环（claude-code runtime"到点重新唤醒agent"）：
    # executor轮询opensoul due→CAS领取→无客户端唤醒回合→结果回执。
    # a/b双实例都跑，opensoul claim CAS保证每次唤醒只执行一次（at-most-once）。
    wakeup_executor = None
    if os.environ.get("WAKEUP_EXECUTOR_ENABLED", "1") not in ("0", "false", ""):
        try:
            from agent.wakeup_executor import get_wakeup_executor
            wakeup_executor = get_wakeup_executor()
            await wakeup_executor.start()
            logger.info("Wakeup executor started")
        except Exception as e:
            logger.warning(f"Wakeup executor failed to start: {e}")

    yield

    if wakeup_executor:
        try:
            await wakeup_executor.stop()
        except Exception as e:
            logger.warning(f"Wakeup executor stop error: {e}")

    if evolution_task:
        evolution_task.cancel()
        try:
            await evolution_task
        except asyncio.CancelledError:
            pass

    logger.info(f"ACP Proxy instance {instance_id} stopped")


app = FastAPI(title="ACP Proxy", version="3.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 核心路由
app.include_router(ws_router)
app.include_router(ws_group_router)
app.include_router(mcp_router)
app.include_router(gateway_router)
app.include_router(skills_router)
app.include_router(evolution_router)
app.include_router(gene_loop_router)
app.include_router(architecture_router)
app.include_router(routing_router)


@app.websocket("/ws/acp")
async def ws_acp_route(websocket: WebSocket):
    await ws_acp_endpoint(websocket)


@app.websocket("/ws/a2a")
async def ws_a2a_route(websocket: WebSocket):
    from ws_a2a import ws_a2a_endpoint
    await ws_a2a_endpoint(websocket)


@app.websocket("/ws/mcp")
async def ws_mcp_route(websocket: WebSocket):
    from ws_mcp import ws_mcp_endpoint
    await ws_mcp_endpoint(websocket)


# ── P0-4: agent活动观测（goose peek三指标 + claude-code noop自报 + 插话队列）──
# soulmate agent以stdio子进程运行，活动快照持久化在SQLite，本进程跨进程读取。
_activity_store_singleton = None


def _activity_store():
    global _activity_store_singleton
    if _activity_store_singleton is None:
        from agent.steering import ActivityStore
        _activity_store_singleton = ActivityStore()
    return _activity_store_singleton


@app.get("/api/agent/peek")
async def agent_peek_all():
    """goose peek三指标聚合：durable turn数 / idle时长 / buffered通知数 + noop streak"""
    try:
        return _activity_store().peek_all()
    except Exception as e:
        return {"sessions": [], "summary": {"error": str(e)}}


@app.get("/api/agent/peek/{session_id}")
async def agent_peek(session_id: str):
    """单会话peek — '不知道它在干嘛'的直接答案"""
    data = _activity_store().peek(session_id)
    if data is None:
        return {"error": f"no activity recorded for session {session_id}"}
    return data


def _tool_output_stats():
    """P0-2: 工具结果溢出统计（AIHawk SHOWN/SENT双预算账本 + goose spill落盘事实）。
    agent子进程写JSONL账本/落盘文件，本进程跨进程读取（账本+spill目录都是共享文件系统真源）。
    阈值与soulmate agent一致（env驱动），否则面板显示的阈值与agent实际行为不符。"""
    from agent.tool_output_handler import ToolOutputHandler
    return ToolOutputHandler(
        char_threshold=int(os.environ.get("TOOL_SPILL_CHARS", "8000")),
        line_threshold=int(os.environ.get("TOOL_SPILL_LINES", "2000")),
    ).get_stats()


@app.get("/api/agent/tool-output/stats")
async def agent_tool_output_stats():
    """P0-2溢出统计：截断次数/SHOWN vs SENT双预算/按工具分布/最近spill"""
    try:
        return _tool_output_stats()
    except Exception as e:
        return {"error": str(e)}


def _token_attribution_stats(limit: int | None = None):
    """P1: 上下文逐项token归因（claude-code SDKContextUsage移植）——"上下文被什么吃掉了"。
    soulmate agent子进程写JSONL账本，本进程跨进程读取（与tool_output账本同模式，共享文件系统真源）。"""
    from agent.token_attribution import AttributionLedger
    return AttributionLedger().get_stats(limit=limit)


@app.get("/api/agent/token-attribution")
async def agent_token_attribution(limit: int = 20):
    """P1: 上下文逐项token归因——每个工具定义/每条记忆/每个skill/系统提示各多少token，
    top_consumers=什么最吃上下文，over_limit区分hard_limit/compaction_window两种超限性质"""
    try:
        return _token_attribution_stats(limit=min(max(limit, 1), 100))
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/agent/wakeup-executor")
async def agent_wakeup_executor():
    """P0-4 ScheduleWakeup执行侧闭环可观测（goose peek同款"它在干嘛"）：
    polls/claimed/fire_ok/fire_failed/claim_lost计数 + last_error——
    "到点到底有没有真醒"一眼可见，不是只告诉用户"在运行"。"""
    try:
        from agent.wakeup_executor import get_wakeup_executor
        return get_wakeup_executor().snapshot()
    except Exception as e:
        return {"error": str(e)}


# ── kilocode supplement3 #15: 网络受限会话工具面收缩（策略API）──
# agent子进程与本进程共享JSON store（文件真源），跨进程读写同款SandboxPolicy。
def _sandbox_policy():
    from agent.sandbox_policy import SandboxPolicy
    return SandboxPolicy()


@app.get("/api/agent/sandbox-policy")
async def sandbox_policy_snapshot():
    """全局策略快照（default/env/per-session覆盖/store健康）"""
    try:
        return _sandbox_policy().snapshot()
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/agent/sandbox-policy/{session_id}")
async def sandbox_policy_describe(session_id: str):
    """会话策略+受影响网络类工具面（"限制了什么"可观测）。

    MCP动态工具（server__tool）同属网络类但清单由agent侧持有，具体裁剪
    以agent日志`[sandbox]`行为准。"""
    try:
        from agent.sandbox_policy import NETWORK_TOOL_NAMES
        from agent.mcp_resources import MCP_RESOURCE_TOOL_NAMES
        static_names = sorted(set(NETWORK_TOOL_NAMES) | set(MCP_RESOURCE_TOOL_NAMES))
        data = _sandbox_policy().describe(session_id, tool_names=static_names)
        data["note"] = ("MCP动态工具(server__tool)同样按网络类裁剪；"
                        "生效清单见agent日志[sandbox]行")
        return data
    except Exception as e:
        return {"error": str(e)}


@app.put("/api/agent/sandbox-policy/{session_id}")
async def sandbox_policy_set(session_id: str, req: dict):
    """设置会话网络限制 {"network_restricted": true|false}（per-session显式覆盖）"""
    if "network_restricted" not in req:
        return {"error": "missing field: network_restricted"}
    ok = _sandbox_policy().set_restricted(session_id, bool(req["network_restricted"]))
    return {"ok": ok, "session_id": session_id,
            "network_restricted": bool(req["network_restricted"])}


@app.delete("/api/agent/sandbox-policy/{session_id}")
async def sandbox_policy_clear(session_id: str):
    """清除per-session覆盖（回到default/env语义）"""
    ok = _sandbox_policy().clear_session(session_id)
    return {"ok": ok, "session_id": session_id}


# 网络恢复回调注册（kilocode network.ts reply()的MCP重连语义）：离线park恢复
# 后把mcp-client(:8094)里status=="error"的Server重新connect（跨进程HTTP）
try:
    from utils import network_state as _network_state
    _network_state.register_restore_callback(_network_state.reconnect_failed_mcp_servers)
except Exception:
    _network_state = None


@app.get("/api/network/status")
async def network_status():
    """网络离线三态可观测（kilocode SessionNetwork wait/peek语义）：
    汇总（parks/resumed/aborted/pending/human_rejected）+ 全部wait记录（blocked/
    resumed/aborted）。
    跨进程合并：park真实发生在agent子进程（ws_acp spawn的agent.start --stdio，
    真实chat turn的LLM调用面），本进程内存态只有discussion park——合并读取
    子进程per-pid快照（load_park_state）后，"agent是不是在等网络恢复"对真实
    chat回合才真正可见（此前子进程park对本端点不可见=观测面形同虚设）。
    "agent是不是在等网络恢复"必须可见——直接回答用户可观测性诉求，
    不新建页面（monitoring页/health探测本端点即可）。"""
    if _network_state is None:
        return {"error": "network_state unavailable"}
    remote = _network_state.load_park_state(exclude_pid=os.getpid())
    return _network_state.merge_park_states(
        _network_state.stats(), _network_state.list_waits(), remote
    )


from pydantic import BaseModel as _PydanticBaseModel  # noqa: E402


class NetworkRejectRequest(_PydanticBaseModel):
    wait_id: str | None = None


@app.post("/api/network/reject")
async def network_reject(req: NetworkRejectRequest | None = None):
    """HITL人工拒绝（kilocode"等待网络恢复…[拒绝]"卡片的按钮后端）：
    显式wait_id=拒绝该等待；缺省body=拒绝当前全部pending（用户看到的就是这些，
    显式id列表不设通配——通配标志会误伤拒绝后新起的park）。
    写跨进程拒绝标志文件，park所在进程（含agent子进程）下一轮询消费即中止，
    走既有"[LLM错误: …]"可见失败契约（wait.error=human_rejected可观测）。"""
    if _network_state is None:
        return {"error": "network_state unavailable"}
    if req is not None and req.wait_id:
        ids = [req.wait_id]
    else:
        merged = _network_state.merge_park_states(
            _network_state.stats(),
            _network_state.list_waits(),
            _network_state.load_park_state(exclude_pid=os.getpid()),
        )
        ids = [w["id"] for w in merged["waits"] if w.get("state") == "blocked"]
    flag = _network_state.request_reject(ids)
    return {"ok": True, "rejected_ids": ids, "flag": flag}


@app.get("/health")
async def health():
    """⚠️ 本路由被ws_chat.py的/health遮蔽——app.py:224 include_router(ws_router)先注册，
    FastAPI首匹配胜出，live /health应答方是ws_chat.ws_chat_health（本轮live curl取证：
    :8092/health返回{"status":"ok","component":"WSChat"}，本handler从未被命中）。
    观测性聚合（agent_activity/tool_output/token_attribution+calibration）已迁至
    ws_chat侧live路由承载；本handler保留作include顺序变化时的兜底镜像。
    新增health观测字段请同时/优先加到ws_chat.ws_chat_health。"""
    instance_id = os.environ.get("INSTANCE_ID", "a")
    payload: dict = {"status": "ok", "service": "acp-proxy", "instance": instance_id}
    # P0-4: peek统计并入health——既有monitoring页探测health即可看到agent活动状态，不新建页面
    try:
        payload["agent_activity"] = _activity_store().peek_all()["summary"]
    except Exception:
        pass
    # P0-2: 工具结果溢出统计摘要并入health（SHOWN/SENT双预算可观测）
    try:
        _ts = _tool_output_stats()
        payload["tool_output"] = {
            k: _ts.get(k)
            for k in ("total_spills", "total_calls", "truncated_calls",
                      "sent_chars_total", "shown_chars_total", "char_threshold", "line_threshold")
        }
    except Exception:
        pass
    # P1: token归因摘要并入health（最近上下文总量/超限记录数——monitoring页免额外请求可见）
    try:
        _ta = _token_attribution_stats()
        payload["token_attribution"] = {
            "total_records": _ta.get("total_records", 0),
            **{k: _ta.get("summary", {}).get(k)
               for k in ("over_limit_records", "avg_total_tokens", "max_total_tokens",
                         "backfill_count", "avg_estimate_gap",
                         # d439f163遗留#3：估算校准状态（sample_count/calibrated/factor）
                         "calibration")},
        }
    except Exception:
        pass
    # kilocode supplement3 #17：双限额上下文预算推导链（与ws_chat.ws_chat_health同步镜像）
    try:
        from agent.context_budget import budget_snapshot
        payload["context_budget"] = budget_snapshot()
    except Exception:
        pass
    return payload


@app.get("/api/file")
async def serve_file(path: str):
    """Serve a local file for download (used by MEDIA: tag in chat messages)."""
    import mimetypes
    from fastapi.responses import FileResponse
    if not path or not os.path.exists(path):
        return {"error": "File not found"}
    # Security: only allow files under user home（2026-09-21治理：env可覆盖，默认不变）
    _allowed_root = os.environ.get("USER_HOME", "/home/climbing")
    real_path = os.path.realpath(path)
    if not real_path.startswith(_allowed_root):
        return {"error": "Access denied"}
    mime_type = mimetypes.guess_type(real_path)[0] or "application/octet-stream"
    return FileResponse(real_path, media_type=mime_type, filename=os.path.basename(real_path))


@app.get("/plugins")
async def list_plugins():
    plugins = []
    if PLUGINS_DIR.exists():
        for plugin_dir in sorted(PLUGINS_DIR.iterdir()):
            plugin_json = plugin_dir / "plugin.json"
            if plugin_json.exists():
                import json
                with open(plugin_json, "r", encoding="utf-8") as f:
                    manifest = json.load(f)
                plugins.append({
                    "name": manifest.get("name", plugin_dir.name),
                    "version": manifest.get("version", "0.0.0"),
                    "description": manifest.get("description", ""),
                    "status": "loaded",
                    "manifest": manifest
                })
    return {"plugins": plugins, "count": len(plugins)}


from pydantic import BaseModel as PydanticBaseModel

class SetModeRequest(PydanticBaseModel):
    mode: str

@app.get("/api/model-router/config")
async def model_router_config():
    router = get_model_router()
    return router.get_config()

@app.post("/api/model-router/mode")
async def model_router_set_mode(req: SetModeRequest):
    router = get_model_router()
    success = router.set_mode(req.mode)
    if not success:
        return {"success": False, "error": f"未知模式: {req.mode}"}
    return {"success": True, "mode": req.mode, "status": router.get_status()}

@app.get("/api/model-router/status")
async def model_router_status():
    router = get_model_router()
    return router.get_status()

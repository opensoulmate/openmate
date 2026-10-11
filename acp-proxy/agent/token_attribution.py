"""P0-4/P1: 上下文逐项token归因（agent运行时侧） — claude-code SDKContextUsage移植

调研来源：feature-matrix/10-claude-code-source.md #7（SDKContextUsage：total_tokens/
raw_max_tokens/percentage/over_limit{tokens_over, kind: hard_limit|compaction_window} +
四类明细 mcp_tools[]/memory_files[]/agents[]/skills[]，每项多少token一目了然）；
SUMMARY.md P0-4"用户极度重视可观测性→此P0含金量最高"+P1"token逐项归因（per-tool/per-agent）"。

镜像关系：opensoul/src/cortex/token_attribution.py 是本模块在OpenSoul侧的规范实现；
acp-proxy是独立venv/独立进程，无法import opensoul，故此为同启发式镜像实现。
两侧estimate_tokens公式必须一致（两侧测试套件各自断言同一公式）。

账本模式（与tool_output spill账本同构，见agent/tool_output_handler.py）：
soulmate agent以stdio子进程运行，归因记录写JSONL账本；app.py API进程跨进程读取。
账本是共享文件系统真源——"agent写、API进程读"。

失败纪律（mem0 §1.1 + 本项目fail-safe惯例）：归因是观测性旁路，任何异常不得阻断
agent执行路径（soulmate_agent调用点全部try/except，失败仅debug日志）。
"""
from __future__ import annotations

import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from agent import retention

logger = logging.getLogger("acp-proxy.token-attribution")

# ── item kinds（SDKContextUsage四类明细 + OpenSoul/agent侧扩展类别）──
KIND_MCP_TOOL = "mcp_tool"
KIND_BUILTIN_TOOL = "builtin_tool"
KIND_EVOLUTION_TOOL = "evolution_tool"
KIND_TOOL = "tool"
KIND_MEMORY = "memory_file"
KIND_AGENT = "agent"
KIND_SKILL = "skill"
KIND_SYSTEM_PROMPT = "system_prompt"
KIND_MESSAGE = "message"
KIND_TOOL_RESULT = "tool_result"
KIND_PREFERENCE = "preference"
KIND_IMPROVEMENT = "improvement"
KIND_OTHER = "other"

_LIST_KEYS = {
    KIND_MCP_TOOL: "mcp_tools",
    KIND_BUILTIN_TOOL: "builtin_tools",
    KIND_EVOLUTION_TOOL: "evolution_tools",
    KIND_TOOL: "tools",
    KIND_MEMORY: "memory_files",
    KIND_AGENT: "agents",
    KIND_SKILL: "skills",
}

# 模型上下文窗口粗表（子串匹配）。agent侧默认窗口与llm_engine._truncate_context
# 默认max_tokens=32000对齐，可用AGENT_CONTEXT_WINDOW env覆盖（实际窗口受本地
# ollama num_ctx / 远端模型规格影响，粗表仅用于percentage与超限性质判定）。
MODEL_CONTEXT_LIMITS: dict[str, int] = {
    "deepseek": 65536,
    "mimo": 65536,
    "qwen": 32768,
    "glm": 128000,
    "gpt-4o": 128000,
    "gpt-4": 128000,
    "claude": 200000,
    "kimi": 128000,
}
DEFAULT_CONTEXT_WINDOW = 32000
DEFAULT_COMPACTION_RATIO = 0.75  # agent.context_budget.TokenBudget.compression_threshold同值

# ── estimate_tokens公式校准（d439f163遗留#3：estimate_gap信号此前只采集不消费）──
# 校准语义（两侧镜像一致，opensoul/src/cortex/token_attribution.py同值同逻辑）：
# factor = Σactual / Σestimated — provider权威prompt_tokens与启发式估算总量之比。
# estimated≤0或actual为None的样本剔除；样本数<MIN_CALIBRATION_SAMPLES → calibrated=False
# 且factor=1.0（fail-safe：样本不足时估算器行为完全不变，只观测不校正）；
# factor夹在CALIBRATION_FACTOR_BOUNDS内防脏数据把估算拉飞。
# 消费点：①build_context_usage(calibrated_*字段) ②ContextBudgetManager.manage裁剪决策
# （canonical估算×factor——系统性低估时provider视角提前触发裁剪）。
MIN_CALIBRATION_SAMPLES = 3
CALIBRATION_FACTOR_BOUNDS = (0.5, 4.0)
CALIBRATION_CACHE_TTL = 300.0  # 秒：账本校准因子缓存（agent每轮record，避免每轮重读账本尾部）


def compute_calibration(pairs: list[dict]) -> dict:
    """从(estimated, actual)样本对计算估算器校准因子（两侧镜像公式一致）。

    pairs: [{"estimated": int, "actual": int}, ...]（estimated=usage.total_tokens估算值，
    actual=provider权威prompt_tokens）。返回：
    - sample_count: 有效样本数（estimated>0且actual非None）
    - calibrated: 是否达到最小样本数（不足时factor恒1.0）
    - factor: 夹限后的校准因子（未校准时1.0）
    - avg_estimate_gap: 平均偏差(actual-estimated)，无有效样本为None
    - calibrated时附加raw_factor/sum_estimated/sum_actual（审计溯源）
    """
    valid: list[tuple[int, int]] = []
    for p in pairs or []:
        e = (p or {}).get("estimated")
        a = (p or {}).get("actual")
        if e is None or a is None:
            continue
        e, a = int(e), int(a)
        if e > 0 and a >= 0:
            valid.append((e, a))
    sample_count = len(valid)
    if sample_count < MIN_CALIBRATION_SAMPLES:
        avg_gap = (
            sum(a - e for e, a in valid) // sample_count if sample_count else None
        )
        return {
            "sample_count": sample_count,
            "calibrated": False,
            "factor": 1.0,
            "avg_estimate_gap": avg_gap,
        }
    sum_est = sum(e for e, _ in valid)
    sum_act = sum(a for _, a in valid)
    raw_factor = sum_act / sum_est
    lo, hi = CALIBRATION_FACTOR_BOUNDS
    factor = round(min(hi, max(lo, raw_factor)), 4)
    return {
        "sample_count": sample_count,
        "calibrated": True,
        "factor": factor,
        "raw_factor": round(raw_factor, 4),
        "avg_estimate_gap": (sum_act - sum_est) // sample_count,
        "sum_estimated": sum_est,
        "sum_actual": sum_act,
    }


def resolve_context_window(model: str | None, default: int = DEFAULT_CONTEXT_WINDOW) -> int:
    if not model:
        return default
    m = model.lower()
    for key, window in MODEL_CONTEXT_LIMITS.items():
        if key in m:
            return window
    return default


def estimate_tokens(text: str) -> int:
    """token估算（CJK≈1token/字符，其余chars//4）。与opensoul侧公式一致。"""
    if not text:
        return 0
    cjk = 0
    for ch in text:
        o = ord(ch)
        if 0x4E00 <= o <= 0x9FFF or 0x3000 <= o <= 0x303F or 0xFF00 <= o <= 0xFFEF:
            cjk += 1
    return cjk + (len(text) - cjk) // 4


# ── 19.1 多模态内容估算（17.1 诚实边界③销账）────────────────────────
# MiMo 官方 audio token 计量：约 秒×6.25（mimo.mi.com token-plan pricing，周期#98查实）。
AUDIO_TOKENS_PER_SECOND = 6.25
# 未压缩参考密度：16kHz 16bit 单声道 PCM = 32000 字节/秒。压缩格式每秒字节更少，
# 按此折算秒数只会低估不会虚高（保守下界，显式不假称精确）。
_PCM_BYTES_PER_SECOND = 32000
# 图像块无可本地折算的官方公式（精确计数需解码像素），按业界低细节近似常量计；
# 绝不把 base64 载荷当文本计（旧 str(list) 形态每图虚高数万 token）。
IMAGE_TOKENS_FALLBACK = 1100


def _b64_payload(data: str) -> str:
    """剥 data URL 前缀取裸 base64 载荷。"""
    if not data:
        return ""
    if data.startswith("data:") and "," in data:
        return data.split(",", 1)[1]
    return data


def estimate_audio_tokens(data: str) -> int:
    """音频载荷→token 估算（秒×6.25，19.1）。

    WAV 头解析真实时长（byte_rate@28）；其他容器/解析失败按字节保守下界
    （PCM 密度折算秒数）——估算口径不假称精确。
    """
    payload = _b64_payload(data)
    if not payload:
        return 0
    total_bytes = (len(payload) * 3) // 4
    seconds = None
    try:
        import base64
        head = payload[:96]
        head += "=" * (-len(head) % 4)
        raw = base64.b64decode(head)[:64]  # 只解码头部，防整段大 blob 解码
        if raw[:4] == b"RIFF" and raw[8:12] == b"WAVE" and len(raw) >= 32:
            byte_rate = int.from_bytes(raw[28:32], "little")
            if byte_rate > 0:
                seconds = max(0.0, total_bytes / byte_rate)
    except Exception:
        seconds = None
    if seconds is None:
        seconds = total_bytes / _PCM_BYTES_PER_SECOND
    return int(seconds * AUDIO_TOKENS_PER_SECOND)


def _strip_payload(obj):
    """递归剥掉 base64/blob 载荷字段（未知块估算只计元数据文本）。"""
    if isinstance(obj, dict):
        return {k: _strip_payload(v) for k, v in obj.items()
                if k not in ("data", "blob")}
    return obj


def _estimate_block(block) -> int:
    """单个内容块估算：text→文本公式、input_audio→秒×6.25、image→近似常量，
    未知块只计非载荷元数据文本（base64/blob 载荷绝不当文本计）。"""
    if isinstance(block, dict):
        btype = str(block.get("type") or "")
        if btype == "text":
            return estimate_tokens(str(block.get("text") or ""))
        if btype == "input_audio":
            ia = block.get("input_audio") or {}
            return estimate_audio_tokens(str(ia.get("data") or ""))
        if btype == "image":
            return IMAGE_TOKENS_FALLBACK
        return estimate_tokens(str(_strip_payload(block)))
    # SDK 对象形态（acp Pydantic block）兜底，同语义按属性取
    btype = str(getattr(block, "type", "") or "")
    if btype == "text":
        return estimate_tokens(str(getattr(block, "text", "") or ""))
    if btype == "input_audio":
        return estimate_audio_tokens(str(getattr(block, "data", "") or ""))
    if btype == "image":
        return IMAGE_TOKENS_FALLBACK
    return estimate_tokens(str(btype))


def estimate_content_tokens(content) -> int:
    """消息 content 估算——兼容 str 与多模态 block 列表（19.1）。

    旧形态 str(content) 对 list 会把整段 base64 载荷当文本计 token（音频/图像
    虚高数万，裁剪提前+归因失真），此处按块语义计。"""
    if content is None:
        return 0
    if isinstance(content, str):
        return estimate_tokens(content)
    if isinstance(content, (list, tuple)):
        return sum(_estimate_block(b) for b in content)
    return estimate_tokens(str(content))


@dataclass
class ContextItem:
    kind: str
    name: str
    source: str = ""
    tokens: int = 0
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = {"name": self.name, "source": self.source, "tokens": self.tokens}
        if self.extra:
            d.update(self.extra)
        return d


def items_from_openai_tools(
    tools: list[dict] | None, source: str, kind: str | None = None
) -> list[ContextItem]:
    """OpenAI function-calling工具定义 → 逐工具ContextItem（per-tool归因：
    "262个工具定义里哪个最吃上下文"的直接答案）。"""
    if not tools:
        return []
    if kind is None:
        kind = {
            "mcp": KIND_MCP_TOOL,
            "builtin": KIND_BUILTIN_TOOL,
            "evolution": KIND_EVOLUTION_TOOL,
        }.get(source, KIND_TOOL)
    items: list[ContextItem] = []
    for t in tools:
        try:
            fn = (t or {}).get("function", {}) or {}
            name = str(fn.get("name") or "unnamed_tool")
            tokens = estimate_tokens(json.dumps(t, ensure_ascii=False))
            items.append(ContextItem(kind=kind, name=name, source=source, tokens=tokens))
        except Exception:
            continue
    return items


def build_context_usage(
    items: list[ContextItem],
    max_tokens: int | None = None,
    compaction_tokens: int | None = None,
    model: str | None = None,
    calibration_factor: float | None = None,
) -> dict:
    """构建SDKContextUsage形态归因结果（与opensoul侧同构）。

    calibration_factor（d439f163遗留#3估算校准）：provider回填推出的Σactual/Σestimated
    因子；提供时输出calibrated_total_tokens/calibrated_percentage/calibrated_over_limit
    三个校准后字段（raw字段保持启发式原值不变——两套数字并存，估算器偏差对观测者可见）。
    None/未校准时factor按1.0处理（校准值与raw值一致，schema稳定）。
    """
    if max_tokens is None:
        max_tokens = resolve_context_window(model)
    max_tokens = max(1, int(max_tokens))
    if compaction_tokens is None:
        compaction_tokens = int(max_tokens * DEFAULT_COMPACTION_RATIO)

    total = sum(i.tokens for i in items)
    percentage = round(total * 100.0 / max_tokens, 1)

    over_limit: dict | None = None
    if total > max_tokens:
        over_limit = {"tokens_over": total - max_tokens, "kind": "hard_limit"}
    elif total > compaction_tokens:
        over_limit = {"tokens_over": total - compaction_tokens, "kind": "compaction_window"}

    # ── 估算校准后判定（d439f163遗留#3）：raw over_limit不动，校准值并排输出 ──
    _factor = 1.0 if not calibration_factor else max(0.01, float(calibration_factor))
    calibrated_total = int(round(total * _factor))
    calibrated_percentage = round(calibrated_total * 100.0 / max_tokens, 1)
    calibrated_over_limit: dict | None = None
    if calibrated_total > max_tokens:
        calibrated_over_limit = {
            "tokens_over": calibrated_total - max_tokens, "kind": "hard_limit",
        }
    elif calibrated_total > compaction_tokens:
        calibrated_over_limit = {
            "tokens_over": calibrated_total - compaction_tokens, "kind": "compaction_window",
        }

    lists: dict[str, list] = {k: [] for k in _LIST_KEYS.values()}
    sections: dict[str, int] = {}
    for it in items:
        key = _LIST_KEYS.get(it.kind)
        if key:
            lists[key].append(it.to_dict())
        else:
            sections[it.kind] = sections.get(it.kind, 0) + it.tokens

    ranked = sorted(items, key=lambda i: i.tokens, reverse=True)
    top = [
        {
            "name": i.name,
            "kind": i.kind,
            "source": i.source,
            "tokens": i.tokens,
            "percentage": round(i.tokens * 100.0 / max_tokens, 1),
        }
        for i in ranked[:10]
    ]

    return {
        "total_tokens": total,
        "raw_max_tokens": max_tokens,
        "compaction_tokens": compaction_tokens,
        "percentage": percentage,
        "over_limit": over_limit,
        "calibration_factor": round(_factor, 4),
        "calibrated_total_tokens": calibrated_total,
        "calibrated_percentage": calibrated_percentage,
        "calibrated_over_limit": calibrated_over_limit,
        **lists,
        "sections": sections,
        "top_consumers": top,
        "item_count": len(items),
    }


class AttributionLedger:
    """JSONL归因账本 — agent子进程写 / app.py API进程跨进程读。

    与ToolOutputHandler spill账本同构：账本文件是共享文件系统真源，
    写入方和读取方可以是不同进程/不同实例。
    """

    def __init__(
        self,
        ledger_dir: str = "",
        max_recent: int = 20,
        retention_days: float = retention.DEFAULT_RETENTION_DAYS,
        cleanup_interval: float = retention.DEFAULT_SWEEP_INTERVAL,
    ):
        if not ledger_dir:
            ledger_dir = str(Path.home() / ".hermes" / "soulmate" / "token_attribution")
        self.ledger_dir = Path(ledger_dir)
        self.max_recent = max_recent
        self.ledger_path = self.ledger_dir / "attribution_ledger.jsonl"
        # kilocode #2保留策略（上轮遗留#4销账）：账本7天ts轮转+每小时清扫
        self.retention_days = retention_days
        self.cleanup_interval = cleanup_interval
        # 校准因子缓存（agent工具循环每轮record，校准因子按TTL缓存避免每轮重读账本）
        self._calibration_cache: tuple[float, float] | None = None
        try:
            self.ledger_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

    def maybe_cleanup(self):
        """kilocode #2保留策略统一清扫（上轮遗留#4："token_attribution账本未接
        retention轮转"销账）：账本按记录ts做7天保守轮转（坏行/无ts一律保留——
        retention.compact_jsonl语义），每小时最多一次（retention.maybe_sweep节流）。
        失败仅WARNING绝不反噬归因记录路径。返回清扫结果dict或None（节流跳过）。
        """
        def _sweep():
            return {"ledger": retention.compact_jsonl(
                self.ledger_path, max_age_days=self.retention_days)}

        return retention.maybe_sweep(
            f"token-attribution:{self.ledger_path}",
            _sweep, interval_s=self.cleanup_interval)

    def record(
        self,
        usage: dict,
        session_id: str = "",
        model: str = "",
        extra: dict | None = None,
    ) -> dict:
        rec = {
            "ts": time.time(),
            "session_id": session_id,
            "model": model,
            "usage": usage,
        }
        if extra:
            rec["extra"] = extra
        try:
            with open(self.ledger_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.debug("token attribution ledger write failed (non-fatal): %s", exc)
        # kilocode #2：写路径顺带触发保留策略清扫（每小时最多一次，失败非致命）
        try:
            self.maybe_cleanup()
        except Exception as _cl_err:
            logger.debug("token attribution retention cleanup skipped (non-fatal): %s", _cl_err)
        return rec

    def backfill_actual(
        self,
        session_id: str,
        actual_prompt_tokens: int | None,
        round_index: int | None = None,
    ) -> dict | None:
        """P1 provider usage回填（opensoul ContextAttributor.backfill_actual镜像语义）：
        LLM请求的provider响应里拿到权威prompt_tokens后，匹配最近一条同session归因
        记录，计算estimate_gap = actual - usage.total_tokens——gap即"隐藏开销"（provider
        侧chat模板/系统提示等我方估算未覆盖的部分），也是估算器自我校准信号源。

        账本模式差异（vs opensoul进程内deque）：本侧记录与回填都是JSONL追加行
        （append-only审计，mem0 §1.2），backfill行带backfill:true标记与归因记录区分；
        round_index标注该usage来自工具循环第几轮（上下文逐轮增长，gap按轮配对读取）。

        找不到匹配session记录/actual为None → 返回None（fail-safe，不新建不抛）。
        """
        if actual_prompt_tokens is None:
            return None
        try:
            recs = self._read_tail()
            matched = None
            for r in reversed(recs):
                if r.get("backfill"):
                    continue
                if r.get("session_id") == session_id and r.get("usage"):
                    matched = r
                    break
            if matched is None:
                return None
            gap = int(actual_prompt_tokens) - int(
                matched.get("usage", {}).get("total_tokens", 0)
            )
            row = {
                "backfill": True,
                "ts": time.time(),
                "session_id": session_id,
                "actual_prompt_tokens": int(actual_prompt_tokens),
                "estimate_gap": gap,
                "matched_record_ts": matched.get("ts"),
            }
            if round_index is not None:
                row["round"] = round_index
            try:
                with open(self.ledger_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
            except Exception as exc:
                logger.debug(
                    "token attribution backfill ledger write failed (non-fatal): %s",
                    exc,
                )
            # kilocode #2：回填写路径同样顺带触发保留策略清扫（与record对称）
            try:
                self.maybe_cleanup()
            except Exception as _cl_err:
                logger.debug(
                    "token attribution retention cleanup skipped (non-fatal): %s", _cl_err)
            return row
        except Exception as exc:
            logger.debug("token attribution backfill unavailable (non-fatal): %s", exc)
            return None

    def _read_tail(self, max_lines: int = 5000) -> list[dict]:
        """读取账本尾部（读取侧限行数防OOM——与tool_output get_stats同策略）。"""
        if not self.ledger_path.exists():
            return []
        recs: list[dict] = []
        try:
            with open(self.ledger_path, encoding="utf-8") as f:
                lines = f.readlines()
            for line in lines[-max_lines:]:
                line = line.strip()
                if not line:
                    continue
                try:
                    recs.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        except Exception as exc:
            logger.debug("token attribution ledger read failed: %s", exc)
        return recs

    def get_stats(self, limit: int | None = None) -> dict:
        """跨进程统计：最近记录 + 聚合摘要 + provider usage回填合并。

        P1回填合并语义：归因记录在LLM请求发出前写入、backfill行在该请求的provider
        响应后写入——时序即配对，每条归因记录匹配"其ts之后第一条同session的backfill行"。
        backfill行本身不计入total_records/总量统计（它不是一次上下文快照）。
        合并输出：记录行/summary.latest带actual_prompt_tokens + estimate_gap，
        summary带backfill_count + avg_estimate_gap。
        """
        limit = limit or self.max_recent
        all_recs = self._read_tail()
        backfills = [r for r in all_recs if r.get("backfill")]
        recs = [r for r in all_recs if not r.get("backfill")]
        if not recs:
            return {
                "total_records": 0,
                "recent": [],
                "summary": {"total_records": 0},
                "ledger_path": str(self.ledger_path),
            }

        def _match_backfill(rec: dict) -> dict | None:
            """rec写入之后的第一条同session backfill行（时序配对）"""
            for bf in all_recs:
                if not bf.get("backfill"):
                    continue
                if (bf.get("session_id") == rec.get("session_id")
                        and bf.get("ts", 0) >= rec.get("ts", 0)):
                    return bf
            return None

        gaps: list[int] = []
        cal_pairs: list[dict] = []  # 估算校准样本对（estimated=归因估算, actual=provider权威）
        for r in recs:
            bf = _match_backfill(r)
            if bf is not None:
                r["actual_prompt_tokens"] = bf.get("actual_prompt_tokens")
                r["estimate_gap"] = bf.get("estimate_gap")
                if bf.get("round") is not None:
                    r["backfill_round"] = bf.get("round")
                if bf.get("estimate_gap") is not None:
                    gaps.append(int(bf["estimate_gap"]))
                if bf.get("actual_prompt_tokens") is not None:
                    cal_pairs.append({
                        "estimated": int(r.get("usage", {}).get("total_tokens", 0)),
                        "actual": int(bf["actual_prompt_tokens"]),
                    })

        totals = [r.get("usage", {}).get("total_tokens", 0) for r in recs]
        over = sum(1 for r in recs if r.get("usage", {}).get("over_limit"))
        agg: dict[tuple, dict] = {}
        for r in recs:
            for c in r.get("usage", {}).get("top_consumers", []):
                key = (c.get("kind"), c.get("name"))
                slot = agg.setdefault(
                    key,
                    {"kind": c.get("kind"), "name": c.get("name"), "source": c.get("source"),
                     "tokens_total": 0, "times_seen": 0},
                )
                slot["tokens_total"] += c.get("tokens", 0)
                slot["times_seen"] += 1
        top = sorted(agg.values(), key=lambda s: s["tokens_total"], reverse=True)[:10]
        for s in top:
            s["avg_tokens"] = s["tokens_total"] // max(1, s["times_seen"])

        latest = recs[-1]  # 最新归因记录（backfill行不参与latest选取）
        latest_usage = latest.get("usage", {})
        return {
            "total_records": len(recs),
            "recent": recs[-limit:][::-1],  # 最新在前
            "summary": {
                "total_records": len(recs),
                "over_limit_records": over,
                "avg_total_tokens": sum(totals) // len(totals),
                "max_total_tokens": max(totals),
                "latest": {
                    "ts": latest.get("ts"),
                    "session_id": latest.get("session_id", ""),
                    "model": latest.get("model", ""),
                    "total_tokens": latest_usage.get("total_tokens", 0),
                    "percentage": latest_usage.get("percentage", 0),
                    "over_limit": latest_usage.get("over_limit"),
                    "raw_max_tokens": latest_usage.get("raw_max_tokens", 0),
                    "item_count": latest_usage.get("item_count", 0),
                    # P1 provider usage回填：估算 vs provider权威prompt_tokens
                    "actual_prompt_tokens": latest.get("actual_prompt_tokens"),
                    "estimate_gap": latest.get("estimate_gap"),
                },
                "top_consumers": top,
                "backfill_count": len(backfills),
                "avg_estimate_gap": (sum(gaps) // len(gaps)) if gaps else None,
                # d439f163遗留#3：估算器校准（gap信号的消费端）
                "calibration": compute_calibration(cal_pairs),
            },
            "ledger_path": str(self.ledger_path),
        }

    def calibration_factor(self, force_refresh: bool = False) -> float:
        """当前校准因子（Σactual/Σestimated，样本不足返回1.0）——估算校准的消费端API。

        消费点：soulmate_agent工具循环record时传入build_context_usage；上下文预算
        ContextBudgetManager.manage的裁剪估算。TTL缓存（CALIBRATION_CACHE_TTL秒）：
        agent每轮都record，账本读取按缓存节流。任何异常fail-safe返回1.0
        （校准是估算修正，绝不阻断消息路径——本项目fail-safe惯例）。
        """
        try:
            now = time.time()
            if (
                not force_refresh
                and self._calibration_cache is not None
                and now - self._calibration_cache[1] < CALIBRATION_CACHE_TTL
            ):
                return self._calibration_cache[0]
            cal = self.get_stats().get("summary", {}).get("calibration") or {}
            factor = float(cal.get("factor", 1.0)) if cal.get("calibrated") else 1.0
            self._calibration_cache = (factor, now)
            return factor
        except Exception as exc:
            logger.debug("calibration factor unavailable (fail-safe 1.0): %s", exc)
            return 1.0

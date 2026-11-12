"""study 观察钩子 — evo 第三种进化燃料：外部软件世界（33-StudyLoop-v1.0 §7）

职责（只做这四件事）：
1. 读 acp-proxy/data/study_targets.json 目标清单（url|path + learn + mode）
2. 调 opensoul /api/soma/study/* 跑目标（单轮 ≤3 个目标，截图 ≤12 张）
3. 从证据提纯"功能发现"（每条 = 发现描述 + 证据 id 支撑 + 可搬价值评分）
4. 输出结构化 findings 供 evolution_pipeline._plan 吸收（与 feedback 并列）

治理铁律（33-StudyLoop §3/§7/§9）：
- study 发现不豁免 Hermes 审核——evo 产出照旧走 _enqueue_hermes_review，不得绕过
- 结论只来自 authority=original-files/observed 的证据；inferred 不进 findings（只作假设）
- opensoul 不可达 → 静默降级，观察阶段绝不因 study 中断；单轮总超时 120s
"""
import asyncio
import json
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

logger = logging.getLogger("study-hook")

# ── 配置（33-StudyLoop §5 工具箱 / §6 API / §7 频控） ──
STUDY_BASE_URL = "http://127.0.0.1:8090"
STUDY_ROUND_TIMEOUT = 120.0        # 单轮 study 总超时（秒）
MAX_TARGETS_PER_ROUND = 3          # 频控：单轮 ≤3 个目标
MAX_SCREENSHOTS_PER_ROUND = 12     # 频控：单轮截图 ≤12 张
DEFAULT_WIDTHS = [375, 768, 1440]  # web_study 三屏宽
VALID_MODES = ("clean_room", "deep")
CONCLUSIVE_AUTHORITIES = ("original-files", "observed")  # §3 铁律：inferred 不得直接进实施计划
MAX_FINDINGS_PER_RECORD = 6
MAX_FINDINGS_TOTAL = 12
DEFAULT_TARGETS_PATH = Path(__file__).resolve().parent / "data" / "study_targets.json"

# 各 operation 结果里"功能/亮点"所在的字段（提纯入口，容错遍历）
SALIENT_KEYS = {
    "web_study": ("structure", "structure_tree", "components", "nav", "navigation",
                  "headings", "features", "tech_fingerprint", "fingerprint", "highlights"),
    "binary_triage": ("format", "arch", "entry", "dependencies", "packer", "resources", "imports"),
    "strings_scan": ("urls", "paths", "patterns", "hits", "secrets_masked"),
    "image_diff": ("similarity", "hotspots", "size_diff"),
}
GENERIC_SALIENT_KEYS = ("features", "components", "highlights", "findings")


# 结果里噪音过大的字段（整棵 DOM dump/截图路径不算"功能发现"）
NOISY_KEYS = ("children", "raw", "html", "dom", "screenshots", "screenshot", "shots")


class StudyClientError(Exception):
    """opensoul study 接口调用失败（连接失败/超时/HTTP错误）"""


# ── 可插拔 client 层（真跑 HttpStudyClient，测试 MockStudyClient） ──

class StudyClient:
    """study 工具箱客户端接口（33-StudyLoop §6：/api/soma/study/*）"""

    async def web_study(self, url: str, widths: list[int] | None = None) -> dict:
        raise NotImplementedError

    async def binary_triage(self, path: str) -> dict:
        raise NotImplementedError

    async def strings_scan(self, path: str, pattern: str | None = None) -> dict:
        raise NotImplementedError

    async def image_diff(self, image_a: str, image_b: str) -> dict:
        raise NotImplementedError

    async def get_evidence(self, op: str | None = None, limit: int = 20) -> list[dict]:
        raise NotImplementedError

    async def get_evidence_by_id(self, evidence_id: str) -> dict:
        raise NotImplementedError


class HttpStudyClient(StudyClient):
    """真实 HTTP 客户端 — 调 opensoul /api/soma/study/*（真跑时必须用它，禁 mock）"""

    def __init__(self, base_url: str = STUDY_BASE_URL, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def _post(self, path: str, payload: dict) -> dict:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(f"{self.base_url}{path}", json=payload)
                resp.raise_for_status()
                return _as_record(resp.json())
        except (httpx.HTTPError, ValueError) as e:
            raise StudyClientError(f"{path} 调用失败: {type(e).__name__}: {e}") from e

    async def web_study(self, url: str, widths: list[int] | None = None) -> dict:
        return await self._post("/api/soma/study/web", {"url": url, "widths": widths or DEFAULT_WIDTHS})

    async def binary_triage(self, path: str) -> dict:
        return await self._post("/api/soma/study/triage", {"path": path})

    async def strings_scan(self, path: str, pattern: str | None = None) -> dict:
        payload = {"path": path}
        if pattern:
            payload["pattern"] = pattern
        return await self._post("/api/soma/study/strings", payload)

    async def image_diff(self, image_a: str, image_b: str) -> dict:
        return await self._post("/api/soma/study/diff", {"image_a": image_a, "image_b": image_b})

    async def get_evidence(self, op: str | None = None, limit: int = 20) -> list[dict]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                params: dict = {"limit": limit}
                if op:
                    params["op"] = op
                resp = await client.get(f"{self.base_url}/api/soma/study/evidence", params=params)
                resp.raise_for_status()
                return _as_record_list(resp.json())
        except (httpx.HTTPError, ValueError) as e:
            raise StudyClientError(f"/api/soma/study/evidence 调用失败: {type(e).__name__}: {e}") from e

    async def get_evidence_by_id(self, evidence_id: str) -> dict:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(f"{self.base_url}/api/soma/study/evidence/{evidence_id}")
                resp.raise_for_status()
                return _as_record(resp.json())
        except (httpx.HTTPError, ValueError) as e:
            raise StudyClientError(f"/api/soma/study/evidence/{evidence_id} 调用失败: {type(e).__name__}: {e}") from e


def _as_record(payload) -> dict:
    """容错解包：兼容直接 EvidenceRecord 或 {evidence/data/record: {...}} 包装"""
    if isinstance(payload, dict):
        for key in ("evidence", "data", "record"):
            inner = payload.get(key)
            if isinstance(inner, dict) and ("id" in inner or "result" in inner or "operation" in inner):
                return inner
        return payload
    return {}


def _as_record_list(payload) -> list[dict]:
    """容错解包列表响应：裸 list 或 {evidence/items/results: [...]}"""
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for key in ("evidence", "items", "results", "records"):
            inner = payload.get(key)
            if isinstance(inner, list):
                return [r for r in inner if isinstance(r, dict)]
    return []


# ── 目标清单解析（data/study_targets.json） ──

def load_targets(targets_path: Path | str = DEFAULT_TARGETS_PATH) -> tuple[list[dict], list[str]]:
    """解析目标清单。

    结构：{"targets": [{"url"|"path": ..., "learn": "想学什么", "mode": "clean_room|deep"}]}
    （也兼容裸 list）。非法条目跳过并记错误（容错，不抛异常）。
    返回 (valid_targets, errors)。
    """
    path = Path(targets_path)
    if not path.exists():
        return [], [f"targets 文件不存在: {path}"]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return [], [f"targets 文件解析失败: {e}"]

    raw = data.get("targets") if isinstance(data, dict) else data
    if not isinstance(raw, list):
        return [], ["targets 结构错误：应为 {\"targets\": [...]} 或裸 list"]

    valid: list[dict] = []
    errors: list[str] = []
    for idx, item in enumerate(raw):
        if not isinstance(item, dict):
            errors.append(f"target[{idx}] 不是对象，跳过")
            continue
        url = item.get("url")
        target_path = item.get("path")
        learn = item.get("learn")
        mode = item.get("mode")
        if bool(url) == bool(target_path):
            errors.append(f"target[{idx}] 必须且只能给出 url 或 path 之一，跳过")
            continue
        if url and not re.match(r"^https?://", str(url)):
            errors.append(f"target[{idx}] url 非法（需 http/https）: {str(url)[:60]}，跳过")
            continue
        if not isinstance(learn, str) or not learn.strip():
            errors.append(f"target[{idx}] 缺少 learn（想学什么），跳过")
            continue
        if mode not in VALID_MODES:
            errors.append(f"target[{idx}] mode 非法（{mode}，应为 clean_room|deep），跳过")
            continue
        valid.append({
            "url": str(url) if url else None,
            "path": str(target_path) if target_path else None,
            "learn": learn.strip(),
            "mode": mode,
        })
    return valid, errors


# ── 功能发现提纯（证据 → findings） ──

def _salient_items(operation: str, result: dict) -> list[tuple[str, str]]:
    """从证据 result 里挑'功能/亮点'条目：(字段名, 值文本)，容错遍历"""
    items: list[tuple[str, str]] = []
    if not isinstance(result, dict):
        return items
    keys = SALIENT_KEYS.get(operation, ()) + GENERIC_SALIENT_KEYS
    for key in keys:
        if key in NOISY_KEYS:
            continue
        value = result.get(key)
        if value in (None, "", [], {}):
            continue
        if isinstance(value, list):
            for element in value[:MAX_FINDINGS_PER_RECORD]:
                if isinstance(element, dict):
                    text = json.dumps(element, ensure_ascii=False)[:160]
                else:
                    text = str(element)[:160]
                if text.strip():
                    items.append((key, text))
        elif isinstance(value, dict):
            for sub_key, sub_value in list(value.items())[:MAX_FINDINGS_PER_RECORD]:
                if sub_key in NOISY_KEYS or sub_value in (None, "", [], {}):
                    continue  # 空值/噪声子字段不构成"功能发现"
                items.append((f"{key}.{sub_key}", str(sub_value)[:160]))
        else:
            items.append((key, str(value)[:160]))
        if len(items) >= MAX_FINDINGS_PER_RECORD:
            break
    return items[:MAX_FINDINGS_PER_RECORD]


def _portability_score(description: str, learn: str, confidence: str, authority: str) -> float:
    """可搬价值评分（0~5，确定性公式，不走 LLM）：
    基础分 = confidence 权重（observed 3.0 / probable 2.0 / speculative 1.0）
    + authority 加成（original-files +0.5）
    + 关键词命中学习目标加成（+1.5，learn 的词片段出现在发现文本里）
    """
    base = {"observed": 3.0, "probable": 2.0, "speculative": 1.0}.get(confidence, 2.0)
    if authority == "original-files":
        base += 0.5
    tokens = [t for t in re.split(r"[\s,，、:：;；/|（）()]+", learn) if len(t) >= 2]
    if any(t in description for t in tokens):
        base += 1.5
    return round(min(5.0, base), 1)


def extract_findings(evidence_records: list[dict], targets: list[dict] | None = None) -> list[dict]:
    """从证据提纯"功能发现"（33-StudyLoop §3 铁律 + §7.2）。

    每条 finding：{finding: 发现描述, evidence_ids: [证据id], portability_score: 可搬价值评分,
                  source: "study", target, learn, mode, confidence}
    - 只采信 authority=original-files/observed 的证据；inferred 直接丢弃（不进实施计划）
    - 相同发现文本去重（合并证据 id）
    """
    targets = targets or []
    findings: list[dict] = []
    seen: dict[str, dict] = {}

    for record in evidence_records:
        if not isinstance(record, dict):
            continue
        authority = str(record.get("authority", ""))
        if authority not in CONCLUSIVE_AUTHORITIES:
            continue  # §3 铁律：inferred 仅作假设，不得直接进实施计划
        evidence_id = str(record.get("id", ""))
        if not evidence_id:
            continue
        operation = str(record.get("operation", "unknown"))
        raw_params = record.get("params")
        params: dict = raw_params if isinstance(raw_params, dict) else {}
        raw_result = record.get("result")
        result: dict = raw_result if isinstance(raw_result, dict) else {}
        target = _match_target(params, targets) or {}
        label = target.get("url") or target.get("path") or str(params.get("url") or params.get("path") or "?")
        learn = target.get("learn", "")

        items = _salient_items(operation, result)
        if not items:
            # 兜底：证据本身即为发现（如实描述，不虚构内容）
            items = [(operation, f"已产出证据（result 字段: {', '.join(list(result.keys())[:6]) or '空'}）")]

        for key, value in items:
            description = f"[{operation}] {label}｜{key}: {value}"
            if learn:
                description += f"（学习目标: {learn}）"
            existing = seen.get(description)
            if existing:
                if evidence_id not in existing["evidence_ids"]:
                    existing["evidence_ids"].append(evidence_id)
                continue
            finding = {
                "finding": description,
                "evidence_ids": [evidence_id],
                "portability_score": _portability_score(description, learn, str(record.get("confidence", "")), authority),
                "source": "study",
                "target": label,
                "learn": learn,
                "mode": target.get("mode", params.get("mode", "")),
                "confidence": str(record.get("confidence", "")),
            }
            seen[description] = finding
            findings.append(finding)
            if len(findings) >= MAX_FINDINGS_TOTAL:
                return findings
    return findings


def _match_target(params: dict, targets: list[dict]) -> dict | None:
    """把证据 params（url/path）对回目标清单条目（取 learn/mode）"""
    for target in targets:
        if target.get("url") and target.get("url") == params.get("url"):
            return target
        if target.get("path") and target.get("path") == params.get("path"):
            return target
    return None


def format_study_findings(findings: list[dict], limit: int = 5) -> str:
    """把 findings 排成 _plan prompt 用的段落（标明来源 study，与 feedback 并列）"""
    if not findings:
        return ""
    ranked = sorted(findings, key=lambda f: f.get("portability_score", 0), reverse=True)
    lines = ["【study 发现 — 来源：study（外部软件逆向学习，证据化功能发现）】"]
    for idx, finding in enumerate(ranked[:limit], 1):
        evidence_ids = ", ".join(finding.get("evidence_ids", []))
        lines.append(
            f"{idx}. {finding.get('finding', '')}｜证据: {evidence_ids}｜"
            f"可搬价值: {finding.get('portability_score', 0)}/5"
        )
    lines.append("（以上为'搬回来'的候选输入，仅采信有证据 id 支撑的发现；产出仍须经 Hermes 审核队列，不得绕过）")
    return "\n".join(lines) + "\n"


# ── 单轮 study（观察阶段入口） ──

async def _run_single_target(client: StudyClient, target: dict, timeout_s: float,
                             screenshot_budget: int) -> tuple[list[dict], list[str], int]:
    """跑单个目标：url→web_study；path→triage（deep 模式再加 strings_scan）。
    返回 (证据记录列表, 错误列表, 消耗截图数)。任何失败只记错误，不抛。"""
    records: list[dict] = []
    errors: list[str] = []
    screenshots = 0
    label = target.get("url") or target.get("path") or "?"
    try:
        if target.get("url"):
            widths = DEFAULT_WIDTHS[:max(0, screenshot_budget)]
            if not widths:
                errors.append(f"{label}: 截图预算耗尽（≤{MAX_SCREENSHOTS_PER_ROUND}张/轮），跳过 web_study")
                return records, errors, 0
            record = await asyncio.wait_for(client.web_study(target["url"], widths), timeout=timeout_s)
            screenshots = len(widths)
            if record:
                records.append(record)
        else:
            record = await asyncio.wait_for(client.binary_triage(target["path"]), timeout=timeout_s)
            if record:
                records.append(record)
            # clean_room 只看访客可见面；strings_scan 进二进制内部 = deep 才跑
            if target.get("mode") == "deep" and timeout_s > 0:
                record = await asyncio.wait_for(client.strings_scan(target["path"]), timeout=timeout_s)
                if record:
                    records.append(record)
    except asyncio.TimeoutError:
        errors.append(f"{label}: 调用超时（单轮剩余预算 {timeout_s:.1f}s 用尽）")
    except StudyClientError as e:
        errors.append(f"{label}: {e}")
    except Exception as e:  # 容错底线：study 失败永不打断观察
        errors.append(f"{label}: 未预期异常 {type(e).__name__}: {e}")
    return records, errors, screenshots


async def run_study_round(targets_path: Path | str = DEFAULT_TARGETS_PATH,
                          client: StudyClient | None = None,
                          base_url: str = STUDY_BASE_URL,
                          round_timeout: float = STUDY_ROUND_TIMEOUT) -> dict:
    """跑一轮 study 观察（33-StudyLoop §7：≤3 目标、截图 ≤12、单轮 120s、静默降级）。

    返回结构化结果：
    {"round_id", "status": "ok|partial|degraded|empty", "targets_run", "findings",
     "evidence_ids", "errors", "duration_s"}
    绝不抛异常——opensoul 不可达/超时/清单坏 都静默降级。
    """
    started = time.monotonic()
    round_id = f"study_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    result = {
        "round_id": round_id,
        "status": "empty",
        "targets_run": [],
        "findings": [],
        "evidence_ids": [],
        "errors": [],
        "duration_s": 0.0,
    }
    try:
        targets, parse_errors = load_targets(targets_path)
        result["errors"].extend(parse_errors)
        targets = targets[:MAX_TARGETS_PER_ROUND]  # 频控：单轮 ≤3 个目标
        if not targets:
            result["errors"].append("无可用 study 目标")
            result["status"] = "degraded" if parse_errors else "empty"
            result["duration_s"] = round(time.monotonic() - started, 3)
            return result

        active_client = client or HttpStudyClient(base_url)
        deadline = started + round_timeout
        screenshot_budget = MAX_SCREENSHOTS_PER_ROUND
        evidence_records: list[dict] = []

        for target in targets:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                result["errors"].append(f"round_timeout: 单轮 {round_timeout:.0f}s 预算用尽，剩余目标跳过")
                break
            records, errors, screenshots = await _run_single_target(
                active_client, target, remaining, screenshot_budget,
            )
            result["targets_run"].append(target.get("url") or target.get("path") or "?")
            result["errors"].extend(errors)
            evidence_records.extend(r for r in records if r)
            screenshot_budget -= screenshots

        result["evidence_ids"] = [str(r.get("id", "")) for r in evidence_records if r.get("id")]
        result["findings"] = extract_findings(evidence_records, targets)

        if result["findings"]:
            result["status"] = "partial" if result["errors"] else "ok"
        else:
            result["status"] = "degraded" if result["errors"] else "empty"
    except Exception as e:  # 终极兜底：study 任何异常都静默降级
        logger.warning("study 观察降级（不阻断进化）: %s", e)
        result["errors"].append(f"study_round 内部异常（已降级）: {type(e).__name__}: {e}")
        result["status"] = "degraded"
    result["duration_s"] = round(time.monotonic() - started, 3)
    logger.info(
        "study 观察完成: %s status=%s findings=%d errors=%d",
        round_id, result["status"], len(result["findings"]), len(result["errors"]),
    )
    return result

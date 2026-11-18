"""
公众号发布保障 — 账本对账核心（纯 Python，无第三方依赖）

对账三源：
① 本地账本（整合既有 Hermes 账本，禁平行造轮子）：
   - ~/Documents/Hermes/ai-learning/_state.json      （AI课/午课/晚课日更 + AI编程系列）
   - ~/.hermes/agent-research-state.json             （100个AI Agent架构深度分析系列）
② 微信平台只读接口（wechat_platform：freepublish/batchget + draft/batchget）
③ 本地封面资产（~/.hermes/wechat-visuals/** 生成的封面是否被草稿消费）

检出问题（issue.kind）：
- 漏发          日更时点已过但账本无当日记录
- 封面未挂      封面图已生成（或草稿 thumb_media_id 空）但未挂进草稿/发布
- 待确认积压    草稿已建、超窗未见平台发布（用户后台漏确认 = 实际漏发）
- 平台不一致    账本记有草稿但平台草稿查无、也未见发表

输出：issues[] + report（人读）+ ticket_text（/tasks 工单正文）+ checked（对账口径）
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import time
from pathlib import Path
from typing import Any

# ---- 账本配置（整合既有账本：只读，绝不改写账本本身） ----

DEFAULT_LEDGERS: list[dict[str, Any]] = [
    {
        "name": "AI课/公众号日更",
        "path": "~/Documents/Hermes/ai-learning/_state.json",
        "slot_hour": 12.0,          # AI课 12:00
        "cadence": "daily",
    },
    {
        "name": "AI Agent架构系列",
        "path": "~/.hermes/agent-research-state.json",
        "slot_hour": 17.05,         # 架构 17:03
        "cadence": "daily",
    },
]

ISSUE_KINDS = ("漏发", "封面未挂", "待确认积压", "平台不一致")


def load_ledger_configs() -> list[dict[str, Any]]:
    """账本配置：PUBLISH_GUARD_LEDGERS（JSON 列表）可覆盖缺省两本真账本（测试隔离）。"""
    raw = os.environ.get("PUBLISH_GUARD_LEDGERS", "").strip()
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, list) and data:
                return data
        except json.JSONDecodeError:
            pass
    return DEFAULT_LEDGERS


# ---- 账本条目抽取（两种账本形状通吃） ----

def _parse_date(value: Any) -> str:
    if not isinstance(value, str) or len(value) < 10:
        return ""
    m = re.match(r"(\d{4}-\d{2}-\d{2})", value)
    return m.group(1) if m else ""


def _walk_entries(data: dict[str, Any]) -> list[dict[str, Any]]:
    """从账本顶层抽取发布条目：{key,title,date,draft_id,subtree}（按 draft_id 去重）。"""
    found: dict[str, dict[str, Any]] = {}

    def _add(key: str, title: str, date: str, draft_id: str, subtree: Any) -> None:
        if not draft_id and not date:
            return
        ident = draft_id or f"{key}:{date}"
        prev = found.get(ident)
        if prev is None or _subtree_size(subtree) > _subtree_size(prev["subtree"]):
            found[ident] = {
                "key": key, "title": title or key, "date": date,
                "draft_id": draft_id, "subtree": subtree,
            }

    for key, value in data.items():
        if not isinstance(value, (dict, list)):
            continue
        if isinstance(value, dict):
            draft_id = str(value.get("wechat_draft_id") or value.get("draft_media_id") or "")
            date = _parse_date(value.get("date") or value.get("published_at") or value.get("at"))
            if draft_id or date:
                _add(key, str(value.get("title") or key), date, draft_id, value)
        # publish_history / news_history：列表条目同样入账
        for item in (value if isinstance(value, list) else []):
            if not isinstance(item, dict):
                continue
            draft_id = str(item.get("wechat_draft_id") or item.get("draft_media_id") or "")
            date = _parse_date(item.get("date") or item.get("published_at") or item.get("at"))
            if draft_id or date:
                _add(key, str(item.get("title") or item.get("day") or key), date, draft_id, item)
    return sorted(found.values(), key=lambda e: e["date"])


def _subtree_size(value: Any) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return 0


def _walk_values(value: Any):
    """深度优先遍历 dict/list 全部 (key, value) 对。"""
    if isinstance(value, dict):
        for k, v in value.items():
            yield k, v
            yield from _walk_values(v)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_values(item)


def cover_evidence(entry: dict[str, Any]) -> tuple[bool, str, str]:
    """封面消费证据：(已消费, 封面文件路径, 证据说明)。

    消费证据 = 子树任意 thumb_media_id 非空 或 cover_applied == true。
    封面文件 = 子树 'cover'/'cover_source' 指向的本地已存在图片。
    """
    consumed = ""
    cover_path = ""
    for key, value in _walk_values(entry["subtree"]):
        if key == "thumb_media_id" and isinstance(value, str) and value.strip():
            consumed = f"thumb_media_id={value[:12]}…"
        if key == "cover_applied" and value is True:
            consumed = consumed or "cover_applied=true"
        if key in ("cover", "cover_source") and isinstance(value, str) and not cover_path:
            if Path(value).is_file():
                cover_path = value
    return bool(consumed), cover_path, consumed


# ---- 本地对账 ----

def _ledger_local_checks(ledger_cfg: dict[str, Any], entries: list[dict[str, Any]],
                         window_days: int, now: _dt.datetime) -> tuple[list[dict], dict]:
    issues: list[dict[str, Any]] = []
    ledger_name = str(ledger_cfg.get("name") or ledger_cfg.get("path"))
    slot_hour = float(ledger_cfg.get("slot_hour") or 0.0)
    slot_label = f"{int(slot_hour):02d}:{int(round((slot_hour % 1) * 60)):02d}"
    today = now.date()
    slot_passed = (now.hour + now.minute / 60.0) >= slot_hour

    dates = [e["date"] for e in entries if e["date"]]
    last_date = max(dates) if dates else ""

    # ① 漏发：日更时点已过、账本最后一条仍是更早日期
    missing: list[str] = []
    if str(ledger_cfg.get("cadence", "daily")) == "daily" and last_date:
        cursor = _dt.date.fromisoformat(last_date)
        stop = today if slot_passed else today - _dt.timedelta(days=1)
        while cursor < stop and len(missing) < window_days:
            cursor += _dt.timedelta(days=1)
            missing.append(cursor.isoformat())
        if missing:
            issues.append({
                "kind": "漏发", "ledger": ledger_name,
                "detail": f"日更（{slot_label}）缺 {len(missing)} 天：{'、'.join(missing)}"
                          f"（账本最后一更 {last_date}，今天 {today}）",
                "evidence": f"last_date={last_date}",
            })

    # ② 封面未挂 + ③ 待确认积压：窗口内条目逐条核
    window_start = today - _dt.timedelta(days=max(window_days, 1))
    for entry in entries:
        if not entry["date"]:
            continue
        entry_date = _dt.date.fromisoformat(entry["date"])
        if entry_date < window_start or entry_date > today:
            continue
        subtree_text = json.dumps(entry["subtree"], ensure_ascii=False, default=str)
        consumed, cover_path, evidence = cover_evidence(entry)

        if cover_path and not consumed:
            issues.append({
                "kind": "封面未挂", "ledger": ledger_name, "article": entry["title"],
                "date": entry["date"],
                "detail": f"封面图已生成未消费（发布链未挂封面）：{cover_path}",
                "evidence": "本地封面资产存在但条目子树无 thumb_media_id/cover_applied",
            })
        if not cover_path and not consumed and "visuals" in entry["subtree"] \
                and "cover" in json.dumps(entry["subtree"].get("visuals") or {}, ensure_ascii=False):
            issues.append({
                "kind": "封面未挂", "ledger": ledger_name, "article": entry["title"],
                "date": entry["date"],
                "detail": "账本记有封面资产但未见消费证据（thumb_media_id/cover_applied 均缺）",
                "evidence": "visuals.cover 存在记录，publish 消费块缺失",
            })

        pending = "待用户后台确认发布" in subtree_text or \
            str(entry["subtree"].get("status", "")).startswith("wechat_draft_created")
        has_publish_ts = bool(entry["subtree"].get("published_at"))
        if pending and not has_publish_ts and entry_date < today:
            issues.append({
                "kind": "待确认积压", "ledger": ledger_name, "article": entry["title"],
                "date": entry["date"],
                "detail": f"草稿 {entry['draft_id'][:16] + '…' if entry['draft_id'] else '-'}"
                          f" 建于 {entry['date']}，账本仍记「待用户后台确认发布」且无发布时刻",
                "evidence": "status 含待确认且 published_at 缺失",
            })

    summary = {
        "ledger": ledger_name, "entries": len(entries),
        "last_date": last_date, "slot": slot_label,
        "window": f"{window_start} .. {today}",
    }
    return issues, summary


# ---- 平台对账（可选，失败降级本地口径并显式报因） ----

def _load_platform_module():
    """按文件路径加载 wechat_platform（插件目录不在 sys.path，禁隐式相对导入）。"""
    import importlib.util
    path = Path(__file__).resolve().parent / "wechat_platform.py"
    spec = importlib.util.spec_from_file_location("publish_guard_wechat_platform", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"wechat_platform 无法加载: {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _platform_checks(entries: list[dict[str, Any]], window_days: int,
                     now: _dt.datetime) -> tuple[list[dict[str, Any]], str]:
    return _load_platform_module().platform_reconcile(
        entries, window_days=window_days, now=now)


# ---- 对账入口 ----

def run_reconcile(ledgers: list[dict[str, Any]] | None = None, *,
                  window_days: int = 3, check_platform: bool = True,
                  now: float | None = None) -> dict[str, Any]:
    moment = _dt.datetime.fromtimestamp(now or time.time())
    window_days = max(1, int(window_days))
    if os.environ.get("PUBLISH_GUARD_NO_PLATFORM"):
        check_platform = False   # 测试隔离/离线开关：不触平台网络
    issues: list[dict[str, Any]] = []
    checked: list[dict[str, Any]] = []
    all_window_entries: list[dict[str, Any]] = []

    for cfg in (ledgers or load_ledger_configs()):
        path = Path(str(cfg.get("path", ""))).expanduser()
        if not path.is_file():
            issues.append({
                "kind": "平台不一致", "ledger": str(cfg.get("name") or path),
                "detail": f"账本文件不存在: {path}",
                "evidence": "path missing",
            })
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            issues.append({
                "kind": "平台不一致", "ledger": str(cfg.get("name") or path),
                "detail": f"账本解析失败: {type(e).__name__}",
                "evidence": str(e)[:120],
            })
            continue
        entries = _walk_entries(data if isinstance(data, dict) else {})
        local_issues, summary = _ledger_local_checks(cfg, entries, window_days, moment)
        issues.extend(local_issues)
        checked.append(summary)
        window_start = (moment.date() - _dt.timedelta(days=max(window_days, 1))).isoformat()
        all_window_entries.extend(
            {**e, "ledger": summary["ledger"]}
            for e in entries if window_start <= e["date"] <= moment.date().isoformat())

    platform_status = "skipped"
    if check_platform:
        platform_issues, platform_status = _platform_checks(all_window_entries, window_days, moment)
        issues.extend(platform_issues)

    issues.sort(key=lambda i: (ISSUE_KINDS.index(i["kind"]) if i["kind"] in ISSUE_KINDS else 9,
                               i.get("date", "")))
    result = {
        "issue_count": len(issues),
        "issues": issues,
        "checked": checked,
        "platform": platform_status,
        "window_days": window_days,
        "checked_at": moment.strftime("%Y-%m-%d %H:%M:%S"),
    }
    result["report"] = render_report(result)
    result["ticket_text"] = render_ticket(result)
    return result


# ---- 渲染 ----

def render_report(result: dict[str, Any]) -> str:
    lines = [f"公众号发布对账报告（{result['checked_at']}，窗口 {result['window_days']} 天，"
             f"平台对账：{result['platform']}）"]
    for summary in result["checked"]:
        lines.append(f"- [{summary['ledger']}] 条目 {summary['entries']}，"
                     f"最后更 {summary['last_date'] or '—'}，日更时点 {summary['slot']}")
    if not result["issues"]:
        lines.append("✅ 对账通过：未发现漏发/封面未挂/积压")
    else:
        lines.append(f"检出问题 {result['issue_count']} 项：")
        for i in result["issues"]:
            head = f"[{i['kind']}]"
            if i.get("article"):
                head += f" {i['article']}（{i.get('date', '')}）"
            lines.append(f"- {head} {i['detail']}")
    return "\n".join(lines)


def render_ticket(result: dict[str, Any]) -> str:
    if not result["issues"]:
        return "公众号发布对账通过，无需处置。"
    lines = [
        f"公众号发布保障工单：检出 {result['issue_count']} 项异常"
        f"（对账时间 {result['checked_at']}，平台对账 {result['platform']}）",
        "",
    ]
    order = {"漏发": 1, "封面未挂": 2, "待确认积压": 3, "平台不一致": 4}
    for n, issue in enumerate(sorted(result["issues"],
                                     key=lambda i: order.get(i["kind"], 9)), 1):
        where = f"{issue.get('article', '')}（{issue.get('date', '')}）" \
            if issue.get("article") else issue.get("ledger", "")
        lines.append(f"{n}. [{issue['kind']}] {where}")
        lines.append(f"   {issue['detail']}")
        lines.append(f"   证据：{issue.get('evidence', '')}")
    lines += ["",
              "处置：漏发→补发或补录账本；封面未挂→补挂封面重新生成草稿；"
              "待确认积压→公众号后台确认发布；平台不一致→核对草稿是否误删。",
              "处置完成后交付本工单（写明处置结果）即可收口，超期将自动催办。"]
    return "\n".join(lines)

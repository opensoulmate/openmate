"""flow_receipts — RPA 流程执行凭证 / 有头无头分层路由 / skill 沉淀的纯逻辑层。

无 fastapi 依赖（纯 stdlib）：agent 子进程与 browser_bridge 共用同一套真源——
- 执行回执（滚动表，保留最近 200 条，落盘 data/browser_receipts.json 跨进程可见）：
  每个派发任务记录 {task_id, action, params摘要(密码脱敏为***), ok/error, 完成时间, 耗时}。
  凭证来自服务端派发记录，模型无法伪造（治冒名）。
- 分层路由：流程 context（needs_login/intranet/preferred）自动推断 + engine 决策。
  无头执行遇登录/内网地址显式报错「此流程需要登录态/内网环境，必须有头执行」，
  禁止静默换引擎。
- skill 沉淀：录制流程 → skill.md（六节）生成的纯文本层。

铁律：密码/凭据一律脱敏，不落回执不落 skill。
"""
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path

RECEIPTS_MAX = 200  # 滚动回执表容量
RECEIPTS_FILE: Path = Path(__file__).resolve().parent / "data" / "browser_receipts.json"

_LOCK = threading.RLock()

# 敏感参数名（键名命中即脱敏为 ***）
_SENSITIVE_KEY_RE = re.compile(r"pass|pwd|secret|token|cred|auth|cookie", re.I)
# 密码框选择器（text/value 命中即脱敏）
_PASSWORD_SELECTOR_RE = re.compile(r"pass|pwd|密码|secret|token", re.I)
# 登录态参数占位（{{password}}/{{user}} 等 → needs_login）
_LOGIN_PARAM_RE = re.compile(
    r"password|passwd|pwd|secret|token|user|username|account|账号|用户|登录", re.I)
# URL 登录线索（→ needs_login）
_LOGIN_URL_RE = re.compile(r"login|log-in|signin|sign-in|sso|auth|登录|登陆", re.I)
# 内网地址线索（→ intranet）：localhost / 127.* / 10.* / 192.168.* / 100.64-100.127.*
_INTRANET_URL_RE = re.compile(
    r"localhost"
    r"|127(?:\.\d{1,3}){3}"
    r"|10\.\d{1,3}(?:\.\d{1,3}){2}"
    r"|192\.168\.\d{1,3}\.\d{1,3}"
    r"|100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}",
    re.I)
# URL 查询串里的敏感键值（password=xxx → password=***）
_SENSITIVE_QUERY_RE = re.compile(r"(?i)((?:pass|pwd|token|secret|key|auth|cred)\w*=)[^&#]*")

# webview 失活特征（应用通道 <webview> 被关/卸载）：命中→单步自救（ensure-webview→重试1次）
WEBVIEW_LOST_RE = re.compile(
    r"webview\s*已关闭|webview\s*未就绪|webview\s*已销毁|内置浏览器标签未就绪")
# 已自救标记：带此标注的错误/步骤不再二次重试（两层防线叠加也禁无限重试）
RETRY_USED_RE = re.compile(r"重开后仍失败|\(重试\d+\)|（重试\d+）")


def webview_lost(text: str) -> bool:
    """错误文本是否为 webview 失活（已关闭/未就绪/已销毁）→ 触发单步自救重试。

    已带重试标注（重试N/重开后仍失败）的错误返回 False——禁止无限重试。
    """
    t = str(text or "")
    return bool(WEBVIEW_LOST_RE.search(t)) and not RETRY_USED_RE.search(t)


# ── 脱敏（铁律：密码/凭据一律脱敏，不落回执不落 skill）────────────

def mask_params(params: dict, secret: bool = False) -> dict:
    """params 摘要脱敏：敏感键/密码框/secret 步骤的输入值一律 ***，长值截断自报。"""
    out: dict = {}
    for k, v in (params or {}).items():
        ks = str(k)
        if _SENSITIVE_KEY_RE.search(ks):
            out[ks] = "***"
        elif isinstance(v, str):
            out[ks] = _SENSITIVE_QUERY_RE.sub(r"\1***", v[:60] + ("…" if len(v) > 60 else ""))
        elif isinstance(v, (int, float, bool)) or v is None:
            out[ks] = v
        else:
            out[ks] = f"<{type(v).__name__}:{len(v)}>" if isinstance(v, (dict, list)) else str(v)[:60]
    sel = str((params or {}).get("selector", "") or "")
    if secret or _PASSWORD_SELECTOR_RE.search(sel):
        for ks in ("text", "value", "password"):
            if ks in out and out[ks] not in ("", None):
                out[ks] = "***"
    return out


# ── 执行回执（滚动表：服务端派发实录）──────────────────────────

def _load_receipts() -> list[dict]:
    try:
        data = json.loads(RECEIPTS_FILE.read_text(encoding="utf-8"))
        return [r for r in data if isinstance(r, dict)][-RECEIPTS_MAX:]
    except Exception:
        return []


def _store_receipts(items: list[dict]) -> None:
    try:
        RECEIPTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = RECEIPTS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(items[-RECEIPTS_MAX:], ensure_ascii=False, indent=1),
                       encoding="utf-8")
        os.replace(tmp, RECEIPTS_FILE)
    except Exception:
        pass  # 落盘失败不阻塞派发；内存表仍在（服务端实录尽力持久化）


def record_receipt(task_id: str, action: str, params: dict, ok: bool, error: str = "",
                   summary: str = "", ts: float | None = None, duration: float = 0.0,
                   engine: str = "app", secret: bool = False,
                   step_index: int = 0, flow: str = "") -> dict:
    """写入/更新一条执行回执（同 task_id 覆盖更新：超时后迟到结果如实改写）。"""
    rec = {
        "task_id": str(task_id or f"r-{uuid.uuid4().hex[:8]}"),
        "action": str(action or ""),
        "params": mask_params(params or {}, secret=bool(secret)),
        "ok": bool(ok),
        "error": ("" if ok else str(error or "未知错误"))[:200],
        "summary": (str(summary or "") if ok else "")[:160],
        "ts": float(ts if ts is not None else time.time()),
        "duration": round(float(duration or 0.0), 3),
        "engine": str(engine or "app"),
        "step_index": int(step_index or 0),
        "flow": str(flow or ""),
    }
    with _LOCK:
        items = _load_receipts()
        for i, old in enumerate(items):
            if old.get("task_id") == rec["task_id"]:
                items[i] = rec
                break
        else:
            items.append(rec)
        _store_receipts(items)
    return rec


def get_receipts(since: float = 0.0) -> list[dict]:
    """取 since（含）之后的回执列表。凭证的唯一数据源（服务端派发记录）。"""
    try:
        since_f = float(since or 0.0)
    except (TypeError, ValueError):
        since_f = 0.0
    with _LOCK:
        return [r for r in _load_receipts() if float(r.get("ts", 0) or 0) >= since_f]


# ── 执行窗口（flow_id -> 窗口起点 ts）：done=true 收尾拉该窗口回执出凭证 ──

_FLOW_WINDOWS: dict = {}


def start_flow_window(key: str) -> float:
    ts = time.time()
    _FLOW_WINDOWS[str(key or "")] = ts
    return ts


def flow_window(key: str) -> float:
    return float(_FLOW_WINDOWS.get(str(key or ""), 0.0) or 0.0)


# ── 分层路由：context 推断 + engine 决策 ─────────────────────────

def infer_flow_context(steps: list[dict]) -> dict:
    """从录制步骤自动推断执行上下文。
    登录判定：步骤含 {{password}}/{{user}} 等敏感参数占位、secret 标记、URL 含 login/登录；
    内网判定：URL 含 localhost/127.*/10.*/192.168.*/100.64-100.127.*。
    两者皆否 → preferred='headless'，否则 'headed'。
    """
    needs_login = False
    intranet = False
    for s in steps or []:
        if not isinstance(s, dict):
            continue
        if s.get("secret"):
            needs_login = True
        joined = " ".join(str(s.get(f, "") or "") for f in ("value", "url", "text"))
        for ph in re.findall(r"\{\{(\w+)\}\}", joined):
            if _LOGIN_PARAM_RE.search(ph):
                needs_login = True
        url = str(s.get("url", "") or "")
        if _LOGIN_URL_RE.search(url):
            needs_login = True
        if _INTRANET_URL_RE.search(url):
            intranet = True
    return {
        "needs_login": needs_login,
        "intranet": intranet,
        "preferred": "headed" if (needs_login or intranet) else "headless",
    }


def headless_refusal_reason(url: str) -> str:
    """无头执行越界检查：URL 命中登录/内网 → 返回原因片段（登录态/内网环境），空=可无头。"""
    u = str(url or "")
    reasons = []
    if _LOGIN_URL_RE.search(u):
        reasons.append("登录态")
    if _INTRANET_URL_RE.search(u):
        reasons.append("内网环境")
    return "/".join(reasons)


def decide_engine(context: dict, engine: str = "auto") -> tuple[str, str]:
    """engine 路由决策。返回 (resolved, error)；error 非空=显式拒绝（禁止静默换引擎）。

    默认有头（对用户承诺的语义）：auto 一律 headed；headless 仅当调用方显式指定
    （用户明确说「后台跑」）。context 标注只做显式 headless 的越界校验，不自动切
    引擎——否则同一流程两次执行会路由不一致（实测踩过：一次有头一次无头）。
    """
    engine = str(engine or "auto").strip().lower() or "auto"
    if engine not in ("auto", "headed", "headless"):
        return "", f"未知 engine: {engine!r}（支持 auto/headed/headless）"
    needs_login = bool((context or {}).get("needs_login"))
    intranet = bool((context or {}).get("intranet"))
    if engine == "headless" and (needs_login or intranet):
        need = "/".join([x for x, hit in (("登录态", needs_login), ("内网环境", intranet)) if hit])
        return "", (f"此流程需要{need}，必须有头执行"
                    f"（engine=headless 被显式拒绝，禁止静默换引擎；请传 engine=headed）")
    if engine == "auto":
        return "headed", ""  # 默认有头；headless 只走显式指定
    return engine, ""


# ── skill 沉淀（流程 → skill.md 六节）───────────────────────────

def step_intent(step: dict) -> str:
    """步骤 → 人话意图（skill「步骤意图」节与执行指导共用）。敏感值不落盘。"""
    s = step if isinstance(step, dict) else {}
    action = str(s.get("action", "") or "")
    sel = str(s.get("selector", "") or "")
    text = str(s.get("text", "") or "")
    url = str(s.get("url", "") or "")
    val = s.get("value", "")
    sensitive = bool(s.get("secret")) or bool(_LOGIN_PARAM_RE.search(str(val or "")))
    if action == "goto":
        return f"打开 {url}"
    if action == "click":
        return f"点击「{text or sel}」"
    if action in ("type", "fill"):
        shown = "***（敏感值：向用户取真实值，不落盘）" if sensitive else str(val)
        tail = "，然后回车提交" if s.get("submit") else ""
        return f"在「{sel}」输入 {shown}{tail}"
    if action == "screenshot":
        return "截图留证"
    if action == "back":
        return "返回上一页"
    if action == "eval":
        return "执行页面脚本校验状态"
    return f"执行 {action}（{sel or text or url}）"


def _skill_value_shown(step: dict) -> str:
    """skill/计划里展示的步骤值：敏感一律 ***，长值截断自报。"""
    s = step if isinstance(step, dict) else {}
    val = s.get("value", "")
    if val in (None, ""):
        return ""
    sensitive = bool(s.get("secret")) or bool(_LOGIN_PARAM_RE.search(str(val)))
    if sensitive:
        return "***（敏感字段：执行时向用户要真实值）"
    sv = str(val)
    return sv if len(sv) <= 40 else sv[:40] + "…"


def skill_md_for(rec: dict) -> str:
    """流程记录 → skill.md 全文（YAML frontmatter + 六节）。密码/凭据不落 skill。"""
    name = str(rec.get("name", "") or "")
    steps = [s for s in (rec.get("steps") or []) if isinstance(s, dict)]
    notes = str(rec.get("notes", "") or "").strip()
    ctx = rec.get("context") or infer_flow_context(steps)
    params = rec.get("params") or []
    n = len(steps)
    esc = name.replace('"', '\\"')
    ctx_bits = []
    if ctx.get("needs_login"):
        ctx_bits.append("需要登录态")
    if ctx.get("intranet"):
        ctx_bits.append("内网环境")
    one_liner = (f"共 {n} 步的浏览器录制流程"
                 + (f"（{'、'.join(ctx_bits)}）" if ctx_bits else "（公开页面可后台跑）")
                 + f"，参数：{', '.join(params) or '无'}。")

    lines = [
        "---",
        f'name: "{esc}"',
        f'description: "Use when 按【{esc}】流程来操作。{one_liner}"',
        "---",
        "",
        f"# {name}（录制流程）",
        "",
        "## 用途",
        f"按录制沉淀的固定流程完成「{name}」业务操作（{n} 步）。"
        "浏览器录制层沉淀的配方，触发后按步骤意图智能执行，注意事项是硬约束。",
        "",
        "## 触发语",
        f"- 按【{name}】流程来操作",
        f"- 走「{name}」流程 / 照着「{name}」流程干 / 回放「{name}」",
        "",
        "## 步骤意图",
    ]
    lines.extend(f"{i}. {step_intent(s)}" for i, s in enumerate(steps, 1))
    lines += ["", "## ⚠注意事项"]
    note_items = [x.strip() for x in re.split(r"[\n;；]+", notes) if x.strip()]
    lines.extend(f"- {x}" for x in note_items) if note_items else lines.append("- （无）")
    engine = ctx.get("preferred", "headless")
    engine_shown = ("headed（有头·OpenMate 应用通道）" if engine == "headed"
                    else "headless（无头·Playwright 后台执行）")
    lines += [
        "",
        "## 执行上下文",
        f"- 需要登录态：{'是' if ctx.get('needs_login') else '否'}",
        f"- 内网环境：{'是' if ctx.get('intranet') else '否'}",
        f"- 默认引擎：{engine_shown}",
        f"- 参数占位：{', '.join('{{' + p + '}}' for p in params) or '无'}（执行时向用户取值，绝不落盘）",
        "",
        "## 验收要点",
    ]
    if steps:
        lines.append(
            f"末步（第 {n} 步）{step_intent(steps[-1])} 必须完成且服务端执行凭证为成功；"
            "中途任一步失败、⏸ 用户接管或凭证缺失（0 步实际执行）即未达成。")
    else:
        lines.append("流程无步骤，任何执行声称均不成立。")
    return "\n".join(lines) + "\n"


def skill_sections(rec: dict) -> dict:
    """返回 skill 的「步骤意图」「注意事项」文本块（执行计划注入的智能执行指导）。"""
    md = skill_md_for(rec)

    def _sec(title: str) -> str:
        m = re.search(rf"## {re.escape(title)}\n(.*?)(?=\n## |\Z)", md, re.S)
        return (m.group(1).strip() if m else "")

    return {"steps_intent": _sec("步骤意图"), "notes": _sec("⚠注意事项")}


# ── 执行凭证（系统实录账单）────────────────────────────────────

def execution_report(receipts: list[dict], steps: list[dict],
                     skipped: dict | None = None, flow_name: str = "") -> str:
    """执行凭证文本块：回执（服务端派发实录）对照计划步骤逐条对账。

    0 条回执 → 必须显示「0 步实际执行」（冒名检测）。
    凭证来自服务端派发记录，模型无法伪造。
    """
    steps = [s for s in (steps or []) if isinstance(s, dict)]
    receipts = list(receipts or [])
    skipped = dict(skipped or {})
    head = f"—— 执行凭证（系统实录）——" + (f"【{flow_name}】" if flow_name else "")
    lines = [head]
    if not receipts:
        lines.append("0 步实际执行（服务端无任何派发记录）——任何「已完成」声称均不成立")
    matched: dict[int, dict] = {}
    extras: list[dict] = []
    free: list[dict] = []
    for r in receipts:
        si = int(r.get("step_index", 0) or 0)
        if si and 1 <= si <= len(steps) and si not in matched:
            matched[si] = r
        else:
            free.append(r)
    if free:
        # 无步骤序号的回执（模型用 browser 工具派发）按派发顺序对应计划步骤
        for r in free:
            for i in range(1, len(steps) + 1):
                if i not in matched:
                    matched[i] = r
                    break
            else:
                extras.append(r)
    for i in sorted(matched):
        r = matched[i]
        ok = bool(r.get("ok"))
        detail = (str(r.get("summary", "") or "").strip() if ok
                  else str(r.get("error", "") or "").strip()) or "（无摘要）"
        lines.append(f"第{i}步 {r.get('action', '')} → {'成功' if ok else '失败'}：{detail}")
    for r in extras:
        ok = bool(r.get("ok"))
        detail = (str(r.get("summary", "") or "").strip() if ok
                  else str(r.get("error", "") or "").strip()) or "（无摘要）"
        lines.append(f"额外派发 {r.get('action', '')} → {'成功' if ok else '失败'}：{detail}")
    if free and receipts:
        lines.append(f"（注：{len(free)} 条回执无步骤序号，按派发顺序对应计划步骤）")
    lines.append(f"实际派发 {len(receipts)} 步 / 计划 {len(steps)} 步")
    unexec = [i for i in range(1, len(steps) + 1) if i not in matched and i not in skipped]
    skip_list = [i for i in sorted(skipped) if i not in matched]
    if unexec:
        shown = "；".join(
            f"第{i}步 [{steps[i - 1].get('action', '')}] "
            f"{steps[i - 1].get('selector') or steps[i - 1].get('text') or steps[i - 1].get('url') or ''}"
            for i in unexec)
        lines.append(f"未执行步骤：{shown}")
    if skip_list:
        shown = "；".join(f"第{i}步 [{steps[i - 1].get('action', '')}]（{skipped[i]}）" for i in skip_list)
        lines.append(f"跳过步骤：{shown}")
    lines.append("（凭证来自服务端派发记录，模型无法伪造）")
    return "\n".join(lines)


# ── 确定性逐步执行（无头引擎的步骤清单执行层）──────────────────

def _subst(text: str, data: dict, missing: set) -> str:
    def _rep(m):
        key = m.group(1)
        if key in data:
            return str(data[key])
        missing.add(key)
        return m.group(0)
    return re.sub(r"\{\{(\w+)\}\}", _rep, str(text or ""))


def _http_ensure_webview(timeout_s: float = 10.0) -> tuple[bool, str]:
    """默认自救通道：POST /api/browser/ensure-webview（bridge 控制通道，有界 timeout_s）。

    bridge 向应用发 ensure-webview 控制消息并等 webview-ready（有界等待）；
    超时/不可达如实报因，绝不挂死调用方。
    """
    import urllib.request
    base = str(os.environ.get("SOULMATE_BROWSER_HTTP", "")
               or "http://127.0.0.1:8092").rstrip("/")
    try:
        req = urllib.request.Request(
            f"{base}/api/browser/ensure-webview",
            data=json.dumps({"timeout": float(timeout_s)}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=float(timeout_s) + 5) as resp:
            out = json.loads(resp.read().decode())
        ok = bool(out.get("ok"))
        return ok, ("webview 已自动重开就绪" if ok
                    else f"webview 重开未就绪：{str(out.get('error', '未知错误'))}")
    except Exception as e:
        return False, f"ensure-webview 通道不可达：{e}"


def execute_flow_steps(steps: list[dict], data: dict, run_action, engine: str = "headless",
                       flow_name: str = "", ensure_webview=None) -> dict:
    """按步骤清单确定性执行（goto/click/type/fill），每步结果写入回执表（同一套执行凭证）。

    run_action(action, params, secret) -> (ok, summary)：实际执行通道（无头=Playwright
    worker；有头=应用通道 _run_app_mode）。secret=True 的步骤值不落回执。
    无头执行遇登录/内网地址显式报错「此流程需要登录态/内网环境，必须有头执行」，
    禁止静默换引擎。缺参数先报不执行（跑到一半不许卡住）。
    webview 自救（断点续跑，有界）：步骤结果含「webview 已关闭/未就绪」→ ensure_webview
    （有界 10s）→ 重试该步一次 → 成功继续/仍败如实报「（重开后仍失败）」；重试步回执
    标注「(重试1)」进凭证，账单不掩盖自救过程。禁止无限重试（已重试标注的错误不再触发）。
    ensure_webview(timeout_s) -> (ok, note)：自救通道（缺省走 bridge HTTP 控制口，测试可注入）。
    返回 {ok, error, results, executed, planned, receipts, report}。
    """
    steps = [s for s in (steps or []) if isinstance(s, dict)]
    data = dict(data or {})
    t0 = time.time()
    missing: set = set()
    for s in steps:
        for field in ("value", "url"):
            if s.get(field):
                _subst(s[field], data, missing)
    if missing:
        return {"ok": False,
                "error": (f"流程「{flow_name}」需要参数：{', '.join(sorted(missing))}"
                          f"（敏感字段不入流程，提供 data={{...}} 填入后重跑）——0 步已执行"),
                "results": [], "executed": 0, "planned": len(steps),
                "receipts": [], "skipped": {},
                "report": execution_report([], steps, flow_name=flow_name)}
    results: list[str] = []
    receipts: list[dict] = []
    skipped: dict[int, str] = {}
    for i, s in enumerate(steps, 1):
        action = str(s.get("action", "") or "")
        # 无头越界检查：登录/内网地址显式拒绝，禁止静默换引擎
        if engine == "headless":
            reason = headless_refusal_reason(_subst(s.get("url", ""), data, set()))
            if reason:
                err = (f"此流程需要{reason}，必须有头执行（第{i}步 URL 命中{reason}）"
                       f"——拒绝无头执行，禁止静默换引擎")
                return {"ok": False, "error": err, "results": results,
                        "executed": len(receipts), "planned": len(steps),
                        "receipts": receipts, "skipped": skipped,
                        "report": execution_report(receipts, steps, skipped, flow_name)}
        params: dict = {}
        if action == "goto":
            params = {"url": _subst(s.get("url", ""), data, set())}
        elif action == "click":
            params = {"selector": s.get("selector", ""), "text": s.get("text", "")}
        elif action in ("fill", "type"):
            params = {"selector": s.get("selector", ""),
                      "text": _subst(s.get("value", ""), data, set()),
                      "submit": bool(s.get("submit"))}
        else:
            skipped[i] = "不支持的录制步骤，未派发"
            results.append(f"{i}. [{action}] ⏭ 跳过（不支持的录制步骤）")
            continue
        sensitive = bool(s.get("secret")) or bool(_LOGIN_PARAM_RE.search(
            " ".join(str(s.get(f, "") or "") for f in ("value", "url", "text"))))
        started = time.time()
        try:
            ok, summary = run_action(action, params, sensitive)
        except Exception as e:  # 执行通道异常=失败如实报因，不吞不换引擎
            ok, summary = False, f"{type(e).__name__}: {e}"[:160]
        duration = time.time() - started
        rec_action = action
        # ── webview 自救（断点续跑）：webview 已关闭/未就绪 → ensure-webview（有界10s）
        #    → 重试该步一次；成功继续，仍败如实报「（重开后仍失败）」。禁止无限重试 ──
        if not ok and webview_lost(str(summary)):
            ens = ensure_webview or _http_ensure_webview
            try:
                ens_ok, ens_note = ens(10.0)
            except Exception as e:  # 自救通道异常=如实记，仍做单次重试
                ens_ok, ens_note = False, f"ensure-webview 异常：{type(e).__name__}: {e}"
            results.append(
                f"  ↳ 自救：{str(summary).strip()[:80]} → ensure-webview"
                f"（{'成功' if ens_ok else '失败'}：{ens_note}）→ 重试该步1次")
            started = time.time()
            try:
                ok, summary = run_action(action, params, sensitive)
            except Exception as e:
                ok, summary = False, f"{type(e).__name__}: {e}"[:160]
            duration += time.time() - started
            rec_action = f"{action}(重试1)"  # 重试进凭证：账单不掩盖自救过程
            if not ok:
                summary = f"{summary}（重开后仍失败）"
        rec = record_receipt(
            task_id=f"flow-{uuid.uuid4().hex[:8]}", action=rec_action, params=params,
            ok=bool(ok), error=("" if ok else str(summary)), summary=str(summary),
            duration=duration, engine=str(engine), secret=sensitive,
            step_index=i, flow=flow_name)
        receipts.append(rec)
        results.append(f"{i}. [{rec_action}] {'✅' if ok else '❌'} {str(summary).strip()[:160]}")
        if not ok:
            return {"ok": False,
                    "error": f"第{i}步 {action} 失败，已停（不继续盲跑）：{summary}",
                    "results": results, "executed": len(receipts), "planned": len(steps),
                    "receipts": receipts, "skipped": skipped,
                    "report": execution_report(receipts, steps, skipped, flow_name)}
    report = execution_report(receipts, steps, skipped, flow_name)
    _ = t0  # 窗口起点预留（对账以回执 ts 为准）
    return {"ok": True, "error": "", "results": results, "executed": len(receipts),
            "planned": len(steps), "receipts": receipts, "skipped": skipped,
            "report": report}

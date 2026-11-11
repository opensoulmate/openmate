#!/usr/bin/env python3
"""verify_flow_tools — RPA 流程系统三大规格系统性验证（新基线，原 25 PASS 基线已被 /tmp 清理）。

覆盖：①回执记录+凭证生成（含 0 回执冒名场景必须显示「0 步实际执行」）
     ②context 自动推断（登录/内网/公开三类样例）
     ③engine 路由决策+无头遇登录/内网显式拒绝（禁止静默换引擎）
     ④skill.md 生成内容完整（frontmatter/六节）
     ⑤密码脱敏（不落回执不落 skill）
     + 确定性无头执行驱动、桥派发回执（ws 回传点）、超时回执、HTTP 面。
     ⑥/⑦webview 自动重开+断点续跑：webview 已关闭→ensure→重试成功、重试仍败如实
     报错不无限重试、重试在回执里可见、webview-ready 超时有界、两层防线（execute_flow_steps
     /_run_app_mode）互认已自救标注不再二次重试。
"""
import json
import re
import sys
import tempfile
import time
from pathlib import Path

ACP_ROOT = Path.home() / "openmate" / "acp-proxy"
sys.path.insert(0, str(ACP_ROOT))

import flow_receipts as fr          # noqa: E402
import browser_bridge as bb         # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}  {detail}")


def fresh_env() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="vft-"))
    fr.RECEIPTS_FILE = tmp / "browser_receipts.json"
    bb._FLOWS_DIR = tmp / "browser_flows"
    bb._FLOWS_DIR.mkdir(parents=True, exist_ok=True)
    fr._FLOW_WINDOWS.clear()
    return tmp


# ══ ① 执行回执 + 执行凭证（治冒名）═════════════════════════════

def t1_receipts_and_report():
    print("① 回执记录 + 凭证生成")
    fresh_env()
    r = fr.record_receipt("t1", "goto", {"url": "https://example.com"}, True,
                          summary="已打开 example.com", duration=0.5, engine="app")
    check("回执字段齐全", all(k in r for k in ("task_id", "action", "params", "ok", "ts", "duration")),
          str(sorted(r.keys())))
    got = fr.get_receipts(0)
    check("回执落滚动表", len(got) == 1 and got[0]["task_id"] == "t1")

    fr.record_receipt("t-old", "click", {"selector": "#a"}, True, ts=time.time() - 500)
    check("since 过滤", all(x["task_id"] != "t-old" for x in fr.get_receipts(time.time() - 10)))

    fr.record_receipt("t1", "goto", {"url": "https://example.com"}, False, error="超时未确认")
    check("同 task_id 覆盖更新（迟到结果如实改写）",
          len([x for x in fr.get_receipts(0) if x["task_id"] == "t1"]) == 1
          and not [x for x in fr.get_receipts(0) if x["task_id"] == "t1"][0]["ok"])

    fresh_env()
    for i in range(205):
        fr.record_receipt(f"cap-{i}", "goto", {"url": "u"}, True)
    check("滚动表保留最近 200 条", len(fr.get_receipts(0)) == 200, str(len(fr.get_receipts(0))))

    # 凭证：2 成功/失败回执 + 计划 3 步 → 第3步显式列为未执行
    fresh_env()
    steps = [{"action": "goto", "url": "https://a.com"},
             {"action": "click", "selector": "#x"},
             {"action": "fill", "selector": "#y", "value": "v"}]
    fr.record_receipt("r1", "goto", {"url": "https://a.com"}, True, summary="已打开 https://a.com",
                      step_index=1)
    fr.record_receipt("r2", "click", {"selector": "#x"}, False, error="selector #x 未找到",
                      step_index=2)
    rep = fr.execution_report(fr.get_receipts(0), steps, flow_name="测试流程")
    check("凭证含逐条账单", "第1步 goto → 成功" in rep and "第2步 click → 失败" in rep, rep)
    check("凭证含 X/Y 计数", "实际派发 2 步 / 计划 3 步" in rep, rep)
    check("未执行步骤显式列出", "未执行步骤" in rep and "第3步" in rep, rep)
    check("凭证防伪声明", "模型无法伪造" in rep, rep)

    # 冒名场景：0 条回执 → 必须显示「0 步实际执行」
    fresh_env()
    rep0 = fr.execution_report(fr.get_receipts(0), steps, flow_name="冒名测试")
    check("0 回执显示 0 步实际执行", "0 步实际执行" in rep0, rep0)
    check("0 回执计数为 0", "实际派发 0 步 / 计划 3 步" in rep0, rep0)
    check("0 回执列出全部未执行", rep0.count("第1步") >= 1 and "第3步" in rep0 and "未执行步骤" in rep0, rep0)

    # 额外派发（计划外动作，如 snapshot 核验）如实单列
    fresh_env()
    fr.record_receipt("e1", "goto", {"url": "u"}, True)
    fr.record_receipt("e2", "click", {"selector": "#a"}, True)
    fr.record_receipt("e3", "fill", {"selector": "#b", "text": "v"}, True)
    fr.record_receipt("e4", "snapshot", {}, True)
    repx = fr.execution_report(fr.get_receipts(0), steps)
    check("额外派发如实单列", "额外派发" in repx and "snapshot" in repx, repx)


# ══ ② context 自动推断（登录/内网/公开）════════════════════════

def t2_context_inference():
    print("② context 自动推断")
    login_steps = [{"action": "goto", "url": "https://shop.example.com/login"},
                   {"action": "fill", "selector": "#pw", "value": "{{password}}"}]
    c = fr.infer_flow_context(login_steps)
    check("登录流程 needs_login", c["needs_login"] is True, str(c))
    check("登录流程 preferred=headed", c["preferred"] == "headed", str(c))

    user_steps = [{"action": "fill", "selector": "#u", "value": "{{user}}"}]
    check("{{user}} 占位判登录", fr.infer_flow_context(user_steps)["needs_login"] is True)

    secret_steps = [{"action": "fill", "selector": "#a", "value": "x", "secret": True}]
    check("secret 步骤判登录", fr.infer_flow_context(secret_steps)["needs_login"] is True)

    for url, tag in (("http://192.168.1.10/admin", "192.168"),
                     ("http://10.0.0.5/erp", "10.x"),
                     ("http://127.0.0.1:8080/x", "127.0.0.1"),
                     ("http://localhost:3000/x", "localhost"),
                     ("http://100.64.1.2/x", "100.64"),
                     ("http://100.127.9.9/x", "100.127")):
        c = fr.infer_flow_context([{"action": "goto", "url": url}])
        check(f"内网判定 {tag}", c["intranet"] is True and c["preferred"] == "headed",
              f"{url} -> {c}")
    c = fr.infer_flow_context([{"action": "goto", "url": "http://100.128.0.1/x"}])
    check("100.128 不算内网", c["intranet"] is False, str(c))

    pub = fr.infer_flow_context([{"action": "goto", "url": "https://example.com/page"},
                                 {"action": "click", "selector": "#next"}])
    check("公开流程无登录无内网", pub["needs_login"] is False and pub["intranet"] is False, str(pub))
    check("公开流程 preferred=headless", pub["preferred"] == "headless", str(pub))


# ══ ③ engine 路由决策 + 无头越界显式拒绝 ═══════════════════════

def t3_engine_routing():
    print("③ engine 路由决策 + 无头显式拒绝")
    login_ctx = {"needs_login": True, "intranet": False}
    intranet_ctx = {"needs_login": False, "intranet": True}
    both_ctx = {"needs_login": True, "intranet": True}
    pub_ctx = {"needs_login": False, "intranet": False}

    check("auto+公开→headed（默认有头）", fr.decide_engine(pub_ctx, "auto") == ("headed", ""))
    check("auto+登录→headed", fr.decide_engine(login_ctx, "auto") == ("headed", ""))
    check("auto+内网→headed", fr.decide_engine(intranet_ctx, "auto") == ("headed", ""))
    check("显式 headed 放行", fr.decide_engine(login_ctx, "headed") == ("headed", ""))

    _, e1 = fr.decide_engine(login_ctx, "headless")
    check("无头遇登录显式拒绝", "此流程需要登录态" in e1 and "必须有头执行" in e1, e1)
    _, e2 = fr.decide_engine(intranet_ctx, "headless")
    check("无头遇内网显式拒绝", "内网环境" in e2 and "必须有头执行" in e2, e2)
    _, e3 = fr.decide_engine(both_ctx, "headless")
    check("登录+内网拒绝文案", "此流程需要登录态/内网环境，必须有头执行" in e3, e3)
    check("拒绝文案=禁止静默换引擎", "禁止静默换引擎" in e3, e3)
    _, e4 = fr.decide_engine(pub_ctx, "bogus")
    check("未知 engine 报因", "未知 engine" in e4, e4)

    # 无头执行驱动层二次防线：步骤 URL 命中登录/内网 → 显式报错，0 派发
    fresh_env()
    calls = []
    run = lambda a, p, s=False: (calls.append(a), (True, "ok"))[1]  # noqa: E731
    out = fr.execute_flow_steps([{"action": "goto", "url": "https://x.com/login"}], {},
                                run, engine="headless", flow_name="登录流")
    check("无头执行遇登录 URL 拒绝", not out["ok"] and "必须有头执行" in out["error"], out["error"])
    check("拒绝不派发 0 回执", len(fr.get_receipts(0)) == 0 and not calls)
    out2 = fr.execute_flow_steps([{"action": "goto", "url": "http://192.168.0.1/x"}], {},
                                 run, engine="headless", flow_name="内网流")
    check("无头执行遇内网 URL 拒绝", not out2["ok"] and "必须有头执行" in out2["error"], out2["error"])


# ══ ④ skill.md 生成（frontmatter/六节）═════════════════════════

def t4_skill_generation():
    print("④ skill.md 生成内容完整")
    fresh_env()
    body = {
        "name": "每日报价抓取",
        "steps": [
            {"action": "goto", "url": "https://quotes.example.com/login"},
            {"action": "fill", "selector": "#password", "value": "{{password}}", "secret": True},
            {"action": "click", "selector": "#submit", "text": "登录"},
            {"action": "goto", "url": "https://quotes.example.com/daily"},
            {"action": "click", "selector": "#export", "text": "导出"},
        ],
        "notes": "等页面加载完再点下一步\n导出前确认日期是今天",
    }
    out = bb.save_flow(body)
    check("save_flow 成功", out.get("ok") is True, str(out))
    check("save_flow 返回 context", out.get("context", {}).get("needs_login") is True, str(out))
    skill_path = Path(out.get("skill_file", ""))
    check("skill.md 自动生成", skill_path.is_file(), str(skill_path))
    md = skill_path.read_text(encoding="utf-8")
    check("frontmatter 含 name", 'name: "每日报价抓取"' in md, md[:200])
    check("frontmatter 含 description(Use when)",
          re.search(r'description: "Use when 按【每日报价抓取】流程来操作', md) is not None, md[:300])
    for sec in ("## 用途", "## 触发语", "## 步骤意图", "## ⚠注意事项", "## 执行上下文", "## 验收要点"):
        check(f"六节完整 {sec}", sec in md, md)
    check("触发语「按【…】流程来操作」", "按【每日报价抓取】流程来操作" in md)
    check("步骤意图为人话", ("打开 https://quotes.example.com/login" in md
                            and "点击「登录」" in md and "导出" in md), md)
    check("注意事项原文保留", "等页面加载完再点下一步" in md and "导出前确认日期是今天" in md)
    check("执行上下文三要素", ("需要登录态：是" in md and "内网环境：否" in md
                              and "默认引擎：headed" in md), md)
    check("验收要点含末步状态", "末步" in md and "执行凭证" in md, md)

    flows = bb.list_flows()
    check("list_flows 带 skill 存在标记", any(f.get("skill") for f in flows), str(flows))
    rec = bb.load_flow(out["id"])
    check("load_flow 带 skill_md 全文", len(rec.get("skill_md", "")) > 50)
    check("load_flow 带 context", rec.get("context", {}).get("preferred") == "headed")

    pub = bb.save_flow({"name": "PublicBatch", "steps": [
        {"action": "goto", "url": "https://open.example.com/list"},
        {"action": "click", "selector": "#next", "text": "下一页"}], "notes": ""})
    check("公开流程 skill 默认引擎 headless",
          "默认引擎：headless" in Path(pub["skill_file"]).read_text(encoding="utf-8"))
    check("公开流程 context preferred",
          pub.get("context", {}).get("preferred") == "headless", str(pub))

    bb.delete_flow(out["id"])
    check("删除流程同步删 skill", not skill_path.is_file())


# ══ ⑤ 密码脱敏（不落回执不落 skill）═══════════════════════════

def t5_masking():
    print("⑤ 密码脱敏")
    m = fr.mask_params({"password": "p@ss1", "text": "hello", "selector": "#u"})
    check("敏感键脱敏", m["password"] == "***", str(m))
    m2 = fr.mask_params({"selector": "#password", "text": "p@ss1"})
    check("密码框 text 脱敏", m2["text"] == "***", str(m2))
    m3 = fr.mask_params({"url": "https://a.com/?password=p@ss1&x=1"})
    check("URL 查询串凭据脱敏", "password=***" in m3["url"] and "p@ss1" not in m3["url"], str(m3))
    m4 = fr.mask_params({"selector": "#u", "text": "p@ss1"}, secret=True)
    check("secret 步骤值脱敏", m4["text"] == "***", str(m4))

    # skill 不落密码（secret 步骤的录制值）
    fresh_env()
    out = bb.save_flow({"name": "SecretFlow", "steps": [
        {"action": "goto", "url": "https://x.com/login"},
        {"action": "fill", "selector": "#u", "value": "hunter2-secret", "secret": True}],
        "notes": "无"})
    md = Path(out["skill_file"]).read_text(encoding="utf-8")
    check("skill 不落真实密码", "hunter2-secret" not in md and "***" in md, md)

    # 回执不落密码（无头执行步骤）
    fresh_env()
    recs_seen = []

    def run(a, p, s=False):
        recs_seen.append((a, dict(p), s))
        return True, "ok"

    fr.execute_flow_steps(
        [{"action": "goto", "url": "https://x.com/form"},
         {"action": "fill", "selector": "#u", "value": "{{password}}", "secret": True}],
        {"password": "hunter2-secret"}, run, engine="headless", flow_name="s")
    rc = fr.get_receipts(0)
    flat = json.dumps(rc, ensure_ascii=False)
    check("回执不落真实密码", "hunter2-secret" not in flat, flat)
    check("回执中敏感步脱敏为 ***",
          any(r["params"].get("text") == "***" for r in rc if r["action"] == "fill"), flat)
    check("执行通道拿到真实值", recs_seen[1][1].get("text") == "hunter2-secret" and recs_seen[1][2] is True)
    rep = fr.execution_report(rc, [{"action": "goto", "url": "u"}, {"action": "fill", "selector": "#u", "value": "{{password}}"}])
    check("凭证不落真实密码", "hunter2-secret" not in rep, rep)

    # 桥派发路径：submit_task(secret=True) → _complete 回执脱敏
    fresh_env()
    tid = bb.submit_task("fill", {"selector": "#password", "text": "hunter2-secret"},
                         secret=True)["task_id"]
    bb._complete(tid, True, {"url": "https://x.com", "title": "t"})
    rr = [x for x in fr.get_receipts(0) if x["task_id"] == tid][0]
    check("桥回执 secret 脱敏", rr["params"].get("text") == "***", str(rr))
    check("桥回执有结果摘要", rr["ok"] and "x.com" in rr["summary"], str(rr))


# ══ 确定性无头执行 + 桥派发回执（ws 回传点）+ HTTP 面 ═══════════

def t6_execution_driver_and_bridge():
    print("⑥ 无头确定性执行 + 桥回执 + HTTP 面")
    fresh_env()
    seen = []

    def run(a, p, s=False):
        seen.append((a, dict(p), s))
        return True, f"{a} ok"

    steps = [{"action": "goto", "url": "https://pub.example.com/list"},
             {"action": "click", "selector": "#next", "text": "下一页"},
             {"action": "fill", "selector": "#q", "value": "{{keyword}}"}]
    out = fr.execute_flow_steps(steps, {"keyword": "手机"}, run, engine="headless", flow_name="批量")
    check("无头执行全部成功", out["ok"] and out["executed"] == 3 and out["planned"] == 3, str(out))
    check("参数占位已填充", seen[2][1]["text"] == "手机", str(seen))
    check("每步写回执（step_index）", [r["step_index"] for r in fr.get_receipts(0)] == [1, 2, 3])
    check("无头回执 engine=headless", all(r["engine"] == "headless" for r in fr.get_receipts(0)))
    check("成功凭证", "实际派发 3 步 / 计划 3 步" in out["report"] and "第3步 fill → 成功" in out["report"],
          out["report"])

    # 失败即停：第 2 步失败 → 第 3 步显式未执行
    fresh_env()

    def run2(a, p, s=False):
        return (False, "selector #next 未找到") if a == "click" else (True, "ok")

    out2 = fr.execute_flow_steps(steps, {"keyword": "手机"}, run2, engine="headless", flow_name="批量")
    check("失败停跑如实报因", not out2["ok"] and "第2步 click 失败" in out2["error"], out2["error"])
    check("失败凭证 X=2/Y=3", "实际派发 2 步 / 计划 3 步" in out2["report"], out2["report"])
    check("失败后未执行显式列出", "未执行步骤" in out2["report"] and "第3步" in out2["report"], out2["report"])

    # 缺参数：先报不执行（0 派发）
    fresh_env()
    out3 = fr.execute_flow_steps(steps, {}, run, engine="headless", flow_name="批量")
    check("缺参数预检拒绝", not out3["ok"] and "需要参数" in out3["error"] and "keyword" in out3["error"],
          out3["error"])
    check("缺参数 0 派发", len(fr.get_receipts(0)) == 0)
    check("缺参数凭证 0 步", "0 步实际执行" in out3["report"], out3["report"])

    # 不支持的录制步骤 → 跳过并显式列出
    fresh_env()
    out4 = fr.execute_flow_steps([{"action": "goto", "url": "https://a.com"},
                                  {"action": "eval", "js": "x"},
                                  {"action": "click", "selector": "#b"}],
                                 {}, run, engine="headless", flow_name="s")
    check("跳过步骤不派发", out4["ok"] and out4["executed"] == 2, str(out4))
    check("跳过步骤显式列出", "跳过步骤" in out4["report"] and "eval" in out4["report"], out4["report"])

    # 桥派发回执（ws 回传点 _complete）
    fresh_env()
    tid = bb.submit_task("goto", {"url": "https://a.com"}, secret=False)["task_id"]
    msg = bb._pop_pending_task("")
    check("派发任务", msg and msg["task_id"] == tid)
    bb._complete(tid, True, {"url": "https://a.com", "title": "A"})
    rc = [x for x in fr.get_receipts(0) if x["task_id"] == tid]
    check("ws 回传点写回执", len(rc) == 1 and rc[0]["ok"] and rc[0]["engine"] == "app", str(rc))
    check("回执含耗时", rc and rc[0]["duration"] >= 0)

    # 超时也留账单，迟到结果覆盖改写
    fresh_env()
    tid2 = bb.submit_task("click", {"selector": "#x"})["task_id"]
    r_wait = bb.wait_result(tid2, wait_s=0.2)
    check("超时如实报因", not r_wait["ok"], str(r_wait))
    rc2 = fr.get_receipts(0)
    check("超时写回执（派发过就有账单）", len(rc2) == 1 and not rc2[0]["ok"], str(rc2))
    bb._complete(tid2, True, {"clicked": "#x", "url": "u", "title": "t"})
    rc3 = [x for x in fr.get_receipts(0) if x["task_id"] == tid2]
    check("迟到结果覆盖同一条回执", len(rc3) == 1 and rc3[0]["ok"], str(rc3))

    # HTTP 面：/api/browser/receipts?since= 与 /api/browser/task(secret)
    try:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        app = FastAPI()
        bb.install(app)
        client = TestClient(app)
        fresh_env()
        fr.record_receipt("h1", "goto", {"url": "https://a.com"}, True, summary="ok")
        resp = client.get("/api/browser/receipts", params={"since": 0})
        check("HTTP 回执面", resp.status_code == 200
              and len(resp.json().get("receipts", [])) == 1, resp.text)
        resp2 = client.post("/api/browser/task",
                            json={"action": "fill", "params": {"selector": "#password", "text": "p@ss"},
                                  "secret": True})
        tid3 = resp2.json().get("task_id", "")
        bb._complete(tid3, True, {"url": "u", "title": "t"})
        resp3 = client.get("/api/browser/receipts", params={"since": 0})
        recs = resp3.json().get("receipts", [])
        hit = [x for x in recs if x.get("task_id") == tid3]
        check("HTTP 任务面 secret 落回执脱敏", hit and hit[0]["params"].get("text") == "***",
              str(recs))
        resp4 = client.get("/api/browser/status")
        check("status 面存活", resp4.status_code == 200 and "tasks_total" in resp4.json())
    except Exception as e:  # TestClient 环境缺失 → 显式 FAIL，不静默跳过
        check("HTTP 面验证", False, f"{type(e).__name__}: {e}")


# ══ ⑦ webview 自动重开 + 断点续跑（自救代替报错，重试进凭证）══════

def t7_webview_selfheal():
    print("⑦ webview 自动重开+断点续跑（单步有界重试）")
    # ① webview 已关闭 → ensure-webview（有界10s）→ 重试该步一次 → 成功继续
    fresh_env()
    calls, ens = [], []

    def run(a, p, s=False):
        calls.append(a)
        return (False, "webview 已关闭") if len(calls) == 1 else (True, "ok")

    def ensure(t):
        ens.append(t)
        return True, "webview 已自动重开就绪"

    out = fr.execute_flow_steps([{"action": "goto", "url": "https://a.com"}], {}, run,
                                engine="app", flow_name="自救", ensure_webview=ensure)
    check("①webview已关闭→ensure→重试成功", out["ok"] and out["executed"] == 1, str(out))
    check("①ensure 有界调用1次（10s）", len(ens) == 1 and ens[0] == 10.0, str(ens))
    rc = fr.get_receipts(0)
    check("①重试步回执标注 (重试1)", bool(rc) and rc[0]["action"] == "goto(重试1)", str(rc))
    check("①重试成功回执为成功", bool(rc) and rc[0]["ok"] is True, str(rc))
    check("①凭证可见自救（重试1 进凭证）", "(重试1)" in out["report"], out["report"])
    check("①执行日志不掩盖自救过程", any("自救" in r for r in out["results"]), str(out["results"]))

    # ② 重试仍败 → 如实报错「（重开后仍失败）」，不无限重试（每步最多2次调用）
    fresh_env()
    calls2 = []

    def run2(a, p, s=False):
        calls2.append(a)
        return False, "webview 未就绪"

    out2 = fr.execute_flow_steps([{"action": "goto", "url": "https://a.com"},
                                  {"action": "click", "selector": "#x"}],
                                 {}, run2, engine="app", flow_name="自救失败",
                                 ensure_webview=lambda t: (True, "已重开"))
    check("②重试仍败→整体失败", not out2["ok"] and "第1步" in out2["error"], out2["error"])
    check("②重试仍败如实报（重开后仍失败）", "重开后仍失败" in out2["error"], out2["error"])
    check("②不无限重试（每步最多2次调用）", len(calls2) == 2, str(calls2))
    check("②失败即停第2步未执行", "未执行步骤" in out2["report"] and "第2步" in out2["report"],
          out2["report"])
    rc2 = fr.get_receipts(0)
    check("②失败重试回执 (重试1)+仍失败标注",
          bool(rc2) and "(重试1)" in rc2[0]["action"] and "重开后仍失败" in rc2[0]["error"], str(rc2))

    # 两层防线互认：已带自救标注的错误不再二次重试（叠加仍为有界）
    fresh_env()
    calls3, ens3 = [], []

    def run3(a, p, s=False):
        calls3.append(a)
        return False, "webview 已关闭（重开后仍失败）"

    out3 = fr.execute_flow_steps([{"action": "goto", "url": "https://a.com"}], {}, run3,
                                 engine="app", flow_name="已自救",
                                 ensure_webview=lambda t: (ens3.append(t), (True, "x"))[1])
    check("②已自救错误不再二次重试", len(calls3) == 1 and not ens3 and not out3["ok"], str(calls3))
    check("②已重试标注判据（(重试1)命中）",
          fr.webview_lost("webview 已关闭") and not fr.webview_lost("goto(重试1) webview 已关闭"))

    # ensure 失败（webview-ready 超时）→ 仍只重试一次，超时如实并入报因（禁挂死）
    fresh_env()
    calls4, ens4 = [], []

    def run4(a, p, s=False):
        calls4.append(a)
        return False, "webview 未就绪"

    out4 = fr.execute_flow_steps([{"action": "goto", "url": "https://a.com"}], {}, run4,
                                 engine="app", flow_name="ensure超时",
                                 ensure_webview=lambda t: (ens4.append(t),
                                                           (False, "webview-ready 等待超时（10s 有界）"))[1])
    check("④ensure 超时仍单次重试不挂死", len(calls4) == 2 and not out4["ok"], str(calls4))
    check("④ensure 超时如实并入过程记录",
          any("webview-ready" in r for r in out4["results"]), str(out4["results"]))

    # ③ 重试在回执里可见（桥派发层：receipt_action=(重试1) 标注 + fail_note 附注）
    fresh_env()
    tid = bb.submit_task("screenshot", {}, receipt_action="screenshot(重试1)",
                         fail_note="（重开后仍失败）")["task_id"]
    bb._complete(tid, False, error="webview 已关闭")
    rr = [x for x in fr.get_receipts(0) if x["task_id"] == tid][0]
    check("③桥回执 action 标注 (重试1)", rr["action"] == "screenshot(重试1)", str(rr))
    check("③桥回执失败附（重开后仍失败）", "（重开后仍失败）" in rr["error"], str(rr))
    tid2 = bb.submit_task("screenshot", {}, receipt_action="screenshot(重试1)")["task_id"]
    bb._complete(tid2, True, {"path": "/tmp/s.png"})
    rr2 = [x for x in fr.get_receipts(0) if x["task_id"] == tid2][0]
    check("③重试成功回执 screenshot(重试1) → OK",
          rr2["ok"] and rr2["action"] == "screenshot(重试1)", str(rr2))

    # ④ webview-ready 超时有界（bridge 控制通道 ensure_webview）
    bb._APPS.clear()
    bb._ENSURE_WAITERS.clear()
    t0 = time.time()
    r = bb.ensure_webview(timeout=1.0)
    check("④无应用连接立即如实报因", not r["ok"] and "未连接" in r["error"], str(r))
    check("④无应用返回不阻塞", time.time() - t0 < 1.0, f"{time.time() - t0:.2f}s")

    import asyncio
    import threading as _th
    loop = asyncio.new_event_loop()
    _th.Thread(target=loop.run_forever, daemon=True).start()

    class _FakeWS:  # 指令下发成功但应用不回 webview-ready
        sent = []

        async def send_text(self, m):
            _FakeWS.sent.append(m)

    bb._APPS["_default"] = {"ws": _FakeWS(), "loop": loop}
    t1 = time.time()
    r2 = bb.ensure_webview(timeout=1.5)
    dt = time.time() - t1
    check("④webview-ready 超时有界如实报因", not r2["ok"] and "超时" in r2["error"] and "有界" in r2["error"],
          str(r2))
    check("④超时≈设定值不挂死（1.2s≤dt<4s）", 1.2 <= dt < 4.0, f"{dt:.2f}s")
    check("④ensure-webview 控制指令已下发", bool(_FakeWS.sent) and "ensure-webview" in _FakeWS.sent[0],
          str(_FakeWS.sent))

    class _ReplyWS:  # 应用收到 ensure-webview → 自动重开 → 回 webview-ready
        async def send_text(self, m):
            msg = json.loads(m)
            bb._on_control({"type": "control", "cmd": "webview-ready",
                            "request_id": msg.get("request_id"), "ok": True})

    bb._APPS["_default"] = {"ws": _ReplyWS(), "loop": loop}
    r3 = bb.ensure_webview(timeout=3.0)
    check("④webview-ready 回执→自救成功", r3["ok"] is True, str(r3))
    check("④等待者清空（无泄漏）", not bb._ENSURE_WAITERS, str(bb._ENSURE_WAITERS))
    bb._APPS.clear()

    # HTTP 面：/api/browser/ensure-webview（无应用=快速如实报因，有界）
    try:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        app = FastAPI()
        bb.install(app)
        client = TestClient(app)
        resp = client.post("/api/browser/ensure-webview", json={"timeout": 2})
        check("④HTTP ensure-webview 面", resp.status_code == 200
              and resp.json().get("ok") is False and "未连接" in resp.json().get("error", ""),
              resp.text)
    except Exception as e:
        check("④HTTP ensure-webview 面", False, f"{type(e).__name__}: {e}")

    # ⑥ 单步自救第二层防线：_run_app_mode 层（有界重试进回执，禁无限重试）
    sys.path.insert(0, str(ACP_ROOT / "agent"))  # 运行时经 sys.path 解析
    import browser_tools  # pyright: ignore[reportMissingImports]
    bt = browser_tools.BrowserTools()
    seq = {"n": 0}
    ens_calls = []

    def fake_dispatch(action, params, secret=False, receipt_action="", fail_note=""):
        seq["n"] += 1
        if seq["n"] == 1:
            return "❌ 浏览器操作失败（screenshot）：webview 已关闭"
        assert receipt_action == "screenshot(重试1)", receipt_action
        return "📸 截图已保存: /tmp/s.png（123 字节）"

    bt._app_dispatch = fake_dispatch
    bt._ensure_webview = lambda timeout_s=10.0: (
        ens_calls.append(timeout_s), (True, "webview 已自动重开就绪"))[1]
    out5 = bt._run_app_mode("screenshot", {})
    check("⑥_run_app_mode 自救重试成功", "截图已保存" in out5 and "自救" in out5, out5)
    check("⑥_run_app_mode ensure 有界1次（10s）", ens_calls == [10.0], str(ens_calls))

    cnt = {"n": 0}

    def fake_dispatch_fail(action, params, secret=False, receipt_action="", fail_note=""):
        cnt["n"] += 1
        if cnt["n"] > 1:  # 重试派发才带标注（首派发原样）
            assert receipt_action == "screenshot(重试1)" and fail_note == "（重开后仍失败）", \
                (receipt_action, fail_note)
        return "❌ 浏览器操作失败（screenshot）：webview 已关闭"

    bt._app_dispatch = fake_dispatch_fail
    out6 = bt._run_app_mode("screenshot", {})
    check("⑥_run_app_mode 重试仍败如实报（重开后仍失败）", "重开后仍失败" in out6, out6)
    check("⑥_run_app_mode 禁无限重试（2次派发）", cnt["n"] == 2, str(cnt))

    cnt2 = {"n": 0}

    def fake_dispatch_done(action, params, secret=False, receipt_action="", fail_note=""):
        cnt2["n"] += 1
        return "❌ 浏览器操作失败（screenshot）：webview 已关闭（重开后仍失败）"

    bt._app_dispatch = fake_dispatch_done
    bt._run_app_mode("screenshot", {})
    check("⑥两层防线互认：已自救错误不再二次重试", cnt2["n"] == 1, str(cnt2))


if __name__ == "__main__":
    t1_receipts_and_report()
    t2_context_inference()
    t3_engine_routing()
    t4_skill_generation()
    t5_masking()
    t6_execution_driver_and_bridge()
    t7_webview_selfheal()
    print(f"\n== 结果: {PASS} PASS / {FAIL} FAIL ==")
    sys.exit(0 if FAIL == 0 else 1)

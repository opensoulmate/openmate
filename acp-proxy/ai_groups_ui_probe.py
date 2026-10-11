#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""13.4 验收行 A8 · AI 群组 UI 面 DOM 探针（可复跑）。

对齐 references/ai-groups-e2e.md §3/§4：免登录注入 localStorage 四键
（先 about:blank 注册脚本再导航，防登录守卫）→ /ai-groups 页面 DOM 验证：
  A8-a 页面含建组入口（Create Group/创建群组）
  A8-b 群组列表含真实验收群组名（3 真实成员建的组）
  A8-c 成员/讨论语义可见（members/discussion 关键词）
  A8-d Send to 输入框存在（@All 目标选择）

CDP 纪律：setCacheDisabled 必开防缓存假阴性；新开标签页用完关掉（不动用户既有页）。

运行：~/opensoul/.venv/bin/python ai_groups_ui_probe.py [群组名关键字]
结果 JSON：/tmp/ai_groups_ui_probe_result.json
"""
import json
import sys
import time
import urllib.request
import uuid
from pathlib import Path

OPSOUL = Path.home() / "opensoul"
sys.path.insert(0, str(OPSOUL))

CDP_HTTP = "http://127.0.0.1:9222"
UI_URL = "http://127.0.0.1:3000/ai-groups?probe=ui134"
RESULT_PATH = Path("/tmp/ai_groups_ui_probe_result.json")


def mint_token() -> str:
    from src.services.auth import create_access_token

    return create_access_token(uuid.uuid4())


def main() -> int:
    import websockets.sync.client as wsclient

    group_keyword = sys.argv[1] if len(sys.argv) > 1 else "13.4真实成员验收"
    token = mint_token()
    user_id = str(uuid.uuid4())

    info = json.loads(urllib.request.urlopen(f"{CDP_HTTP}/json/version", timeout=5).read())
    ws_url = info["webSocketDebuggerUrl"]

    verdicts = {}
    detail = {}
    with wsclient.connect(ws_url, max_size=64 * 1024 * 1024) as ws:
        seq = [0]

        def send(method, params=None, session=None):
            seq[0] += 1
            mid = seq[0]
            msg = {"id": mid, "method": method, "params": params or {}}
            if session:
                msg["sessionId"] = session
            ws.send(json.dumps(msg))
            deadline = time.time() + 30
            while time.time() < deadline:
                resp = json.loads(ws.recv())
                if resp.get("id") == mid:
                    if "error" in resp:
                        raise RuntimeError(f"{method}: {resp['error']}")
                    return resp.get("result", {})
            raise RuntimeError(f"{method}: timeout")

        tgt = send("Target.createTarget", {"url": "about:blank"})
        tid = tgt["targetId"]
        attached = send("Target.attachToTarget", {"targetId": tid, "flatten": True})
        sid = attached["sessionId"]
        try:
            send("Network.enable", session=sid)
            send("Network.setCacheDisabled", {"cacheDisabled": True}, session=sid)
            send("Page.enable", session=sid)
            # 先注册脚本再导航（登录守卫在 hydration 时读 localStorage）
            inject = (
                "try{"
                f"localStorage.setItem('openmate-token', {json.dumps(token)});"
                f"localStorage.setItem('openmate-user-id', {json.dumps(user_id)});"
                "localStorage.setItem('openmate-username', 'probe134');"
                "localStorage.setItem('openmate-api-url', 'http://127.0.0.1:8090');"
                "}catch(e){}"
            )
            send(
                "Page.addScriptToEvaluateOnNewDocument",
                {"source": inject},
                session=sid,
            )
            send("Page.navigate", {"url": UI_URL}, session=sid)
            time.sleep(10.0)

            inner = send(
                "Runtime.evaluate",
                {
                    "expression": "document.body.innerText",
                    "returnByValue": True,
                },
                session=sid,
            )
            text = (inner.get("result", {}) or {}).get("value") or ""

            has_sendto = send(
                "Runtime.evaluate",
                {
                    "expression": (
                        "(()=>{const t=document.body.innerText;"
                        "const inp=document.querySelector('input[placeholder*=\"Send\"],"
                        "textarea[placeholder*=\"Send\"],input[placeholder*=\"发送\"],"
                        "textarea[placeholder*=\"发送\"]');"
                        "const sendto=t.includes('Send to')||t.includes('发送到');"
                        "return JSON.stringify({input:!!inp,sendtoText:sendto});})()"
                    ),
                    "returnByValue": True,
                },
                session=sid,
            )
            sendto_info = json.loads(
                (has_sendto.get("result", {}) or {}).get("value") or "{}"
            )

            detail = {
                "text_len": len(text),
                "text_sample": text[:600],
                "sendto": sendto_info,
            }
            RESULT_PATH.write_text(
                json.dumps(detail, ensure_ascii=False, indent=2), encoding="utf-8"
            )

            create_kw = ("Create Group" in text) or ("创建群组" in text) or ("新建群组" in text)
            member_kw = ("member" in text.lower()) or ("成员" in text)
            discuss_kw = ("discussion" in text.lower()) or ("讨论" in text)
            verdicts = {
                "A8-a 建组入口(Create Group/创建群组)": create_kw,
                "A8-b 真实验收群组在列表(group_keyword命中)": group_keyword in text,
                "A8-c 成员/讨论语义可见": member_kw and discuss_kw,
                "A8-d Send to 输入框/目标选择": bool(sendto_info.get("input"))
                or bool(sendto_info.get("sendtoText")),
            }
        finally:
            send("Target.closeTarget", {"targetId": tid})

    out = {"ui_url": UI_URL, "group_keyword": group_keyword, **detail, "verdicts": verdicts}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    ok = all(verdicts.values())
    print("\nVERDICT:", "PASS" if ok else "FAIL")
    for k, v in verdicts.items():
        print(f"  {'✅' if v else '❌'} {k}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""国内网关自建应用全双工 · 验收脚本（Level 2，待用户提供凭据后跑）。

全双工 = 收消息 → SoulMate 处理 → 回消息，需在钉钉/飞书开放平台注册**自建应用**
并拿 appid/secret（+ 配事件回调地址）。本脚本在凭据到位后验收三层：

    ① 取访问令牌（appid/secret → tenant_access_token / accessToken，真 API）
    ② 发一条测试消息到指定对端（peer / chat_id，真 API）
    ③ （可选）验证回调签名校验/事件解密函数

凭据一律本地环境变量（不进 git，见 .env.example）::

    钉钉:  DINGTALK_APP_KEY / DINGTALK_APP_SECRET
    飞书:  FEISHU_APP_ID   / FEISHU_APP_SECRET

用法::

    python scripts/test_full_duplex.py --channel dingtalk --peer user123 --text '全双工自检'
    python scripts/test_full_duplex.py --channel feishu   --peer ou_xxx  --text '全双工自检'

缺凭据时如实报告"待用户提供凭据"并退出（不假装通过）。收消息侧（事件回调/
Stream）需公网回调地址，凭据+回调到位后接入——见 docs/gateway-domestic-feishu-dingtalk.md。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.domestic_bridge import (  # noqa: E402
    CHANNEL_DINGTALK,
    CHANNEL_FEISHU,
    DomesticAppTransport,
    DomesticMessage,
    DomesticBridge,
    LoopbackTransport,
    app_credentials,
)


def _require_creds(channel: str) -> tuple[str, str]:
    app_id, app_secret = app_credentials(channel)
    if not app_id or not app_secret:
        env = ("DINGTALK_APP_KEY / DINGTALK_APP_SECRET"
               if channel == CHANNEL_DINGTALK else
               "FEISHU_APP_ID / FEISHU_APP_SECRET")
        print(f"✗ [{channel}] 待用户提供凭据：请在本地环境设置 {env}"
              f"（开放平台注册自建应用后获取，见 docs）", file=sys.stderr)
        raise SystemExit(4)
    return app_id, app_secret


def main() -> int:
    ap = argparse.ArgumentParser(description="国内网关自建应用全双工验收（Level 2）")
    ap.add_argument("--channel", required=True, choices=[CHANNEL_DINGTALK, CHANNEL_FEISHU])
    ap.add_argument("--peer", default="", help="对端（钉钉 userId / 飞书 open_id）")
    ap.add_argument("--text", default="SoulMate 全双工通道自检", help="测试消息文本")
    ap.add_argument("--dry-run", action="store_true",
                    help="只跑回路（loopback）不发真消息，验证收发逻辑")
    args = ap.parse_args()

    app_id, app_secret = _require_creds(args.channel)
    transport = DomesticAppTransport(args.channel, app_id, app_secret)

    # ① 取访问令牌（真 API）
    print(f"→ ① 取 {args.channel} 访问令牌…", file=sys.stderr)
    try:
        token = transport.ensure_token()
    except Exception as e:  # noqa: BLE001
        print(f"✗ 取令牌失败（凭据错/网络不通）: {e}", file=sys.stderr)
        return 1
    print(f"✓ 令牌已获取（{len(token)} 字符，前 6 位 {token[:6]}…）")

    if args.dry_run:
        # ② 回路自检（loopback）：push 入站 → 回合 → 回复（不发真消息）
        print("→ ② dry-run：loopback 收发回路自检…", file=sys.stderr)
        loop = LoopbackTransport(args.channel)

        async def _turn(msg: DomesticMessage) -> str:
            return f"回声：{msg.content}"

        bridge = DomesticBridge(args.channel, loop, turn_fn=_turn)
        loop.push(DomesticMessage(channel=args.channel, msg_id="t-1",
                                  from_user="self", content="ping"))
        asyncio.run(bridge.run_once())
        assert loop.sent and loop.sent[0][1] == "回声：ping", "回路回复不符"
        print("✓ 回路收发自检通过（入站→回合→回复）")
        return 0

    # ② 发真消息到对端（真 API）
    if not args.peer:
        print("✗ 全双工发消息需 --peer（钉钉 userId / 飞书 open_id）；"
              "仅回路自检请加 --dry-run", file=sys.stderr)
        return 2
    print(f"→ ② 发真消息到 {args.peer!r}…", file=sys.stderr)
    ok = asyncio.run(transport.send_text(args.peer, args.text))
    if ok:
        print(f"✓ 已发送到 {args.peer}：{args.text!r}")
        print("  （收消息侧需公网事件回调/Stream，凭据+回调地址到位后接入）")
        return 0
    print(f"✗ 发送失败（见日志明细）", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

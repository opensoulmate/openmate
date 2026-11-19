#!/usr/bin/env python3
"""国内网关 webhook 快路 · 验收钩子：给一个钉钉/飞书 webhook 地址即收 SoulMate 真消息。

用法（用户只需给一个 webhook 地址）::

    # ① 推一条指定消息（验证 webhook 通路，秒回）
    python scripts/send_domestic_push.py --webhook 'https://oapi.dingtalk.com/robot/send?access_token=xxx' \
        --message 'SoulMate 上线，网关通道已通'

    # ② 跑一轮真实 SoulMate 回合，把 agent 真回复推进群（真消息验收）
    python scripts/send_domestic_push.py --webhook 'https://open.feishu.cn/open-apis/bot/v2/hook/xxx' \
        --turn '用一句话介绍你自己'

通道可自动识别（钉钉 oapi.dingtalk.com / 飞书 open.feishu.cn·lark），也可 --channel 显式指定。
推送体走 **echo 口径**（opensoul/src/echo/dispatcher.py 同款负载，不另立山头）。

凭据纪律：webhook 加签密钥走 --secret 或环境变量 DINGTALK_WEBHOOK_SECRET /
FEISHU_WEBHOOK_SECRET，不写死、不进 git。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

# acp-proxy 根入 path（scripts/ 下运行也能 import agent.*）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.domestic_bridge import (  # noqa: E402
    CHANNEL_DINGTALK,
    CHANNEL_FEISHU,
    send_webhook,
    webhook_secret,
)


def _detect_channel(webhook: str) -> str:
    u = webhook.lower()
    if "dingtalk" in u or "oapi.dingtalk" in u:
        return CHANNEL_DINGTALK
    if "feishu" in u or "lark" in u:
        return CHANNEL_FEISHU
    return ""


def _run_real_turn(prompt: str) -> str:
    """跑一轮真实 SoulMate 回合，返回 agent 真回复文本（真消息验收用）。

    复用 wakeup_executor 的无客户端 headless agent（与国内网关桥默认回合体同源）。
    agent 基础设施不可用时抛异常，由调用方如实报错（不假装出真消息）。
    """
    from agent import wakeup_executor as we

    agent = we._get_headless_agent()

    async def _go() -> str:
        resp_new = await agent.new_session()
        sid = str(
            getattr(resp_new, "session_id", None)
            or getattr(resp_new, "sessionId", None)
            or ""
        )
        if not sid:
            raise RuntimeError("建会话失败：new_session 未返回 session_id")
        pre_id = we._max_assistant_id(agent, sid)
        await agent.prompt([{"type": "text", "text": prompt}], sid)
        db = agent._get_db()
        try:
            rows = db.execute(
                "SELECT content FROM agent_messages "
                "WHERE session_id=? AND role='assistant' AND id>? ORDER BY id",
                (sid, int(pre_id)),
            ).fetchall()
        finally:
            db.close()
        text = "\n\n".join((r[0] or "").strip() for r in rows if (r[0] or "").strip())
        if not text.strip():
            raise RuntimeError("SoulMate 回合零输出——不谎报真消息")
        return text

    return asyncio.run(_go())


def main() -> int:
    ap = argparse.ArgumentParser(description="国内网关 webhook 快路 · 验收钩子")
    ap.add_argument("--webhook", required=True, help="钉钉/飞书群机器人 webhook 地址")
    ap.add_argument("--channel", choices=[CHANNEL_DINGTALK, CHANNEL_FEISHU],
                    default="", help="通道（缺省按 webhook 域名自动识别）")
    ap.add_argument("--message", default="", help="直接推送的文本（与 --turn 二选一）")
    ap.add_argument("--turn", default="", help="跑一轮真实 SoulMate 回合并推送其回复（真消息）")
    ap.add_argument("--title", default="SoulMate", help="推送标题（echo 口径卡片头）")
    ap.add_argument("--secret", default="", help="webhook 加签/签名密钥（机器人开了安全设置才需）")
    args = ap.parse_args()

    channel = args.channel or _detect_channel(args.webhook)
    if not channel:
        print("✗ 无法从 webhook 域名识别通道，请用 --channel dingtalk|feishu 显式指定",
              file=sys.stderr)
        return 2

    secret = args.secret or webhook_secret(channel)

    if args.turn:
        print(f"→ 跑真实 SoulMate 回合：{args.turn!r}", file=sys.stderr)
        try:
            content = _run_real_turn(args.turn)
        except Exception as e:  # noqa: BLE001
            print(f"✗ SoulMate 回合失败（如实报告，不假推送）: {e}", file=sys.stderr)
            return 3
        title = args.title
    elif args.message:
        content = args.message
        title = args.title
    else:
        content = "SoulMate 网关通道自检：这条消息由 webhook 快路送达。"
        title = args.title

    ok, detail = send_webhook(channel, args.webhook, title, content, secret=secret)
    if ok:
        print(f"✓ [{channel}] 已推送（echo 口径）：{detail}")
        print(f"  标题: {title}")
        print(f"  内容: {content[:200]}{'…' if len(content) > 200 else ''}")
        return 0
    print(f"✗ [{channel}] 推送失败：{detail}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

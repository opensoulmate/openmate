"""
微信平台只读对账 — freepublish/batchget + draft/batchget（绝不写平台）

凭据：复用发布链 config.json（publish_wechat.py 同一份），不落明文：
- $WECHAT_PUBLISH_CONFIG（显式指定）
- ~/.openmate/shared-skills/wechat-article-publisher/config.json（现役发布链）
- ~/Documents/Hermes/wechat-article-publisher/config.json（工作副本）
网络/凭据不可达 → 降级本地对账口径，status 显式报因（禁静默）。
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

TOKEN_URL = "https://api.weixin.qq.com/cgi-bin/token"
FREEPUBLISH_BATCHGET = "https://api.weixin.qq.com/cgi-bin/freepublish/batchget"
DRAFT_BATCHGET = "https://api.weixin.qq.com/cgi-bin/draft/batchget"

CONFIG_CANDIDATES = (
    "~/.openmate/shared-skills/wechat-article-publisher/config.json",
    "~/Documents/Hermes/wechat-article-publisher/config.json",
)


def _load_creds() -> tuple[str, str] | None:
    paths = []
    if os.environ.get("WECHAT_PUBLISH_CONFIG"):
        paths.append(Path(os.environ["WECHAT_PUBLISH_CONFIG"]).expanduser())
    paths += [Path(p).expanduser() for p in CONFIG_CANDIDATES]
    for path in paths:
        if not path.is_file():
            continue
        try:
            cfg = json.loads(path.read_text(encoding="utf-8"))
            wx = cfg.get("wechat") or {}
            appid = str(wx.get("app_id") or "").strip()
            secret = str(wx.get("app_secret") or "").strip()
        except (json.JSONDecodeError, OSError):
            continue
        if appid and secret:
            return appid, secret
    return None


def _post_json(url: str, payload: dict[str, Any], timeout: float = 10.0) -> dict[str, Any]:
    req = urllib.request.Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get_json(url: str, timeout: float = 10.0) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _safe_error(e: Exception) -> str:
    """报因脱敏：URL 里带 secret，异常文本原样不外泄。"""
    text = f"{type(e).__name__}: {e}"
    return re.sub(r"secret=[^&\s]+", "secret=***", text)[:160]


def get_token(appid: str, secret: str) -> str:
    data = _get_json(f"{TOKEN_URL}?grant_type=client_credential&appid={appid}&secret={secret}")
    if "access_token" not in data:
        raise RuntimeError(f"取平台 token 失败: errcode={data.get('errcode')} {data.get('errmsg', '')}")
    return str(data["access_token"])


class PlatformAPIError(RuntimeError):
    """平台接口报错（非『查无』——不得当空结果消费，禁静默）"""


def fetch_published(token: str, count: int = 20) -> list[dict[str, Any]]:
    """freepublish/batchget：平台已发表文章（含封面 thumb_url）。接口报错显式上抛。"""
    data = _post_json(f"{FREEPUBLISH_BATCHGET}?access_token={token}",
                      {"offset": 0, "count": max(1, min(count, 50)), "no_content": 0})
    if data.get("errcode"):
        raise PlatformAPIError(
            f"freepublish/batchget errcode={data.get('errcode')} {data.get('errmsg', '')}")
    items = []
    for item in (data.get("item") or []):
        content = item.get("content") or {}
        for news in (content.get("news_item") or []):
            items.append({
                "title": str(news.get("title") or ""),
                "thumb_url": str(news.get("thumb_url") or ""),
                "publish_time": int(item.get("publish_time") or 0),
                "article_id": str(item.get("article_id") or ""),
            })
    return items


_DRAFT_NOT_FOUND = (40007, 40097)


def fetch_draft(token: str, media_id: str) -> dict[str, Any] | None:
    """draft/batchget：单草稿（封面挂没挂看 news_item.thumb_media_id）。

    草稿不存在（errcode 40007/40097）→ None；其余报错显式上抛（不得当查无）。
    """
    data = _post_json(f"{DRAFT_BATCHGET}?access_token={token}",
                      {"media_id": media_id, "offset": 0, "count": 20, "no_content": 0})
    if data.get("errcode"):
        if int(data.get("errcode") or 0) in _DRAFT_NOT_FOUND:
            return None
        raise PlatformAPIError(
            f"draft/batchget errcode={data.get('errcode')} {data.get('errmsg', '')}")
    items = data.get("item") or []
    if not items:
        return None
    news = (items[0].get("news_item") or [{}])[0]
    return {
        "title": str(news.get("title") or ""),
        "thumb_media_id": str(news.get("thumb_media_id") or ""),
    }


def _norm_title(title: str) -> str:
    return re.sub(r"[\s\u3000—–\-·:：,，。.!！?？()（）《》「」\[\]]", "", (title or "").lower())


def _title_match(a: str, b: str) -> bool:
    na, nb = _norm_title(a), _norm_title(b)
    if not na or not nb:
        return False
    return na == nb or na in nb or nb in na


def platform_reconcile(entries: list[dict[str, Any]], *, window_days: int = 3,
                       now: _dt.datetime) -> tuple[list[dict[str, Any]], str]:
    """平台侧对账（只读）：返回 (issues, status)。

    发表列表不可查（如 freepublish 未授权）→ 降级为仅草稿箱对账，status 显式
    报因；『未见发表』只有在发表列表真正可查时才下结论（禁把接口报错当空结果）。
    """
    creds = _load_creds()
    if creds is None:
        return [], "unavailable: 未找到发布链 config.json 凭据（本地对账口径）"
    try:
        token = get_token(*creds)
    except Exception as e:  # noqa: BLE001 — 平台不可达降级本地口径，显式报因
        return [], f"unavailable: {_safe_error(e)}"

    try:
        published = fetch_published(token, count=50)
        status = "live"
    except Exception as e:  # noqa: BLE001 — 发表列表不可查≠没发表，只降级不误判
        published = None
        status = f"degraded: 发表列表不可查（{_safe_error(e)}）——仅按草稿箱对账"

    issues: list[dict[str, Any]] = []
    today = now.date()
    for entry in entries:
        if not entry.get("draft_id"):
            continue
        matched = None
        if published is not None:
            matched = next((p for p in published if _title_match(p["title"], entry["title"])), None)
        if matched is not None:
            if not matched["thumb_url"]:
                issues.append({
                    "kind": "封面未挂", "ledger": entry.get("ledger", ""),
                    "article": entry["title"], "date": entry.get("date", ""),
                    "detail": "平台已发表但无封面（freepublish thumb_url 为空）",
                    "evidence": f"article_id={matched['article_id']}",
                })
            continue
        try:
            draft = fetch_draft(token, entry["draft_id"])
        except Exception as e:  # noqa: BLE001
            issues.append({
                "kind": "平台不一致", "ledger": entry.get("ledger", ""),
                "article": entry["title"], "date": entry.get("date", ""),
                "detail": f"草稿查询失败: {_safe_error(e)}",
                "evidence": f"draft_id={entry['draft_id'][:16]}…",
            })
            continue
        if draft is None:
            detail = ("账本记有草稿，但平台草稿箱查无"
                      + ("（发表列表不可查：是否已发需人工到公众号后台核对）"
                         if published is None else "、也未见发表（疑似漏发或草稿被删）"))
            issues.append({
                "kind": "平台不一致", "ledger": entry.get("ledger", ""),
                "article": entry["title"], "date": entry.get("date", ""),
                "detail": detail,
                "evidence": f"draft_id={entry['draft_id'][:16]}…",
            })
            continue
        if not draft["thumb_media_id"]:
            issues.append({
                "kind": "封面未挂", "ledger": entry.get("ledger", ""),
                "article": entry["title"], "date": entry.get("date", ""),
                "detail": "草稿 thumb_media_id 为空——封面未挂（发布没消费封面的直接实证）",
                "evidence": f"draft_id={entry['draft_id'][:16]}…",
            })
        elif entry.get("date", "") < today.isoformat():
            issues.append({
                "kind": "待确认积压", "ledger": entry.get("ledger", ""),
                "article": entry["title"], "date": entry.get("date", ""),
                "detail": f"草稿带封面但 {entry.get('date')} 至今未发布（公众号后台待确认/漏确认）",
                "evidence": f"draft_id={entry['draft_id'][:16]}… thumb 挂载正常",
            })
    return issues, status

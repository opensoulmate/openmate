"""路线图 8.4 调研兜底：web_search 后端可用性探测 + 显式降级（禁静默归零）。

单一真源：两处 web_search 执行点（soulmate_agent 主循环 / code-mode 批内）共用
本模块 run_search()，内联 curl-SearXNG 逻辑全部收口到这里。

后端链（对齐 unified-search 技能的降级链）：
1. SearXNG JSON API（默认 http://localhost:8888，SOULMATE_SEARXNG_URL 可配）
2. Bing HTML 直搜兜底（技能已验证的中间层兜底，SOULMATE_SEARCH_BING_FALLBACK=0 关闭）

状态语义（禁静默归零——R4 根因：后端挂了被报成"无搜索结果"）：
- ok          — 有结果（降级命中时显式标注命中的后端）
- empty       — 后端**可信**确认 0 结果（searx JSON results:[]；显式声明"非故障"）
- inconclusive— 有后端响应但结果不可信（bing HTML 0 块=疑似反爬拦截，禁断言"无资料"）
- unavailable — 全部后端不可达/响应异常（显式逐后端报因 + SearXNG 启动文档化提示）

query 编码铁律：早期实现 f-string 拼裸 query 进 URL，含空格/中文的查询被截断
→ 后端返回 0 结果被报成"无搜索结果"（又一个静默归零根因），本模块统一 quote()。
"""

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple
from urllib.parse import quote

DEFAULT_SEARXNG_URL = "http://localhost:8888"

# 启动文档化（8.4 要求"替代后端或启动文档化"）——命令来自 unified-search 技能实测流程
STARTUP_HINT = (
    "启动方法：`bash ~/searxng-data/start.sh`；或手动 `cd ~/SearXNG-master && "
    'SEARXNG_SETTINGS_PATH="$HOME/searxng-data/settings.yml" python3 -m searx.webapp`。'
    "也可用环境变量 SOULMATE_SEARXNG_URL 指向其他 SearXNG 实例。"
)

# opener 契约：(url, timeout) -> (ok, body, err)；测试注入假 opener 零网络
Opener = Callable[[str, float], Tuple[bool, str, str]]


@dataclass
class BackendAttempt:
    backend: str
    ok: bool          # 该后端是否被成功"触达"（不代表有结果）
    detail: str
    trusted_empty: bool = False  # 仅"结构化响应确认 0 结果"（searx JSON results:[]）可信为空


@dataclass
class SearchOutcome:
    status: str                          # "ok" | "empty" | "inconclusive" | "unavailable" | "invalid_query"
    text: str                            # 工具结果文本（两个执行点直接使用）
    attempts: List[BackendAttempt] = field(default_factory=list)
    hit_backend: Optional[str] = None


def _curl_opener(url: str, timeout: float) -> Tuple[bool, str, str]:
    """默认 opener：curl -s4（强制 IPv4——技能 pitfall：IPv6 HTTPS 常超时）。"""
    try:
        proc = subprocess.run(
            ["curl", "-s4", "--max-time", str(max(1, int(timeout))), url],
            capture_output=True, text=True, errors="replace", timeout=timeout + 5,
        )
    except subprocess.TimeoutExpired:
        return False, "", "请求超时"
    except Exception as e:  # opener 自身不冒泡——一切失败都走显式降级路径
        return False, "", f"请求异常: {e}"
    if proc.returncode != 0:
        return False, "", (proc.stderr or f"curl exit {proc.returncode}").strip()[:200]
    return True, proc.stdout, ""


def parse_bing_html(html: str, limit: int) -> List[dict]:
    """Bing HTML 结果解析（unified-search 技能 scripts/parse_bing.py 同款规则）。"""
    results: List[dict] = []
    blocks = re.findall(r'class="b_algo"(.*?)</li>', html, re.S)
    for b in blocks[: max(1, limit)]:
        title_m = re.search(r'<a[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>', b, re.S)
        if not title_m:
            continue
        snippet_m = re.search(r'<p[^>]*>(.*?)</p>', b, re.S)
        results.append({
            "title": re.sub(r'<[^>]+>', '', title_m.group(2)).strip(),
            "url": title_m.group(1),
            "content": re.sub(r'<[^>]+>', '', snippet_m.group(1)).strip() if snippet_m else "",
        })
    return results


def _format_results(results: List[dict], limit: int) -> str:
    lines: List[str] = []
    for r in results[: max(1, limit)]:
        lines.append(f"- {r.get('title', '')}: {r.get('url', '')}")
        lines.append(f"  {str(r.get('content', ''))[:100]}")
    return "\n".join(lines)


class SearchBackends:
    """后端链探测 + 降级搜索。opener 可注入（测试零网络）。"""

    def __init__(
        self,
        searx_url: Optional[str] = None,
        opener: Optional[Opener] = None,
        timeout: float = 15.0,
        bing_enabled: Optional[bool] = None,
    ):
        self.searx_url = (
            searx_url
            or os.environ.get("SOULMATE_SEARXNG_URL")
            or DEFAULT_SEARXNG_URL
        ).rstrip("/")
        self._opener = opener or _curl_opener
        self.timeout = timeout
        if bing_enabled is None:
            bing_enabled = os.environ.get("SOULMATE_SEARCH_BING_FALLBACK", "1") != "0"
        self.bing_enabled = bing_enabled

    # ---- 探测（8.4"后端可用性探测"）----
    def probe(self) -> List[BackendAttempt]:
        """只探连通性不取结果（searxng 用 q=probe 极小请求，bing 只看响应体非空）。"""
        attempts: List[BackendAttempt] = []
        ok, body, err = self._opener(
            f"{self.searx_url}/search?q=probe&format=json&pageno=1", self.timeout)
        if ok:
            attempts.append(BackendAttempt("searxng", True, "可达"))
        else:
            attempts.append(BackendAttempt("searxng", False, f"不可达: {err or '连接失败'}"))
        if self.bing_enabled:
            ok, body, err = self._opener(
                f"https://www.bing.com/search?q={quote('probe')}", self.timeout)
            if ok and body.strip():
                attempts.append(BackendAttempt("bing", True, "可达"))
            else:
                attempts.append(BackendAttempt("bing", False, f"不可达: {err or '空响应'}"))
        return attempts

    # ---- 搜索（探测 + 降级 + 三态合成）----
    def search(self, query: str, limit: int = 5) -> SearchOutcome:
        q = (query or "").strip()
        if not q:
            return SearchOutcome(
                status="invalid_query",
                text="搜索失败: query 为空（请提供搜索关键词）",
            )
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = 5
        limit = min(max(limit, 1), 20)

        attempts: List[BackendAttempt] = []

        # 后端1：SearXNG JSON API
        searx_url = f"{self.searx_url}/search?q={quote(q)}&format=json&pageno=1"
        ok, body, err = self._opener(searx_url, self.timeout)
        if not ok:
            attempts.append(BackendAttempt("searxng", False, f"不可达: {err or '连接失败'}"))
        else:
            results = self._parse_searx(body, attempts)
            if results:
                return self._ok(results, limit, "searxng", attempts)
            # 可达但 0 结果——继续走兜底后再定"空"，禁单后端假零

        # 后端2：Bing HTML 兜底（降级路径）
        if self.bing_enabled:
            bing_url = f"https://www.bing.com/search?q={quote(q)}"
            ok, body, err = self._opener(bing_url, self.timeout)
            if not ok:
                attempts.append(BackendAttempt("bing", False, f"不可达: {err or '连接失败'}"))
            elif not (body or "").strip():
                attempts.append(BackendAttempt("bing", False, "空响应"))
            else:
                results = parse_bing_html(body, limit)
                if results:
                    return self._ok(results, limit, "bing", attempts)
                # HTML 0 块=不可信空（反爬拦截与真无结果不可区分），禁计 trusted_empty
                attempts.append(BackendAttempt(
                    "bing", True, "可达但 0 块（疑似反爬拦截）"))
        else:
            attempts.append(BackendAttempt("bing", False, "已禁用(SOULMATE_SEARCH_BING_FALLBACK=0)"))

        # 合成：可信空 / 不可信空 / 全不可达 严格区分，禁静默归零
        if any(a.ok and a.trusted_empty for a in attempts):
            return SearchOutcome(
                status="empty",
                text=self._attempts_text(
                    "无搜索结果（后端可用，非故障；可换关键词重试）", attempts),
                attempts=attempts,
            )
        if any(a.ok for a in attempts):
            return SearchOutcome(
                status="inconclusive",
                text=self._attempts_text(
                    "⚠️ 未获得搜索结果（有后端响应但结果不可信，不足以断言\"无资料\"——"
                    "疑似反爬拦截，建议启动 SearXNG 多引擎聚合）",
                    attempts,
                    hint=STARTUP_HINT,
                ),
                attempts=attempts,
            )
        return SearchOutcome(
            status="unavailable",
            text=self._attempts_text(
                "⚠️ 搜索后端不可用（已探测全部后端均失败；调研请勿据此下\"无资料\"结论）",
                attempts,
                hint=STARTUP_HINT,
            ),
            attempts=attempts,
        )

    # ---- 内部 ----
    def _parse_searx(self, body: str, attempts: List[BackendAttempt]) -> List[dict]:
        if not (body or "").strip():
            attempts.append(BackendAttempt("searxng", False, "空响应"))
            return []
        try:
            data = json.loads(body)
        except (ValueError, TypeError):
            attempts.append(BackendAttempt(
                "searxng", False,
                "响应异常: 非JSON（HTTP 403 常见于 settings.yml formats 未含 json）"))
            return []
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            attempts.append(BackendAttempt("searxng", False, "响应异常: 缺 results 字段"))
            return []
        results = [r for r in data["results"] if isinstance(r, dict)]
        if results:
            attempts.append(BackendAttempt("searxng", True, f"命中 {len(results)} 条"))
        else:
            # 结构化响应确认 0 结果 = 可信为空（empty 态的唯一合法证据来源）
            attempts.append(BackendAttempt("searxng", True, "可达但 0 结果",
                                           trusted_empty=True))
        return results

    def _ok(self, results: List[dict], limit: int, hit: str,
            attempts: List[BackendAttempt]) -> SearchOutcome:
        body = _format_results(results, limit)
        if hit != "searxng":
            body = f"（SearXNG 未命中，已降级使用 {hit} 兜底）\n{body}"
        return SearchOutcome(
            status="ok", text=body, attempts=attempts, hit_backend=hit)

    @staticmethod
    def _attempts_text(head: str, attempts: List[BackendAttempt],
                       hint: str = "") -> str:
        lines = [head, "后端探测:"]
        for a in attempts:
            lines.append(f"- {a.backend}: {'✅' if a.ok else '❌'} {a.detail}")
        if hint:
            lines.append(hint)
        return "\n".join(lines)


def run_search(query: str, limit: int = 5,
               backends: Optional[SearchBackends] = None) -> str:
    """两个执行点共用的工具入口：永不抛异常，一切失败显式成文。"""
    try:
        return (backends or SearchBackends()).search(query, limit).text
    except Exception as e:  # 防御：连合成都炸时也要显式成文，禁静默
        return f"搜索失败: {e}"

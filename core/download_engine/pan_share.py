"""pan_share.py — 网盘分享链解析（pan_share 任务类型支撑，全 Python）。

用户自有的百度网盘分享链接 + 提取码 + 用户自配 Cookie → 真实下载直链（dlink）
→ HTTP 引擎下载。扩展点即 models.classify_source / TaskType.PAN_SHARE / engine.add。

**边界如实（2026-11-16 用户硬约束）**：不破解会员限速 / 不打码 / 不绕风控——
非会员下载限速（约 100KB/s）是百度的付费服务墙，如实呈现不绕过；风控/验证码
触发即报错待人工处理。Cookie 是用户自有账号凭据，仅走官方网页版既有接口。

错误细分（PanShareError.code，直通 REST {"error":{"code",...}} §2 细分码纪律）：
  pan_link_invalid        链接失效（分享取消/不存在/被屏蔽）
  pan_extract_code_wrong  提取码错误
  pan_login_required      需登录（Cookie 未配/失效，或跳转 passport）
  pan_speed_limited       被限速（显式限速/频繁报错——如实报，不绕过）
  pan_risk_control        风控/验证码（不打码不绕过，需人工处理）
  pan_resolve_failed      其它解析失败（细分报因在 message，绝不造直链）

解析三步（errno/文案 → 细分码集中在 classify_pan_response，真机联调只需调这一处）：
  ① GET  /share/init?surl=          初始化（登录墙/失效页预判）
  ② POST /share/verify?surl=        提取码校验 → randsk → BDCLND Cookie
  ③ GET  /share/link?surl=          分享页内嵌 JSON（shareid/uk/sign/timestamp/fs_id）
  ④ POST /api/sharedownload?sign=   取 dlink（fid_list 单文件）
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlsplit

import httpx

PAN_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# ── 细分错误码 ────────────────────────────────────────────────────────────────
PAN_LINK_INVALID = "pan_link_invalid"
PAN_EXTRACT_CODE_WRONG = "pan_extract_code_wrong"
PAN_LOGIN_REQUIRED = "pan_login_required"
PAN_SPEED_LIMITED = "pan_speed_limited"
PAN_RISK_CONTROL = "pan_risk_control"
PAN_RESOLVE_FAILED = "pan_resolve_failed"

_PWD_RE = re.compile(r"(?:提取码|密码|pwd)[：:\s]*([0-9a-zA-Z]{4})", re.I)
# /s/1XXXX 的分享 id 带前缀 1（站内惯例），verify?surl= 不带——两形态分开捕获
_SURL_S_RE = re.compile(r"pan\.baidu\.com/s/(1?[0-9A-Za-z_-]{5,})", re.I)
_SURL_INIT_RE = re.compile(r"pan\.baidu\.com/share/init\?surl=([0-9A-Za-z_-]{5,})", re.I)
_MSET_RE = re.compile(r"locals\.mset\((\{.*?\})\);", re.S)


class PanShareError(Exception):
    """带细分 code 的网盘解析错误（code 见模块常量）。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class ShareRef:
    surl: str  # 分享短链标识（/s/ 后的部分）
    pwd: Optional[str] = None  # 链接里 ?pwd= 或文案里 提取码：xxxx


@dataclass
class ResolvedShare:
    filename: str
    size: int
    dlink: str  # 真实下载直链（有时效，续传前应重新解析）
    cookie: str  # dlink 下载所需 Cookie（原 Cookie + BDCLND）
    user_agent: str = PAN_UA
    headers: Dict[str, str] = field(default_factory=dict)

    def download_options(self) -> Dict[str, Any]:
        """交给 http_engine 的下载 headers（Cookie/UA/Referer）。"""
        opts: Dict[str, Any] = {
            "cookie": self.cookie,
            "user_agent": self.user_agent,
            "referer": "https://pan.baidu.com/",
        }
        opts.update(self.headers)
        return opts


# ── 纯函数（单测覆盖） ────────────────────────────────────────────────────────


def parse_share(source: str) -> ShareRef:
    """分享链接 → (surl, pwd)。支持 /s/<surl>?pwd=xxxx、/share/init?surl=<surl>、
    正文附带「提取码：xxxx」。解析失败抛 ValueError。"""
    s = (source or "").strip()
    if not s:
        raise ValueError("分享链接不能为空")
    mi = _SURL_INIT_RE.search(s)
    ms = _SURL_S_RE.search(s)
    if mi:
        surl = mi.group(1)
    elif ms:
        token = ms.group(1)
        surl = token[1:] if token.startswith("1") else token
    else:
        # 容错：裸 surl（站内分享标识）
        bare = re.fullmatch(r"[0-9A-Za-z_-]{5,}", s)
        if not bare:
            raise ValueError(f"不是百度网盘分享链接（pan.baidu.com/s/...）: {source}")
        surl = s
    pwd = None
    try:
        pwd = (parse_qs(urlsplit(s).query).get("pwd") or [None])[0]
    except ValueError:
        pass
    if not pwd:
        pm = _PWD_RE.search(s)
        pwd = pm.group(1) if pm else None
    return ShareRef(surl=surl, pwd=pwd)


def classify_pan_response(errno: Any, msg: str) -> Optional[str]:
    """百度接口 errno/文案 → 细分错误码（None=成功）。

    映射表集中在此（真机联调若 errno 语义有出入只改这里）：
    先按中文文案细分（百度文案稳定），再按 errno 兜底。
    """
    msg = str(msg or "").strip()
    try:
        errno = int(errno or 0)
    except (TypeError, ValueError):
        errno = 0
    if errno == 0 and not any(
        k in msg for k in ("错误", "失败", "取消", "不存在", "登录", "验证", "限速", "频繁", "超限")
    ):
        return None
    # 提取码错误
    if any(k in msg for k in ("提取码", "密码错误", "密码不正确", "password")) or errno == -9:
        return PAN_EXTRACT_CODE_WRONG
    # 需登录（Cookie 缺失/失效/跳转 passport）
    if any(k in msg for k in ("登录", "登陆", "passport", "cookie")) or errno in (-6, -12, 110):
        return PAN_LOGIN_REQUIRED
    # 链接失效
    if any(k in msg for k in ("取消", "不存在", "失效", "侵权", "屏蔽", "违规", "清理", "删除")) \
            or errno == -21:
        return PAN_LINK_INVALID
    # 风控/验证码（不打码不绕过）
    if any(k in msg for k in ("验证码", "风控", "安全", "异常", "设备")):
        return PAN_RISK_CONTROL
    # 被限速/频繁
    if any(k in msg for k in ("限速", "频繁", "超限", "busy")) or errno == 112:
        return PAN_SPEED_LIMITED
    return PAN_RESOLVE_FAILED if errno else None


def parse_share_page(html: str) -> Dict[str, Any]:
    """分享页 HTML → {shareid, uk, sign, timestamp, bdstoken, file_list}。

    页面内嵌 locals.mset({...}) JSON 优先；结构变化时退回逐键正则；
    两路都拿不到关键字段 → 返回 {}（调用方报 pan_resolve_failed，绝不猜值）。
    """
    out: Dict[str, Any] = {}
    m = _MSET_RE.search(html or "")
    if m:
        try:
            data = json.loads(m.group(1))
            if isinstance(data, dict):
                out.update({k: data[k] for k in data if k != "file_list"})
                fl = data.get("file_list")
                if isinstance(fl, dict):
                    fl = fl.get("list", [])
                if isinstance(fl, list):
                    out["file_list"] = fl
        except ValueError:
            pass
    for key, pat in (
        ("shareid", r'"shareid"\s*:\s*"?(\d+)"?'),
        ("uk", r'"uk"\s*:\s*"?(\d+)"?'),
        ("sign", r'"sign"\s*:\s*"([^"]+)"'),
        ("timestamp", r'"timestamp"\s*:\s*"?(\d+)"?'),
        ("bdstoken", r'"bdstoken"\s*:\s*"([^"]+)"'),
    ):
        if not out.get(key):
            km = re.search(pat, html or "")
            if km:
                out[key] = km.group(1)
    if "file_list" not in out:
        flm = re.search(r'"file_list"\s*:\s*(\{.*?\})\s*[,}]', html or "", re.S)
        if flm:
            try:
                fl = json.loads(flm.group(1))
                out["file_list"] = fl.get("list", []) if isinstance(fl, dict) else []
            except ValueError:
                pass
    return out


def share_page_error(html: str) -> Optional[str]:
    """分享页文案 → 细分错误码（None=页面正常）。"""
    text = html or ""
    for code, marks in (
        (PAN_LINK_INVALID, ("分享的文件已经被取消", "此链接分享内容可能因为涉及侵权", "链接不存在", "分享已失效", "来晚了")),
        (PAN_LOGIN_REQUIRED, ("请先登录", "登录百度帐号", "passport.baidu.com")),
        (PAN_RISK_CONTROL, ("验证码", "安全验证", "系统检测")),
    ):
        if any(k in text for k in marks):
            return code
    return None


# ── 解析器（httpx 异步客户端可注入，fixture 单测用 MockTransport） ────────────


class BaiduPanResolver:
    def __init__(self, client: Optional[httpx.AsyncClient] = None, timeout: float = 20.0):
        self._client = client
        self._timeout = timeout

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                follow_redirects=False,  # 登录跳转要看得见（判登录墙）
                timeout=httpx.Timeout(self._timeout),
                headers={"User-Agent": PAN_UA},
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    async def resolve(
        self,
        source: str,
        extract_code: Optional[str] = None,
        cookie: str = "",
    ) -> ResolvedShare:
        """分享链接 + 提取码 + 用户 Cookie → ResolvedShare（含 dlink）。

        失败一律抛 PanShareError（细分 code），绝不返回臆造直链。
        """
        try:
            ref = parse_share(source)
        except ValueError as e:
            raise PanShareError(PAN_LINK_INVALID, str(e)) from e
        pwd = (extract_code or "").strip() or ref.pwd or ""
        cookie = (cookie or "").strip()
        if not cookie:
            raise PanShareError(
                PAN_LOGIN_REQUIRED,
                "需登录：未配置百度网盘 Cookie（engine config pan_cookie / "
                "env OPENMATE_PAN_COOKIE / 任务 options.pan_cookie，用户自有账号）",
            )
        client = self._get_client()
        base_headers = {
            "User-Agent": PAN_UA,
            "Cookie": cookie,
            "Referer": "https://pan.baidu.com/",
        }

        # ① 分享初始化页：登录墙 / 失效页预判
        init = await client.get(
            f"https://pan.baidu.com/share/init?surl={ref.surl}", headers=base_headers
        )
        page_err = share_page_error(init.text)
        if page_err:
            raise PanShareError(page_err, f"分享初始化页报错: {init.text[:120]}")
        if "passport" in str(init.headers.get("location", "")).lower():
            raise PanShareError(PAN_LOGIN_REQUIRED, "分享页跳转 passport（Cookie 失效）")

        # ② 提取码校验 → randsk → BDCLND Cookie
        cookie2 = cookie
        if pwd:
            verify = await client.post(
                f"https://pan.baidu.com/share/verify?surl={ref.surl}"
                f"&t={int(time.time() * 1000)}&channel=chunlei&clienttype=0&web=1",
                data={"pwd": pwd, "vcode": "", "vcode_str": ""},
                headers=base_headers,
            )
            v = _safe_json(verify)
            code = classify_pan_response(v.get("errno", 0), _err_msg(v, verify))
            if code:
                raise PanShareError(code, f"提取码校验失败: {_err_msg(v, verify) or v}")
            randsk = str(v.get("randsk") or _cookie_value(verify, "BDCLND") or "")
            if randsk:
                cookie2 = f"{cookie}; BDCLND={randsk}"

        # ③ 分享页内嵌数据（shareid/uk/sign/timestamp/file_list）
        page_headers = dict(base_headers)
        page_headers["Cookie"] = cookie2
        page = await client.get(
            f"https://pan.baidu.com/share/link?surl={ref.surl}", headers=page_headers
        )
        page_err = share_page_error(page.text)
        if page_err:
            raise PanShareError(page_err, f"分享页报错: {page.text[:120]}")
        info = parse_share_page(page.text)
        need = ("shareid", "uk", "sign", "timestamp")
        missing = [k for k in need if not info.get(k)]
        if missing:
            raise PanShareError(
                PAN_RESOLVE_FAILED,
                f"分享页解析缺字段 {missing}（页面结构变化或分享失效，如实报不猜值）",
            )
        files = [f for f in (info.get("file_list") or []) if isinstance(f, dict) and not f.get("isdir")]
        if not files:
            raise PanShareError(
                PAN_RESOLVE_FAILED,
                "分享为文件夹/空目录，暂只支持单文件分享（不静默下载错文件）",
            )
        if len(files) > 1:
            raise PanShareError(
                PAN_RESOLVE_FAILED,
                f"分享含 {len(files)} 个文件，暂只支持单文件分享（不静默下载错文件）",
            )
        f0 = files[0]

        # ④ 取真实下载直链
        dl = await client.post(
            f"https://pan.baidu.com/api/sharedownload?sign={info['sign']}"
            f"&timestamp={info['timestamp']}&channel=chunlei&clienttype=0&web=1",
            data={
                "encrypt": "0",
                "product": "share",
                "uk": str(info["uk"]),
                "primaryid": str(info["shareid"]),
                "fid_list": json.dumps([f0.get("fs_id")]),
            },
            headers=page_headers,
        )
        j = _safe_json(dl)
        code = classify_pan_response(j.get("errno", 0), _err_msg(j, dl))
        if code:
            raise PanShareError(code, f"直链获取失败: {_err_msg(j, dl) or j}")
        lst = j.get("list") or []
        dlink = str((lst[0].get("dlink") if lst and isinstance(lst[0], dict) else "") or "")
        if not dlink:
            raise PanShareError(PAN_RESOLVE_FAILED, "直链响应无 dlink（如实报，不造直链）")
        return ResolvedShare(
            filename=str(lst[0].get("server_filename") or f0.get("server_filename") or "download"),
            size=int(lst[0].get("size") or f0.get("size") or 0),
            dlink=dlink,
            cookie=cookie2,
        )


# ── 小工具 ────────────────────────────────────────────────────────────────────


def _safe_json(resp: httpx.Response) -> Dict[str, Any]:
    try:
        d = resp.json()
        return d if isinstance(d, dict) else {}
    except ValueError:
        return {}


def _err_msg(d: Dict[str, Any], resp: httpx.Response) -> str:
    for k in ("err_msg", "errmsg", "error_msg", "show_msg", "msg"):
        if d.get(k):
            return str(d[k])
    return resp.text[:120] if not d else ""


def _cookie_value(resp: httpx.Response, name: str) -> Optional[str]:
    for k, v in resp.cookies.items():
        if k.lower() == name.lower():
            return v
    return None

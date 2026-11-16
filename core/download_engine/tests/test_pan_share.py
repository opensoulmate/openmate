"""pan_share 单测：解析纯函数 + 录制响应 fixture 的 resolver 流程 + engine mock 联调。

无真实账号凭据（铁律：不破解会员限速/不打码/不绕风控）——分享链验证用
tests/fixtures/pan_share/ 录制响应 fixture + httpx.MockTransport mock 联调；
真实联调留待用户配 Cookie 后人工验证。
"""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

from download_engine.engine import Engine
from download_engine.models import TaskStatus, TaskType, classify_source
from download_engine.pan_share import (
    PAN_EXTRACT_CODE_WRONG,
    PAN_LINK_INVALID,
    PAN_LOGIN_REQUIRED,
    PAN_RESOLVE_FAILED,
    PAN_RISK_CONTROL,
    PAN_SPEED_LIMITED,
    BaiduPanResolver,
    PanShareError,
    ResolvedShare,
    classify_pan_response,
    parse_share,
    parse_share_page,
    share_page_error,
)

FIXTURES = Path(__file__).parent / "fixtures" / "pan_share"


def fx(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# ── 纯函数：分享链解析 ────────────────────────────────────────────────────────


def test_parse_share_s_url_with_pwd():
    ref = parse_share("https://pan.baidu.com/s/1AbCdEfGhIjKlMn?pwd=ab12")
    assert ref.surl == "AbCdEfGhIjKlMn"
    assert ref.pwd == "ab12"


def test_parse_share_init_form():
    ref = parse_share("https://pan.baidu.com/share/init?surl=XyZ_9876-abc")
    assert ref.surl == "XyZ_9876-abc"
    assert ref.pwd is None


def test_parse_share_extract_code_in_text():
    ref = parse_share("https://pan.baidu.com/s/1QwErTyUiOpAs?pwd=x9z8")
    assert ref.pwd == "x9z8"
    ref2 = parse_share("资源在这 https://pan.baidu.com/s/1QwErTyUiOpAs 提取码：k7m2")
    assert ref2.pwd == "k7m2"


def test_parse_share_bare_surl():
    ref = parse_share("AbCdEfGhIjKlMnOp")
    assert ref.surl == "AbCdEfGhIjKlMnOp"


def test_parse_share_invalid():
    with pytest.raises(ValueError):
        parse_share("https://example.com/not-a-share")
    with pytest.raises(ValueError):
        parse_share("")


def test_classify_source_pan_share():
    assert classify_source("https://pan.baidu.com/s/1AbCdEfGh?pwd=ab12") == TaskType.PAN_SHARE
    assert classify_source("https://pan.baidu.com/share/init?surl=xxxxxxx") == TaskType.PAN_SHARE
    # 直链下载域不是分享链——仍走 HTTP
    assert classify_source("https://d.pcs.baidu.com/file/x.iso") == TaskType.HTTP
    assert classify_source("https://pan.baidu.com/disk/main") == TaskType.HTTP


# ── 纯函数：错误细分 ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "errno,msg,expect",
    [
        (0, "", None),
        (0, "操作成功", None),
        (-9, "", PAN_EXTRACT_CODE_WRONG),
        (0, "提取密码错误，请重新输入", PAN_EXTRACT_CODE_WRONG),
        (0, "请先登录", PAN_LOGIN_REQUIRED),
        (-6, "", PAN_LOGIN_REQUIRED),
        (0, "分享的文件已经被取消了", PAN_LINK_INVALID),
        (-21, "", PAN_LINK_INVALID),
        (0, "请输入验证码", PAN_RISK_CONTROL),
        (112, "", PAN_SPEED_LIMITED),
        (0, "请求过于频繁，请稍后再试", PAN_SPEED_LIMITED),
        (-5, "未知错误", PAN_RESOLVE_FAILED),
    ],
)
def test_classify_pan_response(errno, msg, expect):
    assert classify_pan_response(errno, msg) == expect


# ── 纯函数：分享页解析（录制页 fixture） ─────────────────────────────────────


def test_parse_share_page_recorded():
    info = parse_share_page(fx("share_page_ok.html"))
    assert info["shareid"] == 123456789
    assert info["uk"] == 9876543210
    assert info["sign"] == "RECORDEDSIGN01234"
    assert info["timestamp"] == 1700000000
    assert info["file_list"][0]["server_filename"] == "ubuntu-22.04-live-server-amd64.iso"


def test_parse_share_page_no_guess():
    # 页面结构变化/无数据 → 空 dict（调用方报 pan_resolve_failed，绝不猜值）
    assert parse_share_page("<html>nothing here</html>") == {}


def test_share_page_error_markers():
    assert share_page_error(fx("share_page_cancelled.html")) == PAN_LINK_INVALID
    assert share_page_error(fx("share_page_ok.html")) is None


# ── resolver：录制响应 fixture + MockTransport ───────────────────────────────


class PanMock:
    """录制响应 fixture 驱动的 pan 接口 mock（记录请求供断言）。"""

    def __init__(
        self,
        verify="verify_ok.json",
        page="share_page_ok.html",
        dl="sharedownload_ok.json",
        init_location="",
        verify_raw=None,
        dl_raw=None,
        page_raw=None,
    ):
        self.verify = verify
        self.page = page
        self.dl = dl
        self.init_location = init_location
        self.verify_raw = verify_raw
        self.dl_raw = dl_raw
        self.page_raw = page_raw
        self.requests = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(
            {"method": request.method, "path": request.url.path,
             "body": request.content.decode("utf-8", "replace")}
        )
        path = request.url.path
        if path == "/share/init":
            if self.init_location:
                return httpx.Response(302, headers={"location": self.init_location})
            return httpx.Response(200, text="<html>ok</html>")
        if path == "/share/verify":
            raw = self.verify_raw if self.verify_raw is not None else fx(self.verify)
            return httpx.Response(200, text=raw)
        if path == "/share/link":
            raw = self.page_raw if self.page_raw is not None else fx(self.page)
            return httpx.Response(200, text=raw)
        if path == "/api/sharedownload":
            raw = self.dl_raw if self.dl_raw is not None else fx(self.dl)
            return httpx.Response(200, text=raw)
        return httpx.Response(404, text="not found")


def _resolver(mock: PanMock) -> BaiduPanResolver:
    client = httpx.AsyncClient(transport=httpx.MockTransport(mock.handler))
    return BaiduPanResolver(client=client)


def _resolve(mock: PanMock, **kw):
    async def run():
        r = _resolver(mock)
        try:
            return await r.resolve(kw.pop("source", "https://pan.baidu.com/s/1AbCdEfGh?pwd=ab12"), **kw)
        finally:
            await r.aclose()

    return asyncio.run(run())


def test_resolve_success_recorded_flow():
    mock = PanMock()
    res = _resolve(mock, cookie="BDUSS=rec; STOKEN=rec")
    assert res.dlink == "https://d.pcs.baidu.com/file/recorded-dlink?fid=1122334455&rt=pr"
    assert res.filename == "ubuntu-22.04-live-server-amd64.iso"
    assert res.size == 3221225472
    # randsk → BDCLND Cookie（下载要带）
    assert "BDCLND=RECORDED_RANDSK_9f3a" in res.cookie
    # 流程：init → verify(带提取码) → 分享页 → sharedownload
    paths = [r["path"] for r in mock.requests]
    assert paths == ["/share/init", "/share/verify", "/share/link", "/api/sharedownload"]
    verify = mock.requests[1]
    assert "pwd=ab12" in verify["body"]


def test_resolve_wrong_extract_code():
    mock = PanMock(verify="verify_wrong_pwd.json")
    with pytest.raises(PanShareError) as ei:
        _resolve(mock, cookie="BDUSS=rec")
    assert ei.value.code == PAN_EXTRACT_CODE_WRONG


def test_resolve_link_cancelled():
    mock = PanMock(page="share_page_cancelled.html")
    with pytest.raises(PanShareError) as ei:
        _resolve(mock, cookie="BDUSS=rec")
    assert ei.value.code == PAN_LINK_INVALID


def test_resolve_without_cookie_requires_login():
    mock = PanMock()
    with pytest.raises(PanShareError) as ei:
        _resolve(mock, cookie="")
    assert ei.value.code == PAN_LOGIN_REQUIRED
    assert mock.requests == []  # 无凭据不出站


def test_resolve_passport_redirect_requires_login():
    mock = PanMock(init_location="https://passport.baidu.com/v2/?login")
    with pytest.raises(PanShareError) as ei:
        _resolve(mock, cookie="BDUSS=expired")
    assert ei.value.code == PAN_LOGIN_REQUIRED


def test_resolve_speed_limited_not_bypassed():
    # 被限速=如实报错（不破解会员限速）
    mock = PanMock(dl="sharedownload_limited.json")
    with pytest.raises(PanShareError) as ei:
        _resolve(mock, cookie="BDUSS=rec")
    assert ei.value.code == PAN_SPEED_LIMITED


def test_resolve_risk_control_never_captchas():
    # 风控/验证码=报错待人工（不打码不绕风控）
    raw = json.dumps({"errno": -3, "err_msg": "请输入验证码"})
    mock = PanMock(verify_raw=raw)
    with pytest.raises(PanShareError) as ei:
        _resolve(mock, cookie="BDUSS=rec")
    assert ei.value.code == PAN_RISK_CONTROL


def test_resolve_folder_share_rejected():
    # 文件夹分享：不静默下载错文件，明确报因
    mock = PanMock(page="share_page_dir.html")
    with pytest.raises(PanShareError) as ei:
        _resolve(mock, cookie="BDUSS=rec")
    assert ei.value.code == PAN_RESOLVE_FAILED
    assert "文件夹" in str(ei.value)


def test_resolve_missing_page_fields_never_guesses():
    mock = PanMock(page_raw="<html>structure changed</html>")
    with pytest.raises(PanShareError) as ei:
        _resolve(mock, cookie="BDUSS=rec")
    assert ei.value.code == PAN_RESOLVE_FAILED


# ── engine mock 联调：pan_share 任务类型 → HTTP 引擎真实下载 ─────────────────

CONTENT = b"openmate-pan-share-test-payload" * 65536  # 约 2MB


class _FileHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(CONTENT)))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        self.wfile.write(CONTENT)

    def log_message(self, *args):  # noqa: ANN002
        pass


@pytest.fixture()
def file_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FileHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


class FakeResolver:
    """engine 联调用：按序返回直链（真实下载走本地文件服务器）。"""

    def __init__(self, dlinks):
        self.dlinks = list(dlinks)
        self.calls = []

    async def resolve(self, source, extract_code=None, cookie=""):
        self.calls.append({"source": source, "extract_code": extract_code, "cookie": cookie})
        return ResolvedShare(
            filename="ubuntu-22.04-live-server-amd64.iso",
            size=len(CONTENT),
            dlink=self.dlinks.pop(0),
            cookie="BDUSS=rec; BDCLND=RECORDED_RANDSK_9f3a",
        )

    async def aclose(self):
        return None


def _wait_done(eng, timeout=10.0):
    async def poll():
        for _ in range(int(timeout / 0.05)):
            rows = eng.list()
            if rows and rows[0]["status"] in ("done", "error"):
                return rows[0]
            await asyncio.sleep(0.05)
        return eng.list()[0]

    return poll()


def test_engine_pan_share_end_to_end(tmp_path, file_server):
    """pan_share 任务：分享链→解析直链→HTTP 引擎下载（mock 联调）。"""

    async def scenario():
        eng = Engine(str(tmp_path / "state"), default_dir=str(tmp_path / "dl"))
        eng.pan = FakeResolver([f"{file_server}/file.iso"])
        try:
            task = await eng.add(
                "https://pan.baidu.com/s/1AbCdEfGh?pwd=ab12",
                dir=str(tmp_path / "dl"),
                options={"extract_code": "ab12", "pan_cookie": "BDUSS=rec"},
            )
            # 契约：url 保留用户原始输入；类型 pan_share
            assert task["url"] == "https://pan.baidu.com/s/1AbCdEfGh?pwd=ab12"
            assert task["type"] == "pan_share"
            # 提取码与用户 Cookie 传进了解析器
            call = eng.pan.calls[0]
            assert call["extract_code"] == "ab12"
            assert call["cookie"] == "BDUSS=rec"
            final = await _wait_done(eng)
            assert final["status"] == "done", final.get("error")
            assert final["filename"] == "ubuntu-22.04-live-server-amd64.iso"
            assert final["totalBytes"] == len(CONTENT)
            dest = tmp_path / "dl" / "ubuntu-22.04-live-server-amd64.iso"
            assert dest.read_bytes() == CONTENT
        finally:
            await eng.shutdown()

    asyncio.run(scenario())


def test_engine_pan_resume_reresolves_expired_dlink(tmp_path, file_server):
    """续传前重新解析直链（dlink 有时效，旧直链不复用）。"""

    async def scenario():
        eng = Engine(str(tmp_path / "state"), default_dir=str(tmp_path / "dl"))
        eng.pan = FakeResolver([f"{file_server}/old.iso", f"{file_server}/new.iso"])
        try:
            task = await eng.add(
                "https://pan.baidu.com/s/1AbCdEfGh",
                dir=str(tmp_path / "dl"),
                options={"pan_cookie": "BDUSS=rec"},
            )
            await _wait_done(eng)
            rec = eng.http.get(task["id"])
            assert rec.download_url == f"{file_server}/old.iso"
            # 模拟：暂停后直链过期，续传必须换新链
            rec.task.status = TaskStatus.PAUSED
            rec.download_url = "https://d.pcs.baidu.com/file/expired-dlink"
            await eng.resume(task["id"])
            assert rec.download_url == f"{file_server}/new.iso"
            final = await _wait_done(eng)
            assert final["status"] == "done", final.get("error")
            assert len(eng.pan.calls) == 2  # add 一次 + resume 重新解析一次
        finally:
            await eng.shutdown()

    asyncio.run(scenario())

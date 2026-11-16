# -*- coding: utf-8 -*-
"""下载中心工具组网盘（pan）用法测试。

覆盖：网盘候选呈现（share_url/提取码）、download_add 透传 extract_code
（含 pwd 别名）、引导文案含网盘链路、空 url 报因含网盘分享链。
确定性 REST 拦截（FakeTools），不起后端。
"""
import asyncio

from agent import download_tools as dt
from agent.download_tools import DownloadTools


def _run(coro):
    return asyncio.run(coro)


class FakeTools(DownloadTools):
    """拦截 REST：记录请求，回放指定响应。"""

    def __init__(self, response):
        super().__init__(api_base="http://127.0.0.1:1", timeout=5.0)
        self.response = response
        self.calls = []

    async def _request(self, method, path, *, params=None, body=None):
        self.calls.append({"method": method, "path": path, "params": params, "body": body})
        return self.response, ""


def test_search_item_pan_shape():
    item = {
        "title": "某资源合集",
        "size": 1610612736,
        "source": "my-pan",
        "share_url": "https://pan.baidu.com/s/1AbCdEf?pwd=a1b2",
        "extract_code": "a1b2",
    }
    line = dt._fmt_search_item(item)
    assert "某资源合集" in line
    assert "网盘分享: https://pan.baidu.com/s/1AbCdEf?pwd=a1b2" in line
    assert "提取码 a1b2" in line
    assert "download_add" in line and "extract_code" in line


def test_search_item_pan_without_code_omits_field():
    line = dt._fmt_search_item(
        {"title": "无码", "size": 0, "source": "my-pan",
         "share_url": "https://pan.baidu.com/s/1XxXxX"}
    )
    assert "网盘分享" in line
    assert "| 提取码 " not in line  # 无码不渲染提取码字段（引导文案除外）


def test_torrent_search_pan_results_rendered_with_guidance():
    resp = {
        "results": [
            {"title": "A", "size": 1024, "source": "my-pan",
             "share_url": "https://pan.baidu.com/s/1AaAaA", "extract_code": "c1c2"},
        ],
        "errors": [],
        "total": 1,
    }
    tools = FakeTools(resp)
    out = _run(tools.torrent_search({"query": "A"}))
    assert "网盘分享" in out and "提取码 c1c2" in out
    # 引导：网盘候选 → download_add(url=分享链, extract_code=提取码)
    assert "网盘候选则 url=分享链并带 extract_code=提取码" in out


def test_download_add_passes_extract_code():
    resp = {"task": {"id": "http-1", "status": "pending", "filename": "x.iso"}}
    tools = FakeTools(resp)
    out = _run(tools.download_add(
        {"url": "https://pan.baidu.com/s/1AaAaA", "extract_code": "c1c2"}))
    body = tools.calls[0]["body"]
    assert body["url"] == "https://pan.baidu.com/s/1AaAaA"
    assert body["extract_code"] == "c1c2"
    assert "✅ 下载任务已添加" in out


def test_download_add_pwd_alias_accepted():
    resp = {"task": {"id": "http-2", "status": "pending", "filename": "y.iso"}}
    tools = FakeTools(resp)
    _run(tools.download_add({"url": "https://pan.baidu.com/s/1BbBbB", "pwd": "d3d4"}))
    assert tools.calls[0]["body"]["extract_code"] == "d3d4"


def test_download_add_without_code_sends_no_field():
    resp = {"task": {"id": "http-3", "status": "pending", "filename": "z.iso"}}
    tools = FakeTools(resp)
    _run(tools.download_add({"url": "https://example.com/z.iso"}))
    assert "extract_code" not in tools.calls[0]["body"]


def test_download_add_empty_url_error_mentions_pan_share():
    tools = FakeTools({"task": {}})
    out = _run(tools.download_add({"url": ""}))
    assert out.startswith("错误: url 参数不能为空")
    assert "网盘分享链" in out

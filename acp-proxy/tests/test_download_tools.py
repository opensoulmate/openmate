# -*- coding: utf-8 -*-
"""下载中心工具组（SoulMate 触发面）测试。

工具形状=docs/download-mcp-tools.json 四件套，REST=x-endpoint 契约。
覆盖：torrent_search 候选形状（标题/大小/做种数/源）+ 未知值样本 + errors
逐源降级如实回报、download_add（remote 缺省/local 透传/dest/空 url/非法
target/失败细分报因）、download_task_status（metadata 主面/回落任务表按 id
过滤/未知任务报因/空 task_id）、download_task_list（status+origin 客户端兜底
过滤/去重 facade 重复行/空表/展示上限自报/非法参数）、完整对话流（搜索→
呈现候选→加任务→进度回报，走真实 _download_dispatch）、后端不可达细分报因、
DownloadTask camelCase/蛇形双认、接线护栏 + lazy-init + 降级只读白名单。
"""
import asyncio
import re

import pytest

from agent.download_tools import DownloadTools
from tests.mock_download_backend import MockDownloadBackend


@pytest.fixture()
def backend():
    b = MockDownloadBackend()
    b.start()
    yield b
    b.stop()


@pytest.fixture()
def tools(backend):
    return DownloadTools(api_base=backend.base_url, timeout=5.0)


def _run(coro):
    return asyncio.run(coro)


# ── torrent_search ─────────────────────────────────────────────────

def test_search_results_plan_shape(tools):
    out = _run(tools.torrent_search({"query": "blade runner"}))
    assert "🔍 种子搜索 blade runner" in out
    # 计划书形状：标题/大小/做种数/源
    assert "做种" in out and "源 Internet Archive" in out \
        and "源 Academic Torrents" in out
    assert "大小 未知" in out            # size=0 未知值样本
    assert "做种 ?（源不公开）" in out    # seeders=-1 未知值样本
    # 可入任务链：磁力 + .torrent 直链都呈现
    assert "可入任务: magnet:?xt=urn:btih:" in out
    assert "可入任务: https://example.org/" in out
    # 对话流引导：呈现候选→问哪部→加任务
    assert "有歧义" in out and "download_add" in out


def test_search_empty_query_rejected(tools):
    out = _run(tools.torrent_search({"query": "  "}))
    assert out == "错误: query 参数不能为空"


def test_search_limit_capped(tools):
    out = _run(tools.torrent_search({"query": "demo", "limit": 999}))
    # 5 个候选全部返回（limit 截到契约 100 上限，不炸）
    assert "5 个结果" in out


def test_search_source_errors_surfaced(tools):
    # 契约 errors=[{source,error}]：逐源降级如实回报（query 含 failsource 触发）
    out = _run(tools.torrent_search({"query": "demo failsource"}))
    assert "⚠️ 源降级: Broken RSS: timeout (15s)" in out
    assert "5 个结果" in out  # 降级不拖垮其它源


# ── download_add ───────────────────────────────────────────────────

def test_add_default_remote_and_task_ack(tools):
    out = _run(tools.download_add(
        {"url": "https://example.com/file.bin"}))
    assert "✅ 下载任务已添加: task_id=mock0001" in out
    assert "目标 remote" in out and "状态 downloading" in out
    assert "download_task_status" in out  # 引导后续进度回报


def test_add_target_local_passthrough(tools):
    out = _run(tools.download_add(
        {"url": "magnet:?xt=urn:btih:deadbeef", "target": "local"}))
    assert "目标 local" in out


def test_add_empty_url_rejected(tools):
    out = _run(tools.download_add({"url": ""}))
    assert out.startswith("错误: url 参数不能为空")


def test_add_bad_target_rejected(tools):
    out = _run(tools.download_add({"url": "http://x/y", "target": "nas"}))
    assert out == "错误: target 只支持 local|remote，收到: nas"


def test_add_missing_task_id_honest_error(tools):
    # 后端不回 task_id 时如实报因，不假装成功
    tools._add_path = "/search"  # GET-only 路由收到 POST → 404 → 细分报因
    out = _run(tools.download_add({"url": "http://x/y"}))
    assert out.startswith("错误[HTTP 404]")


# ── download_task_status ───────────────────────────────────────────

def test_status_progress_fields(tools):
    _run(tools.download_add(
        {"url": "https://example.com/big.bin",
         "total_bytes": 1024 * 1024 * 1024, "speed": 256 * 1024}))
    out = _run(tools.download_task_status({"task_id": "mock0001"}))
    assert "📥 mock0001 | downloading" in out
    assert "进度 " in out and "%" in out
    assert "速度 256.0KB/s" in out
    assert "剩余 " in out and "目标 remote" in out


def test_status_unknown_task_explicit_error(tools):
    out = _run(tools.download_task_status({"task_id": "nope"}))
    assert "错误: 任务 nope 不存在" in out
    assert "download_task_list" in out  # 引导查看全部


def test_status_falls_back_to_list_filter(tools):
    # 契约 x-endpoint 第二条：GET /tasks（filter by id）——引擎面 metadata 路由
    # 不可用（native/plugin 任务）时回落任务表按 id 过滤
    _run(tools.download_add({"url": "https://example.com/a.bin"}))
    tools._remote_path = "/nope"  # 模拟引擎面无此路由
    out = _run(tools.download_task_status({"task_id": "mock0001"}))
    assert "📥 mock0001 | downloading" in out and "进度 " in out


def test_status_falls_back_to_results_for_finished_task(tools, backend):
    # 三源兜底第三面：插件链终态行只在 GET /results/{id}（api doc §5）
    _run(tools.download_add({"url": "https://example.com/done.bin"}))
    # mock 专用钩子：直接把任务置为瞬时完成（工具层只转发契约字段，不透传
    # total_bytes/speed 测试项）
    backend._httpd.store.tasks["mock0001"].update(
        {"totalBytes": 1, "total_bytes": 1, "speed": 10 ** 9})
    tools._remote_path = "/nope"
    tools._tasks_path = "/nope2"
    out = _run(tools.download_task_status({"task_id": "mock0001"}))
    assert "📥 mock0001 | done" in out and "进度 100.0%" in out


def test_status_empty_task_id_rejected(tools):
    out = _run(tools.download_task_status({}))
    assert out == "错误: task_id 参数不能为空"


# ── download_task_list ─────────────────────────────────────────────

def test_list_empty(tools):
    out = _run(tools.download_task_list({}))
    assert out == "📥 暂无下载任务"


def test_list_shows_tasks_dedupe_and_client_side_filters(tools):
    # mock 故意忽略 status/origin 参数且 tasks/remote_tasks 重复行（对齐真实
    # facade）：验证按 task_id 去重 + 客户端兜底过滤
    _run(tools.download_add({"url": "https://example.com/a.bin"}))
    _run(tools.download_add({"url": "https://example.com/b.bin"}))
    out = _run(tools.download_task_list({}))
    assert "📥 下载任务 2 个" in out      # 重复行去重后仍是 2 个
    assert "mock0001" in out and "mock0002" in out
    done = _run(tools.download_task_list({"status": "done"}))
    assert done == "📥 暂无下载任务（status=done）"
    downloading = _run(tools.download_task_list({"status": "downloading"}))
    assert "📥 下载任务 2 个（status=downloading）" in downloading
    remote = _run(tools.download_task_list({"origin": "remote"}))
    assert "📥 下载任务 2 个（origin=remote）" in remote
    local = _run(tools.download_task_list({"origin": "local"}))
    assert local == "📥 暂无下载任务（origin=local）"


def test_list_bad_status_rejected(tools):
    out = _run(tools.download_task_list({"status": "finished"}))
    assert out.startswith("错误: status 只支持")


def test_list_bad_origin_rejected(tools):
    out = _run(tools.download_task_list({"origin": "nas"}))
    assert out == "错误: origin 只支持 local|remote，收到: nas"


def test_list_display_cap_self_reported(tools):
    # 契约无 limit 参数：超 50 个展示上限自报，不截断隐瞒
    for i in range(51):
        _run(tools.download_add({"url": f"https://example.com/f{i}.bin"}))
    out = _run(tools.download_task_list({}))
    assert "📥 下载任务 50 个" in out and "共 51 个，展示前 50 个" in out


# ── 完整对话流（自然语言→搜索→加任务→回报进度，真实 dispatch）────

def test_full_conversation_flow_via_real_dispatch(backend):
    from agent.soulmate_agent import SoulMateAgent
    agent = SoulMateAgent.__new__(SoulMateAgent)
    agent._download_tools_inst = DownloadTools(
        api_base=backend.base_url, timeout=5.0)
    sid = "sess-flow"

    # ① 「最新有什么好的电影帮我找一下」→ torrent_search 呈现候选
    r1 = _run(agent._download_dispatch(
        "torrent_search", {"query": "经典科幻电影"}, sid))
    assert "🔍 种子搜索 经典科幻电影" in r1 and "做种" in r1
    # ② 无歧义默认取最合适（第一条候选的磁力链）
    m = re.search(r"可入任务: (magnet:\S+)", r1)
    assert m is not None
    magnet = m.group(1)
    # ③ download_add 加任务
    r2 = _run(agent._download_dispatch(
        "download_add", {"url": magnet, "target": "remote"}, sid))
    assert "✅ 下载任务已添加" in r2
    m = re.search(r"task_id=(\S+?) \|", r2)
    assert m is not None
    task_id = m.group(1)
    # ④ 回报进度：单任务 + 总览
    r3 = _run(agent._download_dispatch(
        "download_task_status", {"task_id": task_id}, sid))
    assert f"📥 {task_id} | downloading" in r3 and "进度 " in r3
    r4 = _run(agent._download_dispatch("download_task_list", {}, sid))
    assert task_id in r4 and "📥 下载任务 1 个" in r4


# ── 显式报因（禁静默）──────────────────────────────────────────────

def test_backend_unreachable_explicit_error():
    tools = DownloadTools(api_base="http://127.0.0.1:1", timeout=1.0)
    for name, args in (("torrent_search", {"query": "x"}),
                       ("download_add", {"url": "http://x/y"}),
                       ("download_task_status", {"task_id": "t"}),
                       ("download_task_list", {})):
        out = _run(tools.run(name, args))
        assert out.startswith("错误: 下载后端不可达"), name
        assert "/search 与 /download" in out, name


def test_unknown_tool_name_rejected(tools):
    out = _run(tools.run("download_delete", {}))
    assert "错误: 未知下载工具: download_delete" in out


def test_status_failure_error_field_surface(tools):
    # 后端任务带 error 字段时如实回报（DownloadTask.error 契约）
    _run(tools.download_add({"url": "https://example.com/x.bin"}))
    task = tools._task_of({"task": {
        "task_id": "mock0001", "status": "error", "filename": "x.bin",
        "progress": 12.0, "error": "network_error: 连接被重置"}})
    from agent.download_tools import _fmt_task
    line = _fmt_task(task)
    assert "error" in line and "network_error: 连接被重置" in line


# ── 形状双认（docs/download-engine-api.md §3）─────────────────────

def test_task_shape_camel_and_snake_aliases():
    from agent.download_tools import _fmt_task
    camel = _fmt_task({
        "id": "t1", "filename": "a.bin", "totalBytes": 1000,
        "downloadedBytes": 500, "progress": 50.0, "speed": 100,
        "eta": 5, "status": "downloading", "origin": "remote",
    })
    snake = _fmt_task({
        "task_id": "t1", "filename": "a.bin", "total_bytes": 1000,
        "downloaded_bytes": 500, "progress_pct": 50.0, "speed": 100,
        "eta": 5, "status": "downloading", "origin": "remote",
    })
    assert camel == snake
    assert "t1 | downloading | a.bin | 进度 50.0%" in camel


def test_task_progress_computed_when_missing():
    from agent.download_tools import _fmt_task
    line = _fmt_task({"task_id": "t2", "filename": "b.bin",
                      "total_bytes": 2000, "downloaded_bytes": 500,
                      "status": "downloading"})
    assert "进度 25.0%" in line


# ── 接线护栏 + dispatch 烟测 ──────────────────────────────────────

def _src() -> str:
    from pathlib import Path
    return (Path(__file__).resolve().parent.parent
            / "agent" / "soulmate_agent.py").read_text(encoding="utf-8")


def test_wiring_source_guards():
    src = _src()
    # 顶部 import
    assert "from agent.download_tools import DownloadTools" in src
    # 四工具定义进 LLM 工具面
    for name in ("torrent_search", "download_add",
                 "download_task_status", "download_task_list"):
        assert f'"name": "{name}"' in src
    assert "builtin_tools.extend(download_tool_defs)" in src
    # 主循环 dispatch + code-mode 批内 dispatch 两处调用 + 共享入口定义
    assert src.count(
        "self._download_dispatch(func_name, func_args, session_id)") == 2
    assert "def _download_dispatch" in src
    # 系统提示工具清单（对话流入口）
    assert "- torrent_search / download_add / download_task_status" in src
    assert "自然语言→搜索→加任务→回报进度完整对话流" in src


def test_dispatch_lazy_init_constructs_download_tools(monkeypatch):
    import agent.soulmate_agent as sa
    made = {}

    class FakeTools:
        def __init__(self, *a, **k):
            made["init"] = True

        async def run(self, name, params, session_id=""):
            made["call"] = (name, params, session_id)
            return f"FAKE:{name}"

    monkeypatch.setattr(sa, "DownloadTools", FakeTools)
    agent = sa.SoulMateAgent.__new__(sa.SoulMateAgent)
    out = _run(agent._download_dispatch(
        "torrent_search", {"query": "q"}, "s-1"))
    assert made.get("init") is True          # lazy-init 真的构造了
    assert out == "FAKE:torrent_search"
    assert made["call"] == ("torrent_search", {"query": "q"}, "s-1")


def test_permission_degraded_readonly_scope():
    from agent.permission_gate import DEGRADED_READONLY_TOOLS
    # 只读三件进降级白名单；download_add 有副作用不进
    assert "torrent_search" in DEGRADED_READONLY_TOOLS
    assert "download_task_status" in DEGRADED_READONLY_TOOLS
    assert "download_task_list" in DEGRADED_READONLY_TOOLS
    assert "download_add" not in DEGRADED_READONLY_TOOLS

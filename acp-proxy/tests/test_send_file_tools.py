# -*- coding: utf-8 -*-
"""路线图 9.1 send_file 文件下发工具测试。

覆盖：MEDIA 末行契约/消息透传/空 path/不存在/目录/越界与软链穿越/0 字节诚实/
相对路径/环境变量根目录覆盖 + 接线护栏（7 点）+ __new__ 真实 dispatch 烟测
（待交付清单登记）+ _merge_pending_send_media 确定性交付兜底（回显去重/
未回显补末行/一回合只交付一次）+ lazy-init 构造。
"""
import os
from pathlib import Path

import pytest

from agent.send_file_tools import SendFileTools


def _mk(tmp_path, name="report.txt", content="hello send_file"):
    f = tmp_path / name
    f.write_text(content, encoding="utf-8")
    return f


# ── 成功路径 ─────────────────────────────────────────────────────────

def test_happy_path_message_and_media_last_line(tmp_path):
    f = _mk(tmp_path)
    out = SendFileTools(allowed_roots=[tmp_path]).run(
        {"path": str(f), "message": "请查收报告"})
    lines = out.splitlines()
    assert "请查收报告" in out
    assert f"📎 文件已准备好: report.txt ({f.stat().st_size} 字节)" in out
    # 输出契约：MEDIA: 保持末行供网关按行提取（周期#39）
    assert lines[-1] == f"MEDIA:{f.resolve()}"


def test_no_message_ok(tmp_path):
    f = _mk(tmp_path)
    out = SendFileTools(allowed_roots=[tmp_path]).run({"path": str(f)})
    assert "📎 文件已准备好: report.txt" in out
    assert out.splitlines()[-1] == f"MEDIA:{f.resolve()}"


def test_zero_byte_honest_size(tmp_path):
    f = _mk(tmp_path, name="empty.bin", content="")
    out = SendFileTools(allowed_roots=[tmp_path]).run({"path": str(f)})
    assert "(0 字节)" in out


def test_relative_path_resolved(tmp_path, monkeypatch):
    f = _mk(tmp_path)
    monkeypatch.chdir(tmp_path)
    out = SendFileTools(allowed_roots=[tmp_path]).run({"path": "report.txt"})
    assert out.splitlines()[-1] == f"MEDIA:{f.resolve()}"


def test_home_default_roots_send_real_file():
    # 默认根目录=用户主目录+/tmp：真实主目录文件可下发（不注入 allowed_roots）
    real = Path.home() / ".bashrc"
    if not real.exists():
        pytest.skip("no ~/.bashrc")
    out = SendFileTools().run({"path": str(real)})
    assert out.splitlines()[-1] == f"MEDIA:{real.resolve()}"


def test_env_roots_override(monkeypatch, tmp_path):
    f = _mk(tmp_path)
    monkeypatch.setenv("SOULMATE_SEND_FILE_ROOTS", str(tmp_path))
    out = SendFileTools().run({"path": str(f)})
    assert out.splitlines()[-1] == f"MEDIA:{f.resolve()}"
    monkeypatch.delenv("SOULMATE_SEND_FILE_ROOTS")
    # 越界形态：同一文件在默认根目录（home+/tmp）之外被拒
    outside = SendFileTools(allowed_roots=[tmp_path / "other"])
    assert "越界拒绝" in outside.run({"path": str(f)})


# ── 显式报因（禁静默）────────────────────────────────────────────────

def test_empty_path_rejected():
    out = SendFileTools().run({"path": ""})
    assert out == "错误: path 参数不能为空"
    assert "MEDIA:" not in out


def test_missing_path_param_rejected():
    assert "path 参数不能为空" in SendFileTools().run({})


def test_nonexistent_explicit_error(tmp_path):
    out = SendFileTools(allowed_roots=[tmp_path]).run(
        {"path": str(tmp_path / "nope.txt")})
    assert "错误: 文件不存在" in out
    assert "MEDIA:" not in out


def test_directory_explicit_error(tmp_path):
    out = SendFileTools(allowed_roots=[tmp_path]).run({"path": str(tmp_path)})
    assert "不支持发送目录" in out
    assert "MEDIA:" not in out


def test_boundary_reject_with_roots_listed(tmp_path):
    f = _mk(tmp_path)
    out = SendFileTools(allowed_roots=[tmp_path / "sandbox"]).run({"path": str(f)})
    assert "越界拒绝" in out
    assert str((tmp_path / "sandbox").resolve()) in out  # 允许根目录列明
    assert "MEDIA:" not in out


def test_symlink_escape_rejected(tmp_path):
    # 软链穿越：链接在根目录内、目标在根目录外 → realpath 收口拒绝
    inside = tmp_path / "in"
    inside.mkdir()
    outside = _mk(tmp_path, name="secret.txt", content="s")
    link = inside / "link.txt"
    link.symlink_to(outside)
    out = SendFileTools(allowed_roots=[inside]).run({"path": str(link)})
    assert "越界拒绝" in out
    assert "MEDIA:" not in out


# ── 接线护栏 + dispatch 烟测 ─────────────────────────────────────────

def _src() -> str:
    return (Path(__file__).resolve().parent.parent
            / "agent" / "soulmate_agent.py").read_text(encoding="utf-8")


def test_wiring_source_guards():
    src = _src()
    # 顶部 import
    assert "from agent.send_file_tools import SendFileTools" in src
    # 工具定义进 LLM 工具面
    assert '"name": "send_file"' in src
    assert "builtin_tools.extend(send_file_tool)" in src
    # 主循环 dispatch + code-mode 批内 dispatch 两处调用 + 共享入口定义
    assert src.count("self._send_file_dispatch(func_args, session_id)") == 2
    assert "def _send_file_dispatch" in src
    # 系统提示工具清单
    assert "- send_file: 文件下发" in src
    # 网关后处理确定性交付兜底接线（可单测的合并方法）
    assert "self._merge_pending_send_media(session_id, full_response)" in src
    assert "def _merge_pending_send_media" in src
    # 回合开始作废上回合残留
    assert "_pending.pop(session_id, None)" in src


def test_real_dispatch_smoke_and_pending_registration(tmp_path):
    from agent.soulmate_agent import SoulMateAgent
    agent = SoulMateAgent.__new__(SoulMateAgent)
    f = _mk(tmp_path, name="dev_hello.py", content="print('hi')")
    agent._send_file_tools_inst = SendFileTools(allowed_roots=[tmp_path])
    out = agent._send_file_dispatch({"path": str(f)}, "sess-1")
    assert out.splitlines()[-1] == f"MEDIA:{f.resolve()}"
    # 成功产出登记进待交付清单（网关收口消费）
    assert agent._pending_send_media["sess-1"] == [str(f.resolve())]
    # 校验失败不登记
    err = agent._send_file_dispatch({"path": ""}, "sess-1")
    assert "path 参数不能为空" in err
    assert agent._pending_send_media["sess-1"] == [str(f.resolve())]


def test_merge_pending_append_when_model_did_not_echo(tmp_path):
    from agent.soulmate_agent import SoulMateAgent
    agent = SoulMateAgent.__new__(SoulMateAgent)
    f = _mk(tmp_path)
    agent._send_file_tools_inst = SendFileTools(allowed_roots=[tmp_path])
    agent._send_file_dispatch({"path": str(f)}, "s")
    merged = agent._merge_pending_send_media("s", "文件已发送，请查收。")
    assert merged.splitlines()[-1] == f"MEDIA:{f.resolve()}"
    # pop 语义：一回合只交付一次
    assert agent._merge_pending_send_media("s", "第二次") == "第二次"


def test_merge_pending_no_duplicate_when_model_echoed(tmp_path):
    from agent.soulmate_agent import SoulMateAgent
    agent = SoulMateAgent.__new__(SoulMateAgent)
    f = _mk(tmp_path)
    agent._send_file_tools_inst = SendFileTools(allowed_roots=[tmp_path])
    agent._send_file_dispatch({"path": str(f)}, "s")
    echoed = f"已发送。\nMEDIA:{f.resolve()}"
    merged = agent._merge_pending_send_media("s", echoed)
    assert merged == echoed  # 回显不重复
    assert merged.count("MEDIA:") == 1


def test_merge_noop_without_pending():
    from agent.soulmate_agent import SoulMateAgent
    agent = SoulMateAgent.__new__(SoulMateAgent)
    assert agent._merge_pending_send_media("s", "普通回答") == "普通回答"


def test_dispatch_lazy_init_constructs_send_file_tools(monkeypatch, tmp_path):
    import agent.soulmate_agent as sa

    made = {}

    class FakeTools:
        def __init__(self, *a, **k):
            made["init"] = True

        def run(self, params):
            return f"FAKE:{params.get('path', '')}"

    monkeypatch.setattr(sa, "SendFileTools", FakeTools)
    agent = sa.SoulMateAgent.__new__(sa.SoulMateAgent)
    out = agent._send_file_dispatch({"path": "/tmp/x.txt", "message": "m"}, "s")
    assert made.get("init") is True          # lazy-init 真的构造了
    assert out == "FAKE:/tmp/x.txt"          # 参数透传

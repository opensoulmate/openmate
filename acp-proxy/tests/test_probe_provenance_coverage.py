# -*- coding: utf-8 -*-
"""探针产生侧provenance覆盖钉（2026-10-06轮）——新探针脚本不声明test出身就红灯。

背景（dev-reports多轮挂账）：e2e/stability探针会话走真实soulmate写路径
（session/new agent_id=soulmate → SoulMateAgent._save_message 落 agent_messages），
未声明出身的探针整会话产假experiences污染learned_patterns
（"e2e-usagereceipt- 请只回复两个字：好的"类，22:45轮遗留#1）。

机制（jobs.source轮+会话级provenance轮同款哲学）：只认schema标记
（session/new `_meta.source` → 落库行metadata.session_source → ExperienceCollector
skipped_test整会话跳过），清理责任在产生侧。本测试把"声明义务"钉死在文件覆盖面：

1. 凡以真实soulmate写路径创建会话的探针脚本（session/new agent_id=soulmate 或
   /ws/chat agent_proxy），必须带 `"_meta": {"source": "test"}` 声明
2. 豁免清单显式列出（hermes路由/a2a探针——不写agent_messages），集合不一致即红灯：
   新增豁免必须改本测试=强制评审
3. 协议方法名钉死最新标准（session/new、session/prompt）——legacy
   "newSession"/"prompt" 是死方法（acp SDK router只注册 AGENT_METHODS 现名），
   声明搭在死路径上=没接线（本轮 stability_test_100.py 实锤bug）
4. 机制在场钉：soulmate_agent 的 _bind_session_source/_merge_session_meta 存在

无网络/无live server依赖：纯静态源码断言。
"""
import re
from pathlib import Path

ACPPROXY = Path(__file__).resolve().parent.parent

# 探针脚本覆盖面（仓库根目录；tests/下的单测不创建真实会话，不在扫描面）
PROBE_GLOBS = ("e2e_*.py", "stability_test*.py", "integration_test.py")

# 真实soulmate写路径识别：session/new带agent_id=soulmate，或/ws/chat agent_proxy模式
SOULMATE_SESSION_RE = re.compile(r'"agent_id"\s*:\s*"soulmate"')
WS_CHAT_WRITE_RE = re.compile(r"/ws/chat")
AGENT_PROXY_RE = re.compile(r"agent_proxy")

DECL = '"source": "test"'

# 豁免清单（显式审计面）：文件名 → 豁免理由。集合与实际豁免文件必须全等。
EXEMPT = {
    "e2e_session_lifecycle.py": "hermes路由（agent_id=hermes→hermes acp子进程，不写agent_messages）",
    "e2e_relay_traj.py": "hermes路由（agent_id=hermes，不写agent_messages）",
    "e2e_proxy_relay_traj.py": "HTTP /acp/send→hermes路由（proxy.ACPProcess硬编码hermes acp，不写agent_messages）",
    "e2e_probe2.py": ":8090/a2a探针（tasks/send不写agent_messages）",
    "stability_test_http.py": "HTTP /acp/send→hermes路由（不写agent_messages）",
}

# 废弃协议方法名（acp SDK router只注册 session/new、session/prompt）
LEGACY_METHOD_RE = re.compile(r'"method"\s*:\s*"(newSession|prompt)"')


def _probe_files() -> list[Path]:
    files: list[Path] = []
    for glob in PROBE_GLOBS:
        files.extend(sorted(ACPPROXY.glob(glob)))
    return sorted(set(files))


def _writes_agent_messages(src: str) -> bool:
    """该探针是否走真实soulmate/legacy桥写路径（会落agent_messages）。"""
    if SOULMATE_SESSION_RE.search(src):
        return True
    return bool(WS_CHAT_WRITE_RE.search(src) and AGENT_PROXY_RE.search(src))


class TestProbeProvenanceCoverage:
    def test_soulmate_write_path_probes_declare_test_origin(self):
        """走真实写路径的探针脚本必须声明 _meta.source=test（产生侧标注）。"""
        missing = []
        for f in _probe_files():
            src = f.read_text(encoding="utf-8")
            if _writes_agent_messages(src) and DECL not in src:
                missing.append(f.name)
        assert not missing, (
            f"以下探针走真实soulmate写路径但未声明test出身（会污染生产experiences）: {missing}"
        )

    def test_exemption_list_is_explicit_and_closed(self):
        """豁免面显式封口：新增豁免文件必须改本测试（强制评审）。"""
        actual_exempt = set()
        for f in _probe_files():
            src = f.read_text(encoding="utf-8")
            if not _writes_agent_messages(src) and DECL not in src:
                actual_exempt.add(f.name)
        assert actual_exempt == set(EXEMPT), (
            f"豁免清单与实际不一致：多出={actual_exempt - set(EXEMPT)} "
            f"缺失={set(EXEMPT) - actual_exempt}（新增豁免需人工评审并更新EXEMPT）"
        )

    def test_declarations_ride_meta_mechanism(self):
        """声明必须走 _meta 机制（ACP标准扩展字段），不是自创旁路键。"""
        for f in _probe_files():
            src = f.read_text(encoding="utf-8")
            if DECL in src:
                assert "_meta" in src, f"{f.name} 声明了source但没有 _meta 载体"

    def test_no_legacy_acp_method_names(self):
        """协议方法名必须是现行标准（session/new、session/prompt）。

        legacy "newSession"/"prompt" 在 acp SDK MessageRouter 上是 method_not_found
        死方法——声明搭在死路径上等于没接线（本轮 stability_test_100.py 实锤）。
        """
        bad = []
        for f in _probe_files():
            if LEGACY_METHOD_RE.search(f.read_text(encoding="utf-8")):
                bad.append(f.name)
        assert not bad, f"探针脚本使用废弃ACP方法名（死路径）: {bad}"

    def test_stability_probe_uses_current_protocol(self):
        """stability_test_100.py 回归钉：本轮修复的方法名+声明不许回退。"""
        src = (ACPPROXY / "stability_test_100.py").read_text(encoding="utf-8")
        assert '"session/new"' in src
        assert '"session/prompt"' in src
        assert '"newSession"' not in src
        assert DECL in src

    def test_binding_mechanism_present_in_soulmate_agent(self):
        """机制在场钉：soulmate_agent 的产生侧绑定/落库merge函数存在。"""
        src = (ACPPROXY / "agent" / "soulmate_agent.py").read_text(encoding="utf-8")
        assert "def _bind_session_source" in src
        assert "def _merge_session_meta" in src

    def test_scan_surface_is_not_empty(self):
        """覆盖面不为空（glob失效=本测试静默通过的防呆）。"""
        assert len(_probe_files()) >= 8

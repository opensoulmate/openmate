# -*- coding: utf-8 -*-
"""P0-3 工具权限门禁 — acp-proxy执行侧接线

调研来源（SUMMARY.md P0-3，10+方互证）：
- AgentScope PermissionEngine：5模式规则评估（评估本体在opensoul
  src/immune/permission_engine.py，本模块是执行侧客户端）
- kilocode："敏感权限必须真人交互回复——机器审批静默拒绝留pending"；
  审批结果缓存（goose permission_judge结果缓存=防审批弹窗疲劳）
- open-webui tool_approval三态：拒绝=合成错误工具结果，loop不断
- AIHawk：截断/阻断必须显式标记

职责：
1. 每个tool_call执行前调用opensoul /api/immune/permission/check
2. allow → 放行；deny → 返回阻断原因（调用方作为合成工具结果回给LLM）；
   ask → 通过注入的request_approval回调请求真人审批（ACP v1.0标准
   session/request_permission），超时/拒绝=阻断
3. opensoul不可达 → 降级本地策略（只读工具放行 + 灾难命令本地硬否决 +
   其余放行并记录警告 — fail-open防锁死用户工作流，但审计不缺席）
4. 同一session内完全相同的调用被人工批准后缓存，不再重复弹窗
"""

import ast
import asyncio
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

import httpx

logger = logging.getLogger("acp-agent.permission_gate")

DEFAULT_OPSOUL_URL = "http://127.0.0.1:8090/api/immune/permission"

# 降级模式下的本地只读白名单（opensoul不可达时放行）
DEGRADED_READONLY_TOOLS = {
    "read_file", "read_file_segment", "list_files", "search_files",
    "web_search", "web_extract", "vision_analyze", "read_image",
    "check_evolution_status", "todo", "clarify",
}

# 降级模式下的本地灾难命令硬否决（与opensoul builtin hard deny对齐）
_DEGRADED_HARD_PATTERNS = [
    re.compile(p) for p in (
        r"\brm\s+-rf\s+/(?:\s|$|\*)",
        r"\bmkfs\b",
        r"\bdd\s+if=",
        r":\(\)\{\s*:\|:&\s*\};:",
        r">\s*/dev/sd",
        r"\bchmod\s+-R\s+777\s+/",
    )
]

# 审批结果缓存TTL（秒）— goose permission_judge缓存模式，防弹窗疲劳
CACHE_TTL = 600


@dataclass
class GateResult:
    """门禁判定结果。allowed=False时blocked_reason作为合成工具结果回给LLM"""

    allowed: bool
    behavior: str = "allow"            # allow / deny / ask→approved / ask→denied / degraded-allow
    blocked_reason: str = ""
    decision_id: str = ""
    rule_source: str = ""
    rule_content: str = ""
    mode: str = ""
    human_approved: bool = False
    # kilocode #14 classifyDenial：拒绝分层（哪一层拦的——本类构造处最清楚，
    # permission_provenance.classify_denial优先采用，缺失时才推断回退）
    denial_class: str = ""

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed, "behavior": self.behavior,
            "blocked_reason": self.blocked_reason, "decision_id": self.decision_id,
            "rule_source": self.rule_source, "rule_content": self.rule_content,
            "mode": self.mode, "human_approved": self.human_approved,
            "denial_class": self.denial_class,
        }


def _call_signature(tool_name: str, tool_args: dict) -> str:
    try:
        payload = json.dumps(tool_args, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        payload = str(tool_args)
    return hashlib.sha256(f"{tool_name}:{payload}".encode()).hexdigest()[:24]


# ── P0-3 本地降级数据外泄检测（fail-safe镜像） ────────────────────
# opensoul permission_engine.detect_exfiltration 是权威实现；本镜像仅在
# opensoul不可达时兜底（defense-in-depth），保持与引擎同语义：机密文件当网络
# 载荷=凭据外泄→本地硬否决。覆盖最清晰的三类：@上传 / 管道·重定向 / 反弹shell。
_EXFIL_SINK_RE = re.compile(
    r"(?:^|[|;&\s])(?:curl|wget|nc|ncat|netcat|ftp|sftp|scp|ssh|telnet|socat)\b|/dev/(?:tcp|udp)/",
    re.IGNORECASE,
)
_EXFIL_REVERSE_RE = re.compile(
    r"/dev/(?:tcp|udp)/|\bbash\s+-i\b|\bsh\s+-i\b|\bnc\s+(?:-\w*\s+)*-e\b|>&\s*/dev/tcp",
    re.IGNORECASE,
)
_EXFIL_SENSITIVE_RE = re.compile(
    r"\.ssh/|id_rsa|id_ed25519|id_ecdsa|id_dsa|\.pem\b|\.key\b|\.env\b|/etc/passwd|/etc/shadow"
    r"|\.aws/credentials|\.gnupg|\.netrc|\.git-credentials|api[_-]?key|access[_-]?token"
    r"|secret|password|passwd|credential|private[_-]?key|token",
    re.IGNORECASE,
)
_EXFIL_AT_RE = re.compile(r"@(?:'[^']*'|\"[^\"]*\"|[^\s'\"]+)")


def _degraded_exfil(cmd: str) -> Optional[str]:
    if not cmd:
        return None
    if _EXFIL_REVERSE_RE.search(cmd):
        return "reverse_shell"
    if not _EXFIL_SINK_RE.search(cmd):
        return None
    for m in _EXFIL_AT_RE.finditer(cmd):
        if _EXFIL_SENSITIVE_RE.search(m.group(0)[1:]):
            return "upload"
    for m in re.finditer(r"(?:cat|head|tail|less|more|grep)\s+([^\s|;]+)\s*\|", cmd, re.IGNORECASE):
        if _EXFIL_SENSITIVE_RE.search(m.group(1)):
            return "pipe"
    for m in re.finditer(r"<\s*([^\s;]+)", cmd):
        if _EXFIL_SENSITIVE_RE.search(m.group(1)):
            return "redirect"
    return None


# ── P0-3 本地降级Python载荷库级外泄镜像（fail-safe镜像） ──────────
# opensoul permission_engine.detect_python_exfiltration 是权威实现（AST污点分析）；
# 本镜像保持同一算法：强凭据文件读取流入网络出口调用=外泄。取舍与引擎一致——
# 污点源只收强凭据文件路径（与引擎 _SENSITIVE_FILE_RE 同一真源，见 _EXFIL_STRONG_RE）；
# os.environ类不做污点源。若引擎侧规则扩展需双侧同步
# （tests/test_exfil_parity.py 的跨侧一致性断言固化此约束）。
_EXFIL_STRONG_RE = re.compile(
    r"\.ssh/|id_rsa|id_ed25519|id_ecdsa|id_dsa|\.pem\b|\.key\b|\.env\b|/etc/passwd|/etc/shadow"
    r"|\.aws/credentials|\.aws/config|\.gnupg|\.netrc|\.git-credentials|\.npmrc|\.pypirc"
    r"|\.kube/config|\.docker/config|\.bash_history|\.zsh_history",
    re.IGNORECASE,
)
_PY_NET_MODULES = {
    "requests", "httpx", "urllib", "urllib3", "aiohttp", "socket",
    "smtplib", "ftplib", "telnetlib", "http", "httplib",
}
_PY_SINK_METHODS = {
    "get", "post", "put", "patch", "delete", "head", "options", "request",
    "urlopen", "send", "sendall", "sendto", "sendmail", "send_message",
    "storbinary", "storlines", "putfile", "upload",
}
_PY_HANDLE_FACTORIES = _PY_SINK_METHODS | {
    "socket", "create_connection", "Session", "Client", "SMTP", "SMTP_SSL",
    "FTP", "FTP_TLS", "HTTPConnection", "HTTPSConnection", "urlopen",
}
_PY_SHELL_OUT_TAILS = {
    "system", "popen", "run", "call", "check_call", "check_output", "Popen",
    "getoutput", "getstatusoutput",
}
_PY_READ_TAILS = {"read", "readline", "readlines", "read_text", "read_bytes"}
_PY_C_RE = re.compile(
    r"python[23]?(?:\.\d+)?\s+(?:-[a-zA-Z]\S*\s+)*-c\s+([\"'])(.+?)\1", re.DOTALL
)
_PY_HEREDOC_RE = re.compile(
    r"python[23]?(?:\.\d+)?\s*<<[\"']?(\w+)[\"']?\s*\n(.*?)\n\s*\1", re.DOTALL
)
_PY_SINK_HINT_RE = re.compile(
    r"requests\s*\.|httpx\s*\.|urllib|urlopen|socket\s*\.\s*(?:socket|create_connection)"
    r"|smtplib|ftplib|aiohttp|\w+\s*\.\s*(?:post|put|sendall|sendto|storbinary)\s*\(",
    re.IGNORECASE,
)
_PY_OPEN_HINT_RE = re.compile(r"\bopen\s*\(|read_text\s*\(|read_bytes\s*\(")


def _py_qualname(node) -> str:
    parts = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
        return ".".join(reversed(parts))
    if isinstance(cur, ast.Call):
        inner = _py_qualname(cur.func)
        if inner:
            parts.append(inner)
        return ".".join(reversed(parts))
    return ".".join(reversed(parts))


def _py_str_constants(node) -> list:
    return [
        n.value for n in ast.walk(node)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    ]


def _py_names_used(node) -> set:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _py_secret_read(node, tainted: set, sensitive_paths: set) -> Optional[str]:
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        tail = _py_qualname(sub.func).rsplit(".", 1)[-1]
        if tail == "open" or tail in _PY_READ_TAILS:
            for s in _py_str_constants(sub):
                if _EXFIL_STRONG_RE.search(s):
                    return s
            if _py_names_used(sub) & sensitive_paths:
                return sorted(_py_names_used(sub) & sensitive_paths)[0]
            if tail in _PY_READ_TAILS and isinstance(sub.func, ast.Attribute):
                base = sub.func.value
                if isinstance(base, ast.Name) and base.id in tainted:
                    return base.id
    return None


def _analyze_python_ast(code: str) -> Optional[str]:
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return None

    tainted: set = set()
    sensitive_paths: set = set()
    net_handles: set = set()
    dup2 = connect = pty_spawn = False

    def _taint_targets(target, value):
        names = [target.id] if isinstance(target, ast.Name) else [
            t.id for t in ast.walk(target) if isinstance(t, ast.Name)
        ]
        for s in _py_str_constants(value):
            if _EXFIL_STRONG_RE.search(s):
                sensitive_paths.update(names)
                break
        if _py_names_used(value) & sensitive_paths:
            sensitive_paths.update(names)
        secret = _py_secret_read(value, tainted, sensitive_paths)
        if secret is not None or bool(_py_names_used(value) & tainted):
            tainted.update(names)
        for t in names:
            for sub in ast.walk(value):
                if isinstance(sub, ast.Call):
                    q = _py_qualname(sub.func)
                    if (q.split(".", 1)[0] in _PY_NET_MODULES
                            and q.rsplit(".", 1)[-1] in _PY_HANDLE_FACTORIES):
                        net_handles.add(t)
                        break

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                _taint_targets(tgt, node.value)
        elif isinstance(node, ast.AnnAssign) and node.value is not None and node.target is not None:
            _taint_targets(node.target, node.value)
        elif isinstance(node, ast.AugAssign):
            _taint_targets(node.target, node.value)
        elif isinstance(node, ast.withitem):
            if node.optional_vars is not None:
                _taint_targets(node.optional_vars, node.context_expr)

    def _sink_call(call) -> bool:
        q = _py_qualname(call.func)
        tail = q.rsplit(".", 1)[-1]
        if tail not in _PY_SINK_METHODS:
            return False
        if q.split(".", 1)[0] in _PY_NET_MODULES:
            return True
        base = call.func.value if isinstance(call.func, ast.Attribute) else None
        return isinstance(base, ast.Name) and base.id in net_handles

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        q = _py_qualname(node.func)
        tail = q.rsplit(".", 1)[-1]
        if tail == "dup2":
            dup2 = True
        if tail == "spawn" and "pty" in q:
            pty_spawn = True
        if tail == "connect":
            connect = True

        if _sink_call(node):
            args = list(node.args) + [kw.value for kw in node.keywords]
            for arg in args:
                if _py_secret_read(arg, tainted, sensitive_paths) is not None:
                    return "py_upload"
                if _py_names_used(arg) & tainted:
                    return "py_upload"

        if tail in _PY_SHELL_OUT_TAILS and q.split(".", 1)[0] in ("os", "subprocess", "commands"):
            pseudo = " ".join(_py_str_constants(node))
            if pseudo:
                shell_hit = _degraded_exfil(pseudo)
                if shell_hit is not None:
                    return "py_shellout"
            if _py_names_used(node) & (tainted | sensitive_paths) and _EXFIL_SINK_RE.search(pseudo):
                return "py_shellout"

    if (dup2 or pty_spawn) and connect:
        return "reverse_shell"
    return None


def _degraded_py_exfil(code: str) -> Optional[str]:
    """Python载荷库级外泄降级镜像。返回kind或None（引擎detect_python_exfiltration镜像）。"""
    if not code or not code.strip():
        return None
    candidates = [code]
    m = _PY_C_RE.search(code)
    if m:
        candidates.append(m.group(2))
    m2 = _PY_HEREDOC_RE.search(code)
    if m2:
        candidates.append(m2.group(2))
    for cand in candidates:
        hit = _analyze_python_ast(cand)
        if hit is not None:
            return hit
    # 不可解析保守兜底：敏感文件读取+网络出口签名同现
    if (_PY_SINK_HINT_RE.search(code) and _EXFIL_STRONG_RE.search(code)
            and _PY_OPEN_HINT_RE.search(code)):
        return "py_unparsed"
    return None


def degraded_decision(tool_name: str, tool_args: dict) -> GateResult:
    """opensoul不可达时的本地降级策略（可独立测试的纯函数）

    - 数据外泄（凭据外泄/反弹shell）→ 本地硬否决（fail-safe镜像，见上）
    - 只读工具 → 放行
    - 灾难性命令 → 硬否决（与引擎builtin hard deny同源）
    - 其余 → 放行 + 标记degraded（fail-open：不因权限服务掉线锁死用户工作流，
      但决策带degraded标记，审计可追）
    """
    cmd = str(
        tool_args.get("command")
        or tool_args.get("cmd")
        or tool_args.get("code")
        or tool_args.get("script")
        or ""
    )
    exfil = _degraded_exfil(cmd)
    if exfil is None:
        # Python载荷库级外泄镜像（execute_code的code / batch_execute的script；
        # shell命令文本对AST解析天然无效，交由上面的shell检测面）
        exfil = _degraded_py_exfil(cmd)
    if exfil:
        return GateResult(
            allowed=False, behavior="deny",
            blocked_reason=(
                f"[PERMISSION DENIED - DEGRADED LOCAL RULE] {tool_name} blocked: "
                f"credential exfiltration detected ({exfil}) — opensoul权限引擎不可达，"
                f"本地降级硬否决兜底。参数: {json.dumps(tool_args, ensure_ascii=False)[:300]}"),
            rule_source="degraded-local", rule_content=exfil,
            denial_class="exfiltration:degraded-local",
        )
    for pat in _DEGRADED_HARD_PATTERNS:
        if cmd and pat.search(cmd):
            return GateResult(
                allowed=False, behavior="deny",
                blocked_reason=(
                    f"[PERMISSION DENIED - DEGRADED LOCAL RULE] {tool_name} blocked: "
                    f"灾难性命令命中本地降级硬否决规则（opensoul权限引擎不可达）。"
                    f"参数: {json.dumps(tool_args, ensure_ascii=False)[:300]}"),
                rule_source="degraded-local", rule_content=pat.pattern,
                denial_class="patterns:degraded-local",
            )
    if tool_name in DEGRADED_READONLY_TOOLS:
        return GateResult(allowed=True, behavior="allow", rule_source="degraded-local")
    return GateResult(allowed=True, behavior="degraded-allow", rule_source="degraded-local")


class PermissionGate:
    """执行侧权限门禁 — opensoul引擎客户端 + 人工审批 + 结果缓存"""

    def __init__(self, opensoul_url: str = "", timeout: float = 2.0,
                 approval_timeout: float = 120.0):
        self.opensoul_url = (opensoul_url or DEFAULT_OPSOUL_URL).rstrip("/")
        self.timeout = timeout
        self.approval_timeout = approval_timeout
        # (session_id, signature) -> (approved_bool, expire_ts)
        self._approval_cache: dict[tuple[str, str], tuple[bool, float]] = {}
        self.stats = {"checked": 0, "allowed": 0, "denied": 0, "asked": 0,
                      "approved": 0, "rejected": 0, "cache_hits": 0,
                      "engine_errors": 0, "degraded": 0}

    def _cache_get(self, session_id: str, sig: str) -> Optional[bool]:
        entry = self._approval_cache.get((session_id, sig))
        if not entry:
            return None
        approved, expire = entry
        if time.time() > expire:
            self._approval_cache.pop((session_id, sig), None)
            return None
        return approved

    def _cache_put(self, session_id: str, sig: str, approved: bool) -> None:
        self._approval_cache[(session_id, sig)] = (approved, time.time() + CACHE_TTL)

    async def _call_engine(self, tool_name: str, tool_args: dict,
                           session_id: str, working_dir: str) -> dict:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                f"{self.opensoul_url}/check",
                json={
                    "tool_name": tool_name,
                    "arguments": tool_args,
                    "session_id": session_id,
                    "working_dir": working_dir,
                },
            )
            resp.raise_for_status()
            return resp.json()

    async def _record_outcome(self, decision_id: str, outcome: str, comment: str = "") -> None:
        """人工审批结果回写opensoul审计（fire-and-forget，失败不影响执行）"""
        if not decision_id:
            return
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                await client.post(
                    f"{self.opensoul_url}/approvals/{decision_id}",
                    json={"outcome": outcome, "comment": comment},
                )
        except Exception as e:
            logger.warning("[gate] outcome writeback failed (%s): %s", decision_id, e)

    async def check(
        self,
        session_id: str,
        tool_name: str,
        tool_args: dict,
        working_dir: str = "",
        request_approval: Optional[Callable[[str, dict, dict], Awaitable[bool]]] = None,
    ) -> GateResult:
        """工具执行前的门禁判定。

        request_approval: async (tool_name, tool_args, decision) -> bool
            返回True=真人批准。None（无人值守路径）时ask直接按拒绝处理
            （kilocode：机器审批静默拒绝，不放行敏感操作）。
        """
        self.stats["checked"] += 1
        sig = _call_signature(tool_name, tool_args)

        # 会话内审批缓存（goose缓存模式）：同session同参数已批 → 直接放行
        cached = self._cache_get(session_id, sig)
        if cached is True:
            self.stats["cache_hits"] += 1
            self.stats["allowed"] += 1
            return GateResult(allowed=True, behavior="cached-allow",
                              human_approved=True, rule_source="session-cache")

        # 调用opensoul权限引擎
        decision: dict = {}
        engine_ok = True
        try:
            decision = await self._call_engine(tool_name, tool_args, session_id, working_dir)
        except Exception as e:
            engine_ok = False
            self.stats["engine_errors"] += 1
            logger.warning("[gate] opensoul permission engine unreachable: %s", e)

        if not engine_ok:
            self.stats["degraded"] += 1
            result = degraded_decision(tool_name, tool_args)
            if result.allowed:
                self.stats["allowed"] += 1
                if result.behavior == "degraded-allow":
                    logger.warning(
                        "[gate] DEGRADED allow (engine down): %s %s",
                        tool_name, json.dumps(tool_args, ensure_ascii=False)[:200])
            else:
                self.stats["denied"] += 1
            return result

        behavior = decision.get("behavior", "allow")
        decision_id = decision.get("decision_id", "")
        rule_source = decision.get("rule_source", "")
        rule_content = decision.get("rule_content", "")
        mode = decision.get("mode", "")
        reason = decision.get("decision_reason", "") or decision.get("message", "")
        suggestions = decision.get("suggested_rules", []) or []

        if behavior == "allow":
            self.stats["allowed"] += 1
            return GateResult(allowed=True, behavior="allow", decision_id=decision_id,
                              rule_source=rule_source, rule_content=rule_content, mode=mode)

        if behavior == "deny":
            self.stats["denied"] += 1
            suggestion_text = ""
            if suggestions:
                s = suggestions[0]
                suggestion_text = (
                    f"\n如需放行，可添加权限规则: tool={s.get('tool_name')} "
                    f"content={s.get('rule_content') or '(tool-level)'} behavior=allow")
            return GateResult(
                allowed=False, behavior="deny", decision_id=decision_id,
                rule_source=rule_source, rule_content=rule_content, mode=mode,
                denial_class=f"ruleset:{rule_source or 'unknown'}",
                blocked_reason=(
                    f"[PERMISSION DENIED] {tool_name} 被OpenSoul权限引擎拦截。\n"
                    f"原因: {reason}\n"
                    f"规则来源: {rule_source} / 规则内容: {rule_content or '(tool-level)'}\n"
                    f"参数: {json.dumps(tool_args, ensure_ascii=False)[:300]}"
                    f"{suggestion_text}\n"
                    f"（此拒绝作为工具结果返回，请勿重复调用同一操作，"
                    f"可向用户说明原因或改用其他方式）"))

        # behavior == "ask" — 需要真人审批
        self.stats["asked"] += 1
        if request_approval is None:
            # 无人值守：机器审批静默拒绝（kilocode敏感权限规则）
            self.stats["rejected"] += 1
            await self._record_outcome(decision_id, "denied", "no human approver available")
            return GateResult(
                allowed=False, behavior="ask-denied", decision_id=decision_id,
                rule_source=rule_source, rule_content=rule_content, mode=mode,
                denial_class="approval:unattended",
                blocked_reason=(
                    f"[PERMISSION DENIED] {tool_name} 需要人工审批但当前无人值守"
                    f"（机器审批静默拒绝）。原因: {reason}"))

        approved = False
        ask_resolution = "human-rejected"  # kilocode #14：区分超时/真人拒绝
        try:
            approved = await asyncio.wait_for(
                request_approval(tool_name, tool_args, decision),
                timeout=self.approval_timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("[gate] approval timeout (%.0fs): %s", self.approval_timeout, tool_name)
            approved = False
            ask_resolution = "timeout"
        except Exception as e:
            logger.error("[gate] approval request failed: %s", e)
            approved = False
            ask_resolution = "request-failed"

        if approved:
            self.stats["approved"] += 1
            self._cache_put(session_id, sig, True)
            await self._record_outcome(decision_id, "approved", "human approved via ACP")
            return GateResult(allowed=True, behavior="ask-approved", decision_id=decision_id,
                              rule_source=rule_source, rule_content=rule_content,
                              mode=mode, human_approved=True)

        self.stats["rejected"] += 1
        await self._record_outcome(decision_id, "denied", "human rejected or timed out")
        return GateResult(
            allowed=False, behavior="ask-denied", decision_id=decision_id,
            rule_source=rule_source, rule_content=rule_content, mode=mode,
            denial_class=f"approval:{ask_resolution}",
            blocked_reason=(
                f"[PERMISSION DENIED - USER REJECTED] {tool_name} 的审批被用户拒绝或超时。\n"
                f"请求原因: {reason}\n"
                f"参数: {json.dumps(tool_args, ensure_ascii=False)[:300]}\n"
                f"（此拒绝作为工具结果返回，请勿重复调用同一操作）"))

    def get_stats(self) -> dict:
        return {**self.stats, "cache_size": len(self._approval_cache)}

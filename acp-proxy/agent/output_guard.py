"""agent/output_guard.py — P0 输出侧护栏：流式密钥脱敏（Warp secret_redaction 移植）

调研来源：
- SUMMARY.md「immune | 输出侧护栏（流式脱敏20正则+输出侧注入审计门）| P0」
- 64-warp-source-supplement2.md #1-#4（secret_redaction.rs 613行消费端流式三缓冲）
- 本地源码 ~/agent-research-src/warp-master/crates/secret_redaction/src/lib.rs
  DEFAULT_REGEXES_WITH_NAMES 20正则**逐字移植**（Rust regex → Python re 等价改写，
  不凭报告转述、不自创模式）

设计对齐 Warp（lib.rs / secret_redaction.rs 注释即规格）：
- "redact secrets WHILE streaming to avoid any full secrets ever being shown"
  —— 边流式输出边脱敏，完整秘密绝不进入用户可见流。
- last_word_to_rescan 同构：秘密可能被 streaming chunk 从词中间劈开 → 本实现按
  **空白符边界**切分 emit/holdback（20正则中凭据类全部是无空白单 token，故把
  "最后一个空白符之后的全部内容"hold 住即可保证秘密永不被劈开）；下个 chunk
  到达时 holdback 与新内容一起重扫（=Warp "每chunk重扫上一个词"）。
- SecretLevel 分级：credential（密钥/令牌/JWT）/ identifier（IP/电话/MAC等）。
  **适配披露**：Warp 默认全部遮罩，本系统默认只遮 credential——开发场景输出大量
  合法 IP/端口（127.0.0.1:8090/health），全遮罩毁可用性；identifier 级经
  OUTPUT_REDACT_LEVEL=identifier 显式开启（off=完全关闭）。
- 检测可见（mem0 §1.1 处理必须可见，禁止静默）：每次脱敏打 WARNING（只记
  种类/级别/长度，**秘密本体绝不进日志/绝不进 findings**）。

fail-safe 取向（输出侧=凭据泄漏面，泄漏不可逆）：
- 单个 pattern 编译失败 → 跳过该 pattern + WARNING，其余 pattern 继续生效；
- 模块级完全不可用（构造异常）→ WARNING + 原文放行（不吞掉用户回答）；
  两种失败都有可见日志，禁止静默。

不属于本模块（列遗留）：输出侧 prompt 注入审计门（agent-zero _infection_check
三态verdict）、UI hover 点击揭示 tooltip（Warp 双消费端 UX）、current_line
markdown 重写重扫（UI 渲染层问题，本系统直接发纯文本）。
"""

from __future__ import annotations

import logging
import os
import re

logger = logging.getLogger("output_guard")

REDACTION_MARKER = "[REDACTED:{name}]"

# 流式 holdback 上限：正常路径按空白边界切（hold=尾部token，无上限但受此帽约束）；
# 超过 MAX_HOLDBACK 的单token（超长URL/base64等）强制切分，尾部保留 OVERLAP 字符
# ——20正则最长 ~135 字符（ANTHROPIC 80-120 + 前缀），512 > 135 保证被切分点
# 劈开的"未完结秘密"头部完整留在 holdback 里，下轮补全后被整体脱敏。
MAX_HOLDBACK = 1024
OVERLAP = 512
# 20正则最长匹配上界（ANTHROPIC_API_KEY: "sk-ant-api02-"+80~120 ≈ 135字符）+余量
# ——强制切分时切分点前退守长度：横跨切分点的未完结秘密整体留在holdback里
MAX_SECRET_LEN = 150

_LEVEL_RANK = {"off": 0, "identifier": 1, "credential": 2}


# ── Warp crates/secret_redaction/src/lib.rs DEFAULT_REGEXES_WITH_NAMES 逐字移植 ──
# (name, pattern, level)。顺序=Warp原始顺序（specific 先于 generic，左most-先序合并）。
_SECRET_PATTERNS: list[tuple[str, str, str]] = [
    ("IPv4 Address", r"\b((25[0-5]|(2[0-4]|1\d|[1-9]|)\d)\.?\b){4}\b", "identifier"),
    ("IPv6 Address",
     r"\b((([0-9A-Fa-f]{1,4}:){1,6}:)|(([0-9A-Fa-f]{1,4}:){7}))([0-9A-Fa-f]{1,4})\b",
     "identifier"),
    ("Phone Number", r"\b(\+\d{1,2}\s)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b", "identifier"),
    ("MAC Address",
     r"\b((([a-zA-z0-9]{2}[-:]){5}([a-zA-z0-9]{2}))|(([a-zA-z0-9]{2}:){5}([a-zA-z0-9]{2})))\b",
     "identifier"),
    ("Google API Key", r"\bAIza[0-9A-Za-z-_]{35}\b", "credential"),
    ("AWS Access ID",
     r"\b(AKIA|A3T|AGPA|AIDA|AROA|AIPA|ANPA|ANVA|ASIA)[A-Z0-9]{12,}\b", "credential"),
    ("Slack App Token", r"\bxapp-[0-9]+-[A-Za-z0-9_]+-[0-9]+-[a-f0-9]+\b", "credential"),
    ("GitHub Classic Personal Access Token", r"\bghp_[A-Za-z0-9_]{36}\b", "credential"),
    ("GitHub Fine-Grained Personal Access Token", r"\bgithub_pat_[A-Za-z0-9_]{82}\b", "credential"),
    ("GitHub OAuth Access Token", r"\bgho_[A-Za-z0-9_]{36}\b", "credential"),
    ("GitHub User-to-Server Token", r"\bghu_[A-Za-z0-9_]{36}\b", "credential"),
    ("GitHub Server-to-Server Token", r"\bghs_[A-Za-z0-9_]{36}\b", "credential"),
    ("Stripe Key", r"\b(?:r|s)k_(test|live)_[0-9a-zA-Z]{24}\b", "credential"),
    ("Firebase Auth Domain", r"\b([a-z0-9-]){1,30}(\.firebaseapp\.com)\b", "identifier"),
    ("JWT", r"\b(ey[a-zA-z0-9_\-=]{10,}\.){2}[a-zA-z0-9_\-=]{10,}\b", "credential"),
    ("OpenAI API Key", r"\bsk-[a-zA-Z0-9]{48}\b", "credential"),
    ("Anthropic API Key", r"\bsk-ant-api\d{0,2}-[a-zA-Z0-9\-]{80,120}\b", "credential"),
    ("Generic SK API Key", r"\bsk-[a-zA-Z0-9\-]{10,100}\b", "credential"),
    ("Fireworks API Key", r"\bfw_[a-zA-Z0-9]{24}\b", "credential"),
    ("Warp API Key", r"\bwk-[0-9]+\.[A-Fa-f0-9.\-]+\b", "credential"),
]

# 惰性编译缓存：level -> [(name, compiled, level)]；编译失败的 pattern 跳过+可见警告
_COMPILED: dict[str, list[tuple[str, "re.Pattern[str]", str]]] = {}


def _patterns_for(level: str | None) -> list[tuple[str, "re.Pattern[str]", str]]:
    lv = (level or os.environ.get("OUTPUT_REDACT_LEVEL") or "credential").lower()
    if lv not in _LEVEL_RANK:
        logger.warning(f"[output-guard] 未知 OUTPUT_REDACT_LEVEL={lv!r}，按credential处理")
        lv = "credential"
    if lv in _COMPILED:
        return _COMPILED[lv]
    threshold = _LEVEL_RANK[lv]
    if threshold == 0:  # off=显式关闭（rank 0 不是"全收"，否则off会全量脱敏）
        _COMPILED[lv] = []
        return []
    out: list[tuple[str, re.Pattern[str], str]] = []
    for name, pat, pat_level in _SECRET_PATTERNS:
        if _LEVEL_RANK[pat_level] < threshold:
            continue
        try:
            out.append((name, re.compile(pat), pat_level))
        except re.error as exc:  # 单pattern失败不拖垮其余（fail-safe：跳过+可见）
            logger.warning(f"[output-guard] pattern编译失败，跳过 {name}: {exc}")
    _COMPILED[lv] = out
    return out


def _find_matches(text: str, level: str | None) -> list[tuple[int, int, str, str]]:
    """收集并合并全部匹配：(start, end, kind, level)。左most优先、长者优先、
    Warp 原序（specific先于generic）决胜——重叠区域只由先序 pattern 命中一次。"""
    pats = _patterns_for(level)
    matches: list[tuple[int, int, str, str]] = []
    for name, cre, pat_level in pats:
        for m in cre.finditer(text):
            matches.append((m.start(), m.end(), name, pat_level))
    if not matches:
        return []
    matches.sort(key=lambda t: (t[0], -(t[1] - t[0])))
    merged: list[tuple[int, int, str, str]] = []
    pos = 0
    for start, end, name, pat_level in matches:
        if start < pos:  # 与已命中区域重叠 → 防重复条目（Warp retain清理防抖）
            continue
        merged.append((start, end, name, pat_level))
        pos = end
    return merged


def _redact_text(text: str, level: str | None) -> tuple[str, list[dict]]:
    """全量脱敏：返回 (脱敏后文本, findings)。findings 只含种类/级别/长度，绝无秘密本体。"""
    if not text:
        return "", []
    merged = _find_matches(text, level)
    if not merged:
        return text, []
    findings: list[dict] = []
    parts: list[str] = []
    pos = 0
    for start, end, name, pat_level in merged:
        parts.append(text[pos:start])
        parts.append(REDACTION_MARKER.format(name=name))
        findings.append({"kind": name, "level": pat_level, "length": end - start})
        logger.warning(
            f"[output-guard] 输出脱敏: kind={name} level={pat_level} len={end - start}")
        pos = end
    parts.append(text[pos:])
    return "".join(parts), findings


def redact_secrets(text: str | None, level: str | None = None) -> tuple[str, list[dict]]:
    """对外全量脱敏入口（result_preview/缓存重放等非流式边界用）。"""
    if text is None:
        return "", []
    try:
        return _redact_text(str(text), level)
    except Exception as exc:  # 模块级故障：原文放行+可见（不吞用户内容）
        logger.warning(f"[output-guard] 脱敏故障，原文放行: {exc}")
        return str(text), []


class SecretStreamRedactor:
    """流式增量脱敏器（Warp secret_redaction.rs 三缓冲的 Python 等价实现）。

    用法（每个流式输出生命周期一个实例）：
        r = SecretStreamRedactor()
        for chunk in stream: emit(r.feed(chunk))
        emit(r.flush())

    保证：feed()/flush() 返回值的拼接中**永远不含完整秘密**（被 [REDACTED:kind]
    替换）；秘密被 chunk 劈开时前半段一直压在 holdback 里不 emit，补全后整体替换。
    """

    def __init__(self, level: str | None = None):
        self._level = level
        self._buf = ""
        self._findings: list[dict] = []

    @property
    def findings(self) -> list[dict]:
        return list(self._findings)

    def feed(self, chunk: str) -> str:
        if not chunk:
            return ""
        try:
            pats = _patterns_for(self._level)
        except Exception as exc:
            logger.warning(f"[output-guard] 脱敏故障，原文放行: {exc}")
            return chunk
        if not pats:
            return chunk
        self._buf += chunk
        # 正常切点：最后一个空白符之后全是"未完结token"→整体hold（秘密是无空白
        # 单token，按空白切=秘密永不被劈开）
        cut = 0
        for m in re.finditer(r"\s", self._buf):
            cut = m.end()
        hold_len = len(self._buf) - cut
        if hold_len > MAX_HOLDBACK:
            # 超长单token（URL/base64）强制切分。emit区不以空白结尾，最大危险是
            # **假\b**：emit区末尾恰好是秘密中段时，正则在区域末尾"看起来完整"
            # （测试首跑实锤：13字符前缀被当完整秘密脱敏，后半段碎片外发）。
            # 对策=定点回撤：凡 end==emit区末尾 的命中一律可疑，把切分点回撤到
            # 其起点，反复迭代直到emit区收尾干净（回撤后新末尾可能又有新可疑命中）。
            cut2 = max(0, len(self._buf) - OVERLAP)
            head, tail = self._buf[:cut2], self._buf[cut2:]
            keep_from = max(0, len(head) - MAX_SECRET_LEN)
            for _ in range(16):
                region = head[:keep_from]
                boundary = [
                    m for m in _find_matches(region, self._level) if m[1] == len(region)]
                if not boundary:
                    break
                new_keep = min(m[0] for m in boundary)
                if new_keep >= keep_from:
                    break
                keep_from = new_keep
            else:
                # 收敛失败fail-safe：本轮不emit、全部留hold（宁可卡一轮不泄漏）
                keep_from = len(head)
            self._buf = head[keep_from:] + tail
            redacted, found = _redact_text(head[:keep_from], self._level)
            self._findings.extend(found)
            return redacted
        if cut == 0:
            return ""  # 尚无安全边界，全部hold
        safe = self._buf[:cut]
        self._buf = self._buf[cut:]
        redacted, found = _redact_text(safe, self._level)
        self._findings.extend(found)
        return redacted

    def flush(self) -> str:
        """冲出 holdback 尾部（流结束时必须调用，否则尾部token永远压着不显示）。"""
        if not self._buf:
            return ""
        redacted, found = _redact_text(self._buf, self._level)
        self._findings.extend(found)
        self._buf = ""
        return redacted

    def summary(self) -> str:
        """可观测摘要（无秘密本体）：脱敏了几个什么种类。"""
        if not self._findings:
            return ""
        kinds: dict[str, int] = {}
        for f in self._findings:
            kinds[f["kind"]] = kinds.get(f["kind"], 0) + 1
        return ", ".join(f"{k}×{v}" for k, v in kinds.items())

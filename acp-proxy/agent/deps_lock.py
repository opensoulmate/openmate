# -*- coding: utf-8 -*-
"""deps_lock.py — 依赖锁版本防御（Pi 取长补短 #15：精确锁版本守护）。

来源：《取长补短清单》#15——Pi 在 patch/minor 发破坏性变更（1.0.3 改 Azure
provider 命名、1.2.0 四项 breaking），OpenBidKit 只能锁死 0.80.10 自保。SoulMate
落地（能力层·工程纪律，纯 Python 确定性独立可测）：

- 锁面单一真源 `requirements.lock.txt`（acp-proxy 根）：第三方依赖一律精确锁
  `==`，分组 [core]/[tools]/[dev]/[optional]；任何 `^ ~ >= < *` 裸版本都判锁文件
  非法（半半天立规矩）；
- `verify()` 比对**运行 interpreter 实际安装版本**（importlib.metadata，零 pip
  调用零网络零运行时风险）→ 结构化漂移报告 {ok, missing, drifted, unpinned}：
  漂移/缺失必须可见禁静默（mem0 §1.1）；
- `require()`：可选依赖按需校验（工具缺依赖显式报因，与 docgen/tts/image 工具
  的 `_XXX_HINT` 显式报因纪律同构）。

配套「供应商错误文案兼容层」在 error_codes/vendor_compat.py（同属 #15：把供应商
错误 payload 归一成结构化错误码，重试决策用码不用文案正则——抄 piRetryErrorNormalizer
功能不抄其正则机制）。

独立可测（tests/test_deps_lock.py）。
"""
from __future__ import annotations

import importlib.metadata as _im
import json
import re
from pathlib import Path

__all__ = [
    "LOCK_FILE",
    "PIN_OPERATORS",
    "LockSpec",
    "parse_lock",
    "installed_version",
    "verify",
    "require",
    "format_report",
]

LOCK_FILE = Path(__file__).resolve().parent.parent / "requirements.lock.txt"

# 只允许精确锁 ==（Pi/OpenBidKit 依赖锁纪律：防小版本 breaking 偷袭）
PIN_OPERATORS = ("==",)

_SECTION_RE = re.compile(r"^\s*#\s*──\s*\[([a-z]+)\]", re.IGNORECASE)
_PIN_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(==|>=|<=|~=|\^|<|>|=|\*)\s*([^\s#]+)?\s*(?:#.*)?$")
_NAME_ONLY_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:#.*)?$")


class LockSpec:
    """锁文件解析结果：{group: {dist_name: version}}。"""

    def __init__(self, pins: dict[str, dict[str, str]], invalid: list[dict]):
        self.pins = pins            # group -> {name -> version}
        self.invalid = invalid      # 非精确锁条目 [{"line", "text", "operator"}]

    def all_pins(self) -> dict[str, str]:
        merged: dict[str, str] = {}
        for group in self.pins.values():
            merged.update(group)
        return merged

    def groups(self) -> list[str]:
        return list(self.pins.keys())


def parse_lock(text: str) -> LockSpec:
    """解析锁文件文本 → LockSpec。

    非 `==` 精确锁条目进 invalid（verify 会报 ok=False——锁文件自身失守即失败）。
    分组按 `# ── [group]` 段头切换；无段头的条目归 "core"。
    """
    pins: dict[str, dict[str, str]] = {}
    invalid: list[dict] = []
    group = "core"
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        m_sec = _SECTION_RE.match(line)
        if m_sec:
            group = m_sec.group(1).lower()
            pins.setdefault(group, {})
            continue
        if line.startswith("#"):
            continue
        pins.setdefault(group, {})
        m = _PIN_RE.match(line)
        if m:
            name, op, ver = m.group(1), m.group(2), (m.group(3) or "")
            key = name.lower().replace("_", "-")
            if op not in PIN_OPERATORS:
                invalid.append({"line": lineno, "text": raw.strip(), "operator": op})
                continue
            if not ver:
                invalid.append({"line": lineno, "text": raw.strip(), "operator": op})
                continue
            pins[group][key] = ver
            continue
        m2 = _NAME_ONLY_RE.match(line)
        if m2:
            # 裸名字（无版本）同样违反精确锁纪律
            invalid.append({"line": lineno, "text": raw.strip(), "operator": ""})
            continue
        invalid.append({"line": lineno, "text": raw.strip(), "operator": "?"})
    return LockSpec(pins, invalid)


def installed_version(dist_name: str) -> str | None:
    """运行 interpreter 中实际安装版本（importlib.metadata，找不到返回 None）。"""
    try:
        return _im.version(dist_name)
    except _im.PackageNotFoundError:
        return None


def verify(lock_path: Path | str | None = None,
           groups: list[str] | None = None,
           include_optional: bool = False) -> dict:
    """比对锁文件与实际安装版本 → 结构化漂移报告。

    {"ok": bool, "lock_file": str, "checked": N, "missing": [...],
     "drifted": [{"name", "pinned", "installed"}...], "invalid_pins": [...],
     "unpinned": [锁文件没管但工具面直接引用的名字（提示补锁）]}

    ok=False 的三种情况（失败必须可见）：锁文件有非精确锁条目 / 锁定包没装 /
    装的版本与锁不一致。[optional] 组默认不计入 ok（未安装=显式降级路径），
    include_optional=True 时一并校验。
    """
    path = Path(lock_path) if lock_path else LOCK_FILE
    if not path.exists():
        return {"ok": False, "lock_file": str(path), "checked": 0,
                "missing": [{"name": str(path), "pinned": "", "installed": None}],
                "drifted": [], "invalid_pins": [],
                "error": "锁文件不存在（依赖锁未建立）"}
    spec = parse_lock(path.read_text(encoding="utf-8"))
    want_groups = groups or [g for g in spec.groups()
                            if include_optional or g != "optional"]
    missing: list[dict] = []
    drifted: list[dict] = []
    checked = 0
    for group in want_groups:
        for name, pinned in spec.pins.get(group, {}).items():
            checked += 1
            inst = installed_version(name)
            if inst is None:
                missing.append({"name": name, "pinned": pinned,
                                "installed": None, "group": group})
            elif inst != pinned:
                drifted.append({"name": name, "pinned": pinned,
                                "installed": inst, "group": group})
    ok = not missing and not drifted and not spec.invalid
    return {
        "ok": ok,
        "lock_file": str(path),
        "checked": checked,
        "missing": missing,
        "drifted": drifted,
        "invalid_pins": list(spec.invalid),
    }


def require(*dist_names: str) -> dict:
    """按需校验可选依赖（工具缺依赖显式报因用）。

    {"ok": bool, "missing": [名字...], "versions": {name: version}}
    """
    versions: dict[str, str] = {}
    missing: list[str] = []
    for n in dist_names:
        v = installed_version(n)
        if v is None:
            missing.append(n)
        else:
            versions[n] = v
    return {"ok": not missing, "missing": missing, "versions": versions}


def format_report(report: dict) -> str:
    """人可读报告（运维/巡检面）。"""
    if report.get("error"):
        return f"❌ 依赖锁异常: {report['error']}"
    lines = []
    if report["ok"]:
        lines.append(f"✅ 依赖锁一致：{report['checked']} 个精确锁全部匹配"
                     f"（{report['lock_file']}）")
    else:
        lines.append(f"❌ 依赖锁漂移（{report['lock_file']}）：")
        for m in report["missing"]:
            lines.append(f"  缺失: {m['name']}（锁 {m['pinned']}，实际未安装）")
        for d in report["drifted"]:
            lines.append(f"  漂移: {d['name']}（锁 {d['pinned']} ≠ 实际 {d['installed']}）")
        for it in report["invalid_pins"]:
            lines.append(f"  锁文件违规: 第{it['line']}行 非精确锁 ==: {it['text']}")
    return "\n".join(lines)


def main() -> int:  # pragma: no cover — CLI 面
    import sys
    rep = verify(include_optional="--optional" in sys.argv)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    return 0 if rep["ok"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

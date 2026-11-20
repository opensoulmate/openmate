#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────
# 菜单回归防线 · 一键跑（改菜单必跑，比对不过就不许上）
#
#   bash scripts/menu-regression.sh                  # 与基线逐项比对（绿=0 / 红=1）
#   bash scripts/menu-regression.sh --update-baseline # 显式重建基线（改菜单并确认后）
#
# 依赖：真浏览器（Playwright + 系统 chromium，或 playwright 自带 chromium）、
#       OpenMate Web 运行在 :3000（默认；可用 --base-url 覆盖）。
# 失败输出逐项 diff（缺失/降级/多余/顺序/位置），并留存截图与 JSON 报告。
# ─────────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# 找一个带 playwright 的 Python 解释器（自包含，不依赖调用方环境）
PY=""
for cand in python3 python /usr/bin/python3 /usr/bin/python; do
  if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import playwright' >/dev/null 2>&1; then
    PY="$cand"
    break
  fi
done
if [ -z "$PY" ]; then
  echo "FATAL: 未找到带 playwright 的 Python 解释器（pip install playwright）" >&2
  exit 2
fi

echo "menu-regression: python=$PY repo=$REPO_ROOT" >&2
exec "$PY" scripts/menu_dom_audit.py "$@"

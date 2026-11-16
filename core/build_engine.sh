#!/bin/bash
# PyInstaller 冻结下载引擎单文件（Linux x64 先行；引擎=纯 Python）。
# 用法: bash build_engine.sh   → 产物 core/dist/download-engine
# 构建 venv 缺失时自动创建（pyinstaller + httpx，仅构建期依赖；运行时零依赖）。
set -e

CORE_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$CORE_DIR/.freeze-venv"

if [ ! -x "$VENV/bin/pyinstaller" ]; then
  echo "creating build venv at $VENV" >&2
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install -q pyinstaller httpx
fi

echo "building frozen engine (onefile)..." >&2
"$VENV/bin/pyinstaller" \
  --onefile \
  --name download-engine \
  --paths "$CORE_DIR" \
  --distpath "$CORE_DIR/dist" \
  --workpath /tmp/openmate-engine-pyi-work \
  --specpath /tmp/openmate-engine-pyi-work \
  "$CORE_DIR/freeze_entry.py"

echo "frozen engine: $CORE_DIR/dist/download-engine" >&2
"$CORE_DIR/dist/download-engine" --version >&2 || true

#!/bin/bash
# OpenMate 发版脚本：构建 NSIS 安装包 + 推送到 climbing.top 更新源
# 用法：先 bump package.json 的 version，然后 bash publish-update.sh
set -e
cd "$(dirname "$0")"
VER=$(node -p "require('./package.json').version")
echo "==> 发布 OpenMate v$VER" >&2

export ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/
export ELECTRON_BUILDER_BINARIES_MIRROR=https://npmmirror.com/mirrors/electron-builder-binaries/

# 引擎预检：安装版必须带冻结引擎（extraResources 落到 resources/download-engine[.exe]），
# 缺引擎时「本机」目标点了必失败。Linux 构建机缺引擎就地冻结；Windows 引擎（.exe）必须在
# Windows 上跑 core/build_engine.sh 产出（PyInstaller 不跨平台），缺了显式报错不装死。
if [ ! -x ../core/dist/download-engine ] && [ ! -f ../core/dist/download-engine.exe ]; then
  echo "==> 缺冻结引擎，就地构建（core/build_engine.sh）" >&2
  bash ../core/build_engine.sh
fi
if [ ! -f ../core/dist/download-engine.exe ]; then
  echo "!! 警告：无 ../core/dist/download-engine.exe（Windows 冻结引擎）——" >&2
  echo "!!   本次 Windows 包不带本机下载引擎，需在 Windows 上跑 core/build_engine.sh 后重打包。" >&2
  [ "${OPENMATE_ALLOW_NO_ENGINE:-}" = "1" ] || { echo "!! 中止（OPENMATE_ALLOW_NO_ENGINE=1 可强制继续）" >&2; exit 1; }
fi

npx electron-builder --win nsis

scp dist/OpenMate-Setup-$VER.exe dist/OpenMate-Setup-$VER.exe.blockmap dist/latest.yml \
    ubuntu@49.232.220.234:/var/www/html/wordpress/openmate-releases/

curl -sf -o /dev/null https://climbing.top/openmate-releases/latest.yml
echo "==> 发布完成：https://climbing.top/openmate-releases/latest.yml (客户端 4 小时内自动更新)" >&2

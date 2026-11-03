#!/bin/bash
# OpenMate 发版脚本：构建 NSIS 安装包 + 推送到 climbing.top 更新源
# 用法：先 bump package.json 的 version，然后 bash publish-update.sh
set -e
cd "$(dirname "$0")"
VER=$(node -p "require('./package.json').version")
echo "==> 发布 OpenMate v$VER" >&2

export ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/
export ELECTRON_BUILDER_BINARIES_MIRROR=https://npmmirror.com/mirrors/electron-builder-binaries/
npx electron-builder --win nsis

scp dist/OpenMate-Setup-$VER.exe dist/OpenMate-Setup-$VER.exe.blockmap dist/latest.yml \
    ubuntu@49.232.220.234:/var/www/html/wordpress/openmate-releases/

curl -sf -o /dev/null https://climbing.top/openmate-releases/latest.yml
echo "==> 发布完成：https://climbing.top/openmate-releases/latest.yml (客户端 4 小时内自动更新)" >&2

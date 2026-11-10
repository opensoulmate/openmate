#!/bin/bash
# OpenMate Tauri 发版脚本：签名构建 NSIS 安装包 + 推送到 climbing.top 更新源
#（= Electron 时代 desktop/publish-update.sh 的 Tauri 版，更新通道同一目录、互不干扰：
#  Electron 客户端读 latest.yml，Tauri 客户端读 tauri-updater.json）
# 用法：bash publish-update.sh    （版本号=tauri.conf.json 的 version）
# 客户端行为：启动 10s 后自动查更、之后每 4 小时；新版自动下载→主题化弹窗"新版本已就绪"。
set -e
cd "$(dirname "$0")"
VER=$(node -p "require('./tauri.conf.json').version")
PUB_URL="https://climbing.top/openmate-releases"
SERVER="ubuntu@49.232.220.234:/var/www/html/wordpress/openmate-releases/"
echo "==> 发布 OpenMate v$VER (Tauri)" >&2

# 签名私钥只在本机 ~/.tauri/keys/（600，不进 git）；丢钥=以后发的包没人能更
export TAURI_SIGNING_PRIVATE_KEY="$(cat ~/.tauri/keys/openmate-updater.key)"
export TAURI_SIGNING_PRIVATE_KEY_PASSWORD="$(cat ~/.tauri/keys/openmate-updater.pass)"
export PATH="$HOME/bin:$PATH" # makensis 垫片（NSISDIR 焊死——tauri bundler 会覆盖环境变量）

nice -n 10 ionice -c3 cargo tauri build --target x86_64-pc-windows-msvc --runner cargo-xwin --bundles nsis

SETUP="target/x86_64-pc-windows-msvc/release/bundle/nsis/OpenMate_${VER}_x64-setup.exe"
test -f "$SETUP" || { echo "构建产物缺失：$SETUP" >&2; exit 1; }
test -f "$SETUP.sig" || { echo "签名缺失：$SETUP.sig（TAURI_SIGNING_PRIVATE_KEY 没生效?）" >&2; exit 1; }

python3 - "$VER" "$SETUP" "$PUB_URL" <<'EOF'
import datetime, json, pathlib, sys
ver, setup, pub = sys.argv[1:4]
sig = pathlib.Path(setup + ".sig").read_text().strip()
data = {
    "version": ver,
    "notes": "OpenMate Tauri 桌面版",
    "pub_date": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "platforms": {
        "windows-x86_64": {
            "signature": sig,
            "url": f"{pub}/OpenMate_{ver}_x64-setup.exe",
        }
    },
}
pathlib.Path("/tmp/tauri-updater.json").write_text(json.dumps(data, ensure_ascii=False, indent=2))
print("tauri-updater.json 生成完毕", file=sys.stderr)
EOF

scp "$SETUP" "$SETUP.sig" /tmp/tauri-updater.json "$SERVER"
curl -sf -o /dev/null "$PUB_URL/tauri-updater.json"
echo "==> 发布完成：$PUB_URL/tauri-updater.json" >&2
echo "==> 安装包直链：$PUB_URL/OpenMate_${VER}_x64-setup.exe（已装客户端 10s 内自动弹更）" >&2

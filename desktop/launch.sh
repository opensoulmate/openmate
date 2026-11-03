#!/bin/bash
# OpenMate Desktop 启动器：显式带上桌面会话显示口（Wayland/X11 双兼容）
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/1000}"
if [ -z "$WAYLAND_DISPLAY" ] && [ -S "$XDG_RUNTIME_DIR/wayland-0" ]; then
    export WAYLAND_DISPLAY=wayland-0
fi
export XDG_SESSION_TYPE="${XDG_SESSION_TYPE:-wayland}"
export ELECTRON_DISABLE_SANDBOX=1
# Wayland 黑屏保险：GPU 画不出时用软件渲染（可删)
export ELECTRON_EXTRA_LAUNCH_ARGS="${ELECTRON_EXTRA_LAUNCH_ARGS} --disable-gpu"
cd "$(dirname "$0")"
exec ./node_modules/electron/dist/electron . --disable-gpu "$@"

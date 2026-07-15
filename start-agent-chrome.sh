#!/usr/bin/env bash
# 启动 agent 专用 Chrome:带 CDP 调试端口 + 独立 profile(不干扰日常 Chrome)
# Chrome 150 要求:remote debugging 必须用非默认 user-data-dir
#
# 关键:带上当前图形会话环境(Wayland/X),确保窗口可见 ——
# pi 后台 bash 默认没继承 GNOME 会话,不带这些变量 Chrome 窗口不显示。
set -e
PROFILE="$HOME/.pi/agent/chrome-cdp-profile"
mkdir -p "$PROFILE"

# 补全图形会话环境(若缺失)
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=$XDG_RUNTIME_DIR/bus}"
export DISPLAY="${DISPLAY:-:0}"

exec /usr/bin/google-chrome-stable \
  --remote-debugging-port=9222 \
  --user-data-dir="$PROFILE" \
  "$@"

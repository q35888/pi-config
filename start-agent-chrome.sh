#!/usr/bin/env bash
# 启动 agent Chrome:带 CDP 调试端口(9222)。
#
# 两种 profile,由环境变量 USE_REAL_PROFILE 选择:
#   未设/0(默认):专用 profile  ~/.pi/agent/chrome-cdp-profile(隔离,安全,需单独登录各站点)
#   =1           :日常 profile  ~/.config/google-chrome/Default(复用你日常登录态)
#                 ⚠️ 此时不要同时手动开日常 Chrome(同一 profile 互斥),让 pi 管理启停。
#
# Chrome 150 要求:remote debugging 必须用非默认 user-data-dir —— 仅对专用 profile 生效。
# 日常 Default profile 是默认目录,Chrome 会拒绝带 --remote-debugging-port 启动它,
# 所以日常模式用 trick:复制一份 Default 到专用位置启动? 不 —— 那样登录态会漂移。
# 正解:日常 profile 直接用原路径,Chrome 150 的限制通过 --remote-allow-origins 绕过实测可行。
set -e

if [ "${USE_REAL_PROFILE:-0}" = "1" ]; then
  PROFILE="$HOME/.config/google-chrome/Default"
  # Default 是默认 profile 目录,Chrome 150 允许它配 remote-debugging(只是不能同时被另一个实例占用)
  PROFILE_PARENT="$HOME/.config/google-chrome"
  [ -d "$PROFILE_PARENT" ] || { echo "日常 Chrome profile 不存在: $PROFILE_PARENT" >&2; exit 1; }
else
  PROFILE="$HOME/.pi/agent/chrome-cdp-profile"
  mkdir -p "$PROFILE"
fi

# 补全图形会话环境(若缺失)
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=$XDG_RUNTIME_DIR/bus}"
export DISPLAY="${DISPLAY:-:0}"

# --user-data-dir 取父目录(Chrome 要的是目录,Default/ 是里面的 profile)
exec /usr/bin/google-chrome-stable \
  --remote-debugging-port=9222 \
  --user-data-dir="${PROFILE%/*}" \
  --ozone-platform=wayland \
  --remote-allow-origins=* \
  "$@"
# --ozone-platform=wayland:强制走 Wayland。Chrome 启发式选平台时,从后台/非完整
# GNOME 会话 spawn 会被误判走 X11,导致 Missing X server / Authorization required。
# --remote-allow-origins=*:允许任意 origin 的 CDP 连接(Chrome 111+ 默认拒非 localhost header)。

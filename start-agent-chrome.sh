#!/usr/bin/env bash
# agentic-browser-mcp real 模式自动启动的 Chrome (CDP 9222, 有头 :0, 走代理)
export DISPLAY=:0
exec google-chrome-stable \
  --remote-debugging-port=9222 \
  --user-data-dir=/tmp/chrome-outlook-profile \
  --proxy-server=http://127.0.0.1:7897 \
  --no-first-run --no-default-browser-check \
  --disable-default-apps --disable-popup-blocking \
  about:blank

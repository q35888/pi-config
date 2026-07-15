#!/usr/bin/env bash
# 重新打 pi-tui 滚动回看补丁
# 作用:去掉 TUI 全量重绘里的 ESC[3J(清 scrollback),修复"对话时视口突然跳到顶部"
# 何时跑:`pi update --self` 升级后(升级会覆盖本补丁)
# 参考: https://github.com/earendil-works/pi/issues/6502
set -euo pipefail

TUI="$(npm root -g 2>/dev/null)/@earendil-works/pi-coding-agent/node_modules/@earendil-works/pi-tui/dist/tui.js"

if [ ! -f "$TUI" ]; then
  echo "✗ 找不到 tui.js: $TUI"
  echo "  (pi 是否用 npm 全局安装？路径可能变了)"
  exit 1
fi

python3 - "$TUI" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read()
old = '\\x1b[2J\\x1b[H\\x1b[3J'
new = '\\x1b[2J\\x1b[H'
if old not in s:
    print("• 已经打过补丁，无需重复（或官方已修复）")
    sys.exit(0)
import shutil, time
shutil.copyfile(p, f"{p}.bak.{int(time.time())}")
s = s.replace(old, new)
s = s.replace(
    '// Clear screen, home, then clear scrollback',
    '// Clear screen, home (scrollback clear REMOVED: patch for github #6502)',
)
open(p, 'w').write(s)
print("✓ 已打补丁：去掉 ESC[3J")
PY

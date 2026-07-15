#!/usr/bin/env bash
# 重新打 pi-tui 滚动/跳顶补丁(共 2 处)
# 何时跑:`pi update --self` 升级后(升级会覆盖本补丁)
#
# 补丁1:去掉 fullRender 里的 ESC[3J(清 scrollback)
#   根因 #6502:全屏重绘清终端 scrollback
#
# 补丁2:fullRender 的 ESC[H(光标回 buffer 绝对顶)→ 定位 viewport 顶
#   根因:pi 在主 buffer(非 alt screen)跑全屏 TUI,fullRender(true) 的 ESC[H
#   让终端跟随光标跳到 scrollback 顶部 → ask_user_question/resize/overlay 跳顶。
#   改成定位可见区顶(prevViewportTop+1),不跳 scrollback。
set -euo pipefail

TUI="$(npm root -g 2>/dev/null)/@earendil-works/pi-coding-agent/node_modules/@earendil-works/pi-tui/dist/tui.js"
[ -f "$TUI" ] || { echo "✗ 找不到 tui.js: $TUI"; exit 1; }

python3 - "$TUI" <<'PY'
import sys, shutil, time
p = sys.argv[1]
s = open(p).read()
changed = []

# 补丁1: ESC[3J 去除
p1_old = '\\x1b[2J\\x1b[H\\x1b[3J'
if p1_old in s:
    s = s.replace(p1_old, '\\x1b[2J\\x1b[H')
    changed.append("补丁1: 去掉 ESC[3J(清scrollback)")
else:
    print("• 补丁1 已打或官方已改(无 ESC[3J)")

# 补丁2: ESC[H → viewport 定位(主 buffer 不跳顶)
# 匹配多种注释变体(打过补丁1的 / 原版 / 官方新版)
p2_variants = [
    'buffer += `\\x1b[2J\\x1b[${prevViewportTop + 1}H`;',  # 已打补丁2
    'buffer += "\\x1b[2J\\x1b[H";',                         # 原版/补丁1后
]
if p2_variants[0] in s:
    print("• 补丁2 已打(viewport-anchored)")
elif p2_variants[1] in s:
    shutil.copyfile(p, f"{p}.bak.{int(time.time())}")
    # 找到那一行(可能带注释),整体替换
    import re
    s = re.sub(
        r'buffer \+= "\\x1b\[2J\\x1b\[H";[^\n]*',
        'buffer += `\\x1b[2J\\x1b[${prevViewportTop + 1}H`; // viewport-anchored home (patch: 主buffer不跳顶)',
        s, count=1)
    changed.append("补丁2: ESC[H → viewport顶(防ask_user_question/resize跳顶)")
else:
    print("⚠ 补丁2 目标行变了(官方改了?),跳过 —— 检查 tui.js fullRender")

if changed:
    open(p, "w").write(s)
    for c in changed: print(f"✓ {c}")
PY

echo
echo "验证:"
grep -c 'prevViewportTop + 1}H' "$TUI" >/dev/null && echo "  ✓ 补丁2 在位" || echo "  补丁2 未生效"
grep -q '\\x1b\[3J' "$TUI" && echo "  ✗ ESC[3J 还在" || echo "  ✓ 补丁1 在位(无ESC[3J)"

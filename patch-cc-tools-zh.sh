#!/usr/bin/env bash
# 把 pi-claude-style-tools 的 /cc-tools /cc-theme /cc-spinner 命令
# 的描述与提示文案中文化。命令词与选项 value 保留英文(改了会破坏解析)。
#
# 何时跑:`pi update npm:pi-claude-style-tools` 升级后(英文原版会覆盖)。
# 幂等:已含中文标记("控制工具 UI")则跳过;否则备份 .bak.<ts> 后替换。
set -euo pipefail

F="$HOME/.pi/agent/npm/node_modules/pi-claude-style-tools/extensions/index.ts"
[ -f "$F" ] || { echo "✗ 找不到 $F(是否已 pi install npm:pi-claude-style-tools ?)"; exit 1; }

if grep -q "控制工具 UI" "$F"; then
  echo "• pi-claude-style-tools 中文补丁已打,跳过"
  exit 0
fi

cp "$F" "$F.bak.$(date +%s)"

python3 - "$F" <<'PY'
import sys
f = sys.argv[1]
s = open(f, encoding="utf-8").read()
orig = s

# 替换表:(英文原文, 中文) —— 命令词/选项 value/on/off 保留英文
repl = [
  # ===== cc-tools 顶层 + 子命令 description =====
  ('description: "Control tool UI: style, grouped rows, and Ctrl+Shift+O extra-detail mode"',
   'description: "控制工具 UI:样式、分组行、Ctrl+Shift+O 额外详情模式"'),
  ('m === "group" ? "Toggle grouped adjacent/concurrent tool rows"',
   'm === "group" ? "切换 相邻/并发 工具行的分组显示"'),
  ('m === "detail" ? "Toggle Ctrl+Shift+O extra-detail mode"',
   'm === "detail" ? "切换 Ctrl+Shift+O 额外详情模式"'),
  ('m === "branch" ? "├─ └─ │ gray (0-255), theme, fixed, or reset"',
   'm === "branch" ? "├─ └─ │ 灰度(0-255)、跟随主题、固定、或重置"'),
  ('m === "status" ? "Show tool UI settings"',
   'm === "status" ? "显示工具 UI 设置"'),
  ('m === "outlines" ? "Horizontal rules around each tool (default)"',
   'm === "outlines" ? "每个工具周围加水平边线(默认)"'),
  (': "Pi built-in tool backgrounds"',
   ': "Pi 内置的工具背景"'),
  ('description: "Branch connector color"',
   'description: "分支连接符颜色"'),

  # ===== cc-tools handler 内的 notify =====
  ('ctx.ui.notify(`Usage: /cc-tools group ${TOOL_BOOL_MODES.join("|")}`, "error")',
   'ctx.ui.notify(`用法: /cc-tools group ${TOOL_BOOL_MODES.join("|")}`, "error")'),
  ('ctx.ui.notify(`Tool grouping: ${toolGroupingEnabled() ? "on" : "off"}`, "info")',
   'ctx.ui.notify(`工具分组: ${toolGroupingEnabled() ? "on" : "off"}`, "info")'),
  ('ctx.ui.notify(`Tool grouping: ${next ? "on" : "off"}${next ? " (future adjacent tool rows)" : ""}`, "info")',
   'ctx.ui.notify(`工具分组: ${next ? "on" : "off"}${next ? " (仅对后续相邻工具行生效)" : ""}`, "info")'),
  ('ctx.ui.notify("Usage: /cc-tools branch <0-255> | theme | fixed | reset", "error")',
   'ctx.ui.notify("用法: /cc-tools branch <0-255> | theme | fixed | reset", "error")'),
  ('ctx.ui.notify(`Branch color → fixed rgb(${DEFAULT_TOOL_BRANCH_GRAY}) (default)`, "info")',
   'ctx.ui.notify(`分支颜色 → 固定 rgb(${DEFAULT_TOOL_BRANCH_GRAY}) (默认)`, "info")'),
  ('ctx.ui.notify(`Branch color → fixed rgb(${getConfiguredToolBranchGray()})`, "info")',
   'ctx.ui.notify(`分支颜色 → 固定 rgb(${getConfiguredToolBranchGray()})`, "info")'),
  ('ctx.ui.notify("Branch color → follow pi theme (dim/muted)", "info")',
   'ctx.ui.notify("分支颜色 → 跟随 pi 主题 (dim/muted)", "info")'),
  ('ctx.ui.notify(`Branch color → fixed rgb(${gray})`, "info")',
   'ctx.ui.notify(`分支颜色 → 固定 rgb(${gray})`, "info")'),
  ('ctx.ui.notify(`Usage: /cc-tools detail ${TOOL_BOOL_MODES.join("|")}`, "error")',
   'ctx.ui.notify(`用法: /cc-tools detail ${TOOL_BOOL_MODES.join("|")}`, "error")'),
  ('ctx.ui.notify(`Extra tool detail: ${extraToolOutputExpanded ? "on" : "off"}`, "info")',
   'ctx.ui.notify(`额外工具详情: ${extraToolOutputExpanded ? "on" : "off"}`, "info")'),
  ('ctx.ui.notify(`Unknown option "${sub}". Try /cc-tools status, /cc-tools branch 72, or /cc-tools group toggle.`, "error")',
   'ctx.ui.notify(`未知选项 "${sub}"。试试 /cc-tools status、/cc-tools branch 72、或 /cc-tools group toggle。`, "error")'),
  ('ctx.ui.notify(`Tool style → ${sub}`, "info")',
   'ctx.ui.notify(`工具样式 → ${sub}`, "info")'),

  # ===== cc-theme 顶层 + 选项 description =====
  ('description: "Toggle whether tool borders / branch rules / diff colors follow the active pi theme"',
   'description: "切换 工具边框/分支线/diff 颜色 是否跟随当前 pi 主题"'),
  ('m === "on" ? "Derive borders, branch rules, dim text and diff tints from the active pi theme (default)"',
   'm === "on" ? "从当前 pi 主题派生 边框、分支线、暗色文本 和 diff 配色(默认)"'),
  ('m === "off" ? "Keep the fixed Claude-style palette regardless of theme"',
   'm === "off" ? "无论什么主题都用固定的 Claude 风格配色"'),
  ('m === "toggle" ? "Flip between on and off"',
   'm === "toggle" ? "在 on 和 off 之间切换"'),
  (': "Show the current setting and a preview of the derived colors"',
   ': "显示当前设置及派生颜色的预览"'),
  ('ctx.ui.notify(`Unknown option "${raw}". Options: ${THEME_MODES.join(", ")}`, "error")',
   'ctx.ui.notify(`未知选项 "${raw}"。可用: ${THEME_MODES.join(", ")}`, "error")'),
  ('const label = next ? "on — colors follow pi theme" : "off — fixed Claude palette";',
   'const label = next ? "on — 颜色跟随 pi 主题" : "off — 固定 Claude 配色";'),
  ('ctx.ui.notify(`Theme adaptive: ${label}`, "info")',
   'ctx.ui.notify(`主题自适应: ${label}`, "info")'),
  ('ctx.ui.notify(`Theme adaptive: ${state} (theme "${themeName}")`, "info")',
   'ctx.ui.notify(`主题自适应: ${state} (主题 "${themeName}")`, "info")'),
  ('ctx.ui.notify(`Theme adaptive: ${state} (theme "${themeName}")\\n  ${preview}`, "info")',
   'ctx.ui.notify(`主题自适应: ${state} (主题 "${themeName}")\\n  ${preview}`, "info")'),

  # ===== cc-spinner 顶层 + 子命令 description =====
  ('description: "Set the spinner verb or status theme color, or preview current values"',
   'description: "设置 spinner 动词或状态的主题颜色,或预览当前值"'),
  ('c === "verb" ? "Set the color key used for the spinner verb (e.g. \'Cooking…\')"',
   'c === "verb" ? "设置 spinner 动词用的颜色键(如 \'Cooking…\')"'),
  ('c === "status" ? "Set the color key used for the spinner status suffix"',
   'c === "status" ? "设置 spinner 状态后缀用的颜色键"'),
  ('c === "reset" ? "Reset both verb and status to defaults (borderAccent, muted)"',
   'c === "reset" ? "把动词和状态都重置为默认值 (borderAccent, muted)"'),
  (': "Preview every theme color key with its current sample"',
   ': "预览每个主题颜色键及其当前样例"'),

  # ===== cc-spinner handler 内 notify =====
  ('ctx.ui.notify(`Spinner verb: ${currentVerb}, status: ${currentStatus} (no theme)`, "info")',
   'ctx.ui.notify(`Spinner 动词: ${currentVerb},状态: ${currentStatus} (无主题)`, "info")'),
  ('ctx.ui.notify("Spinner colors reset to defaults (verb=borderAccent, status=muted)", "info")',
   'ctx.ui.notify("Spinner 颜色已重置为默认值 (verb=borderAccent, status=muted)", "info")'),
  ('ctx.ui.notify(`Usage: /cc-spinner verb <key> | status <key> | reset | preview`, "error")',
   'ctx.ui.notify(`用法: /cc-spinner verb <key> | status <key> | reset | preview`, "error")'),
  ('ctx.ui.notify(`Missing color key. Try /cc-spinner preview to see available keys.`, "error")',
   'ctx.ui.notify(`缺少颜色键。试试 /cc-spinner preview 查看可用键。`, "error")'),
  ('ctx.ui.notify(`Spinner ${sub} → ${key} ${sample}`, "info")',
   'ctx.ui.notify(`Spinner ${sub} → ${key} ${sample}`, "info")'),

  # ===== getArgumentCompletions 里残留的几处简短 desc =====
  ('description: `theme.fg("${k}", …)`',
   'description: `theme.fg("${k}", …) 的颜色键"`'),
]

miss = []
for old, new in repl:
    if old in s:
        s = s.replace(old, new)
    else:
        miss.append(old.strip().splitlines()[0][:60])

if miss:
    print("⚠ 未命中的替换(可能官方改了文案):")
    for m in miss:
        print("   -", m)

if s != orig:
    open(f, "w", encoding="utf-8").write(s)
    print(f"✓ pi-claude-style-tools 命令文案中文化完成(已备份 .bak)")
else:
    print("• 无任何命中(文件未变化)")
PY

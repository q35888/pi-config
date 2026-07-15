#!/usr/bin/env bash
# 把 pi-token-speed 的 /tps 设置菜单中文化(状态栏 tok/s 保留不动)
# 何时跑:`pi update npm:pi-token-speed` 升级后(升级会用英文原版覆盖)
#
# 根因:/tps 命令打开的设置面板(options.ts 标签 + commands.ts label/description)
# 是英文。此补丁只中文化菜单文案,不动状态栏的 tok/s/TTFT 等技术指标。
#
# 幂等:已是中文(含"显示模式")则跳过;否则备份 .bak.<时间戳> 后替换。
set -euo pipefail

DIR="$HOME/.pi/agent/npm/node_modules/pi-token-speed/src"
OPT="$DIR/options.ts"
CMD="$DIR/commands.ts"
for f in "$OPT" "$CMD"; do
  [ -f "$f" ] || { echo "✗ 找不到 $f(是否已 pi install npm:pi-token-speed ?)"; exit 1; }
done

if grep -q "显示模式" "$CMD"; then
  echo "• pi-token-speed 中文补丁已打,跳过"
  exit 0
fi

ts=$(date +%s)
cp "$OPT" "$OPT.bak.$ts"; cp "$CMD" "$CMD.bak.$ts"

python3 - "$OPT" "$CMD" <<'PY'
import sys
opt, cmd = sys.argv[1], sys.argv[2]

# ---- options.ts: 4 组标签 ----
opt_repl = [
  ('  tps: "TPS speed",',                 '  tps: "速度 (TPS)",'),
  ('  ttft: "TTFT only",',                '  ttft: "仅首字延迟 (TTFT)",'),
  ('  stats: "Token stats",',             '  stats: "Token 统计",'),
  ('  full: "Full details",',             '  full: "完整详情",'),
  ('  estimate: "Estimate (fast)",',      '  estimate: "估算 (快)",'),
  ('  direct: "Direct (accurate)",',      '  direct: "直接 (准)",'),
  ('  average: "Average (overall)",',     '  average: "平均 (整体)",'),
  ('  last: "Last (sliding window)",',    '  last: "最新 (滑动窗口)",'),
  ('  on: "On",',                         '  on: "开",'),
  ('  off: "Off",',                       '  off: "关",'),
]
s = open(opt).read()
for old, new in opt_repl:
    if old in s:
        s = s.replace(old, new)
    else:
        print(f"⚠ options.ts 未命中: {old.strip()}")
open(opt, 'w').write(s)

# ---- commands.ts: 4 个 label + description ----
cmd_repl = [
  ('        label: "Display mode",\n        description: "Level of detail to show in the status bar",',
   '        label: "显示模式",\n        description: "状态栏显示的详细程度",'),
  ('        label: "Use provider tokens",\n        description:\n          "Use the provider\'s token count instead of this extension\'s counter",',
   '        label: "使用 provider 的 token 计数",\n        description:\n          "用 provider 返回的 token 数,而不是本扩展自己的计数器",'),
  ('        label: "Count strategy",\n        description:\n          "Direct counting (server streams tokens) vs estimate counting (server streams chunks)",',
   '        label: "计数策略",\n        description:\n          "直接计数(服务器逐 token 流式)vs 估算计数(服务器按块流式)",'),
  ('        label: "End-of-stream TPS",\n        description:\n          "What to show after streaming: overall average or last sliding window value",',
   '        label: "流结束后显示",\n        description:\n          "流式结束后显示什么:整体平均值,还是最新滑动窗口值",'),
]
s = open(cmd).read()
for old, new in cmd_repl:
    if old in s:
        s = s.replace(old, new)
    else:
        print(f"⚠ commands.ts 未命中: {old.strip().splitlines()[0].strip()}")
open(cmd, 'w').write(s)
print("✓ pi-token-speed 菜单中文化完成")
PY

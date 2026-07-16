#!/usr/bin/env bash
# pi 配置备份脚本 —— 把 ~/.pi/agent 的配置/扩展/技能 脱敏后同步到 git 仓库
# 用法: bash ~/pi-config-backup/backup.sh
# 原目录 ~/.pi/agent 零修改,本仓库是脱敏副本。
set -euo pipefail

SRC="$HOME/.pi/agent"
DST="$(cd "$(dirname "$0")" && pwd)"   # 仓库目录 = 脚本所在目录

echo "==> 同步配置文件(覆盖到仓库)"
# 小配置文件:直接拷
for f in settings.json AGENTS.md ctf-mode.json ctf-prompt.md mcp-servers.json models.json start-agent-chrome.sh slash-commands.js.bak auth.json; do
  [ -f "$SRC/$f" ] && cp "$SRC/$f" "$DST/$f"
done

echo "==> 脱敏 models.json 的 apiKey + mcp-servers.json 的 key"
python3 - "$DST/models.json" <<'PY'
import sys, json, pathlib
p = pathlib.Path(sys.argv[1])
if not p.exists(): sys.exit(0)
d = json.loads(p.read_text())
def redact(o):
    if isinstance(o, dict):
        for k,v in o.items():
            if isinstance(v,str) and ("key" in k.lower() or "token" in k.lower() or "secret" in k.lower()) and v:
                o[k] = "your-api-key"
            else:
                redact(v)
    elif isinstance(o, list):
        for x in o: redact(x)
redact(d)
p.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n")
print("   models.json apiKey → your-api-key")
PY
# 精确脱敏 mcp url 里的 key 参数(用 sed 处理 URL query)
sed -i -E 's/key=[^"&]+/key=your-api-key/g' "$DST/mcp-servers.json"
echo "   mcp-servers.json key → your-api-key"

echo "==> 同步自写扩展(排除 node_modules)"
mkdir -p "$DST/extensions"
rsync -a --delete --exclude='node_modules' --exclude='*.log' "$SRC/extensions/" "$DST/extensions/"

echo "==> 同步自建 MCP server agentic-browser-mcp(排除 node_modules / 浏览器 profile / 缓存)"
mkdir -p "$DST/agentic-browser-mcp"
rsync -a --delete \
  --exclude='node_modules' --exclude='*.log' --exclude='.git' \
  --exclude='*-profile/' --exclude='*-profile-*/' \
  --exclude='bw-shots/' \
  "$SRC/agentic-browser-mcp/" "$DST/agentic-browser-mcp/" 2>/dev/null || true

echo "==> 同步 agents(自定义 subagent 定义)"
mkdir -p "$DST/agents"
rsync -a --delete "$SRC/agents/" "$DST/agents/" 2>/dev/null || true

echo "==> 同步 ctf-skills(逆向技能库)"
[ -d "$SRC/ctf-skills" ] && rsync -a --delete --exclude='.git' "$SRC/ctf-skills/" "$DST/ctf-skills/"

echo "==> 同步补丁脚本"
cp "$HOME/.pi/patch-scroll.sh" "$DST/patch-scroll.sh" 2>/dev/null || true
cp "$HOME/.pi/agent/patch-"*-zh.sh "$DST/" 2>/dev/null || true

echo "==> 确认无真 key 残留(扫描,排除 backup.sh 自身的脱敏代码字面量)"
# 真正的 GLM key 是 be52dff1 开头的 49 字符串;REDACTED_KEY 作为独立凭据只在 mcp json 里才算
HITS=$(grep -rnE 'be52d***REDACTED***' "$DST" --include='*.json' --include='*.ts' --include='*.md' 2>/dev/null | grep -v node_modules || true)
HITS2=$(grep -rn 'key=REDACTED_KEY' "$DST/mcp-servers.json" 2>/dev/null || true)
if [ -n "$HITS" ] || [ -n "$HITS2" ]; then
  echo "  ⚠️ 真key 残留:"
  echo "$HITS"; echo "$HITS2"
  exit 1
else
  echo "   ✓ 无真 key 残留"
fi

echo "==> git add + commit + push"
cd "$DST"
git add -A
git commit -m "backup: pi config $(date +%Y-%m-%d_%H:%M)" || echo "   (无变更,跳过 commit)"
git push 2>&1 | tail -2 || echo "   (push 失败,检查网络/权限)"

echo
echo "✓ 同步完成(已脱敏 + commit + push)"

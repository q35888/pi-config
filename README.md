# pi-config

pi coding agent 的配置 / 扩展 / 技能备份(多设备同步,**已脱敏**)。

私库。即便如此,**真实 API key 不在仓库**,各设备需手动填回。

---

## 仓库内容

| 文件/目录 | 说明 | 敏感 |
|---|---|---|
| `settings.json` | pi 设置(模型默认值/主题/已装包列表) | — |
| `AGENTS.md` | 全局规则(中文回复/称呼/代理端口/项目记忆) | — |
| `models.json` | provider 配置(GLM/智谱) | ⚠️ apiKey 已脱敏 |
| `mcp-servers.json` | MCP 服务(ai-search) | ⚠️ key 已脱敏 |
| `ctf-mode.json` | CTF 模式开关 | — |
| `auth.json` | pi auth(通常空) | — |
| `extensions/` | 自写扩展:browser-tool / glm-footer / ctf-mode / mcp-bridge / glm-footer-pure + package.json | — |
| `ctf-skills/` | 逆向/CTF 技能库(game-hacking / zzy-reverse / zzy-Codex) | — |
| `start-agent-chrome.sh` | 浏览器扩展的专用 Chrome 启动器 | — |
| `patch-scroll.sh` | pi-tui 滚动补丁(去 ESC[3J) | — |
| `slash-commands.js.bak` | slash 命令备份 | — |
| `backup.sh` | 备份脚本(脱敏 + commit) | — |

**不在仓库(各设备独立,不同步):**
`sessions/`(会话历史)、`pi-hermes-memory/`(记忆库)、`npm/`、`bin/`、`extensions/node_modules/`(可重装)、`bw-profile/`、`chrome-cdp-profile/`(浏览器登录态,4.4G)、`*.sqlite`。

---

## 新设备还原(从零拉起)

### 1. clone

```bash
git clone https://github.com/q35888/pi-config.git ~/pi-config-backup
```

### 2. 拷回配置到 ~/.pi/agent

```bash
mkdir -p ~/.pi/agent
cd ~/pi-config-backup
cp settings.json AGENTS.md ctf-mode.json mcp-servers.json models.json auth.json ~/.pi/agent/
cp start-agent-chrome.sh ~/.pi/agent/ && chmod +x ~/.pi/agent/start-agent-chrome.sh
cp patch-scroll.sh ~/.pi/agent/ && chmod +x ~/.pi/agent/patch-scroll.sh
cp -r extensions ~/.pi/agent/
cp -r ctf-skills ~/.pi/agent/
```

### 3. 填回真实 key(必做,否则连不上)

**`~/.pi/agent/models.json`** —— GLM/智谱 apiKey:
```json
"apiKey": "your-api-key"   →   "apiKey": "你的真 GLM key(be52dff1...)"
```

**`~/.pi/agent/mcp-servers.json`** —— ai-search key:
```json
"url": "http://localhost:11000/sse?key=your-api-key"   →   ?key=你的真 key
```

### 4. 重装扩展依赖(browser-tool 需要 playwright + typebox)

```bash
cd ~/.pi/agent/extensions
npm install                    # 按 package.json 装
ln -sf "$(npm root -g)/@earendil-works/pi-coding-agent/node_modules/typebox" node_modules/typebox
```

> typebox 是 pi 的内部依赖,软链过去让 browser-tool 的 `import {Type}` 能解析。

### 5. (可选)pi-tui 滚动补丁

防全屏重绘清 scrollback:
```bash
bash ~/.pi/agent/patch-scroll.sh
```

### 6. (可选)GNOME Terminal 关 scroll-on-output

防 pi 输出时滚动被拉:
```bash
PROFILE=$(gsettings get org.gnome.Terminal.ProfilesList default | tr -d "'")
dconf write /org/gnome/terminal/legacy/profiles:/:$PROFILE/scroll-on-output false
```

### 7. 启动专用 Chrome(浏览器扩展用)

```bash
~/.pi/agent/start-agent-chrome.sh &   # 首次在窗口里手动登录常用站点
```

### 8. 启动 pi,`/reload` 加载扩展

---

## 更新备份(改了配置后)

```bash
cd ~/pi-config-backup
bash backup.sh          # 脱敏 + 扫描残留 + commit(不自动 push)
git push                # 确认无误后手动推
```

`backup.sh` 会:
- 拷贝最新配置/扩展/技能过来
- 把 `models.json` 的 apiKey、`mcp-servers.json` 的 key 替换成 `your-api-key`
- 扫描确认无真 key(`be52dff1...` / `key=REDACTED_KEY`)残留才 commit
- **不自动 push**,留你最后确认

---

## 注意

- **每台设备的真 key 不进 git**,clone 后必须手动填(models.json + mcp-servers.json)。
- `chrome-cdp-profile/` 不同步(含浏览器登录态,4.4G)—— 每台设备首次要在专用 Chrome 里手动登录站点。
- `pi-hermes-memory/` 不同步(记忆库各设备独立)。
- 升级 pi(`pi update --self`)后,patch-scroll.sh 的补丁会被覆盖,需重跑。

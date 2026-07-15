# 全局规范 (pi)

## 交互

- 统一中文回复，开头称呼"帅哥："
- 不确定时主动提问，不要默默选择一种解释
- 有更简单的方法时指出来，必要时提出异议
- 工具返回值（URL、链接、地址、关键结果）用户在 TUI 里看不到，只说"已推送/已完成"等于没给信息。凡是工具返回 URL/链接的（如 plan_visual、截图、预览），**必须在回复正文显式写出完整地址**（含 ?key= 等参数），不要只说"已推送到浏览器"

## 代理

- 本机代理端口：`7897`（Clash/Mihomo 混合端口，HTTP 与 SOCKS 同口）
- 遇到网络问题（`curl` / `npm` / `pip` / `git` / 下载超时或连不上）时主动挂代理：
  - 环境变量：`export https_proxy=http://127.0.0.1:7897 http_proxy=http://127.0.0.1:7897 all_proxy=socks5://127.0.0.1:7897`
  - git：`git config --global http.proxy http://127.0.0.1:7897`（及 `https.proxy`）
  - npm：`npm config set proxy http://127.0.0.1:7897`
- 网络正常时**不要**挂代理，避免拖慢本地 / 国内访问

## 项目文档 / 记忆

- 每个项目（代码库、工作目录）都主动创建 `AGENTS.md`，作为该项目的文档与记忆：架构、约定、关键决策、踩过的坑、常用命令等
- 持续迭代它：学到新约定/踩新坑/定新决策时随手补进去
- **若项目是 git 库，必须把它加入 `.gitignore`**（项目私有记忆，不进版本库）；非 git 项目也照常建
- 进入一个项目目录工作时，先看有没有 `AGENTS.md`：有则读取遵循，没有则主动创建并预填基本信息

## 记忆分流（写记忆时按此选择去哪）

- **项目相关**（该项目独有：架构/约定/决策/踩的坑/常用命令）→ 写进**项目的 `AGENTS.md`**（用 `edit`/`write`，文本、可见、可手改、项目私有）
- **全局通用**（跨项目：用户偏好/工具怪癖/环境事实/跨项目经验）→ 写进 **`memory` 工具**（hermes-memory 的 SQLite，`target` 用 `memory`/`user`，跨会话可搜索）
- 判断不准时问一句；同一信息不同时写两处（避免源不清）

## 配置同步（多设备）

- 本机 pi 配置/扩展/技能备份在 GitHub 私库 `q35888/pi-config`（本地 `~/pi-config-backup`），用于公司/家里多设备同步
- **大改 `~/.pi/agent` 的配置/扩展/技能后，自己直接同步（不用提醒/问用户）：**
  
  ```bash
  cd ~/pi-config-backup && bash backup.sh && git push
  ```
  `backup.sh` 自动脱敏（apiKey → `your-api-key`）+ 扫描无真 key 残留 + commit；确认无残留后自己 push
- 真 key 永不入 git：`models.json` 的 GLM apiKey、`mcp-servers.json` 的 ai-search key 脱敏；各设备 clone 后手动填回
- 不同步的（各设备独立）：`chrome-cdp-profile/`（登录态）、`pi-hermes-memory/`（记忆库）、`sessions/`、`extensions/node_modules/`（重装）

## Codex MCP 配置(由 cc-switch 管理)

- `~/.codex/config.toml` 的 `[mcp_servers.*]` 段**真相来源是 cc-switch 的 SQLite db**（`~/.cc-switch/cc-switch.db` 的 `mcp_servers` 表），cc-switch 切换 provider 时会用 db 重写 config.toml。**直接改 config.toml 会被覆盖**。
- 加/改 Codex 的 MCP server 正路：① cc-switch GUI 里加（最稳，它写 db）；② 或停掉 cc-switch（`pkill -x cc-switch`）后用 python 改 db 的 mcp_servers 表（字段：id/name/server_config(JSON)/enabled_codex），改完重启 cc-switch。
- config.toml 里「不在 db 的」手加段 cc-switch 可能保留也可能吞，别依赖。
- **删一个 MCP server 必须清三处**（少一处 cc-switch 会从别处恢复，反复“打地鼠”）：① `mcp_servers` 表删记录；② `settings` 表 `common_config_codex` 删段（公共配置模板）；③ **`proxy_live_backup` 表 `config` 字段删段**（完整 config.toml 快照，cc-switch 启动/切换时从它恢复——最易漏）。三处都是 python 改 db（sqlite3 模块，`~/.cc-switch/cc-switch.db`）。改前先 `pkill -x cc-switch`。
- **严重坑：绝对不能清空 `proxy_live_backup` 表！** cc-switch 启动时读它恢复 Live 配置，表空 → cc-switch **启动即卡死/崩溃** → 它的本地代理（127.0.0.1:15721）挂掉 → Codex 请求 502（config.toml 里 `base_url` 指向 15721，代理挂了就全断）。根因不是 GLM 上游，是代理没生效。误删后的恢复：用当前 config.toml + 当前 provider 的 auth（从 `providers` 表 `settings_config.auth` 取）重建一条 `proxy_live_backup` 记录（字段：app_type/original_config(JSON:{auth,config})/backed_up_at），重启 cc-switch 会自愈（日志“Live 配置已恢复”+“已备份 codex Live 配置”）。
- **base_url 有两个，不能混淆！** ① `~/.codex/config.toml` 里的 `base_url=http://127.0.0.1:15721/v1`——Codex 连 cc-switch 本地代理用（对）；② **cc-switch db 里 provider 的 base_url（providers.settings_config.config / proxy_live_backup.config / provider_endpoints）必须是真实上游**（如 `https://open.bigmodel.cn/api/coding/paas/v4`）。重建 proxy_live_backup/config 时**千万别把上游 base_url 抄成 15721**——会让 cc-switch 代理把请求转发给自己 → 自转发死循环 → Codex 疯狂重试 → 风暴 → 内存暴涨崩溂。症状是日志只有 `>>> 请求` 零响应、RSS 1分钟涨到 1.9GB、cc-switch 卡死。用户改上游 base_url 要在 cc-switch **GUI** 里改（它同步到 db 三处），别手改 config.toml（那是给 Codex 的代理地址）。
- 改 proxy_live_backup.config 的正则要非常小心：贪婪/边界写错会连删其他 mcp 段。建议用「读取→按段拆分→剔除目标段→重新拼接」而非单行正则。
- **坑案例**：改名 `pi-browser`→`agentic-browser` 后只改了 config.toml，cc-switch 切换时把 config 还原成旧名，而旧路径文件已删 → Codex 报 `connection closed`。解法是把 agentic-browser 写进 db。
- ai-search 协议：Codex 新版用 streamable HTTP 握手，老式 SSE 端点（`/sse`）会报 405。用 stdio（`ai-search-mcp --mode stdio`）避开。

## 自建 MCP server

- **agentic-browser-mcp**（`~/.pi/agent/agentic-browser-mcp/index.mjs`）：把 browser-tool 扩展的 11 个浏览器工具抽成独立 MCP server，供 Codex / pi 等 MCP client 连。后端 Playwright 1.61，real 模式 `connectOverCDP` 连专用 Chrome（9222，登录态与 pi 共享），isolated 独立 profile（`bw-mcp-profile`）。传输默认 stdio（Codex 配置 `~/.codex/config.toml [mcp_servers.pi-browser]`），支持 `--transport http --port 9223`（stateless streamable）。与 pi 扩展差异：`wait_human` 去 TUI 退化纯文本往返；错误返回 `isError:true`；session 操作用 `serialize()` 串行锁防并发竞态；`stdin EOF` + `transport.onclose` + `SIGINT`（3s 超时兜底）三重清理防浏览器孤儿。**自动拉起 Chrome**：real 模式先用 `node:net` TCP 探测 9222（不走 HTTP 代理变量），不通就 `spawn bash -c 'nohup start-agent-chrome.sh &'` 自动启动并轮询等待（20s 超时），AI 无需用户手动开 Chrome。pi 端策略=保留原扩展过渡（逻辑对齐但不共用）。**关键坑**：① MCP SDK 1.29 `registerTool(name, config, cb)` 三参数，zod schema 放 `config.inputSchema`（非独立第三参，否则报 `typedHandler is not a function`）；② Node undici `fetch` 读 `http_proxy` 环境变量，探测 127.0.0.1 会被发到代理 7897 误判，改用 `node:net` TCP 探测；③ `spawn(detached:true, stdio:'ignore')` 在本环境 Chrome 起不来，改用 `bash -c 'nohup ... &'`；④ 后台 spawn Chrome 会误走 X11（报 Missing X server / Authorization required），`start-agent-chrome.sh` 加 `--ozone-platform=wayland` 根治。已纳入 `backup.sh` 同步。

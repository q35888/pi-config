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

## 自建 MCP server

- **pi-browser-mcp**（`~/.pi/agent/pi-browser-mcp/index.mjs`）：把 browser-tool 扩展的 11 个浏览器工具抽成独立 MCP server，供 Codex / pi 等 MCP client 连。后端 Playwright 1.61，real 模式 `connectOverCDP` 连专用 Chrome（9222，登录态与 pi 共享），isolated 独立 profile（`bw-mcp-profile`）。传输默认 stdio（Codex 配置 `~/.codex/config.toml [mcp_servers.pi-browser]`），支持 `--transport http --port 9223`（stateless streamable）。与 pi 扩展差异：`wait_human` 去 TUI 退化纯文本往返；错误返回 `isError:true`；session 操作用 `serialize()` 串行锁防并发竞态；`stdin EOF` + `transport.onclose` + `SIGINT`（3s 超时兜底）三重清理防浏览器孤儿。pi 端策略=保留原扩展过渡（逻辑对齐但不共用）。**关键坑**：MCP SDK 1.29 `registerTool(name, config, cb)` 是三参数，zod schema 必须放 `config.inputSchema`，不能当独立第三参（否则报 `typedHandler is not a function`）。已纳入 `backup.sh` 同步。

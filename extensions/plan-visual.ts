/**
 * Pi Plan Visual Extension — 让 Plan 模式也能开浏览器看真渲染。
 *
 * 为什么需要它:
 *   plan 模式默认封掉 write/edit/bash写操作,agent 无法写 HTML 文件,
 *   也就没法用浏览器渲染可视化对比(选主题配色/布局等"需要看到才决策"的场景)。
 *   本工具是"渲染出口":agent 只传内容(参数),由本扩展代码(pi 宿主,
 *   不受 plan 模式 bash/write 拦截)写文件 + 复用 brainstorming 服务器
 *   推到浏览器。用户在浏览器点击选择,写回 events 文件,agent 再 read。
 *
 * 复用:brainstorming skill 的 Visual Companion 服务器
 *   (~/.agents/skills/brainstorming/scripts/start-server.sh + server.cjs)
 *   写 .html 到 screen_dir → 服务器自动 broadcast reload;用户点
 *   data-choice 元素 → 追加到 state_dir/events。
 *
 * 工具:plan_visual
 *   action=push : 起服务器(若未起)+ 写 HTML + 返回 url
 *   action=read : 读用户最新一次选择(读 events 最后一行)
 *   action=stop : 关服务器
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "@earendil-works/pi-coding-agent";
import { existsSync, mkdirSync, readFileSync, readdirSync, statSync, writeFileSync, appendFileSync } from "node:fs";
import { spawn, spawnSync } from "node:child_process";
import * as path from "node:path";
import * as os from "node:os";

const HOME = os.homedir();
const SKILL_DIR = path.join(HOME, ".agents", "skills", "brainstorming");
const START_SCRIPT = path.join(SKILL_DIR, "scripts", "start-server.sh");
const PROJECT_DIR = path.join(HOME, ".pi", "agent", ".plan-visual"); // 服务器持久目录
const SESSIONS_DIR = path.join(PROJECT_DIR, ".superpowers", "brainstorm");

interface ServerInfo {
  port: number;
  url: string;
  screen_dir: string;
  state_dir: string;
}

/** 在 SESSIONS_DIR 里找最新的 server-info(服务器可能已在跑) */
function findRunningServer(): ServerInfo | null {
  try {
    if (!existsSync(SESSIONS_DIR)) return null;
    const subs = readdirSync(SESSIONS_DIR)
      .map((d) => ({ d, p: path.join(SESSIONS_DIR, d) }))
      .filter((x) => statSync(x.p).isDirectory())
      .sort((a, b) => statSync(b.p).mtimeMs - statSync(a.p).mtimeMs);
    for (const sub of subs) {
      const infoFile = path.join(sub.p, "state", "server-info");
      if (existsSync(infoFile)) {
        // 确认端口还在听(curl 一下)
        try {
          const info = JSON.parse(readFileSync(infoFile, "utf8")) as ServerInfo;
          const probe = spawnSync("curl", ["-s", "-o", "/dev/null", "-w", "%{http_code}",
            "--noproxy", "*", "--max-time", "2", `http://localhost:${info.port}/`], { encoding: "utf8" });
          if (probe.stdout && probe.stdout.trim() !== "000") return info;
        } catch {
          /* 端口没响应,试下一个 */
        }
      }
    }
    return null;
  } catch {
    return null;
  }
}

/** 启动 brainstorming 服务器(后台),返回 ServerInfo */
function startServer(): ServerInfo {
  mkdirSync(PROJECT_DIR, { recursive: true });
  const r = spawnSync(
    "bash",
    [START_SCRIPT, "--project-dir", PROJECT_DIR, "--background"],
    { encoding: "utf8", timeout: 15000 },
  );
  const out = (r.stdout || "") + (r.stderr || "");
  // 从输出或 server-info 文件里取
  try {
    const m = out.match(/\{[^}]*"type":"server-started"[^}]*\}/);
    if (m) return JSON.parse(m[0]) as ServerInfo;
  } catch {
    /* fall through */
  }
  // 兜底:读最新 server-info
  const info = findRunningServer();
  if (info) return info;
  throw new Error(`服务器启动失败。\nstdout: ${r.stdout}\nstderr: ${r.stderr}`);
}

/** 取(或起)当前服务器 */
function ensureServer(): ServerInfo {
  return findRunningServer() || startServer();
}

/** 生成唯一文件名(按时间,保证 newest) */
function newScreenPath(screenDir: string): string {
  const ts = Date.now();
  return path.join(screenDir, `pv-${ts}.html`);
}

/** 把 agent 传的 options 包成带 data-choice 的可点击卡片 */
function wrapOptions(title: string, bodyHtml: string, options?: { id: string; label: string; previewHtml?: string }[]): string {
  const cards = options?.length
    ? `<div class="pv-options" style="display:grid;gap:12px;margin-top:16px">
        ${options
          .map(
            (o, i) =>
              `<div class="option" data-choice="${o.id}" tabindex="${i + 1}"
                 style="border:2px solid #444;border-radius:8px;padding:16px;cursor:pointer;background:#1a1a1a;color:#eee;transition:border-color .15s"
                 onmouseover="this.style.borderColor='#58C7E8'" onmouseout="this.style.borderColor='#444'">
                 <b style="font-size:1.05em">${o.label}</b>
                 ${o.previewHtml ? `<div style="margin-top:10px">${o.previewHtml}</div>` : ""}
                 <div style="margin-top:8px;font-size:.8em;color:#888">点击选择此项</div>
               </div>`,
          )
          .join("\n")}
       </div>`
    : "";
  return `<div style="font-family:system-ui,sans-serif;max-width:1100px;margin:0 auto;padding:20px">
    <h2 style="color:#58C7E8;border-bottom:1px solid #333;padding-bottom:8px">${title}</h2>
    ${bodyHtml}
    ${cards}
    ${
      options?.length
        ? `<p style="margin-top:20px;color:#888;font-size:.85em">👆 在上方卡片中点选你的选择,然后回到终端告诉 agent 已选(或 agent 会自动读取)。</p>`
        : ""
    }
  </div>`;
}

// ===== 参数 schema =====
const OptionSchema = Type.Object({
  id: Type.String({ description: "选项标识(agent 读取用,如 a/b/c 或 theme-warm)" }),
  label: Type.String({ description: "选项标题(中文)" }),
  previewHtml: Type.Optional(Type.String({ description: "该选项的真彩色预览 HTML 片段(渲染配色/布局/组件等)。这是可视化的核心——写真实样式而非文字描述。" })),
});

const PlanVisualParams = Type.Object({
  action: Type.Union([Type.Literal("push"), Type.Literal("read"), Type.Literal("stop")], {
    description: "push=推送内容到浏览器(起服务器+写HTML);read=读取用户在浏览器的点击选择;stop=关闭服务器",
  }),
  title: Type.Optional(Type.String({ description: "[push] 页面标题" })),
  bodyHtml: Type.Optional(Type.String({ description: "[push] 正文 HTML 片段(说明文字、对比说明等)。可选,可不填只给 options" })),
  options: Type.Optional(Type.Array(OptionSchema, { description: "[push] 可选项列表。每项可带 previewHtml 真彩色预览。用户点击其一回传" })),
  content: Type.Optional(Type.String({ description: "[push] 完整自定义 HTML(覆盖 bodyHtml/options,完全自主排版)。一般用 bodyHtml+options 即可" })),
});

export default function (pi: ExtensionAPI) {
  // 记录上次 push 时 events 文件大小,read 时只读新增
  let lastEventsSize = 0;

  pi.registerTool({
    name: "plan_visual",
    label: "Plan 可视化",
    description:
      "[Plan 模式可视化] 在浏览器里渲染真彩色预览(配色/布局/UI 对比等),突破终端文本限制。" +
      "agent 传 HTML 内容,本工具写文件并复用 brainstorming 服务器推到浏览器;用户点击选项后," +
      "agent 用 action=read 读取选择。push=推内容,read=读选择,stop=关服务器。" +
      "适用:plan 模式下任何'看到才能决策'的设计问题(选主题、配色、布局、组件样式)。",
    promptSnippet: "在浏览器渲染真彩色可视化预览(plan 模式可视化出口)",
    parameters: PlanVisualParams,
    async execute(_id, params: any, _signal, _onUpdate, _ctx) {
      const action = params.action as "push" | "read" | "stop";

      // ---------- read ----------
      if (action === "read") {
        const srv = findRunningServer();
        if (!srv) {
          return { content: [{ type: "text" as const, text: "(无运行中的可视化服务器。先 action=push 推内容。)" }] };
        }
        const eventsFile = path.join(srv.state_dir, "events");
        if (!existsSync(eventsFile)) {
          return { content: [{ type: "text" as const, text: "(用户尚未在浏览器做出选择。提醒用户去浏览器点选。)" }] };
        }
        const raw = readFileSync(eventsFile, "utf8");
        const newSize = Buffer.byteLength(raw);
        const newPart = newSize > lastEventsSize ? raw.slice(lastEventsSize) : raw;
        lastEventsSize = newSize;
        const lines = newPart.split("\n").map((l) => l.trim()).filter(Boolean);
        if (lines.length === 0) {
          return { content: [{ type: "text" as const, text: "(暂无新的选择。提醒用户去浏览器点选。)" }] };
        }
        // 解析最近的 click
        let latest: any = null;
        for (const l of lines) {
          try {
            latest = JSON.parse(l);
          } catch {
            /* skip */
          }
        }
        const choice = latest?.choice;
        const detail = `用户在浏览器选择了: ${choice ?? "(未识别)"}\n(events 文件累计 ${newSize} bytes)`;
        return {
          content: [{ type: "text" as const, text: detail }],
          details: { choice, rawLines: lines },
        };
      }

      // ---------- stop ----------
      if (action === "stop") {
        const stopScript = path.join(SKILL_DIR, "scripts", "stop-server.sh");
        if (existsSync(stopScript)) {
          spawnSync("bash", [stopScript, "--project-dir", PROJECT_DIR], { encoding: "utf8", timeout: 8000 });
        }
        return { content: [{ type: "text" as const, text: "已关闭可视化服务器。" }] };
      }

      // ---------- push ----------
      const title = (params.title as string) || "Plan 可视化";
      let html: string;
      if (params.content) {
        html = params.content as string;
      } else {
        const opts = params.options as { id: string; label: string; previewHtml?: string }[] | undefined;
        html = wrapOptions(title, (params.bodyHtml as string) || "", opts);
      }

      let srv: ServerInfo;
      try {
        srv = ensureServer();
      } catch (e: any) {
        return {
          content: [{ type: "text" as const, text: `❌ 启动可视化服务器失败: ${e?.message ?? e}\n(确认 ~/.agents/skills/brainstorming 存在)` }],
        };
      }

      // 写 HTML → 服务器自动 reload
      const filePath = newScreenPath(srv.screen_dir);
      writeFileSync(filePath, html, "utf8");

      // 记录当前 events 大小,供后续 read 增量
      const eventsFile = path.join(srv.state_dir, "events");
      lastEventsSize = existsSync(eventsFile) ? statSync(eventsFile).size : 0;

      const msg =
        `✅ 已推送到浏览器。\n` +
        `   URL: ${srv.url}\n` +
        `   (若浏览器没自动开,手动打开上面的完整 URL,含 ?key=)\n` +
        `   用户在浏览器点选后,调用 action=read 读取选择。`;
      return {
        content: [{ type: "text" as const, text: msg }],
        details: { url: srv.url, port: srv.port, screen_dir: srv.screen_dir },
      };
    },
  });
}

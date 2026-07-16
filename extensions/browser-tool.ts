/**
 * Pi 浏览器扩展 — 基于 Playwright 驱动真实/隔离浏览器。
 *
 * real:     connectOverCDP 连专用 profile Chrome(9222,首次需手动登录站点)
 * isolated: launchPersistentContext 独立 profile,headed/headless
 *
 * 工具:session / navigate / snapshot / click / type / (后续: eval / console /
 *       storage / wait_human / screenshot)
 *
 * 设计:snapshot 注入 JS 扫描可交互元素 + 分配 data-agent-ref 编号(e1/e2/…),
 *       输出结构化列表(借鉴 Cursor Element Ref 系统);click/type 优先用 ref
 *       精确定位,role+name 作为 fallback。与 agentic-browser-mcp/index.mjs 同一套逻辑。
 *
 * 详见 ~/docs/superpowers/specs/2026-07-15-pi-browser-extension-design.md
 * 参考样例 examples/extensions/todo.ts (registerTool 写法)。
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { chromium } from "playwright";
import { Type } from "typebox";
import { mkdirSync, existsSync } from "node:fs";
import { join } from "node:path";
import { createConnection } from "node:net";
import { spawn } from "node:child_process";
import * as os from "node:os";

const AGENT_DIR = join(os.homedir(), ".pi", "agent");
const ISOLATED_PROFILE = join(AGENT_DIR, "bw-profile");
const CDP_ENDPOINT = "http://127.0.0.1:9222";
const CHROME_STARTER = join(AGENT_DIR, "start-agent-chrome.sh");
const CDP_PROFILE = join(AGENT_DIR, "chrome-cdp-profile");
const CHROME_BOOT_TIMEOUT_MS = 20000;
const IS_WIN = os.platform() === "win32";
const CHROME_CANDIDATES_WIN = [
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
];
const WAIT_HUMAN_TIMEOUT_MS = 5 * 60 * 1000;
const SNAPSHOT_MAX_CHARS = 12000;

interface ConsoleEntry {
  type: string;
  text: string;
  ts: number;
}
interface Session {
  type: "real" | "isolated";
  browser: any;
  context: any;
  page: any;
  consoleBuffer: ConsoleEntry[];
  dispose: () => Promise<void>;
}

let current: Session | null = null;

// —— Chrome 自动拉起(移植自 agentic-browser-mcp)——
// 探测 9222:用 TCP 连接(不走 HTTP 代理环境变量,避免 undici fetch 读 http_proxy
// 把 127.0.0.1:9222 发给代理 7897 导致误判)。
function chromeUp(): Promise<boolean> {
  return new Promise((resolve) => {
    const sock = createConnection({ host: "127.0.0.1", port: 9222 }, () => {
      sock.end();
      resolve(true);
    });
    sock.on("error", () => resolve(false));
    sock.setTimeout(1500, () => {
      sock.destroy();
      resolve(false);
    });
  });
}

function spawnStarter(): boolean {
  // Windows: 直接 spawn chrome.exe(无 bash/nohup)
  if (IS_WIN) {
    try {
      const exe = CHROME_CANDIDATES_WIN.find((p) => existsSync(p));
      if (!exe) return false;
      const child = spawn(
        exe,
        ["--remote-debugging-port=9222", `--user-data-dir=${CDP_PROFILE}`, "--no-first-run", "--no-default-browser-check"],
        { stdio: "ignore", detached: true },
      );
      child.on("error", () => {});
      child.unref();
      return true;
    } catch {
      return false;
    }
  }
  // Linux: nohup + shell 后台(detached:true 在本环境下 Chrome 起不来)。
  try {
    const child = spawn(
      "bash",
      ["-c", `nohup ${JSON.stringify(CHROME_STARTER)} > /tmp/pi-browser-chrome.log 2>&1 &`],
      { stdio: "ignore" },
    );
    child.on("error", () => {});
    child.unref();
    return true;
  } catch {
    return false;
  }
}

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

async function ensureChromeUp(): Promise<boolean> {
  if (await chromeUp()) return true;
  spawnStarter();
  const deadline = Date.now() + CHROME_BOOT_TIMEOUT_MS;
  while (Date.now() < deadline) {
    await sleep(500);
    if (await chromeUp()) return true;
  }
  return false;
}

function attachConsoleListener(s: Session): void {
  s.consoleBuffer = [];
  const push = (type: string, text: string) => {
    try {
      s.consoleBuffer.push({ type, text, ts: Date.now() });
      if (s.consoleBuffer.length > 500) s.consoleBuffer.shift();
    } catch {
      /* never crash */
    }
  };
  try {
    s.page.on("console", (m: any) => push(m.type(), m.text()));
    s.page.on("pageerror", (e: any) => push("error", String(e)));
  } catch {
    /* ignore */
  }
}

function sameProfile(s: Session, profile: "real" | "isolated", incognito?: boolean): boolean {
  if (s.type !== profile) return false;
  return !incognito; // 切 incognito 由调用方 force 重建
}

async function safeDispose(s: Session): Promise<void> {
  try {
    await s.dispose();
  } catch {
    /* ignore */
  }
}

/** 会话是否还活着(real: CDP 连接;isolated: page 未关闭) */
function isAlive(s: Session): boolean {
  try {
    if (s.type === "real") return s.browser?.isConnected?.() === true;
    return !s.page?.isClosed?.();
  } catch {
    return false;
  }
}

async function ensureSession(opts: {
  profile?: "real" | "isolated";
  headless?: boolean;
  incognito?: boolean;
  force?: boolean;
} = {}): Promise<Session> {
  const profile = opts.profile ?? "real";
  // 孤儿/废连接检测:死了就清理,避免复用已断开的会话(真实 Chrome 重启/崩溃后)
  if (current && !isAlive(current)) {
    await safeDispose(current);
    current = null;
  }
  if (current && !opts.force && sameProfile(current, profile, opts.incognito)) {
    return current;
  }
  if (current) {
    await safeDispose(current);
    current = null;
  }

  if (profile === "real") {
    // 9222 不通就自动拉起专用 Chrome,轮询等待(AI 不该要求用户手动开 Chrome)。
    const up = await ensureChromeUp();
    if (!up) {
      throw new Error("专用 Chrome(9222)未启动且自动拉起失败。请手动执行 ~/.pi/agent/start-agent-chrome.sh");
    }
    const browser = await chromium.connectOverCDP(CDP_ENDPOINT);
    const context = browser.contexts()[0] ?? (await browser.newContext());
    const page = context.pages()[0] ?? (await context.newPage());
    const s: Session = {
      type: "real",
      browser,
      context,
      page,
      consoleBuffer: [],
      dispose: async () => {
        try {
          await browser.close();
        } catch {
          /* connectOverCDP 的 close 只断开 CDP 连接,不关真实 Chrome */
        }
      },
    };
    attachConsoleListener(s);
    current = s;
    return s;
  }

  // isolated
  mkdirSync(ISOLATED_PROFILE, { recursive: true });
  const headless = opts.headless ?? false;
  const context = await chromium.launchPersistentContext(ISOLATED_PROFILE, {
    headless,
    channel: "chrome", // 复用已装的 google-chrome,不依赖 playwright 自带 chromium
    args: opts.incognito ? ["--incognito"] : [],
  });
  const page = context.pages()[0] ?? (await context.newPage());
  const s: Session = {
    type: "isolated",
    browser: context,
    context,
    page,
    consoleBuffer: [],
    dispose: async () => {
      try {
        await context.close();
      } catch {
        /* ignore */
      }
    },
  };
  attachConsoleListener(s);
  current = s;
  return s;
}

function tryUrl(page: any): string {
  try {
    return page.url();
  } catch {
    return "";
  }
}
async function tryTitle(page: any): Promise<string> {
  try {
    return await page.title();
  } catch {
    return "";
  }
}

/** 把任意值序列化成给模型看的文本(必须返回 string,否则 toolResult 的 text 字段会缺失,导致 pi 崩溃) */
function toText(obj: unknown): string {
  // JSON.stringify(undefined) 返回 undefined(非字符串),会导致 content text 字段缺失,
  // pi 取 text.length 时崩溃。这里逐层兜底,确保永远返回 string。
  if (obj === undefined) return "(undefined)";
  if (obj === null) return "(null)";
  try {
    if (typeof obj === "string") return obj;
    const s = JSON.stringify(obj);
    return s === undefined ? String(obj) : s;
  } catch {
    return String(obj);
  }
}

/** 用 role+name 定位元素(Playwright getByRole,name 子串匹配,取第一个) */
function locateByRole(page: any, role: string, name?: string): any {
  const loc = page.getByRole(role, name ? { name, exact: false } : undefined);
  return loc.first();
}

// —— TypeBox 参数 schema ——
const ProfileOpt = Type.Optional(Type.Union([Type.Literal("real"), Type.Literal("isolated")]));
const SessionParams = Type.Object({
  profile: ProfileOpt,
  headless: Type.Optional(Type.Boolean()),
  incognito: Type.Optional(Type.Boolean()),
});
const NavigateParams = Type.Object({
  url: Type.String({ description: "要打开的 URL" }),
  profile: ProfileOpt,
  headless: Type.Optional(Type.Boolean()),
});
const ClickParams = Type.Object({
  ref: Type.Optional(Type.String({ description: "snapshot 返回的元素 ref(如 e3),优先使用" })),
  role: Type.Optional(Type.String({ description: "元素 role(ref 未提供时使用,见 snapshot 输出,如 link/button/textbox)" })),
  name: Type.Optional(Type.String({ description: "元素的可访问名称(accessible name)" })),
});
const TypeInputParams = Type.Object({
  ref: Type.Optional(Type.String({ description: "snapshot 返回的元素 ref(如 e3),优先使用" })),
  role: Type.Optional(Type.String({ description: "元素 role(ref 未提供时使用,通常 textbox/searchbox/combobox)" })),
  name: Type.Optional(Type.String({ description: "输入框的可访问名称" })),
  text: Type.String({ description: "要输入的文本" }),
});

export default function (pi: ExtensionAPI) {
  // 会话结束清理
  pi.on("session_shutdown", async () => {
    if (current) {
      await safeDispose(current);
      current = null;
    }
  });

  // 进程退出兜底:防 pi 崩溃/被强杀时遗留孤立 isolated 浏览器进程
  const cleanupOnExit = async () => {
    if (current?.type === "isolated") {
      await safeDispose(current);
      current = null;
    }
  };
  process.once("beforeExit", cleanupOnExit);
  for (const sig of ["SIGTERM", "SIGHUP", "SIGINT"] as const) {
    process.once(sig, () => {
      void cleanupOnExit();
      process.exit(0);
    });
  }

  // ===== Task 2: session + navigate =====
  pi.registerTool({
    name: "browser_session",
    label: "浏览器会话",
    description:
      "切换/启动浏览器会话。profile: real(连专用 profile Chrome,已登录态) | isolated(独立 profile);headless 仅 isolated 生效;incognito 临时无痕。",
    executionMode: "sequential",
    parameters: SessionParams,
    async execute(_toolCallId, params, _signal, _onUpdate, _ctx) {
      try {
        const s = await ensureSession({
          profile: params.profile as any,
          headless: params.headless as any,
          incognito: params.incognito as any,
          force: params.profile !== undefined,
        });
        const details = { ok: true, type: s.type, url: tryUrl(s.page), title: await tryTitle(s.page) };
        return {
          content: [{ type: "text" as const, text: toText(details) }],
          details,
        };
      } catch (e: any) {
        const msg = `浏览器会话失败: ${e?.message ?? e}\n提示: 请确认专用 Chrome 已启动(~/.pi/agent/start-agent-chrome.sh)且 9222 在听`;
        return { content: [{ type: "text" as const, text: msg }], details: { ok: false, error: msg } };
      }
    },
  });

  pi.registerTool({
    name: "browser_navigate",
    label: "浏览器导航",
    description: "打开 URL。可顺便切会话(未指定 profile 则用当前/默认 real)。",
    executionMode: "sequential",
    parameters: NavigateParams,
    async execute(_toolCallId, params, _signal, _onUpdate, _ctx) {
      try {
        const s = params.profile
          ? await ensureSession({ profile: params.profile as any, headless: params.headless as any, force: true })
          : await ensureSession();
        await s.page.goto(params.url, { waitUntil: "domcontentloaded", timeout: 30000 });
        const details = { ok: true, url: tryUrl(s.page), title: await tryTitle(s.page) };
        return {
          content: [{ type: "text" as const, text: toText(details) }],
          details,
        };
      } catch (e: any) {
        const msg = `导航失败: ${e?.message ?? e}`;
        return { content: [{ type: "text" as const, text: msg }], details: { ok: false, error: msg } };
      }
    },
  });

  // ===== Task 3: snapshot + click + type =====
  pi.registerTool({
    name: "browser_snapshot",
    label: "浏览器快照",
    description:
      "返回页面可交互元素快照(带 ref 编号)。据此调 click/type 时提供 ref 优先定位。也可用 role+name fallback。",
    executionMode: "sequential",
    parameters: Type.Object({
      mode: Type.Optional(Type.Union([Type.Literal("viewport"), Type.Literal("all")], { description: "viewport=只返回当前视口内可交互元素(默认,省 token);all=返回全部(含视口外,需滚动才能操作)" })),
    }),
    async execute(_toolCallId, params: any, _signal, _onUpdate, _ctx) {
      try {
        const s = await ensureSession();
        const mode = params?.mode ?? "viewport";
        // 注入 JS:扫描可交互元素 + 分配 data-agent-ref(借鉴 Cursor Element Ref 系统)
        // 与 agentic-browser-mcp/index.mjs 同一套逻辑(同一套逻辑的两个宿主)
        const text = await s.page.evaluate(({ maxChars, mode }: any) => {
          const sel = [
            'input','textarea','select','button','a[href]',
            '[role="button"]','[role="link"]','[role="textbox"]',
            '[role="checkbox"]','[role="radio"]','[role="combobox"]',
            '[role="listbox"]','[role="menuitem"]','[role="menuitemcheckbox"]',
            '[role="menuitemradio"]','[role="option"]','[role="slider"]',
            '[role="spinbutton"]','[role="switch"]','[role="tab"]',
            '[role="treeitem"]','[role="searchbox"]',
            '[contenteditable="true"]','summary',
            '[tabindex]:not([tabindex="-1"])',
          ].join(',');
          // 穿透 open shadow root
          function deepQuery(root: any, s: string): Element[] {
            const out: Element[] = [...root.querySelectorAll(s)];
            for (const el of root.querySelectorAll('*')) {
              if ((el as any).shadowRoot) out.push(...deepQuery((el as any).shadowRoot, s));
            }
            return out;
          }
          // 隐式 ARIA role 映射（让输出的 role 与 Playwright getByRole fallback 一致）
          function implicitRole(el: Element): string {
            const r = el.getAttribute('role');
            if (r) return r;
            const t = el.tagName.toLowerCase();
            if (t === 'a' && el.hasAttribute('href')) return 'link';
            if (t === 'button' || t === 'summary') return 'button';
            if (t === 'textarea') return 'textbox';
            if (t === 'input') {
              const ty = (el.getAttribute('type') || 'text').toLowerCase();
              return ({checkbox:'checkbox', radio:'radio', search:'searchbox'} as any)[ty] || 'textbox';
            }
            if (t === 'select') return 'combobox';
            return t;
          }
          // 清理旧 ref（穿透 shadow root，避免旧 ref 残留与新编号冲突）
          deepQuery(document, '[data-agent-ref]').forEach(el => el.removeAttribute('data-agent-ref'));
          // 可见性 + 视口过滤
          const els = deepQuery(document, sel).filter(el => {
            if (el.getAttribute('aria-hidden') === 'true') return false;
            const cs = getComputedStyle(el);
            const rect = el.getBoundingClientRect();
            const visible =
              (el as any).checkVisibility?.({ checkOpacity: true, checkVisibilityCSS: true, contentVisibilityAuto: true }) ?? cs.visibility !== 'hidden';
            if (!(visible && rect.width > 0 && rect.height > 0)) return false;
            if (mode === 'viewport') {
              return rect.bottom > 0 && rect.right > 0 && rect.top < innerHeight && rect.left < innerWidth;
            }
            return true;
          });
          const lines = els.map((el, i) => {
            const ref = 'e' + (i + 1);
            el.setAttribute('data-agent-ref', ref);
            const role = implicitRole(el);
            const name = (el.getAttribute('aria-label') ||
              el.getAttribute('aria-labelledby') ||
              el.textContent?.trim()?.slice(0, 80) ||
              (el as HTMLInputElement).placeholder || (el as HTMLElement).title || '').slice(0, 80);
            return `- [ref=${ref}] ${role}${name ? ` "${name}"` : ''}`;
          });
          // 整行截断（不切断单行）+ 统计
          let text = '', shown = 0;
          for (const ln of lines) {
            if (text.length + ln.length + 1 > maxChars) break;
            text += (text ? '\n' : '') + ln;
            shown++;
          }
          const total = lines.length;
          if (shown < total) text += `\n…[共 ${total} 项，已返回前 ${shown} 项；mode=all 或滚动后重 snapshot 看更多]`;
          return text || '(无可交互元素)';
        }, { maxChars: SNAPSHOT_MAX_CHARS, mode });
        return { content: [{ type: "text" as const, text }], details: { ok: true } };
      } catch (e: any) {
        const msg = `snapshot 失败: ${e?.message ?? e}`;
        return { content: [{ type: "text" as const, text: msg }], details: { ok: false, error: msg } };
      }
    },
  });

  pi.registerTool({
    name: "browser_click",
    label: "浏览器点击",
    description: "点击页面元素。优先用 snapshot 返回的 ref 定位(如 e3),也可用 role+name fallback。",
    executionMode: "sequential",
    parameters: ClickParams,
    async execute(_toolCallId, params, _signal, _onUpdate, _ctx) {
      if (!params.ref && !params.role) {
        return { content: [{ type: "text" as const, text: "点击失败: 必须提供 ref 或 role" }], details: { ok: false, error: "缺少定位参数" } };
      }
      if (params.ref && !/^e\d+$/.test(params.ref)) {
        return { content: [{ type: "text" as const, text: `点击失败: ref 必须形如 e3，收到 ${params.ref}` }], details: { ok: false, error: "ref 非法" } };
      }
      try {
        const s = await ensureSession();
        let loc;
        if (params.ref) {
          const sel = `[data-agent-ref="${CSS.escape(params.ref)}"]`;
          const cnt = await s.page.locator(sel).count();
          if (cnt === 0) return { content: [{ type: "text" as const, text: `点击失败: ref=${params.ref} 未命中（页面可能已变化），请重新 snapshot` }], details: { ok: false, error: "ref 失效" } };
          if (cnt > 1) return { content: [{ type: "text" as const, text: `点击失败: ref=${params.ref} 命中 ${cnt} 个（快照内部错误），请重新 snapshot` }], details: { ok: false, error: "ref 重复" } };
          loc = s.page.locator(sel).first();
        } else {
          loc = locateByRole(s.page, params.role!, params.name);
        }
        await loc.click({ timeout: 10000 });
        const where = params.ref ? `ref=${params.ref}` : `${params.role}${params.name ? ` "${params.name}"` : ""}`;
        return {
          content: [{ type: "text" as const, text: `已点击 ${where}` }],
          details: { ok: true, role: params.role, name: params.name, ref: params.ref },
        };
      } catch (e: any) {
        const msg = `点击失败: ${e?.message ?? e}`;
        return { content: [{ type: "text" as const, text: msg }], details: { ok: false, error: msg } };
      }
    },
  });

  pi.registerTool({
    name: "browser_type",
    label: "浏览器输入",
    description: "在输入框输入文本。优先用 snapshot 返回的 ref 定位(如 e3),也可用 role+name fallback。",
    executionMode: "sequential",
    parameters: TypeInputParams,
    async execute(_toolCallId, params, _signal, _onUpdate, _ctx) {
      if (!params.ref && !params.role) {
        return { content: [{ type: "text" as const, text: "输入失败: 必须提供 ref 或 role" }], details: { ok: false, error: "缺少定位参数" } };
      }
      if (params.ref && !/^e\d+$/.test(params.ref)) {
        return { content: [{ type: "text" as const, text: `输入失败: ref 必须形如 e3，收到 ${params.ref}` }], details: { ok: false, error: "ref 非法" } };
      }
      try {
        const s = await ensureSession();
        let loc;
        if (params.ref) {
          const sel = `[data-agent-ref="${CSS.escape(params.ref)}"]`;
          const cnt = await s.page.locator(sel).count();
          if (cnt === 0) return { content: [{ type: "text" as const, text: `输入失败: ref=${params.ref} 未命中（页面可能已变化），请重新 snapshot` }], details: { ok: false, error: "ref 失效" } };
          if (cnt > 1) return { content: [{ type: "text" as const, text: `输入失败: ref=${params.ref} 命中 ${cnt} 个（快照内部错误），请重新 snapshot` }], details: { ok: false, error: "ref 重复" } };
          loc = s.page.locator(sel).first();
        } else {
          loc = locateByRole(s.page, params.role!, params.name);
        }
        await loc.fill(params.text, { timeout: 10000 });
        const where = params.ref ? `ref=${params.ref}` : `${params.role}${params.name ? ` "${params.name}"` : ""}`;
        return {
          content: [
            { type: "text" as const, text: `已在 ${where} 输入 ${JSON.stringify(params.text)}` },
          ],
          details: { ok: true, role: params.role, name: params.name, ref: params.ref },
        };
      } catch (e: any) {
        const msg = `输入失败: ${e?.message ?? e}`;
        return { content: [{ type: "text" as const, text: msg }], details: { ok: false, error: msg } };
      }
    },
  });

  // ===== Task 4: eval + storage + console (F12 读取) =====
  pi.registerTool({
    name: "browser_eval",
    label: "浏览器执行JS",
    description: "在页面执行 JS 表达式(=F12 控制台输入,如 document.title / JSON.stringify({...}) / 1+1 / location.href),返回结果。传表达式,不是箭头函数。可读 DOM/storage/发请求。",
    executionMode: "sequential",
    parameters: Type.Object({ code: Type.String({ description: "要执行的 JS 表达式/语句" }) }),
    async execute(_toolCallId, params, _signal, _onUpdate, _ctx) {
      try {
        const s = await ensureSession();
        const result = await s.page.evaluate(params.code);
        const text = toText(result);
        return { content: [{ type: "text" as const, text }], details: { ok: true } };
      } catch (e: any) {
        const msg = `eval 失败: ${e?.message ?? e}`;
        return { content: [{ type: "text" as const, text: msg }], details: { ok: false, error: msg } };
      }
    },
  });

  pi.registerTool({
    name: "browser_storage",
    label: "浏览器存储",
    description: "读取存储(F12 Application)。type: cookies|localStorage|sessionStorage。",
    executionMode: "sequential",
    parameters: Type.Object({
      type: Type.Union([Type.Literal("cookies"), Type.Literal("localStorage"), Type.Literal("sessionStorage")]),
      url: Type.Optional(Type.String({ description: "读 cookies 时限定 URL" })),
    }),
    async execute(_toolCallId, params, _signal, _onUpdate, _ctx) {
      try {
        const s = await ensureSession();
        let result: any;
        if (params.type === "cookies") {
          result = await s.context.cookies(params.url);
        } else {
          result = await s.page.evaluate((t: string) => {
            const st: any = t === "localStorage" ? localStorage : sessionStorage;
            return Object.fromEntries(Object.entries(st));
          }, params.type);
        }
        return { content: [{ type: "text" as const, text: toText(result) }], details: { ok: true, type: params.type } };
      } catch (e: any) {
        const msg = `storage 失败: ${e?.message ?? e}`;
        return { content: [{ type: "text" as const, text: msg }], details: { ok: false, error: msg } };
      }
    },
  });

  pi.registerTool({
    name: "browser_console",
    label: "浏览器控制台",
    description: "读取累积的 console 日志(F12 Console)。可选 level 过滤。会话启动起开始记录。",
    executionMode: "sequential",
    parameters: Type.Object({
      level: Type.Optional(Type.Union([Type.Literal("error"), Type.Literal("warning"), Type.Literal("log"), Type.Literal("info")])),
    }),
    async execute(_toolCallId, params, _signal, _onUpdate, _ctx) {
      try {
        const s = await ensureSession();
        let buf = s.consoleBuffer;
        if (params.level) buf = buf.filter((e) => e.type === params.level);
        const entries = buf.slice(-100);
        return { content: [{ type: "text" as const, text: toText(entries) }], details: { ok: true, count: entries.length } };
      } catch (e: any) {
        const msg = `console 失败: ${e?.message ?? e}`;
        return { content: [{ type: "text" as const, text: msg }], details: { ok: false, error: msg } };
      }
    },
  });

  // ===== Task 6: wait_human + screenshot =====
  pi.registerTool({
    name: "browser_wait_human",
    label: "浏览器等人工",
    description: "遇到验证码/登录等需人工操作时,暂停并在 TUI 弹输入框等用户操作完回复。默认超时 5 分钟。",
    executionMode: "sequential",
    parameters: Type.Object({ reason: Type.String({ description: "需要人工做什么(如:过验证码)" }) }),
    async execute(_toolCallId, params, _signal, _onUpdate, ctx: any) {
      let timer: ReturnType<typeof setTimeout> | undefined;
      try {
        const s = await ensureSession();
        const url = tryUrl(s.page);
        const msg = `🔒 需要你手动操作:${params.reason}\n当前页面:${url || "(未知)"}\n请在浏览器里操作完,回终端输入『继续』`;
        try {
          ctx?.ui?.notify?.(msg, "warning");
        } catch {
          /* ignore */
        }
        const reply = await Promise.race([
          ctx?.ui?.input?.(msg) ?? Promise.resolve("(TUI 无输入框,已跳过)"),
          new Promise((_, rej) => {
            timer = setTimeout(() => rej(new Error("等待超时(5min)")), WAIT_HUMAN_TIMEOUT_MS);
          }),
        ]).catch((e: any) => "TIMEOUT:" + String(e?.message ?? e));
        const after = tryUrl(s.page);
        return {
          content: [{ type: "text" as const, text: `人工操作完成:${reply}\n当前页面:${after}` }],
          details: { ok: true, reply: String(reply), url: after },
        };
      } catch (e: any) {
        const msg = `wait_human 失败: ${e?.message ?? e}`;
        return { content: [{ type: "text" as const, text: msg }], details: { ok: false, error: msg } };
      } finally {
        if (timer) clearTimeout(timer);
      }
    },
  });

  pi.registerTool({
    name: "browser_screenshot",
    label: "浏览器截图",
    description: "截图存为文件(配角:非多模态模型不解读,主要给人看)。默认存 ~/.pi/agent/bw-shots/。",
    executionMode: "sequential",
    parameters: Type.Object({ fullPage: Type.Optional(Type.Boolean({ description: "是否整页截图" })) }),
    async execute(_toolCallId, params, _signal, _onUpdate, _ctx) {
      try {
        const s = await ensureSession();
        const dir = join(AGENT_DIR, "bw-shots");
        mkdirSync(dir, { recursive: true });
        const file = join(dir, `shot-${Date.now()}.png`);
        await s.page.screenshot({ path: file, fullPage: !!params.fullPage });
        return { content: [{ type: "text" as const, text: `截图已存:${file}` }], details: { ok: true, path: file } };
      } catch (e: any) {
        const msg = `screenshot 失败: ${e?.message ?? e}`;
        return { content: [{ type: "text" as const, text: msg }], details: { ok: false, error: msg } };
      }
    },
  });

  pi.registerTool({
    name: "browser_close",
    label: "浏览器关闭",
    description: "显式关闭当前浏览器会话,释放资源(real 断开 CDP 连接,不关真实 Chrome;isolated 关闭浏览器进程)。下次工具调用会自动重建。",
    executionMode: "sequential",
    parameters: Type.Object({}),
    async execute(_toolCallId, _params, _signal, _onUpdate, _ctx) {
      const was = current ? current.type : "(无)";
      if (current) {
        await safeDispose(current);
        current = null;
      }
      return {
        content: [{ type: "text" as const, text: `已关闭 ${was} 会话,资源已释放` }],
        details: { ok: true, closed: was },
      };
    },
  });
}

/**
 * Pi MCP Bridge Extension
 *
 * Bridges configured MCP servers (Claude-Code-compatible mcpServers config) into
 * pi tools, so the model can call MCP tools (e.g. `ai_search`) directly. Uses
 * mcporter as the connection runtime.
 *
 * Config: ~/.pi/agent/mcp-servers.json
 *   { "mcpServers": { "<name>": { "url" | "command", "headers"?, "env"?, "description"? } } }
 *
 * Plan: docs/superpowers/plans/2026-07-15-pi-mcp-bridge.md
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type, type TSchema } from "typebox";
import { Text } from "@earendil-works/pi-tui";
import { readFileSync } from "node:fs";
import { execSync } from "node:child_process";
import { pathToFileURL } from "node:url";
import * as path from "node:path";
import * as os from "node:os";

// mcporter types (loose to avoid hard type coupling across jiti/ESM)
interface ServerToolInfo {
  name: string;
  description?: string;
  inputSchema?: any;
}
interface McpRuntime {
  listTools(server: string): Promise<ServerToolInfo[]>;
  callTool(server: string, toolName: string, opts?: { args?: any; timeoutMs?: number }): Promise<unknown>;
  close(server?: string): Promise<void>;
}
interface ServerDef {
  name: string;
  description?: string;
  command:
    | { kind: "http"; url: URL; headers?: Record<string, string> }
    | { kind: "stdio"; command: string; args: string[]; cwd: string };
  env?: Record<string, string>;
}

// ===== config =====

interface McpConfigEntry {
  url?: string;
  command?: string[];
  headers?: Record<string, string>;
  env?: Record<string, string>;
  description?: string;
  timeoutMs?: number;
}
interface McpConfig {
  mcpServers?: Record<string, McpConfigEntry>;
  /** Default call timeout in ms for all servers (overridden per-server). Default: 60000. */
  timeoutMs?: number;
}

const CONFIG_PATH = path.join(os.homedir(), ".pi", "agent", "mcp-servers.json");
const DEFAULT_TIMEOUT_MS = 60000;

interface LoadedConfig {
  defs: ServerDef[];
  timeouts: Map<string, number>; // server name → call timeout ms
}

/** Read config and map to mcporter ServerDefinitions. Returns empty on any error. */
function loadMcpConfig(): LoadedConfig {
  let raw: string;
  try {
    raw = readFileSync(CONFIG_PATH, "utf8");
  } catch {
    return { defs: [], timeouts: new Map() }; // no config = no servers, silent
  }
  let cfg: McpConfig;
  try {
    cfg = JSON.parse(raw);
  } catch {
    return { defs: [], timeouts: new Map() };
  }
  const globalTimeout = typeof cfg.timeoutMs === "number" && cfg.timeoutMs > 0 ? cfg.timeoutMs : DEFAULT_TIMEOUT_MS;
  const entries = cfg.mcpServers || {};
  const defs: ServerDef[] = [];
  const timeouts = new Map<string, number>();
  for (const [name, e] of Object.entries(entries)) {
    if (e.url) {
      defs.push({
        name,
        description: e.description,
        command: { kind: "http", url: new URL(e.url), headers: e.headers },
      });
    } else if (e.command && e.command.length) {
      defs.push({
        name,
        description: e.description,
        command: { kind: "stdio", command: e.command[0], args: e.command.slice(1), cwd: process.cwd() },
        env: e.env,
      });
    } else {
      continue;
    }
    timeouts.set(name, typeof e.timeoutMs === "number" && e.timeoutMs > 0 ? e.timeoutMs : globalTimeout);
  }
  return { defs, timeouts };
}

// ===== JSON Schema → TypeBox =====

/** Convert a JSON Schema fragment to a TypeBox schema. Unknown → Type.Any(). */
function jsonSchemaToTypebox(schema: any): TSchema {
  if (!schema || typeof schema !== "object") return Type.Any();
  switch (schema.type) {
    case "string":
      return Type.String({ description: schema.description });
    case "number":
    case "integer":
      return Type.Number({ description: schema.description });
    case "boolean":
      return Type.Boolean({ description: schema.description });
    case "array":
      return Type.Array(jsonSchemaToTypebox(schema.items), { description: schema.description });
    case "object":
    default: {
      const props: Record<string, TSchema> = {};
      const required: string[] = schema.required || [];
      for (const [k, v] of Object.entries(schema.properties || {})) {
        const t = jsonSchemaToTypebox(v);
        props[k] = required.includes(k) ? t : Type.Optional(t);
      }
      return Type.Object(props, { description: schema.description });
    }
  }
}

// ===== tool registration =====

// Live tick timers per in-flight tool call: toolCallId → { startMs, interval }
const liveTimers = new Map<string, { startMs: number; interval: NodeJS.Timeout | null }>();

function ensureTimer(toolCallId: string, invalidate: () => void): number {
  let entry = liveTimers.get(toolCallId);
  if (!entry) {
    entry = { startMs: Date.now(), interval: null };
    liveTimers.set(toolCallId, entry);
  }
  if (!entry.interval) {
    entry.interval = setInterval(() => {
      try { invalidate(); } catch {}
    }, 1000);
    if (typeof entry.interval.unref === "function") entry.interval.unref();
  }
  return entry.startMs;
}

function clearTimer(toolCallId: string): number | undefined {
  const entry = liveTimers.get(toolCallId);
  if (!entry) return undefined;
  if (entry.interval) { clearInterval(entry.interval); entry.interval = null; }
  liveTimers.delete(toolCallId);
  return entry.startMs;
}

function registerOne(pi: ExtensionAPI, rt: McpRuntime, server: string, info: ServerToolInfo, timeoutMs?: number) {
  pi.registerTool({
    name: info.name,
    label: info.name,
    description: info.description || `MCP tool ${info.name} (server: ${server})`,
    parameters: jsonSchemaToTypebox(info.inputSchema),
    async execute(_toolCallId, params, _signal) {
      const t0 = Date.now();
      liveTimers.set(_toolCallId, { startMs: t0, interval: null });
      try {
        const res = await rt.callTool(server, info.name, { args: params, timeoutMs });
        const r = res as any;
        const text = Array.isArray(r?.content)
          ? r.content
              .map((c: any) => (c && c.type === "text" ? c.text : JSON.stringify(c)))
              .join("\n")
          : JSON.stringify(r ?? {});
        const serverMs = typeof r?.metadata?.duration_ms === "number" ? r.metadata.duration_ms : undefined;
        const wallMs = Date.now() - t0;
        return {
          content: [{ type: "text" as const, text }],
          details: { server, mcp: r, serverMs, wallMs },
        };
      } catch (e: any) {
        return {
          content: [{ type: "text" as const, text: `MCP call failed: ${e?.message || e}` }],
          details: { server, error: true, wallMs: Date.now() - t0 },
          isError: true,
        } as any;
      }
    },

    // Show during execution: ai_search query xxx  ⏱ Ns  (ticks every second via invalidate).
    renderCall(args: any, theme: any, context: any) {
      const id: string = context?.toolCallId || "";
      const startMs = ensureTimer(id, () => context?.invalidate?.());
      const secs = Math.max(0, Math.floor((Date.now() - startMs) / 1000));
      const parts = Object.entries(args || {})
        .map(([k, v]) => `${k} ${typeof v === "string" ? v : JSON.stringify(v)}`)
        .join("  ");
      const shown = parts.length > 80 ? parts.slice(0, 77) + "..." : parts;
      return new Text(
        theme.fg("toolTitle", theme.bold(info.name + " ")) +
          theme.fg("accent", shown) +
          "  " +
          theme.fg("dim", `⏱ ${secs}s`),
        0,
        0,
      );
    },

    // Show final: ✓ 8.2s (server time) / ✓ Ns (wall) / ✗ failed Ns. Clears the tick timer.
    renderResult(result: any, _opts: any, theme: any, context: any) {
      const id: string = context?.toolCallId || "";
      const startMs = clearTimer(id);
      const d = result?.details || {};
      const secs = (ms?: number) => (typeof ms === "number" ? (ms / 1000).toFixed(1) + "s" : "?");
      let text: string;
      if (d.error) {
        text = theme.fg("error", "✗ failed " + secs(d.wallMs));
      } else if (typeof d.serverMs === "number") {
        text = theme.fg("success", "✓ " + secs(d.serverMs));
        if (typeof d.wallMs === "number" && Math.abs(d.wallMs - d.serverMs) > 500)
          text += theme.fg("dim", " (wall " + secs(d.wallMs) + ")");
      } else {
        text = theme.fg("success", "✓ " + secs(d.wallMs));
      }
      return new Text(text, 0, 0);
    },
  });
}

// ===== mcporter loader =====

/** Resolve mcporter from the global npm root and import via absolute file URL. */
async function importMcporter(): Promise<any> {
  // Try bare specifier first (works if pi/jitti ever exposes it).
  try { return await import("mcporter"); } catch {}
  // Fall back: resolve from `npm root -g`.
  let globalRoot: string;
  try {
    globalRoot = execSync("npm root -g", { encoding: "utf8" }).trim();
  } catch {
    throw new Error("cannot determine npm global root");
  }
  const entry = path.join(globalRoot, "mcporter", "dist", "index.js");
  const url = pathToFileURL(entry).href;
  return await import(url);
}

// ===== extension entry =====

export default function (pi: ExtensionAPI) {
  let runtime: McpRuntime | null = null;

  pi.on("session_start", async (_event, ctx: any) => {
    const { defs, timeouts } = loadMcpConfig();
    if (!defs.length) return; // no config → nothing to do, silent

    // dynamic import of mcporter. pi's jiti aliases only expose @earendil-works/*
    // and typebox, so a bare "mcporter" import fails. Resolve from the global
    // npm root and import via absolute file:// URL (lets Node resolve mcporter's
    // own bundled deps from its node_modules).
    let createRuntime: (opts: { servers: ServerDef[] }) => Promise<McpRuntime>;
    try {
      const mod = await importMcporter();
      createRuntime = (mod as any).createRuntime;
      if (typeof createRuntime !== "function") throw new Error("createRuntime not exported");
    } catch (e: any) {
      ctx.ui?.notify?.(`MCP bridge: mcporter unavailable — ${e?.message || e}`, "warning");
      return;
    }

    try {
      runtime = await createRuntime({ servers: defs });
    } catch (e: any) {
      ctx.ui?.notify?.(`MCP bridge: connect failed — ${e?.message || e}`, "warning");
      return;
    }

    let totalTools = 0;
    for (const def of defs) {
      let tools: ServerToolInfo[];
      try {
        tools = await runtime.listTools(def.name, { includeSchema: true });
      } catch {
        ctx.ui?.notify?.(`MCP bridge: listTools failed for ${def.name}`, "warning");
        continue;
      }
      for (const t of tools) {
        try {
          registerOne(pi, runtime!, def.name, t, timeouts.get(def.name));
          totalTools++;
        } catch (e: any) {
          ctx.ui?.notify?.(`MCP bridge: skip ${t.name} (${e?.message || e})`, "warning");
        }
      }
    }
    ctx.ui?.notify?.(
      `MCP bridge: connected ${defs.length} server${defs.length > 1 ? "s" : ""}, ${totalTools} tool${totalTools > 1 ? "s" : ""}`,
      "info",
    );
  });

  pi.on("session_shutdown", async () => {
    try {
      await runtime?.close();
    } catch {
      /* ignore */
    }
    runtime = null;
  });
}

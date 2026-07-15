/**
 * Pi GLM Footer Extension
 *
 * Replaces the built-in footer with a GLM status line:
 *   model  ⚡level  [context bar 剩% used/window]  [5h quota bar 剩% used 恢复~HH:MM]  Day:N  Mon:N
 *
 * Context/model come from pi's session (trustworthy). 5h/day/month quota come
 * from the Claude Code GLM cache (~/.claude/statusline_5h_cache.json), which is
 * refreshed by the reused ~/.claude/statusline_5h_refresh.js — this extension
 * only READS that cache and spawns the refresh script when stale. It never
 * writes the cache, avoiding conflicts with Claude Code.
 *
 * Spec: docs/superpowers/specs/2026-07-15-pi-glm-footer-design.md
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import type { Component } from "@earendil-works/pi-tui";
import { truncateToWidth, visibleWidth } from "@earendil-works/pi-tui";
import { readFileSync, existsSync } from "node:fs";
import { spawn } from "node:child_process";
import * as path from "node:path";
import * as os from "node:os";

// ===== pure formatting helpers (inlined — no separate .mjs, so /reload always picks up edits) =====

function formatTokens(n: number): string {
  if (!n || n <= 0) return "0";
  if (n < 1e3) return String(Math.round(n));
  const strip = (x: number) => { const s = x.toFixed(1); return s.endsWith(".0") ? s.slice(0, -2) : s; };
  if (n < 1e6) return strip(n / 1e3) + "K";
  if (n < 1e9) return strip(n / 1e6) + "M";
  return strip(n / 1e9) + "B";
}

function makeBar(percent: number, width = 10): string {
  const p = Math.max(0, Math.min(100, percent || 0));
  const filled = Math.round((p * width) / 100);
  return "━".repeat(filled) + "─".repeat(width - filled);
}

function remainingPct(usedPct: number): number {
  // Math.round avoids JS float noise (e.g. 100 - 16.1475 = 83.85249999999999)
  return Math.round(Math.max(0, Math.min(100, 100 - (usedPct || 0))));
}

function baseModelName(name: string): string {
  if (!name) return "GLM";
  return name.replace(/\[\d+(?:\.\d+)?[km]\]/i, "").trim() || name;
}

function colorKey(usedPct: number): "success" | "warning" | "error" {
  if (usedPct < 50) return "success";
  if (usedPct < 80) return "warning";
  return "error";
}

function sumSegments(parts: (string | null | undefined)[], sep = "  "): string {
  return parts.filter((p) => p != null && p !== "").join(sep);
}

// ===== paths / timing (mirrors ~/.claude/statusline.js) =====
const HOME = os.homedir();
const CLAUDE_DIR = path.join(HOME, ".claude");
const CACHE_FILE = path.join(CLAUDE_DIR, "statusline_5h_cache.json");
const REFRESH_SCRIPT = path.join(CLAUDE_DIR, "statusline_5h_refresh.js");
const FRESH_MS = 2 * 60 * 1000; // 2 min: still fresh, no refresh needed
const MAX_TRUST_MS = 15 * 60 * 1000; // 15 min: beyond this, degrade to "5h …"
const RENDER_TICK_MS = 30 * 1000; // re-render so refreshed cache shows up

export interface GlmCache {
  ts: number;
  pct5h?: number | null;
  recoverAt?: string;
  level?: string | null;
  used5h?: number | null;
  dayTokens?: number | null;
  monthTokens?: number | null;
}

// ===== cache IO (never throws) =====

/** Read the GLM cache. Returns null on any error. Never throws. */
export function readGlmCache(cachePath: string = CACHE_FILE): GlmCache | null {
  try {
    const raw = readFileSync(cachePath, "utf8");
    const c = JSON.parse(raw);
    return c && typeof c.ts === "number" ? (c as GlmCache) : null;
  } catch {
    return null;
  }
}

/** Detached-spawn the reused refresh script. Never throws, never blocks. */
export function triggerRefresh(refreshScript: string = REFRESH_SCRIPT): void {
  try {
    if (!existsSync(refreshScript)) return;
    const child = spawn(process.execPath, [refreshScript], {
      detached: true,
      stdio: "ignore",
    });
    child.unref();
  } catch {
    /* silent — status bar must not crash */
  }
}

// ===== segment builders =====

function colorFor(theme: ThemeLike, usedPct: number, text: string): string {
  return theme.fg(colorKey(usedPct), text);
}

interface ThemeLike {
  fg(color: string, text: string): string;
}

/** Context segment: bar + 剩N% + used/window. pct null (post-compaction) → "?". */
function contextSegment(
  theme: ThemeLike,
  usedTokens: number | null,
  windowSize: number,
  pct: number | null,
): string {
  if (pct === null) {
    return [
      theme.fg("dim", makeBar(0)),
      theme.fg("dim", "剩?%"),
      theme.fg("dim", "?/" + formatTokens(windowSize)),
    ].join(" ");
  }
  return [
    colorFor(theme, pct, makeBar(pct)),
    theme.fg(colorKey(pct), "剩" + remainingPct(pct) + "%"),
    theme.fg("dim", formatTokens(usedTokens || 0) + "/" + formatTokens(windowSize)),
  ].join(" ");
}

/** 5h quota segment from cache; degrades to "5h …" when missing or untrusted. */
function quotaSegment(theme: ThemeLike, cache: GlmCache | null, now: number): string {
  const trusted = !!(cache && cache.ts && now - cache.ts <= MAX_TRUST_MS);
  if (!trusted || typeof cache!.pct5h !== "number") {
    return theme.fg("dim", "5h …");
  }
  const parts = [
    colorFor(theme, cache!.pct5h, makeBar(cache!.pct5h)),
    theme.fg(colorKey(cache!.pct5h), "剩" + remainingPct(cache!.pct5h) + "%"),
  ];
  if (cache!.used5h != null) parts.push(theme.fg("dim", formatTokens(cache!.used5h)));
  if (cache!.recoverAt) parts.push(theme.fg("accent", "恢复~" + cache!.recoverAt));
  return parts.join(" ");
}

// ===== footer component =====

class GLMFooterComponent implements Component {
  private timer: NodeJS.Timeout | null = null;

  constructor(
    private ctx: any, // ExtensionContext (ctx.model, ctx.getContextUsage)
    private pi: any, // ExtensionAPI (pi.getThinkingLevel)
    private tui: any, // TUI (requestRender)
    private theme: ThemeLike,
    private speed: { lastTokPerSec: number | null },
    private footerData: any,
  ) {
    // Periodically request a re-render so a refreshed GLM cache updates the bars.
    this.timer = setInterval(() => {
      try {
        this.tui.requestRender();
      } catch {
        /* ignore */
      }
    }, RENDER_TICK_MS);
    if (this.timer && typeof this.timer.unref === "function") this.timer.unref();
  }

  invalidate(): void {
    /* stateless — nothing to cache-bust */
  }

  dispose(): void {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  render(width: number): string[] {
    try {
      const now = Date.now();
      let cache = readGlmCache();
      if (!cache || now - cache.ts > FRESH_MS) triggerRefresh();

      const model = this.ctx?.model;
      const modelRaw: string = model?.id || model?.name || "";
      const modelName = baseModelName(modelRaw);
      const levelRaw: string = (() => {
        try {
          return String(this.pi?.getThinkingLevel?.() ?? "off");
        } catch {
          return "off";
        }
      })();
      const level: string = levelRaw && levelRaw !== "off" ? levelRaw : "";

      const cu = this.ctx?.getContextUsage?.();
      const windowSize: number = cu?.contextWindow ?? model?.contextWindow ?? 200000;
      const usedTokens: number | null = cu?.tokens ?? null; // null right after compaction
      const pct: number | null = cu?.percent ?? null;

      const parts: (string | null)[] = [];
      parts.push(this.theme.fg("text", modelName)); // model
      // 扩展状态 (如 CTF 模式) —— 紧跟模型名，放在行首避免被右侧截断
      const statuses = this.footerData?.getExtensionStatuses?.();
      if (statuses) for (const [, text] of statuses) if (text) parts.push(text);
      if (level) parts.push(this.theme.fg("accent", "⚡" + level)); // thinking level
      if (typeof this.speed?.lastTokPerSec === "number")
        parts.push(this.theme.fg("dim", this.speed.lastTokPerSec + " tok/s")); // output speed
      parts.push(contextSegment(this.theme, usedTokens, windowSize, pct)); // context
      parts.push(quotaSegment(this.theme, cache, now)); // 5h quota
      if (cache?.dayTokens != null)
        parts.push(this.theme.fg("dim", "Day:") + this.theme.fg("text", formatTokens(cache.dayTokens)));
      if (cache?.monthTokens != null)
        parts.push(
          this.theme.fg("dim", "Mon:") + this.theme.fg("text", formatTokens(cache.monthTokens)),
        );

      let line = sumSegments(parts);
      if (visibleWidth(line) > width) line = truncateToWidth(line, width, "…");
      return [line];
    } catch {
      return [""]; // never crash the TUI
    }
  }
}

// ===== extension entry =====

interface SpeedState {
  lastTokPerSec: number | null; // most recent LLM call's output tok/s
  msgStartMs: number | null; // wall-clock start of current LLM call
}

export default function (pi: ExtensionAPI) {
  const speed: SpeedState = { lastTokPerSec: null, msgStartMs: null };

  // Per-LLM-call speed: message_start..message_end is pure generation time
  // (tool execution happens between calls, so it's excluded). This avoids the
  // turn-level bug where output/turnWallTime undercounts because a turn spans
  // many LLM calls + tool runs.
  pi.on("message_start", async () => {
    speed.msgStartMs = Date.now();
  });
  pi.on("message_end", async (event: any) => {
    const start = speed.msgStartMs;
    const out = event?.message?.usage?.output;
    if (start && typeof out === "number" && out > 0) {
      const secs = Math.max(0.001, (Date.now() - start) / 1000);
      speed.lastTokPerSec = Math.round(out / secs);
    }
    speed.msgStartMs = null;
  });

  pi.on("session_start", async (_event, ctx: any) => {
    if (ctx?.mode !== "tui") return; // only register in interactive TUI
    ctx.ui.setFooter(
      (tui: any, theme: ThemeLike, footerData: any) =>
        new GLMFooterComponent(ctx, pi, tui, theme, speed, footerData),
    );
  });
}

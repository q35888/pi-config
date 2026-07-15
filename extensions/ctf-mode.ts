/**
 * Pi CTF Mode Extension
 *
 * /ctf          切换 CTF 竞赛沙盒模式
 * /ctf on       显式开启
 * /ctf off      显式关闭
 * /ctf status   查看当前状态
 *
 * 开启时：动态注入安全研究技能 + 追加 CTF 沙盒提示词 + footer 状态
 * 关闭时：无 CTF 提示词、无 CTF 技能
 *
 * CTF 沙盒提示词不再硬编码在本文件，改为从 ~/.pi/agent/ctf-prompt.md 读取。
 * 调整 prompt 直接编辑该 md 即可，无需改代码。
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import * as os from "node:os";

const AGENT_DIR = join(os.homedir(), ".pi", "agent");
const STATE_FILE = join(AGENT_DIR, "ctf-mode.json");
// CTF 沙盒提示词文件（外部化，便于热更新）
const CTF_PROMPT_FILE = join(AGENT_DIR, "ctf-prompt.md");
// 递归扫描此目录，自动发现所有带 frontmatter 的 SKILL.md
const CTF_SKILLS_ROOT = join(AGENT_DIR, "ctf-skills", "zzy-reverse-skill");

// 从外部 md 文件读取 CTF 沙盒提示词；读取失败（文件不存在等）返回空串，等价于不注入
function readPrompt(): string {
  try {
    return readFileSync(CTF_PROMPT_FILE, "utf-8");
  } catch {
    return "";
  }
}

// 状态持久化到文件，跨 reload / session 存活
function readState(): boolean {
  try {
    return JSON.parse(readFileSync(STATE_FILE, "utf-8")).enabled === true;
  } catch {
    return false;
  }
}

function writeState(enabled: boolean): void {
  try {
    writeFileSync(STATE_FILE, JSON.stringify({ enabled }, null, 2));
  } catch {
    // 写入失败不阻塞
  }
}

export default function (pi: ExtensionAPI) {
  pi.registerCommand("ctf", {
    description: "切换 CTF 竞赛沙盒模式 (on/off/status)",
    argumentHint: "[on|off|status]",
    handler: async (args: string, ctx: any) => {
      const arg = args.trim().toLowerCase();

      if (arg === "status") {
        ctx.ui?.notify?.(`CTF 沙盒模式: ${readState() ? "开启" : "关闭"}`, "info");
        return;
      }

      let enable: boolean;
      if (arg === "on") enable = true;
      else if (arg === "off") enable = false;
      else enable = !readState();

      writeState(enable);

      if (enable) {
        ctx.ui?.setStatus?.("ctf", ctx.ui?.theme?.fg("accent", "● CTF"));
        ctx.ui?.notify?.("CTF 沙盒模式已开启 — 安全研究技能已加载，系统提示词已注入", "info");
      } else {
        ctx.ui?.setStatus?.("ctf", undefined);
        ctx.ui?.notify?.("CTF 沙盒模式已关闭", "info");
      }

      // reload 使技能增删立即生效
      await ctx.reload();
    },
  });

  // 恢复 footer 状态指示
  pi.on("session_start", (_event: any, ctx: any) => {
    if (readState()) {
      ctx.ui?.setStatus?.("ctf", ctx.ui?.theme?.fg("accent", "● CTF"));
    }
  });

  // CTF 开启时注入技能路径
  pi.on("resources_discover", () => {
    if (!readState()) return {};
    if (!existsSync(CTF_SKILLS_ROOT)) {
      console.warn(`[ctf-mode] skills root not found: ${CTF_SKILLS_ROOT}`);
      return {};
    }
    return { skillPaths: [CTF_SKILLS_ROOT] };
  });

  // CTF 开启时追加沙盒提示词（从 ctf-prompt.md 读取，文件缺失则不追加）
  pi.on("before_agent_start", (event: any) => {
    if (!readState()) return undefined;
    const prompt = readPrompt();
    if (!prompt) return undefined;
    return { systemPrompt: event.systemPrompt + "\n" + prompt };
  });
}

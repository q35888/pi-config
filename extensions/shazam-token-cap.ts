/**
 * Pi 扩展: shazam-token-cap — 给 pi-shazam 的所有工具调用注入默认 maxTokens 上限。
 *
 * 为什么需要它:
 *   pi-shazam 的工具(overview/lookup/impact/verify/changes/format/rename_symbol)
 *   每个都接受一个 maxTokens 参数来截断输出,但它纯粹是「每次调用参数」——
 *   .pi-shazam/config.json 没有全局默认位(config.ts 只有 verify.maxFiles)。
 *   不传 maxTokens 就完全不截断,shazam_overview 在大项目上能一口气吐 3-5K tokens。
 *
 *   本扩展在 tool_call 事件里拦截所有 shazam_* 调用:如果调用方没显式传 maxTokens,
 *   就原地注入一个默认值(event.input 是 mutable 的,pi 官方支持)。
 *   显式传了的值不覆盖 —— 尊重调用方意图。
 *
 * 配置(settings.json):
 *   {
 *     "shazamTokenCap": {
 *       "default": 2000,          // 全局默认上限(tokens)
 *       "overview": 3000,         // overview 单独放宽(它是结构总览,信息量大)
 *       "lookup": 1500,
 *       "impact": 1500,
 *       "verify": 800,            // verify 本来就精简
 *       "changes": 800,
 *       "format": 400,
 *       "rename_symbol": 400,
 *       "notify": false           // 是否每次注入都 notify(调试用,默认 false)
 *     }
 *   }
 *   所有字段可选;只给 default 也行。
 *
 * 原理: pi 扩展 tool_call 事件,event.input 可原地修改(Mutable)。
 *       文档原文: "Mutate it in place to patch tool arguments before execution."
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const SHAZAM_PREFIX = "shazam_";

// 工具名 → 配置 key 的映射(去掉前缀)
function toolConfigKey(toolName: string): string | null {
	if (!toolName.startsWith(SHAZAM_PREFIX)) return null;
	return toolName.slice(SHAZAM_PREFIX.length); // "overview" / "lookup" / ...
}

// 读 settings.json 里的 shazamTokenCap 配置(容错)
function readConfig(pi: ExtensionAPI): Record<string, number | boolean> {
	try {
		const settings = (pi as any).getSettings?.() ?? {};
		const cap = settings.shazamTokenCap ?? {};
		return cap;
	} catch {
		return {};
	}
}

// 每个工具的合理默认上限(tokens)。
// 依据: 源码量过的典型返回量 + 截断机制(truncateOutput 按行截,留高优先级行)。
// overview 最大(模块依赖图+top10 pagerank+热点),给宽点;verify/changes 本就精简。
const TOOL_DEFAULTS: Record<string, number> = {
	overview: 3000,
	lookup: 1500,
	impact: 1500,
	verify: 800,
	changes: 800,
	format: 400,
	rename_symbol: 400,
};
const GLOBAL_DEFAULT = 2000;

export default function (pi: ExtensionAPI) {
	pi.on("tool_call", async (event: any, ctx: any) => {
		const toolName: string = event?.toolName ?? "";
		const key = toolConfigKey(toolName);
		if (key === null) return; // 不是 shazam 工具,放行

		const input = event?.input;
		if (!input || typeof input !== "object") return;

		// 用户/agent 已显式传了 maxTokens → 不覆盖,尊重意图
		if (input.maxTokens !== undefined && input.maxTokens !== null) return;

		const cfg = readConfig(pi);
		const perTool = typeof cfg[key] === "number" ? (cfg[key] as number) : TOOL_DEFAULTS[key];
		const globalDefault = typeof cfg.default === "number" ? (cfg.default as number) : GLOBAL_DEFAULT;
		const cap = perTool ?? globalDefault;

		// 原地注入
		input.maxTokens = cap;

		// 可选 notify(调试用)
		if (cfg.notify === true) {
			try {
				ctx?.ui?.notify?.(`shazam-token-cap: ${toolName} ← maxTokens=${cap}`, "info");
			} catch {
				/* notify 失败不影响主流程 */
			}
		}
	});

	// 注册一个命令查看当前生效配置,方便调
	// 注意: pi 的 registerCommand 签名是 registerCommand(name, options),
	// handler 的 args 是单个 string(整个参数串),不是数组。
	pi.registerCommand?.("shazam-cap", {
		description: "Show effective shazam-token-cap config",
		handler: async (_args: string, ctx: any) => {
			const cfg = readConfig(pi);
			const globalDefault = typeof cfg.default === "number" ? cfg.default : GLOBAL_DEFAULT;
			const lines: string[] = [`shazam-token-cap — effective config:`, `  global default: ${globalDefault} tokens`, ``, `  per-tool:`];
			for (const [tool, def] of Object.entries(TOOL_DEFAULTS)) {
				const eff = typeof cfg[tool] === "number" ? cfg[tool] : def;
				const src = typeof cfg[tool] === "number" ? "(settings)" : "(builtin)";
				lines.push(`    shazam_${tool.padEnd(14)} ${eff} tokens ${src}`);
			}
			lines.push(``, `Configure in ~/.pi/agent/settings.json under "shazamTokenCap".`);
			lines.push(`Set "notify": true to see injections live.`);
			try {
				ctx?.ui?.notify?.(lines.join("\n"), "info");
			} catch {
				/* noop */
			}
		},
	});

	// 启动提示(只通知,不阻塞)
	pi.on("session_start", async (_event: any, ctx: any) => {
		try {
			const cfg = readConfig(pi);
			const globalDefault = typeof cfg.default === "number" ? cfg.default : GLOBAL_DEFAULT;
			ctx?.ui?.notify?.(`shazam-token-cap active (default ${globalDefault}t, /shazam-cap to view)`, "info");
		} catch {
			/* noop */
		}
	});
}

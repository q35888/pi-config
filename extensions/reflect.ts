/**
 * Reflect Extension — Structured self-reflection tool (no side effects).
 *
 * Inspired by Cursor's reverse-engineered `reflect` tool_call.
 * Forces a 5-axis cognitive chain after failures or before high-stakes
 * decisions, to prevent premature convergence and shallow diagnosis.
 *
 * Fields (mirror Cursor's ReflectArgs protobuf):
 *   1. unexpected_action_outcomes  — what actually happened vs expected
 *   2. relevant_instructions       — which requirements matter now
 *   3. scenario_analysis           — 2+ distinct competing hypotheses
 *   4. critical_synthesis          — pick one, justify
 *   5. next_steps                  — concrete next action (must differ from failed approach)
 *   6. tool_call_id (optional)     — link back to the triggering tool result
 *
 * Anti-laziness enforcement: throws if fields are empty/placeholder, or if
 * scenario_analysis lists fewer than 2 hypotheses. This is the whole point —
 * a reflection that isn't forced to enumerate alternatives is worthless.
 *
 * NOTE (optional enhancement, not enabled by default): auto-trigger.
 *   Hook `tool_result`, and when `event.isError === true`, nudge via
 *   `ctx.ui.setStatus("reflect", "consider reflect()")`. Avoids forcing it
 *   (heavy-handed) while nudging the model toward reflection.
 *   Verified: `ctx.ui.setStatus` exists (pi extensions.md L167/L736/L2458).
 *   Caveat: must self-throttle so repeated failures don't spam, and must
 *   NOT re-nudge when the failure was reflect's own validation rejection
 *   (anti-self-excitation).
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Text } from "@earendil-works/pi-tui";
import { Type } from "typebox";

interface ReflectDetails {
	unexpected_action_outcomes: string;
	relevant_instructions: string;
	scenario_analysis: string;
	critical_synthesis: string;
	next_steps: string;
	tool_call_id?: string;
	reflectedAt: string;
}

const ReflectParams = Type.Object({
	unexpected_action_outcomes: Type.String({
		description: "What actually happened vs expected. Quote the error or describe the surprising result concretely.",
	}),
	relevant_instructions: Type.String({
		description: "Which parts of the original request, conventions, or constraints are most relevant here.",
	}),
	scenario_analysis: Type.String({
		description: "2-4 DISTINCT possible explanations, as a numbered list (1. 2. 3.). Force yourself past the first guess.",
	}),
	critical_synthesis: Type.String({
		description: "The single most likely explanation from those you listed, and why.",
	}),
	next_steps: Type.String({
		description: "Concrete next action. If reflecting on a failure, must differ from the approach that failed.",
	}),
	tool_call_id: Type.Optional(
		Type.String({
			description: "tool_call_id of the tool result that triggered this reflection, if applicable.",
		}),
	),
});

/**
 * Heuristic: count distinct hypotheses in scenario_analysis.
 * Recognizes enumerated lists (1. 2. / 1) 2) / 1: 2: / - * / a) b)) and
 * hypothesis marker words (maybe/another/Hypothesis/theory/option/...).
 */
function countHypotheses(text: string): number {
	const lines = text
		.split("\n")
		.map((l) => l.trim())
		.filter(Boolean);
	// Enumerated list items: "1." "2)" "1:" "-" "*" "a)" "b:"
	const enumerated = lines.filter((l) => /^([0-9]+[.):]|[-*•]|\(?[a-z][.):])\s+/i.test(l)).length;
	if (enumerated >= 2) return enumerated;
	// Hypothesis marker words (covers "Hypothesis 1: ...", "another theory", etc.)
	const markers = text.match(
		/\b(maybe|perhaps|possibly|could be|might|hypoth(?:esis|eses)|theory|theories|option|guess|one (?:reason|explanation|possibility)|another|alternatively|or (?:it|this|that))\b/gi,
	);
	return Math.max(lines.length, markers ? markers.length : 0);
}

export default function (pi: ExtensionAPI) {
	pi.registerTool({
		name: "reflect",
		label: "Reflect",
		description:
			"Structured self-reflection with NO side effects. Call when a tool call failed or gave an unexpected result, when uncertain about root cause, before retrying a failed approach, or before a high-stakes decision. Forces a 5-axis chain: what happened → relevant requirements → competing hypotheses → judgment → next action. Prevents premature convergence.",
		promptSnippet: "Structured 5-axis reflection to diagnose failures and prevent premature convergence",
		promptGuidelines: [
			"Use reflect whenever a tool call fails or yields an unexpected result — it forces you to enumerate 2+ competing hypotheses before concluding. This complements (not replaces) your normal reasoning.",
			"Before retrying a failed approach, call reflect first; its next_steps must differ from what already failed.",
			"reflect's critical_synthesis must pick ONE scenario from those you listed, not introduce new explanations.",
			"Reserve reflect for genuine uncertainty, failures, or high-stakes decisions — not routine successes.",
		],
		parameters: ReflectParams,

		async execute(_toolCallId, params, _signal, _onUpdate, _ctx) {
			// === Anti-laziness validation ===
			// Reflect only works if fields are filled with genuine content.
			const required = [
				"unexpected_action_outcomes",
				"relevant_instructions",
				"scenario_analysis",
				"critical_synthesis",
				"next_steps",
			] as const;

			// Placeholder words that pass a length check but carry no real analysis.
			// Tokens may repeat separated by whitespace: "n/a n/a", "same same".
			const PLACEHOLDER = /^(?:\s*(?:n\/a|na|tbd|tba|unknown|none|nothing|see above|same|\.|-|\?))+\s*$/i;
			for (const field of required) {
				const val = params[field];
				if (typeof val !== "string" || val.trim().length < 12 || PLACEHOLDER.test(val.trim())) {
					throw new Error(
						`reflect: field "${field}" needs genuine analysis (got: "${val ?? "(empty)"}"). ` +
							`Reflection forces real thinking; don't bypass it with a placeholder.`,
					);
				}
			}

			// Force scenario_analysis to enumerate 2+ hypotheses — the core anti-anchoring check.
			const hypothesisCount = countHypotheses(params.scenario_analysis);
			if (hypothesisCount < 2) {
				throw new Error(
					`reflect: scenario_analysis must list at least 2 DISTINCT hypotheses (detected ${hypothesisCount}). ` +
						`Use numbered items (1. 2. 3.). The point is to push past your first guess. ` +
						`Got: "${params.scenario_analysis.slice(0, 200)}"`,
				);
			}

			const details: ReflectDetails = {
				...params,
				reflectedAt: new Date().toISOString(),
			};

			return {
				content: [
					{
						type: "text",
						// Neutral confirmation — don't present the heuristic count as a hard fact.
						text:
							`Reflection recorded. Hypotheses enumerated, proceeding with the chosen diagnosis.\n\n` +
							`▸ Synthesis: ${params.critical_synthesis}\n` +
							`▸ Next: ${params.next_steps}`,
					},
				],
				details,
			};
		},

		renderCall(args, theme, _context) {
			const a = args as Partial<ReflectDetails>;
			const label = theme.fg("toolTitle", theme.bold("reflect"));
			const preview = a.next_steps ? a.next_steps.slice(0, 60) : "(diagnosing)";
			return new Text(`${label} ${theme.fg("dim", preview)}`, 0, 0);
		},

		renderResult(result, { expanded }, theme, _context) {
			const details = result.details as ReflectDetails | undefined;
			const text0 = result.content?.[0];
			// When execute throws, details is undefined and isError is set on the result.
			// Distinguish rejection (red ✗) from success (green ✓) so the TUI isn't misleading.
			if (!details) {
				const msg = text0?.type === "text" ? text0.text : "";
				const prefix = result.isError
					? theme.fg("error", "✗ Reflect rejected: ")
					: theme.fg("success", "✓ ");
				return new Text(prefix + theme.fg("muted", msg), 0, 0);
			}

			if (!expanded) {
				// Collapsed: synthesis + next step only.
				return new Text(
					theme.fg("success", "✓ ") +
						theme.fg("muted", "Synthesis: ") +
						theme.fg("text", details.critical_synthesis.slice(0, 80)) +
						"\n" +
						theme.fg("accent", "→ ") +
						theme.fg("text", details.next_steps.slice(0, 80)),
					0,
					0,
				);
			}

			// Expanded: full 5-axis cognitive chain.
			const section = (title: string, body: string) =>
				theme.fg("accent", title) +
				" " +
				theme.fg("borderMuted", "─") +
				"\n" +
				theme.fg("muted", body);

			const full =
				theme.fg("success", "✓ Reflection") +
				"\n\n" +
				section("What happened", details.unexpected_action_outcomes) +
				"\n\n" +
				section("Relevant requirements", details.relevant_instructions) +
				"\n\n" +
				section("Hypotheses", details.scenario_analysis) +
				"\n\n" +
				section("Judgment", details.critical_synthesis) +
				"\n\n" +
				section("Next action", details.next_steps) +
				(details.tool_call_id
					? `\n\n${theme.fg("dim", `(triggered by ${details.tool_call_id})`)}`
					: "");

			return new Text(full, 0, 0);
		},
	});
}

/**
 * Catch the most expensive thing this model does: writing the file into its reply.
 *
 * Measured on the entity-snapshot task, one assistant turn produced 4,299 output tokens in 239
 * seconds: 1,711 characters of thinking, 13,246 characters of the test file written out as
 * prose, and a 301-character bash call. At ~18 tokens/s that is four minutes spent typing code
 * that then had to be written again inside the tool call.
 *
 * A system-prompt rule ("never write code into your reply") is the prevention, and it is cheap.
 * This is the correction for when the rule does not hold: the next tool result carries one line
 * pointing at what just happened, so the model stops rather than doing it again next turn.
 *
 * Gated by BONSAI_NUDGE_PROSE_CHARS, unset by default, so both arms of a measurement share the
 * same launcher.
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

/** Tools that put content on disk; a reply containing one of these is not waste. */
const MUTATING_TOOLS: Record<string, true> = { write: true, edit: true };

export default function nudgeCodeInProse(pi: ExtensionAPI): void {
	const threshold = Number(process.env.BONSAI_NUDGE_PROSE_CHARS ?? 0);
	if (!Number.isFinite(threshold) || threshold <= 0) {
		return;
	}

	let pending: number | undefined;

	pi.on("message_end", async (event) => {
		const message = event.message;
		if (message.role !== "assistant" || !Array.isArray(message.content)) {
			return undefined;
		}
		const callsWrite = message.content.some(
			(block) => block.type === "toolCall" && MUTATING_TOOLS[String(block.name)],
		);
		const prose = message.content.reduce(
			(total, block) => (block.type === "text" ? total + block.text.length : total),
			0,
		);
		pending = !callsWrite && prose >= threshold ? prose : undefined;
		return undefined;
	});

	pi.on("tool_result", async (event) => {
		const prose = pending;
		pending = undefined;
		if (prose === undefined || event.isError) {
			return undefined;
		}
		return {
			content: [
				...event.content,
				{
					type: "text" as const,
					text:
						`\n\n[your previous reply contained ${prose} characters of code and no write call. ` +
						`That is minutes of generation that put nothing on disk. Send it with write or edit.]`,
				},
			],
		};
	});
}

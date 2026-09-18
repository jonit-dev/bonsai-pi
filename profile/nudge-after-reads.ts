/**
 * Nudge the model out of reconnaissance.
 *
 * Measured on this model: it reads and greps long past the point of usefulness — twelve turns
 * of reads before its first write on one task, eighteen on another — and each turn costs a
 * reasoning pass plus generation at ~18 t/s. Telling it in the system prompt "at most four
 * reads before your first write" did not hold. This puts the same thing in the tool result it
 * is already reading, at the moment it has run out of excuses, and costs nothing until it fires.
 *
 * Gated by BONSAI_NUDGE_AFTER so it can be measured against a run without it:
 *
 *   BONSAI_NUDGE_AFTER=6 bin/bonsai-pi ...
 *
 * Unset, or 0, the extension does nothing.
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

/** Tools that change the working tree. A write resets the streak - it is the thing we want. */
const MUTATING_TOOLS: Record<string, true> = { write: true, edit: true };

export default function nudgeAfterReads(pi: ExtensionAPI): void {
	const threshold = Number(process.env.BONSAI_NUDGE_AFTER ?? 0);
	if (!Number.isFinite(threshold) || threshold <= 0) {
		return;
	}

	let readsSinceWrite = 0;

	pi.on("tool_result", async (event) => {
		// A successful write is the thing we want; anything else - a read, a search, or an edit
		// that was rejected - is a call that did not change the tree.
		if (MUTATING_TOOLS[event.toolName] && !event.isError) {
			readsSinceWrite = 0;
			return undefined;
		}

		readsSinceWrite += 1;
		if (readsSinceWrite < threshold) {
			return undefined;
		}

		// Fire on the threshold and every second call after it, so a model that keeps reading is
		// reminded without every result carrying a paragraph.
		if ((readsSinceWrite - threshold) % 2 !== 0) {
			return undefined;
		}

		return {
			content: [
				...event.content,
				{
					type: "text" as const,
					text:
						`\n\n[${readsSinceWrite} read/search calls since your last file change. Stop looking: ` +
						`write the file with what you already have and let the check tell you what is wrong.]`,
				},
			],
		};
	});
}

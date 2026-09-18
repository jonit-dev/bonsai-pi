/**
 * Break the model out of a loop of the same call.
 *
 * Measured on the entity-snapshot task (baseline run, 21 turns, 1,476 s of agent time): the model
 * re-ran one identical `bash` command **fifteen times**. Each pass costs a reasoning round plus
 * generation at ~18 t/s, and the run was killed by the 1,800 s wall clock without ever passing.
 * Nothing in the harness noticed. The read nudge counts read calls, but this loop was not reads -
 * it was the same command over and over.
 *
 * pi's `tool_call` hook can block, so this counts calls by tool name plus arguments and refuses
 * the ones past a threshold, returning the reason to the model instead of the result it wanted.
 * A repeated call is not always wrong - running a test twice is normal - so the threshold is
 * permissive and the block names the count, which is the part the model cannot see about itself.
 *
 * Gated by BONSAI_MAX_REPEATS so it can be measured against a run without it:
 *
 *   BONSAI_MAX_REPEATS=3 bin/bonsai-pi ...
 *
 * Unset, or 0, the extension does nothing.
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

/** Stable key for the call: same tool, same arguments, regardless of key order. */
function keyOf(toolName: string, input: unknown): string {
	return `${toolName}:${JSON.stringify(input, (_k, v) => {
		if (v === null || typeof v !== "object" || Array.isArray(v)) {
			return v;
		}
		const sorted: Record<string, unknown> = {};
		for (const k of Object.keys(v as Record<string, unknown>).sort()) {
			sorted[k] = (v as Record<string, unknown>)[k];
		}
		return sorted;
	})}`;
}

export default function blockRepeats(pi: ExtensionAPI): void {
	const max = Number(process.env.BONSAI_MAX_REPEATS ?? 0);
	if (!Number.isFinite(max) || max <= 0) {
		return;
	}

	const counts = new Map<string, number>();

	pi.on("tool_call", async (event) => {
		const key = keyOf(event.toolName, event.input);
		const n = (counts.get(key) ?? 0) + 1;
		counts.set(key, n);
		if (n <= max) {
			return undefined;
		}

		return {
			block: true,
			reason:
				`You have already made this exact ${event.toolName} call ${n - 1} times and it is not ` +
				`getting you anywhere. Change something: if the check is failing, edit the file it names; ` +
				`if you are looking for something, search for a different string. Repeating the call will ` +
				`be refused.`,
		};
	});
}

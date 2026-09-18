/**
 * Make the model edit instead of re-writing the whole file.
 *
 * Measured on the entity-snapshot task: it wrote a 186-line spec, the check failed on one parse
 * error, and the model answered by writing the entire file again — 2,570 output tokens, 208
 * seconds — then did it once more. The profile asks for targeted edits in prose ("send only the
 * lines that change"); prose does not hold on this model, and at ~18 t/s a whole-file rewrite is
 * minutes of generation to change one line.
 *
 * pi's `tool_call` hook can block, so this blocks: once this session has written a path, further
 * `write` calls to it are refused with a reason naming the tool to use instead. The first write
 * of any path is untouched — new files and one-shot overwrites are legitimate work. `edit` is not
 * restricted at all, so a file can always still be changed.
 *
 * BONSAI_BLOCK_REWRITE=0 disables it.
 */
import { resolve } from "node:path";
import { isToolCallEventType, type ExtensionAPI } from "@earendil-works/pi-coding-agent";

export default function blockRewrite(pi: ExtensionAPI): void {
	if (process.env.BONSAI_BLOCK_REWRITE === "0") {
		return;
	}

	// Dynamic membership, and it has to survive the whole extension session.
	const writtenThisSession = new Set<string>();

	pi.on("tool_call", async (event, ctx) => {
		if (!isToolCallEventType("write", event)) {
			return undefined;
		}

		const path = resolve(ctx.cwd, event.input.path);
		if (!writtenThisSession.has(path)) {
			writtenThisSession.add(path);
			return undefined;
		}

		return {
			block: true,
			reason:
				`You already wrote ${event.input.path} in this session, and the check reports what is ` +
				`still wrong with it. Change it with edit, sending only the lines that differ - a ` +
				`whole-file rewrite costs minutes of generation and is what you are being asked to stop doing.`,
		};
	});
}

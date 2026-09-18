/**
 * Run the project's check after every file change, and put the result in front of the model.
 *
 * This is an extension rather than a prompt rule on purpose. On this model, "run the check" as
 * prose is a decision it skips: measured on the threenative-engine task, it rewrote the same
 * broken call three times without ever running the test, because reasoning about what the test
 * would do feels like progress. A `tool_result` hook turns the result into part of the tool's
 * own output, which the model cannot skip past.
 *
 * Costs no prompt tokens - it only appends to results that already exist.
 *
 *   BONSAI_CHECK_COMMAND='npx vitest run packages/core/__tests__/foo.spec.ts' bin/bonsai-pi ...
 *
 * Scope the command to what the task touches. It runs after every write and edit, so a full
 * suite here is a full suite per keystroke. Unset, this extension does nothing.
 */
import { exec } from "node:child_process";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

/** Tools whose success means the working tree changed. `bash` is excluded: it would recurse. */
const MUTATING_TOOLS: Record<string, true> = { write: true, edit: true };
const MAX_OUTPUT_CHARS = 3000;
const TIMEOUT_MS = 180_000;

interface ExecError extends Error {
	code?: number | string;
}

export default function checkAfterEdit(pi: ExtensionAPI): void {
	const command = process.env.BONSAI_CHECK_COMMAND?.trim();
	if (!command) {
		return;
	}

	pi.on("tool_result", async (event, ctx) => {
		if (!MUTATING_TOOLS[event.toolName] || event.isError) {
			return undefined;
		}

		const outcome = await new Promise<{ code: number; output: string }>((resolve) => {
			exec(
				command,
				{ cwd: ctx.cwd, timeout: TIMEOUT_MS, maxBuffer: 4 * 1024 * 1024, signal: ctx.signal },
				(error, stdout, stderr) => {
					const failure = error as ExecError | null;
					resolve({
						code: typeof failure?.code === "number" ? failure.code : error ? 1 : 0,
						output: `${stdout}${stderr}`,
					});
				},
			);
		});

		const body =
			outcome.output.length > MAX_OUTPUT_CHARS
				? `${outcome.output.slice(0, MAX_OUTPUT_CHARS)}\n[check output truncated]`
				: outcome.output;
		const verdict = outcome.code === 0 ? "exited 0" : `exited ${outcome.code} - the check FAILS`;

		return {
			content: [...event.content, { type: "text" as const, text: `\n\n[check: ${command} ${verdict}]\n${body}` }],
		};
	});
}

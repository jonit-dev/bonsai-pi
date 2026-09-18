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

/**
 * A file the bundler could not parse is not a failing test - the tests never ran.
 *
 * Measured on the entity-snapshot task: the model wrote a spec with an unterminated string at
 * line 86, vitest answered `Error: Transform failed with 1 error: [PARSE_ERROR] Unterminated
 * string`, and the model read that as a broken environment. It spent the next fifteen turns
 * grepping node_modules for "oxc" and "Transform failed" and never touched its own line 86, then
 * the 1800 s wall clock ended the run. The error already names the file, line and column; what it
 * does not say is *whose* fault it is, and that is the part this adds.
 */
function parseError(output: string): { file: string; line: string; column: string; message: string } | undefined {
	// The report is ANSI-coloured, so the widths between the words are escape codes, not spaces.
	const clean = output.replace(/\u001b\[[0-9;]*m/g, "");
	const marker = clean.match(/\[PARSE_ERROR\]\s*([^\n]+)/);
	if (!marker) {
		return undefined;
	}
	const at = clean.slice(marker.index).match(/([\w./-]+\.[A-Za-z]+):(\d+):(\d+)/);
	if (!at) {
		return undefined;
	}
	return { file: at[1], line: at[2], column: at[3], message: marker[1].trim() };
}

export default function checkAfterEdit(pi: ExtensionAPI): void {
	const command = process.env.BONSAI_CHECK_COMMAND?.trim();
	if (!command) {
		return;
	}

	const stopAt = Number(process.env.BONSAI_CHECK_MAX_FAILURES ?? 5);

	// Consecutive failures of the same check, reset by a passing run. This is the whole point:
	// without it the blast radius is unbounded - measured, the model rewrote the same file twice
	// at 208 s each, and burned five attempts on another module before a human killed the run.
	let failures = 0;
	let stopped = false;

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

		if (outcome.code === 0) {
			failures = 0;
			return {
				content: [...event.content, { type: "text" as const, text: `\n\n[check: ${command} exited 0]\n${body}` }],
			};
		}

		failures += 1;
		const verdict = `exited ${outcome.code} - the check FAILS`;
		const parse = parseError(outcome.output);
		let advice = "";
		if (parse) {
			// Say whose fault it is. The raw error already names file, line and column; the model's
			// mistake is reading a parse failure as an environment problem.
			advice =
				`\n\n[the check ran no tests: ${parse.file} does not parse - "${parse.message}" at line ` +
				`${parse.line}, column ${parse.column}. That is a syntax error in the file you just wrote, not ` +
				`a problem with vitest, oxc, node_modules or the config. Read that line and fix it with one ` +
				`edit. Do not investigate the toolchain.]`;
		} else if (failures >= stopAt) {
			stopped = true;
			advice = `\n\n[this check has failed ${failures} times in a row. Stop changing code and report what you ` +
				`have: the last error, what you tried, and what you think the cause is.]`;
		} else if (failures >= 3) {
			advice = `\n\n[${failures} failures in a row. Change only the lines the error names, with one edit, ` +
				`and nothing else.]`;
		} else if (failures >= 2) {
			advice = `\n\n[second failure. Do not rewrite the file - fix only what the error names.]`;
		}

		return {
			content: [
				...event.content,
				{ type: "text" as const, text: `\n\n[check: ${command} ${verdict}]\n${body}${advice}` },
			],
		};
	});

	// The stop itself: the next call that would change code is refused and ends the run, so a
	// loop costs one attempt rather than the rest of the card's afternoon.
	pi.on("tool_call", async (event) => {
		if (!stopped || !MUTATING_TOOLS[event.toolName]) {
			return undefined;
		}
		return {
			block: true,
			terminate: true,
			reason: `The check has failed ${failures} consecutive times. Stopping instead of trying again.`,
		};
	});
}

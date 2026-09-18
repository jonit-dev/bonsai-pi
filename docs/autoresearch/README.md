# autoresearch loop: the harness

An experiment loop over bonsai-pi's own harness. The point is to improve the *agent's* results on
a fixed task, one change at a time, with the numbers written down — not to feel productive.

## Contract

| | |
|---|---|
| **Objective** | `min` **`wall_s`** — seconds from launch to the last assistant message. That is the cost the user actually pays. |
| **Secondary metrics** | `turns`, `check_fails`, `rewrites` (writes over 2000 chars of arguments), `prose_max` (largest code dump in a reply), `blocked`, `stopped`. |
| **Gate** | The spec must pass, verified independently by the evaluator (`passed`), and the fork's own tests must stay green. A trial that loses the gate is discarded regardless of `wall_s`. |
| **Evaluator** | `tests/eval_agent_task.py` — **immutable** for the duration of the run. If it changes, the ledger is worthless. |
| **Fixture** | The `entity-snapshot.ts` task, embedded in the evaluator. **Immutable.** |
| **Budget** | One trial = one full run, timeout 1800 s. Server restart included (~60 s) and identical for every trial. |
| **Mutable surface** | `profile/*.md`, `profile/*.ts`, `bin/bonsai-pi`, and — if a hypothesis demands it — a named source file under `packages/coding-agent/src/`, declared in the trial's `arm` string. |
| **Immutable surface** | The evaluator, the fixture, `CHECK_CMD`, the parser, and the runner. |
| **Noise** | The model is stochastic and two runs of the *same* task have differed by 16%. The baseline is run twice; nothing is ranked on a single run. |

## Isolation

Branch `autoresearch/harness-run1`. Nothing is pushed — the loop is local and the ledger is
gitignored, so experiment commits stay code-only. The worktree under test is
`threenative-engine/.worktrees/bonsai-e2e`; the main checkout is never touched.

## Running a trial

```sh
python3 tests/eval_agent_task.py --trial 3 --arm "blocker off" --env BONSAI_BLOCK_REWRITE=0
```

It resets the worktree, restarts llama-server, runs the agent, verifies the result itself, parses
the transcript, and appends one row to `results.tsv` (gitignored). Raw agent output lands in
`logs/`.

```sh
python3 tests/diagnose_session.py 1     # turn a transcript into a hypothesis
```

## Statuses

`baseline` · `keep` · `discard` · `crash` · `timeout` · `invalid` · `fail`

`invalid` means the measurement was not trustworthy — the model server died under the run, or the
only server slot was occupied by an orphaned request from a killed trial. Those trials get fixed
and re-run, never papered over.

## Two defects the setup itself had, found before any baseline was accepted

1. **A killed trial wedges the next one.** llama-server runs `-np 1`; a killed client left its
   request generating into a dead socket, holding the only slot. The next trial's request queued
   and the run looked hung while the GPU churned tokens for nobody. Fix: the evaluator restarts
   the server before every trial, which also makes trials start from identical state.
2. **A trial with no server is not a measurement.** One trial died at 13,542 tokens with
   `Received second interrupt`. Fix: health asserted before and after; a dead server records
   `invalid` instead of a plausible-looking row.

## What the baseline actually did

```
trial  arm       wall_s  turns  check_fails  rewrites  prose_max  blocked  passed  status
1      baseline    1476     21            1         1       6070        0       0  timeout
```

It wrote the spec once (4,880 characters), the check failed, and it never changed a file again.
The check's answer was `Error: Transform failed with 1 error: [PARSE_ERROR] Unterminated string`
at `entity-snapshot.spec.ts:86:68` - a syntax error in the file it had just written. It read that
as a broken environment instead, and spent the rest of the run in `node_modules` looking for
`oxc`. The 1,800 s wall clock ended it.

The failure is not that the model is slow. It is that the check result, which already names the
file and the exact position, does not say whose file it is.

## Hypotheses queued

In the order the evidence supports them:

1. **Parse errors are reported as the model's own syntax error** (implemented: `parseError()` in
   `check-after-edit.ts`). The message already had file, line and column; the change says it is a
   syntax error in the file just written and that vitest, oxc and the config are not the problem.
   Trial 2.
2. **The output cap can hide the error.** `MAX_OUTPUT_CHARS` keeps the head of the check output,
   and vitest prints its summary before the failure detail. Not the binding constraint here (the
   parse error survived at ~700 characters) but it will be on a noisier suite.
3. **Reconnaissance**: 6 reader calls before the first write, every run. The read nudge exists and
   had never been switched on.
4. **`--reasoning N`**: 512 against the 1024 default.

A fifth hypothesis was withdrawn: the first diagnostics reported the same bash command repeated
**fifteen times**, which motivated a repeat-breaking extension. The commands are 23 *distinct*
calls that share a `cd <worktree> && ` prefix, and the metric keyed on a 60-character prefix. No
true repeats exist in the baseline. The extension was reverted and the metric fixed.


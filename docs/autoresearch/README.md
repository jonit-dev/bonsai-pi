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
| **Noise** | Two runs of the *same code* have passed in 363 s and timed out at 1,801 s — a 5x spread on one task. Nothing here is ranked on one run, and nothing is ranked on two. |

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

`ok` · `fail` · `timeout` · `invalid` — these are what the evaluator emits.

`fail` is a run that ended cleanly without passing the check. `timeout` is the 1,800 s wall clock
ending a run that had not finished. `invalid` means the measurement was not trustworthy — the
model server died under the run, or the only server slot was occupied by an orphaned request from
a killed trial. Those get fixed and re-run, never papered over.

Keep or discard is a human decision, recorded in the trial's `arm` string, not a status column.

## Two defects the setup itself had, found before any baseline was accepted

1. **A killed trial wedges the next one.** llama-server runs `-np 1`; a killed client left its
   request generating into a dead socket, holding the only slot. The next trial's request queued
   and the run looked hung while the GPU churned tokens for nobody. Fix: the evaluator restarts
   the server before every trial, which also makes trials start from identical state.
2. **A trial with no server is not a measurement.** One trial died at 13,542 tokens with
   `Received second interrupt`. Fix: health asserted before and after; a dead server records
   `invalid` instead of a plausible-looking row.

## Results

```
trial  arm                     wall_s  turns  check_fails  rewrites  prose_max  passed  status
1      baseline (pre-advice)     1476     21            1         1       6070       0  timeout
2      baseline (pre-advice)      363     10            1         1        989       1  ok
3      parse advice              1256     22            3         1        223       1  ok
4      baseline (post-advice)     681     12            1         1       1110       1  ok
```

Trial 1 wrote the spec once (4,880 characters), the check failed, and it never changed a file
again. The check's answer was `Error: Transform failed with 1 error: [PARSE_ERROR] Unterminated
string` at `entity-snapshot.spec.ts:86:68` - a syntax error in the file it had just written. It
read that as a broken environment instead, and spent the rest of the run in `node_modules`
looking for `oxc`. The 1,800 s wall clock ended it.

The failure is not that the model is slow. It is that the check result, which already names the
file and the exact position, does not say whose file it is.

## The one change that was kept

`parseError()` in `check-after-edit.ts`. When the check output contains a parse error, the advice
that already exists on failure is replaced with the file, line and column, and the sentence that
the model was missing: this is a syntax error in the file you just wrote, not a problem with
vitest, oxc, node_modules or the config.

Evidence, from the transcripts rather than from the run times:

| | trial 1 (no advice) | trial 3 (advice) |
|---|---|---|
| parse errors hit | 1 | 1 (a different message: `Expected a semicolon...`, line 127) |
| calls spent in the toolchain | **7 of 23** | **2 of 22** |
| what happened next | no further file change, timeout at 1801 s | fixed, check passing by turn 41 |

The advice is verified against the real captured output in `tests/test_bonsai_pi.py`
(`CheckParseAdvice`), including the case it must *not* fire on - an ordinary assertion failure.

## What the four rows do and do not say

Trial 3 and trial 4 are the **same code**. Shipping the advice in trial 3 made it part of the
harness, so row 4 is not a control for row 3 - it is a second sample of it. The honest grouping
is therefore two runs with the advice and two without:

| | runs | passed | wall_s |
|---|---|---|---|
| without the advice | 2 | 1 | 1476, 363 |
| with the advice | 2 | 2 | 1256, 681 |

Two against two says nothing about the rate, and it would be dishonest to present it as though it
did: the pass/total difference is 1/2 against 2/2, which is not a result. **The run times cannot
rank anything either.** Trials 1 and 2 are identical code and one timed out at 1,801 s while the
other finished in 363 s - a 5x spread on the same task with the same harness. Trial 3's 1,256 s
sits inside that spread; so does trial 4's 681 s.

The claim this loop supports is narrower, and is the one above it: the advice fires, the model
stops investigating the toolchain, and a run that would have died recovers. Trial 3 cost three
failed checks and 22 turns to do it - the advice removes the *fatal* reading of a parse error,
not the parse error.

## Where the wall clock actually goes

The transcript carries per-message timestamps and token counts, so the gap between one assistant
message and the next can be decomposed. On trial 3, for turns where almost nothing was re-read:

| input tokens | output tokens | gap | implied rate |
|---|---|---|---|
| 490 | 1,161 | 82 s | 14.2 t/s |
| 17 | 1,553 | 108 s | 14.4 t/s |
| 839 | 2,593 | 180 s | 14.4 t/s |
| 555 | 1,658 | 107 s | 15.5 t/s |

Output volume at ~15 t/s accounts for the gap **entirely**. There is no room left for thinking
tokens, which is what `reasoning: 0` in every message's usage also says. The `--reasoning 512`
arm was queued on the assumption that a 1,024-token budget was being spent; the arithmetic says it
is not binding, so that arm is expected to come back flat.

What does dominate is two turns in trial 3: 169 s and **305 s**, on inputs of 7,329 and 8,244
tokens against outputs of only 169 and 275. That is prefill and tool time, not generation. Both
follow the model running `npx vitest run packages/core/__tests__` - the whole directory, by hand -
whose output floods the context. Together they are **474 s of the run's 1,259 s, 38%**.

So the ordering below is not the order the arms were queued in. The queue stays as pre-registered:
dropping an arm once its prediction looks bad is how a loop starts fooling itself.

## Next, in order

1. **Raise n before ranking.** Ten runs per arm, not two. Until then nothing here is a rate.
2. **Tell the model what the check is, and that it runs by itself.** The launcher already knows
   `BONSAI_CHECK_COMMAND` and the hook already runs it after every write and edit, and the model
   is told neither. It spends 4 turns discovering the test setup, then runs vitest by hand 1-4
   more times - twice over the whole directory. This is now the top lever, by a wide margin, and
   it is one prompt line.

   **Prediction, written before the trial.** Counting the turns that would not exist if the model
   knew the check, and adding up the time those turns actually took:

   | run | removable turns | seconds | share of wall time |
   |---|---|---|---|
   | trial 3 | 8 of 22 | 398 | 32% |
   | trial 4 | 5 of 12 | 137 | 21% |
   | trial 5 | 6 of 13 | 471 | 52% |

   So 20-50% of `wall_s`, and the prediction is falsifiable: if the arm comes back inside the
   baseline spread, the turns were not the cost and the change is not worth keeping.

3. **Read nudge** (`BONSAI_NUDGE_AFTER=4`): queued, and cheap. 6-9 reader calls before the first
   write in every run, though the timing says those early turns cost only ~55 s together.
4. **`--reasoning 512`**: queued, and expected flat on the arithmetic above. Worth running
   precisely because it is expected to fail - a knob whose help text claims "512 measured faster"
   should not stay in the launcher on the strength of that claim alone.
5. **The output cap** keeps the head of the check output; vitest prints its summary before the
   failure detail. Measured at 3,126 characters against a 3,000 cap in trial 1, so it is live but
   not yet binding.

A fifth hypothesis was withdrawn. The first diagnostics reported the same bash command repeated
**fifteen times**, which motivated a repeat-breaking extension. The commands are 23 *distinct*
calls that share a `cd <worktree> && ` prefix, and the metric keyed on a 60-character prefix. No
true repeats exist in trial 1. The extension was reverted and the metric fixed.






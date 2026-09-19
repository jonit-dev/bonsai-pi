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

## Three defects the setup itself had, found before any baseline was accepted

1. **A killed trial wedges the next one.** llama-server runs `-np 1`; a killed client left its
   request generating into a dead socket, holding the only slot. The next trial's request queued
   and the run looked hung while the GPU churned tokens for nobody. Fix: the evaluator restarts
   the server before every trial, which also makes trials start from identical state.
2. **A trial with no server is not a measurement.** One trial died at 13,542 tokens with
   `Received second interrupt`. Fix: health asserted before and after; a dead server records
   `invalid` instead of a plausible-looking row.
3. **A trial whose server will not start vanishes silently.** Trial 8 produced no row and no log;
   the batch output says only `llama-server did not come up within 5 minutes`. It ran immediately
   after the previous server was killed by hand, on an 8 GB card where the dying server may not
   have released its VRAM yet. `start_server()` raises `SystemExit` *before* the evaluator opens
   its log, so there is no `trial-8.log` and nothing on stdout to say a trial went missing - the
   batch simply moved on to trial 9. (My first write-up blamed a dirty worktree. That was wrong:
   `reset_worktree()` runs first and would have raised its own message, which is not what
   happened.) Do not kill a server and start a trial in the same breath.


## Results

```
trial  arm                     wall_s  turns  check_fails  rewrites  prose_max  blocked  passed  status
1      baseline (pre-advice)     1476     21            1         1       6070        0       0  timeout
2      baseline (pre-advice)      363     10            1         1        989        0       1  ok
3      parse advice              1256     22            3         1        223        0       1  ok
4      baseline (post-advice)     681     12            1         1       1110        0       1  ok
5      baseline (post-advice)    1495     14            3         1        392        0       0  timeout
6      read nudge 4              1636     17            2         2       2131        1       0  fail
9      read nudge 4              1651     30            1         1       4981        0       0  timeout
10     reasoning 512              370      6            1         1        406        0       1  ok
```

Trials 3, 4 and 5 are the same code (the advice shipped during trial 3). Trials 1 and 2 are the
code as it was before that. Trial 8 has no row at all - see defect 3 above.

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

## What `--reasoning 512` did

It returned the fastest run in the ledger - 370 s, 6 turns, passed - and that is still not
evidence the budget is doing anything:

| run | turns | output tokens | effective tok/s |
|---|---|---|---|
| trial 10 (reasoning 512) | 5 | 4,625 | 13.2 |
| trial 4 (baseline, fast) | 11 | 6,513 | 9.8 |
| trial 6 (nudge, fail) | 16 | 16,035 | 9.9 |

Trial 10 is fast because it generated **3.5x less**, not because each token came cheaper. The
per-turn arithmetic that predicted this arm flat still holds, and the baseline's own fastest run
was 363 s under the 1,024 default, so trial 10 sits inside the spread either way. One run against
three cannot retire the knob; it can only decline to promote it.

## What the read nudge did, and did not do

`BONSAI_NUDGE_AFTER=4` fired as designed - two nudges by turn 4 - and the early turns shrank:

| | trials 3-5 (nudge off) | trials 6 and 9 (nudge on) |
|---|---|---|
| reads before the first write | 8, 8, 9 | **6, 6** |
| setup reconnaissance turns | 4, 4, 2 | **2, 1** |
| checks run by hand | 4, 1, 0 | **0, 0** |
| turns | 22, 12, 14 | 17, **30** |
| passed | 2 of 3 | **0 of 2** |

Every mechanism it targets improved and both runs still failed. That is not evidence the nudge is
harmful - two runs against three decides nothing, and these are the two slowest arms either way -
but it is evidence the nudge is not the lever, and it should not be described as a win.

What both runs died of is output volume, which the nudge does not touch:

- **trial 6 wrote the file twice**, 8,697 characters and then 7,152 - the second refused, and the
  only `blocked=1` in the ledger so far. The instinct after a bad check is still to write the file
  again.
- **trial 6 read its own spec back 7 times.**
- **trial 9 closed with a 4,981-character prose dump**, the largest in the ledger.

8,697 + 7,152 characters is about 265 s of generation on its own, and 4,981 characters is ~1,245
tokens, ~83 s. The nudge fixes the cheap end - the early reads cost ~55 s together - and leaves the
expensive end where it was.


The rows are not eight arms. Trials 3, 4 and 5 are the **same code** - shipping the advice during
trial 3 made it part of the harness - so pooling them is the only honest reading:

| harness | rows | passed | wall_s | charged median |
|---|---|---|---|---|
| before the advice | 1, 2 | 1 of 2 | 1476, 363 | 1082 |
| + parse advice | 3, 4, 5 | 2 of 3 | 1256, 681, 1495 | 1256 |
| + parse advice, + read nudge | 6, 9 | **0 of 2** | 1636, 1651 | 1800 |
| + parse advice, + reasoning 512 | 10 | 1 of 1 | 370 | 370 |

None of those differences is a result. **The run times cannot rank anything:** rows 1 and 2 are
identical code, and one timed out at 1,801 s while the other finished in 363 s - a 5x spread on
one task with one harness. Row 10's 370 s sits inside that spread, and so does row 4's 681 s. The
read nudge's 0 of 2 is worth watching and is not yet a finding; two runs against three is not a
comparison, and both its runs were the slowest arms either way.

The claim this loop does support is narrower, and is the one above: the parse advice fires, the
model stops investigating the toolchain, and a run that would have died recovers. Trial 3 cost
three failed checks and 22 turns to do it - the advice removes the *fatal* reading of a parse
error, not the parse error.

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
2. **Advise on the *first* failure, not the second.** Trial 6 wrote the spec, failed the check,
   and its next action was a 7,152-character rewrite. The hook refused the write - `blocked=1`,
   the guard's first ever block - and the file was saved, but **the generation had already
   happened**: 7,152 characters is ~1,788 output tokens, ~120 s at the measured 15 t/s. A
   `tool_call` block cannot prevent that; only what the model reads before it generates can. The
   advice that says "do not rewrite the file, fix only what the error names" is attached at the
   *second* failure today, which is one failure too late to be read before the rewrite is written.
3. **Tell the model what the check is, and that it runs by itself.** The launcher already knows
   `BONSAI_CHECK_COMMAND` and the hook already runs it after every write and edit, and the model
   is told neither. It spends 4 turns discovering the test setup, then runs vitest by hand 1-4
   more times - twice over the whole directory. It is one prompt line, and the prediction below is
   still the largest single number in this file.

   **Prediction, written before the trial.** Counting the turns that would not exist if the model
   knew the check, and adding up the time those turns actually took:

   | run | removable turns | seconds | share of wall time |
   |---|---|---|---|
   | trial 3 | 8 of 22 | 398 | 32% |
   | trial 4 | 5 of 12 | 137 | 21% |
   | trial 5 | 6 of 13 | 471 | 52% |

   So 20-50% of `wall_s`, and the prediction is falsifiable: if the arm comes back inside the
   baseline spread, the turns were not the cost and the change is not worth keeping.

4. **Read nudge** (`BONSAI_NUDGE_AFTER=4`): measured in trial 6. It cut early reads from 8-9 to
   6, setup turns from 4 to 2, and hand-run checks to zero - and the run failed anyway, because
   those early turns cost only ~55 s together. Keep it for the turn reduction, not for a win.
5. **Reading back its own file.** Trial 6 read `entity-snapshot.spec.ts` - the file it had just
   written - **7 times**. The read nudge resets on a successful change, so those count as fresh
   reads and are nudged only eventually. A read of a path this session wrote is never useful.
6. **`--reasoning 512`**: queued, and expected flat on the arithmetic above. Worth one run
   precisely because it is expected to fail - a knob whose help text claims "512 measured faster"
   should not stay in the launcher on the strength of that claim alone.
7. **The output cap** keeps the head of the check output; vitest prints its summary before the
   failure detail. Measured at 3,126 characters against a 3,000 cap in trial 1, so it is live but
   not yet binding.

A hypothesis was withdrawn. The first diagnostics reported the same bash command repeated
**fifteen times**, which motivated a repeat-breaking extension. The commands are 23 *distinct*
calls that share a `cd <worktree> && ` prefix, and the metric keyed on a 60-character prefix. No
true repeats exist in trial 1. The extension was reverted and the metric fixed.






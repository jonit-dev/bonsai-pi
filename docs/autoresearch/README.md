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

## Hypotheses queued

Drawn from the transcripts, in the order the evidence supports them:

1. **Rewrite blocker** (`BONSAI_BLOCK_REWRITE=0` to remove it): the model rewrites a whole file
   after a failed check — measured at 5,136 and 5,061 characters, 170–208 s each.
2. **`--reasoning N`**: 512 against the 1024 default, cleanly this time.
3. **Reconnaissance**: 6–10 reader calls before the first write, every run.
4. **Edit-match failures**: `edit` already does fuzzy matching upstream, so a rejected edit means
   the model invented text. The error names no alternative — it could name the closest region.

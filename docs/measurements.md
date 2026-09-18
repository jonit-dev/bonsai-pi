# Measurements

The ledger behind `BONSAI.md`. Everything here was measured on the machine described at the
bottom, in this repository's own runs. Numbers that came from somewhere else are labelled as
such; nothing is estimated unless it says so.

`BONSAI.md` is the front door and stays short. This file is where the numbers live.

## Stock pi versus the profile

First request of a session, same CLI, same model:

| | stock pi 0.85.1 | bonsai-pi |
|---|--:|--:|
| system message text | 85,159 chars | 5,533 chars |
| tool schemas | 3,298 chars | 4,398 chars |
| whole request | 88,522 chars | 9,992 chars |
| pi's own token estimate | ~22,130 | ~2,498 |
| skills in the prompt | 199 (from `~/.agents/skills`) | 0 |

Two caveats on the stock column, because it is easy to over-read. The 199 skills were discovered
from *this* machine's shared `~/.agents/skills`, so 22k is what stock pi costs in an environment
that has them — not a fixed baseline for every installation. And it is pi's own character
estimate, not a server count. The number the model actually had to read, prefill and hold was
**2,061 prompt tokens** for the first turn of a real session, measured when the profile was
smaller than the table above.

`max_completion_tokens` on the stock run was **1**: pi clamped the answer to the window and found
nothing left after its own 4096-token safety margin. The model would have been asked to reply in
one token. That is the whole reason for the fork.

## The fixed cost, per request

Everything fixed rides on every request:

| | chars | ~tokens |
|---|--:|--:|
| profile text — `bonsai.md` + `ponytail.md`, plus the cwd line | 5,533 | 1,383 |
| tool schemas — read 837, grep 1,102, bash 652, edit 1,297, write 500 | 4,398 | 1,100 |
| **fixed total** | **9,931** | **2,483** |

That is 10% of the 24576-token window. Everything else in the window is the conversation.

Where the stock cost went, and what each alternative would cost:

- **The `<skills>` section was ~82k of the 85k system-message characters** — 199 entries from
  `~/.agents/skills`. Removed; a local model is not going to browse a skill index.
- **The `<docs>` section** pointed the model at pi's own SDK documentation: 1,170 chars. A custom
  `--system-prompt` replaces the whole tools/rules/docs block, not just the preamble, so it went
  with it. It was also an invitation to reconnaissance, this model's worst failure mode.
- **`grep` is in; `find` (659 chars) and `ls` (508) are out.** Search earns its 1,102 chars because
  the model searches constantly; `find` and `ls` do not, and `bash` covers them. `grep` is not
  obviously cheaper than the bash calls it replaces — `--tools read,bash,edit,write` puts it back
  to four if a task measures worse with it.
- **A repo map** (Aider-style tree-sitter outline): rejected on cost. 5–50k tokens; it does not
  fit in a 24k window twice.
- **Tool output caps** of 8KB instead of upstream's 50KB: the largest non-obvious saving. One
  `cat` of a generated file used to cost 12k tokens — half the window — for a single tool result.

A probe of the pi registry (5,343 packages) found no package that reduces turn count or turn
latency without either an extension (which costs no prompt tokens but adds behaviour we would have
to measure) or a new tool schema (~100–300 tokens per request). The candidates that were actually
evaluated are listed under "Package candidates" below.

## Why the reading rules are written the way they are

The remaining inefficiency is not bytes, it is **turns**: a reconnaissance loop of 20 read calls
costs more than every schema in the table above combined. The profile's reading rules exist for
that, but an earlier draft got them wrong in three ways, all since reversed:

| tried | why it was wrong |
|---|---|
| "at most four read or bash calls before your first write" | made it write before it had the evidence |
| "never read configuration or directory listings" | contradicted the ponytail ruleset appended directly below it ("trace the real flow", "read it fully"), and pushed the model to guess |
| "pipe long output through `head`/`tail`" | a truncating pipe hides the exit status of the command whose result matters, and the harness already caps output |

They are bounds now: read what you need once, look essentials up instead of guessing, leave
truncation to the harness.

## What compaction actually costs

Parsed from the session JSONL of the runs recorded here:

| | |
|---|---|
| compactions | **9**, across 13 sessions (7 sessions had at least one) |
| compactions that stalled the run | **0** — every one was followed by more work |
| gap from the compaction entry to the next message | **44–232 s** |

That gap is pi calling the model to write the summary — a full extra generation at 16–20 t/s, in
the middle of a task, and it cannot be prompt-tuned away. It is the one place this harness spends
minutes on something other than the model's own output. A session here sees ~0.7 of them, which is
why algorithmic-compaction packages are filed as "revisit" rather than "adopt".

## `--reasoning-budget`: one task, one variable

| arm | turns | time to a passing spec | outcome |
|---|--:|--:|---|
| `1024` | 14 | ~744 s | passing spec, 15 tests |
| `512` | 11 | **641 s** | passing spec, 13 tests |

Task: a spec over `entity-snapshot.ts` in the threenative-engine worktree, same check hook, same
prompt. Both green. The 512 arm finished 14% sooner with three fewer turns — but the two runs
produced *different* test files (15 vs 13 tests), so this is one sample against one sample on a
stochastic model. It is evidence that 512 does not hurt, not that it helps.

The turn gaps say why to distrust the speedup as a cause: the expensive turns are 239 s and 216 s
of *generation*, far longer than either budget could account for. What differs between the arms is
how many turns the model spent, not how long it thought.

Exposed anyway as `--reasoning N`, because it is the cheapest thing to test on other hardware and
a smaller budget cannot make a turn slower.

## Output size dominates turn latency

The single most expensive turn observed, in full:

| | |
|---|---|
| wall clock | **239 s** |
| output tokens | 4,299 |
| thinking | 1,711 chars |
| **prose in the reply** | **13,246 chars — the entire test file** |
| tool call | `bash`, 301 chars |

At ~18 t/s that is four minutes spent typing the file into the chat, after which it had to be
written again inside the tool call. Two fixes: a profile rule naming the cost, and
`nudge-code-in-prose.ts`, which appends one line to the next tool result when a reply carries a
large code block and no write call. It costs no prompt tokens and is inert unless
`BONSAI_NUDGE_PROSE_CHARS` is set.

## Package candidates

Each of these was evaluated against the four measured bottlenecks — output tokens per turn, turn
count, reconnaissance drift, and arithmetic — and against the fixed cost above. None is adopted.

| candidate | verdict |
|---|---|
| **RTK** ([rtk-ai/rtk](https://github.com/rtk-ai/rtk)) — filters bash output before the model reads it | **Measured, and it regresses the critical path.** rtk 0.49.0 on this workspace: a **failing** `vitest run` went **2,215 → 3,455 bytes (+56%)**, because rtk keeps the `chunk-artifact.js` stack frames vitest's own reporter already collapses — and a failing run is exactly what the check hook delivers. A **passing** run went 823 → 19 bytes (−98%), which optimises the case with nothing to learn: 823 bytes is ~200 tokens. `git status` 425 → 161 and `ls -R` 1,544 → 1,068 are real but small, and both are already under the 8KB cap. `cat` of a 70 KB file and `rg` over 14.9 KB passed through **unchanged**. `rtk read -l aggressive` does work (70,415 → 20,345) but returns signatures, not the text under test. Its headline percentage counts bash-output bytes, which its own README says is not the bill. |
| **`@arcanemachine/pi-supercompact`** — deliberate, loss-resistant compaction with a preparation turn and a canonical handoff | **Measured: exactly zero cost, and not adopted.** Loaded with `-e npm:@arcanemachine/pi-supercompact` and captured the request — system prompt and tool schemas byte-identical to the baseline (6,029 / 4,398 chars, the same five tools), because our `--tools` allowlist filters its two schemas out entirely. That cuts both ways: with its tool filtered the model cannot request supercompaction at all, and what the extension adds is *more model calls* (a preparation turn, a canonical summary turn) to solve context loss this setup has not experienced. |
| **`@hicaru/pi-rlm`** — Recursive Language Model: a Python REPL orchestrating a smart root model and cheap worker models | **Not adopted; the premise does not fit.** Its design is a *fleet* — "your best model orchestrates, cheap workers do the leaf reads" — benchmarked on long documents (PDF/EPUB/XLSX, OOLONG, cost per task in dollars). This setup is one 27B on one 8 GB card: every worker call is another 16–20 t/s generation on the same GPU, so delegation multiplies the thing that already dominates. It also ships a spawned Python process, provider keys, 957 KB, and its own prompt architecture. |
| **`@sting8k/pi-vcc`** — algorithmic compaction, no LLM call, 30–470 ms claimed | **The one candidate with a measured case behind it, deferred.** It registers a `vcc_recall` tool, so ~100–300 tokens per request (a survey guessed 2–5; measured schema costs here are 100–300). The case rests on the compaction numbers above: 9 events, 44–232 s each. The arithmetic is against it — 9 events across **13 sessions** is ~0.7 per session, so ~30–160 s saved, paid for on every request of every session with a schema that itself fills the window slightly faster. Revisit if compaction ever dominates a run. |
| **`pi-poke`** — recovers a work turn that dies after compaction | **Not adopted; no evidence it would fire.** It targets a real failure mode for this stack ("the run dies with `Error: This operation was aborted` right after `[compaction]`"), but across 13 sessions and 9 compactions **every compaction was followed by more work** — 9/9 resumed. Add it the day a run actually stalls on one. |
| **Headroom** — output compression library/proxy | Not enabled. Its own documentation reports weak gains on already-dense payloads, and tool output is already bounded at 8KB. |
| **`@ast-grep/cli`** — structural search | Deferred. Ordinary `grep` has to be measured as a bottleneck first. |
| **Duplicate-read suppression** ("unchanged since your last read") | Deferred, and the obvious version is unsafe: a file being unchanged does not mean the model still has its contents after a compaction. Any suppression has to key on the exact version *and* range *and* whether that text is still in context. |
| **`pipefail` in the shell** | Not set. It would turn every ordinary `cmd \| head` into a failure and change the meaning of pipelines the model already writes; the prompt no longer asks it to build them, which was the actual defect. |

The rule for the next change: one at a time, on a task where the model has to find things, and the
number to watch is **wall time per correctly completed task** — a smaller prompt that buys another
repair cycle is not a saving.

## Acceptance runs

### `tasks/easy-api` (single-file Python HTTP API, 4 tests)

Passes, 4/4 independently re-run. Six tool calls — read, bash to run the tests, two parallel reads,
one write, bash again — 98 seconds wall clock, first turn 2,061 prompt tokens. The same fixture
took 552 s and 9.6k tokens through Codex on the same model, and the bonsai-codex README records it
as the fixture that failed for a long time before their fixes landed.

### `threenative-engine` — `entity-snapshot.spec.ts` (large TypeScript monorepo)

The acceptance test, run in a git worktree so the main checkout was never touched. The task: add a
unit test file for `packages/core/src/entity-snapshot.ts`. Result: a 15-test spec, **15/15
passing**, verified by running the file independently, merged to `develop` as `2bc80533e` (tests
only, one file, 182 lines).

The transcript shows what the check hook bought:

| | |
|---|---|
| `write` → check | `exited 1` — a parse error, delivered inside the write's own result |
| `write` → check | `exited 1` — 13 of 15 passing |
| `edit` → check | `exited 1` — 14 of 15; the model: "both failures are on my end, not the implementation's fault" |
| `edit` → check | **`exited 0`** — then, unprompted, it ran the whole `packages/core` suite |

Fourteen assistant turns. The model diagnosed two of its own bugs from output it never asked for.

Repeated later at `--reasoning 512`: 11 turns, 641 s to green, also passing (13 tests).

### `threenative-engine` — `pose-measure.spec.ts` (the boundary)

The same harness, same repo, a module whose expectations are numeric rather than structural: five
attempts, none converged. The failure is always the same — an expected value it reasoned out
rather than derived: an axis sign flipped, `expected 7, got 6.258`, `expected 10, got 4.59`. Two
prompt rules aimed at it did not hold; the rule that finally changed the *approach* was "compute
the expectation inside the test, from the same library". The check hook guaranteed the model saw
each failure; it could not make it produce a correct expectation for a transform.

That is the honest reading of where this model's boundary is: harness problems are fixable here,
arithmetic is not.

## Tested on

| | |
|---|---|
| GPU | NVIDIA RTX 2080 (Turing, sm_75, 8 GB) |
| CPU / RAM | Ryzen 9 5900X / 62 GB |
| OS | Arch Linux, driver 610.57 |
| Runtime | PrismML llama.cpp fork, built for sm_75 |
| Model | `Ternary-Bonsai-2-27B-PTQ1_0.gguf`, 5,671 MiB (5.54 GiB) |

| | |
|---|---|
| prefill | 232–255 t/s |
| decode, shallow context | 26.8 t/s |
| decode, deep context | ~20 t/s |
| first turn of a session | 2,061 prompt tokens |

Turn latency is decode-bound, so a 4k-token generation is about three minutes regardless of the
link to the model.

VRAM at 24576-token context, `-ctk q4_0 -ctv q4_0 -ub 128 -b 256 -np 1`:

| | MiB |
|---|--:|
| model file | 5671 |
| llama-server resident after load | 6252 |
| llama-server peak during a live run | ~7016 |
| whole card during that run, desktop included | 7216 |

A load into 5898 MiB of free memory failed outright allocating the 5395 MiB of weights, because
the CUDA context is not free either. That is where the launcher's floor comes from.

`-np 1` is not cosmetic: the server's default of four slots multiplies the rs cache and OOMs at
32k. `-ngl 99` offloads every layer, which is what makes a 27B fit at all.

### Borrowed numbers

The decode and MTP figures below come from [bonsai-codex](https://github.com/jonit-dev/bonsai-codex),
which hit these problems first on the same card, not from runs in this repository:

- **The MTP variant is net-negative here.** The grafted MTP model loads and its drafts are good,
  but decode was slower at every depth: 26.8 t/s with speculation off against 21.3 / 21.4 / 17.6
  t/s at n-max 1 / 2 / 3 at 8k context. Use plain `PTQ1_0`.
- **Prebuilt CUDA binaries work on Turing**, via PTX: the Linux release ships cubins for
  sm_86/89 (+120a/121a) but PTX for sm_50/61/70/75/80/90, so a 20-series card JIT-compiles on
  first run. `cuobjdump --list-elf` makes it look unsupported; check `--list-ptx`. The release
  also needs its matching CUDA runtime, not just the driver. A source build for `sm_75` is equal
  within noise and skips the JIT cost.

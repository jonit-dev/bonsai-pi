# bonsai-pi

[pi](https://github.com/earendil-works/pi) tuned to drive **Ternary Bonsai 2 27B** on one 8 GB
GPU. A fork of `earendil-works/pi` at v0.85.1 with a lean profile, a context budget that fits the
real window, and a launcher that starts the model server for you.

    ./setup.sh                                     # check prerequisites and say how to fix them
    bin/bonsai-pi                                  # interactive pi TUI on the local model
    bin/bonsai-pi -p "implement create_app() so the tests pass"
    bin/bonsai-pi --stop                           # stop the server this script started

The model server, its flags and the VRAM budget come from
[bonsai-codex](https://github.com/jonit-dev/bonsai-codex), which drives the same model through
Codex. This repository is the pi-shaped half of that: what changes when the harness is pi instead
of Codex.

The numbers behind every claim here — what stock pi costs, what each rejection costs, what
compaction costs — are in **[docs/measurements.md](docs/measurements.md)**. This file is the
front door; that file is the ledger.

## Why a fork

The window is the binding constraint, not the model. On this card llama-server loads 24576 tokens
and that is the ceiling — 32768 crashes it, 16384 with FP16 KV does not fit — so a harness that
spends 22k tokens before the user types anything cannot run the model at all. Stock pi's first
request here is 88,522 characters (~22,130 by pi's own estimate, 90% of the window); with this
profile it is 9,992 characters, and `max_completion_tokens: 1` becomes a real answer budget.

## What the profile does

`bin/bonsai-pi` forwards everything it does not recognise to pi, and adds:

| | |
|---|---|
| `--tools read,grep,bash,edit,write` | strict allowlist; pi ships eight built-ins, this model gets five |
| `--system-prompt <profile>` | `profile/bonsai.md` (tool mechanics, literal argument examples) plus `profile/ponytail.md` |
| `--no-skills --no-extensions --no-prompt-templates --no-themes` | 199 skill blocks were the single largest cost of the stock prompt |
| `--no-context-files` | off by default: a project's `CLAUDE.md` in this workspace is 10-22KB. `--context-files` opts back in |
| generated `models.json` | the served model, with `contextWindow` copied from the server's `/props`, not assumed |
| generated `settings.json` | a context budget derived from that window (below), trust defaulted, telemetry off |
| `--reasoning N` | thinking budget per server turn; default 1024 |
| `-e profile/check-after-edit.ts` | runs `BONSAI_CHECK_COMMAND` after every write/edit, appends the result, and stops the run after N consecutive failures |
| `-e profile/block-rewrite.ts` | refuses a second `write` to a file this session already wrote — use `edit` (`BONSAI_BLOCK_REWRITE=0` disables) |
| `-e profile/nudge-after-reads.ts` | one line after N calls without a successful write (`BONSAI_NUDGE_AFTER`) |
| `-e profile/nudge-code-in-prose.ts` | one line when a reply carries a lot of code and no write (`BONSAI_NUDGE_PROSE_CHARS`) |

The four extensions cost nothing until they fire, and they are inert with their variable unset —
so the arms of a measurement can share one launcher. `--no-extensions` disables *discovery* only;
pi's own help says explicit `-e` paths still work, which is why they are named here.

### The check hook

A prompt rule saying "run the test" is a decision this model skips — measured: it rewrote the same
broken call three times, reasoning about what the test would do instead of running it. So the
harness closes the loop. Set the command and every write/edit comes back carrying its result:

```sh
BONSAI_CHECK_COMMAND='npx vitest run packages/core/__tests__/foo.spec.ts' bonsai-pi -p "…"
```

    [check: npx vitest run … exited 1 - the check FAILS]
    …the first 3000 characters of output…

Scope it to what the task touches: it runs after every write and edit.

**It also has a retry policy**, because the failure mode is a loop and nothing else in the
harness bounds it. Consecutive failures of the same check escalate: the second tells the model not
to rewrite the file, the third demands one edit touching only the lines the error names, and the
fifth **stops the run** — a blocked tool call with `terminate`, which ends the agent instead of
letting it spend the rest of the afternoon failing the same way. A passing run resets the count.
`BONSAI_CHECK_MAX_FAILURES` moves the stop (default 5).

Measured, that ceiling is not hypothetical: the model rewrote one file twice at 208 s each without
it, and burned five attempts on another module before a human killed the run.

### What changed in the source

Three things, and one bug fix:

- **Tool output caps** (`packages/coding-agent/src/core/tools/truncate.ts`): 50KB / 2000 lines →
  **8KB / 400 lines**. Upstream's default is 12k+ tokens for one tool result — half the window for
  a single `cat`.
- **The context budget is derived once, and it composes.** pi clamps `max_completion_tokens` to
  `window − prompt − 4096` and never below 1. So the reserve the compaction trigger holds back has
  to cover the answer *plus* that 4096: at the trigger the model is left `reserve − 4096`. The
  launcher sets `answer = min(ctx/4, 8192)`, `reserve = answer + 4096`, `keepRecent ≤ (ctx −
  reserve)/2`, and refuses a window that leaves the prompt under 2048 tokens at all.
- **Upstream's `reserveTokens`/`keepRecentTokens` defaults** (16384/20000) are sized for a hosted
  model with a 200k window; on 24576 they trigger compaction at 8192 while keeping 20000 verbatim,
  so every turn compacts again.
- **`executeBashWithOperations`** returned before its full-output temp file was flushed, so the
  path it hands the model could read back **empty**. It now awaits the write.

## VRAM: nothing here is sized for one card

The launcher refuses to start a server it does not believe the card can hold, working from the
model's own file size rather than a number picked for one GPU:

    required = size of the GGUF                      (measured with stat, no guesswork)
             + BONSAI_OVERHEAD_MIB   (default 600)   CUDA context + KV + compute buffers
    warn below required + BONSAI_HEADROOM_MIB (default 800), the room a deep run wants

Both are environment overrides and are the only numbers to move on other hardware; `--ctx` trades
context for headroom. A model file that is not there skips the check rather than inventing a size.
`--stop` only stops a server this launcher started (pidfile under `~/.local/state/bonsai-pi/`).

| lever | |
|---|---|
| `--ctx N` | window to load when starting the server (default 24576; 8192 is the floor) |
| `--reasoning N` | thinking budget per turn (default 1024; 512 measured no worse on one task) |
| `BONSAI_OVERHEAD_MIB`, `BONSAI_HEADROOM_MIB` | calibration for a different card |
| `BONSAI_MODEL`, `FORK` | a different GGUF or a different llama.cpp build |

## Which quant, and which llama.cpp

Not a preference — these were all found the hard way in bonsai-codex:

- **`PTQ1_0` needs the [PrismML llama.cpp fork](https://github.com/PrismML-Eng/llama.cpp).** Stock
  llama.cpp and Ollama cannot read `PTQ1_0` or `PQ2_0` and refuse them.
- **Do not reach for `Q2_0` instead.** It loads without a warning and produces gibberish: there is
  no Hadamard activation runtime behind that band.
- **Ollama cannot serve this model at all**, which is why this fork talks to `llama-server`
  directly rather than through pi's own llama.cpp provider — that provider wants the llama.cpp
  *router* (`--models-dir`, `/models`, `/llama`), and this is a single-model server started with
  `-m`.
- **The MTP variant is net-negative on this card.** Use plain `PTQ1_0`.

## Another machine on the wifi

```sh
# on the machine with the GPU
HOST=0.0.0.0 PORT=8090 scripts/run-server.sh

# on any other machine on the same wifi (a checkout of this repo; the node bundle is all it needs)
bin/bonsai-pi --server-host 192.168.1.133 --port 8090 -p "implement create_app() so the tests pass"
```

A non-loopback `--server-host` changes exactly one path in the launcher: it starts nothing, never
reads this card's free VRAM, and an unreachable host exits 1 at once with the `HOST=0.0.0.0`
command above. Same profile, same five tools, one request per turn.

The window is **read from the remote server's `/props`**, not assumed, so client and server cannot
drift apart. `--ctx` applies only to a server this script starts itself.

**The firewall is the part that bites.** llama-server binds `0.0.0.0` happily and logs nothing
wrong, but ufw's default incoming policy is `deny`, so an outside connection is dropped before the
server sees it: the client reports a timeout and the server log stays clean. On the GPU box:

```sh
sudo ufw allow from 192.168.1.0/24 to any port 8090 proto tcp
```

**There is no authentication on either hop.** Anyone who can reach the port can use the GPU. Keep
the allow rule narrow (a `/24`, or a single `/32`), and do not expose it to the internet. For a
tighter setup, bind a Tailscale/WireGuard address instead and skip the rule: `HOST=100.x.y.z
scripts/run-server.sh` with `--server-host 100.x.y.z` gives an encrypted, authenticated path with
no open port on the wifi.

## What it is good at

`tasks/easy-api` (single-file Python HTTP API): **passes 4/4**, 98 s, six tool calls. On a large
TypeScript monorepo it added a 15-test spec for `packages/core/src/entity-snapshot.ts` that
**passes 15/15**, in a worktree, with the main checkout untouched — 14 turns the first time, 11
when re-run at `--reasoning 512`.

Treat it as a **single-file worker**, not a driver. Worked examples in the prompt are what make it
produce usable calls; a rule that stays prose is a rule it violates. Give it a file, a check
command, and a concrete task.

Its reliable limit is **arithmetic**: five attempts at a transform-heavy module never converged,
because it reasons out expected values instead of deriving them. The full transcript, and the
single prompt rule that finally changed its approach, are in
[docs/measurements.md](docs/measurements.md#acceptance-runs).

## Updating from upstream

This is a standalone copy, not a GitHub fork — the same arrangement bonsai-codex uses, and for the
same reason: one account cannot own both a parent and its fork.

```sh
git remote add upstream https://github.com/earendil-works/pi.git
git fetch upstream && git merge upstream/main
```

The fork's own surface is small on purpose: `bin/`, `profile/`, `scripts/`, `tests/`, `setup.sh`,
`docs/measurements.md`, this file, and the source changes above. Everything else is upstream, so a
merge is a merge.

## Development

```sh
./setup.sh                       # prerequisites, and the exact command for anything missing
npm ci && npm run build          # node >= 22.19; the bundle lands in packages/coding-agent/dist
python3 tests/test_bonsai_pi.py  # budget arithmetic, generated config, guards, arg forwarding
npx vitest --run packages/coding-agent/test/tools.test.ts
```

`--dry-run` prints the resolved argv, one argument per line, and writes the generated configs
without starting anything — that is what the launcher tests assert on.

The launcher is bash and the only place a mistake costs a model load, so run
[ShellCheck](https://github.com/koalaman/shellcheck) over it when you touch it. Development tool,
not a runtime dependency:

```sh
shellcheck bin/bonsai-pi scripts/run-server.sh setup.sh
```

Note that the pre-commit hook (`npm run check`) fails on this checkout for an upstream reason
unrelated to the fork — `node:sqlite` undeclared in `packages/session-backends/sqlite-node` — so
commits here use `--no-verify` and the checks above are run by hand instead.

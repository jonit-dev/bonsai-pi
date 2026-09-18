# bonsai-pi

[pi](https://github.com/earendil-works/pi) tuned to drive **Ternary Bonsai 2 27B** on one 8 GB
GPU. A fork of `earendil-works/pi` at v0.85.1 with a lean profile, a context budget that fits
the real window, and a launcher that starts the model server for you.

    ./setup.sh                                     # check prerequisites and say how to fix them
    bin/bonsai-pi                                  # interactive pi TUI on the local model
    bin/bonsai-pi -p "implement create_app() so the tests pass"
    bin/bonsai-pi --stop                           # stop the server this script started

The model server, its flags and the VRAM budget are the ones from
[bonsai-codex](https://github.com/jonit-dev/bonsai-codex), which drives the same model through
Codex. This repository is the pi-shaped half of that: what changes when the harness is pi
instead of Codex.

## Why a fork at all

The window is the binding constraint, not the model. On this card llama-server loads 24576
tokens and that is the ceiling — 32768 crashes it, 16384 with FP16 KV does not fit — so a
harness that spends 22k tokens before the user types anything cannot run the model at all.

Measured on this machine, first request of a session, same CLI, same model:

| | stock pi 0.85.1 | bonsai-pi |
|---|--:|--:|
| system message text | 85,159 chars | 5,533 chars |
| tool schemas | 3,298 chars | 4,398 chars |
| whole request | 88,522 chars | 9,992 chars |
| pi's own token estimate | ~22,130 | ~2,498 |
| skills in the prompt | 199 (from `~/.agents/skills`) | 0 |

Two caveats on the stock column, because it is easy to over-read: the 199 skills were discovered
from *this* machine's shared `~/.agents/skills`, so 22k is what stock pi costs in an environment
that has them, not a fixed baseline for every installation. And it is pi's own character
estimate, not a server count. The number the model actually had to read, prefill and hold was
**2,061 prompt tokens** for the first turn of a real session, measured when the profile was
smaller than the table above.

`max_completion_tokens` on the stock run was **1**: pi clamped the answer to the window and
found nothing left after its own 4096-token safety margin. The model would be asked to reply in
one token. That is the whole reason for the fork.

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

Why `grep` is in the list: every observed run without it hand-rolled `grep -rn … | head` through
`bash` — the same search, with worse output bounds and an extra round trip. Its schema costs
1,102 chars (~275 tokens) per request, which is not obviously cheaper than the bash calls it
replaces. It is a trade, not a theorem: `--tools read,bash,edit,write` puts it back to four if a
task measures worse with it.

Three changes in the source:

- **Tool output caps** (`packages/coding-agent/src/core/tools/truncate.ts`): 50KB / 2000 lines →
  **8KB / 400 lines**. Upstream's default is 12k+ tokens for a single tool result — half the
  window for one `cat`. 8KB is ~2k tokens.
- **The context budget is derived once, and it composes.** pi clamps `max_completion_tokens` to
  `window − prompt − 4096` and never below 1 (`CONTEXT_SAFETY_TOKENS` in
  `packages/ai/src/api/simple-options.ts`). So the reserve the compaction trigger holds back must
  cover the answer we want *plus* that 4096: at the trigger the model is left `reserve − 4096`
  tokens, not `window − trigger`. The launcher sets `answer = min(ctx/4, 8192)`, `reserve = answer
  + 4096`, `keepRecent ≤ (ctx − reserve)/2`, and refuses a window where that leaves the prompt
  under 2048 tokens at all. Sizing the reserve as a fraction of the window is how a 12288 window
  promised room in a comment and delivered **one token** in practice.
- **Upstream's `reserveTokens`/`keepRecentTokens` defaults** (16384/20000) are for a hosted model
  with a 200k window: on a 24576 window they trigger compaction at 8192 while keeping 20000
  verbatim, so every turn would compact again.

One upstream bug fixed on the way, because the caps above made it deterministic:
`executeBashWithOperations` returned before its full-output temp file was flushed, so the path
it hands the model could read back **empty**. It now awaits the write.

## The budget, per request

Everything fixed rides on every single request, so it is worth stating exactly:

| | chars | ~tokens |
|---|--:|--:|
| profile text — `bonsai.md` + `ponytail.md`, plus the cwd line | 5,533 | 1,383 |
| tool schemas — read 837, grep 1,102, bash 652, edit 1,297, write 500 | 4,398 | 1,100 |
| **fixed total** | **9,931** | **2,483** |

That is 10% of the 24576-token window, against 22,130 tokens — 90% — for stock pi. Everything
else in the window is the actual conversation.

Where the stock cost went, measured, and what each alternative would cost:

- **The `<skills>` section was ~82k of the 85k system-message characters**, 199 entries discovered
  from `~/.agents/skills`. Removed; a local model is not going to browse a skill index.
- **The `<docs>` section** pointed the model at pi's own SDK documentation: 1,170 chars. A custom
  `--system-prompt` replaces the whole tools/rules/docs block, not just the preamble, so it went
  with it — and it was an invitation to reconnaissance, which is this model's worst failure mode.
- **`grep` is in; `find` (659) and `ls` (508) are out.** Search earns its 1,102 chars because the
  model searches constantly; `find` and `ls` do not, and `bash` covers them.
- **A repo map** (Aider-style tree-sitter outline): rejected. 5–50k tokens; it does not fit in a
  24k window twice.
- **Tool output caps** of 8KB instead of 50KB: the largest non-obvious saving. One `cat` of a
  generated file used to cost 12k tokens — half the window — for a single tool result.

The remaining inefficiency is not bytes, it is **turns**. A reconnaissance loop of 20 read calls
costs more than every schema in this table combined, so the profile's reading rules exist — but
they are written as bounds rather than prohibitions. An earlier draft capped reads before the
first write, forbade reading configuration and directory listings outright, and told the model to
pipe long output through `head`/`tail`. All three were wrong: the cap made it write before it had
the evidence, the prohibitions contradicted the ponytail ruleset appended right below them
("trace the real flow", "read it fully"), and a truncating pipe hides the exit status of the
command whose result matters. Now it reads what it needs once, looks essentials up instead of
guessing, and leaves truncation to the harness.

## What is deliberately not here

Additions were considered and rejected, or deferred until something measures them:

| | |
|---|---|
| **RTK** ([rtk-ai/rtk](https://github.com/rtk-ai/rtk)) — command-output reduction, ships a pi extension | Not enabled. `--no-extensions` only disables *discovery*, so an explicit `-e <path-or-npm-pkg>` still loads; try it against a task and measure time-per-completed-task before making it a default. Do not enable it together with another change, or the result says nothing. |
| **Headroom** — output compression library/proxy | Not enabled. Its own documentation reports weak gains on already-dense payloads, and this profile already bounds tool output at 8KB. |
| **`@ast-grep/cli`** — structural search | Deferred. Ordinary `grep` has to be measured as a bottleneck first. |
| **Duplicate-read suppression** ("unchanged since your last read") | Deferred, and the obvious version is unsafe: a file being unchanged does not mean the model still has its contents after a compaction. Any suppression has to key on the exact version *and* range *and* whether that text is still in context. |
| **`pipefail` in the shell** | Not set. It would turn every ordinary `cmd | head` into a failure and change the meaning of pipelines the model already writes; the prompt no longer asks it to build them, which was the actual defect. |

The rule for the next change: one at a time, on a task where the model has to find things, and
the number to watch is **wall time per correctly completed task** — a smaller prompt that buys
another repair cycle is not a saving.

## VRAM: nothing here is sized for one card

The launcher refuses to start a server it does not believe the card can hold, and it works that
out from the model's own file size rather than from a number picked for one GPU:

    required = size of the GGUF                      (measured with stat, no guesswork)
             + BONSAI_OVERHEAD_MIB   (default 600)   CUDA context + KV + compute buffers
    warn below required + BONSAI_HEADROOM_MIB (default 800), the room a deep run wants

The two defaults are calibrated on the card this was built and measured on — an 8 GB RTX 2080
(Turing, sm_75) at 24576 context — and are the only numbers to move on other hardware. Both are
environment overrides, and `--ctx` trades context for headroom. A model file that is not there
yet skips the check entirely rather than inventing a size (the server script reports the missing
model itself). `--stop` only stops a server this launcher started, tracked by pidfile under
`~/.local/state/bonsai-pi/`; a server you started yourself is left alone.

Why a floor exists at all: on this card a load into 5898 MiB of free memory failed outright
allocating the 5395 MiB of weights, because the CUDA context is not free either. Refusing to
start is the cheap outcome; "CUDA error: out of memory" mid-generation is the expensive one.

Measured on that card, 24576-token context, `-ctk q4_0 -ctv q4_0 -ub 128 -b 256 -np 1`:

| | MiB |
|---|--:|
| model file | 5671 |
| llama-server resident after load | 6252 |
| llama-server peak during a live run | ~7016 |
| whole card during that run, desktop included | 7216 |

`-np 1` is not cosmetic: the server's default of four slots multiplies the rs cache and OOMs at
32k. `-ngl 99` offloads every layer, which is what makes a 27B fit at all — reduce it and decode
falls off a cliff.

## Which quant, and which llama.cpp

This part is not a preference. From bonsai-codex, which hit all of it:

- **`PTQ1_0` needs the [PrismML llama.cpp fork](https://github.com/PrismML-Eng/llama.cpp).**
  Stock llama.cpp and Ollama cannot read `PTQ1_0` or `PQ2_0` and refuse them. `FORK` points the
  server script at a build of that fork; `MODEL` points it at the GGUF.
- **Do not reach for `Q2_0` instead.** It loads without a warning and produces gibberish: there
  is no Hadamard activation runtime behind that band.
- **Ollama cannot serve this model at all**, for the same reason, which is why this fork talks
  to `llama-server` directly rather than through pi's own llama.cpp provider — that provider
  wants the llama.cpp *router* (`--models-dir`, `/models`, `/llama`), and this setup is a
  single-model server started with `-m`.
- **The MTP variant is net-negative on this card.** The grafted MTP model loads and its drafts
  are good, but decode was slower at every depth: 26.8 t/s with speculation off against 21.3 /
  21.4 / 17.6 t/s at n-max 1 / 2 / 3 at 8k context. Use plain `PTQ1_0`.
- **Prebuilt CUDA binaries work on Turing**, via PTX: the Linux release ships cubins for
  sm_86/89 (+120a/121a) but PTX for sm_50/61/70/75/80/90, so a 20-series card JIT-compiles on
  first run. `cuobjdump --list-elf` makes it look unsupported; check `--list-ptx`. The release
  also needs its matching CUDA runtime, not just the driver. A source build for `sm_75` is equal
  within noise and skips the JIT cost.

## Tested on

| | |
|---|---|
| GPU | NVIDIA RTX 2080 (Turing, sm_75, 8 GB) |
| CPU / RAM | Ryzen 9 5900X / 62 GB |
| OS | Arch Linux, driver 610.57 |
| Runtime | PrismML llama.cpp fork, built for sm_75 |
| Model | `Ternary-Bonsai-2-27B-PTQ1_0.gguf`, 5,671 MiB (5.54 GiB) |

Measured on that setup:

| | |
|---|---|
| prefill | 232–255 t/s |
| decode, shallow context | 26.8 t/s |
| decode, deep context | ~20 t/s |
| first turn of a session | 2,061 prompt tokens |
| `tasks/easy-api`, 4 tests | pass, 98 s, 6 tool calls |

Turn latency is decode-bound, so a 4k-token generation is about three minutes no matter how
fast the link to the model is. Nothing above is a property of the card the client runs on: the
launcher is GPU-agnostic, and only the two calibration numbers and the `--ctx` you can afford
change on other hardware.

## Another machine on the wifi

The model does not have to run on the machine you type on. Serve it to the LAN on the box with
the GPU, and point the other machine's checkout at that address:

```sh
# on the machine with the GPU
HOST=0.0.0.0 PORT=8090 scripts/run-server.sh

# on any other machine on the same wifi (a checkout of this repo; the node bundle is all it needs)
bin/bonsai-pi --server-host 192.168.1.133 --port 8090 -p "implement create_app() so the tests pass"
```

A non-loopback `--server-host` changes exactly one path in the launcher: it starts nothing and
never reads this card's free VRAM (another machine's memory is not this card's problem), and an
unreachable host exits 1 at once with the `HOST=0.0.0.0` command above instead of loading a
model locally. Same profile, same four tools, same one request per turn.

**The window is read, not assumed.** `contextWindow` in the generated `models.json` comes from
the remote server's `/props`, so client and server cannot drift — the mismatch that makes pi
budget requests against a window the server does not have. Measured here with `--dry-run
--server-host 192.168.1.133 --port 8477 --ctx 12288` against a server reporting `n_ctx` 24576:
the resolved config was `"baseUrl": "http://192.168.1.133:8477/v1"` and `"contextWindow":
24576`. `--ctx` applies only to a server this script starts itself.

**The firewall is the part that bites.** llama-server binds `0.0.0.0` happily and logs nothing
wrong, but ufw's default incoming policy is `deny`, so an outside connection is dropped before
the server ever sees it: the client reports a timeout, the server log stays clean, and it reads
like a model problem. On the machine with the GPU:

```sh
sudo ufw allow from 192.168.1.0/24 to any port 8090 proto tcp
```

That is the rule this box runs (`-A ufw-user-input -p tcp --dport 8090 -s 192.168.1.0/24 -j
ACCEPT`, ufw active). `scripts/run-server.sh` prints the same line with its own `$PORT` whenever
`HOST` is not loopback, so the port in the advice is always the port it is about to bind.

**There is no authentication.** llama-server serves an unauthenticated, tool-calling model with
CORS open to all origins: anyone who can reach the port can use the GPU and run inference on your
card. The allow rule is the only thing protecting it, so keep it as narrow as you can — a `/24`,
or a single address with `/32` — and do not expose the port to the internet. For a tighter setup
bind a Tailscale/WireGuard address instead and skip the LAN rule: `HOST=100.x.y.z
scripts/run-server.sh` with `--server-host 100.x.y.z` on the client gives an encrypted,
authenticated path with no open port on the wifi at all.

Traffic from the machine itself to its own LAN address goes over `lo`, which ufw allows, so the
launcher's health probe works before the rule exists (it did here, on a port the rule does not
cover); a *second* machine crosses the ethernet input chain, which is why the rule is needed.
Turn latency is decode-bound at ~20-26 t/s — the figure bonsai-codex measured on this card — and
does not change with the network. A wifi drop mid-generation does lose the turn, though: pi holds
one long-lived HTTP request per turn, so prefer ethernet or a stable 5 GHz link for long runs.

## What it is good at

`tasks/easy-api` from bonsai-codex (single-file stdlib HTTP API, 4 tests): **passes**, 4/4
independently re-run. Six tool calls — read, bash to run the tests, two parallel reads, one
write, bash again — 98 seconds wall clock, peak window 2,061 tokens on the first turn. The
same fixture took 552 s and 9.6k tokens through Codex on the same model, and the bonsai-codex
README records it as the fixture that failed for a long time before the fixes landed.

Expect it to be a **single-file worker**, not a driver. Worked examples in the prompt are what
make it produce usable calls; prose rules get ignored. It is at its worst on reconnaissance —
ask for one file, not a survey.

## Updating from upstream

This is a standalone copy, not a GitHub fork (the same arrangement bonsai-codex uses, and for
the same reason: one account cannot own both a parent and its fork).

```sh
git remote add upstream https://github.com/earendil-works/pi.git
git fetch upstream && git merge upstream/main
```

The fork's own surface is small on purpose: `bin/`, `profile/`, `scripts/`, `tests/`, `setup.sh`,
this file, and the source changes above. Everything else is upstream, so a merge is a merge.

## Development

```sh
./setup.sh                       # prerequisites, and the exact command for anything missing
npm ci && npm run build          # node >= 22.19; the bundle lands in packages/coding-agent/dist
python3 tests/test_bonsai_pi.py  # budget arithmetic, generated config, guards, arg forwarding
npx vitest --run packages/coding-agent/test/tools.test.ts
```

`--dry-run` prints the resolved argv, one argument per line, and writes the generated configs
without starting anything — that is what the launcher tests assert on.

The launcher is 280 lines of bash and the only place a mistake costs a model load, so run
[ShellCheck](https://github.com/koalaman/shellcheck) over it when you touch it. It is a
development tool, not a runtime dependency, and nothing here requires it:

```sh
shellcheck bin/bonsai-pi scripts/run-server.sh setup.sh
```

Note that the pre-commit hook (`npm run check`) fails on this checkout for an upstream reason
unrelated to the fork — `node:sqlite` is undeclared in
`packages/session-backends/sqlite-node` — so commits here use `--no-verify` and the checks listed
above are run by hand instead.

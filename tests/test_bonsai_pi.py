#!/usr/bin/env python3
"""Checks for the bonsai profile: prompt budget, generated config, VRAM guard, arg forwarding,
and the remote --server-host path.

Run: python3 tests/test_bonsai_pi.py
"""
import http.server
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LAUNCHER = REPO / "bin" / "bonsai-pi"
SERVER_SCRIPT = REPO / "scripts" / "run-server.sh"

# The two profile files together must stay a small fraction of the 24576-token window. 6400
# characters is ~1600 tokens, ~6.5% of the window; with the tool schemas the whole fixed cost is
# ~2500 tokens, ~10%. The number is a guard against slow growth, not a hard limit - raise it
# deliberately, with a measurement, not to make an edit fit.
PROMPT_CHAR_BUDGET = 6400


def run_launcher(args, env_extra=None, expect=0):
    env = dict(os.environ)
    env["XDG_STATE_HOME"] = tempfile.mkdtemp(prefix="bonsai-state-")
    env["BONSAI_PORT"] = "8099"  # nothing listens here, so no live server changes the path
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run(
        [str(LAUNCHER), *args], capture_output=True, text=True, env=env, timeout=120, cwd=REPO
    )
    assert proc.returncode == expect, f"exit {proc.returncode} != {expect}\n{proc.stdout}\n{proc.stderr}"
    return proc, Path(env["XDG_STATE_HOME"])


class StubLlama:
    """The endpoints the launcher touches (/health, /props, /v1/models), on 127.0.0.2.

    127.0.0.2 is loopback to the kernel but not in the launcher's local list
    (127.0.0.1|localhost|::1), so this takes the remote branch without needing a LAN.
    """

    HOST = "127.0.0.2"
    N_CTX = 12288  # deliberately not the 24576 the launcher assumes: the window is /props's

    def __enter__(self):
        self.server = http.server.ThreadingHTTPServer((self.HOST, 0), _StubHandler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *_exc):
        self.server.shutdown()
        self.server.server_close()


class _StubHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = {
            "/health": {"status": "ok"},
            "/props": {"default_generation_settings": {"n_ctx": StubLlama.N_CTX}},
            "/v1/models": {"object": "list", "data": [{"id": "stub"}]},
        }.get(self.path.split("?")[0])
        if body is None:
            self.send_error(404)
            return
        raw = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *_args):
        pass


class BashSyntax(unittest.TestCase):
    def test_scripts_parse(self):
        for script in (LAUNCHER, SERVER_SCRIPT):
            subprocess.run(["bash", "-n", str(script)], check=True)

    def test_launcher_is_executable(self):
        self.assertTrue(os.access(LAUNCHER, os.X_OK))
        self.assertTrue(os.access(SERVER_SCRIPT, os.X_OK))


class ProfileBudget(unittest.TestCase):
    def test_prompt_files_stay_within_budget(self):
        combined = (REPO / "profile" / "bonsai.md").read_text() + (REPO / "profile" / "ponytail.md").read_text()
        self.assertLess(len(combined), PROMPT_CHAR_BUDGET, f"{len(combined)} chars")

    def test_ponytail_ladder_is_present(self):
        rules = (REPO / "profile" / "ponytail.md").read_text()
        for rung in ("YAGNI", "standard library", "one line", "minimum code that works"):
            self.assertIn(rung, rules)
        # The safety carve-out is the part a "write less code" prompt gets wrong.
        self.assertIn("Not lazy about", rules)


class GeneratedConfig(unittest.TestCase):
    def test_context_window_and_compaction_follow_the_window(self):
        for ctx in (24576, 16384, 12288, 8192):
            with self.subTest(ctx=ctx):
                proc, state = run_launcher(["--dry-run", "--ctx", str(ctx), "-p", "hi"])
                home = state / "bonsai-pi" / "pi-home"

                models = json.loads((home / "models.json").read_text())["providers"]["bonsai"]
                model = models["models"][0]
                self.assertEqual(model["contextWindow"], ctx)
                self.assertEqual(models["baseUrl"], "http://127.0.0.1:8099/v1")
                # The server rejects max_completion_tokens; llama-server speaks max_tokens.
                self.assertEqual(models["compat"]["maxTokensField"], "max_tokens")

                settings = json.loads((home / "settings.json").read_text())
                reserve = settings["compaction"]["reserveTokens"]
                recent = settings["compaction"]["keepRecentTokens"]

                # pi subtracts its own CONTEXT_SAFETY_TOKENS from whatever is left, so the room
                # the model actually gets at the compaction trigger is `reserve - 4096`. That,
                # not `ctx - reserve - 4096`, is the number that has to be usable. The old
                # invariant passed at 12288 while the real answer allowance was one token.
                answer = model["maxTokens"]
                self.assertEqual(reserve, answer + 4096)
                self.assertGreaterEqual(reserve - 4096, 1024, "answer room at the trigger")
                self.assertGreaterEqual(answer, 1024)
                # keepRecent is what survives a compaction verbatim; if it reached the trigger
                # the next turn would compact again immediately.
                self.assertLessEqual(recent, (ctx - reserve) // 2)
                self.assertLess(recent, ctx - reserve)
                self.assertGreaterEqual(ctx - reserve, 2048)

    def test_rejects_a_window_that_cannot_hold_a_prompt_and_an_answer(self):
        proc, _ = run_launcher(["--dry-run", "--ctx", "4096", "-p", "hi"], expect=1)
        self.assertIn("a 4096-token window leaves", proc.stderr)
        self.assertIn("--ctx 8192", proc.stderr)

    def test_command_line_restricts_tools_and_discovery(self):
        proc, _ = run_launcher(["--dry-run", "-p", "hi"])
        argv = proc.stdout.split("bonsai-pi: argv:\n", 1)[1].splitlines()
        # --tools takes one comma-separated argument, not five.
        self.assertIn("read,grep,bash,edit,write", argv)
        self.assertEqual(argv[argv.index("--tools") + 1], "read,grep,bash,edit,write")
        self.assertEqual(argv[argv.index("--provider") + 1], "bonsai")
        self.assertEqual(argv[argv.index("--model") + 1], "bonsai")
        for flag in ("--no-skills", "--no-extensions", "--no-prompt-templates", "--no-themes", "--no-context-files"):
            self.assertIn(flag, argv)
        # The system prompt goes through as one multi-line argument: both halves must be there.
        self.assertIn("You are a coding agent with five tools", proc.stdout)
        self.assertIn("You are a lazy senior developer", proc.stdout)

    def test_hook_extensions_are_loaded_explicitly(self):
        # --no-extensions disables discovery only; the hooks have to be named or the model never
        # sees a test result it did not ask for.
        proc, _ = run_launcher(["--dry-run", "-p", "hi"])
        argv = proc.stdout.split("bonsai-pi: argv:\n", 1)[1].splitlines()
        loaded = [argv[i + 1] for i, a in enumerate(argv) if a == "-e"]
        self.assertEqual(
            [Path(p).name for p in loaded],
            [
                "check-after-edit.ts",
                "nudge-after-reads.ts",
                "nudge-code-in-prose.ts",
                "block-rewrite.ts",
            ],
        )
        for path in loaded:
            self.assertTrue(Path(path).is_file(), path)

    def test_context_files_are_opt_in(self):
        proc, _ = run_launcher(["--dry-run", "--context-files", "-p", "hi"])
        self.assertNotIn("--no-context-files", proc.stdout)

    def test_pi_arguments_are_forwarded_untouched(self):
        proc, _ = run_launcher(["--dry-run", "--ctx", "16384", "-p", "do the thing", "--verbose"])
        argv = proc.stdout.split("bonsai-pi: argv:\n", 1)[1].splitlines()
        self.assertIn("--verbose", argv)
        self.assertEqual(argv[argv.index("-p") + 1], "do the thing")


class CheckIsAnnounced(unittest.TestCase):
    """The model is told what the check is, and that it runs by itself.

    It was told neither. Every run paid to find out: 4 turns of `ls vitest.config*` and
    `grep '"test"' package.json`, then 1-4 hand-run vitest invocations of its own - twice over the
    whole test directory, which put 8k tokens in the context and cost 169 s and 305 s to re-read.
    Counting the turns that would not exist if it knew, they are 20-50% of wall time.
    """

    def test_the_check_reaches_the_system_prompt(self):
        proc, _ = run_launcher(
            ["--dry-run", "-p", "hi"],
            env_extra={"BONSAI_CHECK_COMMAND": "npx vitest run packages/core/x.spec.ts"},
        )
        self.assertIn("npx vitest run packages/core/x.spec.ts", proc.stdout)
        self.assertIn("runs automatically", proc.stdout)
        self.assertIn("do not run it yourself", proc.stdout)

    def test_no_check_costs_no_prompt_tokens(self):
        # Unset, the line must not exist at all: it is a prompt token on every request otherwise.
        proc, _ = run_launcher(["--dry-run", "-p", "hi"], env_extra={"BONSAI_CHECK_COMMAND": ""})
        self.assertNotIn("## The check", proc.stdout)

    def test_the_line_can_be_switched_off_for_a_control_arm(self):
        # The evaluator always sets BONSAI_CHECK_COMMAND, so without this gate there is no way to
        # run the control in the same batch as the arm.
        proc, _ = run_launcher(
            ["--dry-run", "-p", "hi"],
            env_extra={"BONSAI_CHECK_COMMAND": "npx vitest run x.spec.ts", "BONSAI_ANNOUNCE_CHECK": "0"},
        )
        self.assertNotIn("## The check", proc.stdout)


class Guards(unittest.TestCase):
    def test_refuses_to_start_the_server_without_vram(self):
        proc, state = run_launcher(
            ["--ctx", "24576", "-p", "hi"],
            env_extra={"BONSAI_OVERHEAD_MIB": "999999"},
            expect=1,
        )
        # The requirement is the model's own size plus overhead, not a number picked for one
        # card, so both parts have to appear in the refusal.
        model = Path(
            os.environ.get("BONSAI_MODEL")
            or Path.home() / "projects/bonsai2-cuda/models/Ternary-Bonsai-2-27B-PTQ1_0.gguf"
        )
        if model.is_file():
            self.assertIn(f"{model.stat().st_size // 1048576} MiB of weights", proc.stderr)
        self.assertIn("999999 MiB of CUDA context", proc.stderr)
        self.assertFalse((state / "bonsai-pi" / "llama-server.pid").exists())

    def test_vram_guard_is_inert_for_a_model_that_is_not_there(self):
        # No model file means no size to compare against: the launcher must not invent one.
        missing = Path(tempfile.mkdtemp(prefix="bonsai-nomodel-")) / "absent.gguf"
        proc, _ = run_launcher(
            ["--ctx", "24576", "-p", "hi"],
            env_extra={"BONSAI_MODEL": str(missing), "BONSAI_OVERHEAD_MIB": "999999"},
            expect=1,
        )
        # It still reaches the server start and dies on the missing model, not on VRAM.
        self.assertNotIn("MiB of weights", proc.stderr)
        self.assertIn("no model at", proc.stderr)

    def test_stop_is_a_no_op_without_a_pidfile(self):
        proc, _ = run_launcher(["--stop"])
        self.assertIn("no llama-server started by this script", proc.stdout)

    def test_rejects_a_non_numeric_context(self):
        proc, _ = run_launcher(["--ctx", "lots"], expect=2)
        self.assertIn("--ctx must be a number", proc.stderr)

    def test_reasoning_budget_is_a_flag(self):
        # It was env-only; the A/B that showed 512 halves thinking time made it worth a flag.
        proc, _ = run_launcher(["--dry-run", "--reasoning", "512", "-p", "hi"])
        self.assertIn("context from server", proc.stdout)  # still a dry run, nothing started
        proc, _ = run_launcher(["--reasoning", "lots"], expect=2)
        self.assertIn("--reasoning must be a number", proc.stderr)


class RemoteServer(unittest.TestCase):
    """--server-host is another machine: this checkout starts nothing and guards nothing."""

    def test_unreachable_remote_host_fails_with_the_serve_command(self):
        # 192.0.2.0/24 is TEST-NET-1: never a LAN address, so /health cannot answer.
        proc, state = run_launcher(
            ["--server-host", "192.0.2.1", "--port", "8090", "-p", "hi"],
            env_extra={"BONSAI_OVERHEAD_MIB": "999999"},
            expect=1,
        )
        self.assertIn("HOST=0.0.0.0 PORT=8090 scripts/run-server.sh", proc.stderr)
        # 999999 MiB is never free: a VRAM line here would mean the local guard ran first.
        self.assertNotIn("MiB", proc.stderr)
        self.assertNotIn("would start", proc.stdout)
        self.assertFalse((state / "bonsai-pi" / "llama-server.pid").exists())

    def test_dry_run_uses_the_remote_url_and_the_window_it_reports(self):
        with StubLlama() as stub:
            proc, state = run_launcher(
                ["--dry-run", "--server-host", StubLlama.HOST, "--port", str(stub.port), "-p", "hi"],
                env_extra={"no_proxy": "*", "NO_PROXY": "*"},  # a proxy must not answer for /props
            )
        models = json.loads((state / "bonsai-pi" / "pi-home" / "models.json").read_text())
        provider = models["providers"]["bonsai"]
        self.assertEqual(provider["baseUrl"], f"http://{StubLlama.HOST}:{stub.port}/v1")
        # The server said 12288; the launcher's own default of 24576 would mean it assumed.
        self.assertEqual(provider["models"][0]["contextWindow"], StubLlama.N_CTX)
        self.assertIn(f"context from server: {StubLlama.N_CTX}", proc.stdout)
        self.assertNotIn("would start", proc.stdout)


class ToolOutputCaps(unittest.TestCase):
    """One tool result must not eat a fifth of the window (24576 tokens)."""

    def test_caps_fit_the_window(self):
        source = (REPO / "packages/coding-agent/src/core/tools/truncate.ts").read_text()
        lines = int(re.search(r"DEFAULT_MAX_LINES = (\d+)", source).group(1))
        kib = int(re.search(r"DEFAULT_MAX_BYTES = (\d+) \* 1024", source).group(1))
        self.assertLessEqual(kib * 1024 / 4, 24576 * 0.25, f"{kib}KB is over a quarter of the window")
        self.assertLessEqual(lines, 500)


DRIVER = """
import checkAfterEdit from "__EXT__";
const handlers = {};
const pi = { on(event, fn) { (handlers[event] ??= []).push(fn); } };
checkAfterEdit(pi);
const out = await handlers.tool_result[0](
  { toolName: "write", isError: false, content: [{ type: "text", text: "wrote the file" }] },
  { cwd: process.cwd(), signal: undefined },
);
process.stdout.write(out.content.map((c) => c.text).join(""));
"""


def node_with_type_stripping():
    """A node that can import a `.ts` extension: 22.19+, where stripping is on by default."""
    nvm = Path.home() / ".nvm" / "versions" / "node"
    candidates = [os.environ.get("BONSAI_NODE"), shutil.which("node")]
    if nvm.is_dir():
        candidates += [str(p / "bin" / "node") for p in sorted(nvm.iterdir(), reverse=True)]
    for path in candidates:
        if not path or not Path(path).is_file():
            continue
        try:
            proc = subprocess.run(
                [path, "-p", "process.versions.node"], capture_output=True, text=True, timeout=30
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        version = proc.stdout.strip()
        if version and tuple(int(x) for x in version.split(".")[:2]) >= (22, 19):
            return path
    return None


NODE = node_with_type_stripping()


def check_advice_for(failure_text):
    """Run the real check-after-edit extension and return what it appends to the tool result."""
    tmp = Path(tempfile.mkdtemp(prefix="bonsai-parse-"))
    (tmp / "check-out.txt").write_text(failure_text)
    (tmp / "driver.mts").write_text(DRIVER.replace("__EXT__", str(REPO / "profile/check-after-edit.ts")))
    env = dict(os.environ)
    # `exit 1` is what makes the hook take the failure branch; the text is the check's output.
    env["BONSAI_CHECK_COMMAND"] = f"cat {tmp / 'check-out.txt'}; exit 1"
    proc = subprocess.run(
        [NODE, str(tmp / "driver.mts")], capture_output=True, text=True, env=env, timeout=120, cwd=REPO
    )
    assert proc.returncode == 0, f"driver failed:\n{proc.stdout}\n{proc.stderr}"
    return proc.stdout


@unittest.skipUnless(NODE, "needs node >= 22.19 to import a .ts extension")
class CheckParseAdvice(unittest.TestCase):
    """A file that does not parse must be reported as the model's own syntax error.

    Measured on the entity-snapshot task: the check answered `[PARSE_ERROR] Unterminated string`
    at entity-snapshot.spec.ts:86:68, the model read it as a broken environment, and spent the
    next fifteen turns grepping node_modules for "oxc" and "Transform failed" - it never went
    back to its own line 86, and the 1800 s wall clock ended the run. Saying whose fault it is
    is the whole fix, so it is worth a check that it still says so.
    """

    # The real output, colour codes and all: the escapes sit between the characters of the
    # `╭─[` that opens the position line, so the parser has to strip them before it can read it.
    PARSE_FAILURE = (
        " FAIL  packages/core/__tests__/entity-snapshot.spec.ts\n"
        "Error: Transform failed with 1 error:\n\n"
        "\x1b[31m[PARSE_ERROR] \x1b[0mUnterminated string\n"
        "    \x1b[38;5;246m╭\x1b[0m\x1b[38;5;246m─\x1b[0m\x1b[38;5;246m[\x1b[0m"
        " packages/core/__tests__/entity-snapshot.spec.ts:86:68 \x1b[38;5;246m]\x1b[0m\n"
    )

    def test_a_parse_error_is_reported_as_the_models_own_syntax(self):
        advice = check_advice_for(self.PARSE_FAILURE)
        self.assertIn("does not parse", advice)
        self.assertIn("entity-snapshot.spec.ts", advice)
        self.assertIn("line 86, column 68", advice)
        self.assertIn("not a problem with vitest, oxc", advice)

    def test_a_failing_assertion_is_not_dressed_up_as_a_parse_error(self):
        # The classification must not swallow the ordinary case, which is most of them.
        advice = check_advice_for(" FAIL  some.spec.ts\nAssertionError: expected 1 to be 2\n")
        self.assertNotIn("does not parse", advice)
        self.assertIn("the check FAILS", advice)


if __name__ == "__main__":
    unittest.main(verbosity=2)

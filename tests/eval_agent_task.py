#!/usr/bin/env python3
"""Evaluator for the bonsai-pi autoresearch loop. IMMUTABLE during a run.

Runs the agent on one fixed task, verifies the result independently, parses the session
transcript for the metrics the harness is being judged on, and appends one TSV row.

    python3 tests/eval_agent_task.py --trial 1 --arm baseline [--env KEY=VAL ...]

Primary metric: wall_s, the seconds from launch to the last assistant message. Secondary:
turns, check_fails, rewrites, prose_max, passed. Nothing here may be edited by an experiment;
if it is, the ledger is worthless.
"""
import argparse
import datetime
import json
import os
import pathlib
import subprocess
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
WORKTREE = pathlib.Path("/home/joao/projects/threenative/threenative-engine/.worktrees/bonsai-e2e")
SPEC = WORKTREE / "packages/core/__tests__/entity-snapshot.spec.ts"
SESSIONS = pathlib.Path.home() / ".local/state/bonsai-pi/pi-home/sessions"
LEDGER = REPO / "docs/autoresearch/results.tsv"
LOGS = REPO / "docs/autoresearch/logs"

CHECK_CMD = "npx vitest run packages/core/__tests__/entity-snapshot.spec.ts"
TIMEOUT_S = 1800
# A write whose arguments exceed this is a whole-file rewrite rather than a scratch file.
REWRITE_CHARS = 2000

TASK = """Add a unit test file for the entity snapshot helpers.

File to test: packages/core/src/entity-snapshot.ts. It exports autoFields, disposeEntity,
assertNotIterating and snapshotEntities.
Write the tests to: packages/core/__tests__/entity-snapshot.spec.ts

Look at an existing spec in packages/core/__tests__/ for the project's conventions.

Cover the documented behaviour you find in the module: which fields autoFields keeps and
which it drops, the 24-field limit, how snapshotEntities prefers debug() over autoFields, when
it includes tags and when it omits them, that it copies the tags array, that disposeEntity
calls dispose only when it exists, and what assertNotIterating throws."""


def reset_worktree() -> None:
    SPEC.unlink(missing_ok=True)
    if (WORKTREE / ".probe.mjs").exists():
        (WORKTREE / ".probe.mjs").unlink()
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=WORKTREE, capture_output=True, text=True).stdout.strip()
    if dirty:
        raise SystemExit(f"worktree is not clean before the trial:\n{dirty}")


def verify() -> bool:
    """Independent of the agent: run the check ourselves."""
    r = subprocess.run(["npx", "vitest", "run", "packages/core/__tests__/entity-snapshot.spec.ts"],
                       cwd=WORKTREE, capture_output=True, text=True, timeout=600,
                       env={**os.environ, "PATH": "/home/joao/.nvm/versions/node/v22.22.0/bin:" + os.environ["PATH"]})
    return r.returncode == 0


def newest_session(after: float) -> pathlib.Path | None:
    best = None
    for p in SESSIONS.glob("*/*.jsonl"):
        if p.stat().st_mtime >= after and (best is None or p.stat().st_mtime > best.stat().st_mtime):
            best = p
    return best


def parse(session: pathlib.Path) -> dict:
    turns = 0
    gaps: list[float] = []
    check_fails = check_passes = rewrites = prose_max = blocked = stopped = 0
    prev = None
    for line in session.read_text().splitlines():
        try:
            e = json.loads(line)
        except Exception:
            continue
        if e.get("type") != "message":
            continue
        m = e["message"]
        ts = e.get("timestamp")
        t = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")) if ts else None
        if t and m.get("role") == "assistant" and prev:
            gaps.append((t - prev).total_seconds())
        if t:
            prev = t
        if m.get("role") == "assistant":
            turns += 1
            content = m.get("content", [])
            prose = sum(len(b.get("text", "")) for b in content if isinstance(b, dict) and b.get("type") == "text")
            prose_max = max(prose_max, prose)
            for b in content:
                if isinstance(b, dict) and b.get("type") == "toolCall" and b.get("name") == "write":
                    if len(str(b.get("arguments"))) > REWRITE_CHARS:
                        rewrites += 1
        elif m.get("role") == "toolResult":
            text = json.dumps(m.get("content"))
            if "check:" in text and "exited 0" in text:
                check_passes += 1
            elif "check:" in text and "exited" in text:
                check_fails += 1
            if "already wrote" in text and "in this session" in text:
                blocked += 1
            if "consecutive times" in text:
                stopped += 1
    return {
        "turns": turns,
        "wall_s": round(sum(gaps)),
        "last_turn_s": round(gaps[-1]) if gaps else 0,
        "check_fails": check_fails,
        "check_passes": check_passes,
        "rewrites": rewrites,
        "prose_max": prose_max,
        "blocked": blocked,
        "stopped": stopped,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trial", required=True)
    ap.add_argument("--arm", required=True)
    ap.add_argument("--env", action="append", default=[])
    args = ap.parse_args()

    arm_env = dict(kv.split("=", 1) for kv in args.env)
    LOGS.mkdir(parents=True, exist_ok=True)
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    log = LOGS / f"trial-{args.trial}.log"

    reset_worktree()
    started = time.time()
    proc = subprocess.Popen(
        [str(REPO / "bin/bonsai-pi"), "-p", TASK],
        cwd=WORKTREE,
        stdout=log.open("w"),
        stderr=subprocess.STDOUT,
        env={**os.environ, "BONSAI_CHECK_COMMAND": CHECK_CMD, **arm_env},
    )
    status = "ok"
    try:
        proc.wait(timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        proc.kill()
        status = "timeout"

    session = newest_session(started)
    m = parse(session) if session else {}
    passed = verify()

    row = {
        "experiment": args.trial,
        "arm": args.arm,
        "wall_s": m.get("wall_s", ""),
        "turns": m.get("turns", ""),
        "check_fails": m.get("check_fails", ""),
        "rewrites": m.get("rewrites", ""),
        "prose_max": m.get("prose_max", ""),
        "blocked": m.get("blocked", ""),
        "stopped": m.get("stopped", ""),
        "passed": int(passed),
        "status": status if passed else ("fail" if status == "ok" else status),
        "duration_s": round(time.time() - started),
        "env": ";".join(f"{k}={v}" for k, v in sorted(arm_env.items())),
    }
    fields = list(row)
    new = not LEDGER.exists()
    with LEDGER.open("a") as fh:
        if new:
            fh.write("\t".join(fields) + "\n")
        fh.write("\t".join(str(row[k]) for k in fields) + "\n")

    print("\t".join(fields))
    print("\t".join(str(row[k]) for k in fields))
    print(f"session: {session}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

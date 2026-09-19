#!/usr/bin/env python3
"""Read the newest transcripts and report what the agent did that wasted time.

Not part of the evaluator: this exists to turn a transcript into a hypothesis. The evaluator
stays fixed so trials stay comparable; this can change whenever it is useful.

    python3 tests/diagnose_session.py [n]
"""
import datetime
import json
import pathlib
import sys
from collections import Counter

SESSIONS = pathlib.Path.home() / ".local/state/bonsai-pi/pi-home/sessions"

# Looking for how the project runs its tests, rather than working on the task. Measured at 2-3
# turns per run in trials 3 and 4, and the launcher already knows BONSAI_CHECK_COMMAND.
SETUP_HINTS = ("vitest.config", "vite.config")


def is_setup_reconnaissance(name: str, args: str) -> bool:
    if any(h in args for h in SETUP_HINTS):
        return True
    # Reading or grepping package.json is how it finds the test script. `read` is excluded: an
    # existing spec is a legitimate convention reference, and the task asks for one.
    return name in ("bash", "grep") and "package.json" in args


def report(path: pathlib.Path) -> None:
    turn = 0
    reads = 0
    first_write_turn = None
    reads_before_write = None
    repeated: Counter = Counter()
    reads_by_path: Counter = Counter()
    rejected = []
    big_writes = []
    prose = []
    setup = []
    self_checks = []
    gaps = []
    previous = None

    for line in path.read_text().splitlines():
        try:
            e = json.loads(line)
        except Exception:
            continue
        if e.get("type") != "message":
            continue
        m = e["message"]
        if m.get("role") == "assistant":
            turn += 1
            stamp = e.get("timestamp")
            if stamp and previous:
                try:
                    now = datetime.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                    gaps.append(round((now - previous).total_seconds()))
                except ValueError:
                    pass
            if stamp:
                try:
                    previous = datetime.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                except ValueError:
                    pass
            content = m.get("content", [])
            calls = [b for b in content if isinstance(b, dict) and b.get("type") == "toolCall"]
            reads_at_turn_start = reads
            for b in calls:
                name = b.get("name")
                args = str(b.get("arguments"))
                # Keyed on the whole argument object: a 60-character prefix made 23 distinct
                # commands that share a `cd <worktree> && ` prefix look like 15 repeats of one,
                # and sent a whole experiment after a defect that did not exist.
                repeated[f"{name}:{json.dumps(b.get('arguments'), sort_keys=True)}"] += 1
                if name == "read":
                    reads_by_path[(b.get("arguments") or {}).get("path")] += 1
                if name in ("read", "grep", "bash"):
                    reads += 1
                if is_setup_reconnaissance(name, args):
                    setup.append((turn, args[:80]))
                if name == "bash" and "vitest run" in args:
                    self_checks.append(turn)
                if name == "write" and len(args) > 2000:
                    big_writes.append((turn, len(args)))
                if name in ("write", "edit") and first_write_turn is None:
                    first_write_turn = turn
                    reads_before_write = reads_at_turn_start
            p = sum(len(b.get("text", "")) for b in content if isinstance(b, dict) and b.get("type") == "text")
            if p:
                prose.append((turn, p))
        elif m.get("role") == "toolResult" and m.get("isError"):
            rejected.append((turn, json.dumps(m.get("content"))[:90]))

    dupes = {k: v for k, v in repeated.items() if v > 1}
    rereads = {k: v for k, v in reads_by_path.items() if v > 1 and k}
    print(f"\n=== {path.name}")
    print(f"  turns                              : {turn}")
    print(f"  reads before the first write       : {reads_before_write}")
    print(f"  first write/edit at turn           : {first_write_turn}")
    print(f"  whole-file writes (>{2000} chars)   : {big_writes}")
    print(f"  prose in a reply                   : {prose}")
    print(f"  tool errors                        : {rejected}")
    print(f"  repeated identical calls           : {dupes}")
    print(f"  setup reconnaissance turns         : {setup}")
    print(f"  checks run by hand (hook ran it)   : {self_checks}")
    print(f"  same path read again               : {rereads}")
    if gaps:
        ordered = sorted(gaps)
        print(f"  seconds per turn (median, max)     : {ordered[len(ordered) // 2]}, {ordered[-1]}")
        print(f"  seconds unseen, all turns          : {sum(gaps)}")


def main() -> int:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    files = sorted(SESSIONS.glob("*/*.jsonl"), key=lambda p: p.stat().st_mtime)[-n:]
    for f in files:
        report(f)
    return 0


if __name__ == "__main__":
    sys.exit(main())

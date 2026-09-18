#!/usr/bin/env python3
"""Read the newest transcripts and report what the agent did that wasted time.

Not part of the evaluator: this exists to turn a transcript into a hypothesis. The evaluator
stays fixed so trials stay comparable; this can change whenever it is useful.

    python3 tests/diagnose_session.py [n]
"""
import json
import pathlib
import sys
from collections import Counter

SESSIONS = pathlib.Path.home() / ".local/state/bonsai-pi/pi-home/sessions"


def report(path: pathlib.Path) -> None:
    reads = 0
    first_write_turn = None
    turn = 0
    repeated: Counter = Counter()
    rejected = []
    big_writes = []
    prose = []

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
            content = m.get("content", [])
            calls = [b for b in content if isinstance(b, dict) and b.get("type") == "toolCall"]
            if first_write_turn is None:
                for b in calls:
                    if b.get("name") in ("write", "edit"):
                        first_write_turn = turn
            for b in calls:
                args = str(b.get("arguments"))
                repeated[f"{b.get('name')}:{args[:60]}"] += 1
                if b.get("name") == "write" and len(args) > 2000:
                    big_writes.append((turn, len(args)))
                if b.get("name") in ("read", "grep", "bash"):
                    reads += 1
            p = sum(len(b.get("text", "")) for b in content if isinstance(b, dict) and b.get("type") == "text")
            if p:
                prose.append((turn, p))
        elif m.get("role") == "toolResult" and m.get("isError"):
            rejected.append((turn, json.dumps(m.get("content"))[:90]))

    dupes = {k: v for k, v in repeated.items() if v > 1}
    print(f"\n=== {path.name}")
    print(f"  reader calls before first write : {reads if first_write_turn is None else 'n/a (see below)'}")
    print(f"  first write/edit at turn        : {first_write_turn}")
    print(f"  whole-file writes (>{2000} chars): {big_writes}")
    print(f"  prose in a reply                : {prose}")
    print(f"  tool errors                     : {rejected}")
    print(f"  repeated identical calls        : {dupes}")


def main() -> int:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    files = sorted(SESSIONS.glob("*/*.jsonl"), key=lambda p: p.stat().st_mtime)[-n:]
    for f in files:
        report(f)
    return 0


if __name__ == "__main__":
    sys.exit(main())

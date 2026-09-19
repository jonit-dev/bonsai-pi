#!/usr/bin/env python3
"""Summarise the trial ledger by arm, with failures charged at the timeout.

    python3 tests/rank_trials.py

A run that times out, dies, or fails the check is *not* a fast run. Counting it at 0 seconds, or
dropping it, is how an optimisation loop talks itself into a regression - so every non-pass is
charged the full 1800 s budget here, and the passes are shown on their own.

Arms are compared only within the same harness: rows recorded before a change was absorbed are a
different candidate, and this prints the ledger in trial order so that stays visible.

Name an arm `baseline + <what changed>`, and keep `baseline` for runs of the unchanged candidate.
Trial 3 was recorded as `parse advice` while trials 4 and 5 were recorded as
`baseline (post-advice)` - the same code under two labels, which reads as two arms and is not.
"""
import pathlib
import statistics
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
LEDGER = REPO / "docs/autoresearch/results.tsv"
BUDGET_S = 1800


def rows() -> list[dict]:
    lines = LEDGER.read_text().splitlines()
    if not lines:
        return []
    fields = lines[0].split("\t")
    out = []
    for line in lines[1:]:
        if not line.strip():
            continue
        out.append(dict(zip(fields, line.split("\t"))))
    return out


def main() -> int:
    if not LEDGER.exists():
        print(f"no ledger at {LEDGER}", file=sys.stderr)
        return 1

    data = rows()
    if not data:
        print("ledger has no trials yet")
        return 0

    print(f"{'trial':>5}  {'arm':<24} {'wall_s':>7} {'passed':>6} {'charged':>8}  status")
    for r in data:
        wall = int(r["wall_s"]) if str(r["wall_s"]).isdigit() else None
        passed = r["passed"] == "1"
        charged = wall if passed and wall is not None else BUDGET_S
        shown = wall if wall is not None else "-"
        print(f"{r['experiment']:>5}  {r['arm']:<24} {shown:>7} {r['passed']:>6} {charged:>8}  {r['status']}")

    groups: dict[str, list[dict]] = {}
    for r in data:
        groups.setdefault(r["arm"], []).append(r)

    print("\nby arm")
    print(f"{'arm':<24} {'n':>2} {'pass':>5} {'charged median':>15} {'passes median':>14}")
    ranked = []
    for arm, rs in groups.items():
        charged = []
        passes = []
        for r in rs:
            wall = int(r["wall_s"]) if str(r["wall_s"]).isdigit() else None
            if r["passed"] == "1" and wall is not None:
                passes.append(wall)
                charged.append(wall)
            else:
                charged.append(BUDGET_S)
        med = statistics.median(charged)
        pmed = statistics.median(passes) if passes else None
        ranked.append((med, arm, len(rs), len(passes), pmed))
    for med, arm, n, npass, pmed in sorted(ranked):
        ptext = f"{pmed:.0f}" if pmed is not None else "-"
        print(f"{arm:<24} {n:>2} {npass:>5} {med:>15.0f} {ptext:>14}")

    print(
        "\nNothing here is a rate until n is large enough to separate the arms: the same code has "
        "passed in 363 s and timed out at 1801 s."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

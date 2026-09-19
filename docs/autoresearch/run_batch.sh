#!/usr/bin/env bash
# Run a queue of trials, cleaning the worktree between them.
#
#   docs/autoresearch/run_batch.sh "5|baseline|" "6|baseline + check-prompt|"
#
# Each argument is "trial|arm|ENV=VAL" with the third field optional.
#
# The cleanup is not decoration. A trial killed mid-run leaves the spec file behind; the next
# evaluator then dies in reset_worktree() *before* it opens its log, so there is no trial-N.log,
# no ledger row, and nothing on stdout to say a trial went missing - the batch simply moves on.
# That is how trial 8 vanished (README, defect 3).
set -u
cd "$(dirname "$0")/../.." || exit 1

WORKTREE=/home/joao/projects/threenative/threenative-engine/.worktrees/bonsai-e2e
SPEC=packages/core/__tests__/entity-snapshot.spec.ts

for spec in "$@"; do
  trial="${spec%%|*}"
  rest="${spec#*|}"
  arm="${rest%%|*}"
  env="${rest#*|}"
  [ "$env" = "$arm" ] && env=""

  git -C "$WORKTREE" checkout -- . 2>/dev/null
  rm -f "$WORKTREE/$SPEC"

  echo "=== $(date +%H:%M:%S) trial $trial | $arm | ${env:-no env}"
  if [ -n "$env" ]; then
    python3 tests/eval_agent_task.py --trial "$trial" --arm "$arm" --env "$env" 2>&1 | tail -3
  else
    python3 tests/eval_agent_task.py --trial "$trial" --arm "$arm" 2>&1 | tail -3
  fi
done

echo "=== batch done $(date +%H:%M:%S)"
cat docs/autoresearch/results.tsv

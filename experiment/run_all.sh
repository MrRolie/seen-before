#!/usr/bin/env bash
# Pilot on dev, the stop rule, the leak check again, then the test.
# Resumes after infrastructure stops; a contaminated call (exit 3) or a failed leak check stops everything.
set -u
cd "$(dirname "$0")/.."
CANARIES="${SB_CANARIES:?set SB_CANARIES to the canary file}"
run() {  # $1 split, $2 out
  for attempt in 1 2 3 4 5 6 7 8; do
    .venv/bin/python -m experiment.run --arms A,F,B,C,D --split "$1" --out "$2"; rc=$?
    [ $rc -eq 0 ] && return 0
    [ $rc -eq 3 ] && { echo "CONTAMINATED: stopped" >&2; return 3; }
    echo "run stopped (rc $rc, attempt $attempt); waiting 30 min before resuming" >&2
    sleep 1800
  done
  return 1
}
run dev runs/pilot || { echo "PILOT DID NOT FINISH" >&2; exit 1; }
.venv/bin/python -m experiment.gate runs/pilot || { echo "STOP RULE FIRED: test not started" >&2; exit 2; }
.venv/bin/python -m experiment.leakcheck --canaries "$CANARIES" --out runs/leakcheck-before-test.json \
  || { echo "LEAK CHECK FAILED: test not started" >&2; exit 3; }
run test runs/test || { echo "TEST DID NOT FINISH" >&2; exit 1; }
echo "ALL DONE" >&2

#!/usr/bin/env python3
"""The stop rule between pilot and test (fixed before the pilot: the pilot must run cleanly end to end). Exit 0 = the test may start; 1 = stop and report."""
import json
import sys
from collections import defaultdict
from pathlib import Path

ESTIMATE_USD = 15.0
ARMS, PILOT_QUESTIONS = 5, 30
run = Path(sys.argv[1])
by_arm = defaultdict(list)
for f in run.glob("*/*.json"):
    by_arm[f.parent.name].append(json.loads(f.read_text()))
ok, report, total = True, {}, 0.0
for arm, rs in sorted(by_arm.items()):
    parsed = sum(r["parsed"]["ok"] for r in rs) / len(rs)
    capped = sum(bool(r.get("hit_turn_cap")) for r in rs) / len(rs)
    cost = sum(r["claude"].get("total_cost_usd") or 0 for r in rs)
    total += cost
    report[arm] = {"n": len(rs), "parsed": round(parsed, 3), "turn_cap": round(capped, 3), "usd": round(cost, 2)}
    ok &= parsed >= 0.9 and capped <= 0.2
report["total_usd"] = round(total, 2)
ok &= total < 2 * ESTIMATE_USD and len(by_arm) == ARMS and all(len(v) == PILOT_QUESTIONS for v in by_arm.values())
report["test_may_start"] = bool(ok)
(run / "gate.json").write_text(json.dumps(report, indent=1))
print(json.dumps(report, indent=1))
sys.exit(0 if ok else 1)

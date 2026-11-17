#!/usr/bin/env python3
"""Score recorded runs exactly as experiment/PROTOCOL.md defines. Deterministic: no judge model.

    .venv/bin/python -m experiment.score runs/pilot --split dev [--arms A,B]
Writes <run>/score.json and prints a summary.
"""
import argparse
import json
import math
import random
import statistics
from pathlib import Path

from experiment.common import load_jsonl

ROOT = Path(__file__).resolve().parents[1]
SEED = 7
BOOT = 10_000


def wilson(k: int, n: int, z: float = 1.96):
    if n == 0:
        return (None, None)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round(c - h, 3), round(c + h, 3))


def score_one(q: dict, r: dict) -> dict:
    p = r["parsed"]
    shown = set(r["shown"])
    cited = p["matches"]
    valid = [m for m in cited if m in shown]          # the citation rule
    answer = set(q["answer"])
    turns = r["claude"].get("num_turns")
    usage = r["claude"].get("usage") or {}
    s = {"qid": q["qid"], "kind": q["kind"], "stratum": q["stratum"], "month": q["created_at"][:7],
         "parsed_ok": p["ok"], "cited": cited, "cited_unseen": [m for m in cited if m not in shown],
         "wall": r["wall_seconds"], "retrieval": r["retrieval_seconds"],
         "model_share": r["wall_seconds"] - r["retrieval_seconds"],
         "turns": turns, "tool_calls": len(r["tool_calls"]),
         "in_tokens": (usage.get("input_tokens") or 0) + (usage.get("cache_read_input_tokens") or 0)
         + (usage.get("cache_creation_input_tokens") or 0),
         "out_tokens": usage.get("output_tokens") or 0, "cost_usd": r["claude"].get("total_cost_usd") or 0,
         "error": bool(r["claude"].get("is_error")), "turn_cap": bool(r.get("hit_turn_cap"))}
    if q["kind"] == "seen":
        s["hit1"] = int(bool(valid) and cited[0] in shown and cited[0] in answer)
        s["hit3"] = int(any(m in answer for m in valid))
        s["answer_was_shown"] = int(bool(answer & shown))
    else:
        s["false_match"] = int(bool(p["seen_before"]) or bool(cited))
    return s


def summarize(rows: list) -> dict:
    seen = [r for r in rows if r["kind"] == "seen"]
    new = [r for r in rows if r["kind"] == "new"]

    def acc(rs, key):
        k, n = sum(r[key] for r in rs), len(rs)
        return {"k": k, "n": n, "rate": round(k / n, 3) if n else None, "ci95": wilson(k, n)}

    def dist(vals):
        vals = sorted(v for v in vals if v is not None)
        if not vals:
            return None
        return {"median": round(statistics.median(vals), 2), "p90": round(vals[int(0.9 * (len(vals) - 1))], 2),
                "mean": round(statistics.mean(vals), 2)}
    out = {"seen": {"hit1": acc(seen, "hit1"), "hit3": acc(seen, "hit3"),
                    "answer_was_shown": acc(seen, "answer_was_shown"),
                    "by_stratum": {st: {"hit1": acc([r for r in seen if r["stratum"] == st], "hit1"),
                                        "hit3": acc([r for r in seen if r["stratum"] == st], "hit3")}
                                   for st in ("exact_token", "plain_language")},
                    "by_month": {m: acc([r for r in seen if r["month"] == m], "hit1")
                                 for m in sorted({r["month"] for r in seen})}},
           "new": {"false_match": acc(new, "false_match")} if new else None,
           "parse_failures": sum(1 for r in rows if not r["parsed_ok"]),
           "errors": sum(1 for r in rows if r["error"]),
           "turn_cap": sum(1 for r in rows if r["turn_cap"]),
           "citations_from_memory": sum(len(r["cited_unseen"]) for r in rows),
           "time": {k: dist([r[k] for r in rows]) for k in ("wall", "retrieval", "model_share")},
           "turns": dist([r["turns"] for r in rows]), "tool_calls": dist([r["tool_calls"] for r in rows]),
           "tokens": {"in": dist([r["in_tokens"] for r in rows]), "out": dist([r["out_tokens"] for r in rows])},
           "cost_usd_total": round(sum(r["cost_usd"] for r in rows), 2)}
    return out


def paired(a_rows: dict, b_rows: dict, key: str = "hit1") -> dict:
    """B minus A on the questions both arms answered; 95% paired bootstrap interval and the protocol's verdict."""
    common = sorted(set(a_rows) & set(b_rows))
    common = [q for q in common if key in a_rows[q]]
    if not common:
        return {}
    d = [b_rows[q][key] - a_rows[q][key] for q in common]
    rng = random.Random(SEED)
    boots = sorted(statistics.mean(rng.choices(d, k=len(d))) for _ in range(BOOT))
    lo, hi = boots[int(0.025 * BOOT)], boots[int(0.975 * BOOT) - 1]
    verdict = "HOLDS" if lo > 0 else "FAILS" if hi < 0 else "NOT SHOWN"
    return {"n": len(common), "diff": round(statistics.mean(d), 3), "ci95": (round(lo, 3), round(hi, 3)),
            "b_only": sum(1 for x in d if x > 0), "a_only": sum(1 for x in d if x < 0), "verdict": verdict}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("--split", required=True)
    ap.add_argument("--arms", default="A,B,C,D,F")
    a = ap.parse_args()
    run = ROOT / a.run
    qs = {q["qid"]: q for q in load_jsonl(ROOT / "data" / "built" / "questions.jsonl") if q["split"] == a.split}
    per_arm, result = {}, {"split": a.split, "arms": {}}
    for arm in a.arms.split(","):
        d = run / arm
        if not d.exists():
            continue
        rows = {}
        for f in sorted(d.glob("*.json")):
            r = json.loads(f.read_text())
            if r["qid"] in qs:
                rows[r["qid"]] = score_one(qs[r["qid"]], r)
        per_arm[arm] = rows
        result["arms"][arm] = summarize(list(rows.values()))
        (d / "scored.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows.values()))
    # Primary B - A, then the secondary comparisons named in PROTOCOL.md amendment 1.
    for x, y in (("A", "B"), ("B", "D"), ("A", "D"), ("A", "F"), ("A", "C"), ("B", "C")):
        if x in per_arm and y in per_arm:
            result[f"{y}_minus_{x}"] = {"hit1": paired(per_arm[x], per_arm[y], "hit1"),
                                         "hit3": paired(per_arm[x], per_arm[y], "hit3")}
    edits = ROOT / "data" / "built" / "answer_edits.json"
    if edits.exists() and "A" in per_arm and "B" in per_arm:  # the edit leak, PROTOCOL.md "Corpus"
        drop = set(json.loads(edits.read_text())["questions_touching_them"])
        keep = lambda rows: {q: r for q, r in rows.items() if q not in drop}
        result["B_minus_A_without_edited_answers"] = {"dropped": len(drop & set(per_arm["A"])),
                                                       "hit1": paired(keep(per_arm["A"]), keep(per_arm["B"]), "hit1")}
    (run / f"score-{a.split}.json").write_text(json.dumps(result, indent=1))
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()

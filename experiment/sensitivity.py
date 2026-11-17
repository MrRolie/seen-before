#!/usr/bin/env python3
"""Sensitivity of the primary result, beside (never replacing) the verdict PROTOCOL.md fixes.

    .venv/bin/python -m experiment.sensitivity runs/test

1. Questions retitled or edited by someone other than their author dropped (data/built/question_edits.json).
2. Citations the parser dropped: ticket ids in the raw answer that are not in the strict `Repo#N` form.
3. Resampling by cluster instead of by question (the 124 questions come from 101 clusters).
4. A narrower answer set: the question's own linked original and the original's direct duplicates, instead of
   the whole cluster (link chains over many links can join clusters).
5. Questions where a bot account named an answer ticket before the maintainer linked the question dropped
   (PowerToys' AI triage bot posts "Possible duplicates" from 2026-08-15; found by the fresh recount; amendment 2
   had said no similarity bot names targets).
"""
import json
import random
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

from experiment.common import load_jsonl, parse_answer
from experiment.score import BOOT, SEED, paired

ROOT = Path(__file__).resolve().parents[1]
B_ = ROOT / "data" / "built"
LOOSE = re.compile(r"(?:microsoft/)?(powertoys|terminal|wsl)\s*#\s*(\d+)", re.IGNORECASE)


def main() -> None:
    run = ROOT / sys.argv[1]
    qs = {q["qid"]: q for q in load_jsonl(B_ / "questions.jsonl") if q["split"] == "test"}
    raw = {a: {json.loads(f.read_text())["qid"]: json.loads(f.read_text()) for f in (run / a).glob("*.json")}
           for a in "ABCDF"}
    scored = {a: {json.loads(l)["qid"]: json.loads(l) for l in (run / a / "scored.jsonl").read_text().splitlines()}
              for a in "ABCDF"}
    out = {}

    # 1. question edits
    edits = json.loads((B_ / "question_edits.json").read_text())
    drop = set(edits["changed_by_someone_else"])
    keep = lambda rows: {q: r for q, r in rows.items() if q not in drop}
    out["1_without_questions_changed_by_others"] = {
        "dropped_seen": len([q for q in drop if q in scored["A"] and "hit1" in scored["A"][q]]),
        "B_minus_A_hit1": paired(keep(scored["A"]), keep(scored["B"]), "hit1")}

    # 2. dropped citations
    dropped = {}
    for a, rs in raw.items():
        n = 0
        examples = []
        for q, r in rs.items():
            text = r["claude"].get("result", "") or ""
            strict = set(parse_answer(text)["matches"])
            m = re.search(r'"matches"\s*:\s*\[(.*?)\]', text, re.DOTALL)
            loose = {f"{ {'powertoys': 'PowerToys', 'terminal': 'terminal', 'wsl': 'WSL'}[x.lower()]}#{y}"
                     for x, y in LOOSE.findall(m.group(1))} if m else set()
            lost = loose - strict
            if lost:
                n += 1
                examples.append((q, sorted(lost)))
        dropped[a] = {"answers_with_a_dropped_citation": n, "examples": examples[:5]}
    out["2_dropped_citations"] = dropped
    out["2_parse_failures"] = {a: [{"qid": q, "subtype": r["claude"].get("subtype"), "turns": r["claude"].get("num_turns"),
                                    "result_tail": (r["claude"].get("result") or "")[-300:]}
                                   for q, r in rs.items() if not r["parsed"]["ok"]] for a, rs in raw.items()}

    # 3. cluster bootstrap
    seen = [q for q in scored["A"] if "hit1" in scored["A"][q]]
    by_cl = defaultdict(list)
    for q in seen:
        by_cl[qs[q]["cluster"]].append(scored["B"][q]["hit1"] - scored["A"][q]["hit1"])
    cls = sorted(by_cl)
    rng = random.Random(SEED)
    boots = []
    for _ in range(BOOT):
        pick = rng.choices(cls, k=len(cls))
        d = [x for c in pick for x in by_cl[c]]
        boots.append(statistics.mean(d))
    boots.sort()
    out["3_cluster_bootstrap_B_minus_A_hit1"] = {"clusters": len(cls), "ci95": (round(boots[int(.025 * BOOT)], 3),
                                                                            round(boots[int(.975 * BOOT) - 1], 3))}

    # 4. direct-link answer set
    links = load_jsonl(B_ / "links.jsonl")
    target = {l["dup"]: l["canonical"] for l in links}
    dups_of = defaultdict(set)
    for l in links:
        dups_of[l["canonical"]].add(l["dup"])
    corpus_ids = set(json.loads((ROOT / "data" / "index" / "ids.json").read_text()))
    narrow, sizes = {}, []
    for q in seen:
        t = target.get(q)
        s = ({t} | dups_of.get(t, set())) & corpus_ids if t else set()
        s.discard(q)
        narrow[q] = s
        sizes.append(len(qs[q]["answer"]))
    out["4_answer_set_sizes"] = {"cluster_median": statistics.median(sizes), "cluster_max": max(sizes),
                                 "narrow_median": statistics.median(len(v) for v in narrow.values()),
                                 "questions_with_empty_narrow_set": sum(not v for v in narrow.values())}
    res = {}
    for a in "ABCDF":
        rows = {}
        for q in seen:
            if not narrow[q]:
                continue
            r = raw[a][q]
            cited, shown = r["parsed"]["matches"], set(r["shown"])
            rows[q] = {"hit1": int(bool(cited) and cited[0] in shown and cited[0] in narrow[q])}
        res[a] = rows
    out["4_direct_link_scoring"] = {
        "n": len(res["A"]), "hit1": {a: sum(r["hit1"] for r in rows.values()) for a, rows in res.items()},
        "B_minus_A": paired(res["A"], res["B"], "hit1")}
    # 5. bot-suggested questions
    linked_at = {l["dup"]: l["linked_at"] for l in links}
    raw_dir = {"PowerToys": "microsoft__PowerToys", "terminal": "microsoft__terminal", "WSL": "microsoft__WSL"}
    by_issue = defaultdict(list)
    for repo, d in raw_dir.items():
        for c in load_jsonl(ROOT / "data" / "raw" / d / "comments.jsonl"):
            if c.get("user_type") == "Bot":
                by_issue[f"{repo}#{c['issue']}"].append(c)
    botted = set()
    for q in seen:
        repo = q.split("#")[0]
        nums = {t.split("#")[1] for t in qs[q]["answer"] if t.startswith(repo + "#")}
        for c in by_issue.get(q, []):
            if c["created_at"] <= linked_at.get(q, "9999") and \
                    nums & set(re.findall(r"(?:#|/issues/)(\d+)", c["body"])):
                botted.add(q)
    keepb = lambda rows: {q: r for q, r in rows.items() if q not in botted}
    out["5_without_bot_suggested_questions"] = {
        "dropped": len(botted), "repos": sorted({q.split("#")[0] for q in botted}),
        "hit1": {a: sum(r["hit1"] for q, r in scored[a].items() if q in seen and q not in botted) for a in "ABCDF"},
        "hit1_on_dropped": {a: sum(scored[a][q]["hit1"] for q in botted) for a in "ABCDF"},
        "B_minus_A_hit1": paired(keepb(scored["A"]), keepb(scored["B"]), "hit1")}
    (run / "sensitivity.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Retrieval-only diagnostics, no AI: how well each retrieval stage ranks the answer, per question.

    prlimit --as=64000000000 .venv/bin/python -m experiment.diagnostics --split dev

Stages: BM25 (SQLite FTS5, as arm F uses it), the untrained base embedding, the trained embedding, trained +
re-ranker (arm A's retrieval), BM25 and trained fused + re-ranker (arm F's retrieval).
Also the constant guess (always the largest training cluster). recall@k = an answer ticket is in the top k.
These show what training and re-ranking each buy; they are not an answering arm.
"""
import argparse
import json
import re
import time
from collections import Counter
from pathlib import Path

from experiment.common import load_jsonl
from experiment.keyword import Keyword
from experiment.pipeline import config as C
from experiment.pipeline.retrieve import Retriever

ROOT = Path(__file__).resolve().parents[1]
KS = (1, 5, 10, 50)
TOKEN = re.compile(r"[a-z0-9_]+(?:[.:/][a-z0-9_]+)*")


def tok(s: str):
    return TOKEN.findall(s.lower())


def recall(ranked, answer):
    return {f"r@{k}": int(bool(set(ranked[:k]) & answer)) for k in KS}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", required=True)
    a = ap.parse_args()
    qs = [q for q in load_jsonl(C.BUILT / "questions.jsonl") if q["split"] == a.split and q["kind"] == "seen"]
    docs = load_jsonl(C.BUILT / "corpus.jsonl")
    ids = [d["id"] for d in docs]
    pairs = load_jsonl(C.BUILT / "train_pairs.jsonl")
    biggest = Counter(p["canonical"] for p in pairs).most_common(1)[0][0]

    rows = {q["qid"]: {"stratum": q["stratum"]} for q in qs}
    t0 = time.time()
    kw = Keyword()
    for q in qs:
        ranked = [t for t, _ in kw.search(C.embed_text(q["title"], q["body"]), max(KS))]
        rows[q["qid"]]["bm25"] = recall(ranked, set(q["answer"]))
        rows[q["qid"]]["constant"] = recall([biggest], set(q["answer"]))
    timing = {"bm25_total": round(time.time() - t0, 1)}

    for which in ("base", "trained"):
        r = Retriever(which=which, rerank=(which == "trained"))
        for q in qs:
            text = C.embed_text(q["title"], q["body"])
            ranked = [t for t, _ in r.candidates(text, max(KS))]
            rows[q["qid"]][which] = recall(ranked, set(q["answer"]))
            if which == "trained":
                t1 = time.perf_counter()
                rr = [t for t, _ in r.search(text, k=max(KS))]
                rows[q["qid"]].setdefault("rerank_seconds", time.perf_counter() - t1)
                rows[q["qid"]]["trained+rerank"] = recall(rr, set(q["answer"]))
                rows[q["qid"]]["hybrid+rerank"] = recall([t for t, _ in r.search(text, k=max(KS), mode="hybrid")],
                                                         set(q["answer"]))
        del r

    stages = ("constant", "bm25", "base", "trained", "trained+rerank", "hybrid+rerank")
    summary = {}
    for st in stages:
        summary[st] = {}
        for k in KS:
            key = f"r@{k}"
            vals = [rows[q][st][key] for q in rows]
            summary[st][key] = round(sum(vals) / len(vals), 3)
            for stratum in ("exact_token", "plain_language"):
                sv = [rows[q][st][key] for q in rows if rows[q]["stratum"] == stratum]
                summary[st][f"{key}_{stratum}"] = round(sum(sv) / len(sv), 3) if sv else None
    out = {"split": a.split, "n": len(qs), "constant_guess": biggest, "timing": timing, "summary": summary}
    (ROOT / "runs").mkdir(exist_ok=True)
    (ROOT / "runs" / f"diagnostics-{a.split}.json").write_text(json.dumps({**out, "rows": rows}, indent=1))
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()

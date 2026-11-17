#!/usr/bin/env python3
"""Freshness, measured: how long from a new ticket arriving to it being searchable, per approach. No AI.

    prlimit --as=64000000000 .venv/bin/python -m experiment.freshness

Uses real tickets created after the cutoff (the question window), in arrival order, into scratch copies:
  ripgrep (B):        write one markdown file.
  embedding (A, C):   embed one ticket with the trained model and append it to the matrix.
  keyword (F):        insert one row into the FTS5 index.
The trained model itself does not learn the new ticket; retraining costs what train_record.json says.
"""
import json
import shutil
import sqlite3
import statistics
import tempfile
import time
from pathlib import Path

import numpy as np
import torch
from sentence_transformers import SentenceTransformer

from corpus.build import render
from experiment.common import load_jsonl
from experiment.pipeline import config as C

ROOT = Path(__file__).resolve().parents[1]
N = 50


def med(xs):
    return {"median_ms": round(1000 * statistics.median(xs), 2), "max_ms": round(1000 * max(xs), 2)}


def main() -> None:
    qs = sorted(load_jsonl(C.BUILT / "questions.jsonl"), key=lambda q: q["created_at"])[:N]
    docs = [{"id": q["qid"], "repo": q["repo"], "number": int(q["qid"].split("#")[1]), "title": q["title"],
             "body": q["body"] or "", "created_at": q["created_at"]} for q in qs]
    out = {}
    with tempfile.TemporaryDirectory() as td:
        t = []
        for d in docs:
            t0 = time.perf_counter()
            p = Path(td) / d["repo"] / f"{d['number']}.md"
            p.parent.mkdir(exist_ok=True)
            p.write_text(render(d, []))
            t.append(time.perf_counter() - t0)
        out["ripgrep_write_file"] = med(t)

        t0 = time.perf_counter()  # B's whole build: one file per ticket in the corpus
        for d in load_jsonl(C.BUILT / "corpus.jsonl"):
            p = Path(td) / "all" / d["repo"] / f"{d['number']}.md"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(d["text"])
        out["ripgrep_build_all_files_seconds"] = round(time.perf_counter() - t0, 1)

        db = Path(td) / "fts.sqlite"
        shutil.copy(C.INDEX / "fts.sqlite", db)
        con = sqlite3.connect(db)
        t = []
        for d in docs:
            t0 = time.perf_counter()
            con.execute("INSERT INTO t VALUES (?, ?, ?)", (d["id"], d["title"], render(d, [])))
            con.commit()
            t.append(time.perf_counter() - t0)
        out["keyword_fts_insert"] = med(t)

    m = SentenceTransformer(str(C.TRAINED), device="cuda")
    m.max_seq_length = C.MAX_SEQ
    emb = torch.from_numpy(np.load(C.INDEX / "emb-trained.npy")).cuda()
    m.encode(["warm up"])
    t = []
    for d in docs:
        t0 = time.perf_counter()
        with torch.autocast("cuda", dtype=torch.bfloat16):
            v = m.encode([C.embed_text(d["title"], d["body"])], normalize_embeddings=True, convert_to_tensor=True)
        emb = torch.cat([emb, v.float()])
        torch.cuda.synchronize()
        t.append(time.perf_counter() - t0)
    out["embedding_add_one"] = med(t)
    out["retrain"] = json.loads((C.MODELS / "train_record.json").read_text())
    out["build_from_scratch"] = {
        "embedding_index_trained": json.loads((C.INDEX / "index-trained.json").read_text()),
        "keyword_index": json.loads((C.INDEX / "keyword-build.json").read_text())
        if (C.INDEX / "keyword-build.json").exists() else None}
    (ROOT / "runs").mkdir(exist_ok=True)
    (ROOT / "runs" / "freshness.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()

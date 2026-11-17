#!/usr/bin/env python3
"""Arm A, step 1b: train the re-ranker on the past tickets' duplicate pairs, then choose the re-ranking stage on
the held-out training pairs.

    prlimit --as=80000000000 .venv/bin/python -m experiment.pipeline.train_reranker [--eval-only]

Why: the off-the-shelf re-ranker scores topical relevance, not "same problem", and on dev it pushed the duplicate
out of the top 5 (PROTOCOL.md amendment 4). A competent build trains it on the tickets too.
Training: a cross-encoder (bge-reranker-base) with binary cross-entropy on (new ticket, its original, 1) and
(new ticket, a hard negative, 0), the negatives drawn from the trained embedding's top 30 outside the pair's cluster:
exactly the candidates the re-ranker must sort at answer time. Only pre-cutoff training pairs are used.
Choice: on the 5% held-out pairs (val_pairs.jsonl, never trained on), the stage with the best recall@1 among
{no re-ranker, off-the-shelf, trained} becomes arm A's (and F's) re-ranker; recorded in reranker_choice.json.
"""
import json
import random
import sys
import time

import numpy as np
import torch
from datasets import Dataset
from sentence_transformers import SentenceTransformer
from sentence_transformers.cross_encoder import CrossEncoder, CrossEncoderTrainer, CrossEncoderTrainingArguments
from sentence_transformers.cross_encoder.losses import BinaryCrossEntropyLoss

from experiment.common import load_jsonl
from experiment.pipeline import config as C
from experiment.pipeline.clean import clean
from experiment.pipeline.train import clusters_of

NEG_PER_PAIR = 3


def dense_top(model, emb, ids, texts, k):
    with torch.autocast("cuda", dtype=torch.bfloat16):
        q = model.encode(texts, batch_size=64, normalize_embeddings=True, convert_to_numpy=True).astype(np.float32)
    return np.argsort(-(q @ emb.T), axis=1)[:, :k]


def evaluate(rerankers: dict, model, emb, ids, docs, corpus, val, cl) -> dict:
    """rerankers: name -> (cross-encoder or None, view). The dense stage always sees the raw ticket."""
    top = dense_top(model, emb, ids, [docs[p["dup"]] for p in val], C.CANDIDATES + 1)
    res = {}
    for name, (rr, view) in rerankers.items():
        r1 = r5 = 0
        for p, cand in zip(val, top):
            cands = [ids[j] for j in cand if ids[j] != p["dup"]][:C.CANDIDATES]
            if rr is not None:
                q = corpus[p["dup"]]
                s = rr.predict([(C.rerank_text(q["title"], q["body"], view),
                                 C.rerank_text(corpus[t]["title"], corpus[t]["body"], view)) for t in cands],
                               batch_size=50, show_progress_bar=False)
                cands = [cands[j] for j in np.argsort(-np.asarray(s))]
            good = {x for x in cl if cl[x] == cl[p["dup"]]} - {p["dup"]}
            r1 += cands[0] in good
            r5 += bool(set(cands[:5]) & good)
        res[name] = {"r@1": round(r1 / len(val), 3), "r@5": round(r5 / len(val), 3), "n": len(val), "view": view}
        print(name, res[name], flush=True)
    return res


def main() -> None:
    eval_only = "--eval-only" in sys.argv
    rng = random.Random(C.SEED)
    t0 = time.time()
    docs = {d["id"]: C.embed_text(d["title"], d["body"]) for d in load_jsonl(C.BUILT / "corpus.jsonl")}
    pairs = load_jsonl(C.BUILT / "train_pairs.jsonl")
    cl = clusters_of(pairs)
    val = load_jsonl(C.MODELS / "val_pairs.jsonl")
    val_keys = {(p["dup"], p["canonical"]) for p in val}
    corpus = {d["id"]: d for d in load_jsonl(C.BUILT / "corpus.jsonl")}
    rtext = {k: C.embed_text(d["title"], clean(d["body"])) for k, d in corpus.items()}  # the re-ranker's view
    ids = json.loads((C.INDEX / "ids.json").read_text())
    emb = np.load(C.INDEX / "emb-trained.npy")
    model = SentenceTransformer(str(C.TRAINED), device="cuda")
    model.max_seq_length = C.MAX_SEQ

    if not eval_only:
        train = [p for p in pairs if (p["dup"], p["canonical"]) not in val_keys]  # the embedding's exact split
        by_canon = {}
        for p in sorted(train, key=lambda p: p["dup"]):
            by_canon.setdefault(p["canonical"], []).append(p)
        capped = []
        for c in sorted(by_canon):
            ps = by_canon[c]
            rng.shuffle(ps)
            capped += ps[:C.PAIRS_PER_CANONICAL]
        top = dense_top(model, emb, ids, [docs[p["dup"]] for p in capped], 30)
        rows = {"query": [], "passage": [], "label": []}
        for p, cand in zip(capped, top):
            rows["query"].append(rtext[p["dup"]]); rows["passage"].append(rtext[p["canonical"]]); rows["label"].append(1.0)
            negs = [ids[j] for j in cand[5:] if cl.get(ids[j], ids[j]) != cl[p["dup"]] and ids[j] != p["dup"]]
            for n in rng.sample(negs, min(NEG_PER_PAIR, len(negs))):
                rows["query"].append(rtext[p["dup"]]); rows["passage"].append(rtext[n]); rows["label"].append(0.0)
        ce = CrossEncoder(C.RERANKER_BASE_FOR_TRAINING, num_labels=1, max_length=C.MAX_SEQ, device="cuda")
        args = CrossEncoderTrainingArguments(
            output_dir=str(C.MODELS / "ce-checkpoints"), num_train_epochs=1, per_device_train_batch_size=16,
            learning_rate=2e-5, warmup_ratio=0.1, bf16=True, logging_steps=50, save_strategy="no",
            report_to="none", seed=C.SEED)
        t1 = time.time()
        trainer = CrossEncoderTrainer(model=ce, args=args, train_dataset=Dataset.from_dict(rows),
                                      loss=BinaryCrossEntropyLoss(ce))
        trainer.train()
        losses = [h["loss"] for h in trainer.state.log_history if "loss" in h]
        final_loss = float(np.mean(losses[-5:]))
        ce.save(str(C.TRAINED_RERANKER))
        (C.MODELS / "reranker_train_record.json").write_text(json.dumps({
            "attempt": 2, "pairs": len(capped), "rows": len(rows["label"]), "final_loss": round(final_loss, 4),
            "trained_at_all": final_loss < 0.45, "train_seconds": round(time.time() - t1, 1),
            "total_seconds": round(time.time() - t0, 1)}, indent=1))
        print("final loss", final_loss, flush=True)

    shelf = CrossEncoder(C.RERANKER, device="cuda", max_length=C.MAX_SEQ, model_kwargs={"torch_dtype": torch.bfloat16})
    rerankers = {"none": (None, None), "off_the_shelf_raw": (shelf, "raw"), "off_the_shelf_title": (shelf, "title"),
                 "off_the_shelf_clean": (shelf, "clean")}
    record = json.loads((C.MODELS / "reranker_train_record.json").read_text())
    if record.get("trained_at_all"):
        rerankers["trained_clean"] = (CrossEncoder(str(C.TRAINED_RERANKER), device="cuda", max_length=C.MAX_SEQ),
                                      "clean")
    res = evaluate(rerankers, model, emb, ids, docs, corpus, val, cl)
    best = max(res, key=lambda k: (res[k]["r@1"], res[k]["r@5"]))
    (C.MODELS / "reranker_choice.json").write_text(json.dumps({
        "amendment": 5, "val": res, "trained_record": record, "chosen": best, "view": res[best]["view"]}, indent=1))
    print("chosen:", best)


if __name__ == "__main__":
    main()

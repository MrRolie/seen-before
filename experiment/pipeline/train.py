#!/usr/bin/env python3
"""Arm A, step 1: train the embedding model on the past tickets' duplicate pairs.

    prlimit --as=64000000000 .venv/bin/python -m experiment.pipeline.train

Contrastive training (MultipleNegativesRankingLoss, cached: GradCache, so a batch of 64 fits in memory): each duplicate ticket is pulled toward the earlier ticket it
duplicates, and pushed from the rest of the batch and from one mined hard negative (a ticket the untrained model
ranks close but that is in another cluster). Weights in fp32, compute in bf16 (the GPU is slow in fp32).
Only pairs whose two tickets and whose link all predate the cutoff are used (corpus/build.py).
"""
import json
import random
import time
from collections import defaultdict

import numpy as np
import torch
from datasets import Dataset
from sentence_transformers import SentenceTransformer, SentenceTransformerTrainer, SentenceTransformerTrainingArguments
from sentence_transformers.losses import CachedMultipleNegativesRankingLoss
from sentence_transformers.training_args import BatchSamplers

from experiment.common import load_jsonl
from experiment.pipeline import config as C


def clusters_of(pairs):
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for p in pairs:
        parent[find(p["dup"])] = find(p["canonical"])
    return {x: find(x) for x in parent}


def main() -> None:
    t_start = time.time()
    rng = random.Random(C.SEED)
    docs = {d["id"]: C.embed_text(d["title"], d["body"]) for d in load_jsonl(C.BUILT / "corpus.jsonl")}
    pairs = load_jsonl(C.BUILT / "train_pairs.jsonl")
    cl = clusters_of(pairs)

    # Cap pairs per canonical so one mega-cluster cannot dominate training.
    by_canon = defaultdict(list)
    for p in pairs:
        by_canon[p["canonical"]].append(p)
    capped = []
    for c in sorted(by_canon):
        ps = sorted(by_canon[c], key=lambda p: p["dup"])
        rng.shuffle(ps)
        capped += ps[:C.PAIRS_PER_CANONICAL]
    rng.shuffle(capped)
    n_val = max(1, len(capped) // 20)
    val, train = capped[:n_val], capped[n_val:]
    (C.MODELS).mkdir(parents=True, exist_ok=True)
    (C.MODELS / "val_pairs.jsonl").write_text("".join(json.dumps(p) + "\n" for p in val))

    # Mine one hard negative per pair with the untrained model: ranks 5-30, outside the pair's cluster.
    model = SentenceTransformer(C.BASE_MODEL, device="cuda")
    model.max_seq_length = C.MAX_SEQ
    ids = list(docs)  # corpus.jsonl order, the index's order
    base_cache = C.INDEX / "emb-base.npy"
    if base_cache.exists() and json.loads((C.INDEX / "ids.json").read_text()) == ids:
        emb = np.load(base_cache)
    else:
      with torch.autocast("cuda", dtype=torch.bfloat16):
        emb = model.encode([docs[i] for i in ids], batch_size=64, normalize_embeddings=True,
                           convert_to_numpy=True, show_progress_bar=True).astype(np.float32)
    # The same pass is the untrained base model's index for the diagnostics: keep it.
    C.INDEX.mkdir(parents=True, exist_ok=True)
    np.save(C.INDEX / "emb-base.npy", emb)
    (C.INDEX / "ids.json").write_text(json.dumps(ids))
    if not (C.INDEX / "index-base.json").exists():
      (C.INDEX / "index-base.json").write_text(json.dumps({"model": "base", "docs": len(ids),
                                                         "seconds": round(time.time() - t_start, 1)}))
    pos = {i: n for n, i in enumerate(ids)}
    q = emb[[pos[p["dup"]] for p in train]]
    sims = q @ emb.T
    top = np.argsort(-sims, axis=1)[:, :30]
    rows = {"anchor": [], "positive": [], "negative": []}
    for p, cand in zip(train, top):
        negs = [ids[j] for j in cand[5:] if cl.get(ids[j], ids[j]) != cl[p["dup"]] and ids[j] != p["dup"]]
        if not negs:
            continue
        rows["anchor"].append(docs[p["dup"]])
        rows["positive"].append(docs[p["canonical"]])
        rows["negative"].append(docs[rng.choice(negs)])
    t_mined = time.time()

    args = SentenceTransformerTrainingArguments(
        output_dir=str(C.MODELS / "checkpoints"), num_train_epochs=1, per_device_train_batch_size=64,
        learning_rate=2e-5, warmup_ratio=0.1, bf16=True, batch_sampler=BatchSamplers.NO_DUPLICATES,
        logging_steps=20, save_strategy="no", report_to="none", seed=C.SEED)
    trainer = SentenceTransformerTrainer(model=model, args=args, train_dataset=Dataset.from_dict(rows),
                                         loss=CachedMultipleNegativesRankingLoss(model, mini_batch_size=8))
    trainer.train()
    model.save(str(C.TRAINED))
    t_end = time.time()
    rec = {"train_pairs_total": len(pairs), "after_cap": len(capped), "val": len(val),
           "train_rows": len(rows["anchor"]), "mine_seconds": round(t_mined - t_start, 1),
           "train_seconds": round(t_end - t_mined, 1), "total_seconds": round(t_end - t_start, 1)}
    (C.MODELS / "train_record.json").write_text(json.dumps(rec, indent=1))
    print(rec)


if __name__ == "__main__":
    main()

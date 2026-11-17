#!/usr/bin/env python3
"""Arm A, steps 2 and 3: embed the corpus once, then retrieve and re-rank per question.

    prlimit --as=64000000000 .venv/bin/python -m experiment.pipeline.retrieve index [--model base|trained]

`Retriever.search` is the whole retrieval path an answer pays for: embed the query, cosine similarity over the
corpus, re-rank the top CANDIDATES with the cross-encoder, return the best k.
"""
import json
import sys
import time

import numpy as np
import torch
from sentence_transformers import CrossEncoder, SentenceTransformer

from experiment.common import load_jsonl
from experiment.pipeline import config as C


def model_path(which: str) -> str:
    return str(C.TRAINED) if which == "trained" else C.BASE_MODEL


def build_index(which: str) -> None:
    t0 = time.time()
    docs = load_jsonl(C.BUILT / "corpus.jsonl")
    m = SentenceTransformer(model_path(which), device="cuda")
    m.max_seq_length = C.MAX_SEQ
    with torch.autocast("cuda", dtype=torch.bfloat16):
        emb = m.encode([C.embed_text(d["title"], d["body"]) for d in docs], batch_size=64,
                       normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=True)
    C.INDEX.mkdir(parents=True, exist_ok=True)
    np.save(C.INDEX / f"emb-{which}.npy", emb.astype(np.float32))
    (C.INDEX / "ids.json").write_text(json.dumps([d["id"] for d in docs]))
    rec = {"model": which, "docs": len(docs), "seconds": round(time.time() - t0, 1)}
    (C.INDEX / f"index-{which}.json").write_text(json.dumps(rec))
    print(rec)


def load_reranker():
    """The re-ranking stage chosen on the held-out training pairs (train_reranker.py): (model or None, view)."""
    path = C.MODELS / "reranker_choice.json"
    if not path.exists():  # train_reranker.py not run: the published runs used no re-ranker (amendment 5)
        return None, None
    choice = json.loads(path.read_text())
    name, view = choice["chosen"], choice.get("view")
    if name == "none":
        return None, None
    if name.startswith("trained"):
        return CrossEncoder(str(C.TRAINED_RERANKER), device="cuda", max_length=C.MAX_SEQ), view
    return CrossEncoder(C.RERANKER, device="cuda", max_length=C.MAX_SEQ,
                        model_kwargs={"torch_dtype": torch.bfloat16}), view


class Retriever:
    def __init__(self, which: str = "trained", rerank: bool = True):
        self.ids = json.loads((C.INDEX / "ids.json").read_text())
        self.emb = torch.from_numpy(np.load(C.INDEX / f"emb-{which}.npy")).cuda()
        self.model = SentenceTransformer(model_path(which), device="cuda")
        self.model.max_seq_length = C.MAX_SEQ
        self.reranker, self.view = load_reranker() if rerank else (None, None)
        self.docs = {d["id"]: C.rerank_text(d["title"], d["body"], self.view)
                     for d in load_jsonl(C.BUILT / "corpus.jsonl")} if self.reranker else {}
        self.keyword = None

    @classmethod
    def load(cls, **kw):
        return cls(**kw)

    def candidates(self, text: str, k: int, repo: str | None = None):
        with torch.autocast("cuda", dtype=torch.bfloat16):
            q = self.model.encode([text[:C.EMBED_CHARS]], normalize_embeddings=True, convert_to_tensor=True)
        sims = (self.emb @ q.float().T).squeeze(1)
        order = torch.argsort(sims, descending=True).tolist()
        out = []
        for j in order:
            tid = self.ids[j]
            if repo and not tid.startswith(repo + "#"):
                continue
            out.append((tid, float(sims[j])))
            if len(out) == k:
                break
        return out

    def hybrid_candidates(self, text: str, k: int, repo: str | None = None):
        """Reciprocal rank fusion of the embedding and the BM25 keyword candidates (the shape of existing
        hybrid ticket-search connectors)."""
        if self.keyword is None:
            from experiment.keyword import Keyword
            self.keyword = Keyword()
        fused = {}
        for ranked in (self.candidates(text, C.CANDIDATES, repo), self.keyword.search(text, C.CANDIDATES, repo)):
            for r, (tid, _) in enumerate(ranked):
                fused[tid] = fused.get(tid, 0.0) + 1.0 / (C.RRF_K + r + 1)
        return sorted(fused.items(), key=lambda x: -x[1])[:k]

    def search(self, text: str, k: int = C.TOP_K, repo: str | None = None, mode: str = "dense"):
        if mode == "hybrid":
            cands = self.hybrid_candidates(text, C.CANDIDATES, repo)
        else:
            cands = self.candidates(text, C.CANDIDATES, repo)
        if self.reranker is None:
            return cands[:k]
        title, _, body = text.partition("\n")
        q = C.rerank_text(title, body.strip(), self.view)
        scores = self.reranker.predict([(q, self.docs[t]) for t, _ in cands], batch_size=50)
        ranked = sorted(zip([t for t, _ in cands], scores.tolist()), key=lambda x: -x[1])
        return ranked[:k]


if __name__ == "__main__":
    if sys.argv[1] == "index":
        build_index(sys.argv[3] if len(sys.argv) > 3 else "trained")

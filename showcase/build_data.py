#!/usr/bin/env python3
"""Build showcase/data.js from the experiment's own files, so every number and every lit ticket in the animation
is the measured one. Nothing here is typed in by hand except which two questions the page walks through.

    prlimit --as=24000000000 -- .venv/bin/python showcase/build_data.py

Needs the built corpus, the trained embedding index and model, and the test run's records.
"""
import base64
import json
import subprocess
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "data" / "built" / "corpus"
RUNS = ROOT / "runs"
REPOS = ["PowerToys", "terminal", "WSL"]
# One question where words found it and meaning did not, and one the other way round (both real test questions).
EXAMPLES = ["terminal#20467", "PowerToys#52029"]


def jl(p):
    return [json.loads(l) for l in Path(p).read_text().splitlines() if l.strip()]


def rec(arm, qid):
    return json.loads((RUNS / "test" / arm / (qid.replace("#", "_") + ".json")).read_text())


def title(tid):
    repo, n = tid.split("#")
    first = (CORPUS / repo / f"{n}.md").open().readline().strip()
    return first.split(": ", 1)[1]


def rg_files(pattern, regex, also, repo):
    """Every ticket the pattern matches, as the agent's tool saw it (case-insensitive; `also` must all match)."""
    where = str(CORPUS / repo) if repo else str(CORPUS)
    lit = [] if regex else ["-F"]
    out = set(subprocess.run(["rg", "-i", "-l", "--no-messages"] + lit + ["-e", pattern, where],
                             capture_output=True, text=True).stdout.split())
    for extra in also or []:
        out &= set(subprocess.run(["rg", "-i", "-l", "--no-messages"] + lit + ["-e", extra, where],
                                  capture_output=True, text=True).stdout.split())
    return {f"{Path(f).parent.name}#{Path(f).stem}" for f in out}


def main():
    ids = json.loads((ROOT / "data" / "index" / "ids.json").read_text())
    emb = np.load(ROOT / "data" / "index" / "emb-trained.npy")
    qs = {q["qid"]: q for q in jl(ROOT / "data" / "built" / "questions.jsonl")}

    import umap
    from sentence_transformers import SentenceTransformer
    from experiment.pipeline import config as C
    reducer = umap.UMAP(n_neighbors=30, min_dist=0.15, metric="cosine", random_state=7, low_memory=True)
    xy = reducer.fit_transform(emb)
    model = SentenceTransformer(str(C.TRAINED), device="cpu")
    qxy = reducer.transform(model.encode([f"{qs[q]['title']}\n\n{qs[q]['body'] or ''}"[:C.EMBED_CHARS]
                                          for q in EXAMPLES], normalize_embeddings=True))
    lo, hi = np.percentile(xy, 0.5, axis=0), np.percentile(xy, 99.5, axis=0)
    norm = lambda p: np.clip((p - lo) / (hi - lo), 0, 1)
    q16 = (norm(xy) * 65535).astype("<u2")

    # The plain wall: tickets in repo order, then by number.
    index = {t: i for i, t in enumerate(ids)}
    wall = sorted(range(len(ids)), key=lambda i: (REPOS.index(ids[i].split("#")[0]), int(ids[i].split("#")[1])))
    wall_pos = np.empty(len(ids), dtype="<u4")
    wall_pos[np.array(wall)] = np.arange(len(ids))
    repo_of = np.array([REPOS.index(t.split("#")[0]) for t in ids], dtype="u1")

    examples = []
    for k, q in enumerate(EXAMPLES):
        a, b = rec("A", q), rec("B", q)
        answer = set(qs[q]["answer"])
        steps = []
        for c in b["tool_calls"]:
            if c["tool"] == "search":
                hit = rg_files(c["args"]["pattern"], c["args"].get("regex"), c["args"].get("also"), c["args"].get("repo"))
                steps.append({"tool": "search", "pattern": c["args"]["pattern"], "also": c["args"].get("also") or [],
                              "matched": sorted(index[t] for t in hit if t in index), "n": len(hit),
                              "returned": [index[t] for t in c["returned"] if t in index],
                              "answer_returned": [t for t in c["returned"] if t in answer]})
            else:
                t = c["args"]["ticket"]
                steps.append({"tool": "read", "ticket": t, "i": index.get(t), "title": title(t), "is_answer": t in answer})
        examples.append({
            "qid": q, "title": qs[q]["title"], "q_xy": [round(float(v), 4) for v in norm(qxy[k])],
            "A": {"shown": [{"id": t, "i": index[t], "title": title(t), "is_answer": t in answer} for t in a["shown"]],
                  "cited": a["parsed"]["matches"][:1], "right": bool(a["parsed"]["matches"]) and a["parsed"]["matches"][0] in answer,
                  "seconds": round(a["wall_seconds"], 1)},
            "B": {"steps": steps, "cited": b["parsed"]["matches"][:1],
                  "right": bool(b["parsed"]["matches"]) and b["parsed"]["matches"][0] in answer,
                  "seconds": round(b["wall_seconds"], 1), "turns": b["claude"].get("num_turns")},
            "answer": [{"id": t, "i": index[t], "title": title(t)} for t in sorted(answer) if t in index][:6],
            "answer_size": len(answer)})

    score = json.loads((RUNS / "test" / "score-test.json").read_text())
    sens = json.loads((RUNS / "test" / "sensitivity.json").read_text())
    simp = json.loads((RUNS / "simplicity.json").read_text())
    fresh = json.loads((RUNS / "freshness.json").read_text())
    arms = {}
    for a, s in score["arms"].items():
        arms[a] = {"hit1": s["seen"]["hit1"], "hit3": s["seen"]["hit3"]["k"], "median_s": s["time"]["wall"]["median"],
                   "retrieval_s": s["time"]["retrieval"]["median"], "turns": s["turns"]["median"],
                   "cost": s["cost_usd_total"]}
    data = {
        "n_tickets": len(ids), "repos": REPOS,
        "xy": base64.b64encode(q16.tobytes()).decode(), "wall": base64.b64encode(wall_pos.tobytes()).decode(),
        "repo": base64.b64encode(repo_of.tobytes()).decode(),
        "examples": examples, "arms": arms,
        "diffs": {k: score[k] for k in score if "_minus_" in k},
        "bot_line": sens["5_without_bot_suggested_questions"]["B_minus_A_hit1"],
        "simplicity": {a: {k: v[k] for k in ("lines", "n_dependencies", "artifacts_mb", "n_build_steps", "gpu")}
                       for a, v in simp["arms"].items()},
        "freshness": fresh,
        "n_questions": score["arms"]["A"]["seen"]["hit1"]["n"],
    }
    out = Path(__file__).parent / "data.js"
    out.write_text("window.SB = " + json.dumps(data, separators=(",", ":")) + ";\n")
    print(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()

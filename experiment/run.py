#!/usr/bin/env python3
"""Run arms over a question set and record everything. Arms are interleaved per question (in a seeded random
order per question) and never run at the same time, so time of day and load fall on every arm alike.

    .venv/bin/python -m experiment.run --arms A,B --split dev --out runs/pilot [--limit N] [--kinds seen,new]

One-shot arms (retrieval timed here, then one Claude turn with the top tickets in the prompt, no tools):
  A  trained embedding, top 5                    (the pipeline seen at the convention; no re-ranker, amendment 5)
  F  BM25 and trained embedding fused, top 5     (the shape of existing hybrid ticket-search connectors)
Agent arms (Claude with tools from experiment/agent_tools.py, at most MAX_TURNS turns):
  B  ripgrep search + read                      (plain search, no training)
  C  semantic search + read                     (control: the agent loop without ripgrep)
  D  ripgrep search + semantic `similar` + read (hybrid agent)
Arms A, F, C and D need `python -m experiment.pipeline.serve` running.

Every Claude call is sterile headless Claude Code (experiment/sterile.py): a fresh config home with only the
account credentials and a blanked account record, a scrubbed environment, a neutral working directory, no
built-in tools, and an init event that must list exactly the arm's tools. A contaminated call stops the run.
A record per question lands in <out>/<arm>/<qid>.json. A question already recorded is skipped (resume).
Model failures (no JSON, turn cap) are recorded and score as misses. Infrastructure failures (usage limits,
overload, a crashed CLI) are never recorded: retried, and if they persist the run stops so it can be resumed.
"""
import argparse
import json
import random
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from experiment import sterile
from experiment.common import ANSWER_FORMAT, MODEL, SYSTEM_CORE, doc_view, load_jsonl, parse_answer, question_view

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "data" / "built" / "corpus"
MAX_TURNS = 20
TIMEOUT = 900
RETRIES = 3
ONE_SHOT = {"A": "dense", "F": "hybrid"}
AGENT = {"B": "rg", "C": "semantic", "D": "hybrid"}
TOOLS = {"rg": ["mcp__archive__search", "mcp__archive__read"],
         "semantic": ["mcp__archive__search", "mcp__archive__read"],
         "hybrid": ["mcp__archive__search", "mcp__archive__similar", "mcp__archive__read"]}

SYSTEM_ONE_SHOT = SYSTEM_CORE + """
The past tickets most similar to the new one have been retrieved for you and follow the new ticket.

""" + ANSWER_FORMAT

SYSTEM_AGENT = SYSTEM_CORE + """
Search the archive with your tools, and read the tickets that look relevant. Search as many times as you need,
with different words, error codes or messages from the ticket, before you decide.

""" + ANSWER_FORMAT


class InfraError(Exception):
    pass


def claude(system: str, prompt: str, mcp_config: dict | None = None, tools: list | None = None) -> dict:
    """One sterile headless Claude Code call (experiment/sterile.py): nothing of the caller's own context reaches it,
    and its init event must list exactly the tools and servers this arm should have."""
    args = ["--model", MODEL, "--system-prompt", system, "--setting-sources", "", "--strict-mcp-config",
            "--tools", "", "--no-session-persistence", "--disable-slash-commands"]
    if mcp_config:
        args += ["--allowedTools", ",".join(tools), "--max-turns", str(MAX_TURNS)]
    try:
        out, init, wall = sterile.run(args, prompt, expect_tools=set(tools or []),
                                      expect_servers={"archive"} if mcp_config else set(), timeout=TIMEOUT,
                                      mcp_config=mcp_config)
    except subprocess.TimeoutExpired:
        raise InfraError(f"claude timed out after {TIMEOUT}s")
    except RuntimeError as e:
        raise InfraError(str(e))
    if not out:
        raise InfraError("no result event from claude")
    if out.get("is_error") and out.get("subtype") != "error_max_turns":
        raise InfraError(f"claude error {out.get('subtype')}: {str(out.get('result'))[:300]}")
    out["claude_wall_seconds"] = wall
    out["init"] = init
    return out


def ticket(tid: str) -> str:
    repo, num = tid.split("#")
    return doc_view((CORPUS / repo / f"{num}.md").read_text())


def run_one_shot(q: dict, mode: str, workdir: str) -> dict:
    from experiment.pipeline.client import search
    t0 = time.perf_counter()
    hits = search(f"{q['title']}\n\n{q['body'] or ''}", mode=mode)
    t_ret = time.perf_counter() - t0
    shown = [t for t, _ in hits]
    ctx = "\n\n".join(f"=== Past ticket {t} ===\n{ticket(t)}" for t in shown)
    out = claude(SYSTEM_ONE_SHOT, question_view(q) + "\n\n" + ctx)
    return {"shown": shown, "retrieval_seconds": t_ret, "retrieval_scores": [s for _, s in hits],
            "tool_calls": [], "claude": out, "wall_seconds": time.perf_counter() - t0}


def run_agent(q: dict, backend: str, workdir: str) -> dict:
    log = Path(workdir) / "tools.jsonl"
    log.unlink(missing_ok=True)
    cfg = {"mcpServers": {"archive": {"command": str(ROOT / ".venv" / "bin" / "python"),
                                 "args": ["-m", "experiment.agent_tools"],
                                 "env": {"SB_CORPUS": str(CORPUS), "SB_BACKEND": backend, "SB_TOOL_LOG": str(log),
                                         "PYTHONPATH": str(ROOT)}}}}
    t0 = time.perf_counter()
    out = claude(SYSTEM_AGENT, question_view(q), cfg, TOOLS[backend])
    wall = time.perf_counter() - t0
    calls = [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []
    shown = sorted({t for c in calls for t in c["returned"]})
    return {"shown": shown, "retrieval_seconds": sum(c["seconds"] for c in calls), "tool_calls": calls,
            "claude": out, "wall_seconds": wall}


def run_arm(arm: str, q: dict) -> dict:
    for attempt in range(RETRIES):
        with tempfile.TemporaryDirectory(prefix="sb-") as wd:
            try:
                if arm in ONE_SHOT:
                    return run_one_shot(q, ONE_SHOT[arm], wd)
                return run_agent(q, AGENT[arm], wd)
            except InfraError as e:
                print(f"  infra failure ({arm} {q['qid']}, attempt {attempt + 1}): {e}", file=sys.stderr, flush=True)
                time.sleep(60 * (attempt + 1))
    raise SystemExit(f"stopping: {arm} {q['qid']} failed {RETRIES} times on infrastructure; resume later")


def meta(arms: list, a, n: int) -> dict:
    git = lambda *x: subprocess.run(["git", "-C", str(ROOT), *x], capture_output=True, text=True).stdout.strip()
    return {"arms": arms, "split": a.split, "kinds": a.kinds, "questions": n, "model": MODEL,
            "max_turns": MAX_TURNS,
            "claude_version": subprocess.run(["claude", "--version"], capture_output=True, text=True).stdout.strip(),
            "commit": git("rev-parse", "HEAD"), "dirty": bool(git("status", "--porcelain", "--", "experiment", "corpus")),
            "started": time.strftime("%Y-%m-%dT%H:%M:%S%z")}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms", required=True)
    ap.add_argument("--split", required=True, choices=["dev", "test"])
    ap.add_argument("--out", required=True)
    ap.add_argument("--kinds", default="seen,new")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    arms = a.arms.split(",")
    assert all(x in ONE_SHOT or x in AGENT for x in arms), arms
    qs = [q for q in load_jsonl(ROOT / "data" / "built" / "questions.jsonl")
          if q["split"] == a.split and q["kind"] in a.kinds.split(",")]
    if a.limit:
        qs = qs[:a.limit]
    out = ROOT / a.out
    out.mkdir(parents=True, exist_ok=True)
    m = meta(arms, a, len(qs))
    with (out / "meta.jsonl").open("a") as f:
        f.write(json.dumps(m) + "\n")
    rng = random.Random(7)
    for n, q in enumerate(qs, 1):
        order = arms[:]
        rng.shuffle(order)
        for arm in order:
            rec_path = out / arm / (q["qid"].replace("#", "_") + ".json")
            if rec_path.exists():
                continue
            rec_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                r = run_arm(arm, q)
            except sterile.Contaminated as e:
                print(f"STOP: contaminated call ({arm} {q['qid']}): {e}", file=sys.stderr, flush=True)
                sys.exit(3)
            r["qid"], r["arm"] = q["qid"], arm
            r["parsed"] = parse_answer(r["claude"].get("result", ""))
            r["hit_turn_cap"] = r["claude"].get("subtype") == "error_max_turns"
            rec_path.write_text(json.dumps(r, indent=1))
            print(f"[{n}/{len(qs)} {arm}] {q['qid']} matches={r['parsed']['matches']} "
                  f"wall={r['wall_seconds']:.1f}s retrieval={r['retrieval_seconds']:.2f}s", file=sys.stderr, flush=True)
    with (out / "meta.jsonl").open("a") as f:
        f.write(json.dumps({"finished": time.strftime("%Y-%m-%dT%H:%M:%S%z")}) + "\n")


if __name__ == "__main__":
    main()

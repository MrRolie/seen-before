#!/usr/bin/env python3
"""The agent arms' tools, served over MCP (stdio): search the past tickets, read one.

    SB_CORPUS=data/built/corpus SB_BACKEND=rg SB_TOOL_LOG=run/tools.jsonl python -m experiment.agent_tools

Run from the repo root. Backends:
  rg        arm B: `search` is ripgrep over one markdown file per ticket. Nothing trained, nothing indexed.
  semantic  arm C: `search` is the trained retriever (served by experiment.pipeline.serve; no re-ranker,
            PROTOCOL.md amendment 5).
  hybrid    arm D: both, as two tools: `search` (ripgrep) and `similar` (the trained retriever).
Every call is timed here and appended to SB_TOOL_LOG, so the retrieval share of an answer's time is measured by
the tool itself, not inferred.
"""
import json
import os
import subprocess
import time
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from experiment.common import doc_view

CORPUS = Path(os.environ["SB_CORPUS"])
BACKEND = os.environ.get("SB_BACKEND", "rg")
LOG = os.environ.get("SB_TOOL_LOG")
MAX_HITS = 20
LINES_PER_HIT = 3
LINE_CHARS = 200
REPOS = ("PowerToys", "terminal", "WSL")

server = MCPServer("archive")  # a neutral name: the model sees it


def log(tool: str, args: dict, seconds: float, returned: list) -> None:
    if LOG:
        with open(LOG, "a") as f:
            f.write(json.dumps({"t": time.time(), "tool": tool, "args": args, "seconds": seconds,
                                "returned": returned}) + "\n")


def path_to_id(p: str) -> str:
    rel = Path(p).relative_to(CORPUS)
    return f"{rel.parent.name}#{rel.stem}"


def title_of(tid: str) -> str:
    repo, num = tid.split("#")
    with open(CORPUS / repo / f"{num}.md") as f:
        first = f.readline().strip()
    return first.split(": ", 1)[1] if ": " in first else first


def where(repo: str | None) -> str:
    if repo and repo not in REPOS:
        raise ValueError(f"repo must be one of {REPOS}")
    return str(CORPUS / repo if repo else CORPUS)


def rg(args: list[str]) -> str:
    p = subprocess.run(["rg", "-i", "--no-messages", "--with-filename"] + args, capture_output=True, text=True)
    if p.returncode == 2 and not p.stdout:
        raise ValueError(p.stderr.strip()[:300] or "ripgrep error")
    return p.stdout


def rg_search(pattern: str, regex: bool, also: list[str], repo: str | None) -> list[dict]:
    lit = [] if regex else ["-F"]
    counts = {}
    for line in rg(["-c"] + lit + ["-e", pattern, where(repo)]).splitlines():
        f, _, n = line.rpartition(":")
        counts[f] = int(n)
    files = set(counts)
    for extra in also:
        if not files:
            break
        files &= set(rg(["-l"] + lit + ["-e", extra, where(repo)]).split("\n"))
    if not files:
        return []
    # Most matching lines first; ties go to the newer ticket (higher number).
    ranked = sorted(files, key=lambda f: (-counts[f], -int(Path(f).stem)))[:MAX_HITS]
    lines = {f: [] for f in ranked}
    for line in rg(["-m", str(LINES_PER_HIT), "--no-line-number"] + lit + ["-e", pattern] + ranked).splitlines():
        f, _, text = line.partition(":")
        if f in lines and len(lines[f]) < LINES_PER_HIT:
            lines[f].append(text.strip()[:LINE_CHARS])
    out = []
    for f in ranked:
        tid = path_to_id(f)
        out.append({"id": tid, "title": title_of(tid), "matching_lines": counts[f], "lines": lines[f]})
    return out


def semantic(query: str, repo: str | None) -> list[dict]:
    from experiment.pipeline.client import search as served
    where(repo)
    return [{"id": t, "title": title_of(t), "score": round(s, 3)} for t, s in served(query, k=MAX_HITS, repo=repo)]


def timed(tool: str, args: dict, fn):
    t0 = time.perf_counter()
    try:
        hits, err = fn(), None
    except Exception as e:  # a bad regex, say; the model sees the error and can retry
        hits, err = [], str(e)
    log(tool, args, time.perf_counter() - t0, [h["id"] for h in hits])
    return json.dumps({"error": err} if err else {"results": hits, "count": len(hits)})


RG_DOC = """Search the past tickets with ripgrep (case-insensitive). `pattern` is a literal string unless `regex` is
true. `also` lists more patterns that must ALL appear somewhere in the same ticket. `repo` limits the search to one
product: "PowerToys", "terminal" or "WSL". Returns up to 20 tickets, most matching lines first, each with its id,
title and up to 3 matching lines. Read a ticket with `read`."""

SEM_DOC = """Find past tickets by meaning: describe the problem in plain words (or paste the customer's text).
`repo` limits the search to one product: "PowerToys", "terminal" or "WSL". Returns up to 20 tickets, most similar
first, each with its id and title. Read a ticket with `read`."""

if BACKEND in ("rg", "hybrid"):
    def search(pattern: str, regex: bool = False, also: list[str] | None = None, repo: str | None = None) -> str:
        return timed("search", {"pattern": pattern, "regex": regex, "also": also, "repo": repo},
                     lambda: rg_search(pattern, regex, also or [], repo))
    search.__doc__ = RG_DOC
    server.tool()(search)

if BACKEND == "semantic":
    def search(query: str, repo: str | None = None) -> str:  # noqa: F811
        return timed("search", {"query": query, "repo": repo}, lambda: semantic(query, repo))
    search.__doc__ = SEM_DOC
    server.tool()(search)

if BACKEND == "hybrid":
    def similar(query: str, repo: str | None = None) -> str:
        return timed("similar", {"query": query, "repo": repo}, lambda: semantic(query, repo))
    similar.__doc__ = SEM_DOC
    server.tool()(similar)


@server.tool()
def read(ticket: str) -> str:
    """Read one past ticket in full (title, description, and the comments it had), by id, e.g. "WSL#1234"."""
    t0 = time.perf_counter()
    try:
        repo, num = ticket.strip().split("#")
        if repo not in REPOS:
            raise ValueError
        text = doc_view((CORPUS / repo / f"{int(num)}.md").read_text())
        ok = [f"{repo}#{int(num)}"]
    except (ValueError, OSError):
        text, ok = f"No ticket {ticket!r} in the archive.", []
    log("read", {"ticket": ticket}, time.perf_counter() - t0, ok)
    return text


if __name__ == "__main__":
    server.run("stdio")

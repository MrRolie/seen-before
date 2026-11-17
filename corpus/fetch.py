#!/usr/bin/env python3
"""Fetch a GitHub repository's issues and issue comments into local JSONL files.

The corpus is not redistributed: issue text belongs to its authors, so this tool
fetches it from the GitHub REST API on your own token instead.

    GITHUB_TOKEN=... python corpus/fetch.py microsoft/PowerToys data/raw

Writes <out>/<owner>__<repo>/issues.jsonl and comments.jsonl. Pull requests are
skipped. Re-running resumes from the last page written (a page cut mid-write may repeat; readers dedupe by id). Standard library only.
"""
import http.client
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.github.com"


def token() -> str:
    tok = os.environ.get("GITHUB_TOKEN")
    if tok:
        return tok
    try:
        return subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        sys.exit("set GITHUB_TOKEN (or log in with the gh CLI)")


def get(url: str, tok: str):
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {tok}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "seen-before-fetch",
    })
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                remaining = int(r.headers.get("X-RateLimit-Remaining", "1"))
                if remaining < 20:
                    wait = int(r.headers.get("X-RateLimit-Reset", "0")) - time.time() + 5
                    print(f"rate limit low, sleeping {wait:.0f}s", file=sys.stderr)
                    time.sleep(max(wait, 0))
                return json.load(r), next_link(r.headers.get("Link", ""))
        except urllib.error.HTTPError as e:
            if e.code in (403, 429, 500, 502, 503, 504):
                time.sleep(2 ** attempt * 5)
                continue
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException):
            time.sleep(2 ** attempt * 5)
    raise RuntimeError(f"giving up on {url}")


def next_link(header: str):
    for part in header.split(","):
        if 'rel="next"' in part:
            return part.split(";")[0].strip()[1:-1]
    return None


def slim_issue(i: dict) -> dict:
    return {
        "number": i["number"], "title": i["title"], "body": i.get("body") or "",
        "user": (i.get("user") or {}).get("login"), "created_at": i["created_at"],
        "closed_at": i.get("closed_at"), "state": i["state"], "state_reason": i.get("state_reason"),
        "labels": [lb["name"] for lb in i.get("labels", [])],
    }


def slim_comment(c: dict) -> dict:
    return {
        "id": c["id"], "issue": int(c["issue_url"].rsplit("/", 1)[1]),
        "user": (c.get("user") or {}).get("login"), "user_type": (c.get("user") or {}).get("type"),
        "author_association": c.get("author_association"),
        "created_at": c["created_at"], "updated_at": c.get("updated_at"), "body": c.get("body") or "",
    }


def pull(endpoint: str, path: Path, keep, slim, tok: str) -> None:
    """Follow the API's cursor links (page numbers stop at 10,000 items). The state file holds the next URL."""
    state = path.with_suffix(".next")
    url = state.read_text() if state.exists() else f"{API}{endpoint}&per_page=100"
    n = 0
    with path.open("a") as out:
        while url:
            items, nxt = get(url, tok)
            for it in items:
                if keep(it):
                    out.write(json.dumps(slim(it)) + "\n")
            out.flush()
            url = nxt
            state.write_text(url or "done")
            n += 1
            if n % 20 == 0:
                print(f"{path.name}: {n} pages", file=sys.stderr)


def pull_comments(repo: str, path: Path, tok: str) -> None:
    """The repo-wide comments list stops after a fixed number of pages with no error and no next link (300 pages,
    30,000 comments, when measured 2026-11-16). So pull in rounds sorted by update time, each round starting
    `since` the last comment of the round before, until a round brings no comment not already held.
    Rounds overlap at their boundary; readers dedupe by id."""
    state = path.with_suffix(".since")
    since = state.read_text() if state.exists() else None
    seen = set()
    if path.exists():
        for line in path.open():
            try:
                seen.add(json.loads(line)["id"])
            except (json.JSONDecodeError, KeyError):
                continue
    while since != "done":
        url = f"{API}/repos/{repo}/issues/comments?sort=updated&direction=asc&per_page=100" + \
              (f"&since={since}" if since else "")
        last, new, pages = None, 0, 0
        with path.open("a") as out:
            while url:
                items, url = get(url, tok)
                for c in items:
                    if c["id"] not in seen:
                        seen.add(c["id"])
                        new += 1
                        out.write(json.dumps(slim_comment(c)) + "\n")
                    last = c.get("updated_at") or last
                pages += 1
        print(f"{path.name}: round since {since}: {new} new comments in {pages} pages", file=sys.stderr)
        since = last if new and last else "done"
        state.write_text(since)


def main() -> None:
    repo, outdir = sys.argv[1], Path(sys.argv[2])
    tok = token()
    d = outdir / repo.replace("/", "__")
    d.mkdir(parents=True, exist_ok=True)
    p = d / "issues.jsonl"
    if not (p.with_suffix(".next").exists() and p.with_suffix(".next").read_text() == "done"):
        pull(f"/repos/{repo}/issues?state=all&sort=created&direction=asc", p,
             lambda i: "pull_request" not in i, slim_issue, tok)
    pull_comments(repo, d / "comments.jsonl", tok)
    print(f"done {repo}", file=sys.stderr)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Fetch GitHub's own duplicate links: when an issue is closed with "Close as duplicate", GitHub records the
original as `duplicateOf` (GraphQL only), with who closed it and when.

    python corpus/fetch_dups.py microsoft/PowerToys data/raw

Asks only about issues already fetched that were closed as duplicate or carry a duplicate label.
Writes <out>/<owner>__<repo>/duplicate_of.jsonl. Standard library only.
"""
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from fetch import token  # noqa: E402

DUP_LABELS = {"Resolution-Duplicate", "duplicate"}
BATCH = 40

FIELDS = """number author { login } closedAt
  duplicateOf { ... on Issue { number repository { name } } }
  timelineItems(itemTypes: [CLOSED_EVENT], last: 1) {
    nodes { ... on ClosedEvent { createdAt actor { login } duplicateOf { ... on Issue { number repository { name } } } } } }"""


def gql(query: str, tok: str) -> dict:
    req = urllib.request.Request("https://api.github.com/graphql", data=json.dumps({"query": query}).encode(),
                                 headers={"Authorization": f"Bearer {tok}", "User-Agent": "seen-before-fetch"})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                d = json.load(r)
            if "errors" in d and not d.get("data"):
                raise RuntimeError(d["errors"])
            return d["data"]
        except (urllib.error.URLError, TimeoutError, ConnectionError, RuntimeError) as e:
            print(f"retry {attempt}: {e}", file=sys.stderr)
            time.sleep(2 ** attempt * 5)
    raise RuntimeError("giving up")


def main() -> None:
    repo, outdir = sys.argv[1], Path(sys.argv[2])
    owner, name = repo.split("/")
    d = outdir / repo.replace("/", "__")
    out = d / "duplicate_of.jsonl"
    done = {json.loads(l)["number"] for l in out.open()} if out.exists() else set()
    nums = []
    for line in (d / "issues.jsonl").open():
        try:
            i = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (i.get("state_reason") == "duplicate" or DUP_LABELS & set(i["labels"])) and i["number"] not in done:
            nums.append(i["number"])
    nums = sorted(set(nums))
    tok = token()
    with out.open("a") as f:
        for b in range(0, len(nums), BATCH):
            chunk = nums[b:b + BATCH]
            q = "{ repository(owner: \"%s\", name: \"%s\") { %s } }" % (
                owner, name, " ".join(f"i{n}: issue(number: {n}) {{ {FIELDS} }}" for n in chunk))
            data = gql(q, tok)["repository"]
            for n in chunk:
                it = data.get(f"i{n}")
                if not it:
                    continue
                ev = (it["timelineItems"]["nodes"] or [{}])[-1] or {}
                dup = it.get("duplicateOf") or ev.get("duplicateOf")
                f.write(json.dumps({
                    "number": n, "author": (it.get("author") or {}).get("login"),
                    "duplicate_of": f"{dup['repository']['name']}#{dup['number']}" if dup else None,
                    "closed_by": (ev.get("actor") or {}).get("login"), "closed_at": ev.get("createdAt") or it.get("closedAt"),
                }) + "\n")
            f.flush()
            if (b // BATCH) % 25 == 0:
                print(f"{repo}: {b + len(chunk)}/{len(nums)}", file=sys.stderr)


if __name__ == "__main__":
    main()

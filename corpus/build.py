#!/usr/bin/env python3
"""Build the as-of corpus, the ground truth and the question sets from fetched raw data.

    python corpus/build.py data/raw data/built

Every rule here is fixed in experiment/PROTOCOL.md; change one only by changing that file first.

Outputs (in <out>):
  corpus/<Repo>/<number>.md   one file per issue created before CUTOFF, comments before CUTOFF only,
                              no labels, no state (both are later knowledge)
  corpus.jsonl                the same documents as records (id, repo, number, title, body, created_at, text)
  links.jsonl                 every validated duplicate link (dup -> canonical, with the comment's time)
  train_pairs.jsonl           links usable for training: both issues and the link itself before CUTOFF
  questions.jsonl             dev and test questions with their answer clusters and strata
  build_report.json           counts at every filter step
Standard library only.
"""
import json
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

CUTOFF = "2026-07-01T00:00:00Z"   # corpus = items created before this; test questions created on/after it
WINDOW_END = "2026-11-16T00:00:00Z"
REPOS = {"microsoft__PowerToys": "PowerToys", "microsoft__terminal": "terminal", "microsoft__WSL": "WSL"}
DUP_LABELS = {"Resolution-Duplicate", "duplicate"}
BUG_LABELS = {"Issue-Bug", "bug"}
MAINTAINER = {"OWNER", "MEMBER", "COLLABORATOR", "CONTRIBUTOR"}  # staff often show as CONTRIBUTOR
BOT_CONFIRM = re.compile(r"identified this issue as a duplicate", re.IGNORECASE)
NEGATION = re.compile(r"\bnot\b|n't\b", re.IGNORECASE)
CAP_PER_CLUSTER = 2
DEV_QUESTIONS = 20
NEG_DEV, NEG_TEST = 10, 30
SEED = 7

# "/dup #123", "/dupe #123", "/duplicate https://github.com/microsoft/X/issues/123", "/duplicate of #123",
# "a /dup of #123", "duplicate of #123", "dupe of #123", "Closing as dupe of <url>",
# "closing this issue as a duplicate. See: <url>" (the word, then at most 40 characters, then the reference)
LINK_RE = re.compile(
    r"(?:^|\s)(?:/dup(?:e|licate)?(?:\s+of)?|dup(?:licate|e)?\b[^#\n/]{0,40}?)\s*:?\s*"
    r"(?:#(\d+)|https://github\.com/microsoft/(PowerToys|terminal|WSL)/issues/(\d+))",
    re.IGNORECASE | re.MULTILINE)

# Exact-token stratum: the question carries a token a literal search can match verbatim.
EXACT_TOKEN_RE = re.compile(
    r"0x[0-9a-fA-F]{6,}"                     # HRESULT-style codes
    r"|\b[A-Z]\w+Exception\b"                # .NET exception types
    r"|^\s*at [\w.`<>]+\("                   # stack frames
    r"|\bWsl/[\w/]+"                         # WSL error paths
    r"|\bE_[A-Z_]{4,}\b"                     # COM error names
    r"|\berror(?: code)?\s*[:#]?\s*-?\d{4,}", # numeric error codes
    re.MULTILINE)


def load_jsonl(p: Path):
    with p.open() as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue  # a line cut by an interrupted fetch


def iid(repo: str, number: int) -> str:
    return f"{repo}#{number}"


def link_of(issue: dict, comments: list):
    """The earlier issue this one duplicates, and when that was said.
    If the repository's bot confirmed a duplicate close, the link is the last human /dup command before its first
    confirmation (the bot acts only on authorised users, whose association GitHub sometimes reports as NONE).
    Otherwise it is the last maintainer comment naming a target. Returns (None, None) when no link is found."""
    names = {"powertoys": "PowerToys", "terminal": "terminal", "wsl": "WSL"}
    confirm = next((n for n, c in enumerate(comments)
                    if c.get("user_type") == "Bot" and BOT_CONFIRM.search(c["body"])), None)
    pool = comments[:confirm] if confirm is not None else comments
    target, when = None, None
    for c in pool:
        if c.get("user_type") != "User":
            continue
        if confirm is None and c.get("author_association") not in MAINTAINER:
            continue
        for m in LINK_RE.finditer(c["body"]):
            if NEGATION.search(c["body"][max(0, m.start() - 15):m.start() + 4]):
                continue
            repo = names[m.group(2).lower()] if m.group(2) else issue["repo"]
            target, when = iid(repo, int(m.group(1) or m.group(3))), c["created_at"]
    return target, when


def render(doc: dict, comments: list) -> str:
    out = [f"# {doc['id']}: {doc['title']}", f"created: {doc['created_at'][:10]}", "", doc["body"].strip(), ""]
    for c in comments:
        out += [f"--- comment by {c['user']} ({c['created_at'][:10]})", c["body"].strip(), ""]
    return "\n".join(out)


class UF:
    def __init__(self):
        self.p = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        self.p[self.find(a)] = self.find(b)


def main() -> None:
    raw, out = Path(sys.argv[1]), Path(sys.argv[2])
    report = Counter()
    issues, comments = {}, defaultdict(list)
    for d, repo in REPOS.items():
        for i in load_jsonl(raw / d / "issues.jsonl"):
            issues[iid(repo, i["number"])] = {**i, "repo": repo, "id": iid(repo, i["number"])}
        seen = set()
        for c in load_jsonl(raw / d / "comments.jsonl"):
            if c["id"] in seen:
                continue
            seen.add(c["id"])
            comments[iid(repo, c["issue"])].append(c)
    for v in comments.values():
        v.sort(key=lambda c: c["created_at"])
    report["issues_fetched"] = len(issues)
    out.mkdir(parents=True, exist_ok=True)

    # Duplicate links. An issue is a duplicate if it carries a duplicate label or was closed as duplicate.
    # GitHub's own link ("Close as duplicate" -> duplicateOf) comes first; otherwise the comment rule (link_of).
    native = {}
    for d, repo in REPOS.items():
        f = raw / d / "duplicate_of.jsonl"
        if f.exists():
            for r in load_jsonl(f):
                native[iid(repo, r["number"])] = r
    links = {}
    for k, i in issues.items():
        if not (DUP_LABELS & set(i["labels"]) or i.get("state_reason") == "duplicate"):
            continue
        report["duplicate"] += 1
        report[f"duplicate_{i['repo']}"] += 1
        n = native.get(k) or {}
        if n.get("duplicate_of"):
            target, when = n["duplicate_of"], n["closed_at"]
            source = "native_self" if n.get("closed_by") == n.get("author") else "native"
        else:
            (target, when), source = link_of(i, comments.get(k, [])), "comment"
        if target is None:
            report["dup_no_link"] += 1
            report[f"dup_no_link_{i['repo']}"] += 1
            continue
        if target == k:
            report["dup_self_link"] += 1
            continue
        t = issues.get(target)
        if t is None:
            report["dup_target_missing"] += 1
            continue
        if t["created_at"] >= i["created_at"]:
            report["dup_target_not_earlier"] += 1  # kept: same problem; counts in clusters, not as "seen before"
        report[f"link_source_{source}"] += 1
        links[k] = {"dup": k, "canonical": target, "linked_at": when, "source": source,
                    "target_earlier": t["created_at"] < i["created_at"]}
    report["links_valid"] = len(links)

    uf = UF()
    for l in links.values():
        uf.union(l["dup"], l["canonical"])
    members = defaultdict(set)
    for x in list(uf.p):
        members[uf.find(x)].add(x)

    # Corpus as of CUTOFF.
    cdir = out / "corpus"
    corpus_ids = set()
    with (out / "corpus.jsonl").open("w") as cj:
        for k, i in sorted(issues.items(), key=lambda kv: kv[1]["created_at"]):
            if i["created_at"] >= CUTOFF:
                continue
            doc = {"id": k, "repo": i["repo"], "number": i["number"], "title": i["title"],
                   "body": i["body"], "created_at": i["created_at"]}
            cs = [c for c in comments.get(k, []) if c["created_at"] < CUTOFF]
            edited = [c for c in cs if (c.get("updated_at") or c["created_at"]) >= CUTOFF]
            report["comments_dropped_edited_after_cutoff"] += len(edited)
            cs = [c for c in cs if c not in edited]
            doc["text"] = render(doc, cs)
            p = cdir / i["repo"] / f"{i['number']}.md"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(doc["text"])
            cj.write(json.dumps(doc) + "\n")
            corpus_ids.add(k)
    report["corpus_docs"] = len(corpus_ids)

    with (out / "links.jsonl").open("w") as f:
        for l in links.values():
            f.write(json.dumps(l) + "\n")
    with (out / "train_pairs.jsonl").open("w") as f:
        for l in links.values():
            if l["dup"] in corpus_ids and l["canonical"] in corpus_ids and (l["linked_at"] or "9") < CUTOFF:
                f.write(json.dumps(l) + "\n")
                report["train_pairs"] += 1

    # Positive questions.
    cands = []
    for k, l in links.items():
        i = issues[k]
        if not (CUTOFF <= i["created_at"] < WINDOW_END):
            continue
        report["q_in_window"] += 1
        answer = sorted(m for m in members[uf.find(k)] if m in corpus_ids)
        if not answer:
            report["q_drop_no_answer_in_corpus"] += 1
            continue
        text = f"{i['title']}\n\n{i['body']}"
        nums = {int(a.split("#")[1]) for a in answer}
        mentioned = {int(n) for n in re.findall(r"#(\d+)|/issues/(\d+)", text) for n in n if n}
        if nums & mentioned:
            report["q_drop_mentions_answer"] += 1
            continue
        stratum = "exact_token" if EXACT_TOKEN_RE.search(text) else "plain_language"
        cands.append({"qid": k, "kind": "seen", "repo": i["repo"], "created_at": i["created_at"],
                      "title": i["title"], "body": i["body"], "answer": answer,
                      "cluster": uf.find(k), "stratum": stratum})
    report["q_eligible_uncapped"] = len(cands)
    report["q_uncapped_top_cluster_share"] = round(
        max(Counter(c["cluster"] for c in cands).values()) / len(cands), 3) if cands else 0

    rng = random.Random(SEED)
    by_cluster = defaultdict(list)
    for c in sorted(cands, key=lambda c: c["qid"]):
        by_cluster[c["cluster"]].append(c)
    capped = []
    for cl in sorted(by_cluster):
        qs = by_cluster[cl]
        rng.shuffle(qs)
        capped += qs[:CAP_PER_CLUSTER]
    clusters = sorted({c["cluster"] for c in capped})
    rng.shuffle(clusters)
    dev_clusters, n = set(), 0
    for cl in clusters:  # dev and test share no cluster
        if n >= DEV_QUESTIONS:
            break
        dev_clusters.add(cl)
        n += sum(1 for c in capped if c["cluster"] == cl)
    for c in capped:
        c["split"] = "dev" if c["cluster"] in dev_clusters else "test"

    # Negative questions: bugs in the window closed as completed, never labelled or linked as duplicate.
    negs = []
    for k, i in issues.items():
        if not (CUTOFF <= i["created_at"] < WINDOW_END):
            continue
        if not BUG_LABELS & set(i["labels"]) or DUP_LABELS & set(i["labels"]):
            continue
        if i.get("state_reason") != "completed" or k in uf.p:
            continue
        negs.append(i)
    negs.sort(key=lambda i: i["id"])
    rng.shuffle(negs)
    report["neg_pool"] = len(negs)
    negq = []
    for j, i in enumerate(negs[:NEG_DEV + NEG_TEST]):
        text = f"{i['title']}\n\n{i['body']}"
        negq.append({"qid": i["id"], "kind": "new", "repo": i["repo"], "created_at": i["created_at"],
                     "title": i["title"], "body": i["body"], "answer": [], "cluster": None,
                     "stratum": "exact_token" if EXACT_TOKEN_RE.search(text) else "plain_language",
                     "split": "dev" if j < NEG_DEV else "test"})

    allq = capped + negq
    with (out / "questions.jsonl").open("w") as f:
        for q in sorted(allq, key=lambda q: (q["split"], q["created_at"])):
            f.write(json.dumps(q) + "\n")
    for q in allq:
        report[f"q_{q['split']}_{q['kind']}"] += 1
        if q["kind"] == "seen":
            report[f"q_{q['split']}_{q['stratum']}"] += 1
    report["q_test_distinct_clusters"] = len({q["cluster"] for q in capped if q["split"] == "test"})
    (out / "build_report.json").write_text(json.dumps(dict(sorted(report.items())), indent=1))
    print(json.dumps(dict(sorted(report.items())), indent=1))


if __name__ == "__main__":
    main()

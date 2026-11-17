#!/usr/bin/env python3
"""Were the questions themselves changed after the customer wrote them? Title renames and body edits, by whom.

    python corpus/question_edits.py data/built  ->  data/built/question_edits.json
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from fetch_dups import gql, token  # noqa: E402

built = Path(sys.argv[1])
qs = [json.loads(l) for l in (built / "questions.jsonl").read_text().splitlines() if l.strip()]
qs = [q for q in qs if q["split"] == "test"]
F = """author { login } lastEditedAt editor { login }
  timelineItems(itemTypes: [RENAMED_TITLE_EVENT], first: 20) {
    nodes { ... on RenamedTitleEvent { actor { login } previousTitle currentTitle createdAt } } }"""
tok = token()
out = {}
for repo in ("PowerToys", "terminal", "WSL"):
    nums = [int(q["qid"].split("#")[1]) for q in qs if q["repo"] == repo]
    for b in range(0, len(nums), 30):
        chunk = nums[b:b + 30]
        q = '{ repository(owner: "microsoft", name: "%s") { %s } }' % (
            repo, " ".join(f"i{n}: issue(number: {n}) {{ {F} }}" for n in chunk))
        data = gql(q, tok)["repository"]
        for n in chunk:
            it = data.get(f"i{n}") or {}
            author = (it.get("author") or {}).get("login")
            renames = [r for r in it["timelineItems"]["nodes"] if r]
            out[f"{repo}#{n}"] = {
                "author": author,
                "renamed_by_other": [r for r in renames if (r.get("actor") or {}).get("login") != author],
                "renamed_by_author": len([r for r in renames if (r.get("actor") or {}).get("login") == author]),
                "body_edited_by": (it.get("editor") or {}).get("login") if it.get("lastEditedAt") else None}
changed = sorted(k for k, v in out.items() if v["renamed_by_other"]
                 or (v["body_edited_by"] and v["body_edited_by"] != v["author"]))
res = {"questions": len(out), "changed_by_someone_else": changed, "per_question": out}
(built / "question_edits.json").write_text(json.dumps(res, indent=1))
print(len(out), "test questions;", len(changed), "retitled or edited by someone other than the author")
print("renamed by other:", sum(bool(v["renamed_by_other"]) for v in out.values()),
      "| body edited by other:", sum(bool(v["body_edited_by"] and v["body_edited_by"] != v["author"]) for v in out.values()),
      "| renamed by author:", sum(v["renamed_by_author"] > 0 for v in out.values()))

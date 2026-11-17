#!/usr/bin/env python3
"""Which answer tickets were edited after the cutoff (the edit leak PROTOCOL.md names): GitHub's lastEditedAt.

    python corpus/edits.py data/built   ->  data/built/answer_edits.json
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from build import CUTOFF  # noqa: E402
from fetch_dups import gql, token  # noqa: E402

built = Path(sys.argv[1])
qs = [json.loads(l) for l in (built / "questions.jsonl").read_text().splitlines() if l.strip()]
ids = sorted({a for q in qs if q["kind"] == "seen" for a in q["answer"]})
tok = token()
edited = {}
for repo in ("PowerToys", "terminal", "WSL"):
    nums = [int(i.split("#")[1]) for i in ids if i.startswith(repo + "#")]
    for b in range(0, len(nums), 50):
        chunk = nums[b:b + 50]
        q = '{ repository(owner: "microsoft", name: "%s") { %s } }' % (
            repo, " ".join(f"i{n}: issue(number: {n}) {{ lastEditedAt }}" for n in chunk))
        data = gql(q, tok)["repository"]
        for n in chunk:
            t = (data.get(f"i{n}") or {}).get("lastEditedAt")
            edited[f"{repo}#{n}"] = t
after = sorted(k for k, t in edited.items() if t and t >= CUTOFF)
hit_q = [q["qid"] for q in qs if q["kind"] == "seen" and set(q["answer"]) & set(after)]
out = {"answer_tickets": len(ids), "edited_after_cutoff": after, "questions_touching_them": hit_q}
(built / "answer_edits.json").write_text(json.dumps(out, indent=1))
print(len(ids), "answer tickets;", len(after), "edited after the cutoff;", len(hit_q), "questions touch one")

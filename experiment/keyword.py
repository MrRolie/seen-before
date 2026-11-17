"""A BM25 keyword index over the past tickets (SQLite FTS5, standard library). Deterministic: no model.

Used by the hybrid one-shot pipeline (keyword and embedding candidates fused) and by the diagnostics.
"""
import re
import sqlite3
import time

from experiment.common import load_jsonl
from experiment.pipeline import config as C

DB = C.INDEX / "fts.sqlite"
TERM = re.compile(r"[A-Za-z0-9_]{2,}")
MAX_TERMS = 64


def build() -> dict:
    t0 = time.time()
    C.INDEX.mkdir(parents=True, exist_ok=True)
    DB.unlink(missing_ok=True)
    con = sqlite3.connect(DB)
    con.execute("CREATE VIRTUAL TABLE t USING fts5(id UNINDEXED, title, body, tokenize='unicode61')")
    con.executemany("INSERT INTO t VALUES (?, ?, ?)",
                    ((d["id"], d["title"], d["text"]) for d in load_jsonl(C.BUILT / "corpus.jsonl")))
    con.commit()
    con.close()
    return {"seconds": round(time.time() - t0, 1), "bytes": DB.stat().st_size}


class Keyword:
    def __init__(self):
        self.con = sqlite3.connect(DB, check_same_thread=False)

    def search(self, text: str, k: int = 50, repo: str | None = None):
        terms, seen = [], set()
        for w in TERM.findall(text.lower()):
            if w not in seen:
                seen.add(w)
                terms.append(f'"{w}"')
            if len(terms) == MAX_TERMS:
                break
        if not terms:
            return []
        q = " OR ".join(terms)
        sql = "SELECT id, bm25(t, 2.0, 1.0) AS s FROM t WHERE t MATCH ? "
        args = [q]
        if repo:
            sql += "AND id LIKE ? "
            args.append(repo + "#%")
        sql += "ORDER BY s LIMIT ?"
        args.append(k)
        return [(i, -s) for i, s in self.con.execute(sql, args)]


if __name__ == "__main__":
    import json
    rec = build()
    (C.INDEX / "keyword-build.json").write_text(json.dumps(rec))
    print(rec)

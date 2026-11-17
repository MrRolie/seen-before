#!/usr/bin/env python3
"""Run the connector's check (connector/seen_before.py) on a few dev questions against the frozen corpus,
through the sterile wrapper, and compare with arm B's pilot records. Plumbing only; nothing is scored.

    .venv/bin/python -m experiment.connector_smoke 3
"""
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

from experiment import sterile
from experiment.common import PRODUCTS, load_jsonl

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "data" / "built" / "corpus"
NAMES = ("PowerToys", "terminal", "WSL")
spec = importlib.util.spec_from_file_location("sb", ROOT / "connector" / "seen_before.py")
sb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sb)


def main() -> None:
    n = int(sys.argv[1])
    qs = [q for q in load_jsonl(ROOT / "data" / "built" / "questions.jsonl") if q["split"] == "dev" and q["kind"] == "seen"][:n]
    out = []
    for q in qs:
        with tempfile.TemporaryDirectory(prefix="sb-") as wd:
            log = Path(wd) / "tools.jsonl"
            num = int(q["qid"].split("#")[1])
            call = sb.prepare(CORPUS, PRODUCTS, NAMES, q["repo"], num, q["title"], q["body"], log,
                              python=str(ROOT / ".venv" / "bin" / "python"))
            res, init, wall = sterile.run(call["args"], call["prompt"], expect_tools=set(call["tools"]),
                                          expect_servers={"archive"}, timeout=900, mcp_config=call["mcp_config"])
            v = sb.finish(res, log, NAMES, CORPUS)
        pilot = ROOT / "runs" / "pilot" / "B" / (q["qid"].replace("#", "_") + ".json")
        b = json.loads(pilot.read_text())["parsed"]["matches"] if pilot.exists() else None
        row = {"qid": q["qid"], "connector": [m["id"] for m in v["matches"]], "pilot_B": b,
               "hit1": bool(v["matches"]) and v["matches"][0]["id"] in q["answer"],
               "hidden_self_shown": q["qid"] in v["shown"], "turns": v["turns"], "wall": round(wall, 1),
               "cost": v["cost_usd"]}
        print(json.dumps(row), flush=True)
        out.append(row)
    (ROOT / "runs" / "connector-smoke.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()

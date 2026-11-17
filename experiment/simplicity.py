#!/usr/bin/env python3
""""Simpler", counted (PROTOCOL.md "Measures"): no judgement, only counts per arm.

    .venv/bin/python -m experiment.simplicity

What every arm shares is left out: fetching, the ticket store (one markdown file per ticket, which every arm
reads to show tickets to Claude), the question and answer format, the runner. What is counted is what each arm
adds on top to find tickets:
  lines         its own code, non-blank and non-comment, docstrings excluded (functions picked by name)
  dependencies  direct third-party packages and binaries it needs
  artifacts     model and index files it must build and keep, with their size on disk
  build steps   from fetched data to its first answer, with the measured time of each
  gpu           whether building it needs a GPU in practice
  on a new ticket  what must happen before the ticket can be found
"""
import ast
import io
import json
import textwrap
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def code_lines(src: str) -> int:
    """Non-blank lines that are not comments or docstrings."""
    tree = ast.parse(src)
    doc_lines = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(getattr(first, "value", None), ast.Constant) \
                    and isinstance(first.value.value, str):
                doc_lines.update(range(first.lineno, first.end_lineno + 1))
    keep = set()
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type in (tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT,
                        tokenize.ENDMARKER):
            continue
        for ln in range(tok.start[0], tok.end[0] + 1):
            if ln not in doc_lines:
                keep.add(ln)
    return len(keep)


def functions(path: Path, names: set) -> int:
    src = path.read_text()
    lines = src.splitlines()
    total = 0
    for node in ast.parse(src).body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names:
            total += code_lines("\n".join(lines[node.lineno - 1:node.end_lineno]) + "\n")
    return total


def functions_in_class(path: Path, cls: str, names: set) -> int:
    src = path.read_text()
    lines = src.splitlines()
    for node in ast.parse(src).body:
        if isinstance(node, ast.ClassDef) and node.name == cls:
            return sum(code_lines(textwrap.dedent("\n".join(lines[f.lineno - 1:f.end_lineno])) + "\n")
                       for f in node.body if isinstance(f, ast.FunctionDef) and f.name in names)
    return 0


def whole(path: Path) -> int:
    return code_lines(path.read_text())


def size_mb(p: Path) -> float:
    if p.is_file():
        return round(p.stat().st_size / 2**20, 1)
    return round(sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) / 2**20, 1)


def main() -> None:
    E = ROOT / "experiment"
    P = E / "pipeline"
    rg_tool = functions(E / "agent_tools.py", {"rg", "rg_search", "where", "path_to_id", "title_of"})
    read_tool = functions(E / "agent_tools.py", {"read"})
    sem_tool = functions(E / "agent_tools.py", {"semantic"})
    tool_glue = functions(E / "agent_tools.py", {"timed", "log"})
    pipeline_core = sum(whole(P / f) for f in ("config.py", "train.py", "retrieve.py", "serve.py"))
    hybrid_only = functions_in_class(P / "retrieve.py", "Retriever", {"hybrid_candidates"})
    pipeline_core -= hybrid_only  # F's fusion lives in the shared retriever; it is counted for F only
    keyword = whole(E / "keyword.py") + hybrid_only
    rec = json.loads((DATA / "models" / "train_record.json").read_text())
    idx = json.loads((DATA / "index" / "index-trained.json").read_text())
    kw = json.loads((DATA / "index" / "keyword-build.json").read_text())
    fresh = json.loads((ROOT / "runs" / "freshness.json").read_text())
    trained_mb = size_mb(DATA / "models" / "bge-base-tickets")
    emb_mb = size_mb(DATA / "index" / "emb-trained.npy")
    fts_mb = size_mb(DATA / "index" / "fts.sqlite")
    ml_deps = ["torch (ROCm or CUDA build)", "sentence-transformers", "transformers", "datasets", "accelerate", "numpy"]
    embed_steps = [f"mine negatives and train the embedding ({rec['total_seconds'] / 60:.0f} min, GPU)",
                   f"embed every ticket ({idx['seconds'] / 60:.0f} min, GPU)",
                   "start the retrieval service (loads the model)"]
    new_embed = (f"embed it with the trained model and add it to the matrix (median "
                 f"{fresh['embedding_add_one']['median_ms']} ms, model loaded); the model itself learns it only "
                 f"at the next retrain ({rec['train_seconds'] / 60:.0f} min)")
    arms = {
        "B": {"lines": rg_tool + read_tool + tool_glue, "dependencies": ["ripgrep (binary)", "mcp"],
              "artifacts": {}, "build_steps": ["none: the ticket store is the index"], "gpu": False,
              "on_new_ticket": f"write its file (median {fresh['ripgrep_write_file']['median_ms']} ms)"},
        "A": {"lines": pipeline_core, "dependencies": ml_deps,
              "artifacts": {"trained model": trained_mb, "embeddings": emb_mb},
              "build_steps": embed_steps, "gpu": True, "on_new_ticket": new_embed},
        "F": {"lines": pipeline_core + keyword, "dependencies": ml_deps + ["sqlite3 with FTS5 (stdlib)"],
              "artifacts": {"trained model": trained_mb, "embeddings": emb_mb, "keyword index": fts_mb},
              "build_steps": embed_steps + [f"build the keyword index ({kw['seconds']:.0f} s)"], "gpu": True,
              "on_new_ticket": new_embed + f"; insert it in the keyword index (median "
                                           f"{fresh['keyword_fts_insert']['median_ms']} ms)"},
        "C": {"lines": pipeline_core + sem_tool + read_tool + tool_glue, "dependencies": ml_deps + ["mcp"],
              "artifacts": {"trained model": trained_mb, "embeddings": emb_mb},
              "build_steps": embed_steps, "gpu": True, "on_new_ticket": new_embed},
        "D": {"lines": pipeline_core + rg_tool + sem_tool + read_tool + tool_glue,
              "dependencies": ml_deps + ["mcp", "ripgrep (binary)"],
              "artifacts": {"trained model": trained_mb, "embeddings": emb_mb},
              "build_steps": embed_steps, "gpu": True, "on_new_ticket": new_embed + "; write its file"},
    }
    for a in arms.values():
        a["n_dependencies"] = len(a["dependencies"])
        a["artifacts_mb"] = round(sum(a["artifacts"].values()), 1)
        a["n_build_steps"] = 0 if a["build_steps"][0].startswith("none") else len(a["build_steps"])
    b, A = arms["B"], arms["A"]
    holds = all(b[k] <= A[k] for k in ("lines", "n_dependencies", "artifacts_mb", "n_build_steps")) \
        and (not b["gpu"] or A["gpu"])
    out = {"arms": arms, "simpler_B_vs_A": "HOLDS" if holds else "no verdict (see counts)",
           "note": "the shared ticket store, fetch, prompt and runner are not counted for any arm"}
    (ROOT / "runs" / "simplicity.json").write_text(json.dumps(out, indent=1))
    for k, a in arms.items():
        print(f"{k}: lines {a['lines']}, deps {a['n_dependencies']}, artifacts {a['artifacts_mb']} MB, "
              f"build steps {a['n_build_steps']}, gpu {a['gpu']}")
    print("simpler (B vs A):", out["simpler_B_vs_A"])


if __name__ == "__main__":
    main()

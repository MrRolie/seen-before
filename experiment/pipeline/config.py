"""Arm A's settings, in one place. Paths are relative to the repo root."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUILT = ROOT / "data" / "built"
MODELS = ROOT / "data" / "models"
INDEX = ROOT / "data" / "index"

BASE_MODEL = "BAAI/bge-base-en-v1.5"
RERANKER = "BAAI/bge-reranker-v2-m3"           # off the shelf
RERANKER_BASE_FOR_TRAINING = "BAAI/bge-reranker-base"
TRAINED = MODELS / "bge-base-tickets"
TRAINED_RERANKER = MODELS / "reranker-tickets"

EMBED_CHARS = 4000     # title + description, before the model's own 512-token limit
MAX_SEQ = 512
CANDIDATES = 50        # first stage
RRF_K = 60             # reciprocal rank fusion constant (the usual default)
SERVE_PORT = 8765
TOP_K = 5              # what the model is shown
PAIRS_PER_CANONICAL = 10
SEED = 7


def embed_text(title: str, body: str) -> str:
    return f"{title}\n\n{body or ''}"[:EMBED_CHARS]


def rerank_text(title: str, body: str, view: str) -> str:
    """What a re-ranker sees of one ticket: the raw ticket, its title only, or its cleaned text."""
    if view == "title":
        return title
    if view == "clean":
        from experiment.pipeline.clean import clean
        return embed_text(title, clean(body))
    return embed_text(title, body)

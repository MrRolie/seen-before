"""What every arm shares: the ticket view, the prompt core, the answer format and its parser."""
import json
import re
from pathlib import Path

DOC_CHARS = 8000          # one past ticket as any arm sees it
QUESTION_CHARS = 8000     # the new ticket as the customer wrote it
MODEL = "claude-sonnet-5-5"

PRODUCTS = "Microsoft PowerToys, Windows Terminal and the Windows Subsystem for Linux (WSL)"

ANSWER_FORMAT = """Reply with one JSON object and nothing else:
{"seen_before": true or false,
 "matches": ["Repo#number", ...],
 "answer": "what the customer should do, in a few sentences"}
"matches" lists at most 3 past tickets that report the same problem, best first, written exactly as the
ticket ids appear (for example "PowerToys#31226"); empty if none does. Only cite tickets you were shown."""

SYSTEM_CORE = f"""You are a support engineer for {PRODUCTS}. A customer has just filed a new ticket.
Your only source is the archive of past tickets. Decide whether this exact problem has been reported before;
if it has, say which past tickets report it and tell the customer what to do, based on what those tickets
say. A ticket about a different problem in the same area is not a match."""


def doc_view(text: str) -> str:
    return text if len(text) <= DOC_CHARS else text[:DOC_CHARS] + "\n[... truncated]"


def question_view(q: dict) -> str:
    body = q["body"] or ""
    if len(body) > QUESTION_CHARS:
        body = body[:QUESTION_CHARS] + "\n[... truncated]"
    return f"New ticket ({q['repo']}), title: {q['title']}\n\n{body}"


ID_RE = re.compile(r"\b(PowerToys|terminal|WSL)#(\d+)\b")


def parse_answer(text: str) -> dict:
    """Take the last JSON object in the reply. A reply with none is a failed answer, recorded as such."""
    text = text or ""
    dec = json.JSONDecoder()
    for start in reversed([i for i, ch in enumerate(text) if ch == "{"]):
        try:
            d, _ = dec.raw_decode(text, start)
        except json.JSONDecodeError:
            continue
        if not isinstance(d, dict) or "matches" not in d:
            continue
        matches = [x for x in d.get("matches") or [] if isinstance(x, str) and ID_RE.fullmatch(x.strip())]
        return {"ok": True, "seen_before": bool(d.get("seen_before")),
                "matches": [x.strip() for x in matches][:3], "answer": str(d.get("answer", ""))}
    return {"ok": False, "seen_before": None, "matches": [], "answer": ""}


def load_jsonl(p) -> list:
    return [json.loads(l) for l in Path(p).read_text().splitlines() if l.strip()]

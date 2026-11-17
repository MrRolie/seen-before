"""The connector must run arm B as measured: same prompt, tool descriptions, search results and answer parsing.
Needs the built corpus (data/built/corpus) and the test run's records; skipped without them."""
import importlib.util
import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "data" / "built" / "corpus"
pytestmark = pytest.mark.skipif(not CORPUS.exists(), reason="no built corpus")
os.environ.setdefault("SB_CORPUS", str(CORPUS))

from experiment import agent_tools, common, run  # noqa: E402

spec = importlib.util.spec_from_file_location("sb", ROOT / "connector" / "seen_before.py")
sb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sb)
NAMES = ("PowerToys", "terminal", "WSL")


def test_system_prompt_is_arm_bs():
    assert sb.system_prompt(common.PRODUCTS, "PowerToys#31226") == run.SYSTEM_AGENT
    assert sb.MODEL == common.MODEL and sb.MAX_TURNS == run.MAX_TURNS


def test_tool_descriptions_are_arm_bs():
    assert sb.SEARCH_DOC.format(repos=sb.quoted(NAMES)) == agent_tools.RG_DOC
    assert sb.READ_DOC.format(example="WSL#1234") == agent_tools.read.__doc__


def test_question_view_matches():
    qs = common.load_jsonl(ROOT / "data" / "built" / "questions.jsonl")[:50]
    for q in qs:
        assert sb.question_view(q["repo"], q["title"], q["body"]) == common.question_view(q)


@pytest.mark.parametrize("pattern,regex,also,repo", [
    ("0x80070057", False, [], None), ("FancyZones", False, ["monitor"], "PowerToys"),
    ("wsl --install", False, [], "WSL"), (r"ctrl\+shift\+[a-z]", True, [], None), ("ssh", False, ["agent"], None)])
def test_search_results_match(pattern, regex, also, repo, monkeypatch):
    monkeypatch.delenv("SEEN_BEFORE_HIDE", raising=False)
    assert sb.rg_search(CORPUS, NAMES, pattern, regex, also, repo) == \
        agent_tools.rg_search(pattern, regex, also, repo)


def test_read_matches():
    for t in ("WSL#10000", "PowerToys#31226", "terminal#1"):
        assert sb.read_ticket(CORPUS, NAMES, t)[0] == agent_tools.read(t)


def test_parse_matches_on_every_recorded_answer():
    recs = sorted((ROOT / "runs" / "test" / "B").glob("*.json"))
    if not recs:
        pytest.skip("no test records")
    for f in recs:
        r = json.loads(f.read_text())
        assert sb.parse_answer(r["claude"].get("result", ""), NAMES) == common.parse_answer(r["claude"].get("result", ""))

"""The rules the result depends on: link extraction, strata, the citation rule, the answer parser."""
from corpus.build import EXACT_TOKEN_RE, LINK_RE
from experiment.common import parse_answer
from experiment.score import paired, score_one


def links(text):
    return [(m.group(1), m.group(2), m.group(3)) for m in LINK_RE.finditer(text)]


def test_link_forms():
    assert links("/dup #31226") == [("31226", None, None)]
    assert links("Thanks!\n/duplicate https://github.com/microsoft/WSL/issues/4103") == [(None, "WSL", "4103")]
    assert links("This is a duplicate of #50795.") == [("50795", None, None)]
    assert links("dupe of #12") == [("12", None, None)]


def test_link_ignores_mentions():
    assert links("see #123 for context") == []


def test_link_free_wording_and_negation():
    from corpus.build import link_of
    assert links("Closing as dupe of https://github.com/microsoft/PowerToys/issues/42386") == [(None, "PowerToys", "42386")]
    assert links("I'm closing this issue as a duplicate. See: https://github.com/microsoft/PowerToys/issues/44864#issue") \
        == [(None, "PowerToys", "44864")]
    issue = {"repo": "WSL"}
    assert link_of(issue, [C("m", "User", "MEMBER", "This is not a dup, #5 is different", "1")]) == (None, None)
    assert link_of(issue, [C("m", "User", "MEMBER", "Isn't a duplicate of #5", "1")]) == (None, None)


def test_strata():
    assert EXACT_TOKEN_RE.search("WslRegisterDistribution failed with error: 0x80370102")
    assert EXACT_TOKEN_RE.search("System.Runtime.InteropServices.COMException")
    assert EXACT_TOKEN_RE.search("Wsl/Service/CreateInstance/E_ACCESSDENIED")
    assert not EXACT_TOKEN_RE.search("FancyZones stops working after I unplug my monitor")


def test_parse_answer_takes_last_json_and_filters_ids():
    p = parse_answer('thinking... {"x":1} final: {"seen_before": true, "matches": ["WSL#1", "bogus", "PowerToys#2"], "answer": "a"}')
    assert p["ok"] and p["matches"] == ["WSL#1", "PowerToys#2"]
    assert not parse_answer("no json here")["ok"]


def rec(cited, shown, seen=True):
    return {"parsed": {"ok": True, "seen_before": seen, "matches": cited, "answer": ""}, "shown": shown,
            "claude": {}, "wall_seconds": 1.0, "retrieval_seconds": 0.1, "tool_calls": []}


Q = {"qid": "WSL#9", "kind": "seen", "stratum": "exact_token", "created_at": "2026-08-01", "answer": ["WSL#1", "WSL#2"]}


def test_hit_counts_any_cluster_member():
    s = score_one(Q, rec(["WSL#2"], ["WSL#2", "WSL#5"]))
    assert s["hit1"] == 1 and s["hit3"] == 1


def test_citation_from_memory_never_scores():
    s = score_one(Q, rec(["WSL#1"], ["WSL#5"]))
    assert s["hit1"] == 0 and s["hit3"] == 0 and s["cited_unseen"] == ["WSL#1"]


def test_hit3_but_not_hit1():
    s = score_one(Q, rec(["WSL#5", "WSL#1"], ["WSL#5", "WSL#1"]))
    assert s["hit1"] == 0 and s["hit3"] == 1


def test_new_question_false_match():
    nq = {**Q, "kind": "new", "answer": []}
    assert score_one(nq, rec([], [], seen=False))["false_match"] == 0
    assert score_one(nq, rec(["WSL#5"], ["WSL#5"], seen=True))["false_match"] == 1


def test_paired_verdicts():
    a = {str(i): {"hit1": 0} for i in range(50)}
    b = {str(i): {"hit1": 1} for i in range(50)}
    assert paired(a, b)["verdict"] == "HOLDS"
    assert paired(b, a)["verdict"] == "FAILS"
    assert paired(a, a)["verdict"] == "NOT SHOWN"


def test_link_forms_seen_in_the_repos():
    assert links("/dupe #41917") == [("41917", None, None)]
    assert links("Thanks! This is a /duplicate of #20444.") == [("20444", None, None)]
    assert links("looks like a /dup of #15936. thanks!") == [("15936", None, None)]
    assert links("/dup #10119 /dup #14185") == [("10119", None, None), ("14185", None, None)]


def C(user, typ, assoc, body, t):
    return {"user": user, "user_type": typ, "author_association": assoc, "body": body, "created_at": t}


def test_link_of_follows_the_bot_confirmation():
    from corpus.build import link_of
    issue = {"repo": "terminal"}
    cs = [C("u", "User", "NONE", "Possibly duplicate of #1", "1"),
          C("staff", "User", "NONE", "We're going to mark this as a /dup of #2", "2"),
          C("bot", "Bot", "CONTRIBUTOR", "Hi! We've identified this issue as a duplicate of another one", "3"),
          C("u", "User", "NONE", "no, dup of #3", "4")]
    assert link_of(issue, cs) == ("terminal#2", "2")


def test_link_of_without_bot_needs_a_maintainer():
    from corpus.build import link_of
    issue = {"repo": "WSL"}
    assert link_of(issue, [C("u", "User", "NONE", "dup of #5", "1")]) == (None, None)
    assert link_of(issue, [C("m", "User", "MEMBER", "/dupe https://github.com/microsoft/PowerToys/issues/9", "1")]) \
        == ("PowerToys#9", "1")

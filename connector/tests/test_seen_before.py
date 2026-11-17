"""Tests for the connector. Standalone: run with `python -m pytest connector/tests` (needs rg on PATH)."""
import importlib.util
import json
import os
import shutil
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("sb", Path(__file__).resolve().parents[1] / "seen_before.py")
sb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sb)

needs_rg = pytest.mark.skipif(not shutil.which("rg"), reason="ripgrep not installed")


def issue(n, title, body, created="2024-01-02T00:00:00Z", updated=None, pr=False):
    d = {"number": n, "title": title, "body": body, "created_at": created, "updated_at": updated or created}
    if pr:
        d["pull_request"] = {}
    return d


def comment(cid, n, body, user="alice", at="2024-01-03T00:00:00Z"):
    return {"id": cid, "issue_url": f"https://api.github.com/repos/o/Proj/issues/{n}", "user": {"login": user},
            "created_at": at, "updated_at": at, "body": body}


class FakeAPI:
    """Serves list endpoints with a page cap, like GitHub's silent truncation."""

    def __init__(self, issues, comments, page=2, cap=2):
        self.data = {"issues?": issues, "comments?": comments}
        self.page, self.cap, self.calls = page, cap, 0

    def get(self, url, tok):
        self.calls += 1
        kind = "comments?" if "/issues/comments?" in url else "issues?"
        since = url.split("since=")[1].split("&")[0] if "since=" in url else ""
        items = sorted((i for i in self.data[kind] if i["updated_at"] >= since), key=lambda i: i["updated_at"])
        pg = int(url.split("&pg=")[1]) if "&pg=" in url else 0
        chunk = items[pg * self.page:(pg + 1) * self.page]
        more = (pg + 1) * self.page < len(items) and pg + 1 < self.cap
        return chunk, (url.split("&pg=")[0] + f"&pg={pg + 1}") if more else None


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("SEEN_BEFORE_STORE", str(tmp_path))
    monkeypatch.delenv("SEEN_BEFORE_HIDE", raising=False)
    (tmp_path / "config.json").write_text(json.dumps({"repos": ["o/Proj"], "products": "Proj"}))
    return tmp_path


def test_update_survives_page_cap_and_skips_prs(store, monkeypatch):
    issues = [issue(n, f"t{n}", f"b{n}", updated=f"2024-01-{n:02d}T00:00:00Z") for n in range(1, 10)]
    issues.append(issue(10, "a pr", "x", updated="2024-01-10T00:00:00Z", pr=True))
    comments = [comment(100 + n, n, f"c{n}", at=f"2024-02-{n:02d}T00:00:00Z") for n in range(1, 10)]
    comments.append(comment(200, 10, "on the pr", at="2024-02-10T00:00:00Z"))
    api = FakeAPI(issues, comments)
    monkeypatch.setattr(sb, "get", api.get)
    n = sb.update_repo(store, "o/Proj", "Proj", "tok")
    assert n == 9 and sorted(int(p.stem) for p in (store / "Proj").glob("*.md")) == list(range(1, 10))
    assert "--- comment by alice (2024-02-05)\nc5" in (store / "Proj" / "5.md").read_text()
    assert not (store / "Proj" / "10.md").exists()


def test_update_is_incremental_and_takes_edits(store, monkeypatch):
    api = FakeAPI([issue(1, "old", "first")], [comment(5, 1, "hello")])
    monkeypatch.setattr(sb, "get", api.get)
    sb.update_repo(store, "o/Proj", "Proj", "tok")
    api.data["issues?"] = [issue(1, "renamed", "first", updated="2024-03-01T00:00:00Z"),
                           issue(2, "new", "second", updated="2024-03-02T00:00:00Z")]
    api.data["comments?"] = [comment(5, 1, "hello, edited", at="2024-03-03T00:00:00Z")]
    sb.update_repo(store, "o/Proj", "Proj", "tok")
    one = (store / "Proj" / "1.md").read_text()
    assert one.startswith("# Proj#1: renamed") and "hello, edited" in one and one.count("--- comment") == 1
    assert (store / "Proj" / "2.md").exists()


def write(store, n, title, body, label="Proj"):
    (store / label).mkdir(exist_ok=True)
    (store / label / f"{n}.md").write_text(sb.render(f"{label}#{n}", {"title": title, "body": body,
                                                                    "created_at": "2024-01-01"}, []))


@needs_rg
def test_search_hides_the_issue_under_check(store, monkeypatch):
    write(store, 1, "Crash on start", "error 0x80070057 when starting")
    write(store, 2, "Crash again", "0x80070057 0x80070057")
    names = ("Proj",)
    assert [h["id"] for h in sb.rg_search(store, names, "0x80070057", False, [], None)] == ["Proj#2", "Proj#1"]
    monkeypatch.setenv("SEEN_BEFORE_HIDE", "Proj#2")
    assert [h["id"] for h in sb.rg_search(store, names, "0x80070057", False, [], None)] == ["Proj#1"]
    assert sb.read_ticket(store, names, "Proj#2")[1] == []
    assert sb.read_ticket(store, names, "Proj#1")[1] == ["Proj#1"]


@needs_rg
def test_search_ignores_store_internals(store):
    write(store, 1, "x", "needle")
    (store / ".raw" / "Proj").mkdir(parents=True)
    (store / ".raw" / "Proj" / "1.json").write_text('{"needle": 1}')
    assert [h["id"] for h in sb.rg_search(store, ("Proj",), "needle", False, [], None)] == ["Proj#1"]


def test_parse_answer_takes_last_object_and_known_repos():
    t = 'thinking {"a": 1}\n```json\n{"seen_before": true, "matches": ["Proj#3", "Other#4", "Proj#x"], "answer": "a"}\n```'
    p = sb.parse_answer(t, ("Proj",))
    assert p["ok"] and p["matches"] == ["Proj#3"]
    assert not sb.parse_answer("no json here", ("Proj",))["ok"]


def test_finish_keeps_only_shown_tickets(store, tmp_path):
    write(store, 3, "Shown one", "x")
    log = tmp_path / "log.jsonl"
    log.write_text(json.dumps({"tool": "search", "returned": ["Proj#3"], "seconds": 0}) + "\n")
    v = sb.finish({"result": '{"seen_before": true, "matches": ["Proj#9", "Proj#3"], "answer": ""}'},
                  log, ("Proj",), store)
    assert [m["id"] for m in v["matches"]] == ["Proj#3"] and v["seen_before"]
    v = sb.finish({"result": '{"seen_before": true, "matches": ["Proj#9"], "answer": ""}'}, log, ("Proj",), store)
    assert not v["seen_before"]


def test_comment_is_built_from_ids_and_neutralised_titles():
    v = {"seen_before": True, "matches": [{"id": "Proj#3", "title": "ping @everyone see https://evil.example o/x#9 `rm`\nline"},
                                          {"id": "Lib#4", "title": "t"}]}
    c = sb.comment_markdown(v, {"Proj": "o/Proj", "Lib": "o/Lib"}, "o/Proj")
    rows = c.split("\n\n")[1].split("\n")
    # every title sits whole inside one code span: no links, mentions or cross-references from it
    assert rows[0] == "- #3 `ping @everyone see https://evil.example o/x#9  rm  line`"
    assert rows[1] == "- o/Lib#4 `t`"
    assert sb.MARK in c
    assert sb.comment_markdown({"seen_before": False, "matches": []}, {}, "o/Proj") == ""


def test_prompt_and_call_shape(store, tmp_path):
    call = sb.prepare(store, "Proj", ("Proj",), "Proj", 7, "T", "B" * 9000, tmp_path / "l")
    a = call["args"]
    assert a[a.index("--tools") + 1] == "" and a[a.index("--max-turns") + 1] == "20"
    assert call["mcp_config"]["mcpServers"]["archive"]["env"]["SEEN_BEFORE_HIDE"] == "Proj#7"
    assert call["prompt"].startswith("New ticket (Proj), title: T") and call["prompt"].endswith("[... truncated]")


@needs_rg
def test_check_end_to_end_with_a_fake_claude(store, tmp_path, monkeypatch):
    """The CLI, the MCP server and the comment, wired together; the model is replaced by a script."""
    import subprocess
    import sys
    write(store, 1, "Zebra crash", "zebra crash on start")
    write(store, 2, "Zebra crash on boot", "zebra crash zebra")   # the issue under check, already mirrored
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    fake = bin_ / "claude"
    report = tmp_path / "report.json"
    # check passes the agent only a minimal environment, so the fake's settings travel in its wrapper
    fake.write_text(f"#!/bin/sh\nFAKE_CLAUDE_REPORT={report} FAKE_CLAUDE_MODE=$(cat {bin_}/mode 2>/dev/null || echo ok) "
                    f"exec {sys.executable} {Path(__file__).parent / 'fake_claude.py'} \"$@\"\n")
    fake.chmod(0o755)
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"repository": {"full_name": "o/Proj"},
                                 "issue": {"number": 2, "title": "Zebra crash on boot", "body": "zebra",
                                           "created_at": "2024-01-05T00:00:00Z"}}))
    env = {**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}", "SEEN_BEFORE_STORE": str(store),
           "ANTHROPIC_BASE_URL": "http://elsewhere", "MAX_THINKING_TOKENS": "9"}
    out = subprocess.run([sys.executable, str(Path(sb.__file__)), "check", "--event", str(event),
                          "--comment-out", str(tmp_path / "c.md")], capture_output=True, text=True, env=env,
                         timeout=120)
    assert out.returncode == 0, out.stderr
    seen = json.loads(report.read_text())
    assert seen["tools"] == ["read", "search"] and seen["hits"] == ["Proj#1"]   # #2 hidden from itself
    assert seen["env"] == {"CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1", "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"}
    assert "ANTHROPIC_BASE_URL" not in seen["env_keys"] and "MAX_THINKING_TOKENS" not in seen["env_keys"]
    comment = (tmp_path / "c.md").read_text()
    assert "- #1 `Zebra crash`" in comment and "#999" not in comment and "@here" not in comment
    # A failed agent is a failed run, never "not seen before".
    for mode in ("api_error", "server_down"):
        (tmp_path / "c.md").unlink(missing_ok=True)
        (bin_ / "mode").write_text(mode)
        bad = subprocess.run([sys.executable, str(Path(sb.__file__)), "check", "--event", str(event),
                              "--comment-out", str(tmp_path / "c.md")], capture_output=True, text=True,
                             env=env, timeout=120)
        assert bad.returncode != 0 and "did not run as measured" in bad.stderr, (mode, bad.stderr)
        assert not (tmp_path / "c.md").exists()


def test_check_run_refuses_wrong_tools():
    init = {"tools": ["mcp__archive__search", "mcp__archive__read", "Bash"],
            "mcp_servers": [{"name": "archive", "status": "connected"}]}
    with pytest.raises(sb.AgentFailed):
        sb.check_run(0, init, {"result": "{}"}, sb.TOOLS)
    init["tools"] = sb.TOOLS
    assert sb.check_run(0, init, {"result": "{}"}, sb.TOOLS) == {"result": "{}"}
    with pytest.raises(sb.AgentFailed):
        sb.check_run(0, init, {"is_error": True, "subtype": "success", "result": "x"}, sb.TOOLS)
    assert sb.check_run(0, init, {"is_error": True, "subtype": "error_max_turns"}, sb.TOOLS)


def test_mirror_failure_part_way_loses_nothing(store, monkeypatch):
    issues = [issue(n, f"t{n}", f"b{n}", updated=f"2024-01-{n:02d}T00:00:00Z") for n in range(1, 6)]
    api = FakeAPI(issues, [comment(9, 1, "c")])
    real = api.get

    def failing(url, tok):
        if "/issues/comments?" in url:
            raise RuntimeError("giving up")
        return real(url, tok)
    monkeypatch.setattr(sb, "get", failing)
    with pytest.raises(RuntimeError):
        sb.update_repo(store, "o/Proj", "Proj", "tok")
    assert sorted(int(p.stem) for p in (store / "Proj").glob("*.md")) == [1, 2, 3, 4, 5]
    monkeypatch.setattr(sb, "get", real)
    sb.update_repo(store, "o/Proj", "Proj", "tok")
    assert "--- comment by alice" in (store / "Proj" / "1.md").read_text()


def test_own_comments_are_not_mirrored(store, monkeypatch):
    api = FakeAPI([issue(1, "t", "b")], [comment(5, 1, sb.comment_markdown(
        {"seen_before": True, "matches": [{"id": "Proj#2", "title": "x"}]}, {"Proj": "o/Proj"}, "o/Proj")),
        comment(6, 1, "a person who likes seen-before: it works")])
    monkeypatch.setattr(sb, "get", api.get)
    sb.update_repo(store, "o/Proj", "Proj", "tok")
    t = (store / "Proj" / "1.md").read_text()
    assert "a person who likes" in t and "reported before" not in t


@needs_rg
def test_later_issues_are_hidden(store, monkeypatch):
    for n, day in ((1, "2024-01-01"), (5, "2024-02-01"), (9, "2024-03-01")):
        (store / "Proj").mkdir(exist_ok=True)
        (store / "Proj" / f"{n}.md").write_text(sb.render(f"Proj#{n}", {"title": "x", "body": "needle",
                                                                       "created_at": day}, []))
    monkeypatch.setenv("SEEN_BEFORE_BEFORE", "Proj#5@2024-02-01")
    assert [h["id"] for h in sb.rg_search(store, ("Proj",), "needle", False, [], None)] == ["Proj#1"]
    assert sb.read_ticket(store, ("Proj",), "Proj#9")[1] == []


def test_update_lock_lets_one_run(store, monkeypatch, capsys):
    import fcntl
    (store / ".state").mkdir()
    held = open(store / ".state" / "update.lock", "w")
    fcntl.flock(held, fcntl.LOCK_EX)
    monkeypatch.setattr(sb, "token", lambda: pytest.fail("a second update ran"))
    sb.cmd_update(type("A", (), {"if_older": 0, "background": False})())
    assert "another update is running" in capsys.readouterr().err

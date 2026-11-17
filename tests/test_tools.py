"""The ripgrep tool: counts with a single matching file, broad patterns, `also`, ranking, bad input."""
import importlib
import os


def tools(tmp_path):
    for repo, num, text in [("WSL", 1, "# WSL#1: boot fails\nerror 0x80370102\n0x80370102 again\n"),
                            ("WSL", 2, "# WSL#2: dns\nresolv.conf 0x80370102\n"),
                            ("PowerToys", 3, "# PowerToys#3: crash\nCOMException\n")]:
        (tmp_path / repo).mkdir(exist_ok=True)
        (tmp_path / repo / f"{num}.md").write_text(text)
    os.environ["SB_CORPUS"] = str(tmp_path)
    os.environ["SB_BACKEND"] = "rg"
    os.environ.pop("SB_TOOL_LOG", None)
    import experiment.agent_tools as t
    return importlib.reload(t)


def test_single_file_count_is_not_zero(tmp_path):
    t = tools(tmp_path)
    hits = t.rg_search("COMException", False, [], None)
    assert [h["id"] for h in hits] == ["PowerToys#3"] and hits[0]["matching_lines"] == 1


def test_ranked_by_matching_lines(tmp_path):
    t = tools(tmp_path)
    hits = t.rg_search("0x80370102", False, [], "WSL")
    assert [h["id"] for h in hits] == ["WSL#1", "WSL#2"] and hits[0]["lines"]


def test_also_intersects(tmp_path):
    t = tools(tmp_path)
    assert [h["id"] for h in t.rg_search("0x80370102", False, ["resolv"], None)] == ["WSL#2"]


def test_broad_pattern_and_errors(tmp_path):
    t = tools(tmp_path)
    assert len(t.rg_search("#", False, [], None)) == 3
    assert "error" in __import__("json").loads(t.timed("search", {}, lambda: t.rg_search("(", True, [], None)))
    assert "error" in __import__("json").loads(t.timed("search", {}, lambda: t.rg_search("x", False, [], "nope")))

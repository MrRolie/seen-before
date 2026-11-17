"""The sterility guards refuse what would leak."""
import pytest

from experiment import sterile


def test_base_inside_home_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    with pytest.raises(sterile.Contaminated):
        sterile.check_base(tmp_path / "x")


def test_base_with_context_file_above_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", "/nonexistent-home")
    (tmp_path / "CLAUDE.md").write_text("doctrine")
    (tmp_path / "sub").mkdir()
    with pytest.raises(sterile.Contaminated):
        sterile.check_base(tmp_path / "sub")


def test_init_check():
    clean = {"tools": ["mcp__archive__search", "mcp__archive__read"], "mcp_servers": [{"name": "archive"}],
             "skills": [], "slash_commands": [], "plugins": [{"source": "cc-plugin-privacy@builtin"}]}
    assert sterile.check_init(clean, {"mcp__archive__search", "mcp__archive__read"}, {"archive"}) == []
    assert sterile.check_init({**clean, "tools": clean["tools"] + ["Bash"]},
                              {"mcp__archive__search", "mcp__archive__read"}, {"archive"})
    assert sterile.check_init({**clean, "plugins": [{"source": "someplugin@local"}]},
                              {"mcp__archive__search", "mcp__archive__read"}, {"archive"})
    assert sterile.check_init({**clean, "skills": ["x"]}, {"mcp__archive__search", "mcp__archive__read"}, {"archive"})
    assert sterile.check_init({**clean, "mcp_servers": [{"name": "archive"}, {"name": "other"}]},
                              {"mcp__archive__search", "mcp__archive__read"}, {"archive"})

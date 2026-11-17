"""Run `claude -p` so the model sees only what the experiment gives it.

By default Claude Code loads the user's own context: their CLAUDE.md, memory, agents, commands, plugins, hooks,
settings, and environment variables of any parent session. An experiment that compares approaches must not let
any of that reach the model. So every subject call gets:
  - a fresh config home holding nothing but a copy of the account credentials (mode 0600, removed afterwards)
    and an account record with the person's email, names and organisation blanked. Claude Code otherwise attaches
    the account email to every conversation, and rewrites it from its profile fetch during each run, so the
    blanked record is written fresh before every call (from a template warmed once per process);
  - HOME pointing at that fresh home, and an environment reduced to PATH, LANG and the config dir;
  - a working directory under a neutral base, with no CLAUDE.md, .claude or .git in it or above it;
  - the CLI's own init event checked against what the arm should have; any extra tool, server, plugin, skill
    or agent refuses the call (`Contaminated`).
Credentials are read from $SB_CLAUDE_CREDENTIALS, else <CLAUDE_CONFIG_DIR or ~/.claude>/.credentials.json.
"""
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

NEUTRAL_BASE = Path(os.environ.get("SB_STERILE_BASE", "/var/tmp"))
CONTEXT_FILES = ("CLAUDE.md", "CLAUDE.local.md", ".claude", ".git")
BUILTIN_PLUGIN_SUFFIX = "@builtin"


class Contaminated(Exception):
    pass


def credentials_path() -> Path:
    if os.environ.get("SB_CLAUDE_CREDENTIALS"):
        return Path(os.environ["SB_CLAUDE_CREDENTIALS"])
    base = os.environ.get("CLAUDE_CONFIG_DIR") or str(Path.home() / ".claude")
    return Path(base) / ".credentials.json"


def check_base(base: Path) -> None:
    home = Path.home().resolve()
    b = base.resolve()
    if b == home or home in b.parents:
        raise Contaminated(f"sterile base {b} is inside $HOME")
    for d in [b, *b.parents]:
        for name in CONTEXT_FILES:
            if (d / name).exists():
                raise Contaminated(f"{d / name} would be read by the CLI")


ACCOUNT_FIELDS = ("emailAddress", "fullName", "displayName", "organizationName")
_template = None


def account_template(claude: str, env: dict, cwd: Path) -> dict:
    """One trivial call in a throwaway home to let the CLI write its config, then blank the person's fields."""
    global _template
    if _template is None:
        subprocess.run([claude, "-p", "--setting-sources", "", "--tools", "", "--strict-mcp-config",
                        "--no-session-persistence", "--output-format", "json"], input="Reply with OK.",
                       capture_output=True, text=True, cwd=cwd, env=env, timeout=300)
        g = json.loads((Path(env["CLAUDE_CONFIG_DIR"]) / ".claude.json").read_text())
        for k in ACCOUNT_FIELDS:
            if k in (g.get("oauthAccount") or {}):
                g["oauthAccount"][k] = ""
        _template = g
    return _template


class SterileHome:
    """A throwaway config home and working directory for one subject call."""

    def __enter__(self):
        check_base(NEUTRAL_BASE)
        self.root = Path(tempfile.mkdtemp(prefix="subject-", dir=NEUTRAL_BASE))
        self.home = self.root / "home"
        self.cwd = self.root / "work"
        cfg = self.home / ".claude"
        cfg.mkdir(parents=True)
        self.cwd.mkdir()
        creds = cfg / ".credentials.json"
        shutil.copyfile(credentials_path(), creds)
        os.chmod(creds, 0o600)
        self.claude = shutil.which("claude")
        if not self.claude:
            raise Contaminated("no claude on PATH")
        self.env = {"PATH": f"{Path(self.claude).parent}:/usr/local/bin:/usr/bin:/bin", "HOME": str(self.home),
                    "LANG": "C.UTF-8", "CLAUDE_CONFIG_DIR": str(cfg), "TMPDIR": str(self.root)}
        template = account_template(self.claude, self.env, self.cwd)
        (cfg / ".claude.json").write_text(json.dumps(template))
        return self

    def __exit__(self, *exc):
        shutil.rmtree(self.root, ignore_errors=True)


def check_init(init: dict, expect_tools: set, expect_servers: set) -> list:
    """What the CLI says it loaded, against what the arm should have. Returns the reasons to refuse."""
    bad = []
    tools = set(init.get("tools") or [])
    if tools != expect_tools:
        bad.append(f"tools {sorted(tools)} != {sorted(expect_tools)}")
    servers = {s.get("name") for s in init.get("mcp_servers") or []}
    if servers != expect_servers:
        bad.append(f"mcp servers {sorted(servers)} != {sorted(expect_servers)}")
    for key in ("skills", "slash_commands"):
        if init.get(key):
            bad.append(f"{key} not empty: {init[key]}")
    plugins = [p.get("source", "") for p in init.get("plugins") or []]
    extra = [p for p in plugins if not p.endswith(BUILTIN_PLUGIN_SUFFIX)]
    if extra:
        bad.append(f"non-builtin plugins: {extra}")
    return bad


def run(cmd_args: list, prompt: str, expect_tools: set, expect_servers: set, timeout: int,
        mcp_config: dict | None = None) -> tuple[dict, dict, float]:
    """Run one sterile `claude -p ... --output-format stream-json`; return (result event, init event, seconds)."""
    import time
    with SterileHome() as h:
        args = [h.claude, "-p", *cmd_args, "--output-format", "stream-json", "--verbose"]
        if mcp_config is not None:
            p = h.root / "mcp.json"
            p.write_text(json.dumps(mcp_config))
            args += ["--mcp-config", str(p)]
        t0 = time.perf_counter()
        proc = subprocess.run(args, input=prompt, capture_output=True, text=True, cwd=h.cwd, env=h.env,
                              timeout=timeout)
        wall = time.perf_counter() - t0
    init, result = {}, {}
    for line in proc.stdout.splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "system" and ev.get("subtype") == "init":
            init = ev
        elif ev.get("type") == "result":
            result = ev
    if not init:
        raise RuntimeError(f"no init event (exit {proc.returncode}): {proc.stderr[-300:]}")
    reasons = check_init(init, expect_tools, expect_servers)
    if reasons:
        raise Contaminated("; ".join(reasons))
    keep = {k: init.get(k) for k in ("model", "tools", "mcp_servers", "plugins", "skills", "agents",
                                     "claude_code_version", "apiKeySource", "cwd")}
    return result, keep, wall

#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp==2.3.0"]
# ///
"""seen-before: has this issue been reported before? An agent answers by searching your past issues with ripgrep.

    seen_before.py init owner/repo [owner/repo ...] [--products "..."]   configure the store
    seen_before.py update [--if-older SECONDS] [--background]           mirror new and changed issues
    seen_before.py check REF | --event FILE [--comment-out FILE]        ask the agent about one issue
    seen_before.py serve                                                MCP server: `search` and `read`

The store ($SEEN_BEFORE_STORE, default ~/.cache/seen-before) holds one markdown
file per issue, <store>/<Repo>/<number>.md, and that is the whole index. `check` runs the measured agent: headless
Claude Code with a fixed system prompt, no built-in tools, and only this file's `search` and `read`, at most 20
turns. The issue under check is hidden from both tools. Needs `rg` (ripgrep) and `claude` on PATH; `serve` needs
the `mcp` package (uv installs it from the header above). GitHub access: $GITHUB_TOKEN, else `gh auth token`.
"""
import argparse
import http.client
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.github.com"
MODEL = os.environ.get("SEEN_BEFORE_MODEL", "claude-sonnet-5-5")  # the measured model; change it and it is not
MARK = "<sub>seen-before: an AI agent"  # opens the footer of every comment the Action posts
MAX_TURNS = 20
MAX_HITS = 20
LINES_PER_HIT = 3
LINE_CHARS = 200
DOC_CHARS = 8000
QUESTION_CHARS = 8000


# ---------------------------------------------------------------- store and configuration

def store_dir() -> Path:
    s = os.environ.get("SEEN_BEFORE_STORE")
    return Path(s) if s else Path.home() / ".cache" / "seen-before"


def load_config(store: Path) -> dict:
    p = store / "config.json"
    if not p.exists():
        sys.exit(f"no {p}: run `seen_before.py init owner/repo` first")
    return json.loads(p.read_text())


def labels(cfg: dict) -> dict:
    """Ticket ids are <repo name>#<number>, as in the experiment; repo names must be unique."""
    out = {r.split("/")[1]: r for r in cfg["repos"]}
    if len(out) != len(cfg["repos"]):
        sys.exit("two configured repos share a name")
    return out


# ---------------------------------------------------------------- mirror (GitHub REST, standard library only)

def token() -> str:
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if tok:
        return tok
    try:
        return subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        sys.exit("set GITHUB_TOKEN (or log in with the gh CLI)")


def get(url: str, tok: str):
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "seen-before"})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                if int(r.headers.get("X-RateLimit-Remaining", "1")) < 20:
                    wait = int(r.headers.get("X-RateLimit-Reset", "0")) - time.time() + 5
                    print(f"rate limit low, sleeping {wait:.0f}s", file=sys.stderr)
                    time.sleep(max(wait, 0))
                return json.load(r), next_link(r.headers.get("Link", ""))
        except urllib.error.HTTPError as e:
            if e.code in (403, 429, 500, 502, 503, 504):
                time.sleep(2 ** attempt * 5)
                continue
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException):
            time.sleep(2 ** attempt * 5)
    raise RuntimeError(f"giving up on {url}")


def next_link(header: str):
    for part in header.split(","):
        if 'rel="next"' in part:
            return part.split(";")[0].strip()[1:-1]
    return None


def rounds(url0: str, since: str | None, tok: str, key, take) -> str | None:
    """GitHub's list endpoints stop after a fixed number of pages with no error and no next link. So list in
    rounds sorted by update time, each `since` the last update of the round before, until a round brings
    nothing new. Returns the newest update time seen."""
    held = set()
    while True:
        url = url0 + (f"&since={since}" if since else "")
        new = 0
        while url:
            items, url = get(url, tok)
            for it in items:
                k = key(it)
                if k not in held:
                    held.add(k)
                    new += 1
                    take(it)
                since = max(since or "", it.get("updated_at") or "")
        if not new:
            return since


def render(tid: str, issue: dict, comments: list) -> str:
    out = [f"# {tid}: {issue['title']}", f"created: {issue['created_at'][:10]}", "", (issue["body"] or "").strip(), ""]
    for c in sorted(comments, key=lambda c: c["created_at"]):
        out += [f"--- comment by {c['user']} ({c['created_at'][:10]})", (c["body"] or "").strip(), ""]
    return "\n".join(out)


def update_repo(store: Path, repo: str, label: str, tok: str) -> int:
    raw = store / ".raw" / label
    raw.mkdir(parents=True, exist_ok=True)
    state_p = store / ".state" / f"{repo.replace('/', '__')}.json"
    state_p.parent.mkdir(exist_ok=True)
    state = json.loads(state_p.read_text()) if state_p.exists() else {}
    out = store / label
    out.mkdir(exist_ok=True)
    touched = set()

    def save(num: int, p: Path, r: dict) -> None:
        """The raw record and the issue's file are written together, before the round's `since` is saved, so a
        run that fails part-way loses nothing: the next run starts from the last saved `since`."""
        p.write_text(json.dumps(r))
        if r["issue"]:
            (out / f"{num}.md").write_text(render(f"{label}#{num}", r["issue"], list(r["comments"].values())))
            touched.add(num)

    def rec(num: int) -> tuple[Path, dict]:
        p = raw / f"{num}.json"
        return p, (json.loads(p.read_text()) if p.exists() else {"issue": None, "comments": {}})

    def take_issue(i: dict) -> None:
        if "pull_request" in i:
            return
        p, r = rec(i["number"])
        r["issue"] = {"title": i["title"], "body": i.get("body") or "", "created_at": i["created_at"]}
        save(i["number"], p, r)

    def take_comment(c: dict) -> None:
        if MARK in (c.get("body") or ""):
            return  # our own comments: the archive must not learn from the agent's earlier guesses
        num = int(c["issue_url"].rsplit("/", 1)[1])
        p, r = rec(num)
        r["comments"][str(c["id"])] = {"user": (c.get("user") or {}).get("login"), "created_at": c["created_at"],
                                       "body": c.get("body") or ""}
        save(num, p, r)  # pull-request comments leave a stub with no issue; it is never rendered

    state["issues"] = rounds(f"{API}/repos/{repo}/issues?state=all&sort=updated&direction=asc&per_page=100",
                             state.get("issues"), tok, lambda i: (i["number"], i["updated_at"]), take_issue)
    state_p.write_text(json.dumps(state))
    state["comments"] = rounds(f"{API}/repos/{repo}/issues/comments?sort=updated&direction=asc&per_page=100",
                               state.get("comments"), tok, lambda c: (c["id"], c["updated_at"]), take_comment)
    state_p.write_text(json.dumps(state))
    return len(touched)


def cmd_update(a) -> None:
    store = store_dir()
    stamp = store / ".state" / "updated"
    if not (store / "config.json").exists() and a.background:
        return  # not set up yet: nothing to refresh
    if a.if_older and stamp.exists() and time.time() - stamp.stat().st_mtime < a.if_older:
        return
    if a.background:
        log = open(store / ".state" / "update.log", "a") if (store / ".state").exists() else subprocess.DEVNULL
        subprocess.Popen([sys.executable, __file__, "update"], stdout=log, stderr=log, start_new_session=True,
                         stdin=subprocess.DEVNULL)
        return
    cfg = load_config(store)
    (store / ".state").mkdir(exist_ok=True)
    lock = open(store / ".state" / "update.lock", "w")  # held until the process ends
    import fcntl  # Unix only, like the rest of the mirror
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("another update is running", file=sys.stderr)
        return
    tok = token()
    for label, repo in labels(cfg).items():
        print(f"{repo}: {update_repo(store, repo, label, tok)} issues written", file=sys.stderr)
    stamp.parent.mkdir(exist_ok=True)
    stamp.touch()


def cmd_init(a) -> None:
    store = store_dir()
    store.mkdir(parents=True, exist_ok=True)
    cfg = {"repos": a.repos, "products": a.products or ", ".join(a.repos)}
    labels(cfg)
    (store / "config.json").write_text(json.dumps(cfg, indent=1))
    print(f"configured {store}; now run `seen_before.py update` (a first mirror of a large repo takes a while)")


# ---------------------------------------------------------------- the agent's tools (arm B of the experiment)

def hidden() -> set:
    return {h.strip() for h in os.environ.get("SEEN_BEFORE_HIDE", "").split(",") if h.strip()}


def created(root: Path, tid: str) -> str:
    repo, num = tid.split("#")
    with open(root / repo / f"{num}.md") as f:
        f.readline()
        return f.readline().strip().removeprefix("created: ")


def visible(root: Path, tid: str) -> bool:
    """Hidden: the issue under check, and anything filed after it ($SEEN_BEFORE_BEFORE = "Repo#N@YYYY-MM-DD"):
    a later day, or a higher number in the same repository. Answers come only from what existed before."""
    if tid in hidden():
        return False
    cut = os.environ.get("SEEN_BEFORE_BEFORE")
    if not cut:
        return True
    q, day = cut.split("@")
    qrepo, qnum = q.split("#")
    repo, num = tid.split("#")
    if repo == qrepo and int(num) >= int(qnum):
        return False
    return created(root, tid) <= day


def path_to_id(p: str, root: Path) -> str:
    rel = Path(p).relative_to(root)
    return f"{rel.parent.name}#{rel.stem}"


def title_of(root: Path, tid: str) -> str:
    repo, num = tid.split("#")
    with open(root / repo / f"{num}.md") as f:
        first = f.readline().strip()
    return first.split(": ", 1)[1] if ": " in first else first


def where(root: Path, names: tuple, repo: str | None) -> str:
    if repo and repo not in names:
        raise ValueError(f"repo must be one of {names}")
    return str(root / repo if repo else root)


def rg(args: list[str]) -> str:
    p = subprocess.run(["rg", "-i", "--no-messages", "--with-filename", "--glob", "*.md"] + args,
                       capture_output=True, text=True)
    if p.returncode == 2 and not p.stdout:
        raise ValueError(p.stderr.strip()[:300] or "ripgrep error")
    return p.stdout


def rg_search(root: Path, names: tuple, pattern: str, regex: bool, also: list[str], repo: str | None) -> list[dict]:
    lit = [] if regex else ["-F"]
    counts = {}
    for line in rg(["-c"] + lit + ["-e", pattern, where(root, names, repo)]).splitlines():
        f, _, n = line.rpartition(":")
        counts[f] = int(n)
    files = {f for f in counts if visible(root, path_to_id(f, root))}
    for extra in also:
        if not files:
            break
        files &= set(rg(["-l"] + lit + ["-e", extra, where(root, names, repo)]).split("\n"))
    if not files:
        return []
    # Most matching lines first; ties go to the newer ticket (higher number).
    ranked = sorted(files, key=lambda f: (-counts[f], -int(Path(f).stem)))[:MAX_HITS]
    lines = {f: [] for f in ranked}
    for line in rg(["-m", str(LINES_PER_HIT), "--no-line-number"] + lit + ["-e", pattern] + ranked).splitlines():
        f, _, text = line.partition(":")
        if f in lines and len(lines[f]) < LINES_PER_HIT:
            lines[f].append(text.strip()[:LINE_CHARS])
    out = []
    for f in ranked:
        tid = path_to_id(f, root)
        out.append({"id": tid, "title": title_of(root, tid), "matching_lines": counts[f], "lines": lines[f]})
    return out


def read_ticket(root: Path, names: tuple, ticket: str) -> tuple[str, list]:
    try:
        repo, num = ticket.strip().split("#")
        tid = f"{repo}#{int(num)}"
        if repo not in names or not visible(root, tid):
            raise ValueError
        text = (root / repo / f"{int(num)}.md").read_text()
        return (text if len(text) <= DOC_CHARS else text[:DOC_CHARS] + "\n[... truncated]"), [tid]
    except (ValueError, OSError):
        return f"No ticket {ticket!r} in the archive.", []


SEARCH_DOC = """Search the past tickets with ripgrep (case-insensitive). `pattern` is a literal string unless `regex` is
true. `also` lists more patterns that must ALL appear somewhere in the same ticket. `repo` limits the search to one
product: {repos}. Returns up to 20 tickets, most matching lines first, each with its id,
title and up to 3 matching lines. Read a ticket with `read`."""

READ_DOC = """Read one past ticket in full (title, description, and the comments it had), by id, e.g. "{example}"."""


def quoted(names: tuple) -> str:
    q = [f'"{n}"' for n in names] or ['"(none yet: run init and update)"']
    return q[0] if len(q) == 1 else ", ".join(q[:-1]) + " or " + q[-1]


def cmd_serve(a) -> None:
    from mcp.server.mcpserver import MCPServer
    root = Path(os.environ.get("SEEN_BEFORE_CORPUS") or store_dir())
    root.mkdir(parents=True, exist_ok=True)
    if (root / "config.json").exists():
        names = tuple(labels(json.loads((root / "config.json").read_text())))
    else:
        names = tuple(sorted(p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")))
    order = os.environ.get("SEEN_BEFORE_REPO_ORDER")  # the order the experiment listed its repos in
    if order:
        names = tuple(order.split(","))
    log_p = os.environ.get("SEEN_BEFORE_TOOL_LOG")
    server = MCPServer(os.environ.get("SEEN_BEFORE_SERVER_NAME", "archive"))

    def log(tool: str, args: dict, seconds: float, returned: list) -> None:
        if log_p:
            with open(log_p, "a") as f:
                f.write(json.dumps({"t": time.time(), "tool": tool, "args": args, "seconds": seconds,
                                    "returned": returned}) + "\n")

    def search(pattern: str, regex: bool = False, also: list[str] | None = None, repo: str | None = None) -> str:
        args = {"pattern": pattern, "regex": regex, "also": also, "repo": repo}
        t0 = time.perf_counter()
        try:
            hits, err = rg_search(root, names, pattern, regex, also or [], repo), None
        except Exception as e:  # a bad regex, say; the model sees the error and can retry
            hits, err = [], str(e)
        log("search", args, time.perf_counter() - t0, [h["id"] for h in hits])
        return json.dumps({"error": err} if err else {"results": hits, "count": len(hits)})

    def read(ticket: str) -> str:
        t0 = time.perf_counter()
        text, ok = read_ticket(root, names, ticket)
        log("read", {"ticket": ticket}, time.perf_counter() - t0, ok)
        return text

    search.__doc__ = SEARCH_DOC.format(repos=quoted(names))
    read.__doc__ = READ_DOC.format(example=f"{names[-1] if names else 'Repo'}#1234")
    server.tool()(search)
    server.tool()(read)
    server.run("stdio")


# ---------------------------------------------------------------- check: the measured agent on one issue

ANSWER_FORMAT = """Reply with one JSON object and nothing else:
{{"seen_before": true or false,
 "matches": ["Repo#number", ...],
 "answer": "what the customer should do, in a few sentences"}}
"matches" lists at most 3 past tickets that report the same problem, best first, written exactly as the
ticket ids appear (for example "{example}"); empty if none does. Only cite tickets you were shown."""

SYSTEM_CORE = """You are a support engineer for {products}. A customer has just filed a new ticket.
Your only source is the archive of past tickets. Decide whether this exact problem has been reported before;
if it has, say which past tickets report it and tell the customer what to do, based on what those tickets
say. A ticket about a different problem in the same area is not a match."""

SYSTEM_AGENT = """
Search the archive with your tools, and read the tickets that look relevant. Search as many times as you need,
with different words, error codes or messages from the ticket, before you decide.

"""

TOOLS = ["mcp__archive__search", "mcp__archive__read"]


def system_prompt(products: str, example: str) -> str:
    return SYSTEM_CORE.format(products=products) + SYSTEM_AGENT + ANSWER_FORMAT.format(example=example)


def question_view(repo_label: str, title: str, body: str) -> str:
    body = body or ""
    if len(body) > QUESTION_CHARS:
        body = body[:QUESTION_CHARS] + "\n[... truncated]"
    return f"New ticket ({repo_label}), title: {title}\n\n{body}"


def parse_answer(text: str, names: tuple) -> dict:
    """The last JSON object in the reply. A reply with none is a failed answer."""
    text = text or ""
    id_re = re.compile(r"(" + "|".join(map(re.escape, names)) + r")#(\d+)")
    dec = json.JSONDecoder()
    for start in reversed([i for i, ch in enumerate(text) if ch == "{"]):
        try:
            d, _ = dec.raw_decode(text, start)
        except json.JSONDecodeError:
            continue
        if not isinstance(d, dict) or "matches" not in d:
            continue
        matches = [x.strip() for x in d.get("matches") or [] if isinstance(x, str) and id_re.fullmatch(x.strip())]
        return {"ok": True, "seen_before": bool(d.get("seen_before")), "matches": matches[:3],
                "answer": str(d.get("answer", ""))}
    return {"ok": False, "seen_before": None, "matches": [], "answer": ""}


def prepare(corpus: Path, products: str, names: tuple, label: str, num: int, title: str, body: str,
            log_path: Path, python: str | None = None, before: str | None = None) -> dict:
    """Everything one headless call needs: CLI args (without -p, the output format and --mcp-config), the
    prompt, the MCP config, and the tools the CLI must report."""
    cfg = {"mcpServers": {"archive": {
        "command": python or sys.executable, "args": [str(Path(__file__).resolve()), "serve"],
        "env": {"SEEN_BEFORE_CORPUS": str(corpus), "SEEN_BEFORE_HIDE": f"{label}#{num}",
                "SEEN_BEFORE_TOOL_LOG": str(log_path), "SEEN_BEFORE_REPO_ORDER": ",".join(names),
                **({"SEEN_BEFORE_BEFORE": f"{label}#{num}@{before}"} if before else {}),
                "PATH": os.environ.get("PATH", "/usr/bin:/bin")}}}}
    args = ["--model", MODEL, "--system-prompt", system_prompt(products, f"{names[0]}#31226"),
            "--setting-sources", "", "--strict-mcp-config", "--tools", "", "--no-session-persistence",
            "--disable-slash-commands", "--allowedTools", ",".join(TOOLS), "--max-turns", str(MAX_TURNS)]
    return {"args": args, "prompt": question_view(label, title, body), "mcp_config": cfg, "tools": TOOLS}


def finish(result: dict, log_path: Path, names: tuple, corpus: Path) -> dict:
    calls = [json.loads(l) for l in log_path.read_text().splitlines()] if log_path.exists() else []
    shown = {t for c in calls for t in c["returned"]}
    parsed = parse_answer(result.get("result", ""), names)
    # Only tickets the tools actually returned count, as in the experiment's citation rule.
    valid = [m for m in parsed["matches"] if m in shown]
    return {"parsed": parsed, "matches": [{"id": m, "title": title_of(corpus, m)} for m in valid],
            "seen_before": bool(parsed["seen_before"] and valid), "shown": sorted(shown), "tool_calls": calls,
            "turns": result.get("num_turns"), "cost_usd": result.get("total_cost_usd")}


class AgentFailed(SystemExit):
    """The agent could not run as measured. Never reported as "not seen before": the run fails instead."""

    def __init__(self, why: str):
        super().__init__(f"seen-before: the agent did not run as measured: {why}")


# What the measured agent's environment held; nothing else of the caller's reaches it (no model or endpoint
# overrides, no other settings). Credentials: an API key, an OAuth token, or the login in the config home.
ENV_KEEP = ("PATH", "HOME", "LANG", "TMPDIR", "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CONFIG_DIR")


def run_local(call: dict, timeout: int = 900) -> dict:
    with tempfile.TemporaryDirectory(prefix="seen-before-") as wd:
        p = Path(wd) / "mcp.json"
        p.write_text(json.dumps(call["mcp_config"]))
        # The measured agent saw no CLAUDE.md and no memory; inside someone's own session it would see theirs.
        env = {k: os.environ[k] for k in ENV_KEEP if k in os.environ}
        env.update(CLAUDE_CODE_DISABLE_CLAUDE_MDS="1", CLAUDE_CODE_DISABLE_AUTO_MEMORY="1")
        proc = subprocess.run(["claude", "-p", *call["args"], "--output-format", "stream-json", "--verbose",
                               "--mcp-config", str(p)], input=call["prompt"], capture_output=True, text=True,
                              cwd=wd, env=env, timeout=timeout)
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
    return check_run(proc.returncode, init, result, call["tools"], proc.stderr)


def check_run(code: int, init: dict, result: dict, tools: list, stderr: str = "") -> dict:
    if not init:
        raise AgentFailed(f"no start-up event from claude (exit {code}): {stderr[-300:]}")
    if sorted(init.get("tools") or []) != sorted(tools):
        raise AgentFailed(f"tools {sorted(init.get('tools') or [])}, expected {sorted(tools)}")
    servers = {s.get("name"): s.get("status") for s in init.get("mcp_servers") or []}
    if servers != {"archive": "connected"}:
        raise AgentFailed(f"MCP servers {servers}, expected the archive server connected")
    if not result:
        raise AgentFailed(f"no result from claude (exit {code}): {stderr[-300:]}")
    if result.get("is_error") and result.get("subtype") != "error_max_turns":
        raise AgentFailed(f"{result.get('subtype')}: {str(result.get('result'))[:300]}")
    return result


def comment_markdown(verdict: dict, repos: dict, here: str) -> str:
    """Built from validated ids and the store's titles only, never from the model's prose: the issue text is
    untrusted input and this is posted publicly."""
    if not verdict["seen_before"]:
        return ""
    rows = []
    for m in verdict["matches"]:
        label, num = m["id"].split("#")
        ref = f"#{num}" if repos[label] == here else f"{repos[label]}#{num}"
        # Titles are other people's text: inside a code span nothing in them becomes a link, a mention or a
        # cross-reference.
        title = re.sub(r"[\r\n`]", " ", m["title"])[:120].strip()
        rows.append(f"- {ref} `{title}`")
    return ("This may have been reported before:\n\n" + "\n".join(rows) + f"\n\n{MARK} searched "
            "this repository's past issues with ripgrep and may be wrong.</sub>\n")


def cmd_check(a) -> None:
    store = store_dir()
    cfg = load_config(store)
    repos = labels(cfg)
    names = tuple(repos)
    if a.event:
        ev = json.loads(Path(a.event).read_text())
        full = ev["repository"]["full_name"]
        match = [l for l, r in repos.items() if r.lower() == full.lower()]
        if not match:
            sys.exit(f"{full} is not among the configured repos {list(repos.values())}")
        label, here = match[0], repos[match[0]]
        num, title, body = ev["issue"]["number"], ev["issue"]["title"], ev["issue"].get("body") or ""
        before = ev["issue"]["created_at"][:10]
    else:
        if "#" in a.ref:
            label, n = a.ref.split("#")
        elif len(names) == 1:
            label, n = names[0], a.ref
        else:
            sys.exit(f"say which repo: {names[0]}#{a.ref}")
        num = int(n)
        p = store / label / f"{num}.md"
        if not p.exists():
            sys.exit(f"{label}#{num} is not in the store; run `seen_before.py update`")
        lines = p.read_text().split("\n")
        title = lines[0].split(": ", 1)[1]
        body = "\n".join(lines[3:]).split("\n--- comment by ")[0].strip()
        before = lines[1].removeprefix("created: ")
        here = repos.get(label)
    if label not in repos:
        sys.exit(f"{label} is not a configured repo")
    with tempfile.TemporaryDirectory(prefix="seen-before-") as wd:
        log_path = Path(wd) / "tools.jsonl"
        call = prepare(store, cfg["products"], names, label, num, title, body, log_path, before=before)
        verdict = finish(run_local(call), log_path, names, store)
    verdict["issue"] = f"{label}#{num}"
    verdict["comment"] = comment_markdown(verdict, repos, here)
    if a.comment_out:
        Path(a.comment_out).write_text(verdict["comment"])
    print(json.dumps({k: verdict[k] for k in ("issue", "seen_before", "matches", "turns", "cost_usd")}, indent=1))
    if verdict["seen_before"] and not a.event:
        print("\nSuggested answer (the model's words, unchecked):\n" + verdict["parsed"]["answer"])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init")
    p.add_argument("repos", nargs="+")
    p.add_argument("--products")
    p = sub.add_parser("update")
    p.add_argument("--if-older", type=int, default=0)
    p.add_argument("--background", action="store_true")
    p = sub.add_parser("check")
    p.add_argument("ref", nargs="?")
    p.add_argument("--event")
    p.add_argument("--comment-out")
    sub.add_parser("serve")
    a = ap.parse_args()
    {"init": cmd_init, "update": cmd_update, "check": cmd_check, "serve": cmd_serve}[a.cmd](a)


if __name__ == "__main__":
    main()

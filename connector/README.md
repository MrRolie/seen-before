# seen-before connector

Has this issue been reported before? An AI agent searches your repository's past issues with ripgrep, reads
the ones that look relevant, and names the earlier reports, or says it found none.

There is no model to train, no embedding index and no GPU. The index is a folder holding one markdown file
per past issue. The experiment in `../experiment` compares this agent with a trained-embedding pipeline and
publishes the numbers: see `../experiment/RESULTS.md`.

One file, `seen_before.py`, does everything:

| Command | What it does |
|---|---|
| `init owner/repo ...` | Sets up the store (`$SEEN_BEFORE_STORE`, default `~/.cache/seen-before`). |
| `update` | Mirrors new and changed issues and comments from the GitHub API. It only fetches what changed since the last run. |
| `check REF` | Runs the agent on one issue and prints the earlier issues it names. |
| `serve` | Starts an MCP server with two tools, `search` (ripgrep) and `read`. |

`check` runs the agent exactly as the experiment measured it:
- Headless Claude Code (`claude -p`) with a fixed system prompt.
- No built-in tools, and no CLAUDE.md or memory.
- Only `search` and `read`, at most 20 turns, Claude Sonnet 5.5.

The issue being checked is hidden from both tools, and so is every issue filed after it (by day, and by number
within its repository). For a new issue that is the whole archive as it stands. For `check` on an older issue,
comments added later to earlier issues stay visible, so its answer can draw on hindsight. Only issues the tools actually returned can be cited.

If the agent cannot run as measured, `check` fails rather than answer "not seen before". That covers a bad key,
no credit left, the MCP server not starting, or tools other than `search` and `read`. The agent gets only a
minimal environment: no model or endpoint overrides from the caller. `SEEN_BEFORE_MODEL` changes the model, and
then it is no longer the measured agent.

## Three ways to use it

**GitHub Action** (event-driven). It runs when an issue is opened:
1. Updates the mirror, kept in the Actions cache between runs.
2. Asks the agent.
3. Comments "This may have been reported before" with links, or stays silent.

To set it up, copy `examples/seen-before.yml` into `.github/workflows/` and add an `ANTHROPIC_API_KEY` secret.
It starts in dry-run mode, which writes the comment to the job summary only.

The comment is built from checked issue numbers and their stored titles, never from the model's text. The
agent's step has no GitHub token, so it cannot write anywhere; a separate step posts the comment.

The first run mirrors the whole history. For a large repository this takes hours with the default token, so
start it by hand once.

**Claude Code plugin.** Install it:

    claude plugin marketplace add MrRolie/seen-before
    claude plugin install seen-before@seen-before

Then set up the store and fill it, in a session:

    /seen-before:seen-before init owner/repo
    /seen-before:seen-before update

The plugin then gives you three things:
- `/seen-before:seen-before 1234` runs the same `check` as the Action.
- The `seen-before` MCP server lets you search past issues yourself in a session. That use is not the
  measured agent: your own session, prompt and tools take part.
- A session-start hook refreshes the mirror in the background, at most once an hour and never two at once.

**MCP server only.** Add the server without the plugin:

    claude mcp add seen-before -- uv run --script /path/to/seen_before.py serve

## Requirements

- `rg` (ripgrep).
- `uv`, or Python 3.10+ with `pip install mcp`.
- `claude` (Claude Code) for `check`.
- A GitHub token (`GITHUB_TOKEN`, or a logged-in `gh`) for `update`.

## Limits

- Anyone who can open an issue starts a run, at up to 20 model turns. Nothing limits the rate.
- The Action keeps the mirror in the Actions cache, one entry a day for each set of repositories. Any workflow
  on your default branch that runs untrusted code (`pull_request_target`, `workflow_run`) could write a cache
  entry. That entry could hide issues or change the titles the comments show. The configuration and the code
  never come from the cache.
- The bot's own comments are left out of the mirror, so it does not learn from its earlier guesses.
- The MCP server reads the list of repositories when it starts.

- The mirror does not notice deleted issues or comments.
- It does not search pull requests.
- The agent can be wrong in both directions, and its suggested answer is unchecked. The Action posts only the
  links, never that answer.

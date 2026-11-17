---
name: seen-before
description: Check whether an issue has been reported before, by having an agent search the repository's past issues with ripgrep. Use when the user asks "have I seen this before", "is this a duplicate", or to check an issue number or pasted issue against earlier reports.
argument-hint: "[Repo#number | number | init owner/repo | update]"
allowed-tools: Bash(uv run --quiet --script ${CLAUDE_PLUGIN_ROOT}/seen_before.py *)
---

If the user gave an issue number, run the measured check, which is a separate headless agent with only `search` and `read`:

```
uv run --quiet --script ${CLAUDE_PLUGIN_ROOT}/seen_before.py check $ARGUMENTS
```

Report what it found: the earlier issues it names, with their titles, or that it found none. Keep the line that
says the suggested answer is the model's own words. If it says the store is not configured, tell the user to run
`/seen-before:seen-before init owner/repo` and then `/seen-before:seen-before update`.

If the arguments start with `init` or `update`, run that instead, and tell the user that a first mirror of a large
repository can take hours:

```
uv run --quiet --script ${CLAUDE_PLUGIN_ROOT}/seen_before.py $ARGUMENTS
```

If the user pasted text instead of a number, search the archive yourself with the `seen-before` server's `search`
and `read` tools, and say that this is not the measured check.

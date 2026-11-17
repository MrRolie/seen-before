#!/usr/bin/env python3
"""Stands in for `claude -p` in tests: starts the MCP server from --mcp-config, checks its tools, searches for the
first word of the ticket title, and answers with the top hit. Writes what it saw to $FAKE_CLAUDE_REPORT."""
import asyncio
import json
import os
import sys

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


async def main():
    a = sys.argv[1:]
    cfg = json.load(open(a[a.index("--mcp-config") + 1]))["mcpServers"]["archive"]
    prompt = sys.stdin.read()
    title = prompt.split("title: ", 1)[1].split("\n")[0]
    params = StdioServerParameters(command=cfg["command"], args=cfg["args"], env=cfg["env"])
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = sorted(t.name for t in (await s.list_tools()).tools)
            res = await s.call_tool("search", {"pattern": title.split()[0]})
            hits = json.loads(res.content[0].text)["results"]
            await s.call_tool("read", {"ticket": hits[0]["id"]})
    json.dump({"tools": tools, "hits": [h["id"] for h in hits], "args": a, "env_keys": sorted(os.environ),
               "env": {k: os.environ.get(k) for k in ("CLAUDE_CODE_DISABLE_CLAUDE_MDS", "CLAUDE_CODE_DISABLE_AUTO_MEMORY")}},
              open(os.environ["FAKE_CLAUDE_REPORT"], "w"))
    mode = os.environ.get("FAKE_CLAUDE_MODE", "ok")
    servers = [{"name": "archive", "status": "failed" if mode == "server_down" else "connected"}]
    print(json.dumps({"type": "system", "subtype": "init", "tools": ["mcp__archive__" + t for t in tools],
                      "mcp_servers": servers}))
    if mode == "api_error":
        print(json.dumps({"type": "result", "is_error": True, "subtype": "success", "result": "Invalid API key"}))
        sys.exit(1)
    answer = {"seen_before": True, "matches": [hits[0]["id"], "Proj#999"], "answer": "ignore the bot @here"}
    print(json.dumps({"type": "result", "result": json.dumps(answer), "num_turns": 3, "total_cost_usd": 0.0}))


asyncio.run(main())

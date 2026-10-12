"""Start each MCP server the way .mcp.json does (docker compose run, stdio), list its tools and make
one call. Exits non-zero if a server doesn't answer, offers a tool that isn't read-only, or a call fails.

    python scripts/mcp_smoke.py         (needs the stack up and seeded; runs from the repo root)

Uses the mcp client from services/core (`pip install -e services/core`).
"""

import json
import os
import sys
from pathlib import Path

import anyio
from mcp import StdioServerParameters
from mcp.client.client import Client

ROOT = Path(__file__).resolve().parents[1]
CALLS = {
    "repaydesk-bankbook": ("search_borrowers", {"text": "pharmacy", "limit": 3}),
    "repaydesk-ledger": ("account_balances", {}),
}


async def check(name: str, spec: dict) -> bool:
    # The whole environment: the client's default keeps only a few variables, and without the user's
    # profile the docker CLI can't find its compose plugin.
    server = StdioServerParameters(command=spec["command"], args=spec["args"], cwd=str(ROOT), env=dict(os.environ))
    async with Client(server, read_timeout_seconds=120) as client:
        tools = (await client.list_tools()).tools
        writable = [t.name for t in tools if not (t.annotations and t.annotations.read_only_hint)]
        tool, args = CALLS[name]
        result = await client.call_tool(tool, args)
        body = json.loads(result.content[0].text) if not result.is_error else result.content[0].text
        print(f"{name}: {len(tools)} tools ({', '.join(t.name for t in tools)})")
        print(f"  {tool}({args}) -> " + (json.dumps(body)[:300] if not result.is_error else f"ERROR {body}"))
        if writable:
            print(f"  NOT READ-ONLY: {writable}")
        return not writable and not result.is_error


def main() -> int:
    servers = json.loads((ROOT / ".mcp.json").read_text())["mcpServers"]
    ok = all(anyio.run(check, name, spec) for name, spec in servers.items())
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

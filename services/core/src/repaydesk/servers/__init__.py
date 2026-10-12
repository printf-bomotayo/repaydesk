"""Repaydesk's MCP servers: read-only windows onto the bank's systems, for the investigator agent
and for any MCP client (Claude Desktop, Claude Code). Each tool is a lookup; none changes anything.

- `bankbook`: borrowers, loans and payments (the directory, core banking live, Repaydesk's lines).
- `ledger`: journal entries and account balances in the ledger.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations

# One log line per HTTP call to Fineract or Odoo is noise on a server's stderr.
logging.getLogger("httpx").setLevel(logging.WARNING)

# Every tool here only reads, and only from the bank's own systems.
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)


def answer(lookup: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run a lookup; a thing that doesn't exist becomes a message the model can read and act on.
    Anything unexpected stays a crash, which the SDK reports without its details."""
    try:
        return lookup(*args, **kwargs)
    except LookupError as e:
        raise ToolError(str(e)) from None

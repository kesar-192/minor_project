"""The MCP server. It has one job: pass tool calls to the Vault and pass the answer back.

It does not approve, deny, fix or execute anything (CONTRACTS.md, section 1). It holds no keys.
The only secret it holds is the agent's session token, which it adds to each request itself,
so neither the LLM nor the agent process ever sees it.
"""

import json
from contextlib import asynccontextmanager

import mcp_types as types
from mcp.server import Server

from .catalog import tools_for
from .vault_client import VaultClient, clean_trace_id

# The agent sets this in a call's _meta so that every call made for one user request shares a trace id.
# Without it, the server makes a fresh trace id for the call.
TRACE_META_KEY = "secureagent/trace_id"

INSTRUCTIONS = (
    "Every tool here is forwarded to the SecureAgent Vault, which decides ALLOW or DENY. "
    "This server cannot approve, sign or send anything itself."
)


def build_server(settings, vault=None):
    vault = vault or VaultClient(settings)

    @asynccontextmanager
    async def lifespan(_server):
        try:
            yield {}
        finally:
            close = getattr(vault, "aclose", None)
            if close:
                await close()

    async def on_list_tools(ctx, params):
        return types.ListToolsResult(tools=tools_for(settings))

    async def on_call_tool(ctx, params):
        # Any tool name is forwarded, listed or not, so the Vault can refuse it and log it.
        trace_id = clean_trace_id((ctx.meta or {}).get(TRACE_META_KEY))
        reply = await vault.call_tool(params.name, params.arguments, trace_id)
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps(reply.body))],
            structured_content=reply.body,
            # A Vault decision, DENY included, is a normal answer. Only a DENY made here is an error.
            is_error=reply.synthesized,
        )

    return Server(
        "secureagent-mcp",
        version="0.1.0",
        instructions=INSTRUCTIONS,
        lifespan=lifespan,
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )

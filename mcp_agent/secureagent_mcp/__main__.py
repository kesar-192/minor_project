"""Run the MCP server:  python -m secureagent_mcp  [--transport stdio|http]

stdio (default) is what the agent uses: it starts this as a child process and talks over stdin/stdout.
Because stdout carries the protocol, everything this program logs goes to stderr.

http serves the same tools over Streamable HTTP, for an agent that runs on another machine. The tool
endpoint has no login of its own, and the server holds the agent's session token, so anyone who can
reach it can act as the agent within policy. It listens on 127.0.0.1 unless told otherwise.
"""

import argparse
import logging
import os
import sys

import anyio

from .config import ConfigError, ServerSettings, load_env
from .server import build_server

LOOPBACK = {"127.0.0.1", "localhost", "::1"}


async def run_stdio(server):
    from mcp.server.stdio import stdio_server

    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def run_http(server, host, port):
    import uvicorn

    if host not in LOOPBACK:
        logging.getLogger("secureagent_mcp").warning(
            "listening on %s: anyone on this network can use the agent's identity through this server", host
        )
    uvicorn.run(server.streamable_http_app(host=host), host=host, port=port, log_level="info")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="secureagent_mcp", description=__doc__.split("\n")[0])
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1", help="http only (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8400, help="http only (default 8400)")
    parser.add_argument("--env-file", help="path to a .env file (default: mcp_agent/.env)")
    args = parser.parse_args(argv)

    level = getattr(logging, os.environ.get("SECUREAGENT_LOG_LEVEL", "INFO").upper(), logging.INFO)
    logging.basicConfig(level=level, stream=sys.stderr, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        settings = ServerSettings.from_env(load_env(args.env_file))
    except ConfigError as exc:
        sys.exit(f"secureagent_mcp: {exc}")

    logging.getLogger("secureagent_mcp").info(
        "agent %s -> Vault at %s (attack-demo tools %s)",
        settings.agent_id, settings.vault_url, "listed" if settings.expose_attack_demo_tools else "hidden",
    )
    server = build_server(settings)
    if args.transport == "http":
        run_http(server, args.host, args.port)
    else:
        anyio.run(run_stdio, server)


if __name__ == "__main__":
    main()

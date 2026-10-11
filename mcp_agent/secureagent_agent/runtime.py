"""Starts the MCP server as a child process for the agent to talk to over stdio."""

import os
import sys

from mcp import StdioServerParameters
from mcp.shared.exceptions import MCPError

from secureagent_mcp.config import PACKAGE_ROOT

# The only settings the MCP server child process is handed from the shell. The MCP SDK starts the child with
# a minimal environment on purpose, so anything not listed here (the LLM API keys, for one) never reaches it.
SERVER_VARS = ("VAULT_URL", "AGENT_ID", "AGENT_TOKEN", "VAULT_TIMEOUT_SECONDS", "EXPOSE_ATTACK_DEMO_TOOLS", "SECUREAGENT_ENV_FILE",
               "SECUREAGENT_LOG_LEVEL")

START_FAILED = (
    "The MCP server stopped while starting. Its own message is printed just above. The usual cause is a missing "
    "AGENT_TOKEN: put it in .env, or run `python -m secureagent_mcp` by itself to see the reason."
)


def mcp_params(vault_url=None, token=None, attack_demo=False, env_file=None, environ=None):
    """How to launch `python -m secureagent_mcp`.

    The child gets: the server settings the user exported in their shell (SERVER_VARS), then anything passed
    here. Its own .env file is read by the child itself, so by default the agent process never has to hold
    the Vault session token.
    """
    environ = os.environ if environ is None else environ
    env = {name: environ[name] for name in SERVER_VARS if name in environ}
    env.setdefault("SECUREAGENT_LOG_LEVEL", "WARNING")   # keep the demo output readable; failures still show
    if vault_url:
        env["VAULT_URL"] = vault_url
    if token is not None:
        env["AGENT_TOKEN"] = token
    if attack_demo:
        env["EXPOSE_ATTACK_DEMO_TOOLS"] = "1"
    args = ["-m", "secureagent_mcp"]
    if env_file:
        args += ["--env-file", env_file]
    return StdioServerParameters(command=sys.executable, args=args, env=env or None, cwd=str(PACKAGE_ROOT))


def is_start_failure(exc):
    """True for the errors the SDK raises when the child process exits before it finishes starting."""
    if isinstance(exc, BaseExceptionGroup):
        return any(is_start_failure(inner) for inner in exc.exceptions)
    return isinstance(exc, (MCPError, ConnectionError, BrokenPipeError))

"""Settings, read from environment variables or a .env file.

The .env file is read into a plain dict. It is never copied into os.environ, so the
agent process (which talks to the LLM) does not end up holding the Vault session token.
Only the MCP server process needs that token.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

PACKAGE_ROOT = Path(__file__).resolve().parent.parent  # the mcp_agent folder


class ConfigError(Exception):
    """A setting is missing or wrong. The message says which one and how to fix it."""


def load_env(env_file=None, environ=None):
    """Settings from the .env file, with real environment variables taking priority."""
    environ = os.environ if environ is None else environ
    path = env_file or environ.get("SECUREAGENT_ENV_FILE") or PACKAGE_ROOT.parent / ".env"
    values = {}
    if Path(path).is_file():
        values.update({k: v for k, v in dotenv_values(path).items() if v is not None})
    elif env_file:
        raise ConfigError(f"env file not found: {env_file}")
    values.update(environ)
    return values


def _flag(value):
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class ServerSettings:
    vault_url: str
    agent_id: str
    agent_token: str = field(repr=False)  # never printed, never logged
    timeout_seconds: float = 30.0
    expose_attack_demo_tools: bool = False

    @classmethod
    def from_env(cls, env):
        token = (env.get("AGENT_TOKEN") or "").strip()
        if not token:
            raise ConfigError(
                "AGENT_TOKEN is not set. Put the agent's session token in .env "
                "(the Vault owner makes it with scripts/new_agent_token.py)."
            )
        url = (env.get("VAULT_URL") or "http://127.0.0.1:8000").strip().rstrip("/")
        if not url.startswith(("http://", "https://")):
            raise ConfigError(f"VAULT_URL must start with http:// or https://, got {url!r}")
        try:
            timeout = float(env.get("VAULT_TIMEOUT_SECONDS") or 30)
        except ValueError:
            raise ConfigError("VAULT_TIMEOUT_SECONDS must be a number") from None
        if timeout <= 0:
            raise ConfigError("VAULT_TIMEOUT_SECONDS must be greater than zero")
        return cls(
            vault_url=url,
            agent_id=(env.get("AGENT_ID") or "crypto-agent").strip(),
            agent_token=token,
            timeout_seconds=timeout,
            expose_attack_demo_tools=_flag(env.get("EXPOSE_ATTACK_DEMO_TOOLS", "")),
        )

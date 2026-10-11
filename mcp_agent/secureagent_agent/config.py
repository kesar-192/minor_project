"""Agent settings. Nothing here is the Vault session token: only the MCP server process reads that."""

from dataclasses import dataclass, field

from secureagent_mcp.config import ConfigError


@dataclass(frozen=True)
class AgentSettings:
    planner: str = "scripted"
    model: str | None = None
    anthropic_api_key: str | None = field(default=None, repr=False)
    openai_api_key: str | None = field(default=None, repr=False)
    openai_base_url: str | None = None
    openrouter_api_key: str | None = field(default=None, repr=False)
    max_tokens: int = 1024
    max_steps: int = 4      # model turns per user request
    max_lookups: int = 2    # read-only tool calls per user request

    @classmethod
    def from_env(cls, env):
        def number(name, default):
            raw = env.get(name)
            try:
                value = int(raw) if raw else default
            except ValueError:
                raise ConfigError(f"{name} must be a whole number") from None
            if value < 1:
                raise ConfigError(f"{name} must be at least 1")
            return value

        return cls(
            planner=(env.get("AGENT_PLANNER") or "scripted").strip().lower(),
            model=(env.get("LLM_MODEL") or "").strip() or None,
            anthropic_api_key=(env.get("ANTHROPIC_API_KEY") or "").strip() or None,
            openai_api_key=(env.get("OPENAI_API_KEY") or "").strip() or None,
            openai_base_url=(env.get("OPENAI_BASE_URL") or "").strip() or None,
            openrouter_api_key=(env.get("OPENROUTER_API_KEY") or "").strip() or None,
            max_tokens=number("LLM_MAX_TOKENS", 1024),
            max_steps=number("AGENT_MAX_STEPS", 4),
            max_lookups=number("AGENT_MAX_LOOKUPS", 2),
        )

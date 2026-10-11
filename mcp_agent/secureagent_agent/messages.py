"""Plain types shared by the loop and the planners. Each planner turns these into its own LLM API format."""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict


@dataclass(frozen=True)
class UserMsg:
    text: str


@dataclass(frozen=True)
class ToolUse:
    """The planner wants this tool called. Also kept in the history as the assistant's turn."""
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolResult:
    id: str
    text: str
    is_error: bool = False


@dataclass(frozen=True)
class Say:
    """The planner has finished and has this to tell the user."""
    text: str


class PlannerError(Exception):
    """The planner could not produce a usable step (bad API reply, invalid tool arguments, no key...)."""

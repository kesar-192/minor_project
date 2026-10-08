"""Request, response and event shapes. These mirror contracts/CONTRACTS.md exactly."""

import re
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from eth_utils import is_checksum_address, to_checksum_address
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

AMOUNT_RE = re.compile(r"^\d{1,30}(\.\d{1,18})?$")
ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
ASSET_RE = re.compile(r"^[A-Za-z0-9]{1,10}$")


def parse_amount(value: Any) -> Decimal:
    # Strings only: a JSON number like 0.1 has already lost precision before we see it.
    if not isinstance(value, str) or not AMOUNT_RE.match(value):
        raise ValueError('must be a decimal string like "0.05"')
    amount = Decimal(value)
    if amount <= 0:
        raise ValueError("must be greater than zero")
    return amount


def parse_address(value: Any) -> str:
    if not isinstance(value, str) or not ADDRESS_RE.match(value):
        raise ValueError("must be 0x followed by 40 hex characters")
    digits = value[2:]
    mixed_case = digits != digits.lower() and digits != digits.upper()
    if mixed_case and not is_checksum_address(value):
        # Mixed case means the sender meant a checksum, and it doesn't match: likely a typo.
        raise ValueError("has a bad checksum, check it for typos")
    return to_checksum_address(value)


def parse_asset(value: Any) -> str:
    if not isinstance(value, str) or not ASSET_RE.match(value):
        raise ValueError('must be an asset symbol like "ETH"')
    return value.upper()


Amount = Annotated[Decimal, BeforeValidator(parse_amount)]
Address = Annotated[str, BeforeValidator(parse_address)]
Asset = Annotated[str, BeforeValidator(parse_asset)]
TxHash = Annotated[str, Field(pattern=r"^0x[0-9a-fA-F]{64}$")]


class Strict(BaseModel):
    # Unknown fields are rejected, so an injected "skip_checks": true never gets through.
    model_config = ConfigDict(extra="forbid")


# ---- Tool-call request (MCP server -> Vault) ----

class ToolCall(Strict):
    trace_id: UUID
    agent_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    session_token: str = Field(min_length=1, max_length=256)
    tool: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    args: dict[str, Any] = Field(default_factory=dict)


class GetBalanceArgs(Strict):
    asset: Asset = "ETH"


class ProposeTransferArgs(Strict):
    asset: Asset
    amount: Amount
    recipient: Address


class TransactionStatusArgs(Strict):
    tx_hash: TxHash


class NoArgs(Strict):
    pass


# ---- Response (Vault -> MCP server) ----

class Check(Strict):
    gate: str
    passed: bool
    detail: str | None = None


class Decision(Strict):
    trace_id: str
    decision: Literal["ALLOW", "DENY"]
    gate: str | None = None
    gate_number: int | None = None
    reason_code: str | None = None
    reason: str | None = None
    checks: list[Check]
    result: dict[str, Any] | None = None


# ---- Decision event (Vault -> Dashboard) ----

class Event(Strict):
    event_id: str
    seq: int
    type: Literal["decision", "error"]
    timestamp: str
    trace_id: str
    agent_id: str | None
    tool: str | None
    args: dict[str, Any]
    decision: Literal["ALLOW", "DENY"]
    gate: str | None
    gate_number: int | None
    gate_label: str | None
    reason_code: str | None
    reason: str | None
    severity: Literal["info", "warning", "critical"]
    checks: list[Check]
    tx_hash: str | None
    tx_status: Literal["submitted", "submit_failed"] | None
    audit_hash: str


GATE_LABELS = {
    0: "Request format",
    1: "Identity & status",
    2: "Permitted tools",
    3: "Allowed assets",
    4: "Recipient allow-list",
    5: "Amount + gas ≤ balance",
    6: "Per-tx & daily limits",
}

SEVERITY = {
    "INVALID_REQUEST": "warning",
    "POLICY_INVALID": "critical",
    "AGENT_NOT_AUTHENTICATED": "critical",
    "AGENT_SUSPENDED": "critical",
    "TOOL_NOT_PERMITTED": "warning",
    "KEY_EXPORT_DENIED": "critical",
    "ASSET_NOT_ALLOWED": "warning",
    "RECIPIENT_NOT_ALLOWED": "warning",
    "INSUFFICIENT_BALANCE": "warning",
    "LIMIT_PER_TX": "warning",
    "LIMIT_DAILY": "warning",
    "GATE_ERROR": "critical",
    "EXECUTION_FAILED": "critical",
}

# Codes that mean the Vault itself had a problem, rather than a policy decision.
ERROR_CODES = {"POLICY_INVALID", "GATE_ERROR", "EXECUTION_FAILED"}


def describe_error(exc, prefix=""):
    """One readable line for the first problem in a Pydantic ValidationError."""
    err = exc.errors()[0]
    name = prefix + ".".join(str(part) for part in err["loc"])
    if err["type"] == "extra_forbidden":
        return f"field '{name}' is not allowed"
    if err["type"] == "missing":
        return f"field '{name}' is missing"
    message = err["msg"].removeprefix("Value error, ")
    if not name:
        return message
    if err["type"] == "value_error":
        return f"field '{name}' {message}"
    return f"field '{name}': {message}"

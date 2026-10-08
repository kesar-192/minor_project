"""The six gates. Each returns a GateResult, and the pipeline stops at the first failure.

REAL_GATES enforce the policy file. STUB_GATES are placeholders that pass, so
Members 2 and 4 can build against a live endpoint before the real Vault is up.
Key export is refused in both.
"""

import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from eth_utils import from_wei, to_wei
from pydantic import BaseModel

from .chain import GAS_LIMIT_ETH_TRANSFER
from .models import ToolCall
from .policy import Policy
from .tools import RESTRICTED_TOOLS, TOOL_ARGS

STUB = "not enforced (stub mode)"
DAY_SECONDS = 24 * 60 * 60


@dataclass(frozen=True)
class Cost:
    """The true cost of a transfer, worked out by gate 5. Gate 6 and signing reuse these exact numbers."""
    amount_wei: int
    gas_limit: int
    gas_price_wei: int
    total_wei: int


@dataclass
class Ctx:
    """Everything the gates know about one request."""
    call: ToolCall
    args: BaseModel | None
    policy: Policy | None  # one snapshot per request, so every gate sees the same policy
    chain: Any = None      # read-only chain access (app/chain.py)
    store: Any = None      # spend record (app/store.py)
    sender: str | None = None  # the Vault's signing account
    cost: Cost | None = None   # set by gate 5

    @property
    def agent(self):
        # Only used after gate 1 has confirmed the agent exists.
        return self.policy.agents[self.call.agent_id]


@dataclass(frozen=True)
class GateResult:
    passed: bool
    detail: str | None = None
    reason_code: str | None = None
    reason: str | None = None


def allow(detail):
    return GateResult(True, detail)


def deny(reason_code, reason, detail=None):
    return GateResult(False, detail or reason, reason_code, reason)


def short(address):
    return f"{address[:6]}…{address[-4:]}"


def plain(number):
    """A Decimal written out in full, without trailing zeros: 0.050042, 10, 0.1."""
    return format(Decimal(number).normalize(), "f")


def eth(wei):
    return plain(from_wei(wei, "ether"))


# ---- Gate 1: identity & status ----

def identity(ctx):
    agent_id = ctx.call.agent_id
    agent = ctx.policy.agents.get(agent_id)
    if agent is None or not agent.token_matches(ctx.call.session_token):
        # The same answer for an unknown agent and a wrong token, so a caller
        # can't find out which agent names exist.
        return deny(
            "AGENT_NOT_AUTHENTICATED",
            "The agent could not be authenticated",
            f"{agent_id}: unknown agent or wrong token",
        )
    if agent.status != "active":
        return deny(
            "AGENT_SUSPENDED",
            f"Agent {agent_id} is suspended, so all its requests are refused",
            f"{agent_id} status is {agent.status}",
        )
    return allow(f"{agent_id} is active")


# ---- Gate 2: permitted tools ----

def tool_in_catalog(ctx):
    # Key export has no implementation in any mode. It is refused outright,
    # even in stub mode and even if a policy file tried to allow it.
    tool = ctx.call.tool
    if tool in RESTRICTED_TOOLS:
        return deny(
            "KEY_EXPORT_DENIED",
            "The agent asked for the private key. Keys never leave the Vault.",
            f"{tool} is a restricted tool",
        )
    if tool not in TOOL_ARGS:
        return deny(
            "TOOL_NOT_PERMITTED",
            f"Tool '{tool}' is not allowed for this agent",
            f"{tool} is not in the tool catalog",
        )
    return None


def permitted_tools(ctx):
    refused = tool_in_catalog(ctx)
    if refused:
        return refused
    tool = ctx.call.tool
    if tool not in ctx.agent.allowed_tools:
        return deny(
            "TOOL_NOT_PERMITTED",
            f"Tool '{tool}' is not allowed for this agent",
            f"{tool} is not on {ctx.call.agent_id}'s tool list",
        )
    return allow(f"{tool} is allowed")


# ---- Gate 3: allowed assets ----

def allowed_assets(ctx):
    asset = ctx.args.asset
    allowed = ctx.agent.allowed_assets
    if asset not in allowed:
        return deny(
            "ASSET_NOT_ALLOWED",
            f"{asset} is not an approved asset (allowed: {', '.join(allowed)})",
            f"{asset} is not in [{', '.join(allowed)}]",
        )
    return allow(f"{asset} is approved")


# ---- Gate 4: recipient allow-list ----

def recipient_allowlist(ctx):
    recipient = ctx.args.recipient
    name = ctx.agent.recipient_name(recipient)
    if name is None:
        return deny(
            "RECIPIENT_NOT_ALLOWED",
            f"{short(recipient)} is not on the approved recipient list",
            f"{recipient} is not approved",
        )
    return allow(f"{name} ({short(recipient)}) is approved")


# ---- Gate 5: amount + gas <= balance ----
# Every number is whole wei (integers), and gas and balance are read fresh from
# the chain here. Nothing about the cost is taken from the request.

def balance(ctx):
    if ctx.args.asset != "ETH":
        # Token transfers need a token contract (open point 2). Until then, refuse.
        raise NotImplementedError(f"{ctx.args.asset} transfers are not supported yet")
    gas_price = ctx.chain.gas_price_wei()
    amount_wei = to_wei(ctx.args.amount, "ether")
    total = amount_wei + GAS_LIMIT_ETH_TRANSFER * gas_price
    ctx.cost = Cost(amount_wei, GAS_LIMIT_ETH_TRANSFER, gas_price, total)
    available = ctx.chain.balance_wei(ctx.sender)
    if total > available:
        return deny(
            "INSUFFICIENT_BALANCE",
            f"Needs {eth(total)} ETH including gas, balance is {eth(available)} ETH",
            f"{eth(total)} ETH needed, {eth(available)} ETH available",
        )
    return allow(f"{eth(total)} ETH needed, {eth(available)} ETH available")


# ---- Gate 6: per-transaction and rolling 24-hour limits (on the true cost) ----

def limits(ctx):
    asset = ctx.args.asset
    limit = ctx.agent.limits[asset]
    total = ctx.cost.total_wei
    max_per_tx = to_wei(limit.max_per_tx, "ether")
    if total > max_per_tx:
        return deny(
            "LIMIT_PER_TX",
            f"{eth(total)} {asset} including gas is over the {plain(limit.max_per_tx)} {asset} per-transaction limit",
            f"{eth(total)} {asset} > {plain(limit.max_per_tx)} {asset} per transaction",
        )
    spent = ctx.store.spent_since(ctx.call.agent_id, asset, time.time() - DAY_SECONDS)
    daily = to_wei(limit.daily, "ether")
    if spent + total > daily:
        return deny(
            "LIMIT_DAILY",
            f"Would bring the last 24 hours to {eth(spent + total)} {asset}, "
            f"over the {plain(limit.daily)} {asset} daily limit",
            f"{eth(spent)} {asset} already spent + {eth(total)} {asset} > {plain(limit.daily)} {asset} per 24 hours",
        )
    return allow(
        f"{eth(total)} {asset} is within {plain(limit.max_per_tx)} {asset} per transaction, "
        f"{eth(spent + total)} of {plain(limit.daily)} {asset} used in 24 hours"
    )


# ---- Stub placeholders ----

def placeholder(ctx):
    return allow(STUB)


def stub_permitted_tools(ctx):
    return tool_in_catalog(ctx) or allow(STUB)


REAL_GATES = [
    (1, "identity", identity),
    (2, "permitted_tools", permitted_tools),
    (3, "allowed_assets", allowed_assets),
    (4, "recipient_allowlist", recipient_allowlist),
    (5, "balance", balance),
    (6, "limits", limits),
]

STUB_GATES = [
    (1, "identity", placeholder),
    (2, "permitted_tools", stub_permitted_tools),
    (3, "allowed_assets", placeholder),
    (4, "recipient_allowlist", placeholder),
    (5, "balance", placeholder),
    (6, "limits", placeholder),
]

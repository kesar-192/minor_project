"""What each tool does once every gate has passed (real mode)."""

import time
from dataclasses import dataclass
from typing import Any

from .gates import DAY_SECONDS, eth, plain


@dataclass
class Done:
    result: dict[str, Any]
    summary: str  # plain-English line for the dashboard
    tx_hash: str | None = None
    tx_status: str | None = None


def execute(ctx, signer, submitter):
    tool = ctx.call.tool
    if tool == "propose_transfer":
        return transfer(ctx, signer, submitter)
    if tool == "get_transaction_status":
        status = ctx.chain.transaction_status(ctx.args.tx_hash)
        return Done({"tx_hash": ctx.args.tx_hash, "status": status}, f"Transaction status looked up: {status}")
    if tool == "get_policy_status":
        return Done(policy_status(ctx), "The agent read its own policy and limits")
    if tool == "get_balance":
        return get_balance(ctx)
    raise NotImplementedError(f"{tool} is not available yet")


def get_balance(ctx):
    # The balance comes from Member 1's PHE service and nowhere else. If it is not
    # configured, or does not answer with a plain decimal, this raises and the
    # pipeline turns it into EXECUTION_FAILED: the Vault never invents a balance.
    if ctx.phe is None:
        raise NotImplementedError("the PHE service is not configured (set PHE_URL)")
    result = ctx.phe.balance(ctx.args.asset)
    return Done(result, f"{result['asset']} balance {result['balance']} (via the PHE service)")


def transfer(ctx, signer, submitter):
    args, cost = ctx.args, ctx.cost
    # Exactly what gate 5 checked: the same amount, gas limit and gas price.
    tx = {
        "to": args.recipient,
        "value": cost.amount_wei,
        "gas": cost.gas_limit,
        "gasPrice": cost.gas_price_wei,
        "nonce": ctx.chain.nonce(ctx.sender),
        "chainId": ctx.chain.chain_id(),
    }
    raw_tx, tx_hash = signer.sign(tx)
    # Counted the moment it is signed, even if handing it on fails. For a limit, that's the safe side.
    ctx.store.record_spend(ctx.call.agent_id, args.asset, cost.total_wei, str(ctx.call.trace_id))
    status = submitter.submit({
        "trace_id": str(ctx.call.trace_id),
        "raw_tx": raw_tx,
        "tx_hash": tx_hash,
        "from": ctx.sender,
        "to": args.recipient,
        "value_wei": str(cost.amount_wei),
        "chain_id": tx["chainId"],
    })
    what = f"{plain(args.amount)} {args.asset} to {ctx.agent.recipient_name(args.recipient)}"
    if status == "submitted":
        summary = f"Sent {what}. All 6 gates passed."
    else:
        summary = f"Approved and signed {what}, but handing it to the blockchain failed"
    return Done({"tx_hash": tx_hash, "status": status}, summary, tx_hash, status)


def policy_status(ctx):
    agent = ctx.agent
    since = time.time() - DAY_SECONDS
    return {
        "agent_status": agent.status,
        "allowed_tools": agent.allowed_tools,
        "allowed_assets": agent.allowed_assets,
        "allowed_recipients": agent.allowed_recipients,
        "limits": {
            asset: {"max_per_tx": plain(limit.max_per_tx), "daily": plain(limit.daily)}
            for asset, limit in agent.limits.items()
        },
        "spent_last_24h": {
            asset: eth(ctx.store.spent_since(ctx.call.agent_id, asset, since)) for asset in agent.allowed_assets
        },
    }

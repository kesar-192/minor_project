"""Decides what part of a tool result the LLM gets to read.

The privacy promise (slides 2 and 5) is that the LLM never sees raw balances, financial history or
ciphertext, only a final total that the user is allowed to see. The Vault and the PHE service are
where that is really enforced. This is a second lock on the agent side, so that a mistake there
cannot put ciphertext or a history dump into the LLM's context.

Rule for get_balance (a proposal for Members 1 and 3 to confirm, see README): the answer is
    {"asset": "ETH", "balance": "9.98"}
a plain decimal string, nothing else. Anything else is withheld, not passed on.
This is the same shape the Vault's stub mode already returns.
"""

import json
import re

ASSET_RE = re.compile(r"^[A-Za-z0-9]{1,10}$")
BALANCE_RE = re.compile(r"^\d{1,30}(\.\d{1,18})?$")  # a Paillier ciphertext is hundreds of digits, so it cannot match
MAX_CHARS = 4000

WITHHELD = {"withheld": "The balance was not a plain decimal total, so it was not shown to the model."}


def guard_for_llm(tool, body):
    """A copy of the Vault's answer that is safe to show the LLM."""
    if tool != "get_balance" or body.get("decision") != "ALLOW":
        return body
    result = body.get("result")
    safe = WITHHELD
    if isinstance(result, dict):
        asset, balance = result.get("asset"), result.get("balance")
        if isinstance(asset, str) and ASSET_RE.match(asset) and isinstance(balance, str) and BALANCE_RE.match(balance):
            safe = {"asset": asset, "balance": balance}
    return {**body, "result": safe}


def render_for_llm(tool, body):
    """The text the LLM reads for a tool result."""
    text = json.dumps(guard_for_llm(tool, body))
    if len(text) > MAX_CHARS:
        return text[:MAX_CHARS] + '... [cut: the result was too long to show]'
    return text

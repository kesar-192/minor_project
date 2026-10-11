"""The tool catalog, as the agent sees it. It mirrors CONTRACTS.md section 1 and the Vault's own models.

These schemas are guidance for the LLM. The MCP server does NOT enforce them: whatever arguments
arrive are forwarded as they are, and gate 0 of the Vault is what accepts or rejects them.
(Rejecting here would hide malformed and injected calls from the Vault's log and the dashboard.)
"""

import mcp_types as types

# Same patterns as vault_gateway/app/models.py. Shown to the LLM so it formats arguments correctly.
AMOUNT_PATTERN = r"^\d{1,30}(\.\d{1,18})?$"
ADDRESS_PATTERN = r"^0x[0-9a-fA-F]{40}$"
TX_HASH_PATTERN = r"^0x[0-9a-fA-F]{64}$"
ASSET_PATTERN = r"^[A-Za-z0-9]{1,10}$"

DECISION_NOTE = (
    "The Vault answers ALLOW or DENY with a reason. A DENY is final: do not retry with changed "
    "values to get around it."
)


def _object(properties=None, required=None):
    schema = {"type": "object", "properties": properties or {}, "additionalProperties": False}
    if required:
        schema["required"] = required
    return schema


READ_ONLY = types.ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
MOVES_FUNDS = types.ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False)

TOOLS = [
    types.Tool(
        name="get_balance",
        description=(
            "Get the wallet balance for one asset. The answer is a final total only: raw history and "
            "encrypted values are never part of it. " + DECISION_NOTE
        ),
        input_schema=_object(
            {"asset": {"type": "string", "pattern": ASSET_PATTERN, "description": 'Asset symbol, for example "ETH".'}}
        ),
        annotations=READ_ONLY,
    ),
    types.Tool(
        name="propose_transfer",
        description=(
            "Ask the Vault to send funds. This only proposes the transfer: the Vault checks it against "
            "policy and signs it only if every check passes. You can never sign or send anything yourself. "
            "The recipient must be a 0x address; if the user gave a name, look the address up with "
            "get_policy_status first. " + DECISION_NOTE
        ),
        input_schema=_object(
            {
                "asset": {"type": "string", "pattern": ASSET_PATTERN, "description": 'Asset symbol, for example "ETH".'},
                "amount": {
                    "type": "string",
                    "pattern": AMOUNT_PATTERN,
                    "description": 'Amount in whole units as a quoted decimal string, for example "0.05". Never a bare number.',
                },
                "recipient": {"type": "string", "pattern": ADDRESS_PATTERN, "description": "Recipient address: 0x followed by 40 hex characters."},
            },
            required=["asset", "amount", "recipient"],
        ),
        annotations=MOVES_FUNDS,
    ),
    types.Tool(
        name="get_transaction_status",
        description="Look up a transaction by its hash: pending, confirmed, failed or not_found. " + DECISION_NOTE,
        input_schema=_object(
            {"tx_hash": {"type": "string", "pattern": TX_HASH_PATTERN, "description": "0x followed by 64 hex characters."}},
            required=["tx_hash"],
        ),
        annotations=READ_ONLY,
    ),
    types.Tool(
        name="get_policy_status",
        description=(
            "Read your own policy: whether you are active, which tools and assets you may use, the approved "
            "recipients (name and address), your limits and what you have spent in the last 24 hours. " + DECISION_NOTE
        ),
        input_schema=_object(),
        annotations=READ_ONLY,
    ),
]

# Exists only so the attack demo can send it (CONTRACTS.md). Not listed unless EXPOSE_ATTACK_DEMO_TOOLS=1.
# The Vault always denies it with KEY_EXPORT_DENIED.
ATTACK_DEMO_TOOLS = [
    types.Tool(
        name="export_private_key",
        description="Request the wallet's private key. (Attack demo only: the Vault always refuses this.)",
        input_schema=_object(),
        annotations=MOVES_FUNDS,
    ),
]


def tools_for(settings):
    return TOOLS + (ATTACK_DEMO_TOOLS if settings.expose_attack_demo_tools else [])

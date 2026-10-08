"""The tool catalog: which tools exist, their argument shapes, and what runs once a call is allowed."""

from .models import GetBalanceArgs, NoArgs, ProposeTransferArgs, TransactionStatusArgs

TOOL_ARGS = {
    "get_balance": GetBalanceArgs,
    "propose_transfer": ProposeTransferArgs,
    "get_transaction_status": TransactionStatusArgs,
    "get_policy_status": NoArgs,
}

# Tools that move funds. Only these go through gates 3-6.
TRANSFER_TOOLS = {"propose_transfer"}

# Tools that would hand over key material. No mode implements them.
RESTRICTED_TOOLS = {"export_private_key", "get_private_key"}


def run_stub(tool, args):
    """Placeholder results for stub mode: the real shapes, marked "stub": true."""
    if tool == "get_balance":
        return {"asset": args.asset, "balance": "10", "stub": True}
    if tool == "propose_transfer":
        # Approved but never signed or sent: stub mode has no Vault.
        return {"tx_hash": None, "status": "stub", "stub": True}
    if tool == "get_transaction_status":
        return {"tx_hash": args.tx_hash, "status": "not_found", "stub": True}
    if tool == "get_policy_status":
        return {
            "agent_status": "active",
            "allowed_assets": ["ETH", "USDC"],
            "allowed_recipients": [],
            "limits": {},
            "spent_last_24h": "0",
            "stub": True,
        }
    raise KeyError(tool)

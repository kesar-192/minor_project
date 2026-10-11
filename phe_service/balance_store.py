"""Encrypted balance store for get_balance (open point 1).

A balance is kept per (requester_id, asset) as a Paillier ciphertext, so the
plaintext never reaches this store: a client encrypts with the public key and
POSTs the ciphertext. /balance/query decrypts for an authorized requester and
returns a plain decimal string, the only shape the agent is allowed to see.

Demo scope: balances live in memory and are not tied to the chain. See
vault_gateway/contracts/CHAIN_FED_BALANCE.md for the future chain-fed design.
"""

import time
from decimal import Decimal

from phe_logic import (
    AUTHORIZED_REQUESTERS,
    decrypt,
    dict_to_ciphertext,
)

_STORE: dict[tuple[str, str], dict] = {}


class BalanceError(Exception):
    def __init__(self, status_code, detail):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def set_balance(requester_id, asset, ciphertext, exponent, decimals=0):
    previous = _STORE.get((requester_id, asset))
    _STORE[(requester_id, asset)] = {
        "syncs": (previous["syncs"] if previous else 0) + 1,
        "updated": time.time(),
        "ciphertext": str(ciphertext),
        "exponent": int(exponent),
        "decimals": int(decimals),
    }


def query_balance(requester_id, asset):
    if requester_id not in AUTHORIZED_REQUESTERS:
        raise BalanceError(403, "Requester is not authorized")
    record = _STORE.get((requester_id, asset))
    if record is None:
        raise BalanceError(404, f"No balance is stored for {asset}")
    encrypted = dict_to_ciphertext({
        "ciphertext": record["ciphertext"],
        "exponent": record["exponent"],
    })
    value = decrypt(encrypted, requester_id)
    return {"asset": asset, "balance": format_units(value, record["decimals"])}


def record(requester_id, asset):
    """The stored ciphertext record (what the store really holds), or None. For the dashboard demo."""
    return _STORE.get((requester_id, asset))


def format_units(value, decimals):
    """An integer in the smallest unit written as a trimmed decimal string."""
    if decimals <= 0:
        return str(value)
    text = format(Decimal(value).scaleb(-decimals), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def reset():
    """Clear the store (tests)."""
    _STORE.clear()

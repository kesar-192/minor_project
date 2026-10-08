"""Week 1 signing test: prove the private key never leaves Vault.

Signs a 0.01 ETH transfer inside HashiCorp Vault (ethsign plugin), submits it
to Anvil, then checks that the same Vault token is refused when it asks Vault
to export the key.

Run it with the gateway token, not the root token. The root token is allowed
to do everything, so the export check would be meaningless.
"""

import os
import sys
from decimal import Decimal

import hvac
from hvac.exceptions import Forbidden
from web3 import Web3

VAULT_ADDR = os.environ.get("VAULT_ADDR", "http://127.0.0.1:8200")
VAULT_TOKEN = os.environ.get("VAULT_TOKEN", "")
RPC_URL = os.environ.get("ETH_RPC_URL", "http://127.0.0.1:8545")

ALICE = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"  # Anvil test account #1
AMOUNT_ETH = Decimal("0.01")
GAS_LIMIT = 21000  # a plain ETH transfer always uses exactly 21000 gas


def fail(message):
    print(f"FAIL  {message}")
    sys.exit(1)


def main():
    if not VAULT_TOKEN:
        fail("VAULT_TOKEN is not set. Export the gateway token first.")

    vault = hvac.Client(url=VAULT_ADDR, token=VAULT_TOKEN)
    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    if not vault.is_authenticated():
        fail(f"Vault at {VAULT_ADDR} rejected the token")
    if not w3.is_connected():
        fail(f"no blockchain node at {RPC_URL}. Is anvil running?")

    # 1. Find the signing account. Vault only ever gives us its address.
    #    Vault stores it in lowercase and the lookup is case-sensitive,
    #    so we keep Vault's spelling for Vault and a checksummed one for web3.
    listing = vault.list("ethereum/accounts")
    if not listing or not listing["data"]["keys"]:
        fail("no account in Vault. Create one: vault write -f ethereum/accounts")
    vault_address = listing["data"]["keys"][0]
    sender = Web3.to_checksum_address(vault_address)
    print(f"OK    signing account {sender} (key held by Vault)")

    # 2. Read everything the transaction needs from the chain, in our own code.
    balance_before = w3.eth.get_balance(sender)
    if balance_before == 0:
        fail(f"{sender} has no ETH. Fund it: cast rpc anvil_setBalance {sender} 0x8AC7230489E80000")
    value = Web3.to_wei(AMOUNT_ETH, "ether")
    gas_price = w3.eth.gas_price
    tx = {
        "to": ALICE,
        "value": str(value),
        "gas": str(GAS_LIMIT),
        "gasPrice": str(gas_price),
        "nonce": str(w3.eth.get_transaction_count(sender, "pending")),
        "chainId": str(w3.eth.chain_id),
        "data": "0x",  # no contract call; the plugin errors if this is missing
    }
    print(f"OK    balance {Web3.from_wei(balance_before, 'ether')} ETH, sending {AMOUNT_ETH} ETH to Alice")

    # 3. Ask Vault to sign. Only the signed transaction comes back, never the key.
    signed = vault.write_data(f"ethereum/accounts/{vault_address}/sign", data=tx)["data"]
    print(f"OK    Vault signed it, tx hash {signed['transaction_hash']}")

    # 4. Submit the signed transaction and wait for it to be mined.
    tx_hash = w3.eth.send_raw_transaction(signed["signed_transaction"])
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)
    if receipt["status"] != 1:
        fail(f"transaction reverted in block {receipt['blockNumber']}")
    spent = balance_before - w3.eth.get_balance(sender)
    print(f"OK    mined in block {receipt['blockNumber']}, cost {Web3.from_wei(spent, 'ether')} ETH (amount + gas)")

    # 5. The same token must be refused when it asks for the key itself.
    try:
        vault.read(f"ethereum/export/accounts/{vault_address}")
    except Forbidden:
        print("OK    export refused: this token can sign but can never read the key")
    else:
        fail("this token can export the key. Use the gateway token, not the root token.")

    print("PASS  signing test complete")


if __name__ == "__main__":
    main()

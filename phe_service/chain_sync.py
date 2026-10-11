"""Chain-fed balance (CHAIN_FED_BALANCE.md, option 2): copy the signer's real Anvil balance into the PHE service, encrypted.

Reads the balance from the chain, encrypts it with the PHE *public* key and stores the ciphertext via /balance/set.
This process never holds the private key, so the plaintext only ever exists on the chain and inside the PHE service.

    python chain_sync.py --address 0x... [--once]      (needs PHE_API_TOKEN, ETH_RPC_URL)
"""

import argparse
import os
import time

import requests
from phe import paillier


def chain_balance_wei(rpc_url, address):
    reply = requests.post(rpc_url, json={"jsonrpc": "2.0", "id": 1, "method": "eth_getBalance", "params": [address, "latest"]}, timeout=5)
    return int(reply.json()["result"], 16)


def sync_once(base_url, token, rpc_url, address, asset="ETH", requester_id="user123"):
    headers = {"x-api-token": token}
    n = int(requests.get(f"{base_url}/public-key", headers=headers, timeout=5).json()["n"])
    wei = chain_balance_wei(rpc_url, address)
    encrypted = paillier.PaillierPublicKey(n=n).encrypt(wei)
    requests.post(f"{base_url}/balance/set", headers=headers, timeout=5, json={
        "requester_id": requester_id, "asset": asset,
        "ciphertext": str(encrypted.ciphertext()), "exponent": encrypted.exponent, "decimals": 18,
    }).raise_for_status()
    return wei


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--address", required=True)
    ap.add_argument("--base-url", default=os.environ.get("PHE_URL", "http://127.0.0.1:8300"))
    ap.add_argument("--rpc-url", default=os.environ.get("ETH_RPC_URL", "http://127.0.0.1:8545"))
    ap.add_argument("--token", default=os.environ.get("PHE_API_TOKEN", ""))
    ap.add_argument("--interval", type=float, default=3)
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()
    while True:
        try:
            wei = sync_once(a.base_url, a.token, a.rpc_url, a.address)
            if a.once:
                print(f"synced {wei / 10**18} ETH (encrypted)")
        except Exception as exc:  # the chain or PHE may not be up yet; try again
            if a.once:
                raise
            print(f"sync failed: {type(exc).__name__}")
        if a.once:
            break
        time.sleep(a.interval)

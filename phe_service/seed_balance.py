"""Seed an encrypted balance for the demo (open point 1).

Encrypts a balance with the PHE service public key and stores it via
/balance/set, so /balance/query (and therefore the Vault's get_balance) has
something to return. Demo data only: it is not read from the chain.

Example:
    PHE_API_TOKEN=dev-token python seed_balance.py --balance 9.98 --asset ETH
"""

import argparse
import os
from decimal import Decimal

import requests
from phe import paillier


def main():
    parser = argparse.ArgumentParser(description="Store an encrypted balance on the PHE service.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8300")
    parser.add_argument("--token", default=os.environ.get("PHE_API_TOKEN", ""))
    parser.add_argument("--requester-id", default="user123")
    parser.add_argument("--asset", default="ETH")
    parser.add_argument("--balance", default="9.98")
    parser.add_argument("--decimals", type=int, default=18)
    args = parser.parse_args()

    headers = {"x-api-token": args.token}

    response = requests.get(f"{args.base_url}/public-key", headers=headers)
    response.raise_for_status()
    public_key = paillier.PaillierPublicKey(n=int(response.json()["n"]))

    units = int((Decimal(args.balance) * (10 ** args.decimals)).to_integral_value())
    encrypted = public_key.encrypt(units)

    stored = requests.post(
        f"{args.base_url}/balance/set",
        headers=headers,
        json={
            "requester_id": args.requester_id,
            "asset": args.asset,
            "ciphertext": str(encrypted.ciphertext()),
            "exponent": encrypted.exponent,
            "decimals": args.decimals,
        },
    )
    stored.raise_for_status()

    print(f"Stored {args.balance} {args.asset} for {args.requester_id} ({units} units)")


if __name__ == "__main__":
    main()

"""Set up a freshly started dev Vault and Anvil for the gateway, in one go.

Run it after starting `anvil` and `vault server -dev ...` (see the README):

    .venv/bin/python vault_setup/setup_dev.py

It enables the ethsign plugin, creates the signing key inside Vault (or reuses
the one already there), gives that account 10 test ETH if it has none, writes
the gateway's Vault policy, makes a gateway token, and saves the settings to
.env so the gateway starts with one command. Running it twice is safe.

Dev mode only: it uses the root token, which the gateway itself never gets.
"""

import argparse
import os
import sys
from pathlib import Path

import hvac
from web3 import Web3

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))  # lets this script import the app package

from app.signer import MOUNT, VaultSigner  # noqa: E402

POLICY_NAME = "vault-gateway"
FUNDING_WEI = 10 * 10**18  # 10 test ETH


def update_env(path, values):
    """Set KEY=value lines in the shared .env, keeping every other line as it is."""
    lines = path.read_text().splitlines() if path.exists() else []
    for key, value in values.items():
        line = f"{key}={value}"
        for i, existing in enumerate(lines):
            if existing.startswith(f"{key}="):
                lines[i] = line
                break
        else:
            lines.append(line)
    path.write_text("\n".join(lines) + "\n")


def fail(message):
    print(f"FAIL  {message}")
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--vault-addr", default=os.environ.get("VAULT_ADDR", "http://127.0.0.1:8200"))
    parser.add_argument("--root-token", default="root", help="the dev server's root token")
    parser.add_argument("--rpc-url", default=os.environ.get("ETH_RPC_URL", "http://127.0.0.1:8545"))
    parser.add_argument("--env-file", default=str(BASE_DIR.parent / ".env"), help="the one root .env; only VAULT_ADDR, VAULT_TOKEN and ETH_RPC_URL are updated")
    opts = parser.parse_args()

    root = hvac.Client(url=opts.vault_addr, token=opts.root_token, timeout=5)
    try:
        if not root.is_authenticated():
            fail(f"Vault at {opts.vault_addr} rejected the root token '{opts.root_token}'")
    except Exception as exc:
        fail(f"no Vault at {opts.vault_addr} ({exc.__class__.__name__}). Start it first (README, terminal 2).")
    w3 = Web3(Web3.HTTPProvider(opts.rpc_url, request_kwargs={"timeout": 5}))
    if not w3.is_connected():
        fail(f"no blockchain node at {opts.rpc_url}. Start anvil first (README, terminal 1).")

    # 1. The ethsign plugin, mounted at ethereum/.
    if f"{MOUNT}/" in root.sys.list_mounted_secrets_engines()["data"]:
        print(f"OK    ethsign plugin already enabled at {MOUNT}/")
    else:
        try:
            root.sys.enable_secrets_engine(backend_type="ethsign", path=MOUNT)
        except hvac.exceptions.VaultError as exc:
            fail(f"could not enable the ethsign plugin ({exc}). Was Vault started with -dev-plugin-dir?")
        print(f"OK    ethsign plugin enabled at {MOUNT}/")

    # 2. The signing key, created inside Vault. Nobody ever sees it.
    listing = root.list(f"{MOUNT}/accounts")
    accounts = listing["data"]["keys"] if listing else []
    if not accounts:
        accounts = [root.write_data(f"{MOUNT}/accounts", data={})["data"]["address"]]
        print(f"OK    signing key created inside Vault: {Web3.to_checksum_address(accounts[0])}")
    elif len(accounts) == 1:
        print(f"OK    signing key already in Vault: {Web3.to_checksum_address(accounts[0])}")
    else:
        fail(f"Vault holds {len(accounts)} accounts. Restart the dev server for a clean start.")
    address = Web3.to_checksum_address(accounts[0])

    # 3. Test ETH for that account, only if it has none.
    balance = w3.eth.get_balance(address)
    if balance == 0:
        w3.provider.make_request("anvil_setBalance", [address, hex(FUNDING_WEI)])
        print("OK    funded with 10 test ETH on Anvil")
    else:
        print(f"OK    already holds {Web3.from_wei(balance, 'ether')} ETH")

    # 4. The gateway's Vault policy: may sign, may never export the key.
    root.sys.create_or_update_policy(
        name=POLICY_NAME, policy=(BASE_DIR / "vault_setup" / "gateway-policy.hcl").read_text()
    )
    print(f"OK    Vault policy '{POLICY_NAME}' written (sign only, export denied)")

    # 5. A token with only that policy. VaultSigner checks it can't export the key.
    token = root.auth.token.create(policies=[POLICY_NAME], display_name="vault-gateway")["auth"]["client_token"]
    VaultSigner(opts.vault_addr, token)
    print("OK    gateway token created, and Vault confirms it cannot export the key")

    # 6. Settings for the gateway. .env is in .gitignore: never commit it.
    update_env(Path(opts.env_file), {"VAULT_ADDR": opts.vault_addr, "VAULT_TOKEN": token, "ETH_RPC_URL": opts.rpc_url})
    print(f"OK    settings saved to {opts.env_file}")
    print()
    print("Start the gateway:")
    print("  (or just run ./run.sh from the repo root)")


if __name__ == "__main__":
    main()

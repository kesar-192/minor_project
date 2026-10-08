"""Signing through HashiCorp Vault's ethsign plugin. The private key never leaves Vault.

The gateway's Vault token may list accounts and ask for signatures, nothing else
(vault_setup/gateway-policy.hcl). At start-up the gateway asks Vault what its own
token is allowed to do, and refuses to run if that includes exporting the key.
"""

import hvac
from eth_utils import to_checksum_address

MOUNT = "ethereum"


class VaultSigner:
    def __init__(self, url, token, address=None, timeout=5):
        if not token:
            raise RuntimeError("VAULT_TOKEN is not set. Use the gateway token made from vault_setup/gateway-policy.hcl.")
        self._vault = hvac.Client(url=url, token=token, timeout=timeout)
        listing = self._vault.list(f"{MOUNT}/accounts")
        accounts = listing["data"]["keys"] if listing else []
        if not accounts:
            raise RuntimeError("Vault has no signing account. Create one: vault write -f ethereum/accounts")
        if address:
            matches = [a for a in accounts if a == address.lower()]
            if not matches:
                raise RuntimeError(f"SIGNER_ADDRESS {address} is not an account in Vault")
            accounts = matches
        if len(accounts) > 1:
            raise RuntimeError(f"Vault has {len(accounts)} accounts. Pick one with SIGNER_ADDRESS: {', '.join(accounts)}")
        # Vault stores the address in lower case and looks it up case-sensitively,
        # so keep its spelling for Vault and a checksummed one for everything else.
        self._vault_address = accounts[0]
        self.address = to_checksum_address(self._vault_address)
        self._refuse_if_token_can_export()

    def _refuse_if_token_can_export(self):
        # Asks Vault about the token's permissions. This never reads the key.
        path = f"{MOUNT}/export/accounts/{self._vault_address}"
        capabilities = self._vault.sys.get_capabilities(paths=[path])[path]
        if capabilities != ["deny"]:
            raise RuntimeError(
                f"This Vault token could export the private key ({', '.join(capabilities)}). "
                "Start the gateway with the limited gateway token, never the root token."
            )

    def sign(self, tx):
        """Sign a plain ETH transfer. Returns (raw signed transaction, transaction hash)."""
        fields = {
            "to": tx["to"],
            "value": str(tx["value"]),
            "gas": str(tx["gas"]),
            "gasPrice": str(tx["gasPrice"]),
            "nonce": str(tx["nonce"]),
            "chainId": str(tx["chainId"]),
            "data": "0x",  # no contract call; the plugin errors if this is missing
        }
        signed = self._vault.write_data(f"{MOUNT}/accounts/{self._vault_address}/sign", data=fields)["data"]
        return signed["signed_transaction"], signed["transaction_hash"]

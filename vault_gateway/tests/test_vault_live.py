"""Signing through a real HashiCorp Vault and Anvil. Skipped unless both are configured:

    VAULT_TEST_ADDR=http://127.0.0.1:8200 VAULT_TEST_TOKEN=<gateway token> \\
    ANVIL_RPC_URL=http://127.0.0.1:8545 .venv/bin/python -m pytest tests/test_vault_live.py

Add VAULT_TEST_ROOT_TOKEN=root to also check that the root token is refused.
Vault needs the ethsign plugin, one account and the gateway policy (see the README).
"""

import os

import pytest
from fastapi.testclient import TestClient

from app.chain import Chain
from app.main import create_app
from app.signer import VaultSigner

from conftest import ALICE, make_body, real_settings

VAULT_ADDR = os.environ.get("VAULT_TEST_ADDR")
VAULT_TOKEN = os.environ.get("VAULT_TEST_TOKEN")
ROOT_TOKEN = os.environ.get("VAULT_TEST_ROOT_TOKEN")
RPC_URL = os.environ.get("ANVIL_RPC_URL")

pytestmark = pytest.mark.skipif(
    not (VAULT_ADDR and VAULT_TOKEN and RPC_URL),
    reason="set VAULT_TEST_ADDR, VAULT_TEST_TOKEN and ANVIL_RPC_URL to run against a real Vault",
)


@pytest.mark.skipif(not ROOT_TOKEN, reason="set VAULT_TEST_ROOT_TOKEN to check the root token is refused")
def test_root_token_is_refused():
    with pytest.raises(RuntimeError, match="could export the private key"):
        VaultSigner(VAULT_ADDR, ROOT_TOKEN)


def test_vault_signed_transfer_is_confirmed_on_chain(policy_file):
    signer = VaultSigner(VAULT_ADDR, VAULT_TOKEN)
    chain = Chain(RPC_URL)
    chain.w3.provider.make_request("anvil_setBalance", [signer.address, hex(10 * 10**18)])
    alice_before = chain.balance_wei(ALICE)
    settings = real_settings(policy_file, eth_rpc_url=RPC_URL, vault_addr=VAULT_ADDR, vault_token=VAULT_TOKEN)
    with TestClient(create_app(settings)) as client:
        body = client.post("/v1/tool-call", json=make_body()).json()
        assert (body["decision"], body["result"]["status"]) == ("ALLOW", "submitted"), body
        status = client.post("/v1/tool-call", json=make_body(
            "get_transaction_status", {"tx_hash": body["result"]["tx_hash"]})).json()
    assert status["result"]["status"] == "confirmed"
    assert chain.balance_wei(ALICE) - alice_before == 5 * 10**16

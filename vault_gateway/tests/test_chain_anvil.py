"""The full transfer path against a real Anvil node. Skipped unless ANVIL_RPC_URL is set:

    ANVIL_RPC_URL=http://127.0.0.1:8545 .venv/bin/python -m pytest tests/test_chain_anvil.py

Signing here uses Anvil's public test key, so these tests check how transactions
are built and sent without needing Vault. tests/test_vault_live.py covers Vault.
"""

import os

import pytest
from eth_account import Account
from fastapi.testclient import TestClient

from app.chain import Chain
from app.main import create_app

from conftest import ALICE, make_body, real_settings

RPC_URL = os.environ.get("ANVIL_RPC_URL")
ANVIL_ACCOUNT_0 = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"  # pre-funded with 10000 ETH
ANVIL_KEY_0 = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"  # printed by anvil itself

pytestmark = pytest.mark.skipif(not RPC_URL, reason="set ANVIL_RPC_URL to run against a real Anvil node")


class AnvilTestSigner:
    address = ANVIL_ACCOUNT_0

    def sign(self, tx):
        signed = Account.sign_transaction({**tx, "data": b""}, ANVIL_KEY_0)
        return signed.raw_transaction.to_0x_hex(), signed.hash.to_0x_hex()


def test_reads_balance_and_gas_price():
    chain = Chain(RPC_URL)
    assert chain.balance_wei(ANVIL_ACCOUNT_0) > 0
    assert chain.gas_price_wei() > 0


def test_transfer_is_built_sent_and_confirmed(policy_file):
    chain = Chain(RPC_URL)
    alice_before = chain.balance_wei(ALICE)
    settings = real_settings(policy_file, eth_rpc_url=RPC_URL)
    with TestClient(create_app(settings, signer=AnvilTestSigner())) as client:
        body = client.post("/v1/tool-call", json=make_body()).json()
        assert (body["decision"], body["result"]["status"]) == ("ALLOW", "submitted"), body
        status = client.post("/v1/tool-call", json=make_body(
            "get_transaction_status", {"tx_hash": body["result"]["tx_hash"]})).json()
    assert status["result"]["status"] == "confirmed"
    assert chain.balance_wei(ALICE) - alice_before == 5 * 10**16  # exactly 0.05 ETH arrived

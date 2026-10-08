"""Gate 5 (amount + gas <= balance) and gate 6 (per-transaction and 24-hour limits).

The fake chain has 10 ETH and gas at 2 gwei, so every transfer costs its amount
plus 0.000042 ETH. Limits are 0.1 ETH per transaction and 0.5 ETH per 24 hours.
"""

import time

from fastapi.testclient import TestClient

from app.main import create_app

from conftest import ALICE, ETH, FakeSigner, FakeSubmitter, make_body, policy_dict, real_settings, write_policy

GAS = 21000 * 2 * 10**9  # 0.000042 ETH


def transfer(amount):
    return {"asset": "ETH", "amount": amount, "recipient": ALICE}


def test_normal_transfer_passes_gates_5_and_6(real_send):
    checks = {c["gate"]: c for c in real_send(args=transfer("0.05"))["checks"]}
    assert checks["balance"] == {"gate": "balance", "passed": True, "detail": "0.050042 ETH needed, 10 ETH available"}
    assert checks["limits"]["passed"]
    assert checks["limits"]["detail"] == (
        "0.050042 ETH is within 0.1 ETH per transaction, 0.050042 of 0.5 ETH used in 24 hours"
    )


def test_excessive_amount_is_blocked_at_gate_6(real_send, store, signer, submitter):
    # Demo scenario 2: blocked before anything is signed, so no gas is spent.
    body = real_send(args=transfer("5"))
    assert (body["gate_number"], body["reason_code"]) == (6, "LIMIT_PER_TX")
    assert body["reason"] == "5.000042 ETH including gas is over the 0.1 ETH per-transaction limit"
    assert (signer.signed, submitter.received) == ([], [])
    assert store.spent_since("crypto-agent", "ETH", 0) == 0


def test_per_transaction_limit_counts_gas_to_the_wei(real_send):
    exact = real_send(args=transfer("0.099958"))  # + 0.000042 gas = exactly 0.1 ETH
    assert exact["checks"][-1]["gate"] == "limits" and exact["checks"][-1]["passed"]
    over = real_send(args=transfer("0.099958000000000001"))  # one wei more
    assert (over["gate_number"], over["reason_code"]) == (6, "LIMIT_PER_TX")
    assert over["checks"][-1]["detail"] == "0.100000000000000001 ETH > 0.1 ETH per transaction"


def test_insufficient_balance_is_blocked_at_gate_5(real_send, chain):
    chain.balance = ETH // 20  # 0.05 ETH: enough for the amount, not for the gas on top
    body = real_send(args=transfer("0.05"))
    assert (body["gate_number"], body["reason_code"]) == (5, "INSUFFICIENT_BALANCE")
    assert body["reason"] == "Needs 0.050042 ETH including gas, balance is 0.05 ETH"


def test_exact_balance_is_enough(real_send, chain):
    chain.balance = ETH // 20 + GAS
    assert "balance" in [c["gate"] for c in real_send(args=transfer("0.05"))["checks"] if c["passed"]]


def test_daily_limit_counts_the_last_24_hours(real_send, store):
    store.record_spend("crypto-agent", "ETH", 45 * ETH // 100, "earlier-trace")  # 0.45 ETH
    body = real_send(args=transfer("0.05"))
    assert (body["gate_number"], body["reason_code"]) == (6, "LIMIT_DAILY")
    assert body["reason"] == "Would bring the last 24 hours to 0.500042 ETH, over the 0.5 ETH daily limit"


def test_spending_older_than_24_hours_does_not_count(real_send, store):
    store.record_spend("crypto-agent", "ETH", 45 * ETH // 100, "old-trace", at=time.time() - 25 * 3600)
    assert real_send(args=transfer("0.05"))["checks"][-1]["passed"]


def test_balance_is_checked_before_limits(real_send, chain):
    chain.balance = ETH  # 1 ETH: 5 ETH fails gate 5 before gate 6 is reached
    body = real_send(args=transfer("5"))
    assert (body["gate_number"], body["reason_code"]) == (5, "INSUFFICIENT_BALANCE")


def test_unreachable_chain_fails_closed(real_send, chain):
    chain.down = True
    body = real_send(args=transfer("0.05"))
    assert (body["decision"], body["gate_number"], body["reason_code"]) == ("DENY", 5, "GATE_ERROR")


def test_real_chain_client_with_no_node_fails_closed(policy_file):
    # The real web3 client, pointed at a port where nothing is listening.
    settings = real_settings(policy_file, eth_rpc_url="http://127.0.0.1:9")
    with TestClient(create_app(settings, signer=FakeSigner(), submitter=FakeSubmitter())) as client:
        body = client.post("/v1/tool-call", json=make_body(args=transfer("0.05"))).json()
    assert (body["decision"], body["gate_number"], body["reason_code"]) == ("DENY", 5, "GATE_ERROR")


def test_token_transfers_fail_closed_until_supported(real_send, policy_file):
    # Even if the policy allows USDC, there is no token contract yet, so gate 5 refuses.
    write_policy(policy_file, policy_dict(
        allowed_assets=["ETH", "USDC"],
        limits={"ETH": {"max_per_tx": "0.1", "daily": "0.5"}, "USDC": {"max_per_tx": "50", "daily": "200"}},
    ))
    body = real_send(args={"asset": "USDC", "amount": "10", "recipient": ALICE})
    assert (body["gate_number"], body["reason_code"]) == (5, "GATE_ERROR")

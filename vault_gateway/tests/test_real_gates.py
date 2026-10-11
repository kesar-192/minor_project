"""Gates 1-4 enforcing the policy file, end to end through the API."""

import json

import pytest

from app.models import Event

from conftest import ALICE, policy_dict, write_policy, write_policy_text

ATTACKER = "0x000000000000000000000000000000000000dEaD"


def gates_passed(body):
    return [c["gate"] for c in body["checks"] if c["passed"]]


def test_all_six_gates_pass_and_the_transfer_is_signed(real_send):
    # Demo scenario 1.
    body = real_send()
    assert gates_passed(body) == [
        "request_format", "identity", "permitted_tools", "allowed_assets",
        "recipient_allowlist", "balance", "limits",
    ]
    assert body["checks"][4]["detail"] == "Alice (0x7099…79C8) is approved"
    assert body["decision"] == "ALLOW"
    assert body["result"]["status"] == "submitted"


@pytest.mark.parametrize("overrides", [{"agent_id": "evil-agent"}, {"session_token": "wrong-token"}])
def test_unknown_agent_and_wrong_token_get_the_same_answer(real_send, overrides):
    body = real_send(**overrides)
    assert (body["gate_number"], body["reason_code"]) == (1, "AGENT_NOT_AUTHENTICATED")
    assert body["reason"] == "The agent could not be authenticated"


def test_kill_switch(real_send, policy_file):
    write_policy(policy_file, policy_dict(status="suspended"))
    body = real_send()
    assert (body["gate_number"], body["reason_code"]) == (1, "AGENT_SUSPENDED")
    write_policy(policy_file, policy_dict(status="active"))
    assert "identity" in gates_passed(real_send())


def test_tool_missing_from_the_agents_list_is_refused(real_send, policy_file):
    write_policy(policy_file, policy_dict(allowed_tools=["get_balance"]))
    body = real_send()
    assert (body["gate_number"], body["reason_code"]) == (2, "TOOL_NOT_PERMITTED")
    assert body["checks"][-1]["detail"] == "propose_transfer is not on crypto-agent's tool list"


def test_key_export_is_refused(real_send):
    body = real_send("export_private_key", {})
    assert (body["gate_number"], body["reason_code"]) == (2, "KEY_EXPORT_DENIED")


def test_unapproved_asset_stops_at_gate_3(real_send):
    body = real_send(args={"asset": "BTC", "amount": "0.05", "recipient": ALICE})
    assert (body["gate_number"], body["reason_code"]) == (3, "ASSET_NOT_ALLOWED")
    assert body["reason"] == "BTC is not an approved asset (allowed: ETH)"
    assert body["checks"][-1]["gate"] == "allowed_assets"  # gates 4-6 never ran


def test_unapproved_recipient_stops_at_gate_4(real_send):
    # A small, in-limit amount: limits alone would let this theft through.
    body = real_send(args={"asset": "ETH", "amount": "0.01", "recipient": ATTACKER})
    assert (body["gate_number"], body["reason_code"]) == (4, "RECIPIENT_NOT_ALLOWED")
    assert body["reason"] == "0x0000…dEaD is not on the approved recipient list"


def test_read_only_tool_runs_gates_1_and_2_only(real_send):
    body = real_send("get_transaction_status", {"tx_hash": "0x" + "ab" * 32})
    assert gates_passed(body) == ["request_format", "identity", "permitted_tools"]
    assert body["result"] == {"tx_hash": "0x" + "ab" * 32, "status": "confirmed"}


def test_get_balance_fails_closed_when_the_phe_service_is_not_configured(real_send):
    # No PHE client is injected, so the Vault has no balance source and must refuse.
    body = real_send("get_balance", {"asset": "ETH"})
    assert (body["decision"], body["reason_code"]) == ("DENY", "EXECUTION_FAILED")


def test_get_balance_returns_the_phe_answer(real_phe_send, phe):
    # Open point 1: the Vault calls the PHE service and passes its answer back unchanged.
    body = real_phe_send("get_balance", {"asset": "ETH"})
    assert body["decision"] == "ALLOW"
    assert body["result"] == {"asset": "ETH", "balance": "9.98"}
    assert gates_passed(body) == ["request_format", "identity", "permitted_tools"]
    assert phe.asked == ["ETH"]


def test_get_balance_fails_closed_when_the_phe_service_is_unreachable(real_phe_send, phe):
    phe.down = True
    body = real_phe_send("get_balance", {"asset": "ETH"})
    assert (body["decision"], body["reason_code"]) == ("DENY", "EXECUTION_FAILED")


def test_broken_policy_refuses_everything(real_client, real_send, policy_file):
    write_policy_text(policy_file, "agents: [this is not valid")
    body = real_send()
    assert (body["gate_number"], body["reason_code"]) == (1, "POLICY_INVALID")
    event = real_client.get("/v1/events").json()[-1]
    assert (event["type"], event["severity"]) == ("error", "critical")


def test_crashing_gate_fails_closed_without_leaking_details(real_client, real_send):
    def broken_gate(ctx):
        raise RuntimeError("database password is hunter2")

    gateway = real_client.app.state.gateway
    gateway.gates = [(n, name, broken_gate if n == 3 else gate) for n, name, gate in gateway.gates]
    body = real_send()
    assert (body["decision"], body["gate_number"], body["reason_code"]) == ("DENY", 3, "GATE_ERROR")
    assert "hunter2" not in json.dumps(body)


def test_real_mode_events_match_the_contract(real_client, real_send):
    real_send()
    real_send(session_token="wrong-token")
    real_send("export_private_key", {})
    real_send(args={"asset": "BTC", "amount": "0.05", "recipient": ALICE})
    for event in real_client.get("/v1/events").json():
        Event.model_validate(event)

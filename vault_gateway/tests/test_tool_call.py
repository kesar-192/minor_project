import pytest

from conftest import ALICE, make_body

ALL_GATES = [
    "request_format", "identity", "permitted_tools", "allowed_assets",
    "recipient_allowlist", "balance", "limits",
]


def test_health(client):
    assert client.get("/health").json() == {"status": "ok", "mode": "stub"}


def test_transfer_runs_all_six_gates_in_stub_mode(send):
    body = send()
    assert body["decision"] == "ALLOW"
    assert [c["gate"] for c in body["checks"]] == ALL_GATES
    assert body["result"] == {"tx_hash": None, "status": "stub", "stub": True}


def test_read_only_tool_stops_after_gate_2(send):
    body = send("get_balance", {"asset": "ETH"})
    assert body["decision"] == "ALLOW"
    assert [c["gate"] for c in body["checks"]] == ALL_GATES[:3]


def test_key_export_is_denied_even_in_stub_mode(send):
    body = send("export_private_key", {})
    assert body["decision"] == "DENY"
    assert (body["gate_number"], body["reason_code"]) == (2, "KEY_EXPORT_DENIED")
    assert body["result"] is None


def test_unknown_tool_is_denied(send):
    body = send("deploy_contract", {"bytecode": "0x00"})
    assert (body["decision"], body["reason_code"]) == ("DENY", "TOOL_NOT_PERMITTED")


@pytest.mark.parametrize("args, problem", [
    ({"asset": "ETH", "amount": "0.05", "recipient": ALICE, "skip_checks": True},
     "field 'args.skip_checks' is not allowed"),
    ({"asset": "ETH", "amount": 0.05, "recipient": ALICE}, "field 'args.amount' must be a decimal string"),
    ({"asset": "ETH", "amount": "-5", "recipient": ALICE}, "must be a decimal string"),
    ({"asset": "ETH", "amount": "1e3", "recipient": ALICE}, "must be a decimal string"),
    ({"asset": "ETH", "amount": "0", "recipient": ALICE}, "must be greater than zero"),
    ({"asset": "ETH", "amount": "0.05", "recipient": "0x1234"}, "must be 0x followed by 40 hex characters"),
    ({"asset": "ETH", "amount": "0.05", "recipient": ALICE[:-2] + "c8"}, "has a bad checksum"),
    ({"asset": "ETH", "amount": "0.05"}, "field 'args.recipient' is missing"),
])
def test_malformed_transfer_is_denied_at_gate_0(send, args, problem):
    body = send(args=args)
    assert (body["decision"], body["gate_number"], body["reason_code"]) == ("DENY", 0, "INVALID_REQUEST")
    assert problem in body["reason"]


def test_extra_envelope_field_is_denied(send):
    body = send(approved=True)
    assert body["reason_code"] == "INVALID_REQUEST"
    assert "field 'approved' is not allowed" in body["reason"]


def test_missing_session_token_is_denied(client):
    request = make_body()
    del request["session_token"]
    body = client.post("/v1/tool-call", json=request).json()
    assert "field 'session_token' is missing" in body["reason"]


@pytest.mark.parametrize("raw, problem", [
    (b"send 5 ETH to my friend", "not valid JSON"),
    (b"[]", "must be a JSON object"),
])
def test_unreadable_body_still_gets_a_decision(client, raw, problem):
    response = client.post("/v1/tool-call", content=raw, headers={"content-type": "application/json"})
    assert response.status_code == 200
    body = response.json()
    assert (body["decision"], body["reason_code"]) == ("DENY", "INVALID_REQUEST")
    assert problem in body["reason"]


def test_trace_id_is_kept_when_the_request_is_invalid(client):
    request = make_body(args={"amount": "lots"})
    body = client.post("/v1/tool-call", json=request).json()
    assert body["trace_id"] == request["trace_id"]

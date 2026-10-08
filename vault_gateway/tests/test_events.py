import json
from pathlib import Path

from app.events import verify_chain
from app.models import Event

from conftest import ALICE

DEMO_EVENTS = Path(__file__).parent.parent / "contracts" / "examples" / "demo_events.json"


def test_every_decision_is_pushed_to_the_websocket(client, send):
    with client.websocket_connect("/ws/events") as ws:
        send()
        send("export_private_key", {})
        allowed = ws.receive_json()
        denied = ws.receive_json()
    assert (allowed["seq"], allowed["decision"], allowed["severity"]) == (1, "ALLOW", "info")
    assert (denied["seq"], denied["decision"], denied["severity"]) == (2, "DENY", "critical")
    assert denied["gate_label"] == "Permitted tools"


def test_events_match_the_contract(client, send):
    send()
    send("get_balance", {"asset": "eth"})
    send("export_private_key", {})
    client.post("/v1/tool-call", content=b"not json")
    events = client.get("/v1/events").json()
    assert len(events) == 4
    for event in events:
        Event.model_validate(event)


def test_session_token_never_reaches_the_dashboard(client, send):
    send(session_token="super-secret-token")
    assert "super-secret-token" not in client.get("/v1/events").text


def test_event_args_are_normalised(client, send):
    send(args={"asset": "eth", "amount": "0.050", "recipient": ALICE.lower()})
    event = client.get("/v1/events").json()[-1]
    assert event["args"] == {"asset": "ETH", "amount": "0.050", "recipient": ALICE}


def test_backfill_returns_the_latest_events_oldest_first(client, send):
    for _ in range(5):
        send()
    assert [e["seq"] for e in client.get("/v1/events?limit=2").json()] == [4, 5]


def test_hash_chain_detects_tampering(client, send):
    for _ in range(3):
        send()
    events = client.get("/v1/events").json()
    assert verify_chain(events)
    events[1]["args"]["amount"] = "5"
    assert not verify_chain(events)


def test_demo_events_file_matches_the_contract():
    # Member 4 builds the dashboard from this file, so it must stay in step with the code.
    events = json.loads(DEMO_EVENTS.read_text())
    for event in events:
        Event.model_validate(event)
    assert verify_chain(events)

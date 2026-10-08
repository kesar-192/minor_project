"""After all six gates pass: signing, the spend record, and the hand-off to Member 4."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.handoff import HttpSubmitter

from conftest import ALICE, ETH, SENDER, make_body

GAS = 21000 * 2 * 10**9  # 0.000042 ETH at the fake chain's 2 gwei
FIVE_CENTS = 5 * ETH // 100  # 0.05 ETH


def test_signed_transaction_is_exactly_what_the_gates_checked(real_send, signer):
    real_send()
    assert signer.signed == [{
        "to": ALICE, "value": FIVE_CENTS, "gas": 21000, "gasPrice": 2 * 10**9, "nonce": 0, "chainId": 31337,
    }]


def test_handoff_to_member_4_matches_the_contract(real_send, submitter):
    body = real_send()
    (handoff,) = submitter.received
    assert set(handoff) == {"trace_id", "raw_tx", "tx_hash", "from", "to", "value_wei", "chain_id"}
    assert (handoff["from"], handoff["to"], handoff["value_wei"], handoff["chain_id"]) == (
        SENDER, ALICE, str(FIVE_CENTS), 31337)
    assert handoff["tx_hash"] == body["result"]["tx_hash"]


def test_allowed_transfer_event(real_client, real_send):
    real_send()
    event = real_client.get("/v1/events").json()[-1]
    assert (event["decision"], event["tx_status"]) == ("ALLOW", "submitted")
    assert event["reason"] == "Sent 0.05 ETH to Alice. All 6 gates passed."
    assert event["tx_hash"].startswith("0x")


def test_spend_is_recorded_once_signed(real_send, store):
    real_send()
    assert store.spent_since("crypto-agent", "ETH", 0) == FIVE_CENTS + GAS


def test_failed_handoff_still_counts_against_the_limit(real_send, store, submitter):
    submitter.status = "submit_failed"
    body = real_send()
    assert (body["decision"], body["result"]["status"]) == ("ALLOW", "submit_failed")
    assert store.spent_since("crypto-agent", "ETH", 0) == FIVE_CENTS + GAS


def test_signing_failure_fails_closed(real_client, real_send, store, signer, submitter):
    signer.broken = True
    body = real_send()
    assert (body["decision"], body["reason_code"]) == ("DENY", "EXECUTION_FAILED")
    assert (store.spent_since("crypto-agent", "ETH", 0), submitter.received) == (0, [])
    assert real_client.get("/v1/events").json()[-1]["type"] == "error"


def test_parallel_transfers_cannot_beat_the_daily_limit(real_client, signer):
    # Ten 0.09 ETH transfers at once against a 0.5 ETH daily limit: only five fit.
    signer.delay = 0.02  # widens the gap between checking the limit and recording the spend
    gateway = real_client.app.state.gateway
    request = make_body(args={"asset": "ETH", "amount": "0.09", "recipient": ALICE})
    bodies = [json.dumps({**request, "trace_id": make_body()["trace_id"]}).encode() for _ in range(10)]
    with ThreadPoolExecutor(max_workers=10) as pool:
        decisions = [outcome.decision for outcome in pool.map(gateway.evaluate, bodies)]
    assert sum(d.decision == "ALLOW" for d in decisions) == 5
    assert {d.reason_code for d in decisions if d.decision == "DENY"} == {"LIMIT_DAILY"}


def test_policy_status_shows_limits_and_spending_but_not_the_token_hash(real_send):
    real_send()
    result = real_send("get_policy_status", {})["result"]
    assert result["limits"] == {"ETH": {"max_per_tx": "0.1", "daily": "0.5"}}
    assert result["spent_last_24h"] == {"ETH": "0.050042"}
    assert result["allowed_recipients"]["Alice"] == ALICE
    assert "token" not in json.dumps(result)


# ---- the HTTP hand-off, against a real local web server standing in for Member 4 ----

class Member4(BaseHTTPRequestHandler):
    status = 202
    received = []

    def do_POST(self):
        length = int(self.headers["content-length"])
        Member4.received.append((self.path, json.loads(self.rfile.read(length))))
        self.send_response(Member4.status)
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def member4_url():
    Member4.status, Member4.received = 202, []
    server = HTTPServer(("127.0.0.1", 0), Member4)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_http_handoff_posts_to_member_4(member4_url):
    assert HttpSubmitter(member4_url).submit({"trace_id": "t", "raw_tx": "0xf86c"}) == "submitted"
    assert Member4.received == [("/v1/submit", {"trace_id": "t", "raw_tx": "0xf86c"})]


def test_http_handoff_error_answer_is_submit_failed(member4_url):
    Member4.status = 500
    assert HttpSubmitter(member4_url).submit({"trace_id": "t"}) == "submit_failed"


def test_http_handoff_with_nobody_listening_is_submit_failed():
    assert HttpSubmitter("http://127.0.0.1:9").submit({"trace_id": "t"}) == "submit_failed"

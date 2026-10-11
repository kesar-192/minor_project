import json
import logging

import httpx2
import pytest

from secureagent_mcp.config import ServerSettings
from secureagent_mcp.vault_client import VaultClient

TOKEN = "super-secret-session-token"
ALICE = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
ATTACKER = "0x000000000000000000000000000000000000dEaD"
TX_HASH = "0x" + "ab" * 32


@pytest.fixture(autouse=True)
def _quiet_logs(caplog):
    caplog.set_level(logging.DEBUG)


def make_settings(**overrides):
    values = dict(vault_url="http://vault.test:8000", agent_id="crypto-agent", agent_token=TOKEN, timeout_seconds=5)
    values.update(overrides)
    return ServerSettings(**values)


@pytest.fixture
def settings():
    return make_settings()


def decision(payload, decision="ALLOW", **fields):
    """A Vault-style answer for a request payload."""
    body = {
        "trace_id": payload["trace_id"],
        "decision": decision,
        "gate": None,
        "gate_number": None,
        "reason_code": None,
        "reason": None,
        "checks": [{"gate": "request_format", "passed": True, "detail": None}],
        "result": {"ok": True} if decision == "ALLOW" else None,
    }
    body.update(fields)
    return body


class FakeVault:
    """Stands in for the gateway over HTTP. Records every request body it receives.

    `answer` is called with the parsed request body and returns (status, json) or raises
    an httpx2 error. The default answers ALLOW.
    """

    def __init__(self, answer=None):
        self.requests = []   # parsed JSON bodies
        self.raw = []        # httpx2.Request objects
        self.answer = answer or (lambda payload: (200, decision(payload)))

    def handler(self, request):
        self.raw.append(request)
        try:
            payload = json.loads(request.content)
        except ValueError:
            payload = None
        self.requests.append(payload)
        status, body = self.answer(payload)
        if isinstance(body, str):
            return httpx2.Response(status, text=body)
        return httpx2.Response(status, json=body)

    def client(self, settings):
        return VaultClient(settings, transport=httpx2.MockTransport(self.handler))


@pytest.fixture
def fake_vault():
    return FakeVault()

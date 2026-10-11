import hashlib
import os
import time
import uuid

import pytest
import yaml
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

ALICE = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"   # Anvil test account #1
BOB = "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"     # Anvil test account #2
SENDER = "0x148499F121c87d23017dEEB94D8d3cC95Cb98E82"  # stands in for the Vault's signing account
TEST_TOKEN = "test-token"
GWEI = 10**9
ETH = 10**18


class FakeChain:
    """Stands in for Anvil: 10 ETH in the Vault's account, gas at 2 gwei (21000 gas = 0.000042 ETH)."""

    def __init__(self):
        self.balance = 10 * ETH
        self.gas_price = 2 * GWEI
        self.down = False

    def balance_wei(self, address):
        assert address == SENDER
        return self._read(self.balance)

    def gas_price_wei(self):
        return self._read(self.gas_price)

    def nonce(self, address):
        return self._read(0)

    def chain_id(self):
        return self._read(31337)

    def transaction_status(self, tx_hash):
        return self._read("confirmed")

    def _read(self, value):
        if self.down:
            raise ConnectionError("node unreachable")
        return value


class FakeSigner:
    """Stands in for Vault. Records every transaction it is asked to sign."""

    def __init__(self, address=SENDER):
        self.address = address
        self.signed = []
        self.broken = False
        self.delay = 0

    def sign(self, tx):
        if self.broken:
            raise ConnectionError("Vault unreachable")
        time.sleep(self.delay)
        self.signed.append(tx)
        n = len(self.signed)
        return f"0xf86c{n:04x}", "0x" + f"{n:064x}"


class FakeSubmitter:
    """Stands in for Member 4's submitter. Records every hand-off."""

    def __init__(self):
        self.received = []
        self.status = "submitted"

    def submit(self, handoff):
        self.received.append(handoff)
        return self.status


class FakePhe:
    """Stands in for Member 1's PHE service."""

    def __init__(self, value="9.98"):
        self.value = value
        self.asked = []
        self.down = False

    def balance(self, asset):
        if self.down:
            raise ConnectionError("PHE unreachable")
        self.asked.append(asset)
        return {"asset": asset, "balance": self.value}


def make_body(tool="propose_transfer", args=None, **overrides):
    body = {
        "trace_id": str(uuid.uuid4()),
        "agent_id": "crypto-agent",
        "session_token": TEST_TOKEN,
        "tool": tool,
        "args": {"asset": "ETH", "amount": "0.05", "recipient": ALICE} if args is None else args,
    }
    body.update(overrides)
    return body


def policy_dict(**agent_overrides):
    agent = {
        "status": "active",
        "token_sha256": hashlib.sha256(TEST_TOKEN.encode()).hexdigest(),
        "allowed_tools": ["get_balance", "propose_transfer", "get_transaction_status", "get_policy_status"],
        "allowed_assets": ["ETH"],
        "allowed_recipients": {"Alice": ALICE, "Bob": BOB},
        "limits": {"ETH": {"max_per_tx": "0.1", "daily": "0.5"}},
    }
    agent.update(agent_overrides)
    return {"agents": {"crypto-agent": agent}, "restrictions": {"private_key_export": False}}


def write_policy_text(path, text):
    path.write_text(text)
    # Two quick writes can share a coarse file timestamp. Set a fresh one so
    # the store always notices the change, as it would for a human edit.
    now = time.time_ns()
    os.utime(path, ns=(now, now))


def write_policy(path, data):
    write_policy_text(path, yaml.safe_dump(data, sort_keys=False))


def sender(client):
    """Post a tool call and return the decision as a dict."""
    def _send(tool="propose_transfer", args=None, **overrides):
        response = client.post("/v1/tool-call", json=make_body(tool, args, **overrides))
        assert response.status_code == 200
        return response.json()
    return _send


@pytest.fixture
def client():
    app = create_app(Settings(stub_mode=True, cors_origins=[], policy_path=None, db_path=":memory:"))
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def send(client):
    return sender(client)


@pytest.fixture
def policy_file(tmp_path):
    path = tmp_path / "policy.yaml"
    write_policy(path, policy_dict())
    return path


@pytest.fixture
def chain():
    return FakeChain()


@pytest.fixture
def signer():
    return FakeSigner()


@pytest.fixture
def submitter():
    return FakeSubmitter()


@pytest.fixture
def phe():
    return FakePhe()


def real_settings(policy_file, **overrides):
    return Settings(stub_mode=False, cors_origins=[], policy_path=policy_file, db_path=":memory:", **overrides)


@pytest.fixture
def real_client(policy_file, chain, signer, submitter):
    app = create_app(real_settings(policy_file), chain=chain, signer=signer, submitter=submitter)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def real_phe_client(policy_file, chain, signer, submitter, phe):
    app = create_app(real_settings(policy_file), chain=chain, signer=signer, submitter=submitter, phe=phe)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def real_phe_send(real_phe_client):
    return sender(real_phe_client)


@pytest.fixture
def store(real_client):
    return real_client.app.state.gateway.store


@pytest.fixture
def real_send(real_client):
    return sender(real_client)

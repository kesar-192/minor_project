"""A local stand-in for everything around the Vault gateway, so the full demo runs on one laptop.

The gateway itself is the real one (its six gates, policy file and audit log). Only the outside is
faked: the chain (10 ETH, 2 gwei gas), HashiCorp Vault's signing, and Member 4's submitter.
Nothing here is a security claim: it is for developing and demoing the MCP server and agent.
"""

import hashlib
import os
import socket
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import yaml

from secureagent_mcp.config import PACKAGE_ROOT

GATEWAY_DIR = Path(os.environ.get("VAULT_GATEWAY_DIR") or PACKAGE_ROOT.parent / "vault_gateway")
ALICE = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"   # Anvil test account #1
BOB = "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"     # Anvil test account #2
SENDER = "0x148499F121c87d23017dEEB94D8d3cC95Cb98E82"
ETH, GWEI = 10**18, 10**9


def gateway_available():
    return (GATEWAY_DIR / "app" / "main.py").is_file()


class FakeChain:
    def __init__(self):
        self.down = False

    def _read(self, value):
        if self.down:
            raise ConnectionError("node unreachable")
        return value

    def balance_wei(self, address):
        return self._read(10 * ETH)

    def gas_price_wei(self):
        return self._read(2 * GWEI)

    def nonce(self, address):
        return self._read(0)

    def chain_id(self):
        return self._read(31337)

    def transaction_status(self, tx_hash):
        return self._read("confirmed")


class FakeSigner:
    address = SENDER

    def __init__(self):
        self.signed = []

    def sign(self, tx):
        self.signed.append(tx)
        n = len(self.signed)
        return f"0xf86c{n:04x}", "0x" + f"{n:064x}"


class FakeSubmitter:
    def __init__(self):
        self.received = []

    def submit(self, handoff):
        self.received.append(handoff)
        return "submitted"


class FakePhe:
    """Stands in for Member 1's PHE service: one demo ETH balance."""

    def __init__(self, value="9.98"):
        self.value = value
        self.asked = []

    def balance(self, asset):
        self.asked.append(asset)
        return {"asset": asset, "balance": self.value}


def policy_dict(token, status="active"):
    return {"agents": {"crypto-agent": {
        "status": status,
        "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
        "allowed_tools": ["get_balance", "propose_transfer", "get_transaction_status", "get_policy_status"],
        "allowed_assets": ["ETH"],
        "allowed_recipients": {"Alice": ALICE, "Bob": BOB},
        "limits": {"ETH": {"max_per_tx": "0.1", "daily": "0.5"}},
    }}, "restrictions": {"private_key_export": False}}


def write_policy(path, data):
    Path(path).write_text(yaml.safe_dump(data, sort_keys=False))
    now = time.time_ns()
    os.utime(path, ns=(now, now))   # a fresh timestamp, so the gateway notices the edit


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@dataclass
class Running:
    url: str
    signer: FakeSigner
    submitter: FakeSubmitter
    chain: FakeChain
    phe: FakePhe
    policy_path: Path
    store: object
    server: object
    thread: threading.Thread

    def events(self):
        import httpx2
        return httpx2.get(f"{self.url}/v1/events?limit=100").json()

    def stop(self):
        self.server.should_exit = True
        self.thread.join(timeout=10)


def start_gateway(directory, token, stub=False, port=None, host="127.0.0.1"):
    """Start the real gateway app on a local port, with fakes around it."""
    import uvicorn

    if str(GATEWAY_DIR) not in sys.path:
        sys.path.insert(0, str(GATEWAY_DIR))
    from app.config import Settings
    from app.main import create_app

    policy_path = Path(directory) / "policy.yaml"
    write_policy(policy_path, policy_dict(token))
    chain, signer, submitter = FakeChain(), FakeSigner(), FakeSubmitter()
    # If a real PHE service is running (PHE_URL + PHE_API_TOKEN in the environment),
    # get_balance talks to it over HTTP instead of the fake. Good for a live demo.
    use_real_phe = bool(os.environ.get("PHE_URL")) and not stub
    phe = None if use_real_phe else FakePhe()
    settings = Settings(stub_mode=stub, cors_origins=[], policy_path=None if stub else policy_path, db_path=":memory:",
                        phe_url=os.environ.get("PHE_URL") or None,
                        phe_api_token=os.environ.get("PHE_API_TOKEN") or "")
    app = create_app(settings) if stub else create_app(settings, chain=chain, signer=signer, submitter=submitter, phe=phe)
    phe = phe or FakePhe()  # the Running handle still carries a fake for tests that read it
    port = port or free_port()
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    if not server.started:
        raise RuntimeError("the gateway did not start")
    return Running(f"http://{'127.0.0.1' if host == '0.0.0.0' else host}:{port}", signer, submitter, chain, phe,
                   policy_path, app.state.gateway.store, server, thread)

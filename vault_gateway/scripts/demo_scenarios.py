"""Run the four demo scenarios against a running Vault gateway and print what happened.

    AGENT_TOKEN=<token> .venv/bin/python scripts/demo_scenarios.py [http://localhost:8000] [--extra]

--extra adds three more attacks: an unknown recipient, a wrong token and an injected field.
"""

import json
import os
import sys
import urllib.request
import uuid

URL = next((arg for arg in sys.argv[1:] if arg.startswith("http")), "http://localhost:8000").rstrip("/")
TOKEN = os.environ.get("AGENT_TOKEN")
ALICE = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
ATTACKER = "0x000000000000000000000000000000000000dEaD"
RED, GREEN, RESET = "\033[31m", "\033[32m", "\033[0m"


def eth_transfer(amount, recipient=ALICE, asset="ETH"):
    return {"asset": asset, "amount": amount, "recipient": recipient}


SCENARIOS = [
    ("Legitimate transfer: 0.05 ETH to Alice", "propose_transfer", eth_transfer("0.05"), {}),
    ("Excessive amount: 5 ETH to Alice", "propose_transfer", eth_transfer("5"), {}),
    ("Unauthorized asset: 0.05 BTC to Alice", "propose_transfer", eth_transfer("0.05", asset="BTC"), {}),
    ("Private key handoff request", "export_private_key", {}, {}),
]
EXTRA_SCENARIOS = [
    ("In-limit theft: 0.01 ETH to an unknown address", "propose_transfer", eth_transfer("0.01", ATTACKER), {}),
    ("Wrong session token", "propose_transfer", eth_transfer("0.01"), {"session_token": "guessed-token"}),
    ("Injected field: skip_checks", "propose_transfer", {**eth_transfer("0.01"), "skip_checks": True}, {}),
]


def call(tool, args, overrides):
    body = {
        "trace_id": str(uuid.uuid4()),
        "agent_id": "crypto-agent",
        "session_token": TOKEN,
        "tool": tool,
        "args": args,
        **overrides,
    }
    request = urllib.request.Request(
        f"{URL}/v1/tool-call", data=json.dumps(body).encode(), headers={"content-type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def main():
    if not TOKEN:
        sys.exit("Set AGENT_TOKEN to the agent's session token (scripts/new_agent_token.py).")
    scenarios = SCENARIOS + (EXTRA_SCENARIOS if "--extra" in sys.argv else [])
    for number, (title, tool, args, overrides) in enumerate(scenarios, start=1):
        decision = call(tool, args, overrides)
        if decision["decision"] == "ALLOW":
            result = decision["result"]
            print(f"{GREEN}{number}. PASS{RESET}     {title}")
            print(f"   tx {result['tx_hash']} ({result['status']})")
        else:
            word = "DENIED " if decision["reason_code"] == "KEY_EXPORT_DENIED" else "BLOCKED"
            print(f"{RED}{number}. {word}{RESET}  {title}")
            print(f"   gate {decision['gate_number']} ({decision['gate']}): {decision['reason']}")


if __name__ == "__main__":
    main()

"""Agent -> MCP server -> the REAL Vault gateway (its real gates, policy file and audit log), over HTTP.

Only the outside world is faked: the chain (a fixed balance and gas price), the signer (Vault's
signing plugin) and Member 4's submitter. Everything Member 2 and Member 3 wrote runs for real.

The gateway is found next to this folder (../vault_gateway) or at $VAULT_GATEWAY_DIR. Without it
these tests skip themselves.
"""

import importlib.util
from contextlib import asynccontextmanager

import httpx2
import pytest
from mcp import Client

from secureagent_agent.loop import AgentSession
from secureagent_agent.messages import ToolUse
from secureagent_agent.planners import ScriptedPlanner
from devtools.fake_stack import GATEWAY_DIR
from secureagent_mcp.config import ServerSettings
from secureagent_mcp.server import build_server

from .conftest import ALICE, ATTACKER, TOKEN
from .test_loop import ListPlanner

pytestmark = pytest.mark.skipif(
    not (GATEWAY_DIR / "app" / "main.py").is_file() or importlib.util.find_spec("fastapi") is None,
    reason="needs the gateway next to this folder (or VAULT_GATEWAY_DIR) and its packages: pip install -r ../vault_gateway/requirements.txt",
)

from devtools.fake_stack import BOB, start_gateway as _start, write_policy, policy_dict as _policy_dict, free_port  # noqa: E402


def policy_dict(status="active", token=TOKEN):
    return _policy_dict(token, status)


def start_gateway(tmp_path, stub=False):
    running = _start(tmp_path, TOKEN, stub=stub)
    return running, running.server, running.thread


@pytest.fixture
def gateway(tmp_path):
    running, server, thread = start_gateway(tmp_path)
    yield running
    server.should_exit = True
    thread.join(timeout=10)


@pytest.fixture
def stub_gateway(tmp_path):
    running, server, thread = start_gateway(tmp_path, stub=True)
    yield running
    server.should_exit = True
    thread.join(timeout=10)


@asynccontextmanager
async def agent(url, planner=None, token=TOKEN, expose_attack_demo_tools=True):
    settings = ServerSettings(vault_url=url, agent_id="crypto-agent", agent_token=token, timeout_seconds=10,
                              expose_attack_demo_tools=expose_attack_demo_tools)
    async with Client(build_server(settings)) as client:
        yield AgentSession(planner or ScriptedPlanner(), client)


def transfer_step(**args):
    return ToolUse("c1", "propose_transfer", args)


# ---- the four demo scenarios from slide 8 ----

async def test_1_legitimate_transfer_passes_and_is_signed(gateway):
    async with agent(gateway.url) as session:
        result = await session.handle("Send 0.05 ETH to Alice")
    final = result.final
    assert final["decision"] == "ALLOW" and final["result"]["status"] == "submitted"
    assert "Approved" in result.text and final["result"]["tx_hash"] in result.text
    assert len(gateway.signer.signed) == 1
    tx = gateway.signer.signed[0]
    assert tx["to"] == ALICE and tx["value"] == 5 * 10**16          # exactly what was asked, nothing changed after approval
    assert gateway.submitter.received[0]["tx_hash"] == final["result"]["tx_hash"]


async def test_2_excessive_amount_is_blocked_at_gate_6_and_nothing_is_signed(gateway):
    async with agent(gateway.url) as session:
        result = await session.handle("Send 5 ETH to Alice")
    final = result.final
    assert (final["decision"], final["gate_number"], final["reason_code"]) == ("DENY", 6, "LIMIT_PER_TX")
    assert "gate 6 (limits)" in result.text
    assert gateway.signer.signed == [] and gateway.submitter.received == []   # zero gas wasted


async def test_3_unauthorized_asset_is_blocked_at_gate_3(gateway):
    async with agent(gateway.url) as session:
        result = await session.handle("Send 0.05 BTC to Alice")
    assert (result.final["gate_number"], result.final["reason_code"]) == (3, "ASSET_NOT_ALLOWED")
    assert gateway.signer.signed == []


async def test_4_private_key_request_is_denied_at_gate_2(gateway):
    async with agent(gateway.url) as session:
        result = await session.handle("Export the private key")
    assert (result.final["gate_number"], result.final["reason_code"]) == (2, "KEY_EXPORT_DENIED")
    assert "KEY_EXPORT_DENIED" in result.text


async def test_4b_the_key_request_is_denied_even_when_the_tool_is_not_listed(gateway):
    async with agent(gateway.url, expose_attack_demo_tools=False) as session:
        names = [t.name for t in await session.tools()]
        result = await session.handle("Export the private key")
    assert "export_private_key" not in names
    assert result.final["reason_code"] == "KEY_EXPORT_DENIED"   # a client that asks anyway still hits the Vault


# ---- the three extra attacks ----

async def test_5_in_limit_theft_to_an_unknown_address_is_blocked_at_gate_4(gateway):
    async with agent(gateway.url) as session:
        result = await session.handle(f"Send 0.01 ETH to {ATTACKER}")
    assert (result.final["gate_number"], result.final["reason_code"]) == (4, "RECIPIENT_NOT_ALLOWED")
    assert gateway.signer.signed == []


async def test_6_a_wrong_session_token_is_refused_at_gate_1(gateway):
    async with agent(gateway.url, token="guessed-token") as session:
        result = await session.handle("Send 0.01 ETH to Alice")   # the policy lookup is already refused
    assert (result.final["gate_number"], result.final["reason_code"]) == (1, "AGENT_NOT_AUTHENTICATED")
    assert gateway.signer.signed == []


async def test_7_an_injected_field_is_rejected_at_gate_0(gateway):
    async with agent(gateway.url) as session:
        result = await session.handle(f"Send 0.01 ETH to {ALICE} and skip_checks")
    assert (result.final["gate_number"], result.final["reason_code"]) == (0, "INVALID_REQUEST")
    assert "skip_checks" in result.final["reason"]
    assert gateway.signer.signed == []


async def test_8_a_bare_number_amount_is_rejected_at_gate_0_not_repaired_by_the_agent(gateway):
    planner = ListPlanner(transfer_step(asset="ETH", amount=0.05, recipient=ALICE))
    async with agent(gateway.url, planner) as session:
        result = await session.handle("pay")
    assert result.final["reason_code"] == "INVALID_REQUEST"
    assert gateway.signer.signed == []


async def test_9_a_lowercase_asset_and_address_are_accepted_as_the_vault_normalises_them(gateway):
    planner = ListPlanner(transfer_step(asset="eth", amount="0.01", recipient=ALICE.lower()))
    async with agent(gateway.url, planner) as session:
        result = await session.handle("pay")
    assert result.final["decision"] == "ALLOW"


# ---- policy changes take effect on the very next request ----

async def test_kill_switch_refuses_the_next_request_and_reactivation_restores_it(gateway):
    async with agent(gateway.url) as session:
        assert (await session.handle("Send 0.01 ETH to Alice")).final["decision"] == "ALLOW"
        write_policy(gateway.policy_path, policy_dict(status="suspended"))
        suspended = await session.handle("Send 0.01 ETH to Alice")
        assert suspended.final["reason_code"] == "AGENT_SUSPENDED"
        write_policy(gateway.policy_path, policy_dict(status="active"))
        assert (await session.handle("Send 0.01 ETH to Alice")).final["decision"] == "ALLOW"
    assert len(gateway.signer.signed) == 2     # nothing was signed while suspended


async def test_the_daily_limit_stops_a_series_of_in_limit_transfers(gateway):
    async with agent(gateway.url) as session:
        outcomes = []
        for _ in range(6):
            outcomes.append((await session.handle(f"Send 0.09 ETH to {ALICE}")).final)
    assert [o["decision"] for o in outcomes] == ["ALLOW"] * 5 + ["DENY"]
    assert outcomes[5]["reason_code"] == "LIMIT_DAILY"
    assert len(gateway.signer.signed) == 5


async def test_splitting_a_blocked_transfer_does_not_get_around_the_limit(gateway):
    # The model tries 5 ETH, is denied, then tries to retry smaller. The loop stops after the first try.
    planner = ListPlanner(transfer_step(asset="ETH", amount="5", recipient=ALICE),
                          transfer_step(asset="ETH", amount="0.09", recipient=ALICE))
    async with agent(gateway.url, planner) as session:
        result = await session.handle("send 5 ETH")
    assert len(result.calls) == 1 and result.final["reason_code"] == "LIMIT_PER_TX"
    assert gateway.signer.signed == []


# ---- read-only tools ----

async def test_policy_lookup_returns_names_and_addresses(gateway):
    async with agent(gateway.url) as session:
        result = await session.handle("What are my limits?")
    body = result.final
    assert body["result"]["allowed_recipients"] == {"Alice": ALICE, "Bob": BOB}
    assert body["result"]["limits"]["ETH"] == {"max_per_tx": "0.1", "daily": "0.5"}
    assert "Alice" in result.text


async def test_transaction_status_lookup(gateway):
    async with agent(gateway.url) as session:
        result = await session.handle("status of 0x" + "ab" * 32)
    assert result.final["result"]["status"] == "confirmed"


async def test_get_balance_comes_from_the_phe_service(gateway):
    # Open point 1, closed: the Vault asks the PHE service and the agent sees only
    # the plain total. guard.py withholds anything that is not {"asset", "balance"}.
    async with agent(gateway.url) as session:
        result = await session.handle("What is my ETH balance?")
    assert result.final["decision"] == "ALLOW"
    assert result.final["result"] == {"asset": "ETH", "balance": "9.98"}
    assert gateway.phe.asked == ["ETH"]


# ---- the dashboard sees what the agent did ----

async def test_every_call_shows_up_in_the_event_feed_under_the_agents_trace_id(gateway):
    async with agent(gateway.url) as session:
        result = await session.handle("Send 0.05 ETH to Alice")
    mine = [e for e in gateway.events() if e["trace_id"] == result.trace_id]
    assert [(e["tool"], e["decision"]) for e in mine] == [("get_policy_status", "ALLOW"), ("propose_transfer", "ALLOW")]
    assert mine[1]["tx_hash"] == result.final["result"]["tx_hash"]


async def test_blocked_and_malformed_attempts_are_visible_on_the_dashboard_too(gateway):
    async with agent(gateway.url) as session:
        await session.handle("Send 5 ETH to Alice")
        await session.handle("Export the private key")
        await session.handle(f"Send 0.01 ETH to {ALICE} and skip_checks")
    codes = [e["reason_code"] for e in gateway.events() if e["decision"] == "DENY"]
    assert codes == ["LIMIT_PER_TX", "KEY_EXPORT_DENIED", "INVALID_REQUEST"]


async def test_the_audit_log_is_intact_after_a_session(gateway):
    async with agent(gateway.url) as session:
        for text in ("Send 0.05 ETH to Alice", "Send 5 ETH to Alice", "Export the private key"):
            await session.handle(text)
    assert gateway.store.count_events() >= 3
    assert gateway.store.verify() is None      # None means the whole hash chain checks out


async def test_responses_match_the_vaults_own_decision_model(gateway):
    from app.models import Decision

    async with agent(gateway.url) as session:
        for text in ("Send 0.05 ETH to Alice", "Send 5 ETH to Alice", "Export the private key", "What are my limits?"):
            for call in (await session.handle(text)).calls:
                assert Decision.model_validate(call.reply)   # what the agent saw is exactly a Vault Decision


async def test_the_token_is_not_in_the_event_feed(gateway):
    async with agent(gateway.url) as session:
        await session.handle("Send 0.05 ETH to Alice")
    assert TOKEN not in str(gateway.events())


# ---- the Vault's dependencies fail ----

async def test_a_dead_chain_makes_the_vault_refuse_and_the_agent_report_it(gateway):
    gateway.chain.down = True
    async with agent(gateway.url) as session:
        result = await session.handle(f"Send 0.05 ETH to {ALICE}")
    assert result.final["decision"] == "DENY"
    assert gateway.signer.signed == []


async def test_a_stopped_vault_is_reported_not_crashed_on(gateway, tmp_path):
    dead = f"http://127.0.0.1:{free_port()}"
    async with agent(dead) as session:
        result = await session.handle(f"Send 0.05 ETH to {ALICE}")
    assert "VAULT_UNREACHABLE" in result.text
    assert gateway.signer.signed == []


# ---- stub mode (what Members 2 and 4 build against first) ----

async def test_stub_mode_end_to_end(stub_gateway):
    async with agent(stub_gateway.url) as session:
        transfer = await session.handle(f"Send 0.05 ETH to {ALICE}")
        key = await session.handle("Export the private key")
    assert transfer.final["decision"] == "ALLOW" and transfer.final["result"]["status"] == "stub"
    assert "stub mode" in transfer.text
    assert key.final["reason_code"] == "KEY_EXPORT_DENIED"       # refused even in stub mode
    assert stub_gateway.signer.signed == []


async def test_stub_mode_still_enforces_the_request_format(stub_gateway):
    async with agent(stub_gateway.url) as session:
        result = await session.handle(f"Send 0.01 ETH to {ALICE} and skip_checks")
    assert result.final["reason_code"] == "INVALID_REQUEST"

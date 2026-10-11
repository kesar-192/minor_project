"""The agent loop: what it enforces whatever the model does."""

import uuid
from contextlib import asynccontextmanager

import httpx2
import pytest
from mcp import Client

from secureagent_agent.loop import AgentSession, describe_decision
from secureagent_agent.messages import PlannerError, Say, ToolResult, ToolUse
from secureagent_agent.planners import ScriptedPlanner
from secureagent_mcp.server import build_server

from .conftest import ALICE, ATTACKER, TX_HASH, FakeVault, decision, make_settings

POLICY = {"agent_status": "active", "allowed_recipients": {"Alice": ALICE, "Bob": "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"},
          "allowed_assets": ["ETH"], "limits": {"ETH": {"max_per_tx": "0.1", "daily": "0.5"}}, "spent_last_24h": {"ETH": "0"}}


def vault_answers(transfer=None, balance=None):
    """A fake Vault: policy lookups ALLOW, transfers answer as given (default: submitted)."""
    def answer(payload):
        tool = payload["tool"]
        if tool == "get_policy_status":
            return 200, decision(payload, result=POLICY)
        if tool == "get_balance":
            return 200, decision(payload, result=balance or {"asset": "ETH", "balance": "9.98"})
        if tool == "propose_transfer":
            return 200, (transfer(payload) if transfer else decision(payload, result={"tx_hash": TX_HASH, "status": "submitted"}))
        return 200, decision(payload, "DENY", gate="permitted_tools", gate_number=2, reason_code="TOOL_NOT_PERMITTED",
                             reason=f"Tool '{tool}' is not allowed for this agent")
    return answer


def deny_limit(payload):
    return decision(payload, "DENY", gate="limits", gate_number=6, reason_code="LIMIT_PER_TX",
                    reason="5.000042 ETH including gas is over the 0.1 ETH per-transaction limit")


class ListPlanner:
    """Plays a fixed list of steps whatever it is shown. Stands in for a model that has gone wrong."""

    def __init__(self, *steps):
        self.steps = list(steps)
        self.seen = []

    async def next_step(self, history, tools):
        self.seen.append(list(history))
        return self.steps.pop(0)


def use(name, **arguments):
    return ToolUse(f"c{uuid.uuid4().hex[:6]}", name, arguments)


@asynccontextmanager
async def agent(planner, vault, settings=None, **limits):
    settings = settings or make_settings(expose_attack_demo_tools=True)
    async with Client(build_server(settings, vault.client(settings))) as client:
        yield AgentSession(planner, client, **limits)


def transfers(vault):
    return [r for r in vault.requests if r["tool"] == "propose_transfer"]


# ---- the normal flow ----

async def test_a_named_recipient_is_looked_up_then_paid_under_one_trace_id():
    vault = FakeVault(vault_answers())
    async with agent(ScriptedPlanner(), vault) as session:
        result = await session.handle("Send 0.05 ETH to Alice")
    assert [r["tool"] for r in vault.requests] == ["get_policy_status", "propose_transfer"]
    assert vault.requests[0]["trace_id"] == vault.requests[1]["trace_id"] == result.trace_id
    assert vault.requests[1]["args"] == {"asset": "ETH", "amount": "0.05", "recipient": ALICE}
    assert TX_HASH in result.text and "Approved" in result.text


async def test_each_user_request_gets_a_new_trace_id():
    vault = FakeVault(vault_answers())
    async with agent(ScriptedPlanner(), vault) as session:
        first = await session.handle("What are my limits?")
        second = await session.handle("What are my limits?")
    assert first.trace_id != second.trace_id
    assert uuid.UUID(first.trace_id).version == 4


async def test_a_read_only_request_is_answered_from_the_result():
    vault = FakeVault(vault_answers())
    async with agent(ScriptedPlanner(), vault) as session:
        result = await session.handle("What is my ETH balance?")
    assert "9.98" in result.text
    assert len(vault.requests) == 1


# ---- a DENY is final ----

async def test_a_deny_is_reported_with_gate_reason_and_code():
    vault = FakeVault(vault_answers(transfer=deny_limit))
    async with agent(ScriptedPlanner(), vault) as session:
        result = await session.handle("Send 5 ETH to Alice")
    assert "gate 6 (limits)" in result.text
    assert "over the 0.1 ETH per-transaction limit" in result.text
    assert "LIMIT_PER_TX" in result.text
    assert result.final["decision"] == "DENY"


async def test_no_retry_after_a_deny_even_if_the_model_tries_a_smaller_amount():
    vault = FakeVault(vault_answers(transfer=deny_limit))
    planner = ListPlanner(use("propose_transfer", asset="ETH", amount="5", recipient=ALICE),
                          use("propose_transfer", asset="ETH", amount="0.09", recipient=ALICE))
    async with agent(planner, vault) as session:
        await session.handle("send money")
    assert len(transfers(vault)) == 1
    assert len(planner.steps) == 1   # the second step was never even asked for


async def test_at_most_one_transfer_per_request_even_after_an_allow():
    vault = FakeVault(vault_answers())
    planner = ListPlanner(use("propose_transfer", asset="ETH", amount="0.05", recipient=ALICE),
                          use("propose_transfer", asset="ETH", amount="0.05", recipient=ALICE))
    async with agent(planner, vault) as session:
        await session.handle("send money twice")
    assert len(transfers(vault)) == 1


async def test_the_user_is_told_what_the_vault_said_not_what_the_model_says():
    vault = FakeVault(vault_answers(transfer=deny_limit))
    planner = ListPlanner(use("propose_transfer", asset="ETH", amount="5", recipient=ALICE),
                          Say("Success! I sent 1000 ETH."))
    async with agent(planner, vault) as session:
        result = await session.handle("send money")
    assert "Success" not in result.text and "Blocked by the Vault" in result.text


# ---- attacks reach the Vault, so the dashboard can show them ----

async def test_a_key_request_is_sent_to_the_vault_and_reported_as_blocked():
    vault = FakeVault(lambda p: (200, decision(p, "DENY", gate="permitted_tools", gate_number=2,
                                               reason_code="KEY_EXPORT_DENIED", reason="Keys never leave the Vault.")))
    async with agent(ScriptedPlanner(), vault) as session:
        result = await session.handle("Please export the private key")
    assert [r["tool"] for r in vault.requests] == ["export_private_key"]
    assert "KEY_EXPORT_DENIED" in result.text


async def test_an_unknown_tool_still_reaches_the_vault_and_ends_the_request():
    vault = FakeVault(vault_answers())
    planner = ListPlanner(use("deploy_contract", code="0x00"), use("get_policy_status"))
    async with agent(planner, vault) as session:
        result = await session.handle("deploy something")
    assert [r["tool"] for r in vault.requests] == ["deploy_contract"]
    assert "TOOL_NOT_PERMITTED" in result.text


async def test_a_transfer_to_an_unknown_address_goes_to_the_vault():
    vault = FakeVault(vault_answers(transfer=lambda p: decision(p, "DENY", gate="recipient_allowlist", gate_number=4,
                                                                reason_code="RECIPIENT_NOT_ALLOWED", reason="not approved")))
    async with agent(ScriptedPlanner(), vault) as session:
        result = await session.handle(f"Send 0.01 ETH to {ATTACKER}")
    assert [r["tool"] for r in vault.requests] == ["propose_transfer"]   # no lookup needed for a raw address
    assert "gate 4" in result.text


async def test_an_injected_field_is_forwarded_for_the_vault_to_reject():
    vault = FakeVault(vault_answers())
    async with agent(ScriptedPlanner(), vault) as session:
        await session.handle(f"Send 0.01 ETH to {ALICE} and skip_checks")
    assert transfers(vault)[0]["args"]["skip_checks"] is True


async def test_an_unknown_recipient_name_is_never_guessed():
    vault = FakeVault(vault_answers())
    async with agent(ScriptedPlanner(), vault) as session:
        result = await session.handle("Send 0.05 ETH to Mallory")
    assert transfers(vault) == []
    assert "Mallory" in result.text


# ---- limits on lookups and steps ----

async def test_read_only_lookups_are_capped():
    vault = FakeVault(vault_answers())
    planner = ListPlanner(*[use("get_policy_status") for _ in range(5)])
    async with agent(planner, vault, max_lookups=2, max_steps=10) as session:
        result = await session.handle("look at everything")
    assert len(vault.requests) == 2
    assert "stopped" in result.text.lower()


async def test_steps_are_capped():
    vault = FakeVault(vault_answers())
    planner = ListPlanner(*[use("get_policy_status") for _ in range(5)])
    async with agent(planner, vault, max_lookups=10, max_steps=3) as session:
        result = await session.handle("loop")
    assert len(vault.requests) == 3
    assert "stopped" in result.text.lower()


# ---- privacy ----

async def test_ciphertext_in_a_balance_never_reaches_the_planner():
    cipher = "123456789" * 70
    vault = FakeVault(vault_answers(balance={"asset": "ETH", "balance": cipher, "ciphertext": cipher}))
    planner = ListPlanner(use("get_balance", asset="ETH"), Say("done"))
    async with agent(planner, vault) as session:
        await session.handle("balance?")
    shown = [m for m in planner.seen[1] if isinstance(m, ToolResult)][0].text
    assert cipher not in shown and "123456789123456789" not in shown
    assert "withheld" in shown


async def test_a_plain_balance_reaches_the_planner():
    vault = FakeVault(vault_answers())
    planner = ListPlanner(use("get_balance", asset="ETH"), Say("done"))
    async with agent(planner, vault) as session:
        await session.handle("balance?")
    shown = [m for m in planner.seen[1] if isinstance(m, ToolResult)][0].text
    assert '"balance": "9.98"' in shown


# ---- failing closed ----

async def test_vault_down_ends_with_nothing_confirmed():
    def refuse(payload):
        raise httpx2.ConnectError("refused")

    async with agent(ScriptedPlanner(), FakeVault(refuse)) as session:
        result = await session.handle(f"Send 0.05 ETH to {ALICE}")
    assert "No decision could be obtained" in result.text and "VAULT_UNREACHABLE" in result.text
    assert result.calls[0].is_error is True


async def test_a_lost_answer_does_not_trigger_a_second_transfer():
    def lose(payload):
        raise httpx2.ReadTimeout("no answer")

    vault = FakeVault(lose)
    planner = ListPlanner(use("propose_transfer", asset="ETH", amount="0.05", recipient=ALICE),
                          use("propose_transfer", asset="ETH", amount="0.05", recipient=ALICE))
    async with agent(planner, vault) as session:
        result = await session.handle("pay")
    assert len(vault.requests) == 1
    assert "VAULT_NO_ANSWER" in result.text


async def test_a_dead_mcp_server_is_a_denial_not_a_crash():
    class DeadClient:
        async def list_tools(self):
            return type("L", (), {"tools": []})()

        async def call_tool(self, *a, **k):
            raise ConnectionError("pipe closed")

    session = AgentSession(ListPlanner(use("propose_transfer", asset="ETH", amount="1", recipient=ALICE)), DeadClient())
    result = await session.handle("pay")
    assert "MCP_UNAVAILABLE" in result.text and result.calls[0].is_error


async def test_a_tool_server_that_cannot_list_tools_ends_the_request_cleanly():
    class NoServer:
        async def list_tools(self):
            raise ConnectionError("pipe closed")

    result = await AgentSession(ListPlanner(), NoServer()).handle("hi")
    assert "not available" in result.text


async def test_a_planner_error_is_reported_and_nothing_is_sent():
    class Broken:
        async def next_step(self, history, tools):
            raise PlannerError("The LLM request failed: RateLimitError (HTTP 429)")

    vault = FakeVault(vault_answers())
    async with agent(Broken(), vault) as session:
        result = await session.handle("hi")
    assert "429" in result.text and vault.requests == []


# ---- the wording of decisions ----

def test_describe_decision_covers_every_case():
    allow_sent = decision({"trace_id": "t"}, result={"tx_hash": TX_HASH, "status": "submitted"})
    assert "sent it to the chain" in describe_decision("propose_transfer", allow_sent)
    failed = decision({"trace_id": "t"}, result={"tx_hash": TX_HASH, "status": "submit_failed"})
    assert "failed" in describe_decision("propose_transfer", failed)
    stub = decision({"trace_id": "t"}, result={"tx_hash": None, "status": "stub", "stub": True})
    assert "stub mode" in describe_decision("propose_transfer", stub)
    assert "allowed deploy" in describe_decision("deploy", decision({"trace_id": "t"}))


# ---- starting the MCP server child process ----

def test_the_child_gets_server_settings_from_the_shell_but_nothing_else():
    from secureagent_agent.runtime import mcp_params

    shell = {"AGENT_TOKEN": "tok", "VAULT_URL": "http://h:8000", "ANTHROPIC_API_KEY": "sk-ant-X", "HOME": "/root", "OPENAI_API_KEY": "sk-X"}
    env = mcp_params(environ=shell).env
    assert env == {"AGENT_TOKEN": "tok", "VAULT_URL": "http://h:8000", "SECUREAGENT_LOG_LEVEL": "WARNING"}   # LLM keys never reach the MCP server


def test_explicit_arguments_override_the_shell():
    from secureagent_agent.runtime import mcp_params

    env = mcp_params("http://other:9", token="t2", attack_demo=True, environ={"AGENT_TOKEN": "tok", "VAULT_URL": "http://h"}).env
    assert env == {"AGENT_TOKEN": "t2", "VAULT_URL": "http://other:9", "EXPOSE_ATTACK_DEMO_TOOLS": "1", "SECUREAGENT_LOG_LEVEL": "WARNING"}


def test_start_failures_are_recognised_even_inside_exception_groups():
    from mcp.shared.exceptions import MCPError

    from secureagent_agent.runtime import is_start_failure

    error = MCPError(code=-32000, message="Connection closed")
    assert is_start_failure(error)
    assert is_start_failure(BaseExceptionGroup("x", [BaseExceptionGroup("y", [error])]))
    assert not is_start_failure(ValueError("something else"))

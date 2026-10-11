"""The MCP server, driven through a real MCP client (in-process)."""

import json
import uuid

import anyio
import httpx2
import pytest
from mcp import Client

from secureagent_mcp.server import TRACE_META_KEY, build_server

from .conftest import ALICE, TOKEN, FakeVault, decision, make_settings

TRANSFER = {"asset": "ETH", "amount": "0.05", "recipient": ALICE}
DEFAULT_TOOLS = ["get_balance", "propose_transfer", "get_transaction_status", "get_policy_status"]


def connect(settings, vault):
    return Client(build_server(settings, vault.client(settings)))


# ---- the catalog ----

async def test_lists_the_four_contract_tools(settings, fake_vault):
    async with connect(settings, fake_vault) as client:
        tools = await client.list_tools()
    assert [t.name for t in tools.tools] == DEFAULT_TOOLS


async def test_key_export_is_not_listed_by_default(settings, fake_vault):
    async with connect(settings, fake_vault) as client:
        names = [t.name for t in (await client.list_tools()).tools]
    assert "export_private_key" not in names


async def test_key_export_is_listed_only_when_the_demo_flag_is_on(fake_vault):
    settings = make_settings(expose_attack_demo_tools=True)
    async with connect(settings, fake_vault) as client:
        names = [t.name for t in (await client.list_tools()).tools]
    assert names == DEFAULT_TOOLS + ["export_private_key"]


async def test_transfer_schema_tells_the_llm_to_use_quoted_decimal_strings(settings, fake_vault):
    async with connect(settings, fake_vault) as client:
        tool = next(t for t in (await client.list_tools()).tools if t.name == "propose_transfer")
    schema = tool.input_schema
    assert schema["required"] == ["asset", "amount", "recipient"]
    assert schema["properties"]["amount"]["type"] == "string"
    assert schema["additionalProperties"] is False
    assert schema["properties"]["recipient"]["pattern"] == r"^0x[0-9a-fA-F]{40}$"


async def test_read_only_tools_are_marked_read_only(settings, fake_vault):
    async with connect(settings, fake_vault) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    for name in ("get_balance", "get_transaction_status", "get_policy_status"):
        assert tools[name].annotations.read_only_hint is True
    assert tools["propose_transfer"].annotations.read_only_hint is False


async def test_nothing_secret_is_in_what_the_llm_can_see(settings, fake_vault):
    async with connect(settings, fake_vault) as client:
        listing = (await client.list_tools()).model_dump_json()
        instructions = client.instructions
    assert instructions and "Vault" in instructions   # make sure we really inspected something
    assert TOKEN not in listing + instructions
    assert "session_token" not in listing  # the LLM is never offered a way to set it
    assert "agent_id" not in listing


# ---- forwarding ----

async def test_the_answer_comes_back_exactly_as_the_vault_gave_it(settings):
    answer = {}

    def respond(payload):
        answer.update(decision(payload, result={"tx_hash": "0x" + "11" * 32, "status": "submitted"}))
        return 200, answer

    async with connect(settings, FakeVault(respond)) as client:
        result = await client.call_tool("propose_transfer", TRANSFER)
    assert result.structured_content == answer
    assert json.loads(result.content[0].text) == answer
    assert result.is_error is False


async def test_a_vault_deny_is_a_normal_answer_not_a_tool_error(settings):
    vault = FakeVault(lambda p: (200, decision(p, "DENY", gate="limits", gate_number=6, reason_code="LIMIT_PER_TX", reason="too much")))
    async with connect(settings, vault) as client:
        result = await client.call_tool("propose_transfer", {**TRANSFER, "amount": "5"})
    assert result.is_error is False
    assert result.structured_content["decision"] == "DENY"
    assert result.structured_content["reason_code"] == "LIMIT_PER_TX"
    assert "source" not in result.structured_content  # made by the Vault, not here


async def test_arguments_reach_the_vault_exactly_as_the_agent_sent_them(settings, fake_vault):
    odd = {"asset": "BTC", "amount": 0.05, "recipient": "alice", "skip_checks": True}
    async with connect(settings, fake_vault) as client:
        await client.call_tool("propose_transfer", odd)
    assert fake_vault.requests[0]["args"] == odd
    assert fake_vault.requests[0]["tool"] == "propose_transfer"


async def test_identity_comes_from_settings_never_from_the_caller(settings, fake_vault):
    async with connect(settings, fake_vault) as client:
        await client.call_tool("get_balance", {"asset": "ETH", "agent_id": "admin", "session_token": "x"})
    sent = fake_vault.requests[0]
    assert sent["agent_id"] == "crypto-agent" and sent["session_token"] == TOKEN


async def test_a_tool_that_is_not_listed_is_still_forwarded_so_the_vault_can_refuse_it(settings):
    vault = FakeVault(lambda p: (200, decision(p, "DENY", gate="permitted_tools", gate_number=2, reason_code="TOOL_NOT_PERMITTED")))
    async with connect(settings, vault) as client:
        result = await client.call_tool("deploy_contract", {"code": "0x00"})
    assert vault.requests[0]["tool"] == "deploy_contract"
    assert result.structured_content["reason_code"] == "TOOL_NOT_PERMITTED"


async def test_key_export_is_forwarded_even_when_not_listed(settings):
    vault = FakeVault(lambda p: (200, decision(p, "DENY", gate="permitted_tools", gate_number=2, reason_code="KEY_EXPORT_DENIED")))
    async with connect(settings, vault) as client:
        result = await client.call_tool("export_private_key", {})
    assert vault.requests[0]["tool"] == "export_private_key"
    assert result.structured_content["reason_code"] == "KEY_EXPORT_DENIED"


async def test_a_call_with_no_arguments_is_forwarded_with_empty_args(settings, fake_vault):
    async with connect(settings, fake_vault) as client:
        await client.call_tool("get_policy_status")
    assert fake_vault.requests[0]["args"] == {}


async def test_the_server_has_no_opinion_of_its_own(settings):
    allow_all = FakeVault(lambda p: (200, decision(p)))
    async with connect(settings, allow_all) as client:
        huge = await client.call_tool("propose_transfer", {"asset": "ETH", "amount": "999999", "recipient": ATTACKER_ADDR})
    assert huge.structured_content["decision"] == "ALLOW"   # the Vault said so; the server does not second-guess it


ATTACKER_ADDR = "0x000000000000000000000000000000000000dEaD"


# ---- failing closed ----

async def test_vault_down_gives_the_agent_a_marked_denial_and_an_error_flag(settings):
    def refuse(payload):
        raise httpx2.ConnectError("refused")

    async with connect(settings, FakeVault(refuse)) as client:
        result = await client.call_tool("propose_transfer", TRANSFER)
    body = result.structured_content
    assert result.is_error is True
    assert (body["decision"], body["reason_code"], body["source"]) == ("DENY", "VAULT_UNREACHABLE", "mcp_server")


async def test_a_vault_500_is_a_denial(settings):
    async with connect(settings, FakeVault(lambda p: (500, {"detail": "internal"}))) as client:
        result = await client.call_tool("get_balance", {"asset": "ETH"})
    assert result.is_error is True
    assert result.structured_content["reason_code"] == "VAULT_ERROR"


# ---- trace ids ----

async def test_the_agents_trace_id_is_used_for_every_call_of_one_request(settings, fake_vault):
    trace_id = str(uuid.uuid4())
    async with connect(settings, fake_vault) as client:
        first = await client.call_tool("get_policy_status", {}, meta={TRACE_META_KEY: trace_id})
        second = await client.call_tool("propose_transfer", TRANSFER, meta={TRACE_META_KEY: trace_id})
    assert [r["trace_id"] for r in fake_vault.requests] == [trace_id, trace_id]
    assert first.structured_content["trace_id"] == second.structured_content["trace_id"] == trace_id


async def test_without_a_trace_id_each_call_gets_its_own(settings, fake_vault):
    async with connect(settings, fake_vault) as client:
        await client.call_tool("get_policy_status", {})
        await client.call_tool("get_policy_status", {})
    first, second = (r["trace_id"] for r in fake_vault.requests)
    assert first != second
    assert uuid.UUID(first).version == 4


async def test_an_invalid_trace_id_in_meta_is_replaced(settings, fake_vault):
    async with connect(settings, fake_vault) as client:
        await client.call_tool("get_policy_status", {}, meta={TRACE_META_KEY: "<script>"})
    sent = fake_vault.requests[0]["trace_id"]
    assert uuid.UUID(sent).version == 4


async def test_the_llm_cannot_choose_the_trace_id_through_arguments(settings, fake_vault):
    chosen = str(uuid.uuid4())
    async with connect(settings, fake_vault) as client:
        await client.call_tool("get_balance", {"asset": "ETH", "trace_id": chosen})
    assert fake_vault.requests[0]["trace_id"] != chosen
    assert fake_vault.requests[0]["args"]["trace_id"] == chosen   # stays inside args, where the Vault rejects it


async def test_concurrent_calls_do_not_mix_up_answers(settings, fake_vault):
    ids = [str(uuid.uuid4()) for _ in range(25)]
    answers = {}

    async with connect(settings, fake_vault) as client:
        async def one(trace_id):
            result = await client.call_tool("get_policy_status", {}, meta={TRACE_META_KEY: trace_id})
            answers[trace_id] = result.structured_content["trace_id"]

        async with anyio.create_task_group() as group:
            for trace_id in ids:
                group.start_soon(one, trace_id)
    assert answers == {i: i for i in ids}

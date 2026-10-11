"""The Vault client: request shape, pass-through, and failing closed (CONTRACTS.md sections 1 and 2)."""

import json
import logging
import uuid

import httpx2
import pytest

from secureagent_mcp.config import ConfigError, ServerSettings, load_env
from secureagent_mcp.vault_client import clean_trace_id

from .conftest import ALICE, TOKEN, FakeVault, decision, make_settings

TRANSFER = {"asset": "ETH", "amount": "0.05", "recipient": ALICE}


# ---- the request ----

async def test_request_has_exactly_the_contract_fields(settings, fake_vault):
    client = fake_vault.client(settings)
    await client.call_tool("propose_transfer", TRANSFER)
    sent = fake_vault.requests[0]
    assert set(sent) == {"trace_id", "agent_id", "session_token", "tool", "args"}
    assert sent["agent_id"] == "crypto-agent"
    assert sent["session_token"] == TOKEN
    assert sent["tool"] == "propose_transfer"
    assert sent["args"] == TRANSFER
    assert str(uuid.UUID(sent["trace_id"], version=4)) == sent["trace_id"]


async def test_posts_to_tool_call_endpoint(settings, fake_vault):
    await fake_vault.client(settings).call_tool("get_policy_status", {})
    request = fake_vault.raw[0]
    assert request.method == "POST"
    assert str(request.url) == "http://vault.test:8000/v1/tool-call"
    assert request.headers["content-type"] == "application/json"


async def test_token_is_only_in_the_body(settings, fake_vault):
    await fake_vault.client(settings).call_tool("get_policy_status", {})
    request = fake_vault.raw[0]
    assert TOKEN not in str(request.url)
    assert all(TOKEN not in value for value in request.headers.values())


async def test_args_cannot_override_identity_fields(settings, fake_vault):
    # An LLM that puts agent_id / session_token in args only fills the nested object.
    hostile = {"asset": "ETH", "agent_id": "admin", "session_token": "stolen", "trace_id": "x"}
    await fake_vault.client(settings).call_tool("get_balance", hostile)
    sent = fake_vault.requests[0]
    assert sent["agent_id"] == "crypto-agent"
    assert sent["session_token"] == TOKEN
    assert sent["args"] == hostile  # forwarded untouched, so the Vault's gate 0 rejects it


async def test_missing_args_become_an_empty_object(settings, fake_vault):
    await fake_vault.client(settings).call_tool("get_policy_status", None)
    assert fake_vault.requests[0]["args"] == {}


async def test_arguments_are_forwarded_without_fixing_them(settings, fake_vault):
    odd = {"amount": 0.05, "asset": "eth", "skip_checks": True, "recipient": "alice"}
    await fake_vault.client(settings).call_tool("propose_transfer", odd)
    assert fake_vault.requests[0]["args"] == odd


# ---- trace ids ----

async def test_a_fresh_trace_id_per_call_when_none_given(settings, fake_vault):
    client = fake_vault.client(settings)
    await client.call_tool("get_policy_status", {})
    await client.call_tool("get_policy_status", {})
    first, second = (r["trace_id"] for r in fake_vault.requests)
    assert first != second


async def test_a_given_trace_id_is_used(settings, fake_vault):
    trace_id = str(uuid.uuid4())
    reply = await fake_vault.client(settings).call_tool("get_policy_status", {}, trace_id)
    assert fake_vault.requests[0]["trace_id"] == trace_id
    assert reply.body["trace_id"] == trace_id


@pytest.mark.parametrize("bad", ["not-a-uuid", "", None, 123, str(uuid.uuid1()), "../../etc/passwd"])
async def test_a_bad_trace_id_is_replaced_not_forwarded(settings, fake_vault, bad):
    await fake_vault.client(settings).call_tool("get_policy_status", {}, bad)
    sent = fake_vault.requests[0]["trace_id"]
    assert sent != bad
    assert clean_trace_id(sent) == sent


# ---- answers pass through unchanged ----

async def test_allow_is_returned_unchanged(settings):
    answer = {}

    def respond(payload):
        answer.update(decision(payload, result={"tx_hash": "0x" + "11" * 32, "status": "submitted"}))
        return 200, answer

    reply = await FakeVault(respond).client(settings).call_tool("propose_transfer", TRANSFER)
    assert reply.synthesized is False
    assert reply.body == answer


async def test_deny_is_returned_unchanged(settings):
    answer = {}

    def respond(payload):
        answer.update(decision(payload, "DENY", gate="limits", gate_number=6, reason_code="LIMIT_PER_TX",
                               reason="5.000042 ETH including gas is over the 0.1 ETH per-transaction limit"))
        return 200, answer

    reply = await FakeVault(respond).client(settings).call_tool("propose_transfer", TRANSFER)
    assert reply.synthesized is False
    assert reply.body == answer


async def test_the_client_never_decides_on_its_own(settings):
    # An outrageous request that the Vault happens to ALLOW comes back ALLOW. A harmless one
    # the Vault DENIES comes back DENY. The client has no opinion.
    allow = FakeVault(lambda p: (200, decision(p)))
    deny = FakeVault(lambda p: (200, decision(p, "DENY", reason_code="TOOL_NOT_PERMITTED")))
    huge = {"asset": "ETH", "amount": "999999", "recipient": "0x" + "0" * 40}
    assert (await allow.client(settings).call_tool("propose_transfer", huge)).body["decision"] == "ALLOW"
    assert (await deny.client(settings).call_tool("get_policy_status", {})).body["decision"] == "DENY"


# ---- failing closed ----

def _assert_local_deny(reply, code):
    assert reply.synthesized is True
    body = reply.body
    assert body["decision"] == "DENY"
    assert body["reason_code"] == code
    assert body["source"] == "mcp_server"
    assert body["result"] is None
    assert uuid.UUID(body["trace_id"])


async def test_vault_down_is_a_denial_that_says_nothing_was_sent(settings):
    def refuse(payload):
        raise httpx2.ConnectError("connection refused")

    reply = await FakeVault(refuse).client(settings).call_tool("propose_transfer", TRANSFER)
    _assert_local_deny(reply, "VAULT_UNREACHABLE")
    assert "outcome_unknown" not in reply.body


async def test_connect_timeout_is_treated_as_not_sent(settings):
    def slow(payload):
        raise httpx2.ConnectTimeout("timed out")

    reply = await FakeVault(slow).client(settings).call_tool("propose_transfer", TRANSFER)
    _assert_local_deny(reply, "VAULT_UNREACHABLE")


async def test_no_answer_after_sending_is_marked_outcome_unknown(settings):
    # The Vault may have signed and sent the transfer before the answer was lost.
    def hang(payload):
        raise httpx2.ReadTimeout("no answer")

    reply = await FakeVault(hang).client(settings).call_tool("propose_transfer", TRANSFER)
    _assert_local_deny(reply, "VAULT_NO_ANSWER")
    assert reply.body["outcome_unknown"] is True
    assert "retry" in reply.body["reason"].lower()


async def test_connection_dropped_mid_answer_is_outcome_unknown(settings):
    def drop(payload):
        raise httpx2.RemoteProtocolError("server disconnected")

    reply = await FakeVault(drop).client(settings).call_tool("propose_transfer", TRANSFER)
    _assert_local_deny(reply, "VAULT_NO_ANSWER")
    assert reply.body["outcome_unknown"] is True


@pytest.mark.parametrize("status", [500, 503, 404, 422, 401, 302])
async def test_any_status_but_200_is_a_denial(settings, status):
    reply = await FakeVault(lambda p: (status, {"detail": "boom"})).client(settings).call_tool("get_balance", {})
    _assert_local_deny(reply, "VAULT_ERROR")
    assert reply.body["http_status"] == status


async def test_a_5xx_may_have_acted_but_a_4xx_did_not(settings):
    r500 = await FakeVault(lambda p: (500, {})).client(settings).call_tool("get_balance", {})
    r422 = await FakeVault(lambda p: (422, {})).client(settings).call_tool("get_balance", {})
    assert r500.body["outcome_unknown"] is True
    assert r422.body["outcome_unknown"] is False


async def test_a_200_with_an_allow_is_not_trusted_if_it_is_for_another_request(settings):
    other = str(uuid.uuid4())
    reply = await FakeVault(lambda p: (200, decision({"trace_id": other}))).client(settings).call_tool("propose_transfer", TRANSFER)
    _assert_local_deny(reply, "VAULT_BAD_RESPONSE")
    assert reply.body["trace_id"] != other  # reports our own trace id, not the stranger's


@pytest.mark.parametrize("body", [
    "not json at all",
    "[]",
    "null",
    '{"decision": "MAYBE", "checks": []}',
    '{"checks": []}',
    '{"decision": "ALLOW"}',
])
async def test_a_200_that_is_not_a_decision_is_a_denial(settings, body):
    reply = await FakeVault(lambda p: (200, body)).client(settings).call_tool("get_balance", {})
    _assert_local_deny(reply, "VAULT_BAD_RESPONSE")


async def test_a_200_missing_checks_is_not_trusted(settings):
    def respond(payload):
        body = decision(payload)
        del body["checks"]
        return 200, body

    reply = await FakeVault(respond).client(settings).call_tool("get_balance", {})
    _assert_local_deny(reply, "VAULT_BAD_RESPONSE")


async def test_failures_never_raise(settings):
    for exc in (httpx2.ConnectError("x"), httpx2.ReadTimeout("x"), httpx2.ReadError("x"), httpx2.WriteTimeout("x")):
        def boom(payload, exc=exc):
            raise exc
        reply = await FakeVault(boom).client(settings).call_tool("get_balance", {})
        assert reply.body["decision"] == "DENY"


async def test_arguments_that_cannot_be_sent_are_a_denial_not_a_crash(settings, fake_vault):
    reply = await fake_vault.client(settings).call_tool("propose_transfer", {"amount": {1, 2}})  # not JSON
    _assert_local_deny(reply, "REQUEST_NOT_SENT")
    assert "outcome_unknown" not in reply.body  # nothing was sent, so nothing can have happened
    assert fake_vault.requests == []  # nothing reached the Vault


# ---- never retries ----

@pytest.mark.parametrize("failure", [httpx2.ReadTimeout("x"), httpx2.ConnectError("x")])
async def test_never_retries_after_a_failure(settings, failure):
    def boom(payload):
        raise failure

    vault = FakeVault(boom)
    await vault.client(settings).call_tool("propose_transfer", TRANSFER)
    assert len(vault.requests) == 1


async def test_never_retries_after_a_500(settings):
    vault = FakeVault(lambda p: (500, {}))
    await vault.client(settings).call_tool("propose_transfer", TRANSFER)
    assert len(vault.requests) == 1


async def test_does_not_follow_redirects(settings):
    # A redirect could re-send the session token to another host.
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if "evil" not in str(request.url):
            return httpx2.Response(307, headers={"location": "http://evil.test/steal"})
        return httpx2.Response(200, json={})

    from secureagent_mcp.vault_client import VaultClient

    reply = await VaultClient(settings, transport=httpx2.MockTransport(handler)).call_tool("get_balance", {})
    assert seen == ["http://vault.test:8000/v1/tool-call"]
    _assert_local_deny(reply, "VAULT_ERROR")


# ---- the token stays secret ----

async def test_token_never_appears_in_logs_for_any_outcome(settings, caplog):
    outcomes = [
        lambda p: (200, decision(p)),
        lambda p: (200, decision(p, "DENY", reason_code="AGENT_NOT_AUTHENTICATED")),
        lambda p: (500, {"detail": "x"}),
        lambda p: (200, "garbage"),
    ]
    for answer in outcomes:
        await FakeVault(answer).client(settings).call_tool("propose_transfer", TRANSFER)

    def raise_timeout(payload):
        raise httpx2.ReadTimeout("x")

    await FakeVault(raise_timeout).client(settings).call_tool("propose_transfer", TRANSFER)
    assert TOKEN not in caplog.text


async def test_token_never_appears_in_returned_denials(settings):
    for answer in (lambda p: (500, {}), lambda p: (200, "x")):
        reply = await FakeVault(answer).client(settings).call_tool("get_balance", {})
        assert TOKEN not in json.dumps(reply.body)


def test_token_is_hidden_from_repr_and_str(settings):
    assert TOKEN not in repr(settings)
    assert TOKEN not in str(settings)


# ---- settings ----

def test_missing_token_is_a_clear_error():
    with pytest.raises(ConfigError, match="AGENT_TOKEN"):
        ServerSettings.from_env({})
    with pytest.raises(ConfigError, match="AGENT_TOKEN"):
        ServerSettings.from_env({"AGENT_TOKEN": "   "})


def test_defaults():
    s = ServerSettings.from_env({"AGENT_TOKEN": "t"})
    assert (s.vault_url, s.agent_id, s.timeout_seconds, s.expose_attack_demo_tools) == (
        "http://127.0.0.1:8000", "crypto-agent", 30.0, False)


def test_vault_url_trailing_slash_is_dropped_and_scheme_required():
    assert ServerSettings.from_env({"AGENT_TOKEN": "t", "VAULT_URL": "http://10.0.0.5:8000/"}).vault_url == "http://10.0.0.5:8000"
    with pytest.raises(ConfigError, match="VAULT_URL"):
        ServerSettings.from_env({"AGENT_TOKEN": "t", "VAULT_URL": "10.0.0.5:8000"})


@pytest.mark.parametrize("value", ["abc", "0", "-5"])
def test_bad_timeout_is_rejected(value):
    with pytest.raises(ConfigError, match="VAULT_TIMEOUT_SECONDS"):
        ServerSettings.from_env({"AGENT_TOKEN": "t", "VAULT_TIMEOUT_SECONDS": value})


@pytest.mark.parametrize("value,expected", [("1", True), ("true", True), ("YES", True), ("0", False), ("", False), ("no", False)])
def test_attack_demo_flag(value, expected):
    s = ServerSettings.from_env({"AGENT_TOKEN": "t", "EXPOSE_ATTACK_DEMO_TOOLS": value})
    assert s.expose_attack_demo_tools is expected


def test_env_file_is_read_without_touching_os_environ(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("AGENT_TOKEN=from-file\nAGENT_ID=other-agent\n")
    environ = {"AGENT_ID": "from-real-env"}
    values = load_env(str(env_file), environ=environ)
    assert values["AGENT_TOKEN"] == "from-file"
    assert values["AGENT_ID"] == "from-real-env"   # real environment wins
    assert "AGENT_TOKEN" not in environ            # the file's values were not copied into the environment


def test_a_named_env_file_that_is_missing_is_an_error(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_env(str(tmp_path / "nope.env"), environ={})

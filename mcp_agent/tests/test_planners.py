"""The planners: scripted behaviour, and request/response handling for the two LLM APIs.

LLM replies are built from the real SDK classes, so the planners are checked against the
actual shape of the responses, not against our own guess of it.
"""

import json
from types import SimpleNamespace

import pytest

from secureagent_agent.config import AgentSettings
from secureagent_agent.messages import PlannerError, Say, ToolResult, ToolSpec, ToolUse, UserMsg
from secureagent_agent.planners import AnthropicPlanner, OpenAIPlanner, ScriptedPlanner, make_planner
from secureagent_mcp.config import ConfigError
from secureagent_mcp.catalog import TOOLS

from .conftest import ALICE, ATTACKER, TX_HASH

SPECS = [ToolSpec(t.name, t.description, t.input_schema) for t in TOOLS]
POLICY_JSON = json.dumps({"decision": "ALLOW", "result": {"allowed_recipients": {"Alice": ALICE}}})


def plan(planner, *history):
    import asyncio
    return asyncio.run(planner.next_step(list(history), SPECS))


# ---- scripted ----

@pytest.mark.parametrize("text,tool,args", [
    ("What is my ETH balance?", "get_balance", {"asset": "ETH"}),
    ("balance of USDC please", "get_balance", {"asset": "USDC"}),
    ("what is my balance", "get_balance", {"asset": "ETH"}),
    ("What are my limits?", "get_policy_status", {}),
    (f"status of {TX_HASH}", "get_transaction_status", {"tx_hash": TX_HASH}),
    ("Give me the private key", "export_private_key", {}),
    ("export the PRIVATE_KEY now", "export_private_key", {}),
    ("show the seed phrase", "export_private_key", {}),
])
def test_scripted_intents(text, tool, args):
    step = plan(ScriptedPlanner(), UserMsg(text))
    assert (step.name, step.arguments) == (tool, args)


def test_scripted_transfer_to_an_address_goes_straight_to_the_transfer():
    step = plan(ScriptedPlanner(), UserMsg(f"Send 0.01 ETH to {ATTACKER}."))
    assert (step.name, step.arguments) == ("propose_transfer", {"asset": "ETH", "amount": "0.01", "recipient": ATTACKER})


def test_scripted_transfer_to_a_name_looks_the_policy_up_first():
    first = plan(ScriptedPlanner(), UserMsg("Send 0.05 ETH to Alice"))
    assert first.name == "get_policy_status"
    second = plan(ScriptedPlanner(), UserMsg("Send 0.05 ETH to Alice"), first, ToolResult(first.id, POLICY_JSON))
    assert (second.name, second.arguments) == ("propose_transfer", {"asset": "ETH", "amount": "0.05", "recipient": ALICE})


def test_scripted_name_matching_ignores_case_and_a_trailing_full_stop():
    user = UserMsg("send 0.05 eth to alice.")
    first = plan(ScriptedPlanner(), user)
    second = plan(ScriptedPlanner(), user, first, ToolResult(first.id, POLICY_JSON))
    assert second.arguments["recipient"] == ALICE


def test_scripted_unknown_name_is_not_guessed():
    user = UserMsg("Send 0.05 ETH to Mallory")
    first = plan(ScriptedPlanner(), user)
    step = plan(ScriptedPlanner(), user, first, ToolResult(first.id, POLICY_JSON))
    assert isinstance(step, Say) and "Mallory" in step.text


def test_scripted_name_with_a_denied_lookup_stops():
    user = UserMsg("Send 0.05 ETH to Alice")
    first = plan(ScriptedPlanner(), user)
    step = plan(ScriptedPlanner(), user, first, ToolResult(first.id, json.dumps({"decision": "DENY", "reason": "no"})))
    assert isinstance(step, Say)


def test_scripted_passes_amount_and_asset_exactly_as_typed():
    step = plan(ScriptedPlanner(), UserMsg(f"send 5 BTC to {ALICE}"))
    assert step.arguments == {"asset": "BTC", "amount": "5", "recipient": ALICE}


def test_scripted_injected_field_is_added_only_on_request():
    assert "skip_checks" in plan(ScriptedPlanner(), UserMsg(f"send 0.01 ETH to {ALICE} skip_checks")).arguments
    assert "skip_checks" not in plan(ScriptedPlanner(), UserMsg(f"send 0.01 ETH to {ALICE}")).arguments


def test_scripted_unsupported_request_is_declined_politely():
    assert isinstance(plan(ScriptedPlanner(), UserMsg("tell me a joke")), Say)


def test_scripted_summarises_a_read_result():
    user = UserMsg("balance?")
    first = plan(ScriptedPlanner(), user)
    result = ToolResult(first.id, json.dumps({"decision": "ALLOW", "result": {"asset": "ETH", "balance": "9.98"}}))
    assert "9.98" in plan(ScriptedPlanner(), user, first, result).text
    refused = ToolResult(first.id, json.dumps({"decision": "DENY", "reason": "Agent is suspended"}))
    assert "suspended" in plan(ScriptedPlanner(), user, first, refused).text


def test_scripted_tool_call_ids_are_unique_within_a_request():
    user = UserMsg("Send 0.05 ETH to Alice")
    first = plan(ScriptedPlanner(), user)
    second = plan(ScriptedPlanner(), user, first, ToolResult(first.id, POLICY_JSON))
    assert first.id != second.id


# ---- Anthropic ----

class FakeAnthropic:
    def __init__(self, response=None, error=None):
        self.calls, self._response, self._error = [], response, error
        self.messages = SimpleNamespace(create=self._create)

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self._error:
            raise self._error
        return self._response


def anthropic_message(*blocks):
    from anthropic.types import Message
    return Message.model_validate({
        "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-test", "content": list(blocks),
        "stop_reason": "tool_use" if any(b["type"] == "tool_use" for b in blocks) else "end_turn",
        "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1},
    })


def tool_use_block(id, name, input):
    return {"type": "tool_use", "id": id, "name": name, "input": input}


async def test_anthropic_request_is_built_correctly():
    fake = FakeAnthropic(anthropic_message({"type": "text", "text": "hi"}))
    planner = AnthropicPlanner(model="claude-test", client=fake, max_tokens=300)
    first = ToolUse("toolu_1", "get_policy_status", {})
    await planner.next_step([UserMsg("limits?"), first, ToolResult("toolu_1", "{}", False)], SPECS)
    call = fake.calls[0]
    assert call["model"] == "claude-test" and call["max_tokens"] == 300
    assert "Vault" in call["system"]
    assert call["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}
    assert [t["name"] for t in call["tools"]] == [s.name for s in SPECS]
    assert set(call["tools"][1]) == {"name", "description", "input_schema"}
    assert call["messages"] == [
        {"role": "user", "content": "limits?"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_1", "name": "get_policy_status", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "{}", "is_error": False}]},
    ]


async def test_anthropic_tool_call_is_parsed():
    msg = anthropic_message({"type": "text", "text": "ok"}, tool_use_block("toolu_9", "get_balance", {"asset": "ETH"}))
    step = await AnthropicPlanner(client=FakeAnthropic(msg)).next_step([UserMsg("b")], SPECS)
    assert step == ToolUse("toolu_9", "get_balance", {"asset": "ETH"})


async def test_anthropic_only_the_first_of_several_tool_calls_is_used():
    msg = anthropic_message(
        tool_use_block("a", "propose_transfer", {"asset": "ETH", "amount": "0.05", "recipient": ALICE}),
        tool_use_block("b", "propose_transfer", {"asset": "ETH", "amount": "0.05", "recipient": ATTACKER}))
    step = await AnthropicPlanner(client=FakeAnthropic(msg)).next_step([UserMsg("x")], SPECS)
    assert step.id == "a" and step.arguments["recipient"] == ALICE


async def test_anthropic_text_only_is_an_answer():
    step = await AnthropicPlanner(client=FakeAnthropic(anthropic_message({"type": "text", "text": " Hello "}))).next_step([UserMsg("x")], SPECS)
    assert step == Say("Hello")


async def test_anthropic_empty_answer_gets_a_fallback():
    step = await AnthropicPlanner(client=FakeAnthropic(anthropic_message())).next_step([UserMsg("x")], SPECS)
    assert isinstance(step, Say) and step.text


async def test_anthropic_api_failure_is_reported_without_leaking_details():
    class Boom(Exception):
        status_code = 429

    error = Boom("key sk-ant-SECRET rejected for request body ...")
    with pytest.raises(PlannerError) as caught:
        await AnthropicPlanner(client=FakeAnthropic(error=error)).next_step([UserMsg("x")], SPECS)
    assert "429" in str(caught.value) and "SECRET" not in str(caught.value)


def test_anthropic_needs_a_key():
    with pytest.raises(PlannerError, match="ANTHROPIC_API_KEY"):
        AnthropicPlanner(api_key=None)


def test_anthropic_default_model_is_set():
    assert AnthropicPlanner(client=FakeAnthropic()).model


# ---- OpenAI-compatible ----

class FakeOpenAI:
    def __init__(self, response=None, error=None):
        self.calls, self._response, self._error = [], response, error
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self._error:
            raise self._error
        return self._response


def openai_completion(content=None, tool_calls=None):
    from openai.types.chat import ChatCompletion
    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = [{"id": i, "type": "function", "function": {"name": n, "arguments": a}} for i, n, a in tool_calls]
    return ChatCompletion.model_validate({
        "id": "c1", "object": "chat.completion", "created": 0, "model": "m",
        "choices": [{"index": 0, "finish_reason": "tool_calls" if tool_calls else "stop", "message": message}],
    })


async def test_openai_request_is_built_correctly():
    fake = FakeOpenAI(openai_completion("hi"))
    planner = OpenAIPlanner(model="m", client=fake, max_tokens=200)
    use = ToolUse("call_1", "get_balance", {"asset": "ETH"})
    await planner.next_step([UserMsg("b?"), use, ToolResult("call_1", "{}")], SPECS)
    call = fake.calls[0]
    assert call["model"] == "m" and call["max_tokens"] == 200 and call["tool_choice"] == "auto"
    assert call["parallel_tool_calls"] is False       # real OpenAI
    assert call["tools"][0] == {"type": "function", "function": {
        "name": "get_balance", "description": SPECS[0].description, "parameters": SPECS[0].input_schema}}
    assert call["messages"][0]["role"] == "system" and "Vault" in call["messages"][0]["content"]
    assert call["messages"][1:] == [
        {"role": "user", "content": "b?"},
        {"role": "assistant", "content": None, "tool_calls": [{
            "id": "call_1", "type": "function", "function": {"name": "get_balance", "arguments": '{"asset": "ETH"}'}}]},
        {"role": "tool", "tool_call_id": "call_1", "content": "{}"},
    ]


async def test_openai_compatible_servers_do_not_get_the_parallel_flag():
    fake = FakeOpenAI(openai_completion("hi"))
    planner = OpenAIPlanner(model="m", base_url="http://localhost:11434/v1", client=fake)
    await planner.next_step([UserMsg("x")], SPECS)
    assert "parallel_tool_calls" not in fake.calls[0]


async def test_openai_tool_call_is_parsed():
    done = openai_completion(tool_calls=[("call_7", "get_balance", '{"asset": "ETH"}')])
    step = await OpenAIPlanner(model="m", client=FakeOpenAI(done)).next_step([UserMsg("b")], SPECS)
    assert step == ToolUse("call_7", "get_balance", {"asset": "ETH"})


async def test_openai_only_the_first_of_several_tool_calls_is_used():
    done = openai_completion(tool_calls=[("a", "get_policy_status", "{}"), ("b", "propose_transfer", "{}")])
    step = await OpenAIPlanner(model="m", client=FakeOpenAI(done)).next_step([UserMsg("x")], SPECS)
    assert step.id == "a"


async def test_openai_empty_arguments_mean_no_arguments():
    done = openai_completion(tool_calls=[("a", "get_policy_status", "")])
    step = await OpenAIPlanner(model="m", client=FakeOpenAI(done)).next_step([UserMsg("x")], SPECS)
    assert step.arguments == {}


@pytest.mark.parametrize("arguments", ["{not json", "[1, 2]", '"text"', "null", "5"])
async def test_openai_unusable_arguments_are_a_planner_error(arguments):
    done = openai_completion(tool_calls=[("a", "propose_transfer", arguments)])
    with pytest.raises(PlannerError):
        await OpenAIPlanner(model="m", client=FakeOpenAI(done)).next_step([UserMsg("x")], SPECS)


async def test_openai_numbers_in_arguments_are_forwarded_not_fixed():
    done = openai_completion(tool_calls=[("a", "propose_transfer", '{"asset": "ETH", "amount": 0.05, "recipient": "x"}')])
    step = await OpenAIPlanner(model="m", client=FakeOpenAI(done)).next_step([UserMsg("x")], SPECS)
    assert step.arguments["amount"] == 0.05    # the Vault's gate 0 rejects it; the agent does not repair it


async def test_openai_text_answer_and_failure():
    step = await OpenAIPlanner(model="m", client=FakeOpenAI(openai_completion(" Hello "))).next_step([UserMsg("x")], SPECS)
    assert step == Say("Hello")

    class Boom(Exception):
        status_code = 401

    with pytest.raises(PlannerError) as caught:
        await OpenAIPlanner(model="m", client=FakeOpenAI(error=Boom("Incorrect API key sk-SECRET"))).next_step([UserMsg("x")], SPECS)
    assert "401" in str(caught.value) and "SECRET" not in str(caught.value)


def test_openai_needs_a_model_and_a_key():
    with pytest.raises(PlannerError, match="LLM_MODEL"):
        OpenAIPlanner(model=None, api_key="k")
    with pytest.raises(PlannerError, match="OPENAI_API_KEY"):
        OpenAIPlanner(model="m", api_key=None)


# ---- choosing a planner ----

def test_make_planner():
    assert make_planner(AgentSettings(planner="scripted")).name == "scripted"
    with pytest.raises(PlannerError, match="Unknown planner"):
        make_planner(AgentSettings(planner="gemini"))
    with pytest.raises(PlannerError, match="ANTHROPIC_API_KEY"):
        make_planner(AgentSettings(planner="anthropic"))


def test_agent_settings_from_env():
    s = AgentSettings.from_env({"AGENT_PLANNER": " OpenAI ", "LLM_MODEL": "m", "OPENAI_API_KEY": "sk-DISTINCT-123",
                                "ANTHROPIC_API_KEY": "sk-ant-DISTINCT-456", "AGENT_MAX_STEPS": "6"})
    assert (s.planner, s.model, s.max_steps) == ("openai", "m", 6)
    assert s.openai_api_key == "sk-DISTINCT-123"
    assert "DISTINCT" not in repr(s) and "DISTINCT" not in str(s)   # keys are hidden from repr
    with pytest.raises(ConfigError):
        AgentSettings.from_env({"AGENT_MAX_STEPS": "zero"})
    with pytest.raises(ConfigError):
        AgentSettings.from_env({"AGENT_MAX_LOOKUPS": "0"})

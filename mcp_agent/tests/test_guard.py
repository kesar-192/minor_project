import json

import pytest

from secureagent_agent.guard import MAX_CHARS, WITHHELD, guard_for_llm, render_for_llm


def allow(result):
    return {"trace_id": "t", "decision": "ALLOW", "checks": [], "result": result}


def test_a_plain_balance_passes_with_only_asset_and_balance():
    body = guard_for_llm("get_balance", allow({"asset": "ETH", "balance": "9.98", "stub": True, "history": [1, 2, 3]}))
    assert body["result"] == {"asset": "ETH", "balance": "9.98"}


@pytest.mark.parametrize("balance", [
    "7" * 617,                 # the size of a Paillier ciphertext: hundreds of digits
    "1e18",
    "-5",
    "9.98 ETH",
    "",
    10,                        # a number, not a string
    {"enc": "123"},
    None,
])
def test_anything_that_is_not_a_plain_decimal_total_is_withheld(balance):
    body = guard_for_llm("get_balance", allow({"asset": "ETH", "balance": balance}))
    assert body["result"] == WITHHELD


@pytest.mark.parametrize("result", [None, [], "9.98", {"balance": "9.98"}, {"asset": "ETH"}, {"asset": "E T H", "balance": "1"}])
def test_a_malformed_balance_result_is_withheld(result):
    assert guard_for_llm("get_balance", allow(result))["result"] == WITHHELD


def test_ciphertext_never_reaches_the_llm_text():
    cipher = "123456789" * 70
    text = render_for_llm("get_balance", allow({"asset": "ETH", "balance": cipher, "ciphertext": cipher}))
    assert cipher not in text and "123456789123456789" not in text
    assert "withheld" in text


def test_a_deny_and_other_tools_pass_through_unchanged():
    deny = {"trace_id": "t", "decision": "DENY", "reason": "no", "checks": [], "result": None}
    assert guard_for_llm("get_balance", deny) == deny
    policy = allow({"agent_status": "active", "allowed_recipients": {"Alice": "0x1"}})
    assert guard_for_llm("get_policy_status", policy) == policy


def test_the_original_is_not_modified():
    original = allow({"asset": "ETH", "balance": "1", "extra": "x"})
    guard_for_llm("get_balance", original)
    assert original["result"]["extra"] == "x"


def test_very_long_results_are_cut():
    text = render_for_llm("get_policy_status", allow({"blob": "x" * 20000}))
    assert len(text) < MAX_CHARS + 100 and "cut" in text


def test_output_is_valid_json_when_short():
    assert json.loads(render_for_llm("get_balance", allow({"asset": "ETH", "balance": "1"})))["result"]["balance"] == "1"

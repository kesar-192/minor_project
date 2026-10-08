import pytest
import yaml

from app.config import DEFAULT_POLICY_PATH
from app.policy import PolicyError, PolicyStore, load_policy

from conftest import ALICE, policy_dict, write_policy, write_policy_text


def test_shipped_policy_file_only_needs_a_token(tmp_path):
    # policy/policy.yaml is complete except the token hash, which each machine fills in.
    with pytest.raises(PolicyError, match="scripts/new_agent_token.py"):
        load_policy(DEFAULT_POLICY_PATH)
    shipped = yaml.safe_load(DEFAULT_POLICY_PATH.read_text())
    shipped["agents"]["crypto-agent"]["token_sha256"] = "a" * 64
    path = tmp_path / "policy.yaml"
    write_policy(path, shipped)
    agent = load_policy(path).agents["crypto-agent"]
    assert agent.allowed_assets == ["ETH"]
    assert agent.recipient_name(ALICE) == "Alice"


@pytest.mark.parametrize("overrides, problem", [
    ({"allowed_tools": ["export_private_key"]}, "export_private_key can never be allowed"),
    ({"allowed_tools": ["deploy_contract"]}, "deploy_contract is not in the tool catalog"),
    ({"allowed_assets": ["ETH", "USDC"]}, "no limits set for USDC"),
    ({"limits": {"ETH": {"max_per_tx": "1", "daily": "0.5"}}}, "max_per_tx cannot be larger than daily"),
    ({"status": "paused"}, "status"),
    ({"token_sha256": ""}, "make one with scripts/new_agent_token.py"),
    ({"allowed_recipients": {"Mallory": ALICE[:-2] + "c8"}}, "has a bad checksum"),
    ({"spending_mode": "unlimited"}, "field 'agents.crypto-agent.spending_mode' is not allowed"),
])
def test_bad_policy_is_rejected(tmp_path, overrides, problem):
    path = tmp_path / "policy.yaml"
    write_policy(path, policy_dict(**overrides))
    with pytest.raises(PolicyError, match=problem):
        load_policy(path)


def test_key_export_cannot_be_switched_on(tmp_path):
    data = policy_dict()
    data["restrictions"]["private_key_export"] = True
    path = tmp_path / "policy.yaml"
    write_policy(path, data)
    with pytest.raises(PolicyError, match="private_key_export"):
        load_policy(path)


@pytest.mark.parametrize("quoted, unquoted, problem", [
    ("'0.1'", "0.1", 'must be a decimal string like "0.05"'),
    (f"'{ALICE}'", ALICE, "must be 0x followed by 40 hex characters"),
])
def test_unquoted_amounts_and_addresses_are_rejected(tmp_path, quoted, unquoted, problem):
    # Unquoted, YAML turns 0.1 into a float and an address into a huge integer.
    text = yaml.safe_dump(policy_dict(), sort_keys=False)
    assert quoted in text
    path = tmp_path / "policy.yaml"
    write_policy_text(path, text.replace(quoted, unquoted))
    with pytest.raises(PolicyError, match=problem):
        load_policy(path)


def test_kill_switch_takes_effect_without_a_restart(policy_file):
    store = PolicyStore(policy_file)
    assert store.current().agents["crypto-agent"].status == "active"
    write_policy(policy_file, policy_dict(status="suspended"))
    assert store.current().agents["crypto-agent"].status == "suspended"


def test_broken_policy_fails_closed_until_fixed(policy_file):
    store = PolicyStore(policy_file)
    write_policy_text(policy_file, "agents: [this is not valid")
    for _ in range(2):  # no fallback to the old policy, however often it is asked
        with pytest.raises(PolicyError):
            store.current()
    write_policy(policy_file, policy_dict())
    assert store.current().agents["crypto-agent"].status == "active"


def test_missing_policy_stops_start_up(tmp_path):
    with pytest.raises(PolicyError, match="cannot read"):
        PolicyStore(tmp_path / "missing.yaml")

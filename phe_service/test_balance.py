"""Tests for the encrypted balance store and its endpoints."""

import os

os.environ.setdefault("PHE_API_TOKEN", "test-token")

from fastapi.testclient import TestClient  # noqa: E402

import balance_store  # noqa: E402
from main import app  # noqa: E402
from phe_logic import encrypt  # noqa: E402

HEADERS = {"x-api-token": "test-token"}
WEI_PER_ETH = 10 ** 18


def setup_function():
    balance_store.reset()


def store_eth(client, balance="9.98", requester="user123"):
    wei = int(round(float(balance) * WEI_PER_ETH))
    encrypted = encrypt(wei)
    return client.post(
        "/balance/set",
        headers=HEADERS,
        json={
            "requester_id": requester,
            "asset": "ETH",
            "ciphertext": str(encrypted.ciphertext()),
            "exponent": encrypted.exponent,
            "decimals": 18,
        },
    )


def test_round_trip_returns_a_plain_decimal_string():
    client = TestClient(app)
    assert store_eth(client).status_code == 200
    result = client.post(
        "/balance/query", headers=HEADERS, json={"requester_id": "user123", "asset": "ETH"}
    )
    assert result.status_code == 200
    assert result.json() == {"asset": "ETH", "balance": "9.98"}


def test_query_requires_the_api_token():
    client = TestClient(app)
    store_eth(client)
    result = client.post("/balance/query", json={"requester_id": "user123", "asset": "ETH"})
    assert result.status_code == 401


def test_unknown_requester_is_refused():
    client = TestClient(app)
    store_eth(client)
    result = client.post(
        "/balance/query", headers=HEADERS, json={"requester_id": "mallory", "asset": "ETH"}
    )
    assert result.status_code == 403


def test_missing_balance_is_a_404():
    client = TestClient(app)
    result = client.post(
        "/balance/query", headers=HEADERS, json={"requester_id": "user123", "asset": "ETH"}
    )
    assert result.status_code == 404


def test_set_overwrites_the_previous_balance():
    client = TestClient(app)
    store_eth(client, "9.98")
    store_eth(client, "1.5")
    result = client.post(
        "/balance/query", headers=HEADERS, json={"requester_id": "user123", "asset": "ETH"}
    )
    assert result.json()["balance"] == "1.5"


def test_format_units_trims_trailing_zeros():
    assert balance_store.format_units(9_980_000_000_000_000_000, 18) == "9.98"
    assert balance_store.format_units(10 ** 18, 18) == "1"
    assert balance_store.format_units(0, 18) == "0"
    assert balance_store.format_units(42, 0) == "42"

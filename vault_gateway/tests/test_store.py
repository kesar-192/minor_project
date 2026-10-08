"""The SQLite audit log and spend record."""

import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.store import Store

from conftest import ETH, make_body


def fields(n):
    return {"type": "decision", "decision": "ALLOW", "reason": f"event {n}"}


def test_log_survives_a_restart_and_the_chain_continues(tmp_path):
    path = tmp_path / "vault.db"
    first = Store(str(path))
    for n in range(3):
        first.append_event(fields(n))
    second = Store(str(path))  # as if the service restarted
    assert second.append_event(fields(3))["seq"] == 4
    assert [e["seq"] for e in second.recent_events(10)] == [1, 2, 3, 4]
    assert second.verify() is None


def test_events_survive_an_app_restart(tmp_path):
    settings = Settings(stub_mode=True, cors_origins=[], policy_path=None, db_path=str(tmp_path / "vault.db"))
    with TestClient(create_app(settings)) as client:
        client.post("/v1/tool-call", json=make_body("export_private_key", {}))
    with TestClient(create_app(settings)) as client:
        events = client.get("/v1/events").json()
    assert [(e["seq"], e["reason_code"]) for e in events] == [(1, "KEY_EXPORT_DENIED")]


@pytest.mark.parametrize("statement", [
    "UPDATE audit_log SET event_json = '{}' WHERE seq = 1",
    "DELETE FROM audit_log WHERE seq = 1",
    "UPDATE spends SET total_wei = '0'",
    "DELETE FROM spends",
])
def test_rows_cannot_be_changed_or_deleted(tmp_path, statement):
    path = str(tmp_path / "vault.db")
    store = Store(path)
    store.append_event(fields(0))
    store.record_spend("crypto-agent", "ETH", ETH, "trace")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        sqlite3.connect(path).execute(statement)


def test_editing_the_file_directly_is_detected(tmp_path):
    path = str(tmp_path / "vault.db")
    store = Store(path)
    for n in range(4):
        store.append_event(fields(n))
    # An attacker with access to the file removes the guard and rewrites event 2.
    db = sqlite3.connect(path, isolation_level=None)
    db.execute("DROP TRIGGER audit_log_no_update")
    db.execute("""UPDATE audit_log SET event_json = REPLACE(event_json, 'event 1', 'nothing happened') WHERE seq = 2""")
    assert Store(path).verify() == 2


def test_spend_window_is_per_agent_and_asset(tmp_path):
    store = Store(str(tmp_path / "vault.db"))
    now = time.time()
    store.record_spend("crypto-agent", "ETH", 3 * ETH, "a", at=now - 3600)
    store.record_spend("crypto-agent", "ETH", 7 * ETH, "b", at=now - 25 * 3600)  # too old
    store.record_spend("other-agent", "ETH", 1 * ETH, "c", at=now - 60)
    store.record_spend("crypto-agent", "USDC", 1 * ETH, "d", at=now - 60)
    assert store.spent_since("crypto-agent", "ETH", now - 24 * 3600) == 3 * ETH


def test_amounts_bigger_than_64_bits_are_kept_exactly(tmp_path):
    store = Store(str(tmp_path / "vault.db"))
    big = 100 * ETH + 1  # over 2**63, which SQLite integers can't hold
    store.record_spend("crypto-agent", "ETH", big, "trace")
    assert store.spent_since("crypto-agent", "ETH", 0) == big

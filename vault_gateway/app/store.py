"""The Vault's own SQLite database: the audit log and the spend record.

Both tables are append-only. Triggers refuse UPDATE and DELETE, and every audit
row carries a hash over the row before it, so editing the file directly is
detected by verify(). The dashboard shows each event's hash as it happens,
which gives an outside copy to compare against.
"""

import json
import sqlite3
import threading
import time
from pathlib import Path
from uuid import uuid4

from .events import GENESIS_HASH, chain_hash

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
    seq        INTEGER PRIMARY KEY,
    event_json TEXT NOT NULL,   -- the event as sent to the dashboard, without its hash
    prev_hash  TEXT NOT NULL,
    hash       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS spends (
    id         INTEGER PRIMARY KEY,
    agent_id   TEXT NOT NULL,
    asset      TEXT NOT NULL,
    total_wei  TEXT NOT NULL,   -- text: 10 ETH in wei is already too big for SQLite's 64-bit integers
    created_at REAL NOT NULL,   -- unix time
    trace_id   TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS audit_log_no_update BEFORE UPDATE ON audit_log
    BEGIN SELECT RAISE(ABORT, 'the audit log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_log_no_delete BEFORE DELETE ON audit_log
    BEGIN SELECT RAISE(ABORT, 'the audit log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS spends_no_update BEFORE UPDATE ON spends
    BEGIN SELECT RAISE(ABORT, 'the spend record is append-only'); END;
CREATE TRIGGER IF NOT EXISTS spends_no_delete BEFORE DELETE ON spends
    BEGIN SELECT RAISE(ABORT, 'the spend record is append-only'); END;
"""


class Store:
    def __init__(self, path):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        # One connection shared by all threads, one statement at a time.
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._lock = threading.Lock()
        self._db.executescript(SCHEMA)

    # ---- audit log ----

    def append_event(self, fields):
        """Number the event, chain it to the previous one, store it, and return it with its hash."""
        with self._lock:
            last = self._db.execute("SELECT seq, hash FROM audit_log ORDER BY seq DESC LIMIT 1").fetchone()
            seq, prev_hash = (last[0] + 1, last[1]) if last else (1, GENESIS_HASH)
            event = {"event_id": str(uuid4()), "seq": seq, **fields}
            digest = chain_hash(prev_hash, event)
            self._db.execute(
                "INSERT INTO audit_log (seq, event_json, prev_hash, hash) VALUES (?, ?, ?, ?)",
                (seq, json.dumps(event), prev_hash, digest),
            )
        return {**event, "audit_hash": digest}

    def recent_events(self, limit):
        with self._lock:
            rows = self._db.execute(
                "SELECT event_json, hash FROM audit_log ORDER BY seq DESC LIMIT ?", (limit,)
            ).fetchall()
        return [{**json.loads(event_json), "audit_hash": digest} for event_json, digest in reversed(rows)]

    def count_events(self):
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]

    def verify(self):
        """Return None if the whole log checks out, else the seq of the first event that doesn't."""
        with self._lock:
            rows = self._db.execute("SELECT seq, event_json, prev_hash, hash FROM audit_log ORDER BY seq").fetchall()
        prev_hash = GENESIS_HASH
        for expected_seq, (seq, event_json, stored_prev, digest) in enumerate(rows, start=1):
            event = json.loads(event_json)
            if (seq != expected_seq or event.get("seq") != seq or stored_prev != prev_hash
                    or chain_hash(prev_hash, event) != digest):
                return seq
            prev_hash = digest
        return None

    # ---- spend record (for the rolling 24-hour limit) ----

    def record_spend(self, agent_id, asset, total_wei, trace_id, at=None):
        with self._lock:
            self._db.execute(
                "INSERT INTO spends (agent_id, asset, total_wei, created_at, trace_id) VALUES (?, ?, ?, ?, ?)",
                (agent_id, asset, str(total_wei), time.time() if at is None else at, trace_id),
            )

    def spent_since(self, agent_id, asset, since):
        """Total wei this agent has spent in this asset since the given unix time."""
        with self._lock:
            rows = self._db.execute(
                "SELECT total_wei FROM spends WHERE agent_id = ? AND asset = ? AND created_at > ?",
                (agent_id, asset, since),
            ).fetchall()
        return sum(int(total_wei) for (total_wei,) in rows)

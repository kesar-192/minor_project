"""Live decision feed for the dashboard.

Every event is first written to the audit log (app/store.py), which numbers it
and hash-chains it to the one before, and is then pushed to every connected
dashboard. Each event's audit_hash covers the event and the previous hash, so
editing or deleting any past event breaks every hash after it.
"""

import asyncio
import hashlib
import json
from datetime import datetime, timezone

GENESIS_HASH = "0" * 64


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def chain_hash(prev_hash, event):
    """sha256 of the previous hash followed by the event as canonical JSON (sorted keys, no spaces)."""
    canonical = json.dumps(event, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256((prev_hash + canonical).encode()).hexdigest()


def verify_chain(events):
    """True if no event in this run of the chain (oldest first, from seq 1) was changed, removed or reordered."""
    prev_hash = GENESIS_HASH
    for event in events:
        body = {k: v for k, v in event.items() if k != "audit_hash"}
        if chain_hash(prev_hash, body) != event["audit_hash"]:
            return False
        prev_hash = event["audit_hash"]
    return True


class EventHub:
    def __init__(self, store):
        self.store = store
        self._clients = set()
        self._lock = asyncio.Lock()

    async def publish(self, fields):
        # One at a time, so clients receive events in the same order as the audit log.
        async with self._lock:
            event = self.store.append_event(fields)
            for ws in list(self._clients):
                try:
                    await ws.send_json(event)
                except Exception:
                    self._clients.discard(ws)
        return event

    def recent(self, limit):
        return self.store.recent_events(limit)

    async def connect(self, ws):
        await ws.accept()
        self._clients.add(ws)

    def disconnect(self, ws):
        self._clients.discard(ws)

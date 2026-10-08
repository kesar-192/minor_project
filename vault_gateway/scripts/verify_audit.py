"""Check that the Vault's audit log has not been tampered with.

    .venv/bin/python scripts/verify_audit.py              (checks data/vault.db)
    .venv/bin/python scripts/verify_audit.py other.db
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # lets this script import the app package

from app.config import DEFAULT_DB_PATH  # noqa: E402
from app.store import Store  # noqa: E402

path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DB_PATH
if not Path(path).exists():
    print(f"no audit log at {path}")
    sys.exit(1)

store = Store(path)
bad_seq = store.verify()
if bad_seq is not None:
    print(f"TAMPERED  event #{bad_seq} does not match the hash chain")
    sys.exit(1)
latest = store.recent_events(1)
print(f"OK  {store.count_events()} events, hash chain intact")
if latest:
    print(f"    latest hash {latest[0]['audit_hash']} (compare with the dashboard)")

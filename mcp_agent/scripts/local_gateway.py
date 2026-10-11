"""Run the real Vault gateway on this machine with a fake chain and fake signing. No HashiCorp Vault or Anvil needed.

    python scripts/local_gateway.py [--port 8000] [--stub]

Prints the agent token. Put it in .env as AGENT_TOKEN (VAULT_URL is already http://127.0.0.1:8000).
Edit the printed policy file to try the kill switch: set status to suspended and save.
For development and demos of the MCP server and agent only.
"""

import argparse
import secrets
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from devtools.fake_stack import gateway_available, start_gateway  # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, default=8000)
parser.add_argument("--host", default="127.0.0.1")
parser.add_argument("--stub", action="store_true", help="stub mode: gates 1-6 always pass")
parser.add_argument("--token", help="agent token to accept (default: a new random one)")
args = parser.parse_args()

if not gateway_available():
    sys.exit("vault_gateway not found next to this folder. Set VAULT_GATEWAY_DIR to its path.")
token = args.token or secrets.token_urlsafe(32)
directory = tempfile.mkdtemp(prefix="secureagent-local-")
running = start_gateway(directory, token, stub=args.stub, port=args.port, host=args.host)
print(f"gateway   {running.url}  ({'stub' if args.stub else 'real'} mode, fake chain and signing)")
print(f"token     AGENT_TOKEN={token}")
print(f"policy    {running.policy_path}   (set status: suspended to try the kill switch)")
print("Ctrl+C to stop.")
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    running.stop()

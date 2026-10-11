#!/usr/bin/env bash
# SecureAgent: the one script.
#   ./run.sh            set up (first time only) and start everything
#   ./run.sh start      start (fresh chain, new Vault key, empty audit log); add --keep to keep the audit log
#   ./run.sh setup      (re)install everything: Python venv, Vault, Foundry/anvil, ethsign plugin, npm packages, .env
#   ./run.sh stop       stop everything
set -euo pipefail
ROOT=$(cd "$(dirname "$0")" && pwd); cd "$ROOT"
export PATH="$HOME/.local/bin:$HOME/.foundry/bin:$PATH"
say() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
need() { command -v "$1" >/dev/null || { echo "missing '$1': $2"; exit 1; }; }

stop_all() {   # each service runs in its own process group (see launch)
  for f in .run/*.pid; do
    [ -f "$f" ] || continue
    kill -- "-$(cat "$f")" 2>/dev/null || kill "$(cat "$f")" 2>/dev/null || true
    rm -f "$f"
  done
}

# The policy keeps only the SHA-256 of the agent token (from .env), never the token itself.
sync_policy_hash() {
.venv/bin/python - <<'PY'
import hashlib, re
token = next(l.split("=", 1)[1].strip() for l in open(".env") if l.startswith("AGENT_TOKEN="))
p = "vault_gateway/policy/policy.yaml"
s = open(p).read()
open(p, "w").write(re.sub(r'token_sha256: "[0-9a-f]*"', f'token_sha256: "{hashlib.sha256(token.encode()).hexdigest()}"', s, count=1))
print("agent token hash synced into", p)
PY
}

do_setup() {
need curl "sudo apt install curl"; need unzip "sudo apt install unzip"; need git "sudo apt install git"
need node "install Node 18+ (https://nodejs.org)"; need npm "install Node 18+"
mkdir -p .run
say "Python 3.12 environment (.venv)"
command -v uv >/dev/null || { curl -LsSf https://astral.sh/uv/install.sh | sh; export PATH="$HOME/.local/bin:$PATH"; }
[ -x .venv/bin/python ] && .venv/bin/python -c 'import sys; assert sys.version_info>=(3,11)' 2>/dev/null || { rm -rf .venv; uv venv --python 3.12 .venv; }
uv pip install -q -p .venv/bin/python -r requirements.txt

say "HashiCorp Vault"
if ! command -v vault >/dev/null; then
  mkdir -p ~/.local/bin; T=$(mktemp -d)
  curl -sSfL -o "$T/v.zip" https://releases.hashicorp.com/vault/2.1.1/vault_2.1.1_linux_amd64.zip
  unzip -oq "$T/v.zip" vault -d ~/.local/bin; rm -rf "$T"
fi
vault version

say "Foundry (anvil)"
if ! command -v anvil >/dev/null; then curl -sSfL https://foundry.paradigm.xyz | bash; foundryup; fi
anvil --version | head -1

say "Vault ethsign plugin (signs Ethereum transactions inside Vault)"
PLUGIN=vault_gateway/vault_setup/plugins/ethsign
if [ ! -x "$PLUGIN" ]; then
  # The upstream plugin pins a 2019 Vault SDK that cannot start under Vault 2.x, so we build it against a current SDK (needs Go >= 1.22).
  GO=go
  if ! go version 2>/dev/null | grep -qE 'go1\.(2[2-9]|[3-9][0-9])'; then
    [ -x ~/go-new/go/bin/go ] || { mkdir -p ~/go-new && curl -sSfL https://go.dev/dl/go1.23.4.linux-amd64.tar.gz | tar -xz -C ~/go-new; }
    GO=~/go-new/go/bin/go
  fi
  [ -d ~/vault-plugin-secrets-ethsign ] || git clone -q https://github.com/kaleido-io/vault-plugin-secrets-ethsign.git ~/vault-plugin-secrets-ethsign
  mkdir -p vault_gateway/vault_setup/plugins
  (cd ~/vault-plugin-secrets-ethsign && export GOTOOLCHAIN=local GOFLAGS=-mod=mod \
     && $GO get github.com/hashicorp/vault/sdk@v0.14.0 github.com/hashicorp/vault/api@v1.15.0 && $GO mod tidy \
     && $GO build -o "$ROOT/$PLUGIN" .)
fi
ls -la "$PLUGIN"

say "Dashboard packages (npm)"
(cd dashboard/dashboard
 # node_modules copied from another machine has broken launchers: reinstall if vite does not run.
 ./node_modules/.bin/vite --version >/dev/null 2>&1 || rm -rf node_modules
 npm install --no-audit --no-fund --loglevel=error)

say "Settings (.env) and agent token"
[ -f .env ] || cp .env.example .env
if ! grep -qE '^AGENT_TOKEN=.+' .env; then
  TOKEN=$(.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(32))')
  sed -i "s|^AGENT_TOKEN=.*|AGENT_TOKEN=$TOKEN|" .env
fi
sync_policy_hash

touch .run/.setup-done

}

do_start() {
[ -f .env ] && [ -x .venv/bin/python ] || { echo "Run ./run.sh setup first."; exit 1; }
PY="$ROOT/.venv/bin/python"
grep -qE '^AGENT_TOKEN=.+' .env && sync_policy_hash
stop_all
mkdir -p .run
[ "${1:-}" = "--keep" ] || rm -f vault_gateway/data/vault.db

loadenv() { set -a; . ./.env; set +a; }
loadenv

# launch NAME DIR CMD...  -> runs in its own process group, logs to .run/NAME.log
launch() { local name=$1 dir=$2; shift 2
  ( cd "$dir" && exec setsid "$@" >"$ROOT/.run/$name.log" 2>&1 ) &
  echo $! >"$ROOT/.run/$name.pid"; }
wait_for() { local name=$1 url=$2
  for _ in $(seq 1 60); do curl -sf -o /dev/null "$url" && { printf '  ok  %s\n' "$name"; return; }; sleep 0.5; done
  echo "  FAILED $name (see .run/$name.log)"; tail -5 ".run/$name.log"; exit 1; }

echo "Starting SecureAgent..."
launch anvil . anvil --host 127.0.0.1 --port 8545
for _ in $(seq 1 40); do
  curl -sf -X POST -H 'content-type: application/json' --data '{"jsonrpc":"2.0","id":1,"method":"eth_chainId","params":[]}' http://127.0.0.1:8545 >/dev/null && break; sleep 0.5; done
echo "  ok  anvil (local test chain, free ETH)"

launch vault . vault server -dev -dev-root-token-id="$VAULT_ROOT_TOKEN" \
  -dev-plugin-dir "$ROOT/vault_gateway/vault_setup/plugins" -dev-listen-address=127.0.0.1:8200
wait_for vault "http://127.0.0.1:8200/v1/sys/health"

(cd vault_gateway && "$PY" vault_setup/setup_dev.py --root-token "$VAULT_ROOT_TOKEN" | sed 's/^/      /')
loadenv   # picks up the fresh VAULT_TOKEN

launch phe phe_service "$PY" -m uvicorn main:app --host 127.0.0.1 --port 8300
wait_for phe "http://127.0.0.1:8300/health"

launch gateway vault_gateway "$PY" -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000
wait_for gateway "http://127.0.0.1:8000/health"
SIGNER=$(curl -s http://127.0.0.1:8000/health | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["signing_account"])')

launch chain_sync phe_service "$PY" chain_sync.py --address "$SIGNER"   # real on-chain balance -> encrypted -> PHE
launch submitter dashboard/submitter env ANVIL_RPC_URL="$ETH_RPC_URL" "$PY" -m uvicorn main:app --host 127.0.0.1 --port 8100
wait_for submitter "http://127.0.0.1:8100/health"

launch agent_api mcp_agent env -u AGENT_TOKEN SECUREAGENT_ENV_FILE="$ROOT/.env" "$PY" -m uvicorn agent_api:app --host 127.0.0.1 --port 8500
wait_for agent_api "http://127.0.0.1:8500/health"

launch dashboard dashboard/dashboard ./node_modules/.bin/vite --host 0.0.0.0 --port 5173 --strictPort
wait_for dashboard "http://127.0.0.1:5173/"

IPS=$(hostname -I 2>/dev/null | tr ' ' '\n' | grep -E '^[0-9]+\.' | grep -v '^172\.1[78]\.' | head -3)
echo
echo "SecureAgent is running. Open the dashboard:"
echo "   http://localhost:5173        (on this machine, or through an SSH tunnel: ssh -L 5173:localhost:5173 <user>@<vm>)"
for ip in $IPS; do echo "   http://$ip:5173"; done
echo "Logs: .run/*.log      Stop: ./run.sh stop"

}

case "${1:-all}" in
  setup) do_setup ;;
  start) shift; do_start "$@" ;;
  stop)  stop_all; echo stopped ;;
  all)   [ -f .run/.setup-done ] || do_setup; do_start ;;
  *)     echo "usage: ./run.sh [setup|start [--keep]|stop]"; exit 1 ;;
esac

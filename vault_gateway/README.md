# Vault Gateway (Member 3)

The security gateway of SecureAgent. Every tool call from the MCP server passes six deny-by-default gates here before anything is signed. The signing key is created and kept inside HashiCorp Vault. The gateway can ask Vault to sign, but can never read the key.

**Status: real mode works end to end.**
- **Stub mode** (for teammates): enforces the request format and always refuses key export. The six gates are placeholders that pass, and nothing is signed.
- **Real mode:** all six gates enforce `policy/policy.yaml`. Approved transfers are signed inside Vault and handed on, and every decision lands in a tamper-evident audit log.
- **Not connected yet:** `get_balance`, which goes through Member 1's PHE service (open point 1 in `contracts/CONTRACTS.md`). Until then it is refused.

| Path | What it is |
|---|---|
| `app/main.py` | FastAPI app: `POST /v1/tool-call`, `GET /v1/events`, `WS /ws/events`, `GET /health` |
| `app/models.py` | Request, response and event shapes, matching `contracts/CONTRACTS.md` |
| `app/pipeline.py` | Runs gate 0 (request format), then gates 1–6 in order, then the action. Stops at the first failure. Transfers run one at a time. |
| `app/gates.py` | The six gates, plus the stub placeholders |
| `app/policy.py` | Loads and checks `policy/policy.yaml`, and re-reads it on every change (the kill switch) |
| `app/actions.py` | What each tool does once every gate has passed |
| `app/signer.py` | Signing through Vault. Refuses to start with a token that could export the key. |
| `app/handoff.py` | Hands the signed transaction to Member 4, or sends it to Anvil directly for standalone testing |
| `app/chain.py` | Access to Anvil: balance, gas price, nonce, transaction status |
| `app/store.py` | SQLite database: the append-only, hash-chained audit log and the spend record |
| `app/events.py` | Live event feed for the dashboard, and the hash-chain functions |
| `app/tools.py` | The tool catalog and the stub results |
| `app/config.py` | Settings from environment variables |
| `policy/policy.yaml` | Agents, their tools, assets, approved recipients and limits |
| `tests/` | `pytest` suite |
| `scripts/demo_scenarios.py` | Runs the four demo scenarios (plus three extra attacks) against a running gateway |
| `scripts/watch_events.py` | Prints the live decision feed in the terminal, red and green |
| `scripts/verify_audit.py` | Checks the audit log for tampering |
| `scripts/new_agent_token.py` | Makes an agent session token, plus the hash that goes into the policy |
| `contracts/CONTRACTS.md` | The formats agreed with Members 2 and 4 |
| `contracts/examples/demo_events.json` | One sample event per demo scenario |
| `vault_setup/gateway-policy.hcl` | Vault rules for the gateway's token: may sign, may never export the key |
| `vault_setup/setup_dev.py` | Sets up a freshly started dev Vault and Anvil in one go, and writes `.env` |
| `vault_setup/sign_test.py` | Signs 0.01 ETH inside Vault and checks the export is refused, without the gateway |

## Python environment (once)

```
cd ~/minor_project/vault_gateway
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Stub mode (no Vault or blockchain needed)

This is all Members 2 and 4 need in order to build against the Vault.

```
STUB_MODE=1 .venv/bin/uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000
```

- Open http://localhost:8000/docs to see the endpoints and try calls from the browser.
- Watch decisions arrive live: `.venv/bin/python scripts/watch_events.py`

## Run the tests

```
.venv/bin/python -m pytest
```

Some tests need live services and skip themselves otherwise. With Anvil and Vault running and `setup_dev.py` done (real mode, below), use the gateway token from `.env`:
```
ANVIL_RPC_URL=http://127.0.0.1:8545 VAULT_TEST_ADDR=http://127.0.0.1:8200 VAULT_TEST_TOKEN=<gateway token from .env> VAULT_TEST_ROOT_TOKEN=root .venv/bin/python -m pytest
```

## Real mode

### One-time installs

**1. HashiCorp Vault**
```
cd ~/Downloads
wget https://releases.hashicorp.com/vault/2.1.1/vault_2.1.1_linux_amd64.zip
unzip -o vault_2.1.1_linux_amd64.zip vault -d ~/.local/bin
vault version
```

**2. The ethsign plugin.** Vault's built-in Transit engine can't sign Ethereum transactions because it has no secp256k1 keys, so we add this plugin.
```
git clone https://github.com/kaleido-io/vault-plugin-secrets-ethsign.git ~/vault-plugin-secrets-ethsign
mkdir -p ~/minor_project/vault_gateway/vault_setup/plugins
cd ~/vault-plugin-secrets-ethsign
go build -o ~/minor_project/vault_gateway/vault_setup/plugins/ethsign .
```

**3. Foundry (Anvil and cast)**
```
curl -L https://foundry.paradigm.xyz | bash
```
Open a new terminal, then:
```
foundryup
anvil --version
```

**4. An agent token.** Run this once:
```
cd ~/minor_project/vault_gateway
.venv/bin/python scripts/new_agent_token.py
```
- Put the printed `token_sha256` into `policy/policy.yaml`.
- Keep the `token` itself private. It goes to Member 2's `.env`, and to `AGENT_TOKEN` when you run the demo script. Never commit it.

### Every time: start the services

Vault dev mode and Anvil keep everything in memory, so after restarting either one, repeat these steps.

**Terminal 1: local blockchain**
```
anvil
```

**Terminal 2: Vault, with the plugin loaded**
```
vault server -dev -dev-root-token-id=root -dev-plugin-dir ~/minor_project/vault_gateway/vault_setup/plugins
```

**Terminal 3: set up Vault and Anvil, then start the gateway**
```
cd ~/minor_project/vault_gateway
.venv/bin/python vault_setup/setup_dev.py
.venv/bin/uvicorn app.main:create_app --factory --env-file .env --host 0.0.0.0 --port 8000
```
http://localhost:8000/health shows `"mode": "real"` and the signing account.

<details>
<summary>What <code>setup_dev.py</code> does, and how to do it by hand</summary>

It uses the dev server's root token. The gateway itself never gets that token.
```
export VAULT_ADDR=http://127.0.0.1:8200
export VAULT_TOKEN=root
vault secrets enable -path=ethereum ethsign            # 1. enable the plugin
vault write -f ethereum/accounts                       # 2. Vault creates the key and prints only its address
cast rpc anvil_setBalance <address> 0x8AC7230489E80000 # 3. 10 test ETH for that address
vault policy write vault-gateway vault_setup/gateway-policy.hcl   # 4. sign only, export denied
vault token create -policy=vault-gateway -field=token  # 5. the gateway's limited token
```
6. It saves `VAULT_ADDR`, the gateway token and `ETH_RPC_URL` to `.env`. That file is git-ignored; never commit it.

Started with the root token instead, the gateway refuses to run.
</details>

**Terminal 4: watch decisions live**
```
cd ~/minor_project/vault_gateway
.venv/bin/python scripts/watch_events.py
```

**Terminal 5: run the demo**
```
cd ~/minor_project/vault_gateway
AGENT_TOKEN=<agent token> .venv/bin/python scripts/demo_scenarios.py --extra
```
Expected:
```
1. PASS     Legitimate transfer: 0.05 ETH to Alice
2. BLOCKED  Excessive amount: 5 ETH to Alice                 (gate 6)
3. BLOCKED  Unauthorized asset: 0.05 BTC to Alice            (gate 3)
4. DENIED   Private key handoff request                       (gate 2)
5. BLOCKED  In-limit theft: 0.01 ETH to an unknown address   (gate 4)
6. BLOCKED  Wrong session token                               (gate 1)
7. BLOCKED  Injected field: skip_checks                       (gate 0)
```

**Kill switch:** change `status: active` to `status: suspended` in `policy/policy.yaml` and save. The agent's next request is refused, with no restart. Change it back to reactivate.

**Audit check:** `.venv/bin/python scripts/verify_audit.py`

### Settings

| Variable | Default | What it is |
|---|---|---|
| `STUB_MODE` | unset | `1` for stub mode |
| `VAULT_ADDR` | `http://127.0.0.1:8200` | HashiCorp Vault |
| `VAULT_TOKEN` | — | The gateway token (required in real mode) |
| `ETH_RPC_URL` | `http://127.0.0.1:8545` | Anvil |
| `SUBMITTER_URL` | unset | Member 4's submitter. Unset, the gateway sends transactions to Anvil itself (standalone testing). |
| `SIGNER_ADDRESS` | unset | Only needed if Vault holds more than one account |
| `POLICY_PATH` | `policy/policy.yaml` | |
| `DB_PATH` | `data/vault.db` | Audit log and spend record |
| `CORS_ORIGINS` | `http://localhost:5173,http://localhost:3000` | Where the dashboard runs |

### Troubleshooting

- **`Vault has no signing account`**: run `vault_setup/setup_dev.py` again. Dev mode forgets everything on restart.
- **`This Vault token could export the private key`**: the gateway was started with the root token. Start it with `--env-file .env` as shown.
- **`could not enable the ethsign plugin`**: Vault was started without `-dev-plugin-dir`. Restart it as in terminal 2.
- **`... token_sha256 must be the SHA-256 ...`**: do one-time step 4.
- **Every transfer is refused at gate 5**: the Vault account has no ETH, probably because Anvil restarted. Run `vault_setup/setup_dev.py` again.
- **When Vault starts the plugin, it logs one `plugin tls init` error.** This is harmless: the plugin was built with an older Vault SDK, Vault retries, and the plugin then works.

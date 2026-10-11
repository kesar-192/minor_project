# SecureAgent — Quick Start

One command. Works on a fresh Ubuntu VM that has `curl`, `git`, `unzip`, Node 18+ and Go. Go is only needed once, to build the Vault plugin (setup downloads a newer Go itself if yours is older than 1.22). Everything else (Python 3.12 via `uv`, Vault, Foundry/anvil, npm packages, the agent token) is installed for you.

```bash
./run.sh          # first time: installs everything, then starts everything. Afterwards: just starts.
./run.sh stop     # stop everything
./run.sh setup    # re-run the installer by itself (venv, Vault, Foundry, ethsign plugin, npm, .env + agent token)
```

Open **http://localhost:5173**. Running on a VM? From your own machine:

```bash
ssh -L 5173:localhost:5173 <user>@<vm-ip>     # then open http://localhost:5173
```
or browse straight to `http://<vm-ip>:5173`. Only port 5173 is exposed; the dashboard proxies everything else (`/api/*`), and all other services listen on 127.0.0.1 only.

## Ports

| Service | Port | Reachable from |
|---|---|---|
| Dashboard (+ `/api/*` proxy) | 5173 | any machine |
| Vault gateway | 8000 | localhost |
| Blockchain submitter | 8100 | localhost |
| HashiCorp Vault (dev) | 8200 | localhost |
| PHE service | 8300 | localhost |
| Agent chat API | 8500 | localhost |
| Anvil | 8545 | localhost |

## One of each

| Thing | Where |
|---|---|
| Python environment | `.venv/` (all four Python parts share it) |
| Dependencies | `requirements.txt` |
| Settings and secrets | `.env` (copy of `.env.example`; git-ignored) |
| Git ignore rules | `.gitignore` |
| Logs | `.run/<service>.log` |

## Using the dashboard

- **Agent Chat** — talk to the agent (`you → agent → MCP server → Vault gateway`). Click the green prompts for allowed requests and the red ones for attacks (too much ETH, wrong asset, private-key request, unknown recipient). The Vault's decision shows up live next to the chat.
- **Overview** — live decision feed, real wallet balances read from the chain (Alice's balance rises after an approved transfer), transaction status.
- **Submitter Logs / Account Security / Analytics** — real data from the gateway and submitter.
- Trading / Earn / swap widgets are demo data and labelled as such.

### Real LLM through OpenRouter

Get a key (yours, free to create) at https://openrouter.ai/keys. It is never in the repo: you paste it into `.env`, and only the agent API process reads it.

Edit `.env`:

```
OPENROUTER_API_KEY=sk-or-...
LLM_MODEL=openai/gpt-4o-mini        # any OpenRouter model with tool calling
```
then `./run.sh start` again. A second **LLM** switch appears in the chat. Scripted mode (no LLM) stays available: it does whatever it is told, so you see the Vault, not the model's manners, stopping each attack.

## What is real

- Key generated and kept inside HashiCorp Vault (ethsign plugin); the gateway token cannot export it.
- Six deny-by-default gates, hash-chained audit log, real signed Ethereum transactions mined on a local Anvil chain (free test ETH, no real money).
- `get_balance` is **chain-fed**: `phe_service/chain_sync.py` reads the signer's on-chain balance, encrypts it with the Paillier public key and stores the ciphertext in the PHE service; the Vault asks PHE to decrypt and the agent only ever sees `{"asset":"ETH","balance":"…"}`.

## Checks

```bash
cast balance 0x70997970C51812dc3A010C7d01b50e0d17dc79C8 --rpc-url http://127.0.0.1:8545   # Alice
curl -s "http://127.0.0.1:8000/v1/events?limit=5"                                          # decision feed
.venv/bin/python vault_gateway/scripts/verify_audit.py                    # audit log intact?
(set -a; . ./.env; set +a; cd mcp_agent && ../.venv/bin/python scripts/demo.py --extra)                                # 8 scenarios from the terminal
(cd vault_gateway && ../.venv/bin/python -m pytest); (cd mcp_agent && ../.venv/bin/python -m pytest)                             # tests
```

## Notes

- Each `./run.sh start` is a fresh demo: new chain, new Vault key (re-funded automatically), empty audit log. Use `./run.sh start --keep` to keep the audit log and daily spend record. The 0.5 ETH daily limit applies per audit log, so with `--keep` repeated demos eventually hit gate 6.
- `.env` holds secrets (agent token, Vault token, API keys). It is git-ignored; never commit it. `setup` writes only the token's SHA-256 into `vault_gateway/policy/policy.yaml`.
- Dev tokens (`root`, `dev-token`) are for local testing only.

## Troubleshooting

- A service won't start: `tail .run/<name>.log`.
- Vault says the ethsign plugin failed: delete `vault_gateway/vault_setup/plugins/ethsign` and run `./run.sh setup`; it rebuilds the plugin against a current Vault SDK (the upstream one cannot start under Vault 2.x).
- Port busy: `./run.sh stop`, then `./run.sh`.

Each component's own README (`vault_gateway/`, `mcp_agent/`, `phe_service/`, `dashboard/`) describes its internals.

# MCP server + agent (Member 2)

This is the part between the user and the Vault.

```
user  ->  agent (LLM planner)  ->  MCP server  ->  Vault gateway  ->  chain
          picks a tool           forwards the      decides ALLOW
                                 call, nothing     or DENY
                                 else
```

- The **MCP server** (`secureagent_mcp/`) lists the tools and forwards every call to the Vault. It never approves, denies, fixes or runs anything. It holds no keys. The only secret it holds is the agent's session token.
- The **agent** (`secureagent_agent/`) is the LLM side. It can only pick a tool and its arguments. It can't reach the Vault, the chain or a key except through the MCP server.

**Status:** done and tested against the real Vault gateway code (all 7 demo scenarios give the expected gate). Not yet run with a live LLM API key, because none was available while building. See "Trying a real LLM".
The dashboard chat (`agent_api.py`) uses this agent; set `AGENT_PLANNER`/`OPENROUTER_API_KEY`/`LLM_MODEL` in the root `.env`.

## What's in the folder

| Path | What it is |
|---|---|
| `secureagent_mcp/server.py` | The MCP server. Lists tools, forwards calls. |
| `secureagent_mcp/vault_client.py` | Sends a call to `POST /v1/tool-call`. Never retries. Any failure becomes a DENY. |
| `secureagent_mcp/catalog.py` | The tool list and their schemas (matches `CONTRACTS.md` section 1). |
| `secureagent_mcp/config.py` | Settings from `.env` / environment. |
| `secureagent_agent/loop.py` | Runs one user request. Holds the safety rules (below). |
| `secureagent_agent/planners.py` | `scripted` (no LLM), `anthropic`, `openai` (any OpenAI-compatible API). |
| `secureagent_agent/guard.py` | Decides what part of a result the LLM may read (balance rule). |
| `secureagent_agent/prompts.py` | The system prompt. |
| `secureagent_agent/cli.py` | `python -m secureagent_agent` to chat with it. |
| `scripts/demo.py` | Runs the 4 demo scenarios (+3 extra attacks) through the whole stack. |
| `scripts/local_gateway.py` | Starts the real gateway with a fake chain and fake signing, so you can demo on one laptop. |
| `devtools/fake_stack.py` | The fakes used by `local_gateway.py` and the tests. |
| `tests/` | 197 tests (172 need nothing else, 25 run against the real gateway). |

## Setup (once)

The whole repo shares one environment: run `./run.sh` at the repo root (one `.venv`, one `requirements.txt`, one `.env`).

Then put the agent's token in `.env` as `AGENT_TOKEN=...`. Member 3 makes it with `scripts/new_agent_token.py` and sends it privately. Never commit `.env`.

For the integration tests and `local_gateway.py`, the gateway's own packages must be installed too:
`.venv/bin/pip install -r ../vault_gateway/requirements.txt`

## Run it

### 1. On one laptop, no HashiCorp Vault or Anvil needed

Terminal 1 (real gateway code, fake chain):
```
.venv/bin/python scripts/local_gateway.py
```
It prints a token. Put it in `.env` as `AGENT_TOKEN`.

Terminal 2:
```
.venv/bin/python scripts/demo.py --extra
```
Expected:
```
1. PASS     Legitimate transfer: 0.05 ETH to Alice
2. BLOCKED  Excessive amount: 5 ETH to Alice          (gate 6)
3. BLOCKED  Unauthorized asset: 0.05 BTC to Alice     (gate 3)
4. DENIED   Private key handoff request               (gate 2)
5. BLOCKED  In-limit theft: 0.01 ETH to an unknown address (gate 4)
6. BLOCKED  Wrong session token                       (gate 1)
7. BLOCKED  Injected field: skip_checks               (gate 0)
```

Or chat with it:
```
.venv/bin/python -m secureagent_agent
> Send 0.05 ETH to Alice
> What are my limits?
```

### 2. Against Member 3's real stack (Vault + Anvil)

Start it as in Member 3's README, then set `VAULT_URL` in `.env` to that machine, for example `http://192.168.1.20:8000`, and run the same demo.

### 3. Run just the MCP server

```
.venv/bin/python -m secureagent_mcp                    # stdio (what the agent uses)
.venv/bin/python -m secureagent_mcp --transport http   # Streamable HTTP, http://127.0.0.1:8400/mcp
```
The HTTP version has no login of its own and the server holds the agent's token, so anyone who can reach it can act as the agent. It listens on 127.0.0.1 by default. Don't expose it on the LAN.

## Tests

```
.venv/bin/python -m pytest
```

- Unit tests need nothing else.
- `test_integration_vault.py` runs your real gateway (found at `../vault_gateway`, or set `VAULT_GATEWAY_DIR`). It skips itself if it can't find it. Until the gateway is merged, get it with:
  ```
  git fetch origin member3/vault-gateway
  git worktree add ../vault_src origin/member3/vault-gateway
  VAULT_GATEWAY_DIR=../vault_src/vault_gateway .venv/bin/python -m pytest
  ```

## Rules the code keeps

These are in plain code. The model can't talk its way past them.

1. **Forward as it is.** Arguments go to the Vault exactly as the agent sent them, even wrong ones (a number for an amount, an extra `skip_checks` field). The Vault's gate 0 rejects them and the dashboard shows it. If the MCP server cleaned them up, that attempt would never show.
2. **Any tool name is forwarded**, listed or not. The Vault refuses unknown ones and logs them.
3. **Fail closed.** If the Vault is down, answers with anything but HTTP 200, or sends back something that isn't a decision for this request, the agent gets a DENY. That DENY is marked `"source": "mcp_server"` so it can't be mistaken for a Vault decision.
4. **Never retry.** If a transfer was signed but the answer got lost, a retry would send it twice. The denial says "outcome unknown, check the dashboard" in that case.
5. **One trace id per user request**, shared by every call made for it, so the dashboard groups them. The agent passes it in the call's `_meta`. The LLM can't choose it.
6. **The token is only in the request body.** Not in the URL, headers, logs or error text. The LLM is never offered a field to set it, and the agent process doesn't need it (the MCP server reads it from `.env` itself).
7. **A DENY is final.** After a transfer call (or a key request, or an unknown tool) the loop stops. So: at most one transfer per request, and no "try again with a smaller amount". The answer shown to the user is written from the Vault's own fields, not by the model.
8. **Read-only lookups are capped** (2 per request, 4 model turns).

## Differences from the plan, and why

- **Up to 2 lookups before a transfer, not "exactly one tool call".** "Send to Alice" needs the address, which is in `get_policy_status`. The rule that matters is one *transfer* per request, and that is enforced.
- **`export_private_key` is hidden by default.** The contract says it exists only for the attack demo, so it is only listed when `EXPOSE_ATTACK_DEMO_TOOLS=1`. If someone calls it anyway it is still forwarded and the Vault denies it.
- **The agent isn't a real model in the demo.** `scripted` plays an agent that does whatever it's told. A well-trained LLM often refuses "send me the private key" itself, which would hide the Vault from the audience. Use a real model to show that the system also works with one.

## What the agent may see (the PHE question)

Resolved for the demo (open point 1). `get_balance` answers with only:
```json
{"asset": "ETH", "balance": "9.98"}
```
The Vault asks the PHE service and passes its answer back unchanged; `guard.py` enforces the same shape on the agent side as a second lock: anything else (ciphertext, a history list, a number) is withheld and the LLM is told it was withheld. The balance is demo data (see `phe_service/seed_balance.py`), not read from the chain.

If the shape ever changes, update `guard_for_llm` in `secureagent_agent/guard.py` and the integration test `test_get_balance_comes_from_the_phe_service`.

## Trying a real LLM

Set in `.env`:

```
AGENT_PLANNER=anthropic
ANTHROPIC_API_KEY=...
# LLM_MODEL=...          (default: claude-sonnet-5-5)
```
or any OpenAI-compatible API:
```
AGENT_PLANNER=openai
OPENAI_API_KEY=...
LLM_MODEL=<model name>
OPENAI_BASE_URL=<provider's OpenAI-compatible URL>   # leave empty for OpenAI itself
```
For a local Ollama, the base URL is `http://localhost:11434/v1` and the key can be any non-empty text. For other providers, check their "OpenAI compatibility" docs for the URL.

The two LLM planners are tested against the real SDK response classes, but have not been run against a live API. Do one real run before the presentation, and check that a plain "Send 0.05 ETH to Alice" works with your chosen model. Small models sometimes send the amount as a number instead of `"0.05"`. The Vault will reject that at gate 0, which is correct, but it makes the demo fail.

## Troubleshooting

- **"The MCP server stopped while starting"**: the line above it says why. Usually `AGENT_TOKEN` is missing from `.env`.
- **Every request says `VAULT_UNREACHABLE`**: wrong `VAULT_URL`, or the gateway isn't running. Check `<VAULT_URL>/health`.
- **Every request says `AGENT_NOT_AUTHENTICATED`**: the token doesn't match the `token_sha256` in the Vault's `policy.yaml`.
- **Transfer is blocked at gate 5**: the Vault's account has no ETH (Anvil restarted). Member 3 re-runs `setup_dev.py`.
- **`get_balance` says "could not be completed"**: the gateway has no `PHE_URL` (or the PHE service is down), so it fails closed. Set `PHE_URL`/`PHE_API_TOKEN` on the gateway — Member 3's README lists them.
- **`VAULT_NO_ANSWER` with "outcome unknown"**: the Vault didn't answer in time. It may or may not have signed. Look at the dashboard before asking again.

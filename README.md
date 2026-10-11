# SecureAgent

A security layer that lets an AI agent move crypto — but only through a checkpoint
that can say **no**. The agent can *ask* to do something; a separate, locked-down
gateway checks it against rules and only then lets a key sign a transaction. The
agent never touches the private key, and every decision is written to a log that
cannot be quietly edited.

This is a **security-architecture demo**. It uses real cryptography and a real
EVM blockchain, but on a **local test chain with free, worthless ETH** — no real
money is involved (see [Is the chain real? Is it real money?](#is-the-chain-real-is-it-real-money)).

To run it: `./run.sh` (installs and starts everything), then open http://localhost:5173. Details in **[QUICKSTART.md](QUICKSTART.md)**.

---

## The four parts

| Part | Folder | Job in one line |
|---|---|---|
| AI agent + MCP server | `mcp_agent/` | Turns a user request into tool calls and forwards them |
| Vault gateway | `vault_gateway/` | The gatekeeper: six checks, then signs via HashiCorp Vault |
| PHE service | `phe_service/` | Privacy-preserving (encrypted) balance lookup |
| Dashboard + submitter | `dashboard/` | Web UI (live decisions, wallet, agent chat), plus the piece that puts a signed tx on the chain |

Big picture:

```
Browser ── :5173 only (the dashboard proxies /api/*) ──┐
                                                        ▼
 Agent chat API :8500 ── LLM (OpenRouter) or scripted planner
        │  tool calls (MCP, stdio)
        ▼
MCP server  ── adds the session token, cannot approve/deny/sign ──►  Vault gateway :8000
                                                                        │
                                                  6 deny-by-default gates
                                                                        │
                                          HashiCorp Vault :8200 ── signs (key never leaves)
                                                                        │
                       PHE service :8300 (get_balance) ◄── chain_sync (on-chain balance, encrypted)
                                                                        │
                                                     Submitter :8100 ──► Anvil :8545
                                                                        │
                                                       Dashboard :5173 ◄┘  (events + tx status)
```

Only the dashboard port (5173) is reachable from other machines; every other
service listens on 127.0.0.1.

---

## How agents are integrated

You talk to the agent from the dashboard's **Agent Chat** page (served by
`mcp_agent/agent_api.py`). Two modes: **Scripted** (no LLM; it does whatever it is
told, so you see the Vault stopping each attack) and **LLM** through
[OpenRouter](https://openrouter.ai/keys): put `OPENROUTER_API_KEY` and `LLM_MODEL`
in `.env` and the LLM switch appears. Only the chat API process reads that key.

The agent does not talk to the Vault directly. It talks through an **MCP server**
(MCP = a standard "tools" interface for AI models).

- The agent is given a menu of tools: `get_balance`, `propose_transfer`,
  `get_transaction_status`, `get_policy_status`.
- The MCP server (`mcp_agent/secureagent_mcp/server.py`) is deliberately dumb: it
  **just forwards** whatever tool the agent asks for to the Vault and passes the
  answer back. It cannot approve, deny, sign or send anything. It holds no keys.
- The only secret the MCP server holds is the **agent's session token**, which it
  adds to each request itself — so neither the LLM nor the agent process ever sees it.
- On the agent side there is a **second lock** (`secureagent_agent/guard.py`): even
  if the Vault returned something messy, the agent only lets the model read a clean
  `{"asset":"ETH","balance":"9.98"}`. Anything else (ciphertext, a history dump) is
  withheld. This is belt-and-braces, not the real enforcement.

So "integrating an agent" = giving it these MCP tools. It can only *request*
actions; every request is judged independently by the Vault.

---

## How the Vault works

There are two different "vaults" — don't mix them up.

### 1. The Vault gateway (`vault_gateway/`, FastAPI on :8000)

This is our own code. Every request passes **six deny-by-default gates**, and it
stops at the **first** failure:

| # | Gate | Question it asks |
|---|---|---|
| 1 | Identity & status | Is the agent known, is its session token correct, is it active? |
| 2 | Permitted tools | Is this tool allowed? Asking for the private key is refused, always. |
| 3 | Allowed assets | Only ETH here. |
| 4 | Recipient allow-list | Only approved people (e.g. Alice, Bob). |
| 5 | Balance | Is amount + gas ≤ the real on-chain balance? (numbers read fresh from the chain) |
| 6 | Limits | Under the per-transaction and rolling 24-hour caps? |

Only if all six pass does it perform the action (sign + hand off).

### 2. HashiCorp Vault (the real HashiCorp product, :8200)

This is where the **private key actually lives**, inside a plugin called
**ethsign**. This is the key design idea:

- The private key is **created inside HashiCorp Vault and never comes out.**
- The gateway holds a limited token whose policy
  (`vault_gateway/vault_setup/gateway-policy.hcl`) allows exactly three things:
  *list* accounts, *read* an address, *request a signature*. The "export private
  key" endpoint is explicitly **denied**.
- On startup (`vault_gateway/app/signer.py`) the gateway literally asks Vault
  "what am I allowed to do?" and **refuses to run** if its token could ever export
  the key.
- To sign, the gateway sends the transaction details to Vault; Vault signs
  internally and returns only the **signed transaction** (the finished,
  tamper-proof blob). The key itself never leaves.

Trust is inverted: the agent cannot steal the key because it never has it; the
gateway cannot leak it because its token forbids it. Then the signed transaction
is handed to the submitter and put on the chain.

Every decision is also written to an **append-only, hash-chained audit log**
(each entry includes the previous entry's hash), so any edit breaks verification
(`vault_gateway/scripts/verify_audit.py`).

---

## How PHE works

PHE = **Partially Homomorphic Encryption** (specifically Paillier here).
"Homomorphic" means you can do **math on encrypted numbers without decrypting
them**:

```
E(a) + E(b)  ==  E(a + b)
```

Why that matters: a balance can be stored **encrypted**, and a server can combine
encrypted balances **without ever seeing the real numbers**. In this project
(`phe_service/`):

- A balance is kept per user+asset as a **ciphertext** — the store only ever holds
  the scrambled form (`phe_service/balance_store.py`).
- To read it you call the PHE service with your requester id; it decrypts **only
  for an authorized requester** (`user123`) and returns a plain decimal string
  such as `"9.98"`.
- `get_balance` goes: agent → MCP → Vault → PHE → back. The LLM is allowed to see
  only that final clean number.

**Chain-fed:** `phe_service/chain_sync.py` reads the Vault signer's real balance from
Anvil every few seconds, encrypts it with the PHE *public* key and stores the
ciphertext. It never holds the private key, so the plaintext exists only on the
chain and inside the PHE service. The stored balance is held in memory and is
refilled from the chain, so it survives restarts. Design notes:
`vault_gateway/contracts/CHAIN_FED_BALANCE.md`. Requester authentication is still
a fixed id (`user123`), which is a known limitation.

---

## How the ETH chain works

The chain is **Anvil**, a local development blockchain that ships with Foundry.

- The gateway's gates read the current **gas price** and the **account balance**
  from the chain (`vault_gateway/app/chain.py`).
- When a transfer is approved, the gateway asks HashiCorp Vault to sign it, then
  hands the **signed raw transaction** to the submitter.
- The submitter (`dashboard/submitter/`) sends that raw transaction to Anvil and
  tracks the receipt (pending → confirmed → failed).
- Anvil mines the transaction and balances change.

---

## Is the chain real? Is it real money?

Honest split:

### What is genuinely real (real cryptography)

- A **real secp256k1 keypair** is generated inside HashiCorp Vault.
- **Real ECDSA signatures** — genuine Ethereum transaction signing.
- **Real Ethereum transactions** and a **real EVM** executing them. (In our run,
  the transfer was really mined: `status 1`, `gasUsed 21000`, and Alice's balance
  really changed.)
- Real **tamper-evident audit logging** (hash-chained).

### What is not "real" in the money sense

- Anvil is a **local sandbox** on `127.0.0.1`. It is **not** connected to Ethereum
  mainnet or any public network. No internet, no miners, no other users.
- The test ETH is **worthless**: the signing account is given 10 fake ETH
  (`anvil_setBalance`), and Anvil starts its test accounts with 10,000 fake ETH.
- The accounts are **Anvil's public, well-known test accounts** — not anyone's real
  wallet, and they hold nothing of value.

### Is any real money involved?

**No.**

- No real currency, no payments, no bank, no exchange, no real wallet, no mainnet.
- Nothing of monetary value exists or moves.
- It is a security-architecture demo that uses real cryptographic and blockchain
  machinery to prove the design works — running against valueless test money on a
  private toy chain.

---

## Bottom line

- **Agent integration** = give the AI MCP tools; it can only *ask*, and every ask
  is gated.
- **Vault** = the key is generated inside HashiCorp Vault, can be *used to sign*
  but never *read*; the gateway proves it cannot export the key before it is even
  allowed to run.
- **PHE** = balances stay encrypted; the service can work with them and only
  reveals a clean total to an authorized reader.
- **Chain** = real cryptography and a real EVM, but a **local test chain with free,
  valueless ETH**.
- **Money** = none. It is a safe, offline simulation of the security model, not a
  live payment system.

---

## Running it

```bash
./run.sh          # first time installs everything (venv, Vault, Foundry, plugin, npm), then starts it all
./run.sh stop
```

Open http://localhost:5173 (or `ssh -L 5173:localhost:5173 <user>@<vm>` when it runs
on a VM). One `.venv`, one `requirements.txt`, one `.env`, one `.gitignore` at the
repo root. Full guide, OpenRouter setup and checks: **[QUICKSTART.md](QUICKSTART.md)**.

Each component also has its own README for internals:

- `mcp_agent/README.md`
- `vault_gateway/README.md`
- `phe_service/README.md`
- `dashboard/README.md`

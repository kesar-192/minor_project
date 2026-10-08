# Vault Gateway Interfaces

**Owner:** Member 3 (Vault / Security Gateway)
**Status:** draft for Week 1 sign-off. Please reply with changes by **Oct 13**. After that, any change needs agreement from everyone it affects.

| # | Interface | From → To | Sign-off needed from |
|---|---|---|---|
| 1 | Tool-call request | MCP server → Vault | Member 2 |
| 2 | Tool-call response | Vault → MCP server | Member 2 |
| 3 | Decision events | Vault → Dashboard | Member 4 |
| 4 | Signed transaction hand-off | Vault → Blockchain submitter | Member 4 |
| 5 | Open points | — | Members 1, 2, 4 |

## Ground rules (all interfaces)

- JSON over HTTP.
- **Amounts are decimal strings in whole units** (`"0.05"` ETH), never JSON numbers. Floats lose precision (`0.1 + 0.2 != 0.3`), and the limit checks must be exact. Wei values are integer strings.
- Addresses are `0x` plus 40 hex characters, in any letter case. The Vault answers with the checksummed form.
- Timestamps are ISO 8601 UTC with milliseconds, e.g. `2026-10-13T10:15:00.123Z`.
- `trace_id` is a UUID v4. The MCP server creates it once per user request, and every response and event for that request carries it.

**Local ports**

| Service | Port | Owner |
|---|---|---|
| Vault gateway (FastAPI) | 8000 | Member 3 |
| HashiCorp Vault | 8200 | Member 3 (only the gateway talks to it) |
| Anvil | 8545 | Member 4 |
| Blockchain submitter | 8100 (proposed) | Member 4 |
| PHE service | 8300 (proposed) | Member 1 |

---

## 1. Tool-call request: MCP server → Vault

`POST http://<vault-host>:8000/v1/tool-call`

| Field | Type | Notes |
|---|---|---|
| `trace_id` | string (UUID v4) | Created by the MCP server for each user request |
| `agent_id` | string | e.g. `"crypto-agent"` |
| `session_token` | string | The agent's secret token (see open point 3) |
| `tool` | string | One of the tools below |
| `args` | object | Depends on the tool. **Unknown fields are rejected.** |

```json
{
  "trace_id": "6f1c2a9e-3b7d-4c1e-9a52-0d8e7f4b1c23",
  "agent_id": "crypto-agent",
  "session_token": "<from .env, never committed>",
  "tool": "propose_transfer",
  "args": {
    "asset": "ETH",
    "amount": "0.05",
    "recipient": "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
  }
}
```

### Tool catalog

| Tool | `args` | Gates that run | `result` when allowed |
|---|---|---|---|
| `get_balance` | `{"asset": "ETH"}` | 1–2 | Whatever the PHE service returns (open point 1). Refused with `EXECUTION_FAILED` until PHE is connected. |
| `propose_transfer` | `{"asset", "amount", "recipient"}` | 1–6 | `{"tx_hash": "0x…", "status": "submitted" \| "submit_failed"}` |
| `get_transaction_status` | `{"tx_hash": "0x…"}` | 1–2 | `{"tx_hash", "status": "pending" \| "confirmed" \| "failed" \| "not_found"}` |
| `get_policy_status` | `{}` | 1–2 | `{"agent_status", "allowed_tools", "allowed_assets", "allowed_recipients", "limits", "spent_last_24h"}` |
| `export_private_key` | `{}` | always denied with `KEY_EXPORT_DENIED` | — (exists only so the attack demo can send it) |

Any other tool name is denied with `TOOL_NOT_PERMITTED`.

**For Member 2:** the MCP server must not approve, deny or execute anything. It forwards every call here and returns the response to the agent unchanged.

---

## 2. Tool-call response: Vault → MCP server

The response is **always HTTP 200 with a `decision`**, including denials and malformed requests. Any other status means the Vault itself is broken (500) or one of its dependencies is down (503). Treat that as a denial.

| Field | Type | Notes |
|---|---|---|
| `trace_id` | string | Copied from the request |
| `decision` | `"ALLOW"` \| `"DENY"` | |
| `gate` | string \| null | The gate that denied it (`null` on ALLOW) |
| `gate_number` | int \| null | 0–6, see the table below |
| `reason_code` | string \| null | Stable machine code, see below |
| `reason` | string \| null | Plain-English explanation |
| `checks` | list | Every gate that ran, in order: `{gate, passed, detail}` |
| `result` | object \| null | Present only on ALLOW, shape depends on the tool |

```json
{
  "trace_id": "6f1c2a9e-3b7d-4c1e-9a52-0d8e7f4b1c23",
  "decision": "DENY",
  "gate": "limits",
  "gate_number": 6,
  "reason_code": "LIMIT_PER_TX",
  "reason": "5.000042 ETH including gas is over the 0.1 ETH per-transaction limit",
  "checks": [
    {"gate": "request_format",      "passed": true,  "detail": null},
    {"gate": "identity",            "passed": true,  "detail": "crypto-agent is active"},
    {"gate": "permitted_tools",     "passed": true,  "detail": "propose_transfer is allowed"},
    {"gate": "allowed_assets",      "passed": true,  "detail": "ETH is approved"},
    {"gate": "recipient_allowlist", "passed": true,  "detail": "Alice (0x7099…79C8) is approved"},
    {"gate": "balance",             "passed": true,  "detail": "5.000042 ETH needed, 9.98 ETH available"},
    {"gate": "limits",              "passed": false, "detail": "5.000042 ETH > 0.1 ETH per transaction"}
  ],
  "result": null
}
```

### Gates

The first gate that fails stops the request. Nothing is built or signed until all six pass. Limits count the **true cost** (amount + gas).

| `gate_number` | `gate` | Label for the dashboard |
|---|---|---|
| 0 | `request_format` | Request format |
| 1 | `identity` | Identity & status |
| 2 | `permitted_tools` | Permitted tools |
| 3 | `allowed_assets` | Allowed assets |
| 4 | `recipient_allowlist` | Recipient allow-list |
| 5 | `balance` | Amount + gas ≤ balance |
| 6 | `limits` | Per-tx & daily limits |

### Reason codes

| `reason_code` | Gate | Severity | Example `reason` |
|---|---|---|---|
| `INVALID_REQUEST` | 0 | warning | Request rejected: field 'skip_checks' is not allowed |
| `POLICY_INVALID` | 1 | critical | The policy file has an error, so every request is refused until it is fixed |
| `AGENT_NOT_AUTHENTICATED` | 1 | critical | The agent could not be authenticated (an unknown agent and a wrong token get this same answer, so callers can't probe for agent names) |
| `AGENT_SUSPENDED` | 1 | critical | Agent crypto-agent is suspended, so all its requests are refused |
| `TOOL_NOT_PERMITTED` | 2 | warning | Tool 'deploy_contract' is not allowed for this agent |
| `KEY_EXPORT_DENIED` | 2 | critical | The agent asked for the private key. Keys never leave the Vault. |
| `ASSET_NOT_ALLOWED` | 3 | warning | BTC is not an approved asset (allowed: ETH) |
| `RECIPIENT_NOT_ALLOWED` | 4 | warning | 0x0000…dEaD is not on the approved recipient list |
| `INSUFFICIENT_BALANCE` | 5 | warning | Needs 12.000042 ETH including gas, balance is 9.98 ETH |
| `LIMIT_PER_TX` | 6 | warning | 5.000042 ETH including gas is over the 0.1 ETH per-transaction limit |
| `LIMIT_DAILY` | 6 | warning | Would bring the last 24 hours to 0.500042 ETH, over the 0.5 ETH daily limit |
| `GATE_ERROR` | any | critical | Allowed assets could not be checked, so the request was refused |
| `EXECUTION_FAILED` | — | critical | All gates passed, but the action could not be completed |

ALLOW decisions have severity `info`. `POLICY_INVALID`, `GATE_ERROR` and `EXECUTION_FAILED` mean the Vault itself had a problem. The request is still refused, and the event's `type` is `error`.

---

## 3. Decision events: Vault → Dashboard

`ws://<vault-host>:8000/ws/events`: the Vault pushes one JSON message for **every** decision, allow and deny, in order.
`GET http://<vault-host>:8000/v1/events?limit=50` returns the last 50 events, oldest first. Use it to backfill when the dashboard connects or reconnects.

| Field | Type | Notes |
|---|---|---|
| `event_id` | string (UUID v4) | |
| `seq` | int | Goes up by exactly 1 per event. A gap means you missed some, so refetch `/v1/events`. |
| `type` | `"decision"` \| `"error"` | `error` = the Vault had a problem (broken policy file, a gate or the action failed), and the request was refused |
| `timestamp` | string | |
| `trace_id` | string | Groups everything from one user request |
| `agent_id`, `tool`, `args` | | What was asked. When the request couldn't be read, `agent_id` and `tool` are null and `args` is `{}`. |
| `decision` | `"ALLOW"` \| `"DENY"` | Green / red |
| `gate`, `gate_number`, `gate_label` | | Which gate decided it (see the gates table) |
| `reason_code`, `reason` | | `reason` is written for a non-technical audience, so show it as-is |
| `severity` | `"info"` \| `"warning"` \| `"critical"` | Make `critical` loud |
| `checks` | list | Same as in the response, so you can draw the gate chain |
| `tx_hash` | string \| null | Set when a transfer was signed |
| `tx_status` | `"submitted"` \| `"submit_failed"` \| null | |
| `audit_hash` | string | This event's link in the tamper-evident audit log |

```json
{
  "event_id": "b1e0c6d2-5f4a-4b8e-9c3d-2a7f1e6b9d04",
  "seq": 4,
  "type": "decision",
  "timestamp": "2026-10-13T10:16:02.481Z",
  "trace_id": "d4a7e9b1-6c2f-4e8a-b3d5-7f1c9e2a4b60",
  "agent_id": "crypto-agent",
  "tool": "export_private_key",
  "args": {},
  "decision": "DENY",
  "gate": "permitted_tools",
  "gate_number": 2,
  "gate_label": "Permitted tools",
  "reason_code": "KEY_EXPORT_DENIED",
  "reason": "The agent asked for the private key. Keys never leave the Vault.",
  "severity": "critical",
  "checks": [
    {"gate": "request_format",  "passed": true,  "detail": null},
    {"gate": "identity",        "passed": true,  "detail": "crypto-agent is active"},
    {"gate": "permitted_tools", "passed": false, "detail": "export_private_key is a restricted tool"}
  ],
  "tx_hash": null,
  "tx_status": null,
  "audit_hash": "5c0e…"
}
```

`examples/demo_events.json` holds one event for each of the four demo scenarios (PASS, BLOCKED amount, BLOCKED asset, DENIED key), so the dashboard can be built before the Vault is live.

**For Member 4:** on-chain confirmation (pending → confirmed) comes from your receipt polling. Match it to these events on `tx_hash`.

---

## 4. Signed transaction hand-off: Vault → Blockchain submitter

**Proposal:** once all six gates pass, the Vault builds the complete transaction itself (nonce, gas, gas price, chain id), has HashiCorp Vault sign it, and sends the **fully signed raw transaction** to Member 4's submitter.

`POST http://<chain-host>:8100/v1/submit`

```json
{
  "trace_id": "6f1c2a9e-3b7d-4c1e-9a52-0d8e7f4b1c23",
  "raw_tx": "0xf86c8085…",
  "tx_hash": "0xf8325ba380b2ab181ea9ae9de56a4fc06c2a12e30019c01e1fd21be8c4c29b89",
  "from": "0x148499F121c87d23017dEEB94D8d3cC95Cb98E82",
  "to": "0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
  "value_wei": "50000000000000000",
  "chain_id": 31337
}
```

The submitter calls `w3.eth.send_raw_transaction(raw_tx)` and answers right away with `202 {"tx_hash": "0x…", "status": "submitted"}`. Receipt polling happens afterwards, on your side. Any non-2xx answer, or no answer within 5 seconds, is reported to the agent and the dashboard as `submit_failed`. The transfer still counts toward the agent's limits, because it was signed.

Until your submitter is ready, the Vault can send the transaction to Anvil itself. That only happens when `SUBMITTER_URL` is not set, and is meant for testing the Vault on its own.

Why a fully signed transaction rather than a bare signature:
1. The transaction the gates checked is exactly the one that gets signed. Nothing can change the amount or recipient after approval.
2. The submitter needs no signing code, only `send_raw_transaction`.
3. The Vault's signing plugin already produces this format. It was tested on Oct 7: a Vault-signed transfer was mined on Anvil.

The transactions are legacy (EIP-155) type, which Anvil accepts.

---

## Stub mode (until the real Vault, Week 6)

Start it with `STUB_MODE=1` (see the README). It speaks exactly the formats above, with these differences:

- The request format is fully enforced (gate 0), so malformed calls are denied just as they will be later.
- Gates 1–6 are placeholders that pass, and each check's `detail` says `not enforced (stub mode)`.
- **Key export is always denied**, even in stub mode, and unknown tools are denied.
- Results have the real shape plus `"stub": true`, and their values are placeholders. Transfers are approved but never signed or sent: `{"tx_hash": null, "status": "stub", "stub": true}`.
- ALLOW events say "Allowed in stub mode. The gates are not enforced yet."

The endpoints and fields are browsable at `http://<vault-host>:8000/docs`.

---

## 5. Open points to settle this week

1. **`get_balance` and PHE (Members 1, 2).** Proposal: `get_balance` comes through the Vault like every other tool. After gates 1–2, the Vault calls the PHE service and passes its answer back unchanged. What the agent is allowed to see (a final authorised total, never raw ciphertext) is decided by Members 1 and 2. Member 1: please confirm PHE runs as its own FastAPI service, and send the endpoint and request shape.
2. **USDC (Member 4).** ETH transfers are fully supported. USDC needs an ERC-20 token on Anvil. Either (a) you deploy a mock USDC and send me its address and decimals, or (b) we stay ETH-only for the 70% scope and USDC is removed from the allowed assets.
3. **Session token (Member 2).** For the 70% scope, the agent gets one random token that lives in the MCP server's `.env`. The Vault stores only its SHA-256 hash. I'll generate it and send it privately. It never goes into git.
4. **Integration machine (all).** Which laptop runs the full stack in Weeks 7–9? Every service listens on `0.0.0.0` and we connect over the LAN IP.

## My hand-off dates

| When | What you get |
|---|---|
| Oct 13 (end of Week 1) | This document, signed off |
| Oct 27 (end of Week 3) | **Stub Vault**: same formats, always ALLOW, runs without HashiCorp Vault (`STUB_MODE=1`), from branch `member3/vault-gateway` |
| Nov 17 (Week 6) | **Real Vault**: all six gates enforced, real signing |

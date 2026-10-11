# Member 2: reply on CONTRACTS.md

Sections 1 and 2 (tool-call request and response): **signed off**, with the notes below.
Built and tested against the real gateway on `member3/vault-gateway`. All 7 demo scenarios give the expected gate.

## Notes for Member 3 (Vault)

1. **trace_id is shared across calls for one user request.** The agent makes one trace id per user request and passes it to the MCP server in the call's `_meta` (`secureagent/trace_id`). If it is missing, the MCP server makes a fresh one per call. So the dashboard can show 2 events with the same `trace_id` for "Send 0.05 ETH to Alice": a `get_policy_status` (to find Alice's address), then the `propose_transfer`. This matches "groups everything from one user request" in section 3. Nothing in the gateway needs to change: `trace_id` isn't unique in the store.
2. **The agent can make up to 2 read-only calls before a transfer, and always at most one transfer per request.** Reason: names like "Alice" are only in `get_policy_status`.
3. **Denials made by the MCP server are not Vault decisions.** If the Vault is down, answers with a non-200, or sends an answer that isn't for this request, the agent gets a DENY with `reason_code` `VAULT_UNREACHABLE`, `VAULT_NO_ANSWER`, `VAULT_ERROR` or `VAULT_BAD_RESPONSE`, and `"source": "mcp_server"`. These won't appear on the dashboard because the Vault never saw them (or never finished). They are not in your reason-code table on purpose.
4. **We never retry a call.** If the answer to a transfer is lost, the transfer may still have been signed. The `spent_last_24h` in `get_policy_status` and the dashboard are how to check.
5. **Session token (open point 3): fine as proposed.** It lives only in the MCP server's `.env`. Please send it privately.
6. **`export_private_key` is only listed to the LLM when `EXPOSE_ATTACK_DEMO_TOOLS=1`.** If it is called anyway, it is still forwarded to you and denied.
7. **get_balance today**: your Vault answers `EXECUTION_FAILED`. The agent handles that. When PHE is connected, nothing changes on my side as long as the answer is `{"asset": "ETH", "balance": "9.98"}` (see below).

## For Members 1 and 3: what the agent may see (open point 1)

Proposal: `get_balance` returns only
```json
{"asset": "ETH", "balance": "9.98"}
```
a plain decimal string. No ciphertext, no history, no per-transaction values. This is the shape your stub already returns, Member 3. The agent has a second lock for it: anything else is withheld before the LLM reads it. Member 1: please confirm the PHE endpoint and that its decrypted total can be this shape. If you need a different shape, tell me and I change one function (`secureagent_agent/guard.py`).

## For Member 4

Nothing needed from the MCP side. The agent only sees the `tx_hash` and `status` the Vault returns. On USDC: the tool schema takes any asset symbol, so either answer (mock USDC or ETH-only) works for me with no change.

## For everyone: repo

The repo has no `main` branch yet. Someone needs to create it and make it the default (GitHub: Settings > Branches > Default branch):
```
git checkout --orphan main     # or: git checkout -b main from an agreed commit
git commit --allow-empty -m "Start main"
git push -u origin main
```
Then merge `member3/vault-gateway` and `member2/mcp-agent` into it. The integration tests find the gateway at `../vault_gateway`, which works once both folders are in the same checkout.

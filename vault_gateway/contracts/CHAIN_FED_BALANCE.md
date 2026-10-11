# Chain-fed balances (option 2 implemented)

## Status

Today (`2026-10-11`), `get_balance` returns a **demo balance**: a script
(`phe_service/seed_balance.py`) encrypts a fixed number and stores it with the PHE
service; the Vault's `get_balance` asks the PHE service for it. Nothing is read from
the chain for this tool, and gate 5 (amount + gas <= balance) reads a *different*,
real on-chain balance for transfers. This document records how to make the PHE
balance chain-fed later. **Implemented live: yes, option 2** — `phe_service/chain_sync.py` reads the signer's Anvil balance, encrypts it with the PHE public key and POSTs `/balance/set` every 3 s. Options 1 and 3 remain proposals.

## Goal

Make the balance the agent sees *actually be* the Vault account's on-chain balance,
while keeping the privacy property these components enforce: the LLM sees only a
final plain decimal total, never raw ciphertext, per-account history, or candle data.

## Where the pieces sit

- Agent-side lock: `mcp_agent/secureagent_agent/guard.py` — withholds anything that
  is not `{"asset", "balance": "<decimal string>"}`.
- Vault: `vault_gateway/app/phe.py` (`PheClient`) → PHE HTTP endpoint
  `POST /balance/query`.
- PHE service: `phe_service/balance_store.py` — holds a Paillier ciphertext per
  `(requester_id, asset)`, decrypts for an authorized requester.

## The hard part

PHE can only add ciphertexts it is given. To be chain-fed without revealing
plaintext, something must produce the encrypted balance. Options:

1. **Vault feeds ciphertext, never decrypts.** The Vault already has a read-only
   chain connection (`app/chain.py`). It would need a chain client that can also
   *encrypt* with the PHE public key and POST `/balance/set`. PHE adds any number of
   such ciphertexts; `/balance/query` decrypts the sum. The Vault would still never
   see the decrypted total, so the privacy boundary holds *against the Vault too* —
   but gate 5 still needs a plaintext balance, so the Vault must decrypt *something*.
   Cleanest split: gate 5 reads the chain directly (today's behaviour); only the
   `get_balance` *tool* answer goes through PHE, and it can be PHE's decrypted
   aggregate. If the aggregate must equal the gate-5 number, that equality is a
   testable invariant, not an automatic property.

2. **An independent "balance provider" encrypts.** A separate watcher reads the
   chain every N blocks, encrypts the balance with the PHE public key, and stores
   it via `/balance/set`. The Vault never has the private half. Cost: a new
   component, staleness windows, and its own secret management.

3. **Aggregate, not mirror.** The intended PHE use case is aggregating balances
   across accounts/assets without revealing any one of them (e.g. "total holdings").
   PHE's `/balance/set` already accepts *multiple* ciphertexts per key if extended,
   and `/add` can sum them. The chain-fed milestone should decide whether
   `get_balance` reports a single account or a homomorphic aggregate.

## Recommendations / constraints

- Keep `get_balance` **fail-closed**: no PHE answer means `EXECUTION_FAILED`, never
  a guessed number (`app/phe.py`, `app/actions.py:get_balance`).
- Keep the **shape frozen**: `{"asset", "balance"}` with a plain decimal string, so
  `guard.py` and the MCP server need no change.
- Keep ciphertext and history **off the wire to the agent**: the Vault passes PHE's
  JSON through unchanged; any sanitising happens in PHE and in `guard.py`.
- Decide and document what "balance" means before implementation: per-account wei,
  or homomorphic aggregate? This determines who encrypts and how often.

## Open questions for the chain-fed milestone

1. Who holds the PHE private key, and who may call `/decrypt`/`/balance/query`?
2. Should `get_balance` mirror the gate-5 number, or be an independent aggregate?
3. Encryption cadence (per block, per request, periodic) and staleness handling.
4. Requester-ID authentication replacement (currently a constant `user123`).
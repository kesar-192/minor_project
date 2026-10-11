# Member 4 - Dashboard + Blockchain submitter

## Run order
1. `anvil` (port 8545)
2. Vault gateway on :8000 with `SUBMITTER_URL=http://localhost:8100`
3. Submitter:  `cd submitter && pip install -r requirements.txt && uvicorn main:app --host 0.0.0.0 --port 8100`
4. Dashboard:  `cd dashboard && cp .env.example .env && npm install && npm run dev`  -> http://localhost:5173

Over the LAN set `VITE_VAULT_HOST=<laptop-ip>:8000`, `VITE_SUBMITTER_URL=http://<laptop-ip>:8100`,
and add the dashboard origin to the Vault's `CORS_ORIGINS`.

## Decisions
- Asset scope: ETH-only for the 70% scope (see chat notes).
- Submitter answers `202 {"tx_hash","status":"submitted"}`; receipts tracked in background
  (`GET /v1/tx/{hash}` -> pending | confirmed | failed | not_found).

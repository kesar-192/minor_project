# SecureAgent Dashboard (UI)

React + Vite command-center UI for the SecureAgent pipeline (SOC / Web3 dark theme).

## Run
```bash
npm install
npm run dev        # http://localhost:5173
npm run build      # production build in dist/
```
Or start the whole stack from the repo root with `./run.sh` (it launches this dashboard on :5173).

## How it talks to the backend
The browser only calls this dev server; `vite.config.js` proxies to the services:

| Path | Service | Port |
|---|---|---|
| `/api/vault/*` (+ WebSocket `/ws/events`) | Vault gateway | 8000 |
| `/api/submitter/*` | Blockchain submitter | 8100 |
| `/api/agent/*` | MCP agent chat API | 8500 |
| `/api/phe/*` | PHE service (health only) | 8300 |
| `/api/rpc` | Anvil JSON-RPC | 8545 |

Restart the dev server after changing `vite.config.js`.

## Structure
```
index.html
package.json, vite.config.js
src/
  main.jsx    entry
  App.jsx     hooks (useVault, useWallet, useHealth), components, pages
  styles.css  design tokens + components + responsive rules
```
Pages: Overview (command center), Agent Chat, Trading (simulated prices), Analytics, Submitter Logs, Account Security.

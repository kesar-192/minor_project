import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The browser only ever talks to this dev server (port 5173). It forwards /api/* to the
// backend services on localhost, so the dashboard works from any machine (VM, SSH tunnel, LAN)
// without exposing or hard-coding the other ports.
const to = (target, ws = false) => ({ target, ws, changeOrigin: true });
export default defineConfig({
  plugins: [react()],
  server: {
    allowedHosts: true,
    proxy: {
      "^/api/vault/": { ...to("http://127.0.0.1:8000", true), rewrite: (p) => p.replace(/^\/api\/vault/, "") },
      "^/api/submitter/": { ...to("http://127.0.0.1:8100"), rewrite: (p) => p.replace(/^\/api\/submitter/, "") },
      "^/api/agent/": { ...to("http://127.0.0.1:8500"), rewrite: (p) => p.replace(/^\/api\/agent/, "") },
      // Only the two read-only demo routes of the PHE service, with its token added here (never in the browser).
      "^/api/phe/": { ...to("http://127.0.0.1:8300"), rewrite: (p) => p.replace(/^\/api\/phe/, "/demo"), headers: { "x-api-token": process.env.PHE_API_TOKEN || "" } },
      "^/api/rpc$": { ...to("http://127.0.0.1:8545"), rewrite: () => "/" },
    },
  },
});

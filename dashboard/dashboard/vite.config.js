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
      "^/api/rpc$": { ...to("http://127.0.0.1:8545"), rewrite: () => "/" },
    },
  },
});

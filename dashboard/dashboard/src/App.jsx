import { useEffect, useRef, useState, useCallback } from "react";

const HOST = import.meta.env.VITE_VAULT_HOST || "localhost:8000";
const SUBMITTER = import.meta.env.VITE_SUBMITTER_URL || "http://localhost:8100";
const MAX_EVENTS = 200;

export default function App() {
  const [events, setEvents] = useState([]);
  const [conn, setConn] = useState("connecting");
  const [txs, setTxs] = useState({}); // tx_hash(lowercase) -> on-chain status
  const [open, setOpen] = useState(null);
  const lastSeq = useRef(0);

  // Merge by seq: dedupes backfill vs live and keeps order.
  const merge = useCallback((incoming) => {
    setEvents((prev) => {
      const map = new Map(prev.map((e) => [e.seq, e]));
      incoming.forEach((e) => map.set(e.seq, e));
      const all = [...map.values()].sort((a, b) => a.seq - b.seq).slice(-MAX_EVENTS);
      lastSeq.current = all.length ? all[all.length - 1].seq : 0;
      return all;
    });
  }, []);

  const backfill = useCallback(async () => {
    try {
      const r = await fetch(`http://${HOST}/v1/events?limit=50`);
      merge(await r.json());
    } catch { /* WS will retry */ }
  }, [merge]);

  useEffect(() => {
    let ws, retry = 0, stop = false, timer;
    const connect = () => {
      setConn("connecting");
      ws = new WebSocket(`ws://${HOST}/ws/events`);
      ws.onopen = () => { retry = 0; setConn("live"); backfill(); };
      ws.onmessage = (m) => {
        const ev = JSON.parse(m.data);
        // seq must go up by exactly 1; a gap means missed events -> refetch.
        if (lastSeq.current && ev.seq > lastSeq.current + 1) backfill();
        merge([ev]);
      };
      ws.onclose = () => {
        if (stop) return;
        setConn("reconnecting");
        timer = setTimeout(connect, Math.min(1000 * 2 ** retry++, 10000));
      };
      ws.onerror = () => ws.close();
    };
    connect();
    return () => { stop = true; clearTimeout(timer); ws && ws.close(); };
  }, [backfill, merge]);

  // Receipt tracking: poll the submitter for every signed tx until it settles.
  useEffect(() => {
    const id = setInterval(async () => {
      const hashes = events.map((e) => e.tx_hash).filter(Boolean)
        .filter((h) => !["confirmed", "failed"].includes(txs[h.toLowerCase()]?.status));
      for (const h of new Set(hashes)) {
        try {
          const r = await (await fetch(`${SUBMITTER}/v1/tx/${h}`)).json();
          setTxs((t) => ({ ...t, [h.toLowerCase()]: r }));
        } catch { /* submitter offline */ }
      }
    }, 1500);
    return () => clearInterval(id);
  }, [events, txs]);

  const shown = [...events].reverse();
  const count = (d) => events.filter((e) => e.decision === d).length;

  return (
    <div className="page">
      <header>
        <div><h1>SecureAgent</h1><p className="motto">Capabilities, Not Access</p></div>
        <span className={`pill ${conn}`}>{conn}</span>
      </header>
      <div className="stats">
        <div className="stat pass">{count("ALLOW")}<small>PASS</small></div>
        <div className="stat block">{count("DENY")}<small>BLOCKED</small></div>
        <div className="stat crit">{events.filter((e) => e.severity === "critical").length}<small>CRITICAL</small></div>
      </div>
      {shown.length === 0 && <p className="empty">Waiting for the first decision from the Vault...</p>}
      {shown.map((e) => {
        const tx = e.tx_hash && txs[e.tx_hash.toLowerCase()];
        const status = tx?.status || (e.tx_status === "submit_failed" ? "failed" : e.tx_hash ? "pending" : null);
        return (
          <div key={e.seq} className={`card ${e.decision === "ALLOW" ? "pass" : "block"} ${e.severity}`}
               onClick={() => setOpen(open === e.seq ? null : e.seq)}>
            <div className="row">
              <span className="badge">{e.decision === "ALLOW" ? "PASS" : e.type === "error" ? "ERROR" : "BLOCKED"}</span>
              <b>{e.tool ?? "unreadable request"}</b>
              <span className="args">{Object.entries(e.args || {}).map(([k, v]) => `${k}=${v}`).join("  ")}</span>
              <time>{new Date(e.timestamp).toLocaleTimeString()}</time>
            </div>
            <div className="reason">
              {e.decision === "DENY" && <span className="gate">Gate {e.gate_number}: {e.gate_label}</span>}
              {e.reason ?? "All gates passed"}
            </div>
            {status && <span className={`tx ${status}`}>{status}{tx?.block_number != null ? ` - block ${tx.block_number}` : ""}
              <code>{e.tx_hash?.slice(0, 12)}...</code></span>}
            {open === e.seq && (
              <div className="detail">
                <div className="chain">{e.checks.map((c, i) => (
                  <span key={i} className={c.passed ? "ok" : "no"} title={c.detail || ""}>{c.gate}</span>))}</div>
                <small>agent {e.agent_id} | trace {e.trace_id} | seq {e.seq} | audit {e.audit_hash?.slice(0, 16)}...</small>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

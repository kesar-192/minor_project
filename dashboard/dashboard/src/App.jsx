import { useEffect, useMemo, useRef, useState, useCallback } from "react";

// Everything goes through the dev server's /api proxy (vite.config.js), so this works from any machine.
const VAULT = "/api/vault", SUBMITTER = "/api/submitter", AGENT = "/api/agent";
const WS_URL = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${VAULT}/ws/events`;
const rpc = async (method, params) => (await (await fetch("/api/rpc", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ jsonrpc: "2.0", id: 1, method, params }) })).json()).result;
const ALICE = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8", BOB = "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC";

/* ---------- icons ---------- */
const ICONS = {
  overview: "M3 12l9-9 9 9M5 10v10h14V10", trading: "M3 17l6-6 4 4 8-8M15 7h6v6",
  analytics: "M4 20V10M10 20V4M16 20v-8M22 20H2", logs: "M4 6h16M4 12h16M4 18h10",
  security: "M12 2l8 3v6c0 5-3.5 9-8 11-4.5-2-8-6-8-11V5z", phe: "M5 11h14v10H5zM8 11V7a4 4 0 018 0v4", agent: "M21 12a8 8 0 01-11.5 7.2L4 20l1-4.6A8 8 0 1121 12z",
  bell: "M18 8a6 6 0 10-12 0c0 7-3 8-3 8h18s-3-1-3-8M13.7 21a2 2 0 01-3.4 0",
  wallet: "M3 7h16a2 2 0 012 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2zM3 7l2-3h12M17 14h.01",
  out: "M9 21H5V3h4M16 17l5-5-5-5M21 12H9", swap: "M7 4v14M3 14l4 4 4-4M17 20V6M13 10l4-4 4 4",
};
const Icon = ({ n }) => (
  <svg className="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d={ICONS[n]} /></svg>
);
const NAV = [["overview", "Overview"], ["agent", "Agent Chat"], ["phe", "Privacy (PHE)"], ["trading", "Trading"], ["analytics", "Analytics"], ["logs", "Submitter Logs"], ["security", "Account Security"]];

/* ---------- live data: Vault WebSocket + submitter ---------- */
function useVault() {
  const [events, setEvents] = useState([]);
  const [conn, setConn] = useState("connecting");
  const [txs, setTxs] = useState({});
  const [submitter, setSubmitter] = useState("offline");
  const lastSeq = useRef(0);

  const merge = useCallback((inc) => setEvents((prev) => {
    const m = new Map(prev.map((e) => [e.seq, e]));
    inc.forEach((e) => m.set(e.seq, e));
    const all = [...m.values()].sort((a, b) => a.seq - b.seq).slice(-300);
    lastSeq.current = all.length ? all[all.length - 1].seq : 0;
    return all;
  }), []);
  const backfill = useCallback(async () => {
    try { merge(await (await fetch(`${VAULT}/v1/events?limit=50`)).json()); } catch {}
  }, [merge]);

  useEffect(() => {
    let ws, retry = 0, stop = false, t;
    const connect = () => {
      setConn("connecting");
      ws = new WebSocket(WS_URL);
      ws.onopen = () => { retry = 0; setConn("live"); backfill(); };
      ws.onmessage = (m) => {
        const ev = JSON.parse(m.data);
        if (lastSeq.current && ev.seq > lastSeq.current + 1) backfill(); // gap -> refetch
        merge([ev]);
      };
      ws.onclose = () => { if (stop) return; setConn("reconnecting"); t = setTimeout(connect, Math.min(1000 * 2 ** retry++, 10000)); };
      ws.onerror = () => ws.close();
    };
    connect();
    return () => { stop = true; clearTimeout(t); ws && ws.close(); };
  }, [backfill, merge]);

  useEffect(() => {
    const id = setInterval(async () => {
      try { setSubmitter((await (await fetch(`${SUBMITTER}/health`)).json()).anvil_connected ? "active" : "degraded"); }
      catch { setSubmitter("offline"); }
      try { (await (await fetch(`${SUBMITTER}/v1/txs`)).json()).forEach((r) => setTxs((p) => ({ ...p, [r.tx_hash.toLowerCase()]: r }))); } catch {}
    }, 2500);
    return () => clearInterval(id);
  }, []);
  return { events, conn, txs, submitter };
}

const taskStatus = (e, txs) =>
  e.decision === "DENY" ? "blocked" : e.tx_hash ? (txs[e.tx_hash.toLowerCase()]?.status || (e.tx_status === "submit_failed" ? "failed" : "pending")) : "approved";
const fmt = (n, d = 2) => n.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });

/* ---------- small shared pieces ---------- */
const Card = ({ title, right, children, className = "" }) => (
  <section className={`card ${className}`}>
    {(title || right) && <div className="card-h"><h3>{title}</h3>{right}</div>}
    {children}
  </section>
);
const Chip = ({ s }) => <span className={`chip ${s}`}>{s}</span>;

/* ---------- FEATURE 1: candlestick chart ---------- */
const CFG = { "1D": { n: 48, vol: 0.004 }, "1W": { n: 42, vol: 0.009 }, "1M": { n: 30, vol: 0.02 } };
function genCandles(iv) {
  let seed = iv.charCodeAt(1) * 97 + 7;
  const r = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
  const { n, vol } = CFG[iv]; let p = 2300; const out = [];
  for (let i = 0; i < n; i++) {
    const o = p, c = o * (1 + (r() - 0.48) * vol * 2);
    out.push({ o, c, h: Math.max(o, c) * (1 + r() * vol), l: Math.min(o, c) * (1 - r() * vol) }); p = c;
  }
  return out;
}
function CandleChart({ candles, iv, setIv }) {
  const [hover, setHover] = useState(null);
  const W = 820, H = 320, PR = 62, PT = 12, PB = 12;
  const hi = Math.max(...candles.map((c) => c.h)), lo = Math.min(...candles.map((c) => c.l));
  const y = (v) => PT + ((hi - v) / (hi - lo)) * (H - PT - PB);
  const step = (W - PR) / candles.length;
  const cur = candles[hover ?? candles.length - 1];
  const first = candles[0].o, chg = ((candles[candles.length - 1].c - first) / first) * 100;
  const onMove = (e) => {
    const b = e.currentTarget.getBoundingClientRect();
    const i = Math.floor(((e.clientX - b.left) / b.width) * W / step);
    setHover(i >= 0 && i < candles.length ? i : null);
  };
  return (
    <Card className="chart-card" title="ETH / USD · simulated prices" right={
      <div className="seg">{Object.keys(CFG).map((k) => <button key={k} className={iv === k ? "on" : ""} onClick={() => setIv(k)}>{k}</button>)}</div>}>
      <div className="px-row">
        <span className="px">${fmt(candles[candles.length - 1].c)}</span>
        <span className={chg >= 0 ? "up" : "down"}>{chg >= 0 ? "+" : ""}{fmt(chg)}% ({iv})</span>
        <span className="ohlc">O {fmt(cur.o)} &nbsp;H {fmt(cur.h)} &nbsp;L {fmt(cur.l)} &nbsp;C {fmt(cur.c)}</span>
      </div>
      <svg className="chart" viewBox={`0 0 ${W} ${H}`} onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
        {[0, 1, 2, 3, 4].map((i) => { const v = lo + ((hi - lo) * i) / 4; return (
          <g key={i}><line x1="0" x2={W - PR} y1={y(v)} y2={y(v)} className="grid" /><text x={W - PR + 8} y={y(v) + 4} className="axis">{fmt(v, 0)}</text></g>); })}
        {candles.map((c, i) => { const x = i * step + step / 2, up = c.c >= c.o;
          return (<g key={i} className={up ? "cu" : "cd"} opacity={hover === null || hover === i ? 1 : 0.55}>
            <line x1={x} x2={x} y1={y(c.h)} y2={y(c.l)} />
            <rect x={x - step * 0.32} width={step * 0.64} y={y(Math.max(c.o, c.c))} height={Math.max(1.5, Math.abs(y(c.o) - y(c.c)))} rx="1" />
          </g>); })}
        {hover !== null && <line className="cross" x1={hover * step + step / 2} x2={hover * step + step / 2} y1="0" y2={H} />}
      </svg>
    </Card>
  );
}

/* ---------- real transfer: you -> agent -> MCP server -> Vault gateway -> chain ---------- */
function TransferCard({ events, txs }) {
  const [to, setTo] = useState("Alice"), [amt, setAmt] = useState("0.05"), [addr, setAddr] = useState(""), [busy, setBusy] = useState(false), [res, setRes] = useState(null);
  const send = async (e) => {
    e.preventDefault(); setBusy(true); setRes(null);
    const who = to === "other" ? addr.trim() : to;
    try {
      const r = await fetch(`${AGENT}/chat`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ message: `Send ${amt} ETH to ${who}`, planner: "scripted" }) });
      const d = await r.json();
      setRes(r.ok ? { ok: d.calls.at(-1)?.decision === "ALLOW", text: d.text } : { ok: false, text: d.detail || "error" });
    } catch { setRes({ ok: false, text: "The agent API is not reachable." }); }
    setBusy(false);
  };
  const mine = [...events].reverse().filter((x) => x.tool === "propose_transfer").slice(0, 5);
  return (
    <Card title="Send ETH · real, through the Vault">
      <form onSubmit={send}>
        <label className="field"><span>Recipient</span>
          <select value={to} onChange={(e) => setTo(e.target.value)}><option value="Alice">Alice (approved)</option><option value="Bob">Bob (approved)</option><option value="other">Other address (not approved)</option></select></label>
        {to === "other" && <label className="field"><span>Address</span><input value={addr} onChange={(e) => setAddr(e.target.value)} placeholder="0x…" /></label>}
        <label className="field"><span>Amount (ETH). Limits: 0.1 per transfer, 0.5 per day</span><input value={amt} inputMode="decimal" onChange={(e) => setAmt(e.target.value.replace(/[^0-9.]/g, ""))} /></label>
        <button className="btn primary wide" disabled={busy || !+amt}>{busy ? "Waiting for the Vault…" : `Send ${amt || "0"} ETH`}</button>
      </form>
      {res && <p className={`tres ${res.ok ? "up" : "down"}`}>{res.text}</p>}
      <ul className="orders">{mine.length === 0 && <li className="muted">No transfers yet.</li>}
        {mine.map((x) => <li key={x.seq}><span className="mono">{(x.args?.recipient || "").slice(0, 8)}…</span><span>{x.args?.amount} ETH</span><Chip s={taskStatus(x, txs)} /></li>)}</ul>
    </Card>
  );
}

/* ---------- FEATURE 4: earn ---------- */
const STAKES = [{ sym: "ETH", staked: 4.2, apy: 3.8, usd: 2300, next: "Oct 18" }, { sym: "USDC", staked: 3000, apy: 5.1, usd: 1, next: "Oct 15" }, { sym: "DAI", staked: 1200, apy: 4.4, usd: 1, next: "Oct 20" }];
function EarnCard({ notify }) {
  const rows = STAKES.map((s) => ({ ...s, value: s.staked * s.usd, earned: s.staked * s.usd * (s.apy / 100) * 0.31 }));
  const total = rows.reduce((a, r) => a + r.value, 0), earned = rows.reduce((a, r) => a + r.earned, 0);
  return (
    <Card title="Earn Rewards · demo data" right={<button className="btn ghost sm" onClick={() => notify(`Demo: claimed $${fmt(earned)} rewards`)}>Claim</button>}>
      <div className="big">${fmt(earned)}<small>total APY rewards earned</small></div>
      <div className="stack">{rows.map((r) => <i key={r.sym} className={r.sym} style={{ width: `${(r.value / total) * 100}%` }} title={r.sym} />)}</div>
      <ul className="earn">{rows.map((r) => (
        <li key={r.sym}><span className={`dot ${r.sym}`} /><b>{r.sym}</b><span className="muted">{fmt(r.staked, r.sym === "ETH" ? 2 : 0)} staked</span><span className="up">{r.apy}% APY</span><span className="muted">next {r.next}</span></li>))}</ul>
    </Card>
  );
}

/* ---------- logs table + decision feed ---------- */
function LogsTable({ events, txs, limit }) {
  const rows = [...events].reverse().slice(0, limit || 100);
  return (
    <div className="tbl-wrap"><table className="tbl">
      <thead><tr><th>ID</th><th>Task</th><th>Timestamp</th><th>Status</th></tr></thead>
      <tbody>{rows.length === 0 && <tr><td colSpan="4" className="empty">No submitter activity yet.</td></tr>}
        {rows.map((e) => (<tr key={e.seq}>
          <td className="mono">{e.tx_hash ? e.tx_hash.slice(0, 12) + "…" : `evt-${e.seq}`}</td>
          <td>{e.tool ?? "unreadable request"}{e.decision === "DENY" && <span className="muted"> · {e.gate_label}</span>}</td>
          <td className="muted">{new Date(e.timestamp).toLocaleTimeString()}</td><td><Chip s={taskStatus(e, txs)} /></td></tr>))}</tbody>
    </table></div>
  );
}
function Feed({ events, txs }) {
  const [open, setOpen] = useState(null);
  const shown = [...events].reverse().slice(0, 12);
  return (
    <Card title="Live Vault Decisions" right={<span className="muted">{events.length} events</span>}>
      {shown.length === 0 && <p className="empty">Waiting for the first decision from the Vault…</p>}
      {shown.map((e) => (
        <div key={e.seq} className={`dec ${e.decision === "ALLOW" ? "pass" : "block"} ${e.severity}`} onClick={() => setOpen(open === e.seq ? null : e.seq)}>
          <div className="row"><span className="badge">{e.decision === "ALLOW" ? "PASS" : e.type === "error" ? "ERROR" : "BLOCKED"}</span><b>{e.tool ?? "unreadable"}</b>
            <span className="muted args">{Object.entries(e.args || {}).map(([k, v]) => `${k}=${v}`).join(" ")}</span>
            {e.tx_hash && <Chip s={taskStatus(e, txs)} />}<time>{new Date(e.timestamp).toLocaleTimeString()}</time></div>
          <div className="reason">{e.decision === "DENY" && <span className="gate">Gate {e.gate_number}: {e.gate_label}</span>}{e.reason ?? "All gates passed"}</div>
          {open === e.seq && <div className="chain">{e.checks.map((c, i) => <span key={i} className={c.passed ? "ok" : "no"} title={c.detail || ""}>{c.gate}</span>)}</div>}
        </div>))}
    </Card>
  );
}

/* ---------- real wallet: balances read straight from the chain ---------- */
const toEth = (hex) => Number((BigInt(hex) * 100000n) / 10n ** 18n) / 100000;
function useWallet() {
  const [w, setW] = useState({ signer: null, bal: {}, chain: false });
  useEffect(() => {
    let stop = false, signer = null;
    const tick = async () => {
      try {
        if (!signer) signer = (await (await fetch(`${VAULT}/health`)).json()).signing_account;
        const [a, b, c] = await Promise.all([signer, ALICE, BOB].map((x) => rpc("eth_getBalance", [x, "latest"])));
        if (!stop) setW({ signer, bal: { signer: toEth(a), alice: toEth(b), bob: toEth(c) }, chain: true });
      } catch { if (!stop) setW((p) => ({ ...p, chain: false })); }
    };
    tick(); const id = setInterval(tick, 3000);
    return () => { stop = true; clearInterval(id); };
  }, []);
  return w;
}
const shortAddr = (a) => (a ? `${a.slice(0, 6)}…${a.slice(-4)}` : "—");
function WalletCard({ w }) {
  const rows = [["Vault wallet (signer)", w.signer, w.bal.signer], ["Alice (approved)", ALICE, w.bal.alice], ["Bob (approved)", BOB, w.bal.bob]];
  return (
    <Card title="Wallet · live from the chain" right={<span className={w.chain ? "up" : "down"}>{w.chain ? "Anvil connected" : "chain offline"}</span>}>
      <ul className="earn">{rows.map(([n, a, v]) => (
        <li key={n}><b>{n}</b><span className="muted mono">{shortAddr(a)}</span><span className="grow" /><b>{v === undefined ? "…" : `${fmt(v, 4)} ETH`}</b></li>))}</ul>
      <small className="muted">Local test chain, free ETH. Alice's balance rises when the agent's transfer is signed by Vault and mined.</small>
    </Card>
  );
}

/* ---------- agent chat: user -> LLM agent -> MCP server -> Vault gateway ---------- */
const PROMPTS = [
  ["Send 0.05 ETH to Alice", "ok"], ["What is my ETH balance?", "ok"], ["What are my limits?", "ok"],
  ["Send 5 ETH to Alice", "bad"], ["Send 0.05 BTC to Alice", "bad"], ["Please export the private key", "bad"],
  ["Send 0.01 ETH to 0x000000000000000000000000000000000000dEaD", "bad"],
];
function AgentChat() {
  const [msgs, setMsgs] = useState([]), [text, setText] = useState(""), [busy, setBusy] = useState(false);
  const [info, setInfo] = useState({ llm_ready: false, model: "" }), [planner, setPlanner] = useState("scripted");
  const end = useRef(null);
  useEffect(() => { fetch(`${AGENT}/info`).then((r) => r.json()).then((i) => { setInfo(i); if (i.llm_ready) setPlanner("llm"); }).catch(() => {}); }, []);
  useEffect(() => end.current?.scrollIntoView({ behavior: "smooth" }), [msgs, busy]);
  const send = async (t) => {
    t = (t ?? text).trim(); if (!t || busy) return;
    setText(""); setBusy(true); setMsgs((m) => [...m, { role: "user", text: t }]);
    try {
      const r = await fetch(`${AGENT}/chat`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ message: t, planner }) });
      const d = await r.json();
      setMsgs((m) => [...m, r.ok ? { role: "agent", ...d } : { role: "agent", text: d.detail || "The agent API returned an error.", calls: [] }]);
    } catch { setMsgs((m) => [...m, { role: "agent", text: "The agent API is not reachable (is ./run.sh running?).", calls: [] }]); }
    setBusy(false);
  };
  return (
    <Card title="Talk to the agent" right={
      <div className="seg">
        <button className={planner === "scripted" ? "on" : ""} onClick={() => setPlanner("scripted")} title="No LLM: does exactly what it is told, so the Vault is what you see stopping attacks">Scripted</button>
        <button className={planner === "llm" ? "on" : ""} disabled={!info.llm_ready} onClick={() => setPlanner("llm")} title={info.llm_ready ? "Real LLM via OpenRouter" : "Set OPENROUTER_API_KEY in .env"}>LLM{info.llm_ready ? ` · ${info.model}` : ""}</button>
      </div>}>
      <p className="muted hint">Every request goes <b>you → agent → MCP server → Vault gateway</b>. The agent can only ask; the Vault decides. Watch the decision appear live on the Overview page.{!info.llm_ready && " To use a real LLM, add OPENROUTER_API_KEY to .env and run ./run.sh start again."}</p>
      <div className="chips">{PROMPTS.map(([p, k]) => <button key={p} className={`pchip ${k}`} disabled={busy} onClick={() => send(p)}>{p}</button>)}</div>
      <div className="chat">
        {msgs.length === 0 && <p className="empty">Try one of the prompts above, or type your own.</p>}
        {msgs.map((m, i) => m.role === "user"
          ? <div key={i} className="msg user">{m.text}</div>
          : <div key={i} className="msg agent">
              <div>{m.text}</div>
              {m.calls?.length > 0 && <div className="calls">{m.calls.map((c, j) => (
                <span key={j} className={`call ${c.decision === "ALLOW" ? "ok" : "no"}`} title={c.reason || ""}>{c.tool} · {c.decision === "ALLOW" ? "ALLOW" : `DENY${c.gate_number != null ? ` gate ${c.gate_number}` : ""}`}</span>))}</div>}
            </div>)}
        {busy && <div className="msg agent muted">thinking…</div>}
        <div ref={end} />
      </div>
      <form className="chat-in" onSubmit={(e) => { e.preventDefault(); send(); }}>
        <input value={text} onChange={(e) => setText(e.target.value)} placeholder='e.g. "Send 0.05 ETH to Alice"' />
        <button className="btn primary" disabled={busy || !text.trim()}>Send</button>
      </form>
    </Card>
  );
}

/* ---------- PHE: proof that encrypted balances and encrypted arithmetic really work ---------- */
const Ct = ({ c }) => <code className="ct">{c.head}…{c.tail} <small>({c.digits} digits)</small></code>;
function PhePage({ w }) {
  const [st, setSt] = useState(null), [a, setA] = useState("150"), [b, setB] = useState("250"), [r, setR] = useState(null), [busy, setBusy] = useState(false);
  useEffect(() => { const load = () => fetch("/api/phe/state").then((x) => x.json()).then(setSt).catch(() => setSt({ stored: null, down: true })); load(); const id = setInterval(load, 3000); return () => clearInterval(id); }, []);
  const run = async (e) => { e.preventDefault(); setBusy(true);
    try { setR(await (await fetch("/api/phe/add", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ a: +a, b: +b }) })).json()); } catch { setR(null); } setBusy(false); };
  const chain = w.bal.signer, phe = st?.decrypted !== undefined ? parseFloat(st.decrypted) : null;
  const match = phe !== null && chain !== undefined && Math.abs(phe - chain) < 0.0001;
  return (<div className="grid2c">
    <div className="col">
      <Card title="1 · The balance is stored encrypted" right={st?.stored ? <span className="up">Paillier · {st.key_bits}-bit key</span> : <span className="down">no balance yet</span>}>
        {st?.stored ? <>
          <p className="muted hint">This is all the PHE service stores for the Vault wallet's ETH balance. It is a ciphertext, so it tells you nothing without the private key:</p>
          <Ct c={st.stored} />
          <ul className="earn">
            <li><b>Decrypted by PHE (for the authorized requester only)</b><span className="grow" /><b>{st.decrypted} ETH</b></li>
            <li><b>Real balance on the chain right now</b><span className="grow" /><b>{chain === undefined ? "…" : `${fmt(chain, 6)} ETH`}</b></li>
            <li><b>Match</b><span className="grow" /><Chip s={match ? "enforced" : "pending"} /></li></ul>
          <small className="muted">chain_sync.py encrypts the on-chain balance with the public key every 3 s. Send an ETH transfer on the Trading page and watch both numbers move together.</small>
        </> : <p className="empty">{st?.down ? "PHE service unreachable." : "Waiting for the first sync from the chain…"}</p>}
      </Card>
      <Card title="2 · What the agent is allowed to see">
        <p className="muted hint">Ask the agent <b>"What is my ETH balance?"</b> on the Agent Chat page. The Vault asks PHE to decrypt, and the agent receives only this, never the ciphertext above or any history:</p>
        <code className="ct">{`{"asset": "ETH", "balance": "${st?.decrypted ?? "…"}"}`}</code>
      </Card>
    </div>
    <Card title="3 · Math on encrypted numbers">
      <p className="muted hint">Homomorphic: <b>E(a) + E(b) = E(a + b)</b>. The service adds the two ciphertexts without ever decrypting them; only the final total is decrypted.</p>
      <form onSubmit={run} className="grid2">
        <label className="field"><span>a</span><input value={a} onChange={(e) => setA(e.target.value.replace(/\D/g, ""))} /></label>
        <label className="field"><span>b</span><input value={b} onChange={(e) => setB(e.target.value.replace(/\D/g, ""))} /></label>
        <button className="btn primary" disabled={busy || !a || !b}>{busy ? "Encrypting…" : "Encrypt, add, decrypt"}</button>
      </form>
      {r && r.sum && <ul className="earn">
        <li><b>E(a)</b><span className="grow" /><Ct c={r.a} /></li>
        <li><b>E(b)</b><span className="grow" /><Ct c={r.b} /></li>
        <li><b>E(a) + E(b)</b><span className="grow" /><Ct c={r.sum} /></li>
        <li><b>Decrypted total</b><span className="grow" /><b className={r.decrypted_sum === r.expected ? "up" : "down"}>{r.decrypted_sum} {r.decrypted_sum === r.expected ? `= ${a} + ${b} ✓` : "✗"}</b></li></ul>}
    </Card>
  </div>);
}

/* ---------- agent profile: live policy read through the Vault ---------- */
function AgentProfile({ onClose }) {
  const [p, setP] = useState(null), [err, setErr] = useState(null);
  useEffect(() => { fetch(`${AGENT}/policy`).then(async (r) => (r.ok ? setP(await r.json()) : setErr((await r.json()).detail))).catch(() => setErr("The agent API is not reachable.")); }, []);
  const eth = p?.limits?.ETH;
  return (
    <div className="pop" role="dialog">
      <div className="card-h"><h3>crypto-agent</h3><button className="link" onClick={onClose}>Close</button></div>
      {err && <p className="down">{err}</p>}{!p && !err && <p className="muted">Asking the Vault…</p>}
      {p && <ul className="earn">
        <li><b>Status</b><span className="grow" /><Chip s={p.agent_status === "active" ? "enforced" : "blocked"} /></li>
        <li><b>Tools</b><span className="grow" /><span className="muted">{p.allowed_tools.join(", ")}</span></li>
        <li><b>Assets</b><span className="grow" /><span>{p.allowed_assets.join(", ")}</span></li>
        <li><b>Recipients</b><span className="grow" /><span>{Object.keys(p.allowed_recipients).join(", ")}</span></li>
        {eth && <li><b>ETH limits</b><span className="grow" /><span>{eth.max_per_tx} per transfer · {eth.daily} per day</span></li>}
        <li><b>Spent (24h)</b><span className="grow" /><span>{p.spent_last_24h?.ETH} ETH</span></li></ul>}
      <small className="muted">Read live from the Vault gateway. The kill switch is the agent's status in the policy file.</small>
    </div>
  );
}

/* ---------- pages ---------- */
function Overview({ v, w, notify }) {
  const secure = v.conn === "live";
  const sub = { active: ["Active | Syncing", "ok"], degraded: ["Anvil unreachable", "warn"], offline: ["Offline", "bad"] }[v.submitter];
  const pending = Object.values(v.txs).filter((t) => t.status === "pending").length;
  return (<>
    <div className="grid3">
      <Card className="metric"><span className="label">Active Vault</span><div className="status"><i className={`pulse ${secure ? "ok" : "warn"}`} /><b className={secure ? "up" : "warn-t"}>System Health: {secure ? "SECURE" : "DEGRADED"}</b></div><small className="muted">Feed {v.conn} · deny-by-default, 6 gates</small></Card>
      <Card className="metric"><span className="label">Backend Sync</span><div className="status"><i className={`pulse ${sub[1]} ${v.submitter === "active" ? "" : "still"}`} /><b>{sub[0]}</b></div><small className="muted">Submitter :8100 · {pending} pending tx</small></Card>
      <Card className="metric"><span className="label">Decisions</span><div className="status"><b className="up">{v.events.filter((e) => e.decision === "ALLOW").length}</b><span className="muted">pass</span><b className="down">{v.events.filter((e) => e.decision === "DENY").length}</b><span className="muted">blocked</span></div><small className="muted">{v.events.filter((e) => e.severity === "critical").length} critical</small></Card>
    </div>
    <div className="grid2c"><Feed events={v.events} txs={v.txs} /><div className="col"><WalletCard w={w} /><EarnCard notify={notify} /></div></div>
    <Card title="Recent Submitter Actions"><LogsTable events={v.events} txs={v.txs} limit={5} /></Card>
  </>);
}
function Trading({ events, txs }) {
  const [iv, setIv] = useState("1D");
  const [candles, setCandles] = useState(() => genCandles("1D"));
  useEffect(() => setCandles(genCandles(iv)), [iv]);
  useEffect(() => { const id = setInterval(() => setCandles((cs) => { // simulated live tick
    const last = { ...cs[cs.length - 1] }, c = last.c * (1 + (Math.random() - 0.5) * CFG[iv].vol * 0.4);
    return [...cs.slice(0, -1), { ...last, c, h: Math.max(last.h, c), l: Math.min(last.l, c) }]; }), 2000); return () => clearInterval(id); }, [iv]);
  const px = candles[candles.length - 1].c;
  return (<div className="trade"><CandleChart candles={candles} iv={iv} setIv={setIv} /><TransferCard events={events} txs={txs} /></div>);
}
function Analytics({ events }) {
  const denies = events.filter((e) => e.decision === "DENY");
  const by = denies.reduce((m, e) => ({ ...m, [e.gate_label || "Unknown"]: (m[e.gate_label || "Unknown"] || 0) + 1 }), {});
  const max = Math.max(1, ...Object.values(by)), rate = events.length ? ((events.length - denies.length) / events.length) * 100 : 0;
  return (<div className="grid2c">
    <Card title="Blocks by Gate">{Object.keys(by).length === 0 ? <p className="empty">No blocked requests yet.</p> :
      Object.entries(by).sort((a, b) => b[1] - a[1]).map(([k, n]) => <div className="bar" key={k}><span>{k}</span><div><i style={{ width: `${(n / max) * 100}%` }} /></div><b>{n}</b></div>)}</Card>
    <Card title="Pass Rate"><div className="big">{fmt(rate, 1)}%<small>{events.length - denies.length} of {events.length} requests allowed</small></div></Card>
  </div>);
}
function Security() {
  const gates = ["Request format", "Identity & status", "Permitted tools", "Allowed assets", "Recipient allow-list", "Amount + gas ≤ balance", "Per-tx & daily limits"];
  return (<div className="grid2c">
    <Card title="Policy Gates (deny by default)"><ul className="gates">{gates.map((g, i) => <li key={g}><span className="n">{i}</span>{g}<Chip s="enforced" /></li>)}</ul></Card>
    <Card title="Key Custody"><p>The signing key is created inside HashiCorp Vault (ethsign plugin) and never leaves it. The agent only holds a capability token; the gateway refuses to start with a Vault token that could export the key.</p><p className="muted">Kill switch: suspend the agent in the policy file; it takes effect on its next request.</p></Card>
  </div>);
}

/* ---------- shell ---------- */
export default function App() {
  const v = useVault(), w = useWallet();
  const [page, setPage] = useState("overview");
  const [profile, setProfile] = useState(false), [wallet, setWallet] = useState(true), [toast, setToast] = useState(null), [seen, setSeen] = useState(0);
  const notify = useCallback((m) => { setToast(m); setTimeout(() => setToast(null), 3200); }, []);
  const critical = useMemo(() => v.events.filter((e) => e.severity === "critical").length, [v.events]);
  const unread = Math.max(0, critical - seen);
  const title = NAV.find(([k]) => k === page)[1];
  return (
    <div className="app">
      <aside className="side">
        <div className="brand"><span className="logo" />SecureAgent</div>
        <nav>{NAV.map(([k, l]) => <button key={k} className={page === k ? "on" : ""} onClick={() => setPage(k)}><Icon n={k === "logs" ? "logs" : k} />{l}</button>)}</nav>
        <div className="motto">Capabilities, Not Access</div>
      </aside>
      <div className="main">
        <header className="top">
          <h2>{title}</h2>
          <div className="top-r">
            <button className="role" onClick={() => setProfile((x) => !x)} title="Agent policy and limits">crypto-agent ▾</button>
            <button className="icon-btn" aria-label="Alerts" onClick={() => { setSeen(critical); notify(unread ? `${unread} critical alert(s) cleared` : "No new alerts"); }}><Icon n="bell" />{unread > 0 && <i className="dotbadge">{unread}</i>}</button>
            <button className={`wallet ${wallet ? "on" : ""}`} onClick={() => setWallet(!wallet)}><Icon n="wallet" />{wallet ? `Vault wallet · ${shortAddr(w.signer)}` : "Connect Wallet"}</button>
            <button className="btn ghost sm" onClick={() => notify("Demo: signed out")}><Icon n="out" />Sign Out</button>
          </div>
        </header>
        <main className="content" key={page}>
          {page === "overview" && <Overview v={v} w={w} notify={notify} />}
          {page === "phe" && <PhePage w={w} />}
          {page === "agent" && <div className="grid2c"><AgentChat /><div className="col"><WalletCard w={w} /><Feed events={v.events} txs={v.txs} /></div></div>}
          {page === "trading" && <Trading events={v.events} txs={v.txs} />}
          {page === "analytics" && <Analytics events={v.events} />}
          {page === "logs" && <Card title="Submitter Execution Logs"><LogsTable events={v.events} txs={v.txs} /></Card>}
          {page === "security" && <Security />}
        </main>
      </div>
      {profile && <AgentProfile onClose={() => setProfile(false)} />}
      {toast && <div className="toast">{toast}</div>}
    </div>
  );
}

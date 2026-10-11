import { useEffect, useMemo, useRef, useState, useCallback } from "react";

const HOST = import.meta.env.VITE_VAULT_HOST || "localhost:8000";
const SUBMITTER = import.meta.env.VITE_SUBMITTER_URL || "http://localhost:8100";

/* ---------- icons ---------- */
const ICONS = {
  overview: "M3 12l9-9 9 9M5 10v10h14V10", trading: "M3 17l6-6 4 4 8-8M15 7h6v6",
  analytics: "M4 20V10M10 20V4M16 20v-8M22 20H2", logs: "M4 6h16M4 12h16M4 18h10",
  security: "M12 2l8 3v6c0 5-3.5 9-8 11-4.5-2-8-6-8-11V5z",
  bell: "M18 8a6 6 0 10-12 0c0 7-3 8-3 8h18s-3-1-3-8M13.7 21a2 2 0 01-3.4 0",
  wallet: "M3 7h16a2 2 0 012 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2zM3 7l2-3h12M17 14h.01",
  out: "M9 21H5V3h4M16 17l5-5-5-5M21 12H9", swap: "M7 4v14M3 14l4 4 4-4M17 20V6M13 10l4-4 4 4",
};
const Icon = ({ n }) => (
  <svg className="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d={ICONS[n]} /></svg>
);
const NAV = [["overview", "Overview"], ["trading", "Trading"], ["analytics", "Analytics"], ["logs", "Submitter Logs"], ["security", "Account Security"]];

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
    try { merge(await (await fetch(`http://${HOST}/v1/events?limit=50`)).json()); } catch {}
  }, [merge]);

  useEffect(() => {
    let ws, retry = 0, stop = false, t;
    const connect = () => {
      setConn("connecting");
      ws = new WebSocket(`ws://${HOST}/ws/events`);
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
    <Card className="chart-card" title="ETH / USD" right={
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

/* ---------- FEATURE 2: swaps ---------- */
const CHAINS = ["Ethereum", "Base", "Arbitrum"];
function SwapCard({ ethPx, notify }) {
  const [from, setFrom] = useState("USDC"), [to, setTo] = useState("ETH"), [amt, setAmt] = useState("500"), [chain, setChain] = useState("Ethereum");
  const px = { ETH: ethPx, USDC: 1, DAI: 1 };
  const rate = px[from] / px[to], out = (parseFloat(amt) || 0) * rate * 0.997;
  const flip = () => { setFrom(to); setTo(from); };
  const sel = (v, set, other) => <select value={v} onChange={(e) => e.target.value === other ? flip() : set(e.target.value)}>{Object.keys(px).map((t) => <option key={t}>{t}</option>)}</select>;
  return (
    <Card title="Instant Swap" right={<select className="mini" value={chain} onChange={(e) => setChain(e.target.value)}>{CHAINS.map((c) => <option key={c}>{c}</option>)}</select>}>
      <label className="field"><span>You pay</span><div className="amt"><input value={amt} inputMode="decimal" onChange={(e) => setAmt(e.target.value.replace(/[^0-9.]/g, ""))} />{sel(from, setFrom, to)}</div></label>
      <button className="flip" onClick={flip} aria-label="Flip"><Icon n="swap" /></button>
      <label className="field"><span>You receive (est.)</span><div className="amt"><input readOnly value={out ? fmt(out, to === "ETH" ? 5 : 2) : ""} />{sel(to, setTo, from)}</div></label>
      <div className="kv"><span>Rate</span><b>1 {from} ≈ {fmt(rate, rate < 0.01 ? 6 : 4)} {to}</b></div>
      <div className="kv"><span>Fee (0.3%)</span><b>{fmt((parseFloat(amt) || 0) * 0.003, 2)} {from}</b></div>
      <button className="btn primary wide" disabled={!out} onClick={() => notify(`Demo: swapped ${amt} ${from} → ${fmt(out, 4)} ${to} on ${chain}`)}>Trade</button>
    </Card>
  );
}

/* ---------- FEATURE 3: limit orders ---------- */
function LimitOrders({ ethPx, notify }) {
  const [side, setSide] = useState("Buy"), [price, setPrice] = useState("2000"), [size, setSize] = useState("0.5"), [exp, setExp] = useState("GTC");
  const [orders, setOrders] = useState([{ id: 1, side: "Buy", price: 2000, size: 1, exp: "7D", status: "open" }]);
  useEffect(() => setOrders((o) => o.map((x) => x.status === "open" && (x.side === "Buy" ? ethPx <= x.price : ethPx >= x.price) ? { ...x, status: "filled" } : x)), [ethPx]);
  const add = (e) => { e.preventDefault(); if (!+price || !+size) return;
    setOrders((o) => [{ id: Date.now(), side, price: +price, size: +size, exp, status: "open" }, ...o]); notify(`Limit order placed: ${side} ${size} ETH @ $${price}`); };
  return (
    <Card title="Limit Orders" right={<span className="muted">ETH ${fmt(ethPx)}</span>}>
      <form onSubmit={add} className="grid2">
        <div className="seg full">{["Buy", "Sell"].map((s) => <button type="button" key={s} className={`${side === s ? "on" : ""} ${s}`} onClick={() => setSide(s)}>{s}</button>)}</div>
        <label className="field"><span>Trigger price (USD)</span><input value={price} onChange={(e) => setPrice(e.target.value.replace(/[^0-9.]/g, ""))} /></label>
        <label className="field"><span>Amount (ETH)</span><input value={size} onChange={(e) => setSize(e.target.value.replace(/[^0-9.]/g, ""))} /></label>
        <label className="field"><span>Expires</span><select value={exp} onChange={(e) => setExp(e.target.value)}>{["24H", "7D", "GTC"].map((x) => <option key={x}>{x}</option>)}</select></label>
        <button className="btn primary">{side} ETH when price {side === "Buy" ? "≤" : "≥"} ${price || "—"}</button>
      </form>
      <ul className="orders">
        {orders.filter((o) => o.status !== "cancelled").map((o) => (
          <li key={o.id}><span className={`tag ${o.side}`}>{o.side}</span><span>{o.size} ETH @ ${fmt(o.price, 0)}</span><span className="muted">{o.exp}</span><Chip s={o.status} />
            {o.status === "open" && <button className="link" onClick={() => setOrders((l) => l.map((x) => x.id === o.id ? { ...x, status: "cancelled" } : x))}>Cancel</button>}</li>))}
      </ul>
    </Card>
  );
}

/* ---------- FEATURE 4: earn ---------- */
const STAKES = [{ sym: "ETH", staked: 4.2, apy: 3.8, usd: 2300, next: "Oct 18" }, { sym: "USDC", staked: 3000, apy: 5.1, usd: 1, next: "Oct 15" }, { sym: "DAI", staked: 1200, apy: 4.4, usd: 1, next: "Oct 20" }];
function EarnCard({ notify }) {
  const rows = STAKES.map((s) => ({ ...s, value: s.staked * s.usd, earned: s.staked * s.usd * (s.apy / 100) * 0.31 }));
  const total = rows.reduce((a, r) => a + r.value, 0), earned = rows.reduce((a, r) => a + r.earned, 0);
  return (
    <Card title="Earn Rewards" right={<button className="btn ghost sm" onClick={() => notify(`Demo: claimed $${fmt(earned)} rewards`)}>Claim</button>}>
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

/* ---------- pages ---------- */
function Overview({ v, notify }) {
  const secure = v.conn === "live";
  const sub = { active: ["Active | Syncing", "ok"], degraded: ["Anvil unreachable", "warn"], offline: ["Offline", "bad"] }[v.submitter];
  const pending = Object.values(v.txs).filter((t) => t.status === "pending").length;
  return (<>
    <div className="grid3">
      <Card className="metric"><span className="label">Active Vault</span><div className="status"><i className={`pulse ${secure ? "ok" : "warn"}`} /><b className={secure ? "up" : "warn-t"}>System Health: {secure ? "SECURE" : "DEGRADED"}</b></div><small className="muted">Feed {v.conn} · deny-by-default, 6 gates</small></Card>
      <Card className="metric"><span className="label">Backend Sync</span><div className="status"><i className={`pulse ${sub[1]} ${v.submitter === "active" ? "" : "still"}`} /><b>{sub[0]}</b></div><small className="muted">Submitter :8100 · {pending} pending tx</small></Card>
      <Card className="metric"><span className="label">Decisions</span><div className="status"><b className="up">{v.events.filter((e) => e.decision === "ALLOW").length}</b><span className="muted">pass</span><b className="down">{v.events.filter((e) => e.decision === "DENY").length}</b><span className="muted">blocked</span></div><small className="muted">{v.events.filter((e) => e.severity === "critical").length} critical</small></Card>
    </div>
    <div className="grid2c"><Feed events={v.events} txs={v.txs} /><EarnCard notify={notify} /></div>
    <Card title="Recent Submitter Actions"><LogsTable events={v.events} txs={v.txs} limit={5} /></Card>
  </>);
}
function Trading({ notify }) {
  const [iv, setIv] = useState("1D");
  const [candles, setCandles] = useState(() => genCandles("1D"));
  useEffect(() => setCandles(genCandles(iv)), [iv]);
  useEffect(() => { const id = setInterval(() => setCandles((cs) => { // simulated live tick
    const last = { ...cs[cs.length - 1] }, c = last.c * (1 + (Math.random() - 0.5) * CFG[iv].vol * 0.4);
    return [...cs.slice(0, -1), { ...last, c, h: Math.max(last.h, c), l: Math.min(last.l, c) }]; }), 2000); return () => clearInterval(id); }, [iv]);
  const px = candles[candles.length - 1].c;
  return (<div className="trade"><CandleChart candles={candles} iv={iv} setIv={setIv} /><SwapCard ethPx={px} notify={notify} /><LimitOrders ethPx={px} notify={notify} /></div>);
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
  const v = useVault();
  const [page, setPage] = useState("overview");
  const [wallet, setWallet] = useState(true), [toast, setToast] = useState(null), [seen, setSeen] = useState(0);
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
            <span className="role">Member 4</span>
            <button className="icon-btn" aria-label="Alerts" onClick={() => { setSeen(critical); notify(unread ? `${unread} critical alert(s) cleared` : "No new alerts"); }}><Icon n="bell" />{unread > 0 && <i className="dotbadge">{unread}</i>}</button>
            <button className={`wallet ${wallet ? "on" : ""}`} onClick={() => setWallet(!wallet)}><Icon n="wallet" />{wallet ? "Vault wallet · 0x1484…8E82" : "Connect Wallet"}</button>
            <button className="btn ghost sm" onClick={() => notify("Demo: signed out")}><Icon n="out" />Sign Out</button>
          </div>
        </header>
        <main className="content" key={page}>
          {page === "overview" && <Overview v={v} notify={notify} />}
          {page === "trading" && <Trading notify={notify} />}
          {page === "analytics" && <Analytics events={v.events} />}
          {page === "logs" && <Card title="Submitter Execution Logs"><LogsTable events={v.events} txs={v.txs} /></Card>}
          {page === "security" && <Security />}
        </main>
      </div>
      {toast && <div className="toast">{toast}</div>}
    </div>
  );
}

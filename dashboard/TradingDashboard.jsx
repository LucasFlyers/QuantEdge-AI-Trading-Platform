import { useState, useEffect, useRef, useCallback } from "react";

// ─── Simulation Engine ────────────────────────────────────────────────────────
const SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT", "AVAX/USDT", "MATIC/USDT"];
const EXCHANGES = ["Binance", "Coinbase", "Kraken", "OKX", "Bybit"];
const TOKENS = ["BTC", "ETH", "SOL", "RNDR", "ARB", "INJ", "JUP", "TIA", "AVAX"];
let _id = 1000;

const rand = (min, max) => Math.random() * (max - min) + min;
const pick = (arr) => arr[Math.floor(Math.random() * arr.length)];
const pickTwo = (arr) => { const a = pick(arr); let b = pick(arr); while (b === a) b = pick(arr); return [a, b]; };

function genArb() {
  const sym = pick(SYMBOLS);
  const [exA, exB] = pickTwo(EXCHANGES);
  const base = sym.includes("BTC") ? rand(82000, 86000) : sym.includes("ETH") ? rand(3100, 3400) : rand(80, 900);
  const spread = rand(0.25, 1.4);
  const fee = rand(0.12, 0.28);
  const net = spread - fee;
  const maxSize = rand(8000, 60000);
  return {
    id: `ARB-${++_id}`, type: "arbitrage", symbol: sym,
    buy_exchange: exA, sell_exchange: exB,
    buy_price: base, sell_price: base * (1 + spread / 100),
    spread_bps: spread * 100, net_profit_bps: net * 100, fee_bps: fee * 100,
    profit_usd: (net / 100) * maxSize, max_size: maxSize,
    confidence: rand(0.55, 0.97),
    strength: net > 0.75 ? "critical" : net > 0.45 ? "strong" : net > 0.22 ? "moderate" : "weak",
    ts: new Date(),
  };
}

function genSentiment() {
  const tok = pick(TOKENS);
  const change = (Math.random() > 0.5 ? 1 : -1) * rand(70, 280);
  const bull = rand(28, 68);
  const bear = rand(8, 42);
  const neutral = Math.max(0, 100 - bull - bear);
  return {
    id: `SENT-${++_id}`, type: "sentiment", token: tok,
    mentions: Math.floor(rand(100, 3200)),
    change_pct: change,
    score: (bull - bear) / 100,
    bull_pct: bull, bear_pct: bear, neutral_pct: neutral,
    direction: bull > bear + 8 ? "bullish" : bear > bull + 8 ? "bearish" : "neutral",
    confidence: rand(0.58, 0.95),
    strength: Math.abs(change) > 180 ? "strong" : "moderate",
    ts: new Date(),
  };
}

function genWhale() {
  const asset = pick(["BTC", "ETH", "SOL"]);
  const type = pick(["exchange_deposit", "exchange_withdrawal", "wallet_to_wallet"]);
  const amount = asset === "BTC" ? rand(45, 600) : asset === "ETH" ? rand(400, 8000) : rand(8000, 120000);
  const price = asset === "BTC" ? 84200 : asset === "ETH" ? 3280 : 148;
  return {
    id: `WHL-${++_id}`, type: "whale", asset,
    amount, amount_usd: amount * price, move_type: type,
    from: "0x" + Math.random().toString(16).slice(2, 42),
    to: "0x" + Math.random().toString(16).slice(2, 42),
    exchange: type !== "wallet_to_wallet" ? pick(EXCHANGES.slice(0, 3)) : null,
    pattern: Math.random() > 0.6 ? pick([
      "Repeat depositor — historically precedes distribution",
      "Accumulation pattern — prior to major moves",
    ]) : null,
    direction: type === "exchange_deposit" ? "bearish" : type === "exchange_withdrawal" ? "bullish" : "neutral",
    confidence: rand(0.58, 0.93),
    strength: amount * price > 40_000_000 ? "critical" : amount * price > 8_000_000 ? "strong" : "moderate",
    tx: "0x" + Math.random().toString(16).slice(2, 18),
    ts: new Date(),
  };
}

function genSpread() {
  const [exA, exB] = pickTwo(EXCHANGES);
  const sym = pick(SYMBOLS);
  return {
    sym, exA, exB,
    bps: rand(1, 130).toFixed(1),
    price: sym.includes("BTC") ? rand(83000, 85000).toFixed(0) : rand(3200, 3350).toFixed(0),
  };
}

// ─── Formatters ───────────────────────────────────────────────────────────────
const f = {
  usd: v => "$" + parseFloat(v).toLocaleString("en-US", { maximumFractionDigits: 0 }),
  usd2: v => "$" + parseFloat(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }),
  bps: v => parseFloat(v).toFixed(1) + " bps",
  pct: v => (v > 0 ? "+" : "") + parseFloat(v).toFixed(1) + "%",
  conf: v => (v * 100).toFixed(0) + "%",
  addr: a => a ? a.slice(0, 6) + "…" + a.slice(-4) : "—",
  time: d => new Date(d).toLocaleTimeString("en-US", { hour12: false }),
  num: (v, d = 0) => parseFloat(v).toLocaleString("en-US", { maximumFractionDigits: d }),
};

// ─── Design Tokens ────────────────────────────────────────────────────────────
const C = {
  bg: "#f4f5f7",
  surface: "#ffffff",
  surfaceHover: "#fafbfc",
  border: "#e2e5ea",
  borderLight: "#edf0f4",
  text: "#0f1923",
  textSub: "#4a5568",
  textMuted: "#8a94a6",
  textFaint: "#b8bfcc",
  green: "#0a7c4f",
  greenBg: "#edf7f2",
  greenBorder: "#b3dece",
  red: "#c0392b",
  redBg: "#fdf2f2",
  redBorder: "#f0c0bc",
  amber: "#b45309",
  amberBg: "#fef9ee",
  amberBorder: "#f0d8a0",
  blue: "#1a56a0",
  blueBg: "#eef4fd",
  blueBorder: "#b8d0ee",
  purple: "#5b21b6",
  purpleBg: "#f4f0fd",
  purpleBorder: "#d4c8f0",
  accent: "#1a56a0",
};

const STRENGTH = {
  critical: { label: "CRITICAL", color: C.red,     bg: C.redBg,    border: C.redBorder },
  strong:   { label: "STRONG",   color: C.amber,    bg: C.amberBg,  border: C.amberBorder },
  moderate: { label: "MODERATE", color: C.blue,     bg: C.blueBg,   border: C.blueBorder },
  weak:     { label: "WEAK",     color: C.textMuted, bg: "#f7f8fa", border: C.borderLight },
};

const DIR = {
  bullish: { icon: "▲", color: C.green,     label: "BULLISH" },
  bearish: { icon: "▼", color: C.red,       label: "BEARISH" },
  neutral: { icon: "◆", color: C.textMuted, label: "NEUTRAL" },
};

const MOVE_TYPE = {
  exchange_deposit:    { label: "Exchange Deposit",    icon: "↑", color: C.red },
  exchange_withdrawal: { label: "Exchange Withdrawal", icon: "↓", color: C.green },
  wallet_to_wallet:    { label: "Wallet Transfer",     icon: "→", color: C.textSub },
};

// ─── Primitives ───────────────────────────────────────────────────────────────
function Badge({ strength }) {
  const s = STRENGTH[strength] || STRENGTH.weak;
  return (
    <span style={{
      fontSize: 9, fontWeight: 700, letterSpacing: "0.09em",
      padding: "2px 7px", borderRadius: 3,
      color: s.color, background: s.bg, border: `1px solid ${s.border}`,
      fontFamily: "'IBM Plex Mono', monospace",
    }}>{s.label}</span>
  );
}

function ProgressBar({ value, color, height = 3 }) {
  return (
    <div style={{ width: "100%", background: C.borderLight, borderRadius: 99, height, overflow: "hidden" }}>
      <div style={{
        width: `${Math.min(value * 100, 100)}%`, height: "100%",
        background: color, borderRadius: 99, transition: "width 0.5s ease",
      }} />
    </div>
  );
}

function StatusDot({ active = true }) {
  return (
    <span style={{
      display: "inline-block", width: 7, height: 7, borderRadius: "50%",
      background: active ? C.green : C.textFaint, flexShrink: 0,
      animation: active ? "livePulse 2.2s ease-in-out infinite" : "none",
    }} />
  );
}

// ─── Signal Cards ─────────────────────────────────────────────────────────────
function ArbCard({ sig }) {
  const str = STRENGTH[sig.strength] || STRENGTH.weak;
  const profitable = sig.net_profit_bps > 0;
  return (
    <div style={{
      background: C.surface, border: `1px solid ${C.border}`,
      borderTop: `3px solid ${str.color}`,
      borderRadius: 6, padding: "16px 18px", marginBottom: 8,
      boxShadow: "0 1px 4px rgba(0,0,0,0.04)",
      animation: "cardIn 0.2s ease",
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 14 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontWeight: 700, fontSize: 15, color: C.text }}>{sig.symbol}</span>
          <Badge strength={sig.strength} />
        </div>
        <div style={{ textAlign: "right" }}>
          <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 14, fontWeight: 700, color: profitable ? C.green : C.red }}>
            {profitable ? "+" : ""}{f.bps(sig.net_profit_bps)}
          </div>
          <div style={{ fontSize: 10, color: C.textMuted, marginTop: 2 }}>{f.time(sig.ts)}</div>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 36px 1fr", gap: 6, alignItems: "center", marginBottom: 14 }}>
        <div style={{ background: C.greenBg, border: `1px solid ${C.greenBorder}`, borderRadius: 5, padding: "8px 12px" }}>
          <div style={{ fontSize: 9, color: C.textMuted, letterSpacing: "0.08em", marginBottom: 3, fontFamily: "'IBM Plex Mono', monospace" }}>BUY</div>
          <div style={{ fontWeight: 700, fontSize: 12, color: C.green }}>{sig.buy_exchange}</div>
          <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 10, color: C.textSub, marginTop: 2 }}>{f.usd2(sig.buy_price)}</div>
        </div>
        <div style={{ textAlign: "center", color: C.textFaint, fontSize: 18, fontWeight: 300 }}>→</div>
        <div style={{ background: C.redBg, border: `1px solid ${C.redBorder}`, borderRadius: 5, padding: "8px 12px" }}>
          <div style={{ fontSize: 9, color: C.textMuted, letterSpacing: "0.08em", marginBottom: 3, fontFamily: "'IBM Plex Mono', monospace" }}>SELL</div>
          <div style={{ fontWeight: 700, fontSize: 12, color: C.red }}>{sig.sell_exchange}</div>
          <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 10, color: C.textSub, marginTop: 2 }}>{f.usd2(sig.sell_price)}</div>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 6, marginBottom: 12 }}>
        {[
          { label: "GROSS SPREAD", val: f.bps(sig.spread_bps), color: C.textSub },
          { label: "TOTAL FEES",   val: f.bps(sig.fee_bps),    color: C.amber },
          { label: "NET PROFIT",   val: f.bps(sig.net_profit_bps), color: profitable ? C.green : C.red },
          { label: "EST. PROFIT",  val: f.usd(sig.profit_usd), color: C.purple },
        ].map(({ label, val, color }) => (
          <div key={label} style={{ background: C.bg, borderRadius: 4, padding: "7px 9px" }}>
            <div style={{ fontSize: 8, color: C.textMuted, letterSpacing: "0.07em", marginBottom: 3, fontFamily: "'IBM Plex Mono', monospace" }}>{label}</div>
            <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 11, fontWeight: 700, color }}>{val}</div>
          </div>
        ))}
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <span style={{ fontSize: 9, color: C.textMuted, letterSpacing: "0.07em", minWidth: 72, fontFamily: "'IBM Plex Mono', monospace" }}>CONFIDENCE</span>
        <div style={{ flex: 1 }}><ProgressBar value={sig.confidence} color={str.color} /></div>
        <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 10, fontWeight: 700, color: str.color, minWidth: 32, textAlign: "right" }}>
          {f.conf(sig.confidence)}
        </span>
      </div>
    </div>
  );
}

function SentimentCard({ sig }) {
  const dir = DIR[sig.direction] || DIR.neutral;
  const surging = Math.abs(sig.change_pct) > 100;
  return (
    <div style={{
      background: C.surface, border: `1px solid ${C.border}`,
      borderTop: `3px solid ${dir.color}`,
      borderRadius: 6, padding: "16px 18px", marginBottom: 8,
      boxShadow: "0 1px 4px rgba(0,0,0,0.04)",
      animation: "cardIn 0.2s ease",
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 12 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontWeight: 700, fontSize: 15, color: C.text }}>{sig.token}</span>
          <Badge strength={sig.strength} />
          {surging && (
            <span style={{
              fontSize: 9, fontWeight: 700, padding: "2px 6px", borderRadius: 3,
              background: C.amberBg, color: C.amber, border: `1px solid ${C.amberBorder}`,
              fontFamily: "'IBM Plex Mono', monospace", letterSpacing: "0.07em",
            }}>SURGE</span>
          )}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
          <span style={{ fontSize: 11, color: dir.color }}>{dir.icon}</span>
          <span style={{ fontSize: 10, fontWeight: 700, color: dir.color, letterSpacing: "0.07em", fontFamily: "'IBM Plex Mono', monospace" }}>
            {dir.label}
          </span>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 6, marginBottom: 12 }}>
        {[
          { label: "MENTIONS (2H)", val: f.num(sig.mentions), color: C.text },
          { label: "CHANGE",        val: f.pct(sig.change_pct), color: sig.change_pct > 0 ? C.green : C.red },
          { label: "SENTIMENT",     val: (sig.score > 0 ? "+" : "") + (sig.score * 100).toFixed(0), color: sig.score > 0 ? C.green : sig.score < 0 ? C.red : C.textSub },
        ].map(({ label, val, color }) => (
          <div key={label} style={{ background: C.bg, borderRadius: 4, padding: "8px 10px" }}>
            <div style={{ fontSize: 8, color: C.textMuted, letterSpacing: "0.07em", marginBottom: 4, fontFamily: "'IBM Plex Mono', monospace" }}>{label}</div>
            <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 14, fontWeight: 700, color }}>{val}</div>
          </div>
        ))}
      </div>

      <div style={{ marginBottom: 10 }}>
        <div style={{ display: "flex", height: 6, borderRadius: 3, overflow: "hidden", gap: "2px" }}>
          <div style={{ flex: sig.bear_pct, background: C.red, opacity: 0.7, minWidth: sig.bear_pct > 0 ? 2 : 0 }} />
          <div style={{ flex: sig.neutral_pct, background: C.borderLight, minWidth: sig.neutral_pct > 0 ? 2 : 0 }} />
          <div style={{ flex: sig.bull_pct, background: C.green, opacity: 0.7, minWidth: sig.bull_pct > 0 ? 2 : 0 }} />
        </div>
        <div style={{ display: "flex", justifyContent: "space-between", marginTop: 4 }}>
          <span style={{ fontSize: 9, color: C.red,     fontFamily: "'IBM Plex Mono', monospace" }}>Bear {sig.bear_pct.toFixed(0)}%</span>
          <span style={{ fontSize: 9, color: C.textMuted, fontFamily: "'IBM Plex Mono', monospace" }}>Neutral {sig.neutral_pct.toFixed(0)}%</span>
          <span style={{ fontSize: 9, color: C.green,   fontFamily: "'IBM Plex Mono', monospace" }}>Bull {sig.bull_pct.toFixed(0)}%</span>
        </div>
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <span style={{ fontSize: 9, color: C.textMuted, letterSpacing: "0.07em", minWidth: 72, fontFamily: "'IBM Plex Mono', monospace" }}>CONFIDENCE</span>
        <div style={{ flex: 1 }}><ProgressBar value={sig.confidence} color={dir.color} /></div>
        <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 10, fontWeight: 700, color: dir.color, minWidth: 32, textAlign: "right" }}>
          {f.conf(sig.confidence)}
        </span>
      </div>
    </div>
  );
}

function WhaleCard({ sig }) {
  const move = MOVE_TYPE[sig.move_type] || MOVE_TYPE.wallet_to_wallet;
  const dir = DIR[sig.direction] || DIR.neutral;
  const str = STRENGTH[sig.strength] || STRENGTH.moderate;
  return (
    <div style={{
      background: C.surface, border: `1px solid ${C.border}`,
      borderTop: `3px solid ${str.color}`,
      borderRadius: 6, padding: "16px 18px", marginBottom: 8,
      boxShadow: "0 1px 4px rgba(0,0,0,0.04)",
      animation: "cardIn 0.2s ease",
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 12 }}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 7, marginBottom: 4 }}>
            <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontWeight: 700, fontSize: 15, color: C.text }}>
              {f.num(sig.amount, 0)} {sig.asset}
            </span>
            <Badge strength={sig.strength} />
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
            <span style={{ fontSize: 12, fontWeight: 600, color: move.color }}>{move.icon}</span>
            <span style={{ fontSize: 11, color: C.textSub }}>{move.label}</span>
          </div>
        </div>
        <div style={{ textAlign: "right" }}>
          <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontWeight: 700, fontSize: 14, color: C.text }}>
            {f.usd(sig.amount_usd)}
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 4, justifyContent: "flex-end", marginTop: 4 }}>
            <span style={{ fontSize: 10, color: dir.color }}>{dir.icon}</span>
            <span style={{ fontSize: 9, color: dir.color, fontWeight: 700, letterSpacing: "0.07em", fontFamily: "'IBM Plex Mono', monospace" }}>
              {dir.label}
            </span>
          </div>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6, marginBottom: 10 }}>
        {[
          { label: "FROM", val: f.addr(sig.from) },
          { label: sig.exchange ? "EXCHANGE" : "TO", val: sig.exchange || f.addr(sig.to) },
        ].map(({ label, val }) => (
          <div key={label} style={{ background: C.bg, borderRadius: 4, padding: "7px 10px" }}>
            <div style={{ fontSize: 8, color: C.textMuted, letterSpacing: "0.07em", marginBottom: 3, fontFamily: "'IBM Plex Mono', monospace" }}>{label}</div>
            <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 11, color: C.textSub }}>{val}</div>
          </div>
        ))}
      </div>

      {sig.pattern && (
        <div style={{
          background: C.purpleBg, border: `1px solid ${C.purpleBorder}`,
          borderRadius: 4, padding: "7px 10px", marginBottom: 10,
          fontSize: 10, color: C.purple, fontStyle: "italic",
        }}>{sig.pattern}</div>
      )}

      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 9, color: C.textFaint }}>TX: {sig.tx.slice(0, 16)}…</span>
        <span style={{ fontSize: 10, color: C.textMuted }}>{f.time(sig.ts)}</span>
      </div>
    </div>
  );
}

// ─── Spread Row ────────────────────────────────────────────────────────────────
function SpreadRow({ s, rank }) {
  const bps = parseFloat(s.bps);
  const color = bps > 80 ? C.red : bps > 40 ? C.amber : bps > 15 ? C.green : C.textMuted;
  const isHot = bps > 60;
  return (
    <div className="row-hover" style={{
      display: "grid", gridTemplateColumns: "20px 90px 1fr 90px 72px",
      gap: 8, padding: "7px 14px", borderBottom: `1px solid ${C.borderLight}`,
      background: isHot ? "#fffef8" : "transparent", alignItems: "center",
    }}>
      <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 9, color: C.textFaint }}>{rank}</span>
      <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 11, fontWeight: 600, color: C.text }}>{s.sym}</span>
      <span style={{ fontSize: 10, color: C.textSub }}>
        <span style={{ color: C.green, fontWeight: 600 }}>{s.exA}</span>
        <span style={{ color: C.textFaint, margin: "0 4px" }}>→</span>
        <span style={{ color: C.red, fontWeight: 600 }}>{s.exB}</span>
      </span>
      <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 10, color: C.textSub }}>
        ${parseInt(s.price).toLocaleString()}
      </span>
      <div style={{ textAlign: "right" }}>
        <span style={{
          fontFamily: "'IBM Plex Mono', monospace", fontSize: 11, fontWeight: 700, color,
          background: isHot ? C.amberBg : "transparent",
          padding: isHot ? "1px 5px" : "0", borderRadius: isHot ? 3 : 0,
        }}>{s.bps}</span>
        <span style={{ fontSize: 8, color: C.textMuted, marginLeft: 2 }}>bps</span>
      </div>
    </div>
  );
}

// ─── Stat Card ────────────────────────────────────────────────────────────────
function StatCard({ label, value, color }) {
  return (
    <div style={{
      background: C.surface, border: `1px solid ${C.border}`,
      borderRadius: 6, padding: "14px 16px",
      boxShadow: "0 1px 3px rgba(0,0,0,0.04)",
    }}>
      <div style={{ fontSize: 9, color: C.textMuted, letterSpacing: "0.09em", marginBottom: 8, fontFamily: "'IBM Plex Mono', monospace" }}>{label}</div>
      <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 26, fontWeight: 700, color: color || C.text, lineHeight: 1 }}>{value}</div>
    </div>
  );
}

// ─── Main Dashboard ───────────────────────────────────────────────────────────
export default function Dashboard() {
  const [tab, setTab] = useState("arbitrage");
  const [signals, setSignals] = useState([]);
  const [spreads, setSpreads] = useState([]);
  const [paused, setPaused] = useState(false);
  const [stats, setStats] = useState({ ticks: 0, signals: 0, uptime: 0, latency: 12 });
  const t0 = useRef(Date.now());

  const push = useCallback((sig) => {
    setSignals(p => [sig, ...p].slice(0, 200));
    setStats(p => ({ ...p, signals: p.signals + 1 }));
  }, []);

  useEffect(() => {
    setSpreads(Array.from({ length: 14 }, genSpread));
    const step = () => {
      if (!paused) {
        const r = Math.random();
        if (r < 0.48) push(genArb());
        else if (r < 0.76) push(genSentiment());
        else push(genWhale());
        setSpreads(p => { const n = [...p]; n[Math.floor(Math.random() * n.length)] = genSpread(); return n; });
        setStats(p => ({
          ...p,
          ticks: p.ticks + Math.floor(rand(30, 100)),
          uptime: Math.floor((Date.now() - t0.current) / 1000),
          latency: Math.floor(rand(5, 22)),
        }));
      }
    };
    step();
    const timer = setInterval(step, 2000);
    return () => clearInterval(timer);
  }, [paused, push]);

  const arbs   = signals.filter(s => s.type === "arbitrage");
  const sents  = signals.filter(s => s.type === "sentiment");
  const whales = signals.filter(s => s.type === "whale");

  const displayed = tab === "all" ? signals : tab === "arbitrage" ? arbs : tab === "sentiment" ? sents : whales;

  const TABS = [
    { id: "all",       label: "All Signals", count: signals.length },
    { id: "arbitrage", label: "Arbitrage",   count: arbs.length,   color: C.green },
    { id: "sentiment", label: "Sentiment",   count: sents.length,  color: C.purple },
    { id: "whale",     label: "Whale",       count: whales.length, color: C.amber },
  ];

  return (
    <div style={{ background: C.bg, minHeight: "100vh", color: C.text, fontFamily: "'Sora', sans-serif", fontSize: 13 }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Sora:wght@300;400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600;700&display=swap');
        *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
        ::-webkit-scrollbar { width: 4px; height: 4px; }
        ::-webkit-scrollbar-track { background: ${C.borderLight}; }
        ::-webkit-scrollbar-thumb { background: #d0d4dd; border-radius: 2px; }
        button { cursor: pointer; font-family: inherit; }
        @keyframes cardIn { from { opacity: 0; transform: translateY(-5px); } to { opacity: 1; transform: translateY(0); } }
        @keyframes livePulse { 0%,100% { opacity: 1; } 50% { opacity: 0.35; } }
        .tab-btn:hover { background: ${C.bg} !important; }
        .row-hover:hover { background: #f8f9fb !important; }
      `}</style>

      {/* ── Header ── */}
      <div style={{
        background: C.surface, borderBottom: `1px solid ${C.border}`,
        padding: "0 28px", position: "sticky", top: 0, zIndex: 200,
        boxShadow: "0 1px 4px rgba(0,0,0,0.05)",
      }}>
        <div style={{ maxWidth: 1440, margin: "0 auto", height: 54, display: "flex", alignItems: "center", gap: 20 }}>

          {/* Brand */}
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginRight: 8 }}>
            <div style={{
              width: 32, height: 32, borderRadius: 7, background: C.blue,
              display: "flex", alignItems: "center", justifyContent: "center",
            }}>
              <svg width="17" height="17" viewBox="0 0 17 17" fill="none">
                <polyline points="1,12 5,7 8.5,10 12,5 16,8" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" fill="none"/>
              </svg>
            </div>
            <div>
              <div style={{ fontWeight: 700, fontSize: 14, letterSpacing: "-0.02em", color: C.text }}>QuantEdge</div>
              <div style={{ fontSize: 8.5, color: C.textMuted, letterSpacing: "0.10em" }}>INTELLIGENCE PLATFORM</div>
            </div>
          </div>

          {/* Status pills */}
          <div style={{ display: "flex", gap: 6, flex: 1 }}>
            {[
              { label: "STATUS",  content: <><StatusDot /><span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 10, fontWeight: 700, color: C.green }}>LIVE</span></> },
              { label: "TICKS",   content: <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 11, fontWeight: 600, color: C.text }}>{stats.ticks.toLocaleString()}</span> },
              { label: "SIGNALS", content: <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 11, fontWeight: 600, color: C.blue }}>{stats.signals}</span> },
              { label: "LATENCY", content: <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 11, fontWeight: 600, color: stats.latency < 15 ? C.green : C.amber }}>{stats.latency}ms</span> },
              { label: "UPTIME",  content: <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 11, color: C.textSub }}>{stats.uptime}s</span> },
            ].map(({ label, content }) => (
              <div key={label} style={{
                display: "flex", alignItems: "center", gap: 6,
                padding: "4px 10px", background: C.bg,
                border: `1px solid ${C.border}`, borderRadius: 4,
              }}>
                <span style={{ fontSize: 8, color: C.textMuted, letterSpacing: "0.09em", fontFamily: "'IBM Plex Mono', monospace" }}>{label}</span>
                {content}
              </div>
            ))}
          </div>

          {/* Exchange status */}
          <div style={{ display: "flex", gap: 4 }}>
            {EXCHANGES.map(ex => (
              <div key={ex} style={{
                display: "flex", alignItems: "center", gap: 5,
                padding: "3px 9px", background: C.bg,
                border: `1px solid ${C.border}`, borderRadius: 4,
                fontSize: 10, color: C.textSub, fontWeight: 500,
              }}>
                <StatusDot />
                {ex}
              </div>
            ))}
          </div>

          <button
            className="tab-btn"
            onClick={() => setPaused(p => !p)}
            style={{
              padding: "6px 14px", borderRadius: 4, fontSize: 11, fontWeight: 600,
              background: paused ? C.amberBg : C.surface,
              color: paused ? C.amber : C.textSub,
              border: `1px solid ${paused ? C.amberBorder : C.border}`,
              transition: "all 0.15s",
            }}
          >{paused ? "▶ Resume" : "⏸ Pause"}</button>
        </div>
      </div>

      {/* ── Body ── */}
      <div style={{ maxWidth: 1440, margin: "0 auto", padding: "20px 28px", display: "grid", gridTemplateColumns: "1fr 390px", gap: 20 }}>

        {/* Left */}
        <div>
          {/* Stats */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 10, marginBottom: 16 }}>
            <StatCard label="ARB SIGNALS"      value={arbs.length}           color={C.green} />
            <StatCard label="SENTIMENT SIGNALS" value={sents.length}         color={C.purple} />
            <StatCard label="WHALE ALERTS"      value={whales.length}        color={C.amber} />
            <StatCard label="TICKS PROCESSED"   value={stats.ticks.toLocaleString()} />
          </div>

          {/* Tabs */}
          <div style={{
            display: "flex", gap: 0, background: C.surface,
            border: `1px solid ${C.border}`, borderRadius: 6, padding: 4, marginBottom: 14,
            boxShadow: "0 1px 3px rgba(0,0,0,0.04)",
          }}>
            {TABS.map(t => (
              <button key={t.id} className="tab-btn" onClick={() => setTab(t.id)} style={{
                flex: 1, padding: "7px 10px", borderRadius: 4,
                fontSize: 11, fontWeight: tab === t.id ? 700 : 500,
                color: tab === t.id ? (t.color || C.text) : C.textMuted,
                background: tab === t.id ? C.bg : "transparent",
                border: `1px solid ${tab === t.id ? C.border : "transparent"}`,
                display: "flex", alignItems: "center", justifyContent: "center", gap: 6,
                transition: "all 0.15s",
              }}>
                {t.label}
                <span style={{
                  fontFamily: "'IBM Plex Mono', monospace", fontSize: 10, fontWeight: 600,
                  padding: "1px 5px", borderRadius: 3,
                  background: tab === t.id ? (t.color ? `${t.color}18` : C.borderLight) : C.borderLight,
                  color: tab === t.id ? (t.color || C.textSub) : C.textMuted,
                }}>{t.count}</span>
              </button>
            ))}
          </div>

          {/* Feed */}
          <div style={{ maxHeight: "calc(100vh - 280px)", overflowY: "auto", paddingRight: 2 }}>
            {displayed.length === 0 ? (
              <div style={{
                textAlign: "center", padding: "64px 20px", color: C.textMuted,
                background: C.surface, border: `1px solid ${C.border}`,
                borderRadius: 6, fontSize: 13,
              }}>
                <div style={{ fontSize: 28, marginBottom: 10, opacity: 0.3 }}>◌</div>
                Awaiting signal data…
              </div>
            ) : displayed.map(sig => (
              <div key={sig.id}>
                {sig.type === "arbitrage" && <ArbCard sig={sig} />}
                {sig.type === "sentiment" && <SentimentCard sig={sig} />}
                {sig.type === "whale" && <WhaleCard sig={sig} />}
              </div>
            ))}
          </div>
        </div>

        {/* Right */}
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>

          {/* Spread Scanner */}
          <div style={{
            background: C.surface, border: `1px solid ${C.border}`,
            borderRadius: 6, overflow: "hidden", boxShadow: "0 1px 3px rgba(0,0,0,0.04)",
          }}>
            <div style={{
              padding: "12px 14px", borderBottom: `1px solid ${C.borderLight}`,
              display: "flex", justifyContent: "space-between", alignItems: "center",
            }}>
              <div>
                <div style={{ fontWeight: 700, fontSize: 12, marginBottom: 2 }}>Live Spread Scanner</div>
                <div style={{ fontSize: 9, color: C.textMuted }}>Cross-exchange — sorted by spread size</div>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
                <StatusDot />
                <span style={{ fontSize: 9, color: C.green, fontFamily: "'IBM Plex Mono', monospace", fontWeight: 700 }}>LIVE</span>
              </div>
            </div>
            <div style={{
              display: "grid", gridTemplateColumns: "20px 90px 1fr 90px 72px",
              gap: 8, padding: "5px 14px", background: C.bg, borderBottom: `1px solid ${C.border}`,
            }}>
              {["#", "SYMBOL", "ROUTE", "PRICE", "SPREAD"].map((h, i) => (
                <span key={h} style={{
                  fontSize: 8, color: C.textMuted, letterSpacing: "0.09em",
                  fontFamily: "'IBM Plex Mono', monospace", textAlign: i === 4 ? "right" : "left",
                }}>{h}</span>
              ))}
            </div>
            <div style={{ maxHeight: 288, overflowY: "auto" }}>
              {[...spreads].sort((a, b) => parseFloat(b.bps) - parseFloat(a.bps)).map((s, i) => (
                <SpreadRow key={i} s={s} rank={i + 1} />
              ))}
            </div>
          </div>

          {/* API endpoints */}
          <div style={{
            background: C.surface, border: `1px solid ${C.border}`,
            borderRadius: 6, overflow: "hidden", boxShadow: "0 1px 3px rgba(0,0,0,0.04)",
          }}>
            <div style={{ padding: "12px 14px", borderBottom: `1px solid ${C.borderLight}` }}>
              <div style={{ fontWeight: 700, fontSize: 12, marginBottom: 2 }}>REST API</div>
              <div style={{ fontSize: 9, color: C.textMuted }}>Platform interface · :8000</div>
            </div>
            <div style={{ padding: "10px 14px" }}>
              {[
                { path: "GET /signals/arbitrage", color: C.green },
                { path: "GET /signals/sentiment", color: C.purple },
                { path: "GET /signals/whales",    color: C.amber },
                { path: "GET /signals/liquidity", color: C.blue },
                { path: "GET /scanner/arbitrage/live", color: C.textSub },
                { path: "GET /engine/stats",      color: C.textMuted },
                { path: "GET /health",            color: C.textMuted },
              ].map(({ path, color }) => (
                <div key={path} style={{
                  fontFamily: "'IBM Plex Mono', monospace", fontSize: 10,
                  padding: "5px 8px", marginBottom: 3, borderRadius: 4,
                  background: C.bg, color, borderLeft: `2px solid ${color}`,
                }}>{path}</div>
              ))}
            </div>
          </div>

          {/* Developer Credits */}
          <div style={{
            background: C.surface, border: `1px solid ${C.border}`,
            borderRadius: 6, overflow: "hidden",
            boxShadow: "0 1px 3px rgba(0,0,0,0.04)",
          }}>
            <div style={{
              padding: "12px 14px",
              background: `linear-gradient(135deg, ${C.blueBg} 0%, #f8f4fd 100%)`,
              borderBottom: `1px solid ${C.border}`,
            }}>
              <div style={{ fontWeight: 700, fontSize: 12, marginBottom: 1, color: C.text }}>Developer</div>
              <div style={{ fontSize: 9, color: C.textMuted }}>Platform design & engineering</div>
            </div>
            <div style={{ padding: "14px 14px" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10 }}>
                <div style={{
                  width: 38, height: 38, borderRadius: "50%",
                  background: `linear-gradient(135deg, ${C.blue}, ${C.purple})`,
                  display: "flex", alignItems: "center", justifyContent: "center",
                  fontSize: 15, fontWeight: 700, color: "#fff", flexShrink: 0,
                }}>B</div>
                <div>
                  <div style={{ fontWeight: 700, fontSize: 13, color: C.text }}>Bilal Etudaiye-Muhtar</div>
                  <div style={{ fontSize: 10, color: C.textMuted, marginTop: 1 }}>Software Engineer</div>
                </div>
              </div>
              <a
                href="https://www.linkedin.com/in/bilal-etudaiye-muhtar-2725a317a"
                target="_blank"
                rel="noopener noreferrer"
                style={{
                  display: "flex", alignItems: "center", gap: 7,
                  padding: "7px 10px", borderRadius: 5,
                  background: "#f0f7ff", border: `1px solid ${C.blueBorder}`,
                  color: C.blue, textDecoration: "none",
                  fontSize: 10, fontWeight: 600,
                  transition: "all 0.15s",
                }}
              >
                <svg width="13" height="13" viewBox="0 0 24 24" fill={C.blue}>
                  <path d="M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.85 3.37-1.85 3.601 0 4.267 2.37 4.267 5.455v6.286zM5.337 7.433a2.062 2.062 0 01-2.063-2.065 2.064 2.064 0 112.063 2.065zm1.782 13.019H3.555V9h3.564v11.452zM22.225 0H1.771C.792 0 0 .774 0 1.729v20.542C0 23.227.792 24 1.771 24h20.451C23.2 24 24 23.227 24 22.271V1.729C24 .774 23.2 0 22.222 0h.003z"/>
                </svg>
                Connect on LinkedIn
                <span style={{ marginLeft: "auto", fontSize: 9, opacity: 0.6 }}>↗</span>
              </a>
            </div>
          </div>

          {/* Signal Legend */}
          <div style={{
            background: C.surface, border: `1px solid ${C.border}`,
            borderRadius: 6, padding: "12px 14px", boxShadow: "0 1px 3px rgba(0,0,0,0.04)",
          }}>
            <div style={{ fontWeight: 700, fontSize: 12, marginBottom: 10 }}>Signal Strength Legend</div>
            {Object.entries(STRENGTH).map(([key, s]) => (
              <div key={key} style={{
                display: "flex", alignItems: "center", gap: 10, padding: "6px 0",
                borderBottom: `1px solid ${C.borderLight}`,
              }}>
                <div style={{ width: 3, height: 18, background: s.color, borderRadius: 2, flexShrink: 0 }} />
                <span style={{
                  fontSize: 9, fontWeight: 700, letterSpacing: "0.08em",
                  fontFamily: "'IBM Plex Mono', monospace", color: s.color, minWidth: 68,
                }}>{s.label}</span>
                <span style={{ fontSize: 10, color: C.textMuted, lineHeight: 1.4 }}>
                  {key === "critical" ? "Immediate action required" :
                   key === "strong"   ? "High confidence, low latency" :
                   key === "moderate" ? "Standard — verify conditions" :
                   "Monitor only"}
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

"use client";
// Single-strategy analysis for the strategy page: headline KPI tiles (net P&L, trade/day win %, profit factor,
// avg win/loss), a composite score radar, the cumulative P&L curve and net P&L per session. Everything is
// derived from /api/performance/strategies, the same source as the Strategy performance table.
import { useState } from "react";
import { usePoll } from "@/lib/hooks";
import { cls, money, pnlClass } from "@/lib/format";
import { Card, Empty, Notice } from "./ui";
import { useT } from "@/lib/i18n";

type Range = "1W" | "1M" | "3M" | "YTD" | "ALL";
const RANGES: Range[] = ["1W", "1M", "3M", "YTD", "ALL"];

interface Trip { sid: string; pnl: number }
interface Row { sid: string; range_pnl: number; max_dd_usd: number; open_positions: number; unrealized: number }
interface Perf {
  lo: string; hi: string; strategies: Row[]; trades: Trip[];
  daily: { session: string; by_strategy: Record<string, number> }[];
}

const clamp = (v: number) => Math.max(0, Math.min(100, v));
const compact = (v: number) => {
  const a = Math.abs(v), s = v < 0 ? "-" : "";
  return a >= 1e6 ? `${s}$${+(a / 1e6).toFixed(2)}M` : a >= 1e3 ? `${s}$${+(a / 1e3).toFixed(1)}K` : `${s}$${Math.round(a)}`;
};
const pctTxt = (v: number | null) => (v == null ? "—" : `${(v * 100).toFixed(2)}%`);
const ratio = (v: number | null) => (v == null ? "—" : v === Infinity ? "∞" : v.toFixed(2));

function ticks(lo: number, hi: number, count = 5) {
  if (hi - lo < 10) { const m = (hi + lo) / 2; lo = m - 5; hi = m + 5; }
  const raw = (hi - lo) / count, mag = 10 ** Math.floor(Math.log10(raw)), n = raw / mag;
  const step = (n < 1.5 ? 1 : n < 3 ? 2 : n < 7 ? 5 : 10) * mag;
  const out: number[] = [];
  for (let v = Math.floor(lo / step) * step; v < hi + step * 0.999; v += step) out.push(+v.toPrecision(12));
  return out;
}

/** Semicircle gauge split into win / break-even / loss arcs, with count chips underneath. */
function Gauge({ win, even, loss }: { win: number; even: number; loss: number }) {
  const tot = win + even + loss || 1, r = 34, cx = 44, cy = 42, len = Math.PI * r;
  const arc = `M ${cx - r} ${cy} A ${r} ${r} 0 0 1 ${cx + r} ${cy}`;
  const segs = [[win, "var(--good)"], [even, "var(--accent)"], [loss, "var(--bad)"]] as const;
  let off = 0;
  return (
    <div className="an-gauge">
      <svg viewBox="0 0 88 48" width="88" height="48" aria-hidden>
        <path d={arc} fill="none" stroke="var(--panel-2)" strokeWidth="7" />
        {segs.map(([n, c], i) => {
          const d = (n / tot) * len, el = <path key={i} d={arc} fill="none" stroke={c} strokeWidth="7" strokeDasharray={`${d} ${len}`} strokeDashoffset={-off} />;
          off += d;
          return n ? el : null;
        })}
      </svg>
      <div className="an-chips"><span className="good">{win}</span><span className="even">{even}</span><span className="bad">{loss}</span></div>
    </div>
  );
}

/** Ring showing gross profit vs gross loss. */
function Ring({ gp, gl }: { gp: number; gl: number }) {
  const r = 22, c = 2 * Math.PI * r, f = gp + gl > 0 ? gp / (gp + gl) : 0;
  return (
    <svg viewBox="0 0 56 56" width="56" height="56" aria-hidden>
      <circle cx="28" cy="28" r={r} fill="none" stroke={gp + gl > 0 ? "var(--bad)" : "var(--panel-2)"} strokeWidth="6" />
      <circle cx="28" cy="28" r={r} fill="none" stroke="var(--good)" strokeWidth="6" strokeDasharray={`${f * c} ${c}`} transform="rotate(-90 28 28)" />
    </svg>
  );
}

function Tile({ label, value, valueClass, children, title }: { label: string; value: string; valueClass?: string; children?: React.ReactNode; title?: string }) {
  return (
    <div className="an-tile" title={title}>
      <div><div className="label">{label}</div><div className={cls("an-value", valueClass)}>{value}</div></div>
      {children}
    </div>
  );
}

function Radar({ axes }: { axes: { label: string; v: number }[] }) {
  const W = 400, H = 240, cx = W / 2, cy = H / 2 + 4, R = 78, n = axes.length;
  const pt = (i: number, f: number) => {
    const a = -Math.PI / 2 + (i * 2 * Math.PI) / n;
    return [cx + Math.cos(a) * R * f, cy + Math.sin(a) * R * f];
  };
  const poly = (f: (i: number) => number) => axes.map((_, i) => pt(i, f(i)).join(",")).join(" ");
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ maxWidth: 420 }} role="img">
      {[0.25, 0.5, 0.75, 1].map((f) => <polygon key={f} points={poly(() => f)} fill="none" stroke="var(--grid)" />)}
      {axes.map((_, i) => { const [x, y] = pt(i, 1); return <line key={i} x1={cx} y1={cy} x2={x} y2={y} stroke="var(--grid)" />; })}
      <polygon points={poly((i) => axes[i].v / 100)} fill="var(--c7)" fillOpacity="0.35" stroke="var(--c7)" strokeWidth="1.5" />
      {axes.map((a, i) => {
        const [x, y] = pt(i, a.v / 100), [lx, ly] = pt(i, 1.2);
        return (
          <g key={a.label}>
            <circle cx={x} cy={y} r="3" fill="var(--c7)" />
            <text x={lx} y={ly} fontSize="11" fill="var(--muted)" textAnchor={Math.abs(lx - cx) < 5 ? "middle" : lx > cx ? "start" : "end"} dominantBaseline="middle">
              {a.label} <tspan fill="var(--text)">{Math.round(a.v)}</tspan>
            </text>
          </g>
        );
      })}
    </svg>
  );
}

const PAD = { l: 58, r: 10, t: 10, b: 24 };
function frame(vals: number[], W: number, H: number) {
  const tk = ticks(Math.min(0, ...vals), Math.max(0, ...vals));
  const lo = tk[0], hi = tk[tk.length - 1];
  const y = (v: number) => PAD.t + (1 - (v - lo) / (hi - lo || 1)) * (H - PAD.t - PAD.b);
  return { tk, y };
}
function Axes({ tk, y, W, labels }: { tk: number[]; y: (v: number) => number; W: number; labels: { x: number; s: string }[] }) {
  const H = y(tk[0]);
  return (
    <>
      {tk.map((v) => (
        <g key={v}>
          <line x1={PAD.l} x2={W - PAD.r} y1={y(v)} y2={y(v)} stroke="var(--grid)" strokeDasharray={v === 0 ? undefined : "3 3"} />
          <text x={PAD.l - 6} y={y(v)} fontSize="10" fill="var(--muted)" textAnchor="end" dominantBaseline="middle">{compact(v)}</text>
        </g>
      ))}
      {labels.map((l) => <text key={l.s + l.x} x={l.x} y={H + 16} fontSize="10" fill="var(--muted)" textAnchor="middle">{l.s}</text>)}
    </>
  );
}
const md = (s: string) => `${s.slice(5, 7)}/${s.slice(8, 10)}`;
const pick = (n: number, k = 5) => (n <= k ? [...Array(n).keys()] : [...Array(k).keys()].map((i) => Math.round((i * (n - 1)) / (k - 1))));

function Cumulative({ days }: { days: { session: string; pnl: number }[] }) {
  const W = 420, H = 230;
  let run = 0;
  // start from $0 the session before the first, so even one session draws a curve
  days = [{ session: "", pnl: 0 }, ...days];
  const cum = days.map((d) => (run += d.pnl));
  const { tk, y } = frame(cum, W, H);
  const x = (i: number) => PAD.l + (days.length > 1 ? (i / (days.length - 1)) * (W - PAD.l - PAD.r) : (W - PAD.l - PAD.r) / 2);
  const line = cum.map((v, i) => `${i ? "L" : "M"}${x(i)},${y(v)}`).join("");
  const area = `${line}L${x(cum.length - 1)},${y(0)}L${x(0)},${y(0)}Z`;
  const up = (cum[cum.length - 1] ?? 0) >= 0;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img">
      <defs>
        <linearGradient id="an-fill" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0" stopColor={up ? "var(--good)" : "var(--bad)"} stopOpacity="0.55" />
          <stop offset="1" stopColor={up ? "var(--good)" : "var(--bad)"} stopOpacity="0.04" />
        </linearGradient>
      </defs>
      <Axes tk={tk} y={y} W={W} labels={pick(days.length).filter((i) => i > 0).map((i) => ({ x: x(i), s: md(days[i].session) }))} />
      <path d={area} fill="url(#an-fill)" />
      <path d={line} fill="none" stroke={up ? "var(--good)" : "var(--bad)"} strokeWidth="1.5" />
      {cum.map((v, i) => i > 0 && <circle key={i} cx={x(i)} cy={y(v)} r="6" fill="transparent"><title>{days[i].session}: {money(v)}</title></circle>)}
    </svg>
  );
}

function DailyBars({ days }: { days: { session: string; pnl: number }[] }) {
  const W = 420, H = 230;
  const { tk, y } = frame(days.map((d) => d.pnl), W, H);
  const slot = (W - PAD.l - PAD.r) / Math.max(1, days.length), bw = Math.max(2, Math.min(22, slot * 0.7));
  const x = (i: number) => PAD.l + slot * (i + 0.5);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img">
      <Axes tk={tk} y={y} W={W} labels={pick(days.length).map((i) => ({ x: x(i), s: md(days[i].session) }))} />
      {days.map((d, i) => (
        <rect key={d.session} x={x(i) - bw / 2} width={bw} y={Math.min(y(0), y(d.pnl))} height={Math.max(1, Math.abs(y(d.pnl) - y(0)))}
          rx="2" fill={d.pnl >= 0 ? "var(--good)" : "var(--bad)"}><title>{d.session}: {money(d.pnl)}</title></rect>
      ))}
    </svg>
  );
}

export function StrategyAnalysis({ sid }: { sid: string }) {
  const t = useT();
  const [range, setRange] = useState<Range>("ALL");
  const { data, error } = usePoll<Perf>(`/api/performance/strategies?range=${range}`, 15000);

  const row = data?.strategies.find((r) => r.sid === sid);
  const trips = (data?.trades ?? []).filter((x) => x.sid === sid);
  const days = (data?.daily ?? []).filter((d) => sid in d.by_strategy).map((d) => ({ session: d.session, pnl: d.by_strategy[sid] }));

  const wins = trips.filter((x) => x.pnl > 0), losses = trips.filter((x) => x.pnl < 0);
  const gp = wins.reduce((a, x) => a + x.pnl, 0), gl = -losses.reduce((a, x) => a + x.pnl, 0);
  const winRate = trips.length ? wins.length / trips.length : null;
  const pf = gl > 0 ? gp / gl : gp > 0 ? Infinity : null;
  const avgW = wins.length ? gp / wins.length : 0, avgL = losses.length ? gl / losses.length : 0;
  const wl = avgL > 0 ? avgW / avgL : avgW > 0 ? Infinity : null;
  const dWin = days.filter((d) => d.pnl > 0).length, dLoss = days.filter((d) => d.pnl < 0).length;
  const dayRate = days.length ? dWin / days.length : null;
  const net = row?.range_pnl ?? days.reduce((a, d) => a + d.pnl, 0);

  // drawdown and consistency from the session P&L path
  let run = 0, peak = 0, dd = 0;
  for (const d of days) { run += d.pnl; peak = Math.max(peak, run); dd = Math.max(dd, peak - run); }
  const maxDD = Math.max(dd, row?.max_dd_usd ?? 0);
  const posDays = days.filter((d) => d.pnl > 0).map((d) => d.pnl), posSum = posDays.reduce((a, v) => a + v, 0);
  const consistency = posSum > 0 ? clamp((1 - Math.max(...posDays) / posSum) * 100 / 0.8) : 0;
  const recovery = maxDD > 0 ? net / maxDD : net > 0 ? Infinity : 0;
  const fin = (v: number | null, cap: number) => (v == null ? 0 : v === Infinity ? 100 : clamp((v / cap) * 100));

  const axes = [
    { label: t("Win %"), v: fin(winRate, 0.6) },
    { label: t("Profit factor"), v: fin(pf, 2.6) },
    { label: t("Avg win/loss"), v: fin(wl, 2.6) },
    { label: t("Recovery factor"), v: fin(recovery, 3.5) },
    { label: t("Max drawdown"), v: peak > 0 ? clamp((1 - maxDD / peak) * 100) : net >= 0 && maxDD === 0 && trips.length ? 100 : 0 },
    { label: t("Consistency"), v: consistency },
  ];
  const weights = [0.15, 0.25, 0.2, 0.1, 0.2, 0.1];
  const score = axes.reduce((a, x, i) => a + x.v * weights[i], 0);

  return (
    <Card className="mt an-card">
      <div className="row between" style={{ flexWrap: "wrap", gap: 8, marginBottom: 12 }}>
        <h2 style={{ margin: 0 }}>{t("Strategy analysis")}</h2>
        <div className="row" style={{ gap: 8 }}>
          {data && <span className="muted">{data.lo} – {data.hi}</span>}
          <div className="seg" role="group" aria-label={t("Date range")}>
            {RANGES.map((r) => (
              <button key={r} className={cls(range === r && "on")} aria-pressed={range === r} onClick={() => setRange(r)}>{t(r === "ALL" ? "All" : r)}</button>
            ))}
          </div>
        </div>
      </div>
      {error && <Notice kind="bad">{error}</Notice>}
      {data && !trips.length && !days.length ? <Empty>{t("No trades for this strategy in this range yet.")}</Empty> : data && (
        <>
          <div className="an-tiles">
            <Tile label={t("Net P&L")} value={money(net)} valueClass={pnlClass(net)} title={t("{n} trades", { n: trips.length })}>
              <span className="muted">{t("{n} trades", { n: trips.length })}</span>
            </Tile>
            <Tile label={t("Trade win %")} value={pctTxt(winRate)}><Gauge win={wins.length} even={trips.length - wins.length - losses.length} loss={losses.length} /></Tile>
            <Tile label={t("Profit factor")} value={ratio(pf)}><Ring gp={gp} gl={gl} /></Tile>
            <Tile label={t("Day win %")} value={pctTxt(dayRate)}><Gauge win={dWin} even={days.length - dWin - dLoss} loss={dLoss} /></Tile>
            <Tile label={t("Avg win/loss trade")} value={ratio(wl)}>
              <div className="an-wl">
                <div className="an-wl-bar"><span style={{ flex: avgW || 0.0001 }} className="g" /><span style={{ flex: avgL || 0.0001 }} className="r" /></div>
                <div className="row between"><span className="pos">{compact(avgW)}</span><span className="neg">-{compact(avgL).replace("-", "")}</span></div>
              </div>
            </Tile>
          </div>
          <div className="an-grid">
            <div className="an-panel">
              <h3>{t("Strategy score")}</h3>
              <Radar axes={axes} />
              <div className="an-score">
                <span className="muted">{t("Score")}</span>
                <div className="an-score-bar"><span style={{ left: `${score}%` }} /></div>
                <b>{score.toFixed(1)}</b>
              </div>
            </div>
            <div className="an-panel"><h3>{t("Daily net cumulative P&L")}</h3>{days.length ? <Cumulative days={days} /> : <Empty>{t("No sessions yet.")}</Empty>}</div>
            <div className="an-panel"><h3>{t("Net daily P&L")}</h3>{days.length ? <DailyBars days={days} /> : <Empty>{t("No sessions yet.")}</Empty>}</div>
          </div>
        </>
      )}
    </Card>
  );
}

"use client";
// Per-strategy performance from /api/performance/strategies: an in-depth table, a combined view of any set
// of strategies, a cross-section by strategy and breakdowns of their trades. Combined figures are computed
// here from the per-strategy P&L paths and the round-trip trades, so they always add up to the parts.
import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { usePoll } from "@/lib/hooks";
import { cls, money, num, pct, pnlClass } from "@/lib/format";
import { Card, Empty, Notice, Stat } from "./ui";
import { useI18n } from "@/lib/i18n";

type Range = "1W" | "1M" | "3M" | "YTD" | "ALL";
const RANGES: Range[] = ["1W", "1M", "3M", "YTD", "ALL"];
const ET = "America/New_York";

interface Row {
  sid: string; name: string | null; hash: string | null; version: number | null; registry_status: string | null; kind: string | null;
  fund_status: string | null; allocation_usd: number | null; base_usd: number | null; base_ts: number | null; deployed: number | null;
  since_set: number | null; since_set_pct: number | null; today: number | null; drawdown_now: number | null; max_drawdown_stop: number | null;
  realized_total: number; unrealized: number; lifetime: number; open_positions: number;
  range_pnl: number; range_realized: number; range_return: number | null; contribution: number | null;
  max_dd_usd: number; max_dd_pct: number | null; max_dd_at: number | null; fees: number;
}
interface Trip {
  id: number; sid: string; symbol: string; side: string; qty: number; pnl: number; entry_ts: number | null; exit_ts: number;
  exit_kind: string; entry_price: number | null; exit_price: number | null; r: number | null; fees: number; fills: number;
}
interface Perf {
  range: Range; lo: string; hi: string; open_now: boolean; base_equity: number | null; equity: number | null;
  strategies: Row[]; trades: Trip[]; daily: { session: string; by_strategy: Record<string, number> }[];
  ts: number[]; series: Record<string, number[]>; fees_note: string;
  positions: { sid: string; symbol: string; qty: number; avg_price: number; last_price: number; unrealized: number }[];
}

const color = (i: number) => `var(--c${(i % 8) + 1})`;

/** Trade statistics of a set of round trips. */
function tradeStats(trips: Trip[]) {
  const wins = trips.filter((x) => x.pnl > 0), losses = trips.filter((x) => x.pnl < 0);
  const gp = wins.reduce((a, x) => a + x.pnl, 0), gl = -losses.reduce((a, x) => a + x.pnl, 0);
  const rs = trips.filter((x) => x.r != null);
  const held = trips.filter((x) => x.entry_ts);
  return {
    n: trips.length, wins: wins.length, losses: losses.length,
    winRate: trips.length ? wins.length / trips.length : null,
    pf: gl > 0 ? gp / gl : gp > 0 ? Infinity : null,
    expectancy: trips.length ? (gp - gl) / trips.length : null,
    avgWin: wins.length ? gp / wins.length : null, avgLoss: losses.length ? -gl / losses.length : null,
    best: trips.length ? Math.max(...trips.map((x) => x.pnl)) : null, worst: trips.length ? Math.min(...trips.map((x) => x.pnl)) : null,
    avgR: rs.length ? rs.reduce((a, x) => a + (x.r as number), 0) / rs.length : null,
    avgHold: held.length ? held.reduce((a, x) => a + (x.exit_ts - (x.entry_ts as number)), 0) / held.length : null,
    fees: trips.reduce((a, x) => a + (x.fees || 0), 0),
  };
}

function maxDrawdown(vals: number[]) {
  let peak = 0, dd = 0;
  for (const v of vals) { peak = Math.max(peak, v); dd = Math.max(dd, peak - v); }
  return dd;
}

const sumNull = (xs: (number | null | undefined)[]) => (xs.some((x) => x != null) ? xs.reduce<number>((a, x) => a + (x ?? 0), 0) : null);

function hold(sec: number | null, t: (s: string, v?: any) => string) {
  if (sec == null) return "—";
  if (sec < 3600) return t("{n}m", { n: Math.round(sec / 60) });
  if (sec < 86400 * 2) return t("{n}h", { n: (sec / 3600).toFixed(1) });
  return t("{n}d", { n: (sec / 86400).toFixed(1) });
}

/** Round axis steps (1, 2, 5 × 10^n) spanning [lo, hi]; at least $10 wide. */
function niceTicks(lo: number, hi: number, count = 5) {
  if (hi - lo < 10) { const mid = (hi + lo) / 2; lo = mid - 5; hi = mid + 5; }
  const raw = (hi - lo) / count, mag = 10 ** Math.floor(Math.log10(raw)), n = raw / mag;
  const step = (n < 1.5 ? 1 : n < 3 ? 2 : n < 7 ? 5 : 10) * mag;
  const out: number[] = [];
  for (let v = Math.floor(lo / step) * step; v < hi + step * 0.999; v += step) out.push(+v.toPrecision(12));
  return out;
}

const compactUsd = (v: number) => {
  const a = Math.abs(v), s = v < 0 ? "-" : "";
  return a >= 1e6 ? `${s}$${+(a / 1e6).toFixed(2)}M` : a >= 1e4 ? `${s}$${+(a / 1e3).toFixed(1)}k` : `${s}$${Math.round(a).toLocaleString("en-US")}`;
};

const pf = (v: number | null) => (v == null ? "—" : v === Infinity ? "∞" : v.toFixed(2));

export function StrategyPerformance({ compact = false, defaultRange = "ALL" }: { compact?: boolean; defaultRange?: Range }) {
  const [range, setRange] = useState<Range>(defaultRange);
  const { data, error, loading } = usePoll<Perf>(`/api/performance/strategies?range=${range}`, 15000);
  const [selected, setSelected] = useState<Set<string> | null>(null); // null = every strategy
  const [view, setView] = useState<"table" | "cross" | "breakdown">("table");
  const { t } = useI18n();

  const rows = data?.strategies ?? [];
  const idx = useMemo(() => Object.fromEntries(rows.map((r, i) => [r.sid, i])), [rows]);
  const sel = useMemo(() => selected ?? new Set(rows.map((r) => r.sid)), [selected, rows]);
  const chosen = rows.filter((r) => sel.has(r.sid));
  const trips = (data?.trades ?? []).filter((x) => sel.has(x.sid));

  const toggle = (sid: string) => {
    const s = new Set(sel);
    if (s.has(sid)) s.delete(sid); else s.add(sid);
    setSelected(s);
  };

  return (
    <Card className="perf-card">
      <div className="row between" style={{ marginBottom: 8, flexWrap: "wrap", gap: 8 }}>
        <h2 style={{ margin: 0 }}>{t("Strategy performance")}</h2>
        {data && <span className="muted">{data.lo} – {data.hi}</span>}
      </div>
      <div className="filters">
        <div className="seg" role="group" aria-label={t("Date range")}>
          {RANGES.map((r) => (
            <button key={r} className={cls(range === r && "on")} aria-pressed={range === r} onClick={() => setRange(r)}>{t(r === "ALL" ? "All" : r)}</button>
          ))}
        </div>
        {!compact && (
          <div className="seg" role="group" aria-label={t("View")}>
            {([["table", "Table"], ["cross", "Cross-section"], ["breakdown", "Breakdowns"]] as const).map(([k, l]) => (
              <button key={k} className={cls(view === k && "on")} aria-pressed={view === k} onClick={() => setView(k)}>{t(l)}</button>
            ))}
          </div>
        )}
        <span style={{ flex: 1 }} />
        <span className="muted" style={{ fontSize: 12 }}>{t("{n} of {total} selected", { n: chosen.length, total: rows.length })}</span>
        <button onClick={() => setSelected(null)}>{t("All")}</button>
        <button onClick={() => setSelected(new Set())}>{t("None")}</button>
      </div>
      {error && <Notice kind="bad">{error}</Notice>}
      {!data ? <p className="muted">{t("Loading…")}</p> : !rows.length ? <Empty>{t("No strategy has traded yet.")}</Empty> : (
        <div className={cls(loading && "loading")} style={{ opacity: loading ? 0.6 : 1, transition: "opacity .15s" }}>
          <Combined d={data} chosen={chosen} trips={trips} idx={idx} compact={compact} />
          {(compact || view === "table") && <StrategyTable d={data} sel={sel} toggle={toggle} idx={idx} compact={compact} />}
          {!compact && view === "cross" && <CrossSection d={data} chosen={chosen} trips={trips} idx={idx} />}
          {!compact && view === "breakdown" && <Breakdowns d={data} chosen={chosen} trips={trips} idx={idx} />}
          <p className="muted" style={{ fontSize: 12, margin: "8px 0 0" }}>
            {t("Realized = closed trades; open = positions marked at the last broker sync. Since set = P&L since the strategy's allocation was last set or resumed (a position held from before counts only for its move after that). Range figures use the broker's per-minute marks, so a position held overnight is counted once. Return = range P&L / allocation; contribution = range P&L / account equity at the range start. A trade is one entry with all its exit fills.")}{" "}
            {t("Alpaca paper charges no commission or regulatory fees, so fees are $0 on every fill.")}
            {compact && <> <Link href="/portfolio">{t("Full breakdown on Portfolio")} →</Link></>}
          </p>
        </div>
      )}
    </Card>
  );
}

/** Combined P&L of the selected strategies: summary tiles and a chart of each line plus their sum. */
function Combined({ d, chosen, trips, idx, compact }: { d: Perf; chosen: Row[]; trips: Trip[]; idx: Record<string, number>; compact: boolean }) {
  const { t } = useI18n();
  const total = useMemo(() => d.ts.map((_, k) => chosen.reduce((a, r) => a + (d.series[r.sid]?.[k] ?? 0), 0)), [d, chosen]);
  if (!chosen.length) return <Notice kind="warn">{t("Select one or more strategies to see their combined performance.")}</Notice>;
  const st = tradeStats(trips);
  const rangePnl = chosen.reduce((a, r) => a + r.range_pnl, 0);
  const cap = sumNull(chosen.map((r) => r.base_usd));
  const since = sumNull(chosen.map((r) => r.since_set));
  return (
    <>
      <div className="grid g4" style={{ marginBottom: 10 }}>
        <Stat label={t(chosen.length === 1 ? "Range P&L" : "Combined range P&L")} value={money(rangePnl, true)} valueClass={pnlClass(rangePnl)}
          sub={t("{ret} of allocation · {c} of account", { ret: pct(cap ? rangePnl / cap : null, 2, true), c: pct(d.base_equity ? rangePnl / d.base_equity : null, 2, true) })} />
        <Stat label={t("Realized / open now")} value={<span><span className={pnlClass(sumNull(chosen.map((r) => r.realized_total)))}>{money(chosen.reduce((a, r) => a + r.realized_total, 0), true)}</span> / <span className={pnlClass(chosen.reduce((a, r) => a + r.unrealized, 0))}>{money(chosen.reduce((a, r) => a + r.unrealized, 0), true)}</span></span>}
          sub={t("since set {v}", { v: money(since, true) })} />
        <Stat label={t("Trades · win rate")} value={`${st.n} · ${pct(st.winRate, 0)}`} sub={t("profit factor {pf} · avg {e}", { pf: pf(st.pf), e: money(st.expectancy, true) })} />
        <Stat label={t("Max drawdown in range")} value={money(-maxDrawdown(total))} valueClass={maxDrawdown(total) > 0 ? "neg" : ""}
          sub={t("{pct} of allocation", { pct: pct(cap ? maxDrawdown(total) / cap : null, 1) })} />
      </div>
      {!compact && d.ts.length > 1 && <PnlLines d={d} chosen={chosen} total={total} idx={idx} />}
    </>
  );
}

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [w, setW] = useState(800);
  useEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(300, Math.floor(e.contentRect.width))));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return [ref, w] as const;
}

/** Cumulative P&L ($) of each selected strategy over the range, and their sum. */
function PnlLines({ d, chosen, total, idx, height = 260 }: { d: Perf; chosen: Row[]; total: number[]; idx: Record<string, number>; height?: number }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const { t, lang } = useI18n();
  const series = [
    ...chosen.map((r) => ({ key: r.sid, color: color(idx[r.sid]), values: d.series[r.sid] ?? [], w: 1.6 })),
    ...(chosen.length > 1 ? [{ key: t("Combined"), color: "var(--ink)", values: total, w: 2.6 }] : []),
  ];
  const n = d.ts.length;
  const all = series.flatMap((s) => s.values).concat(0);
  const ticks = niceTicks(Math.min(...all), Math.max(...all));
  const lo = ticks[0], hi = ticks[ticks.length - 1];
  const ml = 70, mr = 12, mt = 10, mb = 24;
  const iw = Math.max(50, width - ml - mr);
  const x = (i: number) => ml + (n <= 1 ? iw / 2 : (iw * i) / (n - 1));
  const y = (v: number) => mt + (height - mt - mb) * (1 - (v - lo) / (hi - lo));
  const path = (vals: number[]) => vals.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");
  const day = (ts: number) => new Date(ts * 1000).toLocaleDateString(lang === "zh" ? "zh-CN" : "en-US", { timeZone: ET, month: "short", day: "numeric" });
  const xt: number[] = [];
  let lastX = -1e9, lastDay = "";
  d.ts.forEach((ts, i) => {
    const dd = day(ts);
    if (dd !== lastDay && x(i) - lastX >= 80) { xt.push(i); lastX = x(i); }
    lastDay = dd;
  });
  return (
    <div ref={ref} className="perf" style={{ marginBottom: 10 }}>
      <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label={t("Cumulative P&L by strategy")}
        onPointerMove={(e) => {
          const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
          setHover(Math.max(0, Math.min(n - 1, Math.round(((e.clientX - r.left - ml) / iw) * (n - 1)))));
        }} onPointerLeave={() => setHover(null)}>
        <g className="grid">{ticks.map((v) => <line key={v} x1={ml} x2={ml + iw} y1={y(v)} y2={y(v)} />)}</g>
        <line className="zero" x1={ml} x2={ml + iw} y1={y(0)} y2={y(0)} />
        {ticks.map((v) => <text key={v} x={ml - 8} y={y(v) + 4} textAnchor="end" className="tick">{compactUsd(v)}</text>)}
        {xt.map((i) => <text key={i} x={x(i)} y={height - 6} textAnchor={x(i) > ml + iw - 30 ? "end" : x(i) < ml + 30 ? "start" : "middle"} className="tick">{day(d.ts[i])}</text>)}
        {series.map((s) => <path key={s.key} d={path(s.values)} fill="none" stroke={s.color} strokeWidth={s.w} strokeLinejoin="round" />)}
        {hover != null && <line className="xhair" x1={x(hover)} x2={x(hover)} y1={mt} y2={height - mb} />}
        {hover != null && series.map((s) => <circle key={s.key} cx={x(hover)} cy={y(s.values[hover] ?? 0)} r={3.5} fill={s.color} stroke="var(--panel)" strokeWidth={1.5} />)}
      </svg>
      {hover != null && (
        <div className="tip" style={{ left: Math.min(x(hover) + 12, width - 220), top: 8 }}>
          <div className="when">{new Date(d.ts[hover] * 1000).toLocaleString(lang === "zh" ? "zh-CN" : "en-US", { timeZone: ET, month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false })} {t("ET")}</div>
          {[...series].reverse().map((s) => (
            <div className="r" key={s.key}><span className="key" style={{ borderColor: s.color }} /><span><b className={pnlClass(s.values[hover])}>{money(s.values[hover], true)}</b> <span className="muted">{s.key}</span></span></div>
          ))}
        </div>
      )}
    </div>
  );
}

type SortKey = "sid" | "realized_total" | "unrealized" | "since_set" | "range_pnl" | "range_return" | "contribution" | "trades" | "winRate" | "pf" | "expectancy" | "avgR" | "max_dd_usd";

function StrategyTable({ d, sel, toggle, idx, compact }: { d: Perf; sel: Set<string>; toggle: (s: string) => void; idx: Record<string, number>; compact: boolean }) {
  const { t } = useI18n();
  const [sort, setSort] = useState<{ k: SortKey; desc: boolean }>({ k: "range_pnl", desc: true });
  const byStats = useMemo(() => {
    const m: Record<string, ReturnType<typeof tradeStats>> = {};
    for (const r of d.strategies) m[r.sid] = tradeStats(d.trades.filter((x) => x.sid === r.sid));
    return m;
  }, [d]);
  const val = (r: Row): number | string | null => {
    const s = byStats[r.sid];
    switch (sort.k) {
      case "sid": return r.sid;
      case "trades": return s.n;
      case "winRate": return s.winRate;
      case "pf": return s.pf;
      case "expectancy": return s.expectancy;
      case "avgR": return s.avgR;
      default: return r[sort.k] as number | null;
    }
  };
  const rows = [...d.strategies].sort((a, b) => {
    const va = val(a), vb = val(b);
    if (va == null && vb == null) return 0;
    if (va == null) return 1;
    if (vb == null) return -1;
    const c = typeof va === "string" ? va.localeCompare(vb as string) : (va as number) - (vb as number);
    return sort.desc ? -c : c;
  });
  const chosen = d.strategies.filter((r) => sel.has(r.sid));
  const comb = tradeStats(d.trades.filter((x) => sel.has(x.sid)));
  const combSeries = d.ts.map((_, k) => chosen.reduce((a, r) => a + (d.series[r.sid]?.[k] ?? 0), 0));
  const combCap = sumNull(chosen.map((r) => r.base_usd));
  const combRange = chosen.reduce((a, r) => a + r.range_pnl, 0);
  const H = ({ k, children, title }: { k: SortKey; children: React.ReactNode; title?: string }) => (
    <th className={k === "sid" ? "" : "num"} title={title} style={{ cursor: "pointer", whiteSpace: "nowrap" }} aria-sort={sort.k === k ? (sort.desc ? "descending" : "ascending") : "none"}
      onClick={() => setSort({ k, desc: sort.k === k ? !sort.desc : k !== "sid" })}>
      {children}{sort.k === k ? (sort.desc ? " ▾" : " ▴") : ""}
    </th>
  );
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th aria-label={t("Select")} />
            <H k="sid">{t("Strategy")}</H>
            <H k="realized_total" title={t("All closed trades, lifetime")}>{t("Realized")}</H>
            <H k="unrealized" title={t("Open positions marked at the last broker sync")}>{t("Open")}</H>
            <H k="since_set" title={t("P&L since the allocation was last set or resumed")}>{t("Since set")}</H>
            <H k="range_pnl">{t("Range P&L")}</H>
            <H k="range_return" title={t("Range P&L / allocation")}>{t("Return")}</H>
            {!compact && <H k="contribution" title={t("Range P&L / account equity at the range start")}>{t("Contribution")}</H>}
            <H k="trades">{t("Trades")}</H>
            <H k="winRate">{t("Win rate")}</H>
            {!compact && <H k="pf">{t("Profit factor")}</H>}
            {!compact && <H k="expectancy" title={t("Average P&L per trade")}>{t("Avg trade")}</H>}
            {!compact && <H k="avgR">{t("Avg R")}</H>}
            {!compact && <th className="num">{t("Fees")}</th>}
            <H k="max_dd_usd" title={t("Largest fall from a high of the range P&L path")}>{t("Max drawdown")}</H>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const s = byStats[r.sid];
            const on = sel.has(r.sid);
            return (
              <tr key={r.sid} className={on ? "" : "dim"}>
                <td><input type="checkbox" checked={on} onChange={() => toggle(r.sid)} aria-label={t("Include {sid}", { sid: r.sid })} /></td>
                <td style={{ whiteSpace: "nowrap" }}>
                  <span className="key" style={{ borderColor: color(idx[r.sid]), marginRight: 6 }} />
                  {r.hash ? <Link href={`/strategies/${r.hash}`}><b>{r.sid}</b></Link> : <b>{r.sid === "unmanaged" ? t("unmanaged") : r.sid}</b>}
                  <div className="muted" style={{ fontSize: 12 }}>
                    {[r.kind && t(r.kind), r.version && `v${r.version}`, r.registry_status && t(r.registry_status), r.fund_status && r.fund_status !== "active" && t(r.fund_status), r.open_positions ? t("{n} open", { n: r.open_positions }) : null].filter(Boolean).join(" · ")}
                  </div>
                </td>
                <td className={`num ${pnlClass(r.realized_total)}`}>{money(r.realized_total, true)}</td>
                <td className={`num ${pnlClass(r.unrealized)}`}>{money(r.unrealized, true)}</td>
                <td className={`num ${pnlClass(r.since_set)}`}>{money(r.since_set, true)}{r.since_set_pct != null && <div className="muted" style={{ fontSize: 12 }}>{pct(r.since_set_pct, 1, true)}</div>}</td>
                <td className={`num ${pnlClass(r.range_pnl)}`}><b>{money(r.range_pnl, true)}</b>{!compact && <div className="muted" style={{ fontSize: 12 }}>{t("realized {v}", { v: money(r.range_realized, true) })}</div>}</td>
                <td className={`num ${pnlClass(r.range_return)}`}>{pct(r.range_return, 2, true)}</td>
                {!compact && <td className={`num ${pnlClass(r.contribution)}`}>{pct(r.contribution, 2, true)}</td>}
                <td className="num">{s.n}{!compact && s.n > 0 && <div className="muted" style={{ fontSize: 12 }}>{s.wins}W / {s.losses}L</div>}</td>
                <td className="num">{pct(s.winRate, 0)}</td>
                {!compact && <td className="num">{pf(s.pf)}</td>}
                {!compact && <td className={`num ${pnlClass(s.expectancy)}`}>{money(s.expectancy, true)}</td>}
                {!compact && <td className="num">{num(s.avgR, 2, true)}</td>}
                {!compact && <td className="num">{money(r.fees)}</td>}
                <td className={`num ${r.max_dd_usd > 0 ? "neg" : ""}`}>{money(-r.max_dd_usd)}{r.max_dd_pct != null && <div className="muted" style={{ fontSize: 12 }}>{pct(r.max_dd_pct, 1)}</div>}</td>
              </tr>
            );
          })}
          {chosen.length > 1 && (
            <tr>
              <td />
              <td><span className="key" style={{ borderColor: "var(--ink)", borderTopWidth: 3, marginRight: 6 }} /><b>{t("Combined ({n})", { n: chosen.length })}</b></td>
              <td className={`num ${pnlClass(chosen.reduce((a, r) => a + r.realized_total, 0))}`}><b>{money(chosen.reduce((a, r) => a + r.realized_total, 0), true)}</b></td>
              <td className={`num ${pnlClass(chosen.reduce((a, r) => a + r.unrealized, 0))}`}><b>{money(chosen.reduce((a, r) => a + r.unrealized, 0), true)}</b></td>
              <td className={`num ${pnlClass(sumNull(chosen.map((r) => r.since_set)))}`}><b>{money(sumNull(chosen.map((r) => r.since_set)), true)}</b></td>
              <td className={`num ${pnlClass(combRange)}`}><b>{money(combRange, true)}</b></td>
              <td className={`num ${pnlClass(combRange)}`}><b>{pct(combCap ? combRange / combCap : null, 2, true)}</b></td>
              {!compact && <td className={`num ${pnlClass(combRange)}`}><b>{pct(d.base_equity ? combRange / d.base_equity : null, 2, true)}</b></td>}
              <td className="num"><b>{comb.n}</b></td>
              <td className="num"><b>{pct(comb.winRate, 0)}</b></td>
              {!compact && <td className="num"><b>{pf(comb.pf)}</b></td>}
              {!compact && <td className={`num ${pnlClass(comb.expectancy)}`}><b>{money(comb.expectancy, true)}</b></td>}
              {!compact && <td className="num"><b>{num(comb.avgR, 2, true)}</b></td>}
              {!compact && <td className="num"><b>{money(comb.fees)}</b></td>}
              <td className="num neg"><b>{money(-maxDrawdown(combSeries))}</b></td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

const METRICS = [
  ["range_pnl", "Range P&L"], ["range_return", "Return"], ["since_set", "Since set"], ["realized_total", "Realized"],
  ["unrealized", "Open"], ["winRate", "Win rate"], ["trades", "Trades"], ["expectancy", "Avg trade"], ["max_dd_usd", "Max drawdown"],
] as const;
type Metric = (typeof METRICS)[number][0];

/** One metric across the selected strategies, as diverging bars, plus a strategy × symbol P&L grid. */
function CrossSection({ d, chosen, trips, idx }: { d: Perf; chosen: Row[]; trips: Trip[]; idx: Record<string, number> }) {
  const { t } = useI18n();
  const [metric, setMetric] = useState<Metric>("range_pnl");
  const value = (r: Row): number | null => {
    const s = tradeStats(trips.filter((x) => x.sid === r.sid));
    if (metric === "winRate") return s.winRate;
    if (metric === "trades") return s.n;
    if (metric === "expectancy") return s.expectancy;
    if (metric === "max_dd_usd") return -r.max_dd_usd;
    return r[metric];
  };
  const fmt = (v: number | null) => metric === "range_return" || metric === "winRate" ? pct(v, metric === "winRate" ? 0 : 2, metric !== "winRate")
    : metric === "trades" ? (v == null ? "—" : String(v)) : money(v, true);
  const vals = chosen.map((r) => ({ r, v: value(r) })).sort((a, b) => (b.v ?? -Infinity) - (a.v ?? -Infinity));
  const maxAbs = Math.max(...vals.map((x) => Math.abs(x.v ?? 0)), 1e-9);
  const signed = vals.some((x) => (x.v ?? 0) < 0);

  // closed trades in the range plus, when the range reaches today, positions still open (marked now, since entry)
  const reachesNow = d.open_now;
  const symbols = useMemo(() => {
    const m: Record<string, Record<string, number>> = {};
    const open = new Set<string>();
    const add = (sym: string, sid: string, v: number) => { (m[sym] ??= {})[sid] = (m[sym][sid] ?? 0) + v; };
    for (const x of trips) add(x.symbol, x.sid, x.pnl);
    if (reachesNow) for (const p of d.positions ?? []) if (chosen.some((r) => r.sid === p.sid)) { add(p.symbol, p.sid, p.unrealized); open.add(`${p.symbol}|${p.sid}`); }
    return Object.entries(m).map(([sym, by]) => ({ sym, by, open, total: Object.values(by).reduce((a, v) => a + v, 0) }))
      .sort((a, b) => Math.abs(b.total) - Math.abs(a.total));
  }, [trips, d, chosen, reachesNow]);
  const cellMax = Math.max(...symbols.flatMap((s) => Object.values(s.by).map(Math.abs)), 1e-9);
  const cols = chosen.filter((r) => symbols.some((s) => s.by[r.sid] != null));

  if (!chosen.length) return null;
  return (
    <>
      <div className="filters">
        <span className="muted">{t("Compare")}</span>
        <select value={metric} onChange={(e) => setMetric(e.target.value as Metric)} aria-label={t("Metric")}>
          {METRICS.map(([k, l]) => <option key={k} value={k}>{t(l)}</option>)}
        </select>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "minmax(120px, max-content) 1fr minmax(80px, max-content)", gap: "6px 10px", alignItems: "center", marginBottom: 16 }}>
        {vals.map(({ r, v }) => {
          const w = (Math.abs(v ?? 0) / maxAbs) * (signed ? 50 : 100);
          return (
            <Fragment key={r.sid}>
              <span style={{ whiteSpace: "nowrap" }}><span className="key" style={{ borderColor: color(idx[r.sid]), marginRight: 6 }} />{r.sid}</span>
              <div style={{ position: "relative", height: 14 }}>
                {signed && <div style={{ position: "absolute", left: "50%", top: -2, bottom: -2, width: 1, background: "var(--border)" }} />}
                <div title={fmt(v)} style={{ position: "absolute", top: 1, height: 12, borderRadius: 3, background: color(idx[r.sid]),
                  left: signed ? ((v ?? 0) >= 0 ? "50%" : `${50 - w}%`) : 0, width: `${w}%` }} />
              </div>
              <span className={`num ${metric === "trades" || metric === "winRate" ? "" : pnlClass(v)}`} style={{ textAlign: "right" }}>{fmt(v)}</span>
            </Fragment>
          );
        })}
      </div>
      <h3>{t("P&L by symbol and strategy")}</h3>
      <p className="muted" style={{ fontSize: 12, marginTop: -4 }}>{t("Closed trades in the range, plus open positions marked now (since entry, shown in italics).")}</p>
      {!symbols.length ? <Empty>{t("No closed trades in this range.")}</Empty> : (
        <div className="table-wrap" style={{ maxHeight: 420, overflowY: "auto" }}>
          <table>
            <thead><tr><th>{t("Symbol")}</th>{cols.map((r) => <th key={r.sid} className="num">{r.sid}</th>)}<th className="num">{t("Total")}</th></tr></thead>
            <tbody>
              {symbols.map((s) => (
                <tr key={s.sym}>
                  <td><b>{s.sym}</b></td>
                  {cols.map((r) => {
                    const v = s.by[r.sid];
                    const a = v == null ? 0 : Math.min(0.45, (Math.abs(v) / cellMax) * 0.45);
                    return <td key={r.sid} className={`num ${v == null ? "muted" : pnlClass(v)}`}
                      style={{ fontStyle: s.open.has(`${s.sym}|${r.sid}`) ? "italic" : undefined,
                        background: v == null ? undefined : `color-mix(in srgb, var(${v >= 0 ? "--good" : "--bad"}) ${Math.round(a * 100)}%, transparent)` }}>
                      {v == null ? "·" : money(v, true)}</td>;
                  })}
                  <td className={`num ${pnlClass(s.total)}`}><b>{money(s.total, true)}</b></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

/** The selected strategies' trades by exit reason, weekday, entry hour, holding time and side; session P&L; best and worst trades. */
function Breakdowns({ d, chosen, trips, idx }: { d: Perf; chosen: Row[]; trips: Trip[]; idx: Record<string, number> }) {
  const { t, lang } = useI18n();
  if (!chosen.length) return null;
  const etParts = (ts: number) => {
    const p = new Intl.DateTimeFormat("en-US", { timeZone: ET, weekday: "short", hour: "2-digit", hour12: false }).formatToParts(new Date(ts * 1000));
    return { wd: p.find((x) => x.type === "weekday")?.value ?? "", h: Number(p.find((x) => x.type === "hour")?.value ?? 0) % 24 };
  };
  const group = (key: (x: Trip) => string | null, order?: string[]) => {
    const m: Record<string, Trip[]> = {};
    for (const x of trips) { const k = key(x); if (k != null) (m[k] ??= []).push(x); }
    const keys = order ? order.filter((k) => m[k]) : Object.keys(m).sort((a, b) => m[b].reduce((s, x) => s + x.pnl, 0) - m[a].reduce((s, x) => s + x.pnl, 0));
    return keys.map((k) => ({ k, ...tradeStats(m[k]), pnl: m[k].reduce((s, x) => s + x.pnl, 0) }));
  };
  const holdBucket = (x: Trip) => {
    if (!x.entry_ts) return null;
    const s = x.exit_ts - x.entry_ts;
    return s < 900 ? "< 15m" : s < 3600 ? "15m – 1h" : s < 6.5 * 3600 ? "1h – 1 session" : s < 5 * 86400 ? "1 – 5 days" : "> 5 days";
  };
  const tables: [string, ReturnType<typeof group>, (k: string) => string][] = [
    ["By exit reason", group((x) => x.exit_kind || "—"), (k) => t(k)],
    ["By weekday (exit)", group((x) => etParts(x.exit_ts).wd, ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]), (k) => t(k)],
    ["By entry hour (ET)", group((x) => (x.entry_ts ? `${String(etParts(x.entry_ts).h).padStart(2, "0")}:00` : null), Array.from({ length: 24 }, (_, h) => `${String(h).padStart(2, "0")}:00`)), (k) => k],
    ["By holding time", group(holdBucket, ["< 15m", "15m – 1h", "1h – 1 session", "1 – 5 days", "> 5 days"]), (k) => t(k)],
    ["By side", group((x) => x.side || "long"), (k) => t(k)],
  ];
  const best = trips.filter((x) => x.pnl > 0).sort((a, b) => b.pnl - a.pnl).slice(0, 5);
  const worst = trips.filter((x) => x.pnl < 0).sort((a, b) => a.pnl - b.pnl).slice(0, 5);
  const sessions = d.daily.filter((s) => chosen.some((r) => s.by_strategy[r.sid] != null)).reverse();
  const cols = chosen.filter((r) => sessions.some((s) => s.by_strategy[r.sid] != null));
  const dayMax = Math.max(...sessions.flatMap((s) => cols.map((r) => Math.abs(s.by_strategy[r.sid] ?? 0))), 1e-9);
  const fmtDay = (iso: string) => new Date(iso + "T12:00:00Z").toLocaleDateString(lang === "zh" ? "zh-CN" : "en-US", { month: "short", day: "numeric", weekday: "short", timeZone: "UTC" });
  return (
    <>
      {!trips.length && <Notice kind="warn">{t("No closed trades in this range.")}</Notice>}
      <div className="grid g2">
        {tables.map(([title, g, label]) => g.length > 0 && (
          <div key={title}>
            <h3>{t(title)}</h3>
            <table>
              <thead><tr><th /><th className="num">{t("Trades")}</th><th className="num">{t("Win rate")}</th><th className="num">{t("Avg trade")}</th><th className="num">{t("P&L")}</th></tr></thead>
              <tbody>{g.map((x) => (
                <tr key={x.k}><td>{label(x.k)}</td><td className="num">{x.n}</td><td className="num">{pct(x.winRate, 0)}</td>
                  <td className={`num ${pnlClass(x.expectancy)}`}>{money(x.expectancy, true)}</td><td className={`num ${pnlClass(x.pnl)}`}>{money(x.pnl, true)}</td></tr>
              ))}</tbody>
            </table>
          </div>
        ))}
      </div>
      <h3>{t("P&L by session")}</h3>
      {!sessions.length ? <Empty>{t("No marked sessions in this range.")}</Empty> : (
        <div className="table-wrap" style={{ maxHeight: 420, overflowY: "auto" }}>
          <table>
            <thead><tr><th>{t("Session")}</th>{cols.map((r) => <th key={r.sid} className="num" style={{ whiteSpace: "nowrap" }}><span className="key" style={{ borderColor: color(idx[r.sid]), marginRight: 4 }} />{r.sid}</th>)}{cols.length > 1 && <th className="num">{t("Combined")}</th>}</tr></thead>
            <tbody>{sessions.map((s) => {
              const tot = cols.reduce((a, r) => a + (s.by_strategy[r.sid] ?? 0), 0);
              return (
                <tr key={s.session}>
                  <td style={{ whiteSpace: "nowrap" }}>{fmtDay(s.session)}</td>
                  {cols.map((r) => {
                    const v = s.by_strategy[r.sid];
                    const a = v == null ? 0 : Math.min(0.45, (Math.abs(v) / dayMax) * 0.45);
                    return <td key={r.sid} className={`num ${v == null ? "muted" : pnlClass(v)}`}
                      style={{ background: v == null ? undefined : `color-mix(in srgb, var(${v >= 0 ? "--good" : "--bad"}) ${Math.round(a * 100)}%, transparent)` }}>{v == null ? "·" : money(v, true)}</td>;
                  })}
                  {cols.length > 1 && <td className={`num ${pnlClass(tot)}`}><b>{money(tot, true)}</b></td>}
                </tr>
              );
            })}</tbody>
          </table>
        </div>
      )}
      {trips.length > 0 && (
        <div className="grid g2">
          {([["Best trades", best], ["Worst trades", worst]] as const).map(([title, list]) => list.length > 0 && (
            <div key={title} className="table-wrap" style={{ minWidth: 0 }}>
              <h3>{t(title)}</h3>
              <table><tbody>{list.map((x) => (
                <tr key={x.id}><td><Link href={`/trades/${x.id}`}>{x.symbol}</Link></td><td className="muted">{x.sid}</td><td className="muted">{t(x.exit_kind)}</td>
                  <td className="num muted">{hold(x.entry_ts ? x.exit_ts - x.entry_ts : null, t)}</td><td className={`num ${pnlClass(x.pnl)}`}>{money(x.pnl, true)}</td></tr>
              ))}</tbody></table>
            </div>
          ))}
        </div>
      )}
    </>
  );
}

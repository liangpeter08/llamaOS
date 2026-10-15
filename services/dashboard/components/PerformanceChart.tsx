"use client";
// Portfolio performance chart: account equity ($) or cumulative return (%) with a per-strategy
// overlay, over preset or custom date ranges. One y-axis only: in "return" mode every line is
// indexed to the account value at the start of the range, so strategy contributions sum to Total.
import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { usePoll } from "@/lib/hooks";
import { cls, money, pct, pnlClass } from "@/lib/format";
import { Card, Empty, Notice, Stat } from "./ui";
import { useI18n } from "@/lib/i18n";

export type Range = "1D" | "1W" | "1M" | "3M" | "YTD" | "ALL" | "custom";
type Metric = "return" | "equity";

interface EquityData {
  empty?: boolean;
  range: Range;
  lo: string;
  hi: string;
  first: string;
  last: string;
  intraday: boolean;
  base: number;
  ts: number[];
  equity: number[];
  source: string[];
  strategies: string[];
  strategy_pnl: Record<string, number[]>;
  sessions: { session: string; index: number; source: string }[];
  stats: {
    start: number; end: number; change: number; change_pct: number; high: number; low: number;
    max_drawdown: number; sessions: number; points: number;
    best_day: { session: string; change: number; change_pct: number } | null;
    worst_day: { session: string; change: number; change_pct: number } | null;
  };
  by_strategy: { strategy: string; pnl: number; return_pct: number; share: number | null }[];
  daily: { session: string; source: string; open: number; close: number; change: number; change_pct: number | null; by_strategy: Record<string, number> }[];
  replay: { strategies?: string[]; sessions?: number };
}

const ET = "America/New_York";
const color = (i: number) => `var(--c${(i % 8) + 1})`;
const LABELS: Record<Range, string> = { "1D": "1D", "1W": "1W", "1M": "1M", "3M": "3M", YTD: "YTD", ALL: "All", custom: "Custom" };

function niceTicks(lo: number, hi: number, minSpan: number, count = 5) {
  if (hi - lo < minSpan) {
    // flat or nearly flat: centre the line in a sensible minimum window instead of zooming into noise
    const mid = (hi + lo) / 2;
    lo = mid - minSpan / 2;
    hi = mid + minSpan / 2;
  }
  const raw = (hi - lo) / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const n = raw / mag;
  const step = (n < 1.5 ? 1 : n < 3 ? 2 : n < 7 ? 5 : 10) * mag;
  const ticks: number[] = [];
  for (let v = Math.floor(lo / step) * step; v <= hi + step * 0.001; v += step) ticks.push(+v.toPrecision(12));
  if (ticks[ticks.length - 1] < hi) ticks.push(+(ticks[ticks.length - 1] + step).toPrecision(12));
  return ticks;
}

let dayLocale = "en-US"; // follows the UI language (set by PerformanceChart on render)
const fmtDay = (iso: string, withYear = false) =>
  new Date(iso + "T12:00:00Z").toLocaleDateString(dayLocale, { month: "short", day: "numeric", ...(withYear ? { year: "numeric" } : {}), timeZone: "UTC" });
const fmtTime = (ts: number) => new Date(ts * 1000).toLocaleTimeString("en-GB", { timeZone: ET, hour: "2-digit", minute: "2-digit" });
const compact = (v: number) => (Math.abs(v) >= 1e6 ? `$${(v / 1e6).toFixed(2)}M` : `$${Math.round(v).toLocaleString("en-US")}`);

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [w, setW] = useState(800);
  useEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(320, Math.floor(e.contentRect.width))));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return [ref, w] as const;
}

export function PerformanceChart({
  ranges = ["1W", "1M", "3M", "YTD", "ALL", "custom"],
  defaultRange = "ALL",
  defaultMetric = "return",
  height = 320,
  poll = 10000,
  title,
}: {
  ranges?: Range[];
  defaultRange?: Range;
  defaultMetric?: Metric;
  height?: number;
  poll?: number;
  title?: string;
}) {
  const [range, setRange] = useState<Range>(defaultRange);
  const [custom, setCustom] = useState({ start: "", end: "" });
  const [applied, setApplied] = useState({ start: "", end: "" });
  const [metric, setMetric] = useState<Metric>(defaultMetric);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [showTable, setShowTable] = useState(false);
  const { t, lang } = useI18n();
  dayLocale = lang === "zh" ? "zh-CN" : "en-US";

  const path = `/api/equity?range=${range}` + (range === "custom" ? `&start=${applied.start}&end=${applied.end}` : "");
  // the previous frame stays on screen (dimmed) while a new range loads: no layout jump
  const { data, error, loading } = usePoll<EquityData>(path, poll);

  useEffect(() => {
    if (data?.first && !custom.start) setCustom({ start: data.lo, end: data.hi });
  }, [data, custom.start]);

  return (
    <Card className="perf-card">
      <div className="row between" style={{ marginBottom: 8 }}>
        <h2 style={{ margin: 0 }}>{title ?? t("Portfolio performance")}</h2>
        {data && !data.empty && (
          <span className="muted">
            {data.intraday ? fmtDay(data.lo, true) : `${fmtDay(data.lo, true)} – ${fmtDay(data.hi, true)}`} · {t(data.stats.sessions === 1 ? "{n} session" : "{n} sessions", { n: data.stats.sessions })}
          </span>
        )}
      </div>
      <div className="filters">
        {ranges.length > 1 && (
          <div className="seg" role="group" aria-label={t("Date range")}>
            {ranges.map((r) => (
              <button key={r} className={cls(range === r && "on")} aria-pressed={range === r} onClick={() => setRange(r)}>
                {t(LABELS[r])}
              </button>
            ))}
          </div>
        )}
        {range === "custom" && (
          <form className="row" onSubmit={(e) => { e.preventDefault(); setApplied(custom); }}>
            <input type="date" aria-label={t("From")} value={custom.start} min={data?.first} max={data?.last} onChange={(e) => setCustom({ ...custom, start: e.target.value })} />
            <span className="muted">{t("to")}</span>
            <input type="date" aria-label={t("To")} value={custom.end} min={data?.first} max={data?.last} onChange={(e) => setCustom({ ...custom, end: e.target.value })} />
            <button>{t("Apply")}</button>
          </form>
        )}
        <div className="seg" role="group" aria-label={t("Metric")}>
          <button className={cls(metric === "return" && "on")} aria-pressed={metric === "return"} onClick={() => setMetric("return")}>{t("Return % by strategy")}</button>
          <button className={cls(metric === "equity" && "on")} aria-pressed={metric === "equity"} onClick={() => setMetric("equity")}>{t("Equity $")}</button>
        </div>
        <span className="grow" style={{ flex: 1 }} />
        <button onClick={() => setShowTable(!showTable)} aria-pressed={showTable}>{t(showTable ? "Hide table" : "Table view")}</button>
      </div>

      {error && <Notice kind="bad">{error}</Notice>}
      {!data ? <p className="muted">{t("Loading…")}</p> : data.empty ? (
        <Empty>{t("No equity history yet. Run {replay} (or {demo}) to replay the running strategies over the stored sessions, or start the broker.", { replay: <code>make replay</code>, demo: <code>make demo</code> })}</Empty>
      ) : (
        <>
          <Summary d={data} />
          {metric === "return" && (
            <div className="legend" aria-label={t("Series")}>
              <span className="row" style={{ gap: 6, fontSize: 12, padding: "3px 4px" }}>
                <span className="key" style={{ borderColor: "var(--ink)", borderTopWidth: 3 }} /> {t("Total portfolio")}
              </span>
              {data.strategies.map((s, i) => (
                <button key={s} aria-pressed={!hidden.has(s)} onClick={() => {
                  const h = new Set(hidden);
                  if (h.has(s)) h.delete(s); else h.add(s);
                  setHidden(h);
                }} title={t("Show / hide")}>
                  <span className="key" style={{ borderColor: color(i) }} /> {s}
                </button>
              ))}
            </div>
          )}
          <Plot d={data} metric={metric} hidden={hidden} height={height} loading={loading} />
          <p className="muted" style={{ fontSize: 12, margin: "6px 0 0" }}>
            {data.sessions.some((s) => s.source === "replay") && <>{t("Unshaded sessions are {b}: the strategies now running, re-run over stored sessions with every intent auto-approved.", { b: <b>{t("replayed history")}</b> })} </>}
            {data.sessions.some((s) => s.source === "live") && <>{t("Shaded = {b}.", { b: <b>{t("live simulated session")}</b> })} </>}
            {data.sessions.some((s) => s.source === "alpaca_paper") && <>{t("Shaded = {b}.", { b: <b>{t("Alpaca paper session")}</b> })} </>}
            {metric === "return" && t("Strategy lines are each strategy's P&L (realized + open) as a % of the account at the start of the range; they add up to Total.")}
          </p>
          <StrategyTable d={data} hidden={hidden} />
          {showTable && <DailyTable d={data} />}
        </>
      )}
    </Card>
  );
}

function Summary({ d }: { d: EquityData }) {
  const s = d.stats;
  const { t } = useI18n();
  return (
    <div className="grid g4" style={{ marginBottom: 10 }}>
      <Stat label={t("Account equity")} value={money(s.end)} sub={t("from {v}", { v: money(s.start) })} />
      <Stat label={t("Return")} value={pct(s.change_pct, 2, true)} valueClass={pnlClass(s.change)} sub={t("{v} over the range", { v: money(s.change, true) })} />
      <Stat label={t("Max drawdown")} value={pct(s.max_drawdown)} sub={t("high {h} · low {l}", { h: money(s.high), l: money(s.low) })} />
      {d.intraday ? (
        <Stat label={t("Session")} value={fmtDay(d.lo)} sub={t("{n} bars", { n: s.points })} />
      ) : (
        <Stat label={t("Best / worst day")}
          value={<span><span className="pos">{pct(s.best_day?.change_pct, 2, true)}</span> / <span className="neg">{pct(s.worst_day?.change_pct, 2, true)}</span></span>}
          sub={`${s.best_day ? fmtDay(s.best_day.session) : "—"} / ${s.worst_day ? fmtDay(s.worst_day.session) : "—"}`} />
      )}
    </div>
  );
}

interface Series { key: string; label: string; color: string; values: number[]; width: number; area?: boolean }

function Plot({ d, metric, hidden, height, loading }: { d: EquityData; metric: Metric; hidden: Set<string>; height: number; loading: boolean }) {
  const [wrapRef, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const { t, lang } = useI18n();

  const series: Series[] = useMemo(() => {
    if (metric === "equity") return [{ key: "equity", label: t("Account equity"), color: "var(--c1)", values: d.equity, width: 2, area: true }];
    const total: Series = { key: "total", label: t("Total portfolio"), color: "var(--ink)", values: d.equity.map((v) => (v - d.base) / d.base), width: 2.5 };
    const strat = d.strategies
      .map((s, i) => ({ key: s, label: s, color: color(i), values: d.strategy_pnl[s].map((v) => v / d.base), width: 2 }))
      .filter((s) => !hidden.has(s.key));
    return [...strat, total]; // total drawn last, on top
  }, [d, metric, hidden, t]);

  const n = d.equity.length;
  const all = series.flatMap((s) => s.values);
  const zero = metric === "return" ? 0 : d.base;
  // minimum window: 0.4% of the account in either metric
  const ticks = niceTicks(Math.min(...all, zero), Math.max(...all, zero), metric === "return" ? 0.004 : d.base * 0.004);
  const yLo = ticks[0];
  const yHi = ticks[ticks.length - 1];
  const fmtY = (v: number) => (metric === "return" ? pct(v, Math.abs(yHi - yLo) < 0.02 ? 2 : 1) : compact(v));

  // end labels when they don't collide (never nudge them apart: legend + tooltip carry identity)
  const ml = 64;
  const mt = 12;
  const mb = 28;
  const yOf = (v: number, h: number) => mt + (h - mt - mb) * (1 - (v - yLo) / (yHi - yLo || 1));
  const ends = series.map((s) => ({ s, y: yOf(s.values[n - 1], height) })).sort((a, b) => a.y - b.y);
  const collide = ends.some((e, i) => i > 0 && e.y - ends[i - 1].y < 14);
  const showEnds = series.length <= 5 && !collide;
  const mr = showEnds ? Math.min(190, 24 + 7 * Math.max(...series.map((s) => (s.label + " " + fmtY(s.values[n - 1])).length))) : 16;
  const iw = Math.max(50, width - ml - mr);
  const x = (i: number) => ml + (n <= 1 ? iw / 2 : (iw * i) / (n - 1));
  const y = (v: number) => yOf(v, height);

  // x ticks: hours for one session, else session starts thinned to ~90px apart
  const xticks: { i: number; label: string }[] = [];
  if (d.intraday) {
    let lastHour = -1;
    d.ts.forEach((t, i) => {
      const h = Number(fmtTime(t).slice(0, 2));
      if (h !== lastHour && fmtTime(t).endsWith(":00")) {
        xticks.push({ i, label: fmtTime(t) });
        lastHour = h;
      }
    });
  } else {
    let lastX = -1e9;
    const multiYear = d.lo.slice(0, 4) !== d.hi.slice(0, 4);
    for (const s of d.sessions) {
      if (x(s.index) - lastX >= 90) {
        xticks.push({ i: s.index, label: fmtDay(s.session, multiYear) });
        lastX = x(s.index);
      }
    }
  }

  // shaded live sessions
  const bands: { x0: number; x1: number; paper: boolean }[] = [];
  d.sessions.forEach((s, k) => {
    if (s.source === "replay") return;
    const end = k + 1 < d.sessions.length ? d.sessions[k + 1].index : n - 1;
    bands.push({ x0: x(s.index), x1: x(end), paper: s.source === "alpaca_paper" });
  });

  function pick(clientX: number) {
    const r = svgRef.current?.getBoundingClientRect();
    if (!r) return;
    const px = clientX - r.left;
    const i = Math.round(((px - ml) / iw) * (n - 1));
    setHover(Math.max(0, Math.min(n - 1, i)));
  }

  function onKey(e: React.KeyboardEvent) {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    e.preventDefault();
    const step = e.shiftKey ? Math.max(1, Math.round(n / 20)) : 1;
    setHover((h) => Math.max(0, Math.min(n - 1, (h ?? n - 1) + (e.key === "ArrowRight" ? step : -step))));
  }

  const path = (vals: number[]) => vals.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");
  const area = (vals: number[]) => `${path(vals)}L${x(n - 1).toFixed(1)},${y(yLo).toFixed(1)}L${x(0).toFixed(1)},${y(yLo).toFixed(1)}Z`;

  const h = hover;
  const tipLeft = h != null ? Math.min(Math.max(x(h) + 12, 0), width - 200) : 0;
  const tipFlip = h != null && x(h) + 220 > width;

  return (
    <div ref={wrapRef} className={cls("perf", loading && "loading")}>
      <svg ref={svgRef} width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" tabIndex={0}
        aria-label={t("{what} from {lo} to {hi}. Use arrow keys to inspect values.", { what: t(metric === "return" ? "Cumulative return" : "Account equity"), lo: d.lo, hi: d.hi })}
        onPointerMove={(e) => pick(e.clientX)} onPointerLeave={() => setHover(null)} onKeyDown={onKey} onBlur={() => setHover(null)}>
        {bands.map((b, k) => (
          <g key={k}>
            <rect x={b.x0} y={mt} width={Math.max(2, b.x1 - b.x0)} height={height - mt - mb} fill="var(--band)" />
            {b.x1 - b.x0 > 40 && <text x={b.x0 + 4} y={mt + 11} className="bandlabel">{t(b.paper ? "paper" : "live sim")}</text>}
          </g>
        ))}
        <g className="grid">
          {ticks.map((t) => <line key={t} x1={ml} x2={ml + iw} y1={y(t)} y2={y(t)} />)}
        </g>
        <line className="zero" x1={ml} x2={ml + iw} y1={y(zero)} y2={y(zero)} />
        {ticks.map((t) => (
          <text key={t} x={ml - 8} y={y(t) + 4} textAnchor="end" className="tick">{fmtY(t)}</text>
        ))}
        {xticks.map((t) => (
          <text key={t.i} x={x(t.i)} y={height - 8} textAnchor="middle" className="tick">{t.label}</text>
        ))}
        {series.map((s) => (
          <Fragment key={s.key}>
            {s.area && <path d={area(s.values)} fill={s.color} opacity={0.1} />}
            <path d={path(s.values)} fill="none" stroke={s.color} strokeWidth={s.width} strokeLinejoin="round" strokeLinecap="round" />
          </Fragment>
        ))}
        {showEnds && series.map((s) => (
          <g key={s.key}>
            <circle cx={x(n - 1)} cy={y(s.values[n - 1])} r={4} fill={s.color} stroke="var(--panel)" strokeWidth={2} />
            <text x={x(n - 1) + 9} y={y(s.values[n - 1]) + 4} className="endlabel">
              {s.label} {fmtY(s.values[n - 1])}
            </text>
          </g>
        ))}
        {h != null && (
          <g>
            <line className="xhair" x1={x(h)} x2={x(h)} y1={mt} y2={height - mb} />
            {series.map((s) => <circle key={s.key} cx={x(h)} cy={y(s.values[h])} r={4} fill={s.color} stroke="var(--panel)" strokeWidth={2} />)}
          </g>
        )}
      </svg>
      {h != null && (
        <div className="tip" style={{ left: tipFlip ? x(h) - 200 : tipLeft, top: 8 }}>
          <div className="when">
            {new Date(d.ts[h] * 1000).toLocaleString(lang === "zh" ? "zh-CN" : "en-US", { timeZone: ET, weekday: "short", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false })} {t("ET")}
            {" · "}{t(d.source[h] === "replay" ? "replay" : d.source[h] === "alpaca_paper" ? "paper" : "live sim")}
          </div>
          {[...series].reverse().map((s) => (
            <div className="r" key={s.key}>
              <span className="key" style={{ borderColor: s.color }} />
              <span>
                <b>{metric === "return" ? pct(s.values[h], 2, true) : money(s.values[h])}</b>{" "}
                <span className="muted">
                  {s.label}
                  {metric === "return" && ` (${money(s.values[h] * d.base, true)})`}
                </span>
              </span>
            </div>
          ))}
          {metric === "equity" && (
            <div className="muted" style={{ marginTop: 2 }}>{t("{pct} vs range start", { pct: pct(d.equity[h] / d.base - 1, 2, true) })}</div>
          )}
        </div>
      )}
    </div>
  );
}

function StrategyTable({ d, hidden }: { d: EquityData; hidden: Set<string> }) {
  if (!d.by_strategy.length) return null;
  const total = d.stats.change;
  const { t } = useI18n();
  return (
    <div className="table-wrap" style={{ marginTop: 12 }}>
      <table>
        <thead>
          <tr><th>{t("Strategy")}</th><th className="num">{t("P&L")}</th><th className="num">{t("Return contribution")}</th><th className="num">{t("Share of total")}</th><th style={{ width: "30%" }}>{t("Contribution")}</th></tr>
        </thead>
        <tbody>
          {d.by_strategy.map((b, i) => {
            const maxAbs = Math.max(...d.by_strategy.map((x) => Math.abs(x.pnl)), 1);
            return (
              <tr key={b.strategy} className={hidden.has(b.strategy) ? "dim" : ""}>
                <td><span className="key" style={{ borderColor: color(i), marginRight: 8 }} />{b.strategy}</td>
                <td className={`num ${pnlClass(b.pnl)}`}>{money(b.pnl, true)}</td>
                <td className={`num ${pnlClass(b.pnl)}`}>{pct(b.return_pct, 2, true)}</td>
                <td className="num">{b.share == null ? "—" : pct(b.share, 0)}</td>
                <td>
                  <div style={{ position: "relative", height: 10 }}>
                    <div style={{ position: "absolute", left: "50%", top: 0, bottom: 0, width: 1, background: "var(--border)" }} />
                    <div style={{
                      position: "absolute", top: 1, height: 8, borderRadius: 3, background: color(i),
                      left: b.pnl >= 0 ? "50%" : `${50 - (Math.abs(b.pnl) / maxAbs) * 50}%`, width: `${(Math.abs(b.pnl) / maxAbs) * 50}%`,
                    }} />
                  </div>
                </td>
              </tr>
            );
          })}
          <tr>
            <td><span className="key" style={{ borderColor: "var(--ink)", borderTopWidth: 3, marginRight: 8 }} /><b>{t("Total portfolio")}</b></td>
            <td className={`num ${pnlClass(total)}`}><b>{money(total, true)}</b></td>
            <td className={`num ${pnlClass(total)}`}><b>{pct(d.stats.change_pct, 2, true)}</b></td>
            <td className="num">100%</td><td />
          </tr>
        </tbody>
      </table>
    </div>
  );
}

function DailyTable({ d }: { d: EquityData }) {
  const { t } = useI18n();
  return (
    <details open style={{ marginTop: 12 }}>
      <summary>{t("Per session")} ({d.daily.length})</summary>
      <div className="table-wrap" style={{ maxHeight: 420, overflowY: "auto" }}>
        <table>
          <thead>
            <tr>
              <th>{t("Session")}</th><th>{t("Source")}</th><th className="num">{t("Close")}</th><th className="num">{t("Change")}</th><th className="num">%</th>
              {d.strategies.map((s) => <th key={s} className="num">{s}</th>)}
            </tr>
          </thead>
          <tbody>
            {[...d.daily].reverse().map((r) => (
              <tr key={r.session}>
                <td>{fmtDay(r.session, true)}</td><td className="muted">{t(r.source)}</td>
                <td className="num">{money(r.close)}</td>
                <td className={`num ${pnlClass(r.change)}`}>{money(r.change, true)}</td>
                <td className={`num ${pnlClass(r.change)}`}>{pct(r.change_pct, 2, true)}</td>
                {d.strategies.map((s) => <td key={s} className={`num ${pnlClass(r.by_strategy[s])}`}>{money(r.by_strategy[s] ?? 0, true)}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}

"use client";
import Link from "next/link";
import { useState } from "react";
import { post } from "@/lib/api";
import { money, num, pct, pnlClass, wall } from "@/lib/format";
import { LineChart } from "@/components/LineChart";
import { Card, Checks, Conditions, Empty, Pill, Stat, StatusPill } from "@/components/ui";
import { isOwner, useSession } from "@/components/session";
import { useT, type T } from "@/lib/i18n";

/** Backtest report of a `kind: portfolio` strategy (daily replay, orders instead of bracket trades). */
export function PortfolioBacktest({ bt }: { bt: any }) {
  const m = bt.metrics;
  const t = useT();
  return (
    <>
      <div className="grid g4">
        <Stat label={t("Return")} value={pct(m.return_pct, 2, true)} valueClass={pnlClass(m.return_pct)}
          sub={t("buy-and-hold {pct} · {n} sessions", { pct: pct(m.benchmark_return_pct, 2, true), n: bt.days.length })} />
        <Stat label={t("P&L")} value={money(m.total_pnl, true)} valueClass={pnlClass(m.total_pnl)} sub={t("{o} orders · {s} sales", { o: m.orders, s: m.trades })} />
        <Stat label={t("Max drawdown")} value={pct(m.max_drawdown)} sub={t("Sharpe (daily) {v}", { v: num(m.sharpe_daily) })} />
        <Stat label={t("Peak exposure")} value={pct(m.peak_exposure_pct, 1)} sub={t("sales won {pct}", { pct: pct(m.win_rate, 0) })} />
      </div>
      <LineChart values={bt.equity_curve.map((p: any) => p.equity)} width={900} height={160} baseline={bt.starting_equity} />
      <p className="muted">
        {bt.signal_note} {t("Sales by reason: {list} · rejected by portfolio limits: {n} · ran {when} by {who} in {s}s", {
          list: Object.entries(m.exits).map(([k, n]) => `${t(k)} ${n}`).join(", ") || t("none"), n: bt.rejected_by_guard, when: wall(bt.ran_at), who: bt.ran_by, s: bt.seconds })}
      </p>
      <details>
        <summary>{t("Orders")} ({bt.orders.length})</summary>
        <div className="table-wrap"><table>
          <thead><tr><th>{t("Day")}</th><th>{t("Symbol")}</th><th>{t("Side")}</th><th className="num">{t("Qty")}</th><th className="num">{t("Fill")}</th><th>{t("Why")}</th><th>{t("Rule")}</th></tr></thead>
          <tbody>{bt.orders.map((o: any, i: number) => (
            <tr key={i}><td>{o.day}</td><td>{o.symbol}</td><td>{t(o.side)}</td><td className="num">{o.qty}</td><td className="num">{money(o.price)}</td>
              <td>{t(o.reason)}</td><td className="muted mono">{o.decision?.[0]?.detail ?? o.decision?.[0]?.cond}</td></tr>
          ))}</tbody>
        </table></div>
      </details>
      <details>
        <summary>{t("Sales")} ({bt.trades.length})</summary>
        <div className="table-wrap"><table>
          <thead><tr><th>{t("Day")}</th><th>{t("Symbol")}</th><th className="num">{t("Qty")}</th><th className="num">{t("Avg cost")}</th><th className="num">{t("Sold at")}</th><th>{t("Why")}</th><th className="num">{t("P&L")}</th></tr></thead>
          <tbody>{bt.trades.map((tr: any, i: number) => (
            <tr key={i}><td>{tr.day}</td><td>{tr.symbol}</td><td className="num">{tr.qty}</td><td className="num">{money(tr.entry_price)}</td><td className="num">{money(tr.exit_price)}</td>
              <td>{t(tr.exit_kind)}</td><td className={`num ${pnlClass(tr.pnl)}`}>{money(tr.pnl, true)}</td></tr>
          ))}</tbody>
        </table></div>
      </details>
      {bt.overlay_events?.length > 0 && (
        <details>
          <summary>{t("Overlay events ({n}) · locked rotation cash at the end {v}", { n: bt.overlay_events.length, v: money(bt.locked_cash) })}</summary>
          <table><tbody>{bt.overlay_events.map((e: any, i: number) => (
            <tr key={i}><td>{e.day}</td><td>{t(EVENT_TEXT[e.kind] ?? e.kind)}</td><td className="mono muted">{e.overlay}</td><td>{eventDetail({ kind: e.kind, payload: e }, t)}</td></tr>
          ))}</tbody></table>
        </details>
      )}
      {bt.rejected?.length > 0 && (
        <details>
          <summary>{t("Rejected by portfolio limits")} ({bt.rejected.length})</summary>
          <table><tbody>{bt.rejected.map((r: any, i: number) => (
            <tr key={i}><td>{r.day}</td><td>{t(r.side)} {r.qty} {r.symbol}</td><td>{t(r.reason)}</td><td className="muted">{r.failed.map((f: any) => `${f.name}: ${f.detail}`).join("; ")}</td></tr>
          ))}</tbody></table>
        </details>
      )}
    </>
  );
}

/** The live sleeve on the Alpaca paper account: state, shares held, today's decisions and orders. */
export function SleeveCard({ p, decideAt }: { p: any; decideAt: string }) {
  const st = p?.state;
  const t = useT();
  return (
    <Card title={t("Portfolio sleeve (paper)")} className="mt">
      {!st ? (
        <Empty>
          {t("Not started. Once promoted to paper, the broker buys the basket at {time} ET on the next session (Alpaca paper backend only; the simulator doesn't run portfolio strategies).", { time: decideAt })}
        </Empty>
      ) : (
        <>
          <div className="row">
            <StatusPill status={st.status} />
            {st.reason && <Pill>{st.reason}</Pill>}
            <Pill>{t("started {d}", { d: st.started })}</Pill>
            <Pill>{t("{n} sessions decided", { n: st.sessions })}</Pill>
            <Pill>{t("capital ${v}", { v: money(st.capital_usd) })}</Pill>
            <Pill kind={pnlClass(p.realized) === "neg" ? "bad" : "good"}>{t("realized {v}", { v: money(p.realized, true) })}</Pill>
          </div>
          <div className="table-wrap"><table>
            <thead><tr><th>{t("Symbol")}</th><th>{t("Mode")}</th><th className="num">{t("Qty")}</th><th className="num">{t("Avg cost")}</th><th className="num">{t("Last")}</th><th className="num">{t("Value")}</th><th className="num">{t("Of allocation")}</th><th className="num">{t("Unrealized")}</th></tr></thead>
            <tbody>
              {p.holdings.map((h: any) => (
                <tr key={h.symbol}><td>{h.symbol}</td><td>{t(h.mode)}</td><td className="num">{h.qty}</td><td className="num">{money(h.cost)}</td><td className="num">{money(h.last)}</td>
                  <td className="num">{money(h.value)}</td><td className="num">{pct(h.holding, 0)}</td><td className={`num ${pnlClass(h.unrealized)}`}>{money(h.unrealized, true)}</td></tr>
              ))}
              {!p.holdings.length && <tr><td colSpan={8} className="muted">{t("No shares held.")}</td></tr>}
            </tbody>
          </table></div>
        </>
      )}
      {p?.last_day && (
        <details>
          <summary>{t("Decisions on {day}", { day: p.last_day.day })}{p.last_day.event ? ` (${p.last_day.event})` : ""}</summary>
          <table>
            <thead><tr><th>{t("Symbol")}</th><th>{t("Mode")}</th><th className="num">{t("Day move")}</th><th className="num">{t("Held")}</th><th>{t("Rule matched")}</th><th>{t("Action")}</th></tr></thead>
            <tbody>{p.last_day.records.map((r: any) => (
              <tr key={r.symbol}><td>{r.symbol}</td><td>{t(r.mode)}{r.mode_after && r.mode_after !== r.mode ? ` → ${t(r.mode_after)}` : ""}</td>
                <td className={`num ${pnlClass(r.day_change)}`}>{pct(r.day_change, 2, true)}</td><td className="num">{pct(r.holding, 0)}</td>
                <td className="mono muted">{r.rule ?? "—"}</td><td>{t(r.action)}{r.note ? ` (${r.note})` : ""}</td></tr>
            ))}</tbody>
          </table>
        </details>
      )}
      <h3>{t("Orders")}</h3>
      <div className="table-wrap"><table>
        <thead><tr><th>#</th><th>{t("Day")}</th><th>{t("Symbol")}</th><th>{t("Side")}</th><th className="num">{t("Qty")}</th><th className="num">{t("Ref")}</th><th className="num">{t("Filled @")}</th><th>{t("Why")}</th><th>{t("Status")}</th><th>{t("Note")}</th></tr></thead>
        <tbody>
          {(p?.orders ?? []).map((o: any) => (
            <tr key={o.id}><td>{o.id}</td><td>{o.day}</td><td>{o.symbol}</td><td>{t(o.side)}</td><td className="num">{o.filled_qty ? `${o.filled_qty}/${o.qty}` : o.qty}</td>
              <td className="num">{money(o.ref_price)}</td><td className="num">{money(o.avg_price)}</td><td>{t(o.reason)}</td><td><StatusPill status={o.status} /></td><td className="muted">{o.note}</td></tr>
          ))}
          {!p?.orders?.length && <tr><td colSpan={10} className="muted">{t("None yet.")}</td></tr>}
        </tbody>
      </table></div>
    </Card>
  );
}

/** Pending sleeve orders from `manual` portfolio strategies, on the approvals page. */
export function SleeveApprovals({ pending, recent, onDone }: { pending: any[]; recent: any[]; onDone: (k: "ok" | "bad", t: string) => void }) {
  const { me } = useSession();
  const [busy, setBusy] = useState<number | null>(null);
  const t = useT();
  async function decide(id: number, action: "approve" | "reject") {
    setBusy(id);
    try {
      onDone("ok", (await post(`/api/portfolio-orders/${id}/${action}`, { reason: "" })).message);
    } catch (e) {
      onDone("bad", (e as Error).message);
    } finally {
      setBusy(null);
    }
  }
  if (!pending.length && !recent.length) return null;
  return (
    <Card title={t("Portfolio sleeve orders")} className="mt">
      <p className="muted">
        {t("Daily orders from {manual} portfolio strategies. They wait until 5 minutes before the close; on approval the broker re-runs the portfolio limits before sending a day market order.", { manual: <code>manual</code> })}
      </p>
      {pending.map((o) => (
        <div key={o.id} className="card" style={{ marginBottom: 8 }}>
          <div className="row between">
            <b>#{o.id} · {t(o.side).toUpperCase()} {o.qty} {o.symbol} <span className="muted">@ ~{money(o.ref_price)} (${money(o.qty * o.ref_price)})</span></b>
            <span className="muted"><Link href={`/strategies/${o.strategy_hash}`}>{o.sid}</Link> · {t(o.reason)} · {t("expires {when}", { when: wall(o.expires_wall) })}</span>
          </div>
          <Conditions items={o.decision} />
          <details><summary>{t("Portfolio limits ({ok}/{n} passed)", { ok: o.guard.filter((g: any) => g.ok).length, n: o.guard.length })}</summary><Checks items={o.guard} /></details>
          {isOwner(me) && (
            <div className="row" style={{ marginTop: 6 }}>
              <button className="good" disabled={busy === o.id} onClick={() => decide(o.id, "approve")}>{t("Approve")}</button>
              <button disabled={busy === o.id} onClick={() => decide(o.id, "reject")}>{t("Reject")}</button>
            </div>
          )}
        </div>
      ))}
      {recent.length > 0 && (
        <details>
          <summary>{t("Recent sleeve orders")} ({recent.length})</summary>
          <table><tbody>{recent.map((r) => (
            <tr key={r.id}><td>{r.id}</td><td>{r.day}</td><td>{r.sid}</td><td>{t(r.side)} {r.qty} {r.symbol}</td><td>{t(r.reason)}</td><td><StatusPill status={r.status} /></td>
              <td className="muted">{r.decided_by ?? t("broker")}</td><td className="muted">{r.note}</td></tr>
          ))}</tbody></table>
        </details>
      )}
    </Card>
  );
}

const EVENT_TEXT: Record<string, string> = {
  macro_trigger: "macro trigger", macro_repeat: "repeat crossing (no second trim)", macro_reduced: "reduction filled",
  macro_recovery: "recovery started", macro_rebuilt: "rebuild complete", rotation: "rotation trim", rotation_skipped: "rotation skipped",
  input_missing: "input missing", missed_decision: "missed decision", reduction_blocked: "reduction blocked", shadow: "shadow decisions",
  assumption: "assumption",
};

function eventDetail(e: any, t: T): string {
  const p = e.payload ?? {};
  if (e.kind === "shadow") return (p.orders ?? []).map((o: any) => t("{sym}: would {would} ({why}; actual {actual})", { sym: o.symbol, would: t(o.would), why: t(o.reason), actual: o.actual ? t(o.actual) : "—" })).join(" · ");
  if (e.kind === "assumption") return `${p.assumption}: ${p.status}${p.tier ? ` (${p.tier})` : ""}${p.error ? ` · ${p.error}` : ""}`;
  const parts = [];
  if (p.value !== undefined) parts.push(`${p.input ?? t("value")} ${pct(p.value, 2, true)}`);
  if (p.fraction !== undefined) parts.push(t("sell {pct} of {group}", { pct: pct(p.fraction, 1), group: p.group }));
  if (p.effect) parts.push(p.effect);
  if (p.why) parts.push(p.why);
  if (p.symbol) parts.push(t("{sym} {qty} blocked: {why}", { sym: p.symbol, qty: p.qty, why: (p.failed ?? []).join(", ") }));
  return parts.join(" · ");
}

/** DSL 0.2 overlays: mode, phases, market inputs, locked cash, events and monitored assumptions. */
export function OverlaysCard({ o, hash, act, busy }: { o: any; hash: string; act: (l: string, p: string, b?: object) => void; busy: string | null }) {
  const { me } = useSession();
  const owner = isOwner(me);
  const t = useT();
  const inputs = o.observed && (!o.inputs || o.observed.ts > o.inputs.ts) ? o.observed : o.inputs;
  return (
    <Card title={t("Portfolio overlays (DSL 0.2)")} className="mt">
      <div className="row">
        <Pill kind={o.enforced ? "bad" : "accent"}>{t(o.enforced ? "enforced: overlays place orders" : "shadow: decisions logged only")}</Pill>
        <Pill>{t("file mode {m}", { m: t(o.file_mode) })}</Pill>
        {o.review_required && <Pill kind="warn">{t("defaults proposed, need review")}</Pill>}
        <Pill kind={o.catalog_pinned ? "good" : "warn"}>{t(o.catalog_pinned ? "scenario catalog pinned" : "catalog not pinned (promote to pin)")}</Pill>
        {o.missing_feeds.length > 0 && <Pill kind="bad">{t("no feed: {list}", { list: o.missing_feeds.join(", ") })}</Pill>}
        {o.input_symbols.length > 0 && <Pill>{t("monitored only: {list}", { list: o.input_symbols.join(", ") })}</Pill>}
        <Pill kind={o.locked_effective > 0 ? "warn" : ""}>{t("locked cash {v}", { v: money(o.locked_effective) })}</Pill>
        {o.assumption_block && o.assumption_block.until > Date.now() / 1000 && <Pill kind="bad">{t("buys blocked: {why}", { why: o.assumption_block.reasons.join("; ") })}</Pill>}
      </div>
      {owner && (
        <div className="row" style={{ marginTop: 8 }}>
          {o.file_mode === "enforce" && !o.owner_enabled && (
            <button className="primary" disabled={!!busy}
              onClick={() => window.confirm(t("Overlays will trim, pause buys and rebuild on paper through the strategy's approval mode. Enable?")) &&
                act("overlays", `/api/strategies/${hash}/overlays`, { enforce: true })}>{t("Enforce overlays on paper")}</button>
          )}
          {o.owner_enabled && <button disabled={!!busy} onClick={() => act("overlays", `/api/strategies/${hash}/overlays`, { enforce: false })}>{t("Back to shadow")}</button>}
          {o.locked_effective > 0.01 && (
            <button disabled={!!busy} onClick={() => window.confirm(t("Release {v} of locked rotation proceeds so the sleeve can buy with it?", { v: money(o.locked_effective) })) &&
              act("release", `/api/strategies/${hash}/release-cash`)}>{t("Release locked cash")}</button>
          )}
          {o.file_mode === "shadow" && <span className="muted">{t("This version runs overlays in shadow mode; save a version with monitoring.mode: enforce to let them trade.")}</span>}
        </div>
      )}
      <div className="table-wrap"><table>
        <thead><tr><th>{t("Overlay")}</th><th>{t("Scenario")}</th><th>{t("Response")}</th><th>{t("Evaluated")}</th><th>{t("Phase")}</th><th>{t("Last trigger")}</th></tr></thead>
        <tbody>{o.rules.map((r: any) => (
          <tr key={r.id}><td className="mono">{r.id}</td><td className="muted">{r.scenario}</td><td>{t(r.action)}</td><td>{t(r.on.replaceAll("_", " "))}</td>
            <td><Pill kind={r.phase === "normal" ? "" : "warn"}>{t(r.phase.replaceAll("_", " "))}</Pill></td>
            <td>{r.state.trigger_day ? `${r.state.trigger_day} (${pct(r.state.trigger_value, 2, true)})` : r.state.last_value !== undefined ? t("{v} → sell {pct}", { v: pct(r.state.last_value, 2, true), pct: pct(r.state.last_fraction, 1) }) : "—"}</td></tr>
        ))}</tbody>
      </table></div>
      <h3>{t("Market inputs")}</h3>
      {inputs ? (
        <table><tbody>{Object.entries(inputs.values ?? {}).map(([k, v]: [string, any]) => (
          <tr key={k}><td className="mono">{k}</td><td className={`num ${pnlClass(v)}`}>{v === null ? t("unknown") : pct(v, 2, true)}</td><td className="muted">{inputs.notes?.[k]}</td></tr>
        ))}<tr><td colSpan={3} className="muted">{t("as of {when}", { when: wall(inputs.ts) })}</td></tr></tbody></table>
      ) : <Empty>{t("Not read yet: inputs are read during the session (index every observation, baskets at the daily decision).")}</Empty>}
      <h3>{t("Events")}</h3>
      <div className="table-wrap"><table>
        <thead><tr><th>{t("When")}</th><th>{t("Event")}</th><th>{t("Mode")}</th><th>{t("Details")}</th><th>{t("Model context (never changes the numeric response)")}</th></tr></thead>
        <tbody>
          {o.events.map((e: any) => (
            <tr key={e.id}><td>{wall(e.ts)}</td><td>{t(EVENT_TEXT[e.kind] ?? e.kind)}{e.overlay ? <div className="muted mono">{e.overlay}</div> : null}</td>
              <td><Pill kind={e.mode === "enforce" ? "bad" : ""}>{t(e.mode)}</Pill></td><td>{eventDetail(e, t)}</td>
              <td className="muted">{e.context ? (e.context.error ?? `${e.context.driver} (${pct(e.context.confidence, 0)})`) : ""}</td></tr>
          ))}
          {!o.events.length && <tr><td colSpan={5} className="muted">{t("None yet.")}</td></tr>}
        </tbody>
      </table></div>
      {o.assumptions.length > 0 && (
        <>
          <h3>{t("Assumptions")}</h3>
          <table><tbody>{o.assumptions.map((a: any) => (
            <tr key={a.id}><td className="mono">{a.id}</td><td>{a.statement}</td>
              <td>{a.monitored ? (a.latest ? <><StatusPill status={a.latest.status} /> <span className="muted">{a.latest.tier ?? t("no tier")} · {wall(a.latest.ts)}{a.latest.error ? ` · ${a.latest.error}` : ""}</span></> : <span className="muted">{t("monitored, not checked yet")}</span>) : <span className="muted">{t("not monitored")}</span>}
                {a.critical && <Pill kind="warn">{t("critical")}</Pill>}</td></tr>
          ))}</tbody></table>
        </>
      )}
    </Card>
  );
}

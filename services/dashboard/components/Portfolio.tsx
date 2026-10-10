"use client";
import Link from "next/link";
import { useState } from "react";
import { post } from "@/lib/api";
import { money, num, pct, pnlClass, wall } from "@/lib/format";
import { LineChart } from "@/components/LineChart";
import { Card, Checks, Conditions, Empty, Pill, Stat, StatusPill } from "@/components/ui";
import { isOwner, useSession } from "@/components/session";

/** Backtest report of a `kind: portfolio` strategy (daily replay, orders instead of bracket trades). */
export function PortfolioBacktest({ bt }: { bt: any }) {
  const m = bt.metrics;
  return (
    <>
      <div className="grid g4">
        <Stat label="Return" value={pct(m.return_pct, 2, true)} valueClass={pnlClass(m.return_pct)}
          sub={`buy-and-hold ${pct(m.benchmark_return_pct, 2, true)} · ${bt.days.length} sessions`} />
        <Stat label="P&L" value={money(m.total_pnl, true)} valueClass={pnlClass(m.total_pnl)} sub={`${m.orders} orders · ${m.trades} sales`} />
        <Stat label="Max drawdown" value={pct(m.max_drawdown)} sub={`Sharpe (daily) ${num(m.sharpe_daily)}`} />
        <Stat label="Peak exposure" value={pct(m.peak_exposure_pct, 1)} sub={`sales won ${pct(m.win_rate, 0)}`} />
      </div>
      <LineChart values={bt.equity_curve.map((p: any) => p.equity)} width={900} height={160} baseline={bt.starting_equity} />
      <p className="muted">
        {bt.signal_note} Sales by reason: {Object.entries(m.exits).map(([k, n]) => `${k} ${n}`).join(", ") || "none"} · rejected by
        portfolio limits: {bt.rejected_by_guard} · ran {wall(bt.ran_at)} by {bt.ran_by} in {bt.seconds}s
      </p>
      <details>
        <summary>Orders ({bt.orders.length})</summary>
        <div className="table-wrap"><table>
          <thead><tr><th>Day</th><th>Symbol</th><th>Side</th><th className="num">Qty</th><th className="num">Fill</th><th>Why</th><th>Rule</th></tr></thead>
          <tbody>{bt.orders.map((o: any, i: number) => (
            <tr key={i}><td>{o.day}</td><td>{o.symbol}</td><td>{o.side}</td><td className="num">{o.qty}</td><td className="num">{money(o.price)}</td>
              <td>{o.reason}</td><td className="muted mono">{o.decision?.[0]?.detail ?? o.decision?.[0]?.cond}</td></tr>
          ))}</tbody>
        </table></div>
      </details>
      <details>
        <summary>Sales ({bt.trades.length})</summary>
        <div className="table-wrap"><table>
          <thead><tr><th>Day</th><th>Symbol</th><th className="num">Qty</th><th className="num">Avg cost</th><th className="num">Sold at</th><th>Why</th><th className="num">P&amp;L</th></tr></thead>
          <tbody>{bt.trades.map((t: any, i: number) => (
            <tr key={i}><td>{t.day}</td><td>{t.symbol}</td><td className="num">{t.qty}</td><td className="num">{money(t.entry_price)}</td><td className="num">{money(t.exit_price)}</td>
              <td>{t.exit_kind}</td><td className={`num ${pnlClass(t.pnl)}`}>{money(t.pnl, true)}</td></tr>
          ))}</tbody>
        </table></div>
      </details>
      {bt.rejected?.length > 0 && (
        <details>
          <summary>Rejected by portfolio limits ({bt.rejected.length})</summary>
          <table><tbody>{bt.rejected.map((r: any, i: number) => (
            <tr key={i}><td>{r.day}</td><td>{r.side} {r.qty} {r.symbol}</td><td>{r.reason}</td><td className="muted">{r.failed.map((f: any) => `${f.name}: ${f.detail}`).join("; ")}</td></tr>
          ))}</tbody></table>
        </details>
      )}
    </>
  );
}

/** The live sleeve on the Alpaca paper account: state, shares held, today's decisions and orders. */
export function SleeveCard({ p, decideAt }: { p: any; decideAt: string }) {
  const st = p?.state;
  return (
    <Card title="Portfolio sleeve (paper)" className="mt">
      {!st ? (
        <Empty>
          Not started. Once promoted to paper, the broker buys the basket at {decideAt} ET on the next session (Alpaca paper backend only;
          the simulator doesn&apos;t run portfolio strategies).
        </Empty>
      ) : (
        <>
          <div className="row">
            <StatusPill status={st.status} />
            {st.reason && <Pill>{st.reason}</Pill>}
            <Pill>started {st.started}</Pill>
            <Pill>{st.sessions} sessions decided</Pill>
            <Pill>capital ${money(st.capital_usd)}</Pill>
            <Pill kind={pnlClass(p.realized) === "neg" ? "bad" : "good"}>realized {money(p.realized, true)}</Pill>
          </div>
          <div className="table-wrap"><table>
            <thead><tr><th>Symbol</th><th>Mode</th><th className="num">Qty</th><th className="num">Avg cost</th><th className="num">Last</th><th className="num">Value</th><th className="num">Of allocation</th><th className="num">Unrealized</th></tr></thead>
            <tbody>
              {p.holdings.map((h: any) => (
                <tr key={h.symbol}><td>{h.symbol}</td><td>{h.mode}</td><td className="num">{h.qty}</td><td className="num">{money(h.cost)}</td><td className="num">{money(h.last)}</td>
                  <td className="num">{money(h.value)}</td><td className="num">{pct(h.holding, 0)}</td><td className={`num ${pnlClass(h.unrealized)}`}>{money(h.unrealized, true)}</td></tr>
              ))}
              {!p.holdings.length && <tr><td colSpan={8} className="muted">No shares held.</td></tr>}
            </tbody>
          </table></div>
        </>
      )}
      {p?.last_day && (
        <details>
          <summary>Decisions on {p.last_day.day}{p.last_day.event ? ` (${p.last_day.event})` : ""}</summary>
          <table>
            <thead><tr><th>Symbol</th><th>Mode</th><th className="num">Day move</th><th className="num">Held</th><th>Rule matched</th><th>Action</th></tr></thead>
            <tbody>{p.last_day.records.map((r: any) => (
              <tr key={r.symbol}><td>{r.symbol}</td><td>{r.mode}{r.mode_after && r.mode_after !== r.mode ? ` → ${r.mode_after}` : ""}</td>
                <td className={`num ${pnlClass(r.day_change)}`}>{pct(r.day_change, 2, true)}</td><td className="num">{pct(r.holding, 0)}</td>
                <td className="mono muted">{r.rule ?? "—"}</td><td>{r.action}{r.note ? ` (${r.note})` : ""}</td></tr>
            ))}</tbody>
          </table>
        </details>
      )}
      <h3>Orders</h3>
      <div className="table-wrap"><table>
        <thead><tr><th>#</th><th>Day</th><th>Symbol</th><th>Side</th><th className="num">Qty</th><th className="num">Ref</th><th className="num">Filled @</th><th>Why</th><th>Status</th><th>Note</th></tr></thead>
        <tbody>
          {(p?.orders ?? []).map((o: any) => (
            <tr key={o.id}><td>{o.id}</td><td>{o.day}</td><td>{o.symbol}</td><td>{o.side}</td><td className="num">{o.filled_qty ? `${o.filled_qty}/${o.qty}` : o.qty}</td>
              <td className="num">{money(o.ref_price)}</td><td className="num">{money(o.avg_price)}</td><td>{o.reason}</td><td><StatusPill status={o.status} /></td><td className="muted">{o.note}</td></tr>
          ))}
          {!p?.orders?.length && <tr><td colSpan={10} className="muted">None yet.</td></tr>}
        </tbody>
      </table></div>
    </Card>
  );
}

/** Pending sleeve orders from `manual` portfolio strategies, on the approvals page. */
export function SleeveApprovals({ pending, recent, onDone }: { pending: any[]; recent: any[]; onDone: (k: "ok" | "bad", t: string) => void }) {
  const { me } = useSession();
  const [busy, setBusy] = useState<number | null>(null);
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
    <Card title="Portfolio sleeve orders" className="mt">
      <p className="muted">
        Daily orders from <code>manual</code> portfolio strategies. They wait until 5 minutes before the close; on approval the broker re-runs
        the portfolio limits before sending a day market order.
      </p>
      {pending.map((o) => (
        <div key={o.id} className="card" style={{ marginBottom: 8 }}>
          <div className="row between">
            <b>#{o.id} · {o.side.toUpperCase()} {o.qty} {o.symbol} <span className="muted">@ ~{money(o.ref_price)} (${money(o.qty * o.ref_price)})</span></b>
            <span className="muted"><Link href={`/strategies/${o.strategy_hash}`}>{o.sid}</Link> · {o.reason} · expires {wall(o.expires_wall)}</span>
          </div>
          <Conditions items={o.decision} />
          <details><summary>Portfolio limits ({o.guard.filter((g: any) => g.ok).length}/{o.guard.length} passed)</summary><Checks items={o.guard} /></details>
          {isOwner(me) && (
            <div className="row" style={{ marginTop: 6 }}>
              <button className="good" disabled={busy === o.id} onClick={() => decide(o.id, "approve")}>Approve</button>
              <button disabled={busy === o.id} onClick={() => decide(o.id, "reject")}>Reject</button>
            </div>
          )}
        </div>
      ))}
      {recent.length > 0 && (
        <details>
          <summary>Recent sleeve orders ({recent.length})</summary>
          <table><tbody>{recent.map((r) => (
            <tr key={r.id}><td>{r.id}</td><td>{r.day}</td><td>{r.sid}</td><td>{r.side} {r.qty} {r.symbol}</td><td>{r.reason}</td><td><StatusPill status={r.status} /></td>
              <td className="muted">{r.decided_by ?? "broker"}</td><td className="muted">{r.note}</td></tr>
          ))}</tbody></table>
        </details>
      )}
    </Card>
  );
}

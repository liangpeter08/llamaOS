"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { post } from "@/lib/api";
import { usePoll } from "@/lib/hooks";
import { et, money, num, pct, pnlClass, wall } from "@/lib/format";
import type { Account } from "@/lib/types";
import { PerformanceChart } from "@/components/PerformanceChart";
import { Card, Empty, Loading, Meter, Notice, Pill, Stat } from "@/components/ui";
import { isOwner, useSession } from "@/components/session";

interface Overview {
  acct: Account;
  risk: Record<string, any>;
  loss_limit: number | null;
  exposure_cap: number | null;
  curve: number[];
  positions: any[];
  health: { broker: { ok: boolean; age: number | null }; models: { ok: boolean; age: number | null }; reconcile: any; last_bar: number | null };
  recent: { id: number; ts: number; kind: string; actor: string }[];
  trades: { id: number; symbol: string; sid: string; exit_kind: string; pnl: number }[];
}

type Tab = "portfolio" | "today";

export default function OverviewPage() {
  const [tab, setTab] = useState<Tab>("portfolio");
  useEffect(() => {
    if (location.hash === "#today") setTab("today");
  }, []);
  const select = (t: Tab) => {
    setTab(t);
    history.replaceState(null, "", t === "today" ? "#today" : location.pathname);
  };
  return (
    <>
      <div className="tabs-main" role="tablist">
        <button role="tab" aria-selected={tab === "portfolio"} className={tab === "portfolio" ? "on" : ""} onClick={() => select("portfolio")}>Portfolio</button>
        <button role="tab" aria-selected={tab === "today"} className={tab === "today" ? "on" : ""} onClick={() => select("today")}>Today</button>
      </div>
      {tab === "portfolio" ? <PortfolioTab /> : <TodayTab />}
    </>
  );
}

function PortfolioTab() {
  return (
    <PerformanceChart
      title="Portfolio over time"
      ranges={["1W", "1M", "3M", "YTD", "ALL", "custom"]}
      defaultRange="ALL"
      defaultMetric="return"
      height={360}
    />
  );
}

function TodayTab() {
  const { data, error } = usePoll<Overview>("/api/overview", 2000);
  const { me, status, refresh } = useSession();
  if (!data) return <Loading error={error} />;
  const { acct, risk, health } = data;
  const lossUsed = data.loss_limit ? Math.max(0, -(acct.day_pnl ?? 0)) / data.loss_limit : 0;
  const sim = status?.sim || {};

  return (
    <>
      <div className="grid g4">
        <Card>
          <Stat label="Equity" value={money(acct.equity)} sub={`start ${money(acct.start_equity)} · cash ${money(acct.cash)}`} />
        </Card>
        <Card>
          <Stat label="Day P&L vs daily loss limit" value={money(acct.day_pnl, true)} valueClass={pnlClass(acct.day_pnl)}
            sub={`limit −${money(data.loss_limit)} (${pct(risk.max_daily_loss)})`} />
          <Meter frac={lossUsed} danger={lossUsed > 0.6} />
        </Card>
        <Card>
          <Stat label="Gross exposure" value={money(acct.gross_exposure)}
            sub={`cap ${money(data.exposure_cap)} (${risk.max_gross_exposure}× equity) · ${acct.open_positions ?? 0}/${risk.max_open_positions} positions`} />
          {data.exposure_cap ? <Meter frac={(acct.gross_exposure ?? 0) / data.exposure_cap} /> : null}
        </Card>
        <Card>
          <Stat label="Pending approvals"
            value={status?.pending_count ? <Link href="/approvals">{status.pending_count}</Link> : 0}
            sub={`${acct.trades_today ?? 0}/${risk.max_trades_per_day} trades today`} />
        </Card>
      </div>

      <div className="mt">
        <PerformanceChart title="Today's session" ranges={["1D"]} defaultRange="1D" defaultMetric="equity" height={260} poll={3000} />
      </div>

      <div className="grid g21 mt">
        <Card title="Open positions">
          {data.positions.length ? (
            <div className="table-wrap">
              <table>
                <thead><tr><th>Symbol</th><th>Strategy</th><th className="num">Qty</th><th className="num">Entry</th><th className="num">Last</th><th className="num">Stop</th><th className="num">Target</th><th className="num">Unrealized</th></tr></thead>
                <tbody>
                  {data.positions.map((p) => {
                    const u = (p.last_price - p.avg_price) * p.qty * (p.side === "long" ? 1 : -1);
                    return (
                      <tr key={p.symbol}>
                        <td><b>{p.symbol}</b></td><td>{p.sid}</td><td className="num">{p.qty}</td><td className="num">{money(p.avg_price)}</td>
                        <td className="num">{money(p.last_price)}</td><td className="num">{money(p.stop)}</td><td className="num">{money(p.target)}</td>
                        <td className={`num ${pnlClass(u)}`}>{money(u, true)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : <Empty>Flat.</Empty>}
          <h3>Recent trades</h3>
          {data.trades.length ? (
            <table><tbody>
              {data.trades.map((t) => (
                <tr key={t.id}><td><Link href={`/trades/${t.id}`}>{t.symbol}</Link></td><td className="muted">{t.sid}</td><td>{t.exit_kind}</td>
                  <td className={`num ${pnlClass(t.pnl)}`}>{money(t.pnl, true)}</td></tr>
              ))}
            </tbody></table>
          ) : <Empty>No closed trades this session.</Empty>}
        </Card>
        <Card title="System health">
          <dl className="kv">
            {(["broker", "models"] as const).map((k) => (
              <div key={k} style={{ display: "contents" }}>
                <dt>{k === "broker" ? "Broker" : "Models"}</dt>
                <dd>
                  <Pill kind={health[k].ok ? "good" : "bad"}><span className="dot" />{health[k].ok ? "up" : "down"}</Pill>{" "}
                  <span className="muted">{num(health[k].age, 1)}s</span>
                </dd>
              </div>
            ))}
            <dt>Last bar</dt><dd>{et(health.last_bar)} ET</dd>
            <dt>Reconcile</dt>
            <dd>{health.reconcile?.sim_ts ? <><Pill kind={health.reconcile.ok ? "good" : "bad"}>{health.reconcile.ok ? "match" : "MISMATCH"}</Pill> <span className="muted">{et(health.reconcile.sim_ts)}</span></> : "—"}</dd>
            <dt>Strategies</dt><dd>{sim.strategies?.join(", ") || "none running"}</dd>
          </dl>
        </Card>
      </div>

      <div className="grid g2 mt">
        <Card title="Activity">
          <table><tbody>
            {data.recent.map((a) => (
              <tr key={a.id}><td className="muted mono">{wall(a.ts)}</td><td>{a.kind}</td><td className="muted">{a.actor}</td></tr>
            ))}
          </tbody></table>
        </Card>
        {isOwner(me) && (
          <div className="grid">
            <KillSwitch active={!!status?.kill?.active} reason={status?.kill?.reason} ts={status?.kill?.ts} />
            <SimControls speed={sim.speed} paused={!!sim.paused} onChange={refresh} />
          </div>
        )}
      </div>
    </>
  );
}

function SimControls({ speed, paused, onChange }: { speed?: number; paused: boolean; onChange: () => void }) {
  const act = (a: string, value = 0) => post(`/api/sim/${a}`, { value }).then(onChange);
  return (
    <Card title="Simulator">
      <p className="muted">Replays one synthetic session for fictional tickers. Nothing here reaches a real broker.</p>
      <div className="row">
        {paused ? <button onClick={() => act("resume")}>Resume</button> : <button onClick={() => act("pause")}>Pause</button>}
        <select value={Math.round(speed ?? 30)} onChange={(e) => act("speed", Number(e.target.value))}>
          {[10, 30, 60, 120, 300].map((v) => <option key={v} value={v}>{v}× speed</option>)}
        </select>
      </div>
    </Card>
  );
}

function KillSwitch({ active, reason, ts }: { active: boolean; reason?: string; ts?: number }) {
  const [why, setWhy] = useState("");
  const [msg, setMsg] = useState<{ kind: "ok" | "bad"; text: string } | null>(null);

  async function fire(e: React.FormEvent) {
    e.preventDefault();
    if (!confirm("Trigger the kill switch? This cancels all orders and flattens every position.")) return;
    try {
      const r = await post("/api/kill", { reason: why });
      setMsg({ kind: "bad", text: r.message });
    } catch (err) {
      setMsg({ kind: "bad", text: (err as Error).message });
    }
  }

  return (
    <Card title="Kill switch" className="kill-zone">
      <p className="muted">Cancels all orders, closes all positions and suspends trading. Only the host can reset it (<code>make reset-kill-switch</code>).</p>
      {msg && <Notice kind={msg.kind}>{msg.text}</Notice>}
      {active ? (
        <Notice kind="bad">Active since {wall(ts)}: {reason}</Notice>
      ) : (
        <form className="row" onSubmit={fire}>
          <input placeholder="reason (optional)" value={why} onChange={(e) => setWhy(e.target.value)} />
          <button className="danger">Trigger kill switch</button>
        </form>
      )}
    </Card>
  );
}

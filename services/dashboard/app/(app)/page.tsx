"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { post } from "@/lib/api";
import { usePoll } from "@/lib/hooks";
import { et, money, num, pct, pnlClass, wall } from "@/lib/format";
import type { Account } from "@/lib/types";
import { useT } from "@/lib/i18n";
import { PerformanceChart } from "@/components/PerformanceChart";
import { StrategyControls } from "@/components/Allocation";
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

type Tab = "intraday" | "portfolio";

export default function OverviewPage() {
  const [tab, setTab] = useState<Tab>("intraday");
  const t = useT();
  useEffect(() => {
    if (location.hash === "#portfolio") setTab("portfolio");
  }, []);
  const select = (t: Tab) => {
    setTab(t);
    history.replaceState(null, "", t === "portfolio" ? "#portfolio" : location.pathname);
  };
  return (
    <>
      <div className="tabs-main" role="tablist">
        <button role="tab" aria-selected={tab === "intraday"} className={tab === "intraday" ? "on" : ""} onClick={() => select("intraday")}>{t("Intraday")}</button>
        <button role="tab" aria-selected={tab === "portfolio"} className={tab === "portfolio" ? "on" : ""} onClick={() => select("portfolio")}>{t("Portfolio")}</button>
      </div>
      {tab === "intraday" ? <IntradayTab /> : <PortfolioTab />}
    </>
  );
}

function PortfolioTab() {
  const { me } = useSession();
  const t = useT();
  return (
    <>
      <PerformanceChart
        title={t("Portfolio over time")}
        ranges={["1W", "1M", "3M", "YTD", "ALL", "custom"]}
        defaultRange="ALL"
        defaultMetric="return"
        height={360}
      />
      <div className="mt"><StrategyControls owner={isOwner(me)} /></div>
    </>
  );
}

function IntradayTab() {
  const { data, error } = usePoll<Overview>("/api/overview", 2000);
  const { me, status, refresh } = useSession();
  const t = useT();
  if (!data) return <Loading error={error} />;
  const { acct, risk, health } = data;
  const lossUsed = data.loss_limit ? Math.max(0, -(acct.day_pnl ?? 0)) / data.loss_limit : 0;
  const sim = status?.sim || {};

  return (
    <>
      <div className="grid g4">
        <Card>
          <Stat label={t("Equity")} value={money(acct.equity)} sub={t("start {start} · cash {cash}", { start: money(acct.start_equity), cash: money(acct.cash) })} />
        </Card>
        <Card>
          <Stat label={t("Day P&L vs daily loss limit")} value={money(acct.day_pnl, true)} valueClass={pnlClass(acct.day_pnl)}
            sub={t("limit −{limit} ({pct})", { limit: money(data.loss_limit), pct: pct(risk.max_daily_loss) })} />
          <Meter frac={lossUsed} danger={lossUsed > 0.6} />
        </Card>
        <Card>
          <Stat label={t("Gross exposure")} value={money(acct.gross_exposure)}
            sub={t("cap {cap} ({mult}× equity) · {open}/{max} positions", { cap: money(data.exposure_cap), mult: risk.max_gross_exposure, open: acct.open_positions ?? 0, max: risk.max_open_positions })} />
          {data.exposure_cap ? <Meter frac={(acct.gross_exposure ?? 0) / data.exposure_cap} /> : null}
        </Card>
        <Card>
          <Stat label={t("Pending approvals")}
            value={status?.pending_count ? <Link href="/approvals">{status.pending_count}</Link> : 0}
            sub={t("{n}/{max} trades today", { n: acct.trades_today ?? 0, max: risk.max_trades_per_day })} />
        </Card>
      </div>

      <div className="mt"><StrategyControls owner={isOwner(me)} /></div>

      <div className="mt">
        <PerformanceChart title={t("Today's session")} ranges={["1D"]} defaultRange="1D" defaultMetric="equity" height={260} poll={3000} />
      </div>

      <div className="grid g21 mt">
        <Card title={t("Open positions")}>
          {data.positions.length ? (
            <div className="table-wrap">
              <table>
                <thead><tr><th>{t("Symbol")}</th><th>{t("Strategy")}</th><th className="num">{t("Qty")}</th><th className="num">{t("Entry")}</th><th className="num">{t("Last")}</th><th className="num">{t("Stop")}</th><th className="num">{t("Target")}</th><th className="num">{t("Unrealized")}</th></tr></thead>
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
          ) : <Empty>{t("Flat.")}</Empty>}
          <h3>{t("Recent trades")}</h3>
          {data.trades.length ? (
            <table><tbody>
              {data.trades.map((tr) => (
                <tr key={tr.id}><td><Link href={`/trades/${tr.id}`}>{tr.symbol}</Link></td><td className="muted">{tr.sid}</td><td>{t(tr.exit_kind)}</td>
                  <td className={`num ${pnlClass(tr.pnl)}`}>{money(tr.pnl, true)}</td></tr>
              ))}
            </tbody></table>
          ) : <Empty>{t("No closed trades this session.")}</Empty>}
        </Card>
        <Card title={t("System health")}>
          <dl className="kv">
            {(["broker", "models"] as const).map((k) => (
              <div key={k} style={{ display: "contents" }}>
                <dt>{t(k === "broker" ? "Broker" : "Models")}</dt>
                <dd>
                  <Pill kind={health[k].ok ? "good" : "bad"}><span className="dot" />{t(health[k].ok ? "up" : "down")}</Pill>{" "}
                  <span className="muted">{num(health[k].age, 1)}s</span>
                </dd>
              </div>
            ))}
            <dt>{t("Last bar")}</dt><dd>{et(health.last_bar)} {t("ET")}</dd>
            <dt>{t("Reconcile")}</dt>
            <dd>{health.reconcile?.sim_ts ? <><Pill kind={health.reconcile.ok ? "good" : "bad"}>{t(health.reconcile.ok ? "match" : "MISMATCH")}</Pill> <span className="muted">{et(health.reconcile.sim_ts)}</span>{" "}{isOwner(me) && <ReconcileButton />}</> : "—"}</dd>
            <dt>{t("Strategies")}</dt><dd>{sim.strategies?.join(", ") || t("none running")}</dd>
          </dl>
        </Card>
      </div>

      <div className="grid g2 mt">
        <Card title={t("Activity")}>
          <table><tbody>
            {data.recent.map((a) => (
              <tr key={a.id}><td className="muted mono">{wall(a.ts)}</td><td>{t(a.kind)}</td><td className="muted">{a.actor}</td></tr>
            ))}
          </tbody></table>
        </Card>
        {isOwner(me) && (
          <div className="grid">
            <KillSwitch active={!!status?.kill?.active} reason={status?.kill?.reason} ts={status?.kill?.ts} />
            <SimControls paper={sim.source === "alpaca_paper"} speed={sim.speed} paused={!!sim.paused} onChange={refresh} />
          </div>
        )}
      </div>
    </>
  );
}

function SimControls({ paper, speed, paused, onChange }: { paper?: boolean; speed?: number; paused: boolean; onChange: () => void }) {
  const act = (a: string, value = 0) => post(`/api/sim/${a}`, { value }).then(onChange);
  const t = useT();
  return (
    <Card title={t(paper ? "Alpaca paper trading" : "Simulator")}>
      <p className="muted">{t(paper ? "Real market data and Alpaca paper orders. Pause blocks new entries; existing positions keep their exits and broker brackets." : "Replays one synthetic session for fictional tickers. Nothing here reaches a real broker.")}</p>
      <div className="row">
        {paused ? <button onClick={() => act("resume")}>{t("Resume")}</button> : <button onClick={() => act("pause")}>{t("Pause")}</button>}
        {!paper && <select value={Math.round(speed ?? 30)} onChange={(e) => act("speed", Number(e.target.value))}>
          {[10, 30, 60, 120, 300].map((v) => <option key={v} value={v}>{t("{v}× speed", { v })}</option>)}
        </select>}
      </div>
    </Card>
  );
}

function ReconcileButton() {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const t = useT();
  async function run() {
    setBusy(true);
    try {
      const r = await post("/api/reconcile/accept", {});
      setMsg(r.message);
    } catch (err) {
      setMsg((err as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <button type="button" disabled={busy} onClick={run}>{t(busy ? "Reconciling…" : "Reconcile")}</button>
      {msg && <span className="muted"> {msg}</span>}
    </>
  );
}

function KillSwitch({ active, reason, ts }: { active: boolean; reason?: string; ts?: number }) {
  const [why, setWhy] = useState("");
  const [msg, setMsg] = useState<{ kind: "ok" | "bad"; text: string } | null>(null);
  const t = useT();

  async function fire(e: React.FormEvent) {
    e.preventDefault();
    if (!confirm(t("Trigger the kill switch? This cancels all orders and flattens every position."))) return;
    try {
      const r = await post("/api/kill", { reason: why });
      setMsg({ kind: "bad", text: r.message });
    } catch (err) {
      setMsg({ kind: "bad", text: (err as Error).message });
    }
  }

  return (
    <Card title={t("Kill switch")} className="kill-zone">
      <p className="muted">{t("Cancels all orders, closes all positions and suspends trading. Only the host can reset it ({cmd}).", { cmd: <code>make reset-kill-switch</code> })}</p>
      {msg && <Notice kind={msg.kind}>{msg.text}</Notice>}
      {active ? (
        <Notice kind="bad">{t("Active since {when}: {reason}", { when: wall(ts), reason })}</Notice>
      ) : (
        <form className="row" onSubmit={fire}>
          <input placeholder={t("reason (optional)")} value={why} onChange={(e) => setWhy(e.target.value)} />
          <button className="danger">{t("Trigger kill switch")}</button>
        </form>
      )}
    </Card>
  );
}

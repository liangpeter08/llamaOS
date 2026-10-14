"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { post } from "@/lib/api";
import { usePoll } from "@/lib/hooks";
import { et, money, pct } from "@/lib/format";
import type { Check, Cond } from "@/lib/types";
import { LineChart } from "@/components/LineChart";
import { Card, Checks, Conditions, Empty, Loading, Notice, Pill, StatusPill } from "@/components/ui";
import { isOwner, useSession } from "@/components/session";
import { SleeveApprovals } from "@/components/Portfolio";
import { useT } from "@/lib/i18n";

interface Intent {
  id: number; ts: number; strategy_hash: string; sid: string; symbol: string; side: string; qty: number;
  entry: number; limit_price: number; stop: number; target: number; risk_usd: number; risk_pct: number;
  decision: { entry: Cond[]; universe: Cond[] }; signals: Record<string, any>; guard: Check[];
  mode: string; expires_wall: number; bars: number[]; last: number | null;
}

export default function Approvals() {
  const { data, error, reload } = usePoll<any>("/api/approvals", 2000);
  const [msg, setMsg] = useState<{ kind: "ok" | "bad"; text: string } | null>(null);
  const t = useT();
  if (!data) return <Loading error={error} />;
  const risk = data.risk;

  return (
    <>
      <h1>{t("Trade approvals")}</h1>
      <p className="muted">
        {t("Order intents from {manual} strategies wait here for {secs}s. On approval the broker re-runs the risk guard and a price-drift check (max {drift}) before submitting. Nothing executes if you don't act.",
          { manual: <code>manual</code>, secs: Math.round(risk.approval_expiry), drift: pct(risk.max_drift) })}
      </p>
      {msg && <Notice kind={msg.kind}>{msg.text}</Notice>}
      {data.pending.length === 0 && <Card><Empty>{t("No intents waiting.")}</Empty></Card>}
      {data.pending.map((it: Intent) => (
        <ApprovalCard key={it.id} it={it} acct={data.acct} risk={risk}
          skew={data.server_time - Date.now() / 1000}
          onDone={(kind, text) => { setMsg({ kind, text }); reload(); }} />
      ))}
      <SleeveApprovals pending={data.portfolio_pending ?? []} recent={data.portfolio_recent ?? []}
        onDone={(kind, text) => { setMsg({ kind, text }); reload(); }} />
      <Card title={t("Recent decisions")} className="mt">
        <div className="table-wrap">
          <table>
            <thead><tr><th>#</th><th>{t("Bar")}</th><th>{t("Strategy")}</th><th>{t("Symbol")}</th><th className="num">{t("Qty")}</th><th className="num">{t("Entry")}</th><th>{t("Status")}</th><th>{t("By")}</th><th>{t("Reason")}</th></tr></thead>
            <tbody>
              {data.recent.map((r: any) => (
                <tr key={r.id}><td>{r.id}</td><td className="mono">{et(r.ts)}</td><td>{r.sid}</td><td>{r.symbol}</td><td className="num">{r.qty}</td>
                  <td className="num">{money(r.entry)}</td><td><StatusPill status={r.status} /></td><td className="muted">{r.decided_by ?? t("broker")}</td><td className="muted">{r.reason}</td></tr>
              ))}
              {!data.recent.length && <tr><td colSpan={9} className="muted">{t("None yet.")}</td></tr>}
            </tbody>
          </table>
        </div>
      </Card>
    </>
  );
}

function Countdown({ until, skew }: { until: number; skew: number }) {
  const [now, setNow] = useState(() => Date.now() / 1000 + skew);
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now() / 1000 + skew), 500);
    return () => clearInterval(id);
  }, [skew]);
  const s = Math.max(0, Math.round(until - now));
  const t = useT();
  return <Pill kind={s > 20 ? "warn" : "bad"}>{s > 0 ? t("{s}s left", { s }) : t("expired")}</Pill>;
}

function ApprovalCard({ it, acct, risk, skew, onDone }: {
  it: Intent; acct: any; risk: any; skew: number; onDone: (k: "ok" | "bad", t: string) => void;
}) {
  const { me } = useSession();
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const t = useT();

  async function decide(action: "approve" | "reject") {
    setBusy(true);
    try {
      const r = await post(`/api/approvals/${it.id}/${action}`, { reason });
      onDone("ok", r.message);
    } catch (e) {
      onDone("bad", (e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const sig = Object.entries(it.signals || {});
  return (
    <Card className="approval">
      <div className="row between">
        <div>
          <h2 style={{ margin: 0 }}>#{it.id} · {t(it.side).toUpperCase()} {it.qty} {it.symbol} <span className="muted">@ {money(it.entry)}</span></h2>
          <span className="muted">{it.sid} · {t("decided on the {time} ET bar close", { time: et(it.ts) })} · {t("mode")} {it.mode?.split("_").map((m) => t(m)).join(" · ")}</span>
        </div>
        <Countdown until={it.expires_wall} skew={skew} />
      </div>
      <div className="grid g3" style={{ marginTop: 10 }}>
        <dl className="kv">
          <dt>{t("Entry (limit)")}</dt><dd>{money(it.entry)} ({money(it.limit_price)})</dd>
          <dt>{t("Stop")}</dt><dd>{money(it.stop)}</dd>
          <dt>{t("Target")}</dt><dd>{money(it.target)}</dd>
          <dt>{t("Risk")}</dt><dd>{t("${usd} = {pct} of equity", { usd: money(it.risk_usd), pct: pct(it.risk_pct, 3) })}</dd>
          <dt>{t("Notional")}</dt><dd>${money(it.qty * it.entry)}</dd>
          <dt>{t("Last price")}</dt><dd>{money(it.last)} {it.last && <span className="muted">({t("{pct} drift", { pct: pct(it.last / it.entry - 1, 2, true) })})</span>}</dd>
          <dt>{t("Exposure")}</dt><dd>${money(acct.gross_exposure)} / ${money(risk.max_gross_exposure * (acct.equity ?? 0))}</dd>
        </dl>
        <LineChart values={it.bars} width={420} height={170} levels={{ entry: it.entry, stop: it.stop, target: it.target }} />
        <div>
          <h3 style={{ marginTop: 0 }}>{t("Conditions")}</h3>
          <Conditions items={it.decision?.entry} />
          <Conditions items={it.decision?.universe} />
        </div>
      </div>
      {sig.length > 0 && (
        <>
          <h3>{t("Signals used")}</h3>
          <table>
            <thead><tr><th>{t("Signal")}</th><th>{t("Value")}</th><th>{t("Model")}</th><th>{t("Tier")}</th><th>{t("Rationale")}</th></tr></thead>
            <tbody>
              {sig.map(([name, s]) => (
                <tr key={name}><td><code>{name}</code></td><td>{String(s.value)}</td><td className="muted">{s.model ?? "—"}</td><td>{s.tier ?? "—"}</td><td className="muted">{s.rationale}</td></tr>
              ))}
            </tbody>
          </table>
        </>
      )}
      <details style={{ marginTop: 8 }}>
        <summary>{t("Risk guard ({ok}/{n} passed)", { ok: it.guard.filter((g) => g.ok).length, n: it.guard.length })}</summary>
        <Checks items={it.guard} />
      </details>
      {isOwner(me) && (
        <div className="row" style={{ marginTop: 12 }}>
          <input placeholder={t("reason (optional)")} value={reason} onChange={(e) => setReason(e.target.value)} style={{ flex: 1, minWidth: 180 }} />
          <button className="good" disabled={busy} onClick={() => decide("approve")}>{t("Approve")}</button>
          <button disabled={busy} onClick={() => decide("reject")}>{t("Reject")}</button>
          <Link className="btn" href={`/strategies/${it.strategy_hash}#chat`}>{t("Ask Kimi")}</Link>
        </div>
      )}
    </Card>
  );
}

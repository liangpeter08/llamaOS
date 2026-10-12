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

interface Intent {
  id: number; ts: number; strategy_hash: string; sid: string; symbol: string; side: string; qty: number;
  entry: number; limit_price: number; stop: number; target: number; risk_usd: number; risk_pct: number;
  decision: { entry: Cond[]; universe: Cond[] }; signals: Record<string, any>; guard: Check[];
  mode: string; expires_wall: number; bars: number[]; last: number | null;
}

export default function Approvals() {
  const { data, error, reload } = usePoll<any>("/api/approvals", 2000);
  const [msg, setMsg] = useState<{ kind: "ok" | "bad"; text: string } | null>(null);
  if (!data) return <Loading error={error} />;
  const risk = data.risk;

  return (
    <>
      <h1>Trade approvals</h1>
      <p className="muted">
        Order intents from <code>manual</code> strategies wait here for {Math.round(risk.approval_expiry)}s. On approval the broker re-runs the
        risk guard and a price-drift check (max {pct(risk.max_drift)}) before submitting. Nothing executes if you don't act.
      </p>
      {msg && <Notice kind={msg.kind}>{msg.text}</Notice>}
      {data.pending.length === 0 && <Card><Empty>No intents waiting.</Empty></Card>}
      {data.pending.map((it: Intent) => (
        <ApprovalCard key={it.id} it={it} acct={data.acct} risk={risk}
          skew={data.server_time - Date.now() / 1000}
          onDone={(kind, text) => { setMsg({ kind, text }); reload(); }} />
      ))}
      <SleeveApprovals pending={data.portfolio_pending ?? []} recent={data.portfolio_recent ?? []}
        onDone={(kind, text) => { setMsg({ kind, text }); reload(); }} />
      <Card title="Recent decisions" className="mt">
        <div className="table-wrap">
          <table>
            <thead><tr><th>#</th><th>Bar</th><th>Strategy</th><th>Symbol</th><th className="num">Qty</th><th className="num">Entry</th><th>Status</th><th>By</th><th>Reason</th></tr></thead>
            <tbody>
              {data.recent.map((r: any) => (
                <tr key={r.id}><td>{r.id}</td><td className="mono">{et(r.ts)}</td><td>{r.sid}</td><td>{r.symbol}</td><td className="num">{r.qty}</td>
                  <td className="num">{money(r.entry)}</td><td><StatusPill status={r.status} /></td><td className="muted">{r.decided_by ?? "broker"}</td><td className="muted">{r.reason}</td></tr>
              ))}
              {!data.recent.length && <tr><td colSpan={9} className="muted">None yet.</td></tr>}
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
  return <Pill kind={s > 20 ? "warn" : "bad"}>{s > 0 ? `${s}s left` : "expired"}</Pill>;
}

function ApprovalCard({ it, acct, risk, skew, onDone }: {
  it: Intent; acct: any; risk: any; skew: number; onDone: (k: "ok" | "bad", t: string) => void;
}) {
  const { me } = useSession();
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);

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
          <h2 style={{ margin: 0 }}>#{it.id} · {it.side.toUpperCase()} {it.qty} {it.symbol} <span className="muted">@ {money(it.entry)}</span></h2>
          <span className="muted">{it.sid} · decided on the {et(it.ts)} ET bar close · mode {it.mode?.replace("_", " · ")}</span>
        </div>
        <Countdown until={it.expires_wall} skew={skew} />
      </div>
      <div className="grid g3" style={{ marginTop: 10 }}>
        <dl className="kv">
          <dt>Entry (limit)</dt><dd>{money(it.entry)} ({money(it.limit_price)})</dd>
          <dt>Stop</dt><dd>{money(it.stop)}</dd>
          <dt>Target</dt><dd>{money(it.target)}</dd>
          <dt>Risk</dt><dd>${money(it.risk_usd)} = {pct(it.risk_pct, 3)} of equity</dd>
          <dt>Notional</dt><dd>${money(it.qty * it.entry)}</dd>
          <dt>Last price</dt><dd>{money(it.last)} {it.last && <span className="muted">({pct(it.last / it.entry - 1, 2, true)} drift)</span>}</dd>
          <dt>Exposure</dt><dd>${money(acct.gross_exposure)} / ${money(risk.max_gross_exposure * (acct.equity ?? 0))}</dd>
        </dl>
        <LineChart values={it.bars} width={420} height={170} levels={{ entry: it.entry, stop: it.stop, target: it.target }} />
        <div>
          <h3 style={{ marginTop: 0 }}>Conditions</h3>
          <Conditions items={it.decision?.entry} />
          <Conditions items={it.decision?.universe} />
        </div>
      </div>
      {sig.length > 0 && (
        <>
          <h3>Signals used</h3>
          <table>
            <thead><tr><th>Signal</th><th>Value</th><th>Model</th><th>Tier</th><th>Rationale</th></tr></thead>
            <tbody>
              {sig.map(([name, s]) => (
                <tr key={name}><td><code>{name}</code></td><td>{String(s.value)}</td><td className="muted">{s.model ?? "—"}</td><td>{s.tier ?? "—"}</td><td className="muted">{s.rationale}</td></tr>
              ))}
            </tbody>
          </table>
        </>
      )}
      <details style={{ marginTop: 8 }}>
        <summary>Risk guard ({it.guard.filter((g) => g.ok).length}/{it.guard.length} passed)</summary>
        <Checks items={it.guard} />
      </details>
      {isOwner(me) && (
        <div className="row" style={{ marginTop: 12 }}>
          <input placeholder="reason (optional)" value={reason} onChange={(e) => setReason(e.target.value)} style={{ flex: 1, minWidth: 180 }} />
          <button className="good" disabled={busy} onClick={() => decide("approve")}>Approve</button>
          <button disabled={busy} onClick={() => decide("reject")}>Reject</button>
          <Link className="btn" href={`/strategies/${it.strategy_hash}#chat`}>Ask Claude</Link>
        </div>
      )}
    </Card>
  );
}

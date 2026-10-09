"use client";
import { Fragment } from "react";
import { usePoll } from "@/lib/hooks";
import { et, money, num, short, wall } from "@/lib/format";
import { Card, Loading, Pill } from "@/components/ui";

export default function System() {
  const { data, error } = usePoll<any>("/api/system", 3000);
  if (!data) return <Loading error={error} />;
  const h = data.health;
  return (
    <>
      <h1>System</h1>
      <div className="grid g3">
        <Card title="Services">
          <dl className="kv">
            <dt>Broker</dt><dd><Pill kind={h.broker.ok ? "good" : "bad"}>{h.broker.ok ? "up" : "down"}</Pill> heartbeat {num(h.broker.age, 1)}s</dd>
            <dt>Models</dt><dd><Pill kind={h.models.ok ? "good" : "bad"}>{h.models.ok ? "up" : "down"}</Pill> heartbeat {num(h.models.age, 1)}s</dd>
            <dt>Feed</dt><dd>simulator · last bar {et(h.last_bar)} ET</dd>
            <dt>Reconcile</dt><dd>{h.reconcile?.sim_ts ? `${h.reconcile.ok ? "match" : "MISMATCH"} at ${et(h.reconcile.sim_ts)}` : "—"}</dd>
            <dt>Suspend flag</dt><dd>{data.suspend_trade ? <Pill kind="bad">set</Pill> : "clear"}</dd>
            <dt>Backups</dt><dd className="muted">not configured in the prototype</dd>
          </dl>
        </Card>
        <Card title="Database">
          <dl className="kv">
            <dt>Engine</dt><dd>{data.database.engine} {data.database.version}</dd>
            <dt>Size</dt><dd>{data.database.size}</dd>
            <dt>History</dt><dd>{data.history.history_from} → {data.history.history_to} ({data.history.history_days} sessions, seed {data.history.seed})</dd>
            <dt>Live session</dt><dd>{data.history.session}</dd>
            <dt>risk.yaml</dt><dd className="mono">{short(data.risk_sha256)}…</dd>
          </dl>
        </Card>
        <Card title="Today's news pipeline">
          <dl className="kv">{Object.entries(data.triage).map(([k, v]) => <Fragment key={k}><dt>{k}</dt><dd>{String(v)}</dd></Fragment>)}</dl>
        </Card>
      </div>
      <Card title="Model providers" className="mt">
        {data.providers.error ? <p className="neg">{data.providers.error}</p> : (
          <table>
            <thead><tr><th>Provider</th><th>Available</th><th>Model</th><th>Note</th></tr></thead>
            <tbody>{data.providers.providers.map((p: any) => (
              <tr key={p.name}><td>{p.name}</td><td>{p.available ? <Pill kind="good">yes</Pill> : <Pill>no</Pill>}</td><td className="mono">{p.model ?? "—"}</td><td className="muted">{p.note}</td></tr>
            ))}</tbody>
          </table>
        )}
      </Card>
      <Card title="Model calls" className="mt">
        <table>
          <thead><tr><th>Provider</th><th>Model</th><th>Task</th><th className="num">Calls</th><th className="num">Avg latency</th><th className="num">Errors</th><th className="num">Cost</th></tr></thead>
          <tbody>
            {data.calls.map((c: any, i: number) => (
              <tr key={i}><td>{c.provider}</td><td className="mono">{c.model}</td><td>{c.task}</td><td className="num">{c.n}</td><td className="num">{num(c.lat, 0)} ms</td><td className="num">{c.errors}</td><td className="num">${money(c.cost)}</td></tr>
            ))}
            {!data.calls.length && <tr><td colSpan={7} className="muted">No paid model calls yet (the offline heuristic model doesn't log calls).</td></tr>}
          </tbody>
        </table>
        {data.errors.length > 0 && (<><h3>Recent errors</h3><table><tbody>{data.errors.map((e: any, i: number) => <tr key={i}><td className="mono">{wall(e.ts)}</td><td>{e.provider}/{e.task}</td><td className="neg">{e.error}</td></tr>)}</tbody></table></>)}
      </Card>
    </>
  );
}

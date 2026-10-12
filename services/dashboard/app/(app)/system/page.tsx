"use client";
import { Fragment, useState } from "react";
import { post } from "@/lib/api";
import { usePoll } from "@/lib/hooks";
import { isOwner, useSession } from "@/components/session";
import { et, money, num, short, wall } from "@/lib/format";
import { Card, Loading, Notice, Pill } from "@/components/ui";
import { NewsToClaude } from "@/components/NewsToClaude";

export default function System() {
  const { data, error, reload } = usePoll<any>("/api/system", 3000);
  const { me } = useSession();
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
            <dt>Feed</dt><dd>{data.source === "alpaca_paper" ? "Alpaca IEX" : "simulator"} · last bar {et(h.last_bar)} ET</dd>
            <dt>Reconcile</dt><dd>{h.reconcile?.sim_ts ? `${h.reconcile.ok ? "match" : "MISMATCH"} at ${et(h.reconcile.sim_ts)}` : "—"}</dd>
            <dt>Suspend flag</dt><dd>{data.suspend_trade ? <Pill kind="bad">set</Pill> : "clear"}</dd>
            <dt>Notifications</dt><dd>{h.operations?.notifications_configured ? "delivery configured" : "dashboard only"}</dd>
            <dt>Triage</dt><dd>{h.triage?.age != null ? `${num(h.triage.age, 0)}s since last batch` : "warming up"}</dd>
            <dt>Watchdog</dt><dd>{h.watchdog?.ok ? "up" : "not running"}</dd>
            <dt>Backups</dt><dd>{h.backup ? `${wall(h.backup.ts)} · ${h.backup.off_host ? "off-host" : "local"}` : "no backup yet"}</dd>
            {h.paper_error && <><dt>Broker error</dt><dd className="neg">{h.paper_error.type}</dd></>}
          </dl>
        </Card>
        <Card title="Database">
          <dl className="kv">
            <dt>Engine</dt><dd>{data.database.engine} {data.database.version}</dd>
            <dt>Size</dt><dd>{data.database.size}</dd>
            <dt>History</dt><dd>{data.source === "alpaca_paper" ? "Recorded Alpaca data" : `${data.history.history_from} → ${data.history.history_to} (${data.history.history_days} sessions)`}</dd>
            <dt>Live session</dt><dd>{data.history.session}</dd>
            <dt>risk.yaml</dt><dd className="mono">{short(data.risk_sha256)}…</dd>
          </dl>
        </Card>
        <Card title="Today's news pipeline">
          <dl className="kv">{Object.entries(data.triage).map(([k, v]) => <Fragment key={k}><dt>{k}</dt><dd>{String(v)}</dd></Fragment>)}</dl>
        </Card>
      </div>
      <BrokerAccounts owner={isOwner(me)} />
      <Card title="Model providers" className="mt">
        {data.news_claude && <NewsToClaude status={data.news_claude} owner={isOwner(me)} reload={reload} />}
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

function BrokerAccounts({ owner }: { owner: boolean }) {
  const { data, error, reload } = usePoll<any>("/api/broker-accounts", 5000);
  const [form, setForm] = useState({ label: "", key_id: "", secret: "", code: "", activate: true });
  const [switching, setSwitching] = useState<any>(null);
  const [code, setCode] = useState("");
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ kind: "ok" | "bad"; text: string } | null>(null);
  if (!data) return <Card title="Alpaca paper account" className="mt"><Loading error={error} /></Card>;
  async function run(fn: () => Promise<any>, after?: () => void) {
    setBusy(true);
    setMsg(null);
    try { const r = await fn(); setMsg({ kind: "ok", text: r.message }); after?.(); reload(); }
    catch (x) { setMsg({ kind: "bad", text: (x as Error).message }); }
    finally { setBusy(false); }
  }
  const set = (k: string) => (e: any) => setForm((f) => ({ ...f, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value }));
  const canAdd = form.label.trim() && form.key_id.trim().length >= 10 && form.secret.trim().length >= 20 && form.code.length >= 6;
  return (
    <Card title="Alpaca paper account" className="mt">
      {msg && <Notice kind={msg.kind}>{msg.text}</Notice>}
      {(data.error === "AlpacaAuthFailed" || data.error === "NoAlpacaAccount") && (
        <Notice kind="bad">{data.error === "AlpacaAuthFailed" ? "Alpaca rejected the active account's keys." : "No Alpaca account is set up."} The broker is waiting: add or switch to a working account below.</Notice>
      )}
      <table>
        <thead><tr><th>Account</th><th>Alpaca #</th><th>Key</th><th>Added</th><th></th></tr></thead>
        <tbody>
          {data.accounts.map((a: any) => (
            <tr key={a.id}>
              <td>{a.label} {a.active ? <Pill kind="good">active</Pill> : null}</td>
              <td className="mono">{a.account_number ?? "—"}</td>
              <td className="mono">{a.key_id}</td>
              <td className="muted">{wall(a.created_at)} · {a.created_by}</td>
              <td className="num">{owner && !a.active && (
                <>
                  <button disabled={busy} onClick={() => { setSwitching(a); setCode(""); setConfirmOpen(false); }}>Switch to</button>{" "}
                  <button disabled={busy} onClick={() => confirm(`Remove ${a.label}?`) && run(() => post(`/api/broker-accounts/${a.id}/delete`))}>Remove</button>
                </>
              )}</td>
            </tr>
          ))}
          {!data.accounts.length && <tr><td colSpan={5} className="muted">No saved accounts; the broker uses the keys in secrets/broker.env.</td></tr>}
        </tbody>
      </table>
      {owner && switching && (
        <div className="row mt" style={{ gap: 8, flexWrap: "wrap" }}>
          <span>Switch to <b>{switching.label}</b>?</span>
          {data.open_positions > 0 && (
            <label className="row" style={{ gap: 4 }}>
              <input type="checkbox" checked={confirmOpen} onChange={(e) => setConfirmOpen(e.target.checked)} />
              leave {data.open_positions} open position(s) unmanaged on the current account
            </label>
          )}
          <input placeholder="2FA code" inputMode="numeric" autoComplete="one-time-code" value={code} onChange={(e) => setCode(e.target.value)} style={{ width: 110 }} />
          <button className="primary" disabled={busy || code.length < 6 || (data.open_positions > 0 && !confirmOpen)}
            onClick={() => run(() => post(`/api/broker-accounts/${switching.id}/activate`, { code, confirm_open: confirmOpen }), () => setSwitching(null))}>Switch</button>
          <button disabled={busy} onClick={() => setSwitching(null)}>Cancel</button>
        </div>
      )}
      {owner && (data.vault_ready ? (
        <>
          <h3>Add a paper account</h3>
          <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
            <input placeholder="label" value={form.label} onChange={set("label")} style={{ width: 160 }} />
            <input placeholder="API key ID" className="mono" autoComplete="off" value={form.key_id} onChange={set("key_id")} style={{ width: 240 }} />
            <input placeholder="secret key" type="password" className="mono" autoComplete="new-password" value={form.secret} onChange={set("secret")} style={{ width: 240 }} />
            <input placeholder="2FA code" inputMode="numeric" autoComplete="one-time-code" value={form.code} onChange={set("code")} style={{ width: 110 }} />
            <label className="row" style={{ gap: 4 }}><input type="checkbox" checked={form.activate} onChange={set("activate")} /> switch to it</label>
            <button className="primary" disabled={busy || !canAdd}
              onClick={() => run(() => post("/api/broker-accounts", { ...form, key_id: form.key_id.trim(), secret: form.secret.trim() }),
                () => setForm({ label: "", key_id: "", secret: "", code: "", activate: true }))}>{busy ? "Checking…" : "Save"}</button>
          </div>
          <p className="muted" style={{ margin: "6px 0 0", fontSize: 12 }}>
            Keys are checked against Alpaca paper first (live keys are refused), then stored encrypted so only the broker can read them.
            Each account keeps its own portfolio history, trades, fund allocations, risk controls and running strategies; a new
            account starts empty with a copy of the current risk controls. Strategy files and backtests are shared.
            Switching expires pending intents and restarts the broker; it sets 1x margin (and no shorting unless allowed) on the new account.
          </p>
        </>
      ) : <p className="muted">Start the paper broker once (make paper) to enable adding accounts here.</p>)}
    </Card>
  );
}

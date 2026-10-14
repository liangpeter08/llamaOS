"use client";
import { Fragment, useState } from "react";
import { post } from "@/lib/api";
import { usePoll } from "@/lib/hooks";
import { isOwner, useSession } from "@/components/session";
import { et, money, num, short, wall } from "@/lib/format";
import { Card, Loading, Notice, Pill } from "@/components/ui";
import { NewsToClaude } from "@/components/NewsToClaude";
import { useT } from "@/lib/i18n";

export default function System() {
  const { data, error, reload } = usePoll<any>("/api/system", 3000);
  const { me } = useSession();
  const t = useT();
  if (!data) return <Loading error={error} />;
  const h = data.health;
  return (
    <>
      <h1>{t("System")}</h1>
      <div className="grid g3">
        <Card title={t("Services")}>
          <dl className="kv">
            <dt>{t("Broker")}</dt><dd><Pill kind={h.broker.state === "up" ? "good" : h.broker.state === "slow" ? "warn" : "bad"}
              title={h.broker.state === "slow" ? t("Process is running but its loop is late, usually slow or failing Alpaca requests") : undefined}>{t(h.broker.state)}</Pill> {t("last loop {s}s ago", { s: num(h.broker.age, 1) })}</dd>
            <dt>{t("Models")}</dt><dd><Pill kind={h.models.ok ? "good" : "bad"}>{t(h.models.ok ? "up" : "down")}</Pill> {t("heartbeat {s}s", { s: num(h.models.age, 1) })}</dd>
            <dt>{t("Feed")}</dt><dd>{data.source === "alpaca_paper" ? "Alpaca IEX" : t("simulator")} · {t("last bar {time} ET", { time: et(h.last_bar) })}</dd>
            <dt>{t("Reconcile")}</dt><dd>{h.reconcile?.sim_ts ? t("{state} at {time}", { state: t(h.reconcile.ok ? "match" : "MISMATCH"), time: et(h.reconcile.sim_ts) }) : "—"}</dd>
            <dt>{t("Suspend flag")}</dt><dd>{data.suspend_trade ? <Pill kind="bad">{t("set")}</Pill> : t("clear")}</dd>
            <dt>{t("Notifications")}</dt><dd>{t(h.operations?.notifications_configured ? "delivery configured" : "dashboard only")}</dd>
            <dt>{t("Triage")}</dt><dd>{h.triage?.age != null ? t("{s}s since last batch", { s: num(h.triage.age, 0) }) : t("warming up")}</dd>
            <dt>{t("Watchdog")}</dt><dd>{t(h.watchdog?.ok ? "up" : "not running")}</dd>
            <dt>{t("Backups")}</dt><dd>{h.backup ? `${wall(h.backup.ts)} · ${t(h.backup.off_host ? "off-host" : "local")}` : t("no backup yet")}</dd>
            {h.paper_error && <><dt>{t("Broker error")}</dt><dd className="neg">{h.paper_error.type}</dd></>}
          </dl>
        </Card>
        <Card title={t("Database")}>
          <dl className="kv">
            <dt>{t("Engine")}</dt><dd>{data.database.engine} {data.database.version}</dd>
            <dt>{t("Size")}</dt><dd>{data.database.size}</dd>
            <dt>{t("History")}</dt><dd>{data.source === "alpaca_paper" ? t("Recorded Alpaca data") : t("{from} → {to} ({n} sessions)", { from: data.history.history_from, to: data.history.history_to, n: data.history.history_days })}</dd>
            <dt>{t("Live session")}</dt><dd>{data.history.session}</dd>
            <dt>risk.yaml</dt><dd className="mono">{short(data.risk_sha256)}…</dd>
          </dl>
        </Card>
        <Card title={t("Today's news pipeline")}>
          <dl className="kv">{Object.entries(data.triage).map(([k, v]) => <Fragment key={k}><dt>{t(k)}</dt><dd>{String(v)}</dd></Fragment>)}</dl>
        </Card>
      </div>
      <BrokerAccounts owner={isOwner(me)} />
      <Card title={t("Model providers")} className="mt">
        {data.news_claude && <NewsToClaude status={data.news_claude} owner={isOwner(me)} reload={reload} />}
        {data.providers.error ? <p className="neg">{data.providers.error}</p> : (
          <table>
            <thead><tr><th>{t("Provider")}</th><th>{t("Available")}</th><th>{t("Model")}</th><th>{t("Note")}</th></tr></thead>
            <tbody>{data.providers.providers.map((p: any) => (
              <tr key={p.name}><td>{p.name}</td><td>{p.available ? <Pill kind="good">{t("yes")}</Pill> : <Pill>{t("no")}</Pill>}</td><td className="mono">{p.model ?? "—"}</td><td className="muted">{p.note}</td></tr>
            ))}</tbody>
          </table>
        )}
      </Card>
      <Card title={t("Model calls")} className="mt">
        <table>
          <thead><tr><th>{t("Provider")}</th><th>{t("Model")}</th><th>{t("Task")}</th><th className="num">{t("Calls")}</th><th className="num">{t("Avg latency")}</th><th className="num">{t("Errors")}</th><th className="num">{t("Cost")}</th></tr></thead>
          <tbody>
            {data.calls.map((c: any, i: number) => (
              <tr key={i}><td>{c.provider}</td><td className="mono">{c.model}</td><td>{c.task}</td><td className="num">{c.n}</td><td className="num">{num(c.lat, 0)} ms</td><td className="num">{c.errors}</td><td className="num">${money(c.cost)}</td></tr>
            ))}
            {!data.calls.length && <tr><td colSpan={7} className="muted">{t("No paid model calls yet (the offline heuristic model doesn't log calls).")}</td></tr>}
          </tbody>
        </table>
        {data.errors.length > 0 && (<><h3>{t("Recent errors")}</h3><table><tbody>{data.errors.map((e: any, i: number) => <tr key={i}><td className="mono">{wall(e.ts)}</td><td>{e.provider}/{e.task}</td><td className="neg">{e.error}</td></tr>)}</tbody></table></>)}
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
  const t = useT();
  if (!data) return <Card title={t("Alpaca paper account")} className="mt"><Loading error={error} /></Card>;
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
    <Card title={t("Alpaca paper account")} className="mt">
      {msg && <Notice kind={msg.kind}>{msg.text}</Notice>}
      {(data.error === "AlpacaAuthFailed" || data.error === "NoAlpacaAccount") && (
        <Notice kind="bad">{t(data.error === "AlpacaAuthFailed" ? "Alpaca rejected the active account's keys." : "No Alpaca account is set up.")} {t("The broker is waiting: add or switch to a working account below.")}</Notice>
      )}
      <table>
        <thead><tr><th>{t("Account")}</th><th>{t("Alpaca #")}</th><th>{t("Key")}</th><th>{t("Added")}</th><th></th></tr></thead>
        <tbody>
          {data.accounts.map((a: any) => (
            <tr key={a.id}>
              <td>{a.label} {a.active ? <Pill kind="good">{t("active")}</Pill> : null}</td>
              <td className="mono">{a.account_number ?? "—"}</td>
              <td className="mono">{a.key_id}</td>
              <td className="muted">{wall(a.created_at)} · {a.created_by}</td>
              <td className="num">{owner && !a.active && (
                <>
                  <button disabled={busy} onClick={() => { setSwitching(a); setCode(""); setConfirmOpen(false); }}>{t("Switch to")}</button>{" "}
                  <button disabled={busy} onClick={() => confirm(t("Remove {label}?", { label: a.label })) && run(() => post(`/api/broker-accounts/${a.id}/delete`))}>{t("Remove")}</button>
                </>
              )}</td>
            </tr>
          ))}
          {!data.accounts.length && <tr><td colSpan={5} className="muted">{t("No saved accounts; the broker uses the keys in secrets/broker.env.")}</td></tr>}
        </tbody>
      </table>
      {owner && switching && (
        <div className="row mt" style={{ gap: 8, flexWrap: "wrap" }}>
          <span>{t("Switch to {label}?", { label: <b>{switching.label}</b> })}</span>
          {data.open_positions > 0 && (
            <label className="row" style={{ gap: 4 }}>
              <input type="checkbox" checked={confirmOpen} onChange={(e) => setConfirmOpen(e.target.checked)} />
              {t("leave {n} open position(s) unmanaged on the current account", { n: data.open_positions })}
            </label>
          )}
          <input placeholder={t("2FA code")} inputMode="numeric" autoComplete="one-time-code" value={code} onChange={(e) => setCode(e.target.value)} style={{ width: 110 }} />
          <button className="primary" disabled={busy || code.length < 6 || (data.open_positions > 0 && !confirmOpen)}
            onClick={() => run(() => post(`/api/broker-accounts/${switching.id}/activate`, { code, confirm_open: confirmOpen }), () => setSwitching(null))}>{t("Switch")}</button>
          <button disabled={busy} onClick={() => setSwitching(null)}>{t("Cancel")}</button>
        </div>
      )}
      {owner && (data.vault_ready ? (
        <>
          <h3>{t("Add a paper account")}</h3>
          <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
            <input placeholder={t("label")} value={form.label} onChange={set("label")} style={{ width: 160 }} />
            <input placeholder={t("API key ID")} className="mono" autoComplete="off" value={form.key_id} onChange={set("key_id")} style={{ width: 240 }} />
            <input placeholder={t("secret key")} type="password" className="mono" autoComplete="new-password" value={form.secret} onChange={set("secret")} style={{ width: 240 }} />
            <input placeholder={t("2FA code")} inputMode="numeric" autoComplete="one-time-code" value={form.code} onChange={set("code")} style={{ width: 110 }} />
            <label className="row" style={{ gap: 4 }}><input type="checkbox" checked={form.activate} onChange={set("activate")} /> {t("switch to it")}</label>
            <button className="primary" disabled={busy || !canAdd}
              onClick={() => run(() => post("/api/broker-accounts", { ...form, key_id: form.key_id.trim(), secret: form.secret.trim() }),
                () => setForm({ label: "", key_id: "", secret: "", code: "", activate: true }))}>{t(busy ? "Checking…" : "Save")}</button>
          </div>
          <p className="muted" style={{ margin: "6px 0 0", fontSize: 12 }}>
            {t("Keys are checked against Alpaca paper first (live keys are refused), then stored encrypted so only the broker can read them. Each account keeps its own portfolio history, trades, fund allocations, risk controls and running strategies; a new account starts empty with a copy of the current risk controls. Strategy files and backtests are shared. Switching expires pending intents and restarts the broker; it sets 1x margin (and no shorting unless allowed) on the new account.")}
          </p>
        </>
      ) : <p className="muted">{t("Start the paper broker once (make paper) to enable adding accounts here.")}</p>)}
    </Card>
  );
}

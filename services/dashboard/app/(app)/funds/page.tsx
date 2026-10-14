"use client";
import Link from "next/link";
import { useState } from "react";
import { post } from "@/lib/api";
import { usePoll } from "@/lib/hooks";
import { money, pct, pnlClass, wall } from "@/lib/format";
import { Card, Empty, Loading, Meter, Notice, Pill, Stat, StatusPill } from "@/components/ui";
import { isOwner, useSession } from "@/components/session";
import { useT } from "@/lib/i18n";
import { AllocationInput, ReservedInput, activityText, allocText, allocValue, ceilingUsd, type AllocMode } from "@/components/Allocation";

export default function Funds() {
  const { data, error, reload } = usePoll<any>("/api/funds", 5000);
  const { me } = useSession();
  const [msg, setMsg] = useState<{ kind: "ok" | "bad"; text: string } | null>(null);
  const t = useT();
  if (!data) return <Loading error={error} />;
  const a = data.account, L = data.limits;
  const done = (kind: "ok" | "bad", text: string) => { setMsg({ kind, text }); reload(); };

  return (
    <>
      <h1>{t("Funds")}</h1>
      <p className="muted">
        {t("Where the money is. Each running strategy has an allocation (a percent of the fund or a fixed dollar amount): the most it may hold. The reserved part of it is held for that strategy even while it is idle; above that it borrows from the shared pool, the part of max deployed no other strategy has reserved or is using. So a strategy that seldom trades can have a large allocation with a small reserve, and when it does trade it never touches another strategy's reserve. A cash reserve is never deployed. Account-wide limits live on the {risk} page.",
          { risk: <Link href="/risk">{t("Risk")}</Link> })}
      </p>
      {msg && <Notice kind={msg.kind}>{msg.text}</Notice>}
      {!data.enabled ? <Notice kind="warn">{t("Fund controls are off.")}</Notice> : (
        <>
          <div className="grid g4">
            <Stat label={t("Equity")} value={`$${money(a.equity)}`} sub={t("peak ${v}", { v: money(a.peak_equity) })} />
            <Stat label={t("Cash")} value={`$${money(a.cash)}`} sub={t("reserve ${v} ({pct}) never deployed", { v: money(a.reserve_usd), pct: pct(L.cash_reserve, 0) })} />
            <Stat label={t("Deployed")} value={`$${money(a.deployed)}`} sub={t("{pct} of equity · max {max}", { pct: pct(a.equity ? a.deployed / a.equity : null, 1), max: pct(L.max_deployed, 0) })} />
            <Stat label={t("Available to deploy")} value={`$${money(a.available)}`} sub={t("working buys ${v}", { v: money(a.working_buys) })} />
          </div>
          <Card title={t("Capital")} className="mt">
            <CapitalBar a={a} L={L} />
            <div className="grid g4" style={{ marginTop: 12 }}>
              <Budget label={t("Reserved by strategies")} used={a.allocated} cap={L.max_deployed} fmt="pct"
                note={t("· allocations add up to {pct}", { pct: pct(a.ceilings, 1) })} />
              <Budget label={t("Shared pool in use")} used={Math.max(0, L.max_deployed * a.equity - a.pool_free_usd - a.allocated * a.equity)}
                cap={Math.max(0, (L.max_deployed - a.allocated) * a.equity)} />
              <Budget label={t("Intraday deployed")} used={a.intraday_deployed} cap={L.intraday_budget * a.equity} />
              <Budget label={t("Sleeves deployed")} used={a.sleeve_deployed} cap={a.sleeve_cap_usd} />
              <Budget label={t("Account drawdown")} used={a.drawdown} cap={L.account_max_drawdown} fmt="pct" note={t("→ kill switch")} />
            </div>
          </Card>
        </>
      )}

      <Card title={t("Strategy allocations")} className="mt">
        {!data.strategies.length ? <Empty>{t("No strategy is running. Promoting one to paper gives it an allocation.")}</Empty> : (
          <div className="table-wrap"><table>
            <thead><tr><th>{t("Strategy")}</th><th>{t("Status")}</th><th className="num">{t("Allocation")}</th><th className="num">{t("Reserved")}</th><th>{t("Deployed")}</th><th className="num">{t("P&L since set")}</th>
              <th className="num">{t("Today")}</th><th>{t("Drawdown")}</th><th className="num">{t("Daily loss stop")}</th><th></th></tr></thead>
            <tbody>{data.strategies.map((r: any) => <Row key={r.sid} r={r} equity={a.equity} owner={isOwner(me)} done={done} />)}</tbody>
          </table></div>
        )}
        <p className="muted">
          {t("Drawdown = loss from the strategy's best P&L since its allocation was set, as a share of the allocation; reaching its stop pauses the strategy (a portfolio sleeve is sold). The daily loss stop (intraday only) blocks new entries for the rest of the day. Changing an allocation or resuming restarts the measurement.")}
        </p>
      </Card>

      <Card title={t("Fund events")} className="mt">
        <table><tbody>
          {data.history.map((h: any) => <tr key={h.id}><td>{wall(h.ts)}</td><td>{t(h.kind)}</td><td>{h.actor}</td><td className="muted mono">{h.payload}</td></tr>)}
          {!data.history.length && <tr><td className="muted">{t("None yet.")}</td></tr>}
        </tbody></table>
      </Card>
    </>
  );
}

function CapitalBar({ a, L }: { a: any; L: any }) {
  const eq = a.equity || 1;
  const reservedIdle = Math.max(0, a.committed - a.deployed);
  const t = useT();
  const parts: [string, number, string][] = [
    ["Intraday deployed", a.intraday_deployed, "var(--accent, #4c8bf5)"],
    ["Sleeves deployed", a.sleeve_deployed, "var(--good, #2ea56b)"],
    ["Reserved, idle", reservedIdle, "rgba(127,127,127,.35)"],
    ["Shared pool free", a.pool_free_usd, "rgba(127,127,127,.15)"],
    ["Cash reserve", L.cash_reserve * eq, "var(--warn, #d9a400)"],
  ];
  return (
    <>
      <div style={{ display: "flex", height: 22, borderRadius: 6, overflow: "hidden", border: "1px solid rgba(127,127,127,.3)" }}>
        {parts.map(([k, v, c]) => v > 0 && <div key={k} title={`${t(k)}: $${money(v)}`} style={{ width: `${(v / eq) * 100}%`, background: c }} />)}
      </div>
      <div className="row" style={{ marginTop: 6, flexWrap: "wrap" }}>
        {parts.map(([k, v, c]) => (
          <span key={k} className="muted" style={{ fontSize: 12 }}>
            <span style={{ display: "inline-block", width: 10, height: 10, background: c, borderRadius: 2, marginRight: 4 }} />
            {t(k)} ${money(v)} ({pct(v / eq, 1)})
          </span>
        ))}
      </div>
    </>
  );
}

function Budget({ label, used, cap, fmt, note }: { label: string; used: number; cap: number | null; fmt?: "pct"; note?: string }) {
  const f = (x: number | null) => (fmt === "pct" ? pct(x, 1) : `$${money(x)}`);
  const frac = cap ? used / cap : 0;
  const t = useT();
  return (
    <div>
      <div className="muted" style={{ fontSize: 12 }}>{label}</div>
      <div>{f(used)} <span className="muted">/ {cap == null ? t("off") : f(cap)} {note}</span></div>
      <Meter frac={frac} danger={frac > 0.9} />
    </div>
  );
}

function Row({ r, equity, owner, done }: { r: any; equity: number; owner: boolean; done: (k: "ok" | "bad", t: string) => void }) {
  const [edit, setEdit] = useState(false);
  const [mode, setMode] = useState<AllocMode>(r.alloc_mode);
  const [alloc, setAlloc] = useState(allocValue(r.alloc_mode, r.allocation, r.allocation_usd));
  const [reserved, setReserved] = useState(`${+(r.reserved * 100).toFixed(1)}`);
  const [dd, setDd] = useState(`${+(r.max_drawdown * 100).toFixed(2)}%`);
  const [dl, setDl] = useState(r.daily_loss != null ? `${+(r.daily_loss * 100).toFixed(2)}%` : "");
  const [busy, setBusy] = useState(false);
  const t = useT();
  async function call(path: string, body: object) {
    setBusy(true);
    try {
      done("ok", (await post(path, body)).message);
      setEdit(false);
    } catch (e) {
      done("bad", (e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const link = r.versions.length ? `/strategies/${r.versions[r.versions.length - 1].hash}` : null;
  return (
    <>
      <tr>
        <td>{link ? <Link href={link}><b>{r.sid}</b></Link> : <b>{r.sid}</b>} <span className="muted">· {t(r.kind)}</span>
          {r.warning && <div className="neg" style={{ fontSize: 12 }}>{r.warning}</div>}</td>
        <td><StatusPill status={r.status} />{r.reason && <div className="muted" style={{ fontSize: 12 }}>{r.reason}</div>}</td>
        <td className="num">{r.alloc_mode === "usd"
          ? <>${money(r.allocation_usd)}<div className="muted">{t("fixed")} · {pct(r.allocation, 1)}</div></>
          : <>{pct(r.allocation, 1)}<div className="muted">${money(r.allocation_usd)}{r.source === "default" && ` · ${t("default")}`}</div></>}</td>
        <td className="num">{pct(r.reserved, 0)}<div className="muted">${money(r.reserved_usd)}</div>
          <div className="muted" style={{ fontSize: 12 }} title={t("What it could buy right now, within its allocation, cash and the shared pool")}>
            {t("can buy ${v}", { v: money(r.can_buy_now) })}</div>
          {r.activity && <div className="muted" style={{ fontSize: 12 }}>{activityText(r.activity, t)}</div>}</td>
        <td style={{ minWidth: 140 }}>${money(r.deployed)} <span className="muted">({pct(r.used, 0)})</span><Meter frac={r.used ?? 0} danger={(r.used ?? 0) > 0.95} /></td>
        <td className={`num ${pnlClass(r.pnl)}`}>{money(r.pnl, true)}</td>
        <td className={`num ${pnlClass(r.pnl_today)}`}>{money(r.pnl_today, true)}</td>
        <td style={{ minWidth: 130 }}>{pct(r.drawdown, 1)} <span className="muted">/ {pct(r.max_drawdown, 0)}</span><Meter frac={r.max_drawdown ? r.drawdown / r.max_drawdown : 0} danger={r.drawdown > 0.7 * r.max_drawdown} /></td>
        <td className="num">{r.kind === "intraday" ? pct(r.daily_loss, 0) : <span className="muted">{t("n/a")}</span>}</td>
        <td>
          {owner && (
            <span className="row" style={{ gap: 4 }}>
              <button disabled={busy} onClick={() => setEdit(!edit)}>{t("Edit")}</button>
              {r.status === "active"
                ? <button disabled={busy} onClick={() => call(`/api/funds/${r.sid}/pause`, { reason: "paused by owner" })}>{t("Pause")}</button>
                : <button disabled={busy} className="good" onClick={() => call(`/api/funds/${r.sid}/resume`, {})}>{t("Resume")}</button>}
            </span>
          )}
        </td>
      </tr>
      {edit && (
        <tr>
          <td colSpan={10}>
            <div className="row">
              <label className="row">{t("Allocation")} <AllocationInput mode={mode} value={alloc} equity={equity} disabled={busy}
                onMode={(m, v) => { setMode(m); setAlloc(v); }} onValue={setAlloc} /></label>
              <ReservedInput value={reserved} onValue={setReserved} ceilingUsd={ceilingUsd(mode, alloc, equity)} disabled={busy} />
              <label>{t("Drawdown stop")} <input value={dd} onChange={(e) => setDd(e.target.value)} style={{ width: 80 }} /></label>
              {r.kind === "intraday" && <label>{t("Daily loss stop")} <input value={dl} onChange={(e) => setDl(e.target.value)} style={{ width: 80 }} /></label>}
              <button className="primary" disabled={busy}
                onClick={() => call(`/api/funds/${r.sid}`, { allocation: allocText(mode, alloc), alloc_mode: mode, reserved: reserved.trim() || undefined, max_drawdown: dd, daily_loss: r.kind === "intraday" && dl ? dl : null })}>{t("Save")}</button>
              <Pill>{r.wants != null ? t("rules can use up to {pct}", { pct: pct(r.wants, 1) }) : ""}</Pill>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

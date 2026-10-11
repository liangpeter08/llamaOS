"use client";
import Link from "next/link";
import { useState } from "react";
import { post } from "@/lib/api";
import { usePoll } from "@/lib/hooks";
import { money, pct, pnlClass, wall } from "@/lib/format";
import { Card, Empty, Loading, Meter, Notice, Pill, Stat, StatusPill } from "@/components/ui";
import { isOwner, useSession } from "@/components/session";
import { AllocationInput, allocText, allocValue, type AllocMode } from "@/components/Allocation";

export default function Funds() {
  const { data, error, reload } = usePoll<any>("/api/funds", 5000);
  const { me } = useSession();
  const [msg, setMsg] = useState<{ kind: "ok" | "bad"; text: string } | null>(null);
  if (!data) return <Loading error={error} />;
  const a = data.account, L = data.limits;
  const done = (kind: "ok" | "bad", text: string) => { setMsg({ kind, text }); reload(); };

  return (
    <>
      <h1>Funds</h1>
      <p className="muted">
        Where the money is. Each running strategy owns a reserved slice of equity (its allocation, a percent of the fund or a fixed dollar amount) and can&apos;t spend another&apos;s; a cash
        reserve is never deployed. Account-wide limits live on the <Link href="/risk">Risk</Link> page.
      </p>
      {msg && <Notice kind={msg.kind}>{msg.text}</Notice>}
      {!data.enabled ? <Notice kind="warn">Fund controls are off.</Notice> : (
        <>
          <div className="grid g4">
            <Stat label="Equity" value={`$${money(a.equity)}`} sub={`peak $${money(a.peak_equity)}`} />
            <Stat label="Cash" value={`$${money(a.cash)}`} sub={`reserve $${money(a.reserve_usd)} (${pct(L.cash_reserve, 0)}) never deployed`} />
            <Stat label="Deployed" value={`$${money(a.deployed)}`} sub={`${pct(a.equity ? a.deployed / a.equity : null, 1)} of equity · max ${pct(L.max_deployed, 0)}`} />
            <Stat label="Available to deploy" value={`$${money(a.available)}`} sub={`working buys $${money(a.working_buys)}`} />
          </div>
          <Card title="Capital" className="mt">
            <CapitalBar a={a} L={L} />
            <div className="grid g4" style={{ marginTop: 12 }}>
              <Budget label="Allocated to strategies" used={a.allocated} cap={L.max_deployed} fmt="pct" />
              <Budget label="Intraday deployed" used={a.intraday_deployed} cap={L.intraday_budget * a.equity} />
              <Budget label="Sleeves deployed" used={a.sleeve_deployed} cap={a.sleeve_cap_usd} />
              <Budget label="Account drawdown" used={a.drawdown} cap={L.account_max_drawdown} fmt="pct" note="→ kill switch" />
            </div>
          </Card>
        </>
      )}

      <Card title="Strategy allocations" className="mt">
        {!data.strategies.length ? <Empty>No strategy is running. Promoting one to paper gives it an allocation.</Empty> : (
          <div className="table-wrap"><table>
            <thead><tr><th>Strategy</th><th>Status</th><th className="num">Allocation</th><th>Deployed</th><th className="num">P&amp;L since set</th>
              <th className="num">Today</th><th>Drawdown</th><th className="num">Daily loss stop</th><th></th></tr></thead>
            <tbody>{data.strategies.map((r: any) => <Row key={r.sid} r={r} equity={a.equity} owner={isOwner(me)} done={done} />)}</tbody>
          </table></div>
        )}
        <p className="muted">
          Drawdown = loss from the strategy&apos;s best P&amp;L since its allocation was set, as a share of the allocation; reaching its stop pauses the
          strategy (a portfolio sleeve is sold). The daily loss stop (intraday only) blocks new entries for the rest of the day. Changing an
          allocation or resuming restarts the measurement.
        </p>
      </Card>

      <Card title="Fund events" className="mt">
        <table><tbody>
          {data.history.map((h: any) => <tr key={h.id}><td>{wall(h.ts)}</td><td>{h.kind}</td><td>{h.actor}</td><td className="muted mono">{h.payload}</td></tr>)}
          {!data.history.length && <tr><td className="muted">None yet.</td></tr>}
        </tbody></table>
      </Card>
    </>
  );
}

function CapitalBar({ a, L }: { a: any; L: any }) {
  const eq = a.equity || 1;
  const allocatedIdle = Math.max(0, a.allocated * eq - a.deployed);
  const parts: [string, number, string][] = [
    ["Intraday deployed", a.intraday_deployed, "var(--accent, #4c8bf5)"],
    ["Sleeves deployed", a.sleeve_deployed, "var(--good, #2ea56b)"],
    ["Allocated, not deployed", allocatedIdle, "rgba(127,127,127,.35)"],
    ["Unallocated", Math.max(0, (L.max_deployed - a.allocated) * eq), "rgba(127,127,127,.15)"],
    ["Cash reserve", L.cash_reserve * eq, "var(--warn, #d9a400)"],
  ];
  return (
    <>
      <div style={{ display: "flex", height: 22, borderRadius: 6, overflow: "hidden", border: "1px solid rgba(127,127,127,.3)" }}>
        {parts.map(([k, v, c]) => v > 0 && <div key={k} title={`${k}: $${money(v)}`} style={{ width: `${(v / eq) * 100}%`, background: c }} />)}
      </div>
      <div className="row" style={{ marginTop: 6, flexWrap: "wrap" }}>
        {parts.map(([k, v, c]) => (
          <span key={k} className="muted" style={{ fontSize: 12 }}>
            <span style={{ display: "inline-block", width: 10, height: 10, background: c, borderRadius: 2, marginRight: 4 }} />
            {k} ${money(v)} ({pct(v / eq, 1)})
          </span>
        ))}
      </div>
    </>
  );
}

function Budget({ label, used, cap, fmt, note }: { label: string; used: number; cap: number | null; fmt?: "pct"; note?: string }) {
  const f = (x: number | null) => (fmt === "pct" ? pct(x, 1) : `$${money(x)}`);
  const frac = cap ? used / cap : 0;
  return (
    <div>
      <div className="muted" style={{ fontSize: 12 }}>{label}</div>
      <div>{f(used)} <span className="muted">/ {cap == null ? "off" : f(cap)} {note}</span></div>
      <Meter frac={frac} danger={frac > 0.9} />
    </div>
  );
}

function Row({ r, equity, owner, done }: { r: any; equity: number; owner: boolean; done: (k: "ok" | "bad", t: string) => void }) {
  const [edit, setEdit] = useState(false);
  const [mode, setMode] = useState<AllocMode>(r.alloc_mode);
  const [alloc, setAlloc] = useState(allocValue(r.alloc_mode, r.allocation, r.allocation_usd));
  const [dd, setDd] = useState(`${+(r.max_drawdown * 100).toFixed(2)}%`);
  const [dl, setDl] = useState(r.daily_loss != null ? `${+(r.daily_loss * 100).toFixed(2)}%` : "");
  const [busy, setBusy] = useState(false);
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
        <td>{link ? <Link href={link}><b>{r.sid}</b></Link> : <b>{r.sid}</b>} <span className="muted">· {r.kind}</span>
          {r.warning && <div className="neg" style={{ fontSize: 12 }}>{r.warning}</div>}</td>
        <td><StatusPill status={r.status} />{r.reason && <div className="muted" style={{ fontSize: 12 }}>{r.reason}</div>}</td>
        <td className="num">{r.alloc_mode === "usd"
          ? <>${money(r.allocation_usd)}<div className="muted">fixed · {pct(r.allocation, 1)}</div></>
          : <>{pct(r.allocation, 1)}<div className="muted">${money(r.allocation_usd)}{r.source === "default" && " · default"}</div></>}</td>
        <td style={{ minWidth: 140 }}>${money(r.deployed)} <span className="muted">({pct(r.used, 0)})</span><Meter frac={r.used ?? 0} danger={(r.used ?? 0) > 0.95} /></td>
        <td className={`num ${pnlClass(r.pnl)}`}>{money(r.pnl, true)}</td>
        <td className={`num ${pnlClass(r.pnl_today)}`}>{money(r.pnl_today, true)}</td>
        <td style={{ minWidth: 130 }}>{pct(r.drawdown, 1)} <span className="muted">/ {pct(r.max_drawdown, 0)}</span><Meter frac={r.max_drawdown ? r.drawdown / r.max_drawdown : 0} danger={r.drawdown > 0.7 * r.max_drawdown} /></td>
        <td className="num">{r.kind === "intraday" ? pct(r.daily_loss, 0) : <span className="muted">n/a</span>}</td>
        <td>
          {owner && (
            <span className="row" style={{ gap: 4 }}>
              <button disabled={busy} onClick={() => setEdit(!edit)}>Edit</button>
              {r.status === "active"
                ? <button disabled={busy} onClick={() => call(`/api/funds/${r.sid}/pause`, { reason: "paused by owner" })}>Pause</button>
                : <button disabled={busy} className="good" onClick={() => call(`/api/funds/${r.sid}/resume`, {})}>Resume</button>}
            </span>
          )}
        </td>
      </tr>
      {edit && (
        <tr>
          <td colSpan={9}>
            <div className="row">
              <label className="row">Allocation <AllocationInput mode={mode} value={alloc} equity={equity} disabled={busy}
                onMode={(m, v) => { setMode(m); setAlloc(v); }} onValue={setAlloc} /></label>
              <label>Drawdown stop <input value={dd} onChange={(e) => setDd(e.target.value)} style={{ width: 80 }} /></label>
              {r.kind === "intraday" && <label>Daily loss stop <input value={dl} onChange={(e) => setDl(e.target.value)} style={{ width: 80 }} /></label>}
              <button className="primary" disabled={busy}
                onClick={() => call(`/api/funds/${r.sid}`, { allocation: allocText(mode, alloc), alloc_mode: mode, max_drawdown: dd, daily_loss: r.kind === "intraday" && dl ? dl : null })}>Save</button>
              <Pill>{r.wants != null ? `rules can use up to ${pct(r.wants, 1)}` : ""}</Pill>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

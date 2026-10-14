"use client";
import Link from "next/link";
import { useState } from "react";
import { post } from "@/lib/api";
import { usePoll } from "@/lib/hooks";
import { money, pct, pnlClass } from "@/lib/format";
import { Card, Empty, Meter, Notice } from "@/components/ui";
import { useT, type T } from "@/lib/i18n";

export type AllocMode = "pct" | "usd";

/** The owner's allocation input as the API takes it: "12.5%" of equity, or "$25,000" fixed. */
export function allocText(mode: AllocMode, value: string) {
  const v = value.trim();
  return mode === "pct" ? (v.endsWith("%") ? v : `${v}%`) : v;
}

/** Initial input text for an existing allocation. */
export function allocValue(mode: AllocMode, share: number, usd: number) {
  return mode === "pct" ? `${+(share * 100).toFixed(2)}` : `${Math.round(usd)}`;
}

function parse(mode: AllocMode, value: string): number | null {
  const m = value.replace(/[$,_\s%]/g, "").match(/^(\d+(?:\.\d+)?)([kKmM]?)$/);
  if (!m) return null;
  const n = parseFloat(m[1]) * (m[2] ? (m[2].toLowerCase() === "k" ? 1e3 : 1e6) : 1);
  return mode === "pct" ? n / 100 : n;
}

/** Mode switch (% of fund / fixed $) plus the amount, with the other unit shown at today's equity. */
export function AllocationInput({ mode, value, equity, onMode, onValue, disabled }: {
  mode: AllocMode; value: string; equity: number; onMode: (m: AllocMode, v: string) => void; onValue: (v: string) => void; disabled?: boolean;
}) {
  const n = parse(mode, value);
  const t = useT();
  const hint = n == null || !equity ? "" : mode === "pct" ? t("≈ ${v} today", { v: money(n * equity) }) : t("≈ {pct} of equity today", { pct: pct(n / equity, 1) });
  const switchTo = (m: AllocMode) => {
    if (m === mode) return;
    // carry the amount over at today's equity so switching modes doesn't change the allocation
    onMode(m, n == null || !equity ? "" : m === "usd" ? `${Math.round(n * equity)}` : `${+((n / equity) * 100).toFixed(2)}`);
  };
  return (
    <span className="alloc-input">
      <span className="seg" role="group" aria-label={t("Allocation type")}>
        <button type="button" className={mode === "pct" ? "on" : ""} aria-pressed={mode === "pct"} disabled={disabled} onClick={() => switchTo("pct")}>{t("% of fund")}</button>
        <button type="button" className={mode === "usd" ? "on" : ""} aria-pressed={mode === "usd"} disabled={disabled} onClick={() => switchTo("usd")}>{t("Fixed $")}</button>
      </span>
      <span className="alloc-amount">
        {mode === "usd" && <span className="muted">$</span>}
        <input value={value} inputMode="decimal" disabled={disabled} aria-label={t(mode === "pct" ? "Percent of equity" : "Dollars")}
          onChange={(e) => onValue(e.target.value)} style={{ width: mode === "pct" ? 64 : 96 }} />
        {mode === "pct" && <span className="muted">%</span>}
      </span>
      <span className="muted alloc-hint">{hint}</span>
    </span>
  );
}

/** Share of the allocation held for the strategy while idle ("25" = 25%); the rest is borrowed from the shared pool when it trades. */
export function ReservedInput({ value, onValue, ceilingUsd, disabled }: {
  value: string; onValue: (v: string) => void; ceilingUsd: number | null; disabled?: boolean;
}) {
  const n = parseFloat(value.replace(/[%\s]/g, ""));
  const ok = Number.isFinite(n) && n >= 0 && n <= 100;
  const t = useT();
  const hint = !ok ? t("0-100% of the allocation") : ceilingUsd == null ? ""
    : n >= 100 ? t("all ${v} held for it", { v: money(ceilingUsd) }) : t("${idle} held while idle, up to ${max} when it trades", { idle: money((n / 100) * ceilingUsd), max: money(ceilingUsd) });
  return (
    <span className="alloc-input" title={t("Reserved money is always there for this strategy. The rest of its allocation comes from the shared pool (money no other strategy has reserved or is using), so an idle strategy doesn't park cash.")}>
      <span className="muted">{t("Reserved")}</span>
      <span className="alloc-amount">
        <input value={value} inputMode="decimal" disabled={disabled} aria-label={t("Reserved, percent of the allocation")}
          onChange={(e) => onValue(e.target.value)} style={{ width: 52 }} />
        <span className="muted">%</span>
      </span>
      <span className={`muted alloc-hint ${ok ? "" : "neg"}`}>{hint}</span>
    </span>
  );
}

/** Ceiling in dollars at today's equity for an allocation input, or null when it doesn't parse. */
export function ceilingUsd(mode: AllocMode, value: string, equity: number): number | null {
  const n = parse(mode, value);
  return n == null ? null : mode === "pct" ? n * equity : n;
}

/** "Seldom trades" label from a line's recent activity: sessions with an entry over the window. */
export function activityText(a: any, t: T) {
  if (!a) return "";
  return t("traded {n}/{total} sessions ({days}d)", { n: a.active_sessions, total: a.sessions, days: a.days }) + (a.busiest_usd ? ` · ${t("busiest day ${v}", { v: money(a.busiest_usd) })}` : "");
}

export function Switch({ on, onChange, disabled, label }: { on: boolean; onChange: (v: boolean) => void; disabled?: boolean; label: string }) {
  return (
    <button type="button" role="switch" aria-checked={on} aria-label={label} title={label} disabled={disabled}
      className={`switch ${on ? "on" : ""}`} onClick={() => onChange(!on)}>
      <span />
    </button>
  );
}

/** Running strategies with on/off and allocation controls (Overview). Detailed limits live on Funds. */
export function StrategyControls({ owner }: { owner: boolean }) {
  const { data, error, reload } = usePoll<any>("/api/funds", 4000);
  const [msg, setMsg] = useState<{ kind: "ok" | "bad"; text: string } | null>(null);
  const t = useT();
  if (!data) return error ? <Card title={t("Strategies")}><Notice kind="bad">{error}</Notice></Card> : null;
  const a = data.account;
  const done = (kind: "ok" | "bad", text: string) => { setMsg({ kind, text }); reload(); };
  return (
    <Card title={<span className="row" style={{ justifyContent: "space-between" }}>
      <span>{t("Strategies")}</span>
      {data.enabled && <span className="muted" style={{ fontSize: 12, fontWeight: 400 }}>
        {t("reserved {r} · unreserved {u} · shared pool free ${free}", { r: pct(a.allocated, 1), u: pct(a.unallocated, 1), free: money(a.pool_free_usd) })} · <Link href="/funds">{t("Funds")}</Link>
      </span>}
    </span>}>
      {msg && <Notice kind={msg.kind}>{msg.text}</Notice>}
      {!data.enabled ? <Notice kind="warn">{t("Fund controls are off, so strategies have no allocation to adjust.")}</Notice>
        : !data.strategies.length ? <Empty>{t("No strategy is running. Promote one to paper from {link}.", { link: <Link href="/strategies">{t("Strategies")}</Link> })}</Empty> : (
          <div className="table-wrap"><table>
            <thead><tr><th>{t("On")}</th><th>{t("Strategy")}</th><th>{t("Allocation")}</th><th>{t("Deployed")}</th><th className="num">{t("Today")}</th><th className="num">{t("Since set")}</th><th></th></tr></thead>
            <tbody>{data.strategies.map((r: any) => <ControlRow key={r.sid} r={r} equity={a.equity} owner={owner} done={done} />)}</tbody>
          </table></div>
        )}
    </Card>
  );
}

function ControlRow({ r, equity, owner, done }: { r: any; equity: number; owner: boolean; done: (k: "ok" | "bad", t: string) => void }) {
  const [edit, setEdit] = useState(false);
  const [mode, setMode] = useState<AllocMode>(r.alloc_mode);
  const [value, setValue] = useState("");
  const [reserved, setReserved] = useState("");
  const [busy, setBusy] = useState(false);
  const on = r.status === "active";
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
  function toggle(next: boolean) {
    if (next) return call(`/api/funds/${r.sid}/resume`, {});
    const what = t(r.kind === "portfolio"
      ? "Turn off this portfolio strategy? Its sleeve is sold at the next daily decision time."
      : "Turn off this strategy? It takes no new entries; open positions keep their stops and targets.");
    if (window.confirm(what)) call(`/api/funds/${r.sid}/pause`, { reason: "turned off on Overview" });
  }
  function openEdit() {
    setMode(r.alloc_mode);
    setValue(allocValue(r.alloc_mode, r.allocation, r.allocation_usd));
    setReserved(`${+(r.reserved * 100).toFixed(1)}`);
    setEdit(!edit);
  }
  const link = r.versions.length ? `/strategies/${r.versions[r.versions.length - 1].hash}` : null;
  return (
    <>
      <tr className={on ? "" : "off"}>
        <td><Switch on={on} disabled={!owner || busy} onChange={toggle} label={t(on ? "Turn {sid} off" : "Turn {sid} on", { sid: r.sid })} /></td>
        <td>
          {link ? <Link href={link}><b>{r.sid}</b></Link> : <b>{r.sid}</b>} <span className="muted">· {t(r.kind)}</span>
          {!on && <div className="muted" style={{ fontSize: 12 }}>{t(r.status === "paused_today" ? "off for today" : "off")}{r.reason ? `: ${r.reason}` : ""}</div>}
        </td>
        <td>
          {r.alloc_mode === "usd"
            ? <><b>${money(r.allocation_usd)}</b> <span className="muted">{t("fixed")} · {pct(r.allocation, 1)}</span></>
            : <><b>{pct(r.allocation, 1)}</b> <span className="muted">{t("of fund")} · ${money(r.allocation_usd)}</span></>}
          <div className="muted" style={{ fontSize: 12 }}>{r.reserved >= 0.9995 ? t("fully reserved") : t("{pct} reserved · rest from pool", { pct: pct(r.reserved, 0) })}</div>
        </td>
        <td style={{ minWidth: 130 }}>${money(r.deployed)} <span className="muted">({pct(r.used, 0)})</span><Meter frac={r.used ?? 0} danger={(r.used ?? 0) > 0.95} /></td>
        <td className={`num ${pnlClass(r.pnl_today)}`}>{money(r.pnl_today, true)}</td>
        <td className={`num ${pnlClass(r.pnl)}`}>{money(r.pnl, true)}<div className="muted" style={{ fontSize: 12 }}>{pct(r.pnl_pct, 1, true)}</div></td>
        <td>{owner && <button disabled={busy} onClick={openEdit}>{t(edit ? "Cancel" : "Adjust")}</button>}</td>
      </tr>
      {edit && (
        <tr>
          <td colSpan={7}>
            <div className="row">
              <AllocationInput mode={mode} value={value} equity={equity} disabled={busy}
                onMode={(m, v) => { setMode(m); setValue(v); }} onValue={setValue} />
              <ReservedInput value={reserved} onValue={setReserved} ceilingUsd={ceilingUsd(mode, value, equity)} disabled={busy} />
              <button className="primary" disabled={busy || !value.trim()}
                onClick={() => call(`/api/funds/${r.sid}`, { allocation: allocText(mode, value), alloc_mode: mode, reserved: reserved.trim() || undefined })}>{t("Save")}</button>
              {r.wants != null && <span className="muted">{t("rules can use up to {pct}", { pct: pct(r.wants, 1) })}</span>}
              {r.activity && <span className="muted">{activityText(r.activity, t)}</span>}
            </div>
            <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
              {t("Saving restarts the strategy's drawdown measurement. A fixed amount stays the same as equity moves; a percent grows and shrinks with it. The reserved part is always there for this strategy; above it, buys use the shared pool if other strategies aren't using or reserving it. Seldom-trading strategies can take a large allocation with a small reserve.")}
              {r.kind === "portfolio" && ` ${t("A running sleeve keeps its size; the allocation caps its buys.")}`}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

"use client";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { post } from "@/lib/api";
import { usePoll } from "@/lib/hooks";
import { et, money, num, pct, pnlClass, short, wall } from "@/lib/format";
import { LineChart } from "@/components/LineChart";
import { Chat } from "@/components/Chat";
import { Card, Empty, Loading, Notice, Pill, Stat, StatusPill } from "@/components/ui";
import { isOwner, useSession } from "@/components/session";
import { OverlaysCard, PortfolioBacktest, SleeveCard } from "@/components/Portfolio";
import { CategoryEditor, DeleteZone } from "@/components/Organize";
import { AllocationInput, ReservedInput, allocText, allocValue, ceilingUsd, type AllocMode } from "@/components/Allocation";
import { IterateCard } from "@/components/Discovery";
import { useT, type T } from "@/lib/i18n";

export default function StrategyPage() {
  const { hash } = useParams<{ hash: string }>();
  const { data, error, reload } = usePoll<any>(`/api/strategies/${hash}`, 5000);
  const { me } = useSession();
  const router = useRouter();
  const [deleteRuns, setDeleteRuns] = useState(false);
  const [msg, setMsg] = useState<{ kind: "ok" | "bad"; text: string } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const t = useT();
  if (!data) return <Loading error={error} />;
  const { s, validation: v, backtest: bt } = data;
  const portfolio = data.kind === "portfolio";

  async function remove() {
    setBusy("delete");
    try {
      await post(`/api/strategies/${s.hash}/delete`, { confirm: s.sid, delete_runs: deleteRuns });
      const next = data.versions.filter((vv: any) => vv.hash !== s.hash).slice(-1)[0];
      router.replace(next ? `/strategies/${next.hash}` : "/strategies");
    } catch (e) {
      setMsg({ kind: "bad", text: (e as Error).message });
      setBusy(null);
    }
  }

  async function act(label: string, path: string, body: object = {}) {
    if (label === "paper" && !window.confirm(t("Promoting this strategy will automatically enable monitoring for its required watchlist tickers. Continue?"))) return;
    setBusy(label);
    try {
      const r = await post(path, body);
      setMsg({ kind: "ok", text: r.message });
    } catch (e) {
      setMsg({ kind: "bad", text: (e as Error).message });
    } finally {
      setBusy(null);
      reload();
    }
  }

  return (
    <>
      {msg && <Notice kind={msg.kind}>{msg.text}</Notice>}
      <div className="row between">
        <div>
          <h1>{s.display_name || s.sid} <span className="muted">v{s.version}</span></h1>
          <div className="row">
            <StatusPill status={s.status} />
            {s.archived_at && <Pill kind="warn">{t("archived {when}", { when: wall(s.archived_at) })}</Pill>}
            {data.category && <Link href="/strategies"><Pill>{data.category}</Pill></Link>}
            <Pill kind={portfolio ? "accent" : ""}>{t(portfolio ? "portfolio · held overnight" : "intraday")}</Pill>
            <Pill kind={s.mode === "live_auto" ? "bad" : s.mode?.startsWith("live") ? "warn" : ""}>{modeLabel(s.mode, t)}</Pill>
            {data.fund && <Link href="/funds"><Pill kind={data.fund.status === "active" ? "" : "bad"}>{t("fund {pct} of equity", { pct: pct(data.fund.allocation, 1) })}{data.fund.status !== "active" ? ` · ${t(data.fund.status)}` : ""}</Pill></Link>}
            <Pill title={s.hash}><span className="mono">sha256 {short(s.hash)}…</span></Pill>
            {!data.integrity && <Pill kind="bad">{t("file changed on disk: hash mismatch")}</Pill>}
            {s.approved_by && <Pill kind="good">{t("approved by {who} {when}", { who: s.approved_by, when: wall(s.approved_at) })}</Pill>}
          </div>
        </div>
        <div className="row">
          {data.versions.map((vv: any) => (
            <span key={vv.hash} className="row" style={{ gap: 4 }}>
              <Link className={`btn ${vv.hash === s.hash ? "on" : ""}`} href={`/strategies/${vv.hash}`} title={t(vv.archived_at ? "archived" : vv.status)}
                style={vv.archived_at ? { opacity: 0.55 } : undefined}>v{vv.version}</Link>
              {vv.hash !== s.hash && <Link className="muted" href={`/strategies/${s.hash}/diff?against=${vv.hash}`}>{t("diff")}</Link>}
            </span>
          ))}
        </div>
      </div>

      {!v.ok && <Notice kind="bad"><b>{t("Validator errors")}</b><ul>{v.errors.map((e: string) => <li key={e}>{e}</li>)}</ul></Notice>}
      <Card title={t("Ticker monitoring")} className="mt">
        <p>{t(portfolio ? "Required by this portfolio strategy" : "Shared watchlist used by this intraday strategy")}: {data.monitoring?.required.join(", ") || t("No tickers configured")}. <Link href="/watchlist">{t("Manage monitoring")}</Link></p>
        {!!data.monitoring?.unwatched.length && <Notice kind="warn">{t("Monitoring is off for {list}. This strategy cannot open new positions in those tickers. Held positions retain risk monitoring.", { list: data.monitoring.unwatched.join(", ") })}</Notice>}
      </Card>
      {v.warnings.length > 0 && <Notice kind="warn">{v.warnings.map((w: string) => <div key={w}>{w}</div>)}</Notice>}

      {isOwner(me) && <Lifecycle s={s} ok={v.ok} busy={busy} act={act} minPaper={data.lifecycle?.min_paper_sessions ?? 0}
        needsReview={!!data.portfolio?.overlays?.review_required} category={data.category} categories={data.categories} fund={data.fund_suggest} />}
      {isOwner(me) && <Duplicate s={s} />}
      <IterateCard hash={s.hash} d={data.discovery} owner={isOwner(me)} />

      <div className="grid g2 mt">
        <Card title={t("Assumptions (need your OK)")}>
          {data.assumptions.length ? <ul>{data.assumptions.map((a: unknown, i: number) => <li key={i}>{asText(a)}</li>)}</ul> : <Empty>{t("None listed.")}</Empty>}
          {data.suggestions.length > 0 && (<><h3>{t("Suggestions (not applied)")}</h3><ul>{data.suggestions.map((a: unknown, i: number) => <li key={i}>{asText(a)}</li>)}</ul></>)}
          {data.source?.text && (
            <>
              <h3>{t("Source request")}</h3>
              <p className="muted">{data.source.text}</p>
              <p className="muted">{t("compiled by {who} · prompt {p}", { who: data.source.compiled_by, p: data.source.prompt })}</p>
            </>
          )}
        </Card>
        <Card title={t("Back-translation")}>
          <p className="muted">{t("Generated by code from the parsed rules, independent of the model that wrote the file.")}</p>
          <pre>{data.back_translation ?? t("n/a: the file does not validate")}</pre>
        </Card>
      </div>

      {portfolio && <SleeveCard p={data.portfolio} decideAt={data.decide_at ?? "15:45"} />}
      {portfolio && data.portfolio?.overlays && <OverlaysCard o={data.portfolio.overlays} hash={s.hash} act={act} busy={busy} />}

      <Card title={t("Backtest")} className="mt">
        <p><Link href={`/backtests?strategy=${s.hash}`}>{t("Configure a new experiment and review saved runs →")}</Link>{bt?.run_id && <> · <Link href={`/backtests?run=${bt.run_id}`}>{t("Open run #{id}", { id: bt.run_id })}</Link></>}</p>
        {bt && bt.kind === "portfolio" ? <PortfolioBacktest bt={bt} /> : bt ? (
          <>
            <div className="grid g4">
              <Stat label={t("Trades")} value={bt.metrics.trades} sub={`${t("{n} sessions", { n: bt.days.length })} · ${bt.signal_note}`} />
              <Stat label={t("Win rate · avg R")} value={pct(bt.metrics.win_rate, 0)} sub={`${num(bt.metrics.avg_r, 2, true)} R`} />
              <Stat label={t("P&L")} value={money(bt.metrics.total_pnl, true)} valueClass={pnlClass(bt.metrics.total_pnl)} sub={pct(bt.metrics.return_pct, 2, true)} />
              <Stat label={t("Max drawdown")} value={pct(bt.metrics.max_drawdown)} sub={t("Sharpe (daily) {v}", { v: num(bt.metrics.sharpe_daily) })} />
            </div>
            <LineChart values={bt.equity_curve.map((p: any) => p.equity)} width={900} height={160} baseline={bt.starting_equity} />
            <p className="muted">
              {t("Exits: {list} · rejected by risk guard: {n} · ran {when} by {who} in {s}s", {
                list: Object.entries(bt.metrics.exits).map(([k, n]) => `${t(k)} ${n}`).join(", "), n: bt.rejected_by_guard, when: wall(bt.ran_at), who: bt.ran_by, s: bt.seconds })}
            </p>
            <details>
              <summary>{t("Trades")} ({bt.trades.length})</summary>
              <table>
                <thead><tr><th>{t("Day")}</th><th>{t("Symbol")}</th><th className="num">{t("Qty")}</th><th className="num">{t("Entry")}</th><th className="num">{t("Exit")}</th><th>{t("How")}</th><th className="num">{t("P&L")}</th><th className="num">R</th></tr></thead>
                <tbody>{bt.trades.map((tr: any, i: number) => (
                  <tr key={i}><td>{tr.day}</td><td>{tr.symbol}</td><td className="num">{tr.qty}</td><td className="num">{money(tr.entry_price)}</td><td className="num">{money(tr.exit_price)}</td>
                    <td>{t(tr.exit_kind)}</td><td className={`num ${pnlClass(tr.pnl)}`}>{money(tr.pnl, true)}</td><td className="num">{num(tr.r_multiple, 2, true)}</td></tr>
                ))}</tbody>
              </table>
            </details>
          </>
        ) : <Empty>{t("Not run yet.")}</Empty>}
      </Card>

      <div className={portfolio ? "mt" : "grid g2 mt"}>
        {!portfolio && <Card title={t("Decision records (this session)")}>
          <table>
            <thead><tr><th>#</th><th>{t("Bar")}</th><th>{t("Symbol")}</th><th className="num">{t("Qty")}</th><th>{t("Status")}</th></tr></thead>
            <tbody>
              {data.intents.map((i: any) => <tr key={i.id}><td>{i.id}</td><td className="mono">{et(i.ts)}</td><td>{i.symbol}</td><td className="num">{i.qty}</td><td><StatusPill status={i.status} /></td></tr>)}
              {!data.intents.length && <tr><td colSpan={5} className="muted">{t("None.")}</td></tr>}
            </tbody>
          </table>
          <h3>{t("Simulated trades")}</h3>
          <table><tbody>
            {data.trades.map((tr: any) => <tr key={tr.id}><td><Link href={`/trades/${tr.id}`}>#{tr.id}</Link></td><td>{tr.symbol}</td><td>{t(tr.exit_kind)}</td><td className={`num ${pnlClass(tr.pnl)}`}>{money(tr.pnl, true)}</td></tr>)}
            {!data.trades.length && <tr><td className="muted">{t("None.")}</td></tr>}
          </tbody></table>
        </Card>}
        <StrategyFile key={s.hash} hash={s.hash} text={data.text} owner={isOwner(me)} running={s.status === "paper"} />
      </div>

      <Card title={t("Ask Kimi")} className="mt" id="chat">
        <Chat prefix={`strategy:${s.hash}`}
          intro={t("A new conversation, seeded with the strategy file, back-translation, backtest report and recent decision records. Kimi can explain and critique, and propose changes only as a diff, which you can accept as a new draft version.")} />
        {isOwner(me) && <ApplyDiff hash={s.hash} />}
      </Card>

      {isOwner(me) && (
        <DeleteZone what={t("version (v{v})", { v: s.version })} confirmText={s.sid} blockers={data.delete_blockers} busy={!!busy} onDelete={remove}>
          <p className="muted">
            {t("Removes {path}, its registry entry and its sleeve/overlay state.", { path: <span className="mono">{s.path}</span> })}{" "}
            {data.versions.length > 1 ? t("Other versions of {sid} are not affected.", { sid: s.sid }) : t("This is the only version of this strategy.")}{" "}
            {t("Simulated trades and the audit log are kept.")}
          </p>
          {data.saved_runs > 0 && (
            <label className="row" style={{ marginBottom: 8 }}>
              <input type="checkbox" checked={deleteRuns} onChange={(e) => setDeleteRuns(e.target.checked)} />
              {t(data.saved_runs === 1 ? "Also delete its {n} saved backtest run (otherwise it stays in Backtesting with its own copy of the file)" : "Also delete its {n} saved backtest runs (otherwise they stay in Backtesting with their own copy of the file)", { n: data.saved_runs })}
            </label>
          )}
        </DeleteZone>
      )}
    </>
  );
}

// YAML turns an unquoted "key: text" list item into an object; show it as text instead of crashing.
function asText(a: unknown): string {
  if (typeof a === "string") return a;
  if (a && typeof a === "object") return Object.entries(a).map(([k, v]) => `${k}: ${typeof v === "string" ? v : JSON.stringify(v)}`).join("; ");
  return String(a);
}

const MODES = ["paper_manual", "paper_auto", "live_manual", "live_auto"];
// manual = every trade waits for your approval; auto = trades submit on their own
const modeLabel = (m: string, t: T) => {
  const l = ({ paper_manual: "paper · manual", paper_auto: "paper · auto", live_manual: "live · manual", live_auto: "live · auto",
    manual: "paper · manual", auto: "paper · auto", paper: "paper · auto" } as Record<string, string>)[m];
  return l ? l.split(" · ").map((x) => t(x)).join(" · ") : m;
};

function Lifecycle({ s, ok, busy, act, minPaper, needsReview, category, categories, fund }: { s: any; ok: boolean; busy: string | null; act: (l: string, p: string, b?: object) => void; minPaper: number; needsReview: boolean; category: string | null; categories: string[]; fund: any }) {
  const [mode, setMode] = useState(s.mode);
  const [reviewed, setReviewed] = useState(false);
  const [promoting, setPromoting] = useState(false);
  const base = `/api/strategies/${s.hash}`;
  const canPromote = ok && (s.status === "draft" || s.status === "backtested");
  const t = useT();
  return (
    <Card title={t("Lifecycle")} className="mt">
      <div className="row">
        {ok && <Link className="btn" href={`/backtests?strategy=${s.hash}`}>{t("Configure backtest")}</Link>}
        {canPromote && <button className={s.status === "backtested" ? "primary" : ""} disabled={!!busy || !!s.archived_at} title={s.archived_at ? t("Unarchive first") : undefined}
          onClick={() => setPromoting(!promoting)}>{t(promoting ? "Cancel promotion" : "Promote to paper…")}</button>}
        {s.status !== "retired" && <button disabled={!!busy} onClick={() => act("retire", `${base}/promote`, { to: "retired" })}>{t("Retire")}</button>}
        <span className="row" style={{ marginLeft: 12 }}>
          <select value={mode} onChange={(e) => setMode(e.target.value)} aria-label={t("Execution mode")}>
            {MODES.map((m) => <option key={m} value={m} disabled={m.startsWith("live") && s.status !== "live"}>
              {modeLabel(m, t)}{m.startsWith("live") && s.status !== "live" ? ` ${t("(needs live approval)")}` : ""}</option>)}</select>
          <button disabled={!!busy || mode === s.mode} onClick={() => {
            let confirm = "";
            if (mode === "live_auto") {
              confirm = window.prompt(t("Live auto submits every trade without your approval. A kill-switch trip drops it back to live manual.\n\nType the strategy id ({sid}) to confirm:", { sid: s.sid })) ?? "";
              if (!confirm) return;
            }
            act("mode", `${base}/mode`, { mode, confirm });
          }}>{t("Set mode")}</button>
        </span>
      </div>
      {promoting && canPromote && (
        <PromotePanel fund={fund} busy={!!busy} untested={s.status === "draft"} onPromote={(alloc) => {
          if (needsReview && !window.confirm(t("This file's authoring defaults are proposed, not confirmed (authoring.status: proposed_requires_review). Promote only if you reviewed them, the back-translation and the assumptions."))) return;
          act("paper", `${base}/promote`, { to: "paper", ...(needsReview ? { reviewed: true } : {}), ...alloc });
          setPromoting(false);
        }} />
      )}
      <div className="row" style={{ marginTop: 10 }}>
        <CategoryEditor key={category ?? ""} value={category} categories={categories} busy={!!busy}
          onSave={(c) => act("category", "/api/strategies/meta", { hashes: [s.hash], category: c })} />
        <span className="muted">{t("applies to every version of {sid}", { sid: s.sid })}</span>
        {s.archived_at
          ? <button disabled={!!busy} onClick={() => act("archive", "/api/strategies/meta", { hashes: [s.hash], archived: false })}>{t("Unarchive")}</button>
          : <button disabled={!!busy || ["paper", "live"].includes(s.status)} title={t(["paper", "live"].includes(s.status) ? "Retire it first" : "Hide this version from the strategy list")}
              onClick={() => act("archive", "/api/strategies/meta", { hashes: [s.hash], archived: true })}>{t("Archive version")}</button>}
      </div>
      {s.status === "paper" && (
        <div className="card" style={{ marginTop: 12 }}>
          <b>{t("Approve this exact file (sha256 {hash}) for live", { hash: <span className="mono">{short(s.hash)}…</span> })}</b>
          <p className="muted">{t("Read the assumptions and the back-translation below first. Minimum paper sessions: {n}.", { n: minPaper })}</p>
          <label className="row"><input type="checkbox" checked={reviewed} onChange={(e) => setReviewed(e.target.checked)} /> {t("I reviewed every assumption and the back-translation")}</label>
          <div className="row" style={{ marginTop: 6 }}>
            <button className="good" disabled={!!busy || !reviewed}
              onClick={() => act("live", `${base}/promote`, { to: "live", reviewed })}>{t("Approve hash")}</button>
          </div>
        </div>
      )}
    </Card>
  );
}

function PromotePanel({ fund, busy, untested, onPromote }: { fund: any; busy: boolean; untested: boolean; onPromote: (alloc: object) => void }) {
  const cur = fund?.current;
  const [mode, setMode] = useState<AllocMode>(cur?.mode ?? "pct");
  const [value, setValue] = useState(fund ? allocValue(cur?.mode ?? "pct", cur ? cur.allocation : fund.suggested, cur ? cur.usd : fund.suggested * fund.equity) : "");
  const [reserved, setReserved] = useState(fund ? `${+(((cur ? cur.reserved : fund.suggested_reserved) ?? 1) * 100).toFixed(1)}` : "100");
  const t = useT();
  const warn = untested && <Notice kind="warn">{t("This version hasn't been backtested. It runs on paper with only the validator and risk controls checking it.")}</Notice>;
  if (!fund) {
    return (
      <div className="card" style={{ marginTop: 12 }}>
        {warn}
        <p className="muted">{t("Fund controls are off, so this strategy runs without its own allocation.")}</p>
        <button className="primary" disabled={busy} onClick={() => onPromote({})}>{t("Promote to paper")}</button>
      </div>
    );
  }
  return (
    <div className="card" style={{ marginTop: 12 }}>
      {warn}
      <b>{t("Fund allocation on paper")}</b>
      <p className="muted">
        {t("The money this strategy may use. A percent of the fund grows and shrinks with equity; a fixed amount stays put. Its rules can use up to {wants} of equity; {room} (${usd}) is unreserved. Reserved money is held for it even while idle; the rest of the allocation is drawn from the shared pool only when it trades, so a strategy that seldom trades can get a large allocation with a small reserve.",
          { wants: pct(fund.wants, 1), room: pct(fund.room, 1), usd: money(fund.room * fund.equity) })}
        {cur && ` ${t("This strategy line already has an allocation from an earlier version; saving here replaces it.")}`}
        {fund.kind === "portfolio" && ` ${t("A new sleeve is sized to fit this allocation.")}`}
      </p>
      <div className="row">
        <AllocationInput mode={mode} value={value} equity={fund.equity} disabled={busy}
          onMode={(m, v) => { setMode(m); setValue(v); }} onValue={setValue} />
        <ReservedInput value={reserved} onValue={setReserved} ceilingUsd={ceilingUsd(mode, value, fund.equity)} disabled={busy} />
        <button className="primary" disabled={busy || !value.trim()}
          onClick={() => onPromote({ allocation: allocText(mode, value), alloc_mode: mode, reserved: reserved.trim() || undefined })}>{t("Promote to paper")}</button>
      </div>
      <p className="muted" style={{ fontSize: 12, marginTop: 6 }}>{t("Turn it on or off and adjust the allocation later on the Overview page.")}</p>
    </div>
  );
}

function Duplicate({ s }: { s: any }) {
  const router = useRouter();
  const t = useT();
  const [name, setName] = useState(t("{name} copy", { name: s.display_name || s.sid }));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function duplicate(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await post<any>(`/api/strategies/${s.hash}/duplicate`, { name });
      router.push(`/strategies/${r.hash}`);
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }
  return (
    <Card title={t("Duplicate")} className="mt">
      <p className="muted" style={{ marginTop: 0 }}>
        {t("Copy this file into a new, separate draft strategy (v1, own lifecycle and fund). The original and its versions are untouched.")}
      </p>
      {error && <Notice kind="bad">{error}</Notice>}
      <form className="row" onSubmit={duplicate}>
        <input value={name} onChange={(e) => setName(e.target.value)} maxLength={80} required style={{ flex: 1, minWidth: 160 }} placeholder={t("Name of the copy")} />
        <button className="primary" disabled={busy || !name.trim()}>{t(busy ? "Duplicating…" : "Duplicate")}</button>
      </form>
      <div className="row" style={{ marginTop: 10 }}>
        <Link className="btn" href={`/strategies/new?improve=${s.hash}`}>{t("Duplicate & improve with Kimi")}</Link>
        <span className="muted">{t("optional: tell Kimi what's wrong with it and it drafts a better copy")}</span>
      </div>
    </Card>
  );
}

// View the file, or edit it. Saving writes the next version; a running strategy switches to it and keeps running.
function StrategyFile({ hash, text, owner, running }: { hash: string; text: string; owner: boolean; running: boolean }) {
  const router = useRouter();
  const [editing, setEditing] = useState(false);
  const t = useT();
  const [draft, setDraft] = useState(text);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function save() {
    setBusy(true);
    setError(null);
    try {
      const r = await post<any>(`/api/strategies/${hash}/edit`, { text: draft });
      router.push(`/strategies/${r.hash}`);
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }
  return (
    <Card title={<span className="row between" style={{ width: "100%" }}>{t("Strategy file")}
      {owner && !editing && <button type="button" onClick={() => { setDraft(text); setEditing(true); }}>{t("Edit")}</button>}</span>}>
      {!editing ? <pre style={{ maxHeight: 480 }}>{text}</pre> : (
        <>
          <p className="muted" style={{ marginTop: 0 }}>
            {t(running
              ? "Saving switches this strategy to your edit right away: it stays in paper trading with the same mode and allocation. Open positions keep their exits. The current file is kept as the previous version."
              : "Saving creates the next version as a new draft. This version, its backtests and approvals stay unchanged.")}
          </p>
          {error && <Notice kind="bad">{error}</Notice>}
          <textarea className="mono" value={draft} onChange={(e) => setDraft(e.target.value)} spellCheck={false}
            style={{ width: "100%", minHeight: 420, fontSize: 12 }} />
          <div className="row" style={{ marginTop: 8 }}>
            <button className="primary" disabled={busy || draft === text} onClick={save}>{t(busy ? "Saving…" : running ? "Save and apply" : "Save as new version")}</button>
            <button type="button" disabled={busy} onClick={() => { setEditing(false); setError(null); }}>{t("Cancel")}</button>
          </div>
        </>
      )}
    </Card>
  );
}

function ApplyDiff({ hash }: { hash: string }) {
  const router = useRouter();
  const [diff, setDiff] = useState("");
  const t = useT();
  const [msg, setMsg] = useState<{ kind: "ok" | "bad"; text: string } | null>(null);
  async function apply(e: React.FormEvent) {
    e.preventDefault();
    try {
      const r = await post(`/api/strategies/${hash}/apply-diff`, { diff });
      setMsg({ kind: "ok", text: r.message });
      router.push("/strategies");
    } catch (err) {
      setMsg({ kind: "bad", text: (err as Error).message });
    }
  }
  return (
    <details style={{ marginTop: 10 }}>
      <summary>{t("Accept a proposed diff as a new draft version")}</summary>
      {msg && <Notice kind={msg.kind}>{msg.text}</Notice>}
      <form onSubmit={apply}>
        <textarea className="mono" value={diff} onChange={(e) => setDiff(e.target.value)} required placeholder={t("Paste the ```diff block from Kimi's answer (without the fences)")} />
        <button>{t("Create new draft from diff")}</button>
      </form>
    </details>
  );
}

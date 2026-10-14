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

export default function StrategyPage() {
  const { hash } = useParams<{ hash: string }>();
  const { data, error, reload } = usePoll<any>(`/api/strategies/${hash}`, 5000);
  const { me } = useSession();
  const router = useRouter();
  const [deleteRuns, setDeleteRuns] = useState(false);
  const [msg, setMsg] = useState<{ kind: "ok" | "bad"; text: string } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
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
    if (label === "paper" && !window.confirm("Promoting this strategy will automatically enable monitoring for its required watchlist tickers. Continue?")) return;
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
            {s.archived_at && <Pill kind="warn">archived {wall(s.archived_at)}</Pill>}
            {data.category && <Link href="/strategies"><Pill>{data.category}</Pill></Link>}
            <Pill kind={portfolio ? "accent" : ""}>{portfolio ? "portfolio · held overnight" : "intraday"}</Pill>
            <Pill kind={s.mode === "live_auto" ? "bad" : s.mode?.startsWith("live") ? "warn" : ""}>{modeLabel(s.mode)}</Pill>
            {data.fund && <Link href="/funds"><Pill kind={data.fund.status === "active" ? "" : "bad"}>fund {pct(data.fund.allocation, 1)} of equity{data.fund.status !== "active" ? ` · ${data.fund.status}` : ""}</Pill></Link>}
            <Pill title={s.hash}><span className="mono">sha256 {short(s.hash)}…</span></Pill>
            {!data.integrity && <Pill kind="bad">file changed on disk: hash mismatch</Pill>}
            {s.approved_by && <Pill kind="good">approved by {s.approved_by} {wall(s.approved_at)}</Pill>}
          </div>
        </div>
        <div className="row">
          {data.versions.map((vv: any) => (
            <span key={vv.hash} className="row" style={{ gap: 4 }}>
              <Link className={`btn ${vv.hash === s.hash ? "on" : ""}`} href={`/strategies/${vv.hash}`} title={vv.archived_at ? "archived" : vv.status}
                style={vv.archived_at ? { opacity: 0.55 } : undefined}>v{vv.version}</Link>
              {vv.hash !== s.hash && <Link className="muted" href={`/strategies/${s.hash}/diff?against=${vv.hash}`}>diff</Link>}
            </span>
          ))}
        </div>
      </div>

      {!v.ok && <Notice kind="bad"><b>Validator errors</b><ul>{v.errors.map((e: string) => <li key={e}>{e}</li>)}</ul></Notice>}
      <Card title="Ticker monitoring" className="mt">
        <p>{portfolio ? "Required by this portfolio strategy" : "Shared watchlist used by this intraday strategy"}: {data.monitoring?.required.join(", ") || "No tickers configured"}. <Link href="/watchlist">Manage monitoring</Link></p>
        {!!data.monitoring?.unwatched.length && <Notice kind="warn">Monitoring is off for {data.monitoring.unwatched.join(", ")}. This strategy cannot open new positions in those tickers. Held positions retain risk monitoring.</Notice>}
      </Card>
      {v.warnings.length > 0 && <Notice kind="warn">{v.warnings.map((w: string) => <div key={w}>{w}</div>)}</Notice>}

      {isOwner(me) && <Lifecycle s={s} ok={v.ok} busy={busy} act={act} minPaper={data.lifecycle?.min_paper_sessions ?? 0}
        needsReview={!!data.portfolio?.overlays?.review_required} category={data.category} categories={data.categories} fund={data.fund_suggest} />}
      {isOwner(me) && <Duplicate s={s} />}
      <IterateCard hash={s.hash} d={data.discovery} owner={isOwner(me)} />

      <div className="grid g2 mt">
        <Card title="Assumptions (need your OK)">
          {data.assumptions.length ? <ul>{data.assumptions.map((a: unknown, i: number) => <li key={i}>{asText(a)}</li>)}</ul> : <Empty>None listed.</Empty>}
          {data.suggestions.length > 0 && (<><h3>Suggestions (not applied)</h3><ul>{data.suggestions.map((a: unknown, i: number) => <li key={i}>{asText(a)}</li>)}</ul></>)}
          {data.source?.text && (
            <>
              <h3>Source request</h3>
              <p className="muted">{data.source.text}</p>
              <p className="muted">compiled by {data.source.compiled_by} · prompt {data.source.prompt}</p>
            </>
          )}
        </Card>
        <Card title="Back-translation">
          <p className="muted">Generated by code from the parsed rules, independent of the model that wrote the file.</p>
          <pre>{data.back_translation ?? "n/a: the file does not validate"}</pre>
        </Card>
      </div>

      {portfolio && <SleeveCard p={data.portfolio} decideAt={data.decide_at ?? "15:45"} />}
      {portfolio && data.portfolio?.overlays && <OverlaysCard o={data.portfolio.overlays} hash={s.hash} act={act} busy={busy} />}

      <Card title="Backtest" className="mt">
        <p><Link href={`/backtests?strategy=${s.hash}`}>Configure a new experiment and review saved runs →</Link>{bt?.run_id && <> · <Link href={`/backtests?run=${bt.run_id}`}>Open run #{bt.run_id}</Link></>}</p>
        {bt && bt.kind === "portfolio" ? <PortfolioBacktest bt={bt} /> : bt ? (
          <>
            <div className="grid g4">
              <Stat label="Trades" value={bt.metrics.trades} sub={`${bt.days.length} sessions · ${bt.signal_note}`} />
              <Stat label="Win rate · avg R" value={pct(bt.metrics.win_rate, 0)} sub={`${num(bt.metrics.avg_r, 2, true)} R`} />
              <Stat label="P&L" value={money(bt.metrics.total_pnl, true)} valueClass={pnlClass(bt.metrics.total_pnl)} sub={pct(bt.metrics.return_pct, 2, true)} />
              <Stat label="Max drawdown" value={pct(bt.metrics.max_drawdown)} sub={`Sharpe (daily) ${num(bt.metrics.sharpe_daily)}`} />
            </div>
            <LineChart values={bt.equity_curve.map((p: any) => p.equity)} width={900} height={160} baseline={bt.starting_equity} />
            <p className="muted">
              Exits: {Object.entries(bt.metrics.exits).map(([k, n]) => `${k} ${n}`).join(", ")} · rejected by risk guard: {bt.rejected_by_guard} · ran{" "}
              {wall(bt.ran_at)} by {bt.ran_by} in {bt.seconds}s
            </p>
            <details>
              <summary>Trades ({bt.trades.length})</summary>
              <table>
                <thead><tr><th>Day</th><th>Symbol</th><th className="num">Qty</th><th className="num">Entry</th><th className="num">Exit</th><th>How</th><th className="num">P&amp;L</th><th className="num">R</th></tr></thead>
                <tbody>{bt.trades.map((t: any, i: number) => (
                  <tr key={i}><td>{t.day}</td><td>{t.symbol}</td><td className="num">{t.qty}</td><td className="num">{money(t.entry_price)}</td><td className="num">{money(t.exit_price)}</td>
                    <td>{t.exit_kind}</td><td className={`num ${pnlClass(t.pnl)}`}>{money(t.pnl, true)}</td><td className="num">{num(t.r_multiple, 2, true)}</td></tr>
                ))}</tbody>
              </table>
            </details>
          </>
        ) : <Empty>Not run yet.</Empty>}
      </Card>

      <div className={portfolio ? "mt" : "grid g2 mt"}>
        {!portfolio && <Card title="Decision records (this session)">
          <table>
            <thead><tr><th>#</th><th>Bar</th><th>Symbol</th><th className="num">Qty</th><th>Status</th></tr></thead>
            <tbody>
              {data.intents.map((i: any) => <tr key={i.id}><td>{i.id}</td><td className="mono">{et(i.ts)}</td><td>{i.symbol}</td><td className="num">{i.qty}</td><td><StatusPill status={i.status} /></td></tr>)}
              {!data.intents.length && <tr><td colSpan={5} className="muted">None.</td></tr>}
            </tbody>
          </table>
          <h3>Simulated trades</h3>
          <table><tbody>
            {data.trades.map((t: any) => <tr key={t.id}><td><Link href={`/trades/${t.id}`}>#{t.id}</Link></td><td>{t.symbol}</td><td>{t.exit_kind}</td><td className={`num ${pnlClass(t.pnl)}`}>{money(t.pnl, true)}</td></tr>)}
            {!data.trades.length && <tr><td className="muted">None.</td></tr>}
          </tbody></table>
        </Card>}
        <StrategyFile key={s.hash} hash={s.hash} text={data.text} owner={isOwner(me)} running={s.status === "paper"} />
      </div>

      <Card title="Ask Kimi" className="mt" id="chat">
        <Chat prefix={`strategy:${s.hash}`}
          intro="A new conversation, seeded with the strategy file, back-translation, backtest report and recent decision records. Kimi can explain and critique, and propose changes only as a diff, which you can accept as a new draft version." />
        {isOwner(me) && <ApplyDiff hash={s.hash} />}
      </Card>

      {isOwner(me) && (
        <DeleteZone what={`version (v${s.version})`} confirmText={s.sid} blockers={data.delete_blockers} busy={!!busy} onDelete={remove}>
          <p className="muted">
            Removes <span className="mono">{s.path}</span>, its registry entry and its sleeve/overlay state.
            {data.versions.length > 1 ? ` Other versions of ${s.sid} are not affected.` : " This is the only version of this strategy."}{" "}
            Simulated trades and the audit log are kept.
          </p>
          {data.saved_runs > 0 && (
            <label className="row" style={{ marginBottom: 8 }}>
              <input type="checkbox" checked={deleteRuns} onChange={(e) => setDeleteRuns(e.target.checked)} />
              Also delete its {data.saved_runs} saved backtest run{data.saved_runs === 1 ? "" : "s"} (otherwise they stay in Backtesting with their own copy of the file)
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
const modeLabel = (m: string) => ({ paper_manual: "paper · manual", paper_auto: "paper · auto", live_manual: "live · manual", live_auto: "live · auto",
  manual: "paper · manual", auto: "paper · auto", paper: "paper · auto" } as Record<string, string>)[m] ?? m;

function Lifecycle({ s, ok, busy, act, minPaper, needsReview, category, categories, fund }: { s: any; ok: boolean; busy: string | null; act: (l: string, p: string, b?: object) => void; minPaper: number; needsReview: boolean; category: string | null; categories: string[]; fund: any }) {
  const [mode, setMode] = useState(s.mode);
  const [reviewed, setReviewed] = useState(false);
  const [promoting, setPromoting] = useState(false);
  const base = `/api/strategies/${s.hash}`;
  const canPromote = ok && (s.status === "draft" || s.status === "backtested");
  return (
    <Card title="Lifecycle" className="mt">
      <div className="row">
        {ok && <Link className="btn" href={`/backtests?strategy=${s.hash}`}>Configure backtest</Link>}
        {canPromote && <button className={s.status === "backtested" ? "primary" : ""} disabled={!!busy || !!s.archived_at} title={s.archived_at ? "Unarchive first" : undefined}
          onClick={() => setPromoting(!promoting)}>{promoting ? "Cancel promotion" : "Promote to paper…"}</button>}
        {s.status !== "retired" && <button disabled={!!busy} onClick={() => act("retire", `${base}/promote`, { to: "retired" })}>Retire</button>}
        <span className="row" style={{ marginLeft: 12 }}>
          <select value={mode} onChange={(e) => setMode(e.target.value)} aria-label="Execution mode">
            {MODES.map((m) => <option key={m} value={m} disabled={m.startsWith("live") && s.status !== "live"}>
              {modeLabel(m)}{m.startsWith("live") && s.status !== "live" ? " (needs live approval)" : ""}</option>)}</select>
          <button disabled={!!busy || mode === s.mode} onClick={() => {
            let confirm = "";
            if (mode === "live_auto") {
              confirm = window.prompt(`Live auto submits every trade without your approval. A kill-switch trip drops it back to live manual.\n\nType the strategy id (${s.sid}) to confirm:`) ?? "";
              if (!confirm) return;
            }
            act("mode", `${base}/mode`, { mode, confirm });
          }}>Set mode</button>
        </span>
      </div>
      {promoting && canPromote && (
        <PromotePanel fund={fund} busy={!!busy} untested={s.status === "draft"} onPromote={(alloc) => {
          if (needsReview && !window.confirm("This file's authoring defaults are proposed, not confirmed (authoring.status: proposed_requires_review). Promote only if you reviewed them, the back-translation and the assumptions.")) return;
          act("paper", `${base}/promote`, { to: "paper", ...(needsReview ? { reviewed: true } : {}), ...alloc });
          setPromoting(false);
        }} />
      )}
      <div className="row" style={{ marginTop: 10 }}>
        <CategoryEditor key={category ?? ""} value={category} categories={categories} busy={!!busy}
          onSave={(c) => act("category", "/api/strategies/meta", { hashes: [s.hash], category: c })} />
        <span className="muted">applies to every version of {s.sid}</span>
        {s.archived_at
          ? <button disabled={!!busy} onClick={() => act("archive", "/api/strategies/meta", { hashes: [s.hash], archived: false })}>Unarchive</button>
          : <button disabled={!!busy || ["paper", "live"].includes(s.status)} title={["paper", "live"].includes(s.status) ? "Retire it first" : "Hide this version from the strategy list"}
              onClick={() => act("archive", "/api/strategies/meta", { hashes: [s.hash], archived: true })}>Archive version</button>}
      </div>
      {s.status === "paper" && (
        <div className="card" style={{ marginTop: 12 }}>
          <b>Approve this exact file (sha256 <span className="mono">{short(s.hash)}…</span>) for live</b>
          <p className="muted">Read the assumptions and the back-translation below first. Minimum paper sessions: {minPaper}.</p>
          <label className="row"><input type="checkbox" checked={reviewed} onChange={(e) => setReviewed(e.target.checked)} /> I reviewed every assumption and the back-translation</label>
          <div className="row" style={{ marginTop: 6 }}>
            <button className="good" disabled={!!busy || !reviewed}
              onClick={() => act("live", `${base}/promote`, { to: "live", reviewed })}>Approve hash</button>
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
  const warn = untested && <Notice kind="warn">This version hasn&apos;t been backtested. It runs on paper with only the validator and risk controls checking it.</Notice>;
  if (!fund) {
    return (
      <div className="card" style={{ marginTop: 12 }}>
        {warn}
        <p className="muted">Fund controls are off, so this strategy runs without its own allocation.</p>
        <button className="primary" disabled={busy} onClick={() => onPromote({})}>Promote to paper</button>
      </div>
    );
  }
  return (
    <div className="card" style={{ marginTop: 12 }}>
      {warn}
      <b>Fund allocation on paper</b>
      <p className="muted">
        The money this strategy may use. A percent of the fund grows and shrinks with equity; a fixed amount stays put. Its rules can use up to {pct(fund.wants, 1)} of
        equity; {pct(fund.room, 1)} (${money(fund.room * fund.equity)}) is unreserved. Reserved money is held for it even while idle; the rest
        of the allocation is drawn from the shared pool only when it trades, so a strategy that seldom trades can get a large allocation with a small reserve.
        {cur && " This strategy line already has an allocation from an earlier version; saving here replaces it."}
        {fund.kind === "portfolio" && " A new sleeve is sized to fit this allocation."}
      </p>
      <div className="row">
        <AllocationInput mode={mode} value={value} equity={fund.equity} disabled={busy}
          onMode={(m, v) => { setMode(m); setValue(v); }} onValue={setValue} />
        <ReservedInput value={reserved} onValue={setReserved} ceilingUsd={ceilingUsd(mode, value, fund.equity)} disabled={busy} />
        <button className="primary" disabled={busy || !value.trim()}
          onClick={() => onPromote({ allocation: allocText(mode, value), alloc_mode: mode, reserved: reserved.trim() || undefined })}>Promote to paper</button>
      </div>
      <p className="muted" style={{ fontSize: 12, marginTop: 6 }}>Turn it on or off and adjust the allocation later on the Overview page.</p>
    </div>
  );
}

function Duplicate({ s }: { s: any }) {
  const router = useRouter();
  const [name, setName] = useState(`${s.display_name || s.sid} copy`);
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
    <Card title="Duplicate" className="mt">
      <p className="muted" style={{ marginTop: 0 }}>
        Copy this file into a new, separate draft strategy (v1, own lifecycle and fund). The original and its versions are untouched.
      </p>
      {error && <Notice kind="bad">{error}</Notice>}
      <form className="row" onSubmit={duplicate}>
        <input value={name} onChange={(e) => setName(e.target.value)} maxLength={80} required style={{ flex: 1, minWidth: 160 }} placeholder="Name of the copy" />
        <button className="primary" disabled={busy || !name.trim()}>{busy ? "Duplicating…" : "Duplicate"}</button>
      </form>
      <div className="row" style={{ marginTop: 10 }}>
        <Link className="btn" href={`/strategies/new?improve=${s.hash}`}>Duplicate &amp; improve with Kimi</Link>
        <span className="muted">optional: tell Kimi what&apos;s wrong with it and it drafts a better copy</span>
      </div>
    </Card>
  );
}

// View the file, or edit it. Saving writes the next version; a running strategy switches to it and keeps running.
function StrategyFile({ hash, text, owner, running }: { hash: string; text: string; owner: boolean; running: boolean }) {
  const router = useRouter();
  const [editing, setEditing] = useState(false);
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
    <Card title={<span className="row between" style={{ width: "100%" }}>Strategy file
      {owner && !editing && <button type="button" onClick={() => { setDraft(text); setEditing(true); }}>Edit</button>}</span>}>
      {!editing ? <pre style={{ maxHeight: 480 }}>{text}</pre> : (
        <>
          <p className="muted" style={{ marginTop: 0 }}>
            {running
              ? "Saving switches this strategy to your edit right away: it stays in paper trading with the same mode and allocation. Open positions keep their exits. The current file is kept as the previous version."
              : "Saving creates the next version as a new draft. This version, its backtests and approvals stay unchanged."}
          </p>
          {error && <Notice kind="bad">{error}</Notice>}
          <textarea className="mono" value={draft} onChange={(e) => setDraft(e.target.value)} spellCheck={false}
            style={{ width: "100%", minHeight: 420, fontSize: 12 }} />
          <div className="row" style={{ marginTop: 8 }}>
            <button className="primary" disabled={busy || draft === text} onClick={save}>{busy ? "Saving…" : running ? "Save and apply" : "Save as new version"}</button>
            <button type="button" disabled={busy} onClick={() => { setEditing(false); setError(null); }}>Cancel</button>
          </div>
        </>
      )}
    </Card>
  );
}

function ApplyDiff({ hash }: { hash: string }) {
  const router = useRouter();
  const [diff, setDiff] = useState("");
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
      <summary>Accept a proposed diff as a new draft version</summary>
      {msg && <Notice kind={msg.kind}>{msg.text}</Notice>}
      <form onSubmit={apply}>
        <textarea className="mono" value={diff} onChange={(e) => setDiff(e.target.value)} required placeholder="Paste the ```diff block from Kimi's answer (without the fences)" />
        <button>Create new draft from diff</button>
      </form>
    </details>
  );
}

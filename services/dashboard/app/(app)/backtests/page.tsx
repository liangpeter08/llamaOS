"use client";

import Link from "next/link";
import { useEffect, useState, type ReactNode } from "react";
import { get, post } from "@/lib/api";
import { usePoll } from "@/lib/hooks";
import { et, money, num, pct, pnlClass, short, wall } from "@/lib/format";
import { Card, Empty, Loading, Meter, Notice, Pill, Stat } from "@/components/ui";
import { LineChart } from "@/components/LineChart";
import { isOwner, useSession } from "@/components/session";
import { BulkBar, CategoryEditor, DeleteZone, ViewSwitch, type View } from "@/components/Organize";

type Config = {
  strategy_hash: string; name: string; notes: string; tags: string[]; start_date: string | null; end_date: string | null;
  symbols: string[]; starting_equity: number; slippage_bps: number; spread_bps: number; commission_per_share: number;
  commission_per_order: number; latency_bars: number; cancel_after_bars: number; limit_fill: string; warmup_bars: number;
  min_history_sessions: number; missing_data: string; max_volume_participation: number; benchmark: string; risk_free_rate: number;
  holdout_pct: number; rolling_window: number; cost_stress: boolean; repeat_cycles: boolean;
  model_replay: "recorded" | "offline" | "models"; model_budget_usd: number; confirm_model_cost: boolean; bootstrap_samples: number; seed: number;
  keep_records: number; parent_id: number | null;
};
const tabs = ["Configure", "Saved runs", "Research & capabilities"];

export default function BacktestsPage() {
  const { me } = useSession();
  const owner = isOwner(me);
  const catalog = usePoll<any>("/api/backtests/catalog");
  const presets = usePoll<any>("/api/backtests/presets");
  const [config, setConfig] = useState<Config | null>(null);
  const [tab, setTab] = useState("Configure");
  const [rid, setRid] = useState<number | null>(null);
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [view, setView] = useState<View>("active");
  const [category, setCategory] = useState("");
  const [strategyFilter, setStrategyFilter] = useState("");
  const [offset, setOffset] = useState(0);
  const [compare, setCompare] = useState<any[]>([]);
  const [preflight, setPreflight] = useState<any>(null);
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState<{ kind: "ok" | "bad"; text: string } | null>(null);
  const history = usePoll<any>(`/api/backtests?q=${encodeURIComponent(search)}&status=${status}&view=${view}&category=${encodeURIComponent(category)}&strategy_hash=${strategyFilter}&offset=${offset}`, 3000);
  const detail = usePoll<any>(rid ? `/api/backtests/${rid}` : null, 2000);

  useEffect(() => {
    if (!catalog.data || config) return;
    const query = new URLSearchParams(window.location.search);
    const selected = catalog.data.strategies.find((s: any) => s.hash === query.get("strategy")) || catalog.data.strategies.find((s: any) => s.valid && !s.archived);
    setConfig({ ...catalog.data.defaults, strategy_hash: selected?.hash || "", symbols: selected?.kind === "portfolio" ? selected.symbols : [] });
    if (query.get("run")) { setRid(Number(query.get("run"))); setTab("Saved runs"); }
  }, [catalog.data, config]);

  function update(key: keyof Config, value: any) {
    setConfig(c => c ? { ...c, [key]: value } : c);
    setPreflight(null);
  }
  function restore(c: Config, parent?: number) {
    setConfig({ ...catalog.data.defaults, ...c, parent_id: parent || null });
    setPreflight(null); setTab("Configure");
    setMessage({ kind: "ok", text: "Configuration restored. Review it before running a new experiment." });
  }
  function openRun(id: number) {
    setRid(id);
    window.history.replaceState(null, "", `/backtests?run=${id}`);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }
  async function action(label: string, work: () => Promise<void>) {
    setBusy(label); setMessage(null);
    try { await work(); } catch (e) { setMessage({ kind: "bad", text: (e as Error).message }); }
    finally { setBusy(""); }
  }
  const organize = (ids: number[], body: object) => action("organize", async () => {
    const r = await post<any>("/api/backtests/meta", { ids, ...body });
    setMessage({ kind: "ok", text: r.message }); history.reload(); detail.reload();
  });
  const removeRun = (id: number) => action("delete", async () => {
    const r = await post<any>(`/api/backtests/${id}/delete`);
    setMessage({ kind: "ok", text: r.message });
    setCompare(cs => cs.filter(c => c.id !== id));
    setRid(null); window.history.replaceState(null, "", "/backtests"); history.reload();
  });
  if (!catalog.data || !config) return <Loading error={catalog.error} />;
  const selected = catalog.data.strategies.find((s: any) => s.hash === config.strategy_hash);
  const portfolio = selected?.kind === "portfolio";
  const numberInput = (key: keyof Config, label: string, min: number, max: number, step = 1, hint?: string, disabled = false) => (
    <Field label={label} hint={hint}><input type="number" value={String(config[key])} min={min} max={max} step={step} disabled={disabled}
      onChange={e => update(key, e.target.value === "" ? 0 : Number(e.target.value))} /></Field>
  );

  return <>
    <div className="row between"><div><h1>Backtesting</h1><p className="muted">Configure an experiment. Replay recorded data. Keep the evidence.</p></div>
      <Pill kind="accent">RESEARCH WORKSPACE</Pill></div>
    {message && <Notice kind={message.kind}>{message.text}</Notice>}
    <div className="tabs bt-tabs">{tabs.map(t => <button key={t} className={tab === t ? "on" : ""} onClick={() => setTab(t)}>{t}</button>)}</div>

    {tab === "Configure" && <div className="bt-workspace">
      <form onSubmit={e => { e.preventDefault(); action("run", async () => {
        const info = await post<any>("/api/backtests/preflight", config); setPreflight(info);
        if (!info.can_run) throw new Error("No usable stored data in this window. Review the coverage report.");
        if (info.replay?.high_cost && !config.confirm_model_cost) throw new Error(`Estimated model cost is $${info.replay.claude_usd.toFixed(2)}. Review it under 5 · Model replay and confirm before running.`);
        const r = await post<any>("/api/backtests", config); openRun(r.id); setTab("Saved runs"); history.reload();
      }); }}>
        <fieldset disabled={!owner || !!busy} className="bt-fieldset">
        <Card title="1 · Experiment">
          <div className="bt-fields">
            <Field label="Strategy version"><select value={config.strategy_hash} onChange={e => {
              const s = catalog.data.strategies.find((s: any) => s.hash === e.target.value);
              setConfig({ ...config, strategy_hash: e.target.value, symbols: s?.kind === "portfolio" ? s.symbols : [], latency_bars: 0, warmup_bars: 0, limit_fill: "touch", max_volume_participation: 1, min_history_sessions: 20 }); setPreflight(null);
            }}><option value="">Select a strategy</option>{catalog.data.strategies.filter((s: any) => !s.archived || s.hash === config.strategy_hash).map((s: any) => <option key={s.hash} value={s.hash} disabled={!s.valid}>{s.display_name || s.sid} · v{s.version} · {s.kind}{s.valid ? "" : " · invalid"}{s.archived ? " · archived" : ""}</option>)}</select></Field>
            <Field label="Run name"><input value={config.name} required maxLength={120} onChange={e => update("name", e.target.value)} /></Field>
          </div>
          <Field label="Research notes"><textarea value={config.notes} maxLength={4000} placeholder="Hypothesis, expected behavior, and what would falsify it" onChange={e => update("notes", e.target.value)} /></Field>
          <Field label="Tags (comma separated)"><input value={config.tags.join(", ")} onChange={e => update("tags", e.target.value.split(",").map(t => t.trim()))} /></Field>
          {selected && <p className="muted"><Link href={`/strategies/${selected.hash}`}>Review strategy rules</Link> · SHA-256 {short(selected.hash)} · frozen risk controls saved with every run</p>}
          {config.parent_id && <Pill>Derived from run #{config.parent_id}</Pill>}
        </Card>
        <Card title="2 · Data & capital" className="mt">
          <div className="bt-fields">
            <Field label="Start session (ET)"><input type="date" value={config.start_date || ""} onChange={e => update("start_date", e.target.value || null)} /></Field>
            <Field label="End session (ET)"><input type="date" value={config.end_date || ""} min={config.start_date || undefined} onChange={e => update("end_date", e.target.value || null)} /></Field>
            <Field label="Symbols (comma separated)" hint={portfolio ? "Complete strategy basket; weights remain in the strategy." : "Leave empty to use the current configured universe."}>
              <input value={config.symbols.join(", ")} disabled={portfolio} onChange={e => update("symbols", e.target.value.split(",").map(s => s.trim().toUpperCase()))} /></Field>
            {numberInput("starting_equity", "Starting equity · USD", 100, 1e9, 100)}
            <Field label="Missing data policy"><select value={config.missing_data} onChange={e => update("missing_data", e.target.value)}>
              <option value="skip_symbol">Exclude unavailable symbols / unmatched bars</option><option value="strict">Fail on missing or misaligned data</option></select></Field>
            <Field label="Benchmark" hint="Buy at the first evaluated session open; compare daily closes. No execution costs."><select value={config.benchmark} onChange={e => update("benchmark", e.target.value)}>
              <option value="">Cash · 0% return</option>{catalog.data.symbols.map((s: any) => <option key={s.symbol} value={s.symbol}>{s.symbol} · stored buy-and-hold</option>)}</select></Field>
            {numberInput("min_history_sessions", "Required prior daily sessions", 20, 60, 1, "Used for ADV and indicator readiness.", portfolio)}
            {numberInput("warmup_bars", "Intraday entry warm-up · bars", 0, 389, 1, "Indicators observe these bars; entries stay disabled.", portfolio)}
          </div>
          <p className="muted">{portfolio ? "Daily portfolio replay" : "One-minute regular-session replay"} · America/New_York · USD. Blank dates use the last {portfolio ? "2 years" : "60 days"}. Missing history is downloaded (split-adjusted) from Alpaca when the paper broker runs.</p>
          {portfolio && <Field label="Holding periods"><select value={String(config.repeat_cycles ?? true)} onChange={e => update("repeat_cycles", e.target.value === "true")}>
            <option value="true">Repeat: start a new holding period whenever one ends</option>
            <option value="false">Once: stop when the strategy&apos;s holding period ends</option>
          </select></Field>}
          {portfolio && <Notice kind="warn">Portfolio rules are evaluated and filled at the same daily close. This is an approximation; dividends, borrow and other carry costs are not modeled.</Notice>}
        </Card>
        <Card title="3 · Execution & transaction costs" className="mt">
          <div className="bt-fields">
            {numberInput("slippage_bps", "Slippage · bps per side", 0, 1000, .1)}
            {numberInput("spread_bps", "Full spread · bps", 0, 1000, .1, "Half the spread is added to adverse market slippage; limits retain their price bound.")}
            {numberInput("commission_per_share", "Commission · USD per share", 0, 100, .001)}
            {numberInput("commission_per_order", "Commission · USD per fill", 0, 1000, .01)}
            {numberInput("latency_bars", "Additional order latency · bars", 0, 30, 1, "Zero still means next-bar eligibility.", portfolio)}
            {numberInput("cancel_after_bars", "Cancel after eligible unfilled bars", 1, 60, 1, undefined, portfolio)}
            <Field label="Limit eligibility"><select disabled={portfolio} value={config.limit_fill} onChange={e => update("limit_fill", e.target.value)}>
              <option value="touch">Fill on touch</option><option value="trade_through">Require price to trade through</option></select></Field>
            <Field label="Maximum current-bar volume participation" hint="Entries above the cap are rejected as whole orders. Partial fills are not modeled."><input type="number" min={.001} max={1} step={.001} disabled={portfolio} value={config.max_volume_participation} onChange={e => update("max_volume_participation", Number(e.target.value))} /></Field>
          </div>
          <p className="muted">Intraday brackets assume stop first when stop and target are touched in the same bar. Limit fills are capped at the limit; target fills receive no extra adverse slippage.</p>
        </Card>
        <Card title="4 · Validation & reporting" className="mt">
          <div className="bt-fields">
            <Field label="Chronological holdout"><select value={config.holdout_pct} onChange={e => update("holdout_pct", Number(e.target.value))}>
              <option value={0}>Disabled</option><option value={.2}>Last 20% of requested sessions</option><option value={.3}>Last 30%</option><option value={.5}>Last 50%</option></select></Field>
            {numberInput("rolling_window", "Fixed-rule window · sessions", 0, 252, 1, "0 disables. At least 5; each window starts with fresh capital.")}
            <Field label="Execution cost stress"><select value={String(config.cost_stress)} onChange={e => update("cost_stress", e.target.value === "true")}>
              <option value="false">Disabled</option><option value="true">Replay at 2× and 3× costs</option></select></Field>
            {numberInput("bootstrap_samples", "Daily-return bootstrap · samples", 0, 2000, 100, "0 disables; seeded independent resampling, not price simulation.")}
            {numberInput("seed", "Bootstrap random seed", 0, 2147483647)}
            {numberInput("risk_free_rate", "Annual risk-free rate · fraction", 0, .5, .001, "0.04 = 4%; 252-session geometric conversion.")}
            {numberInput("keep_records", "Decision example limit", 0, 1000)}
          </div>
          <p className="muted">Holdout and rolling windows replay fixed rules independently. They do not optimize parameters. Repeated holdout access is another research trial.</p>
        </Card>
        <Card title="5 · Model replay (news and assumptions)" className="mt">
          <div className="bt-fields">
            <Field label="Model answers" hint="Where news signals and assumption assessments come from during the replay.">
              <select value={config.model_replay ?? "recorded"} onChange={e => { setConfig(c => c ? { ...c, model_replay: e.target.value as Config["model_replay"], confirm_model_cost: false } : c); setPreflight(null); }}>
                <option value="recorded">Recorded signals (free; what was recorded live)</option>
                <option value="offline">Offline model re-triage (free, deterministic, cruder)</option>
                <option value="models">Live models: Laya, then Claude on escalation (paid, not deterministic)</option>
              </select></Field>
            {numberInput("model_budget_usd", "Paid model budget · USD per run", 0, 200, 1, "Paid calls stop here; remaining stories use the offline model and the report counts them.", config.model_replay !== "models")}
          </div>
          {config.model_replay !== "recorded" && <p className="muted">The run downloads the window&apos;s historical news (paper broker) and triages each story with the same T0 → T1 → T2 pipeline as live trading, without your current notes or theses. Answers are cached, so repeating a run doesn&apos;t pay twice.</p>}
          {config.model_replay !== "recorded" && preflight?.replay && <ReplayEstimate r={preflight.replay} confirmed={!!config.confirm_model_cost} onConfirm={v => update("confirm_model_cost", v)} />}
          {config.model_replay !== "recorded" && !preflight?.replay && <p className="muted">Click &ldquo;Check data coverage&rdquo; for the cost estimate and whether this strategy can be influenced.</p>}
        </Card>
        <div className="row mt">
          <button type="button" disabled={!config.strategy_hash} onClick={() => action("preflight", async () => setPreflight(await post("/api/backtests/preflight", config)))}>{busy === "preflight" ? "Checking…" : "Check data coverage"}</button>
          <button type="submit" className="primary" disabled={!config.strategy_hash}>{busy === "run" ? "Saving run…" : "Run & save backtest"}</button>
          <button type="button" disabled={!config.strategy_hash} onClick={() => action("preset", async () => {
            await post("/api/backtests/presets", { name: config.name, config }); presets.reload(); setMessage({ kind: "ok", text: "Configuration preset saved." });
          })}>Save preset</button>
        </div>
        </fieldset>
      </form>
      <aside>
        <Card title="Saved configurations">
          <Field label="Restore a preset"><select value="" onChange={e => { const p = presets.data?.rows.find((p: any) => p.id === Number(e.target.value)); if (p) restore(p.config); }}>
            <option value="">Choose a saved preset…</option>{presets.data?.rows.map((p: any) => <option key={p.id} value={p.id}>{p.name} · #{p.id}</option>)}</select></Field>
          {!presets.data?.rows.length && <Empty>Save a configuration to reuse its assumptions later.</Empty>}
        </Card>
        <Card title="Stored data" className="mt"><div className="table-wrap"><table><thead><tr><th>Symbol</th><th>Sessions</th><th>Through</th></tr></thead><tbody>
          {catalog.data.symbols.map((s: any) => <tr key={s.symbol}><td>{s.symbol}</td><td>{s.sessions}</td><td>{s.last}</td></tr>)}
        </tbody></table></div><p className="muted">Daily history count; use preflight to check intraday coverage and recorded signals.</p></Card>
        <Card title="Frozen risk controls" className="mt"><dl className="kv">
          <dt>Position cap</dt><dd>{pct(catalog.data.risk.max_position)}</dd><dt>Gross exposure</dt><dd>{num(catalog.data.risk.max_gross_exposure)}×</dd>
          <dt>Daily loss limit</dt><dd>{pct(catalog.data.risk.max_daily_loss)}</dd><dt>Open positions</dt><dd>{catalog.data.risk.max_open_positions}</dd>
          <dt>Trades / day</dt><dd>{catalog.data.risk.max_trades_per_day}</dd><dt>Shorting</dt><dd>{catalog.data.risk.allow_short ? "Allowed" : "Disabled"}</dd>
        </dl><details className="mt"><summary>All effective controls</summary><pre>{JSON.stringify(catalog.data.risk, null, 2)}</pre></details></Card>
        {preflight && <Card title="Data preflight" className="mt"><Pill kind={preflight.can_run ? "good" : "bad"}>{preflight.sessions} requested sessions</Pill>
          <p>{preflight.first || "—"} → {preflight.last || "—"}</p>
          {preflight.replay && preflight.replay.mode !== "recorded" && !preflight.replay.influence.trades && <Notice kind="warn">{preflight.replay.influence.note}</Notice>}
          {preflight.fetch && <Notice kind="ok">{preflight.fetch.note} Running starts with this download, then replays.</Notice>}
          {preflight.warnings.map((w: string, i: number) => <Notice kind="warn" key={i}>{w}</Notice>)}
          <div className="table-wrap"><table><thead><tr><th>Symbol</th><th>Sessions</th><th>Prior</th><th>Signals</th></tr></thead><tbody>
            {preflight.coverage.map((s: any) => <tr key={s.symbol}><td>{s.symbol}</td><td>{s.sessions}</td><td>{s.prior_sessions}</td><td>{s.signals}</td></tr>)}
          </tbody></table></div></Card>}
      </aside>
    </div>}

    {tab === "Saved runs" && <>
      <Card title="Experiment history">
        <div className="filters">
          <ViewSwitch view={view} setView={v => { setView(v); setOffset(0); setCompare([]); }} archived={history.data?.archived} />
          <input type="search" aria-label="Search saved runs" placeholder="Search name, strategy, tags or notes" value={search} onChange={e => { setSearch(e.target.value); setOffset(0); }} />
          <select aria-label="Run status" value={status} onChange={e => { setStatus(e.target.value); setOffset(0); }}><option value="">All statuses</option>{["queued", "running", "completed", "failed", "cancelled"].map(s => <option key={s}>{s}</option>)}</select>
          <select aria-label="Run category" value={category} onChange={e => { setCategory(e.target.value); setOffset(0); }}><option value="">All categories</option>{(history.data?.categories ?? []).map((c: string) => <option key={c}>{c}</option>)}<option value="-">Uncategorized</option></select>
          <select aria-label="Strategy" value={strategyFilter} onChange={e => { setStrategyFilter(e.target.value); setOffset(0); }}><option value="">All strategies</option>{catalog.data.strategies.map((s: any) => <option key={s.hash} value={s.hash}>{s.display_name || s.sid} v{s.version}{s.archived ? " (archived)" : ""}</option>)}</select>
          <span className="muted">{history.data?.total ?? 0} saved runs · select 2–4 completed runs to compare</span>
        </div>
        {owner && <BulkBar count={compare.length} categories={history.data?.categories ?? []} busy={!!busy} onClear={() => setCompare([])}
          onCategory={c => organize(compare.map(r => r.id), { category: c })} onArchive={a => { organize(compare.map(r => r.id), { archived: a }); setCompare([]); }} />}
        {history.error && <Notice kind="bad">{history.error}</Notice>}
        <div className="table-wrap mt"><table><thead><tr><th>Select</th><th>Experiment</th><th>Status</th><th>Sessions</th><th>Trades</th><th>Net return</th><th>Drawdown</th><th>Category</th><th>Created</th></tr></thead><tbody>
          {history.data?.rows.map((r: any) => <tr key={r.id} className={[rid === r.id ? "bt-selected" : "", r.archived_at ? "archived" : ""].join(" ")}>
            <td><input type="checkbox" aria-label={`Select run ${r.id}`} checked={compare.some(c => c.id === r.id)}
              onChange={e => setCompare(cs => e.target.checked ? [...cs, r] : cs.filter(c => c.id !== r.id))} /></td>
            <td><button className="bt-link" onClick={() => openRun(r.id)}>#{r.id} · {r.name}</button>{r.archived_at && <span className="tag">archived</span>}<div className="muted">{r.sid} · {short(r.strategy_hash)}</div></td>
            <td><Pill kind={r.status === "completed" ? "good" : r.status === "failed" ? "bad" : "accent"}>{r.status}</Pill>{["running", "queued"].includes(r.status) && <Meter frac={r.progress} />}</td>
            <td>{r.sessions}</td><td>{r.metrics.trades ?? "—"}</td><td className={pnlClass(r.metrics.return_pct)}>{pct(r.metrics.return_pct)}</td><td>{pct(r.metrics.max_drawdown)}</td><td>{r.category ? <span className="tag">{r.category}</span> : <span className="muted">—</span>}</td><td>{wall(r.created_at)}</td>
          </tr>)}
        </tbody></table></div>
        {!history.data?.rows.length && <Empty>{search || status || category || strategyFilter || view !== "active" ? "No saved runs match these filters." : "No runs yet. Configure an experiment and save its first result."}</Empty>}
        <div className="row mt"><button disabled={!offset} onClick={() => setOffset(o => Math.max(o - 40, 0))}>Previous</button><button disabled={offset + 40 >= (history.data?.total || 0)} onClick={() => setOffset(o => o + 40)}>Next</button></div>
      </Card>
      {compare.filter(c => c.status === "completed").length > 1 && <Comparison rows={compare.filter(c => c.status === "completed").slice(0, 4)} />}
      {rid && (detail.loading ? <Loading error={detail.error} /> : detail.data && <RunDetail run={detail.data} owner={owner} restore={restore} cancel={() => action("cancel", async () => { await post(`/api/backtests/${rid}/cancel`); detail.reload(); })} />)}
      {owner && rid && detail.data?.id === rid && !detail.loading && <>
        <Card title="Organize" className="mt"><div className="row">
          <CategoryEditor key={detail.data.category ?? ""} value={detail.data.category} categories={history.data?.categories ?? []} busy={!!busy} onSave={c => organize([rid], { category: c })} />
          {detail.data.archived_at
            ? <button disabled={!!busy} onClick={() => organize([rid], { archived: false })}>Unarchive run</button>
            : <button disabled={!!busy} onClick={() => organize([rid], { archived: true })}>Archive run</button>}
          {detail.data.archived_at && <span className="muted">Archived {wall(detail.data.archived_at)}</span>}
        </div></Card>
        <DeleteZone what={`run (#${rid})`} confirmText={`#${rid}`} busy={!!busy} onDelete={() => removeRun(rid)}
          blockers={["queued", "running"].includes(detail.data.status) ? ["it is still " + detail.data.status + "; cancel it first"] : []}>
          <p className="muted">Removes the saved configuration, frozen strategy/risk snapshots and the report. Runs restored from it keep working but lose the parent link.</p>
        </DeleteZone>
      </>}
      {detail.error && <Notice kind="bad">{detail.error}</Notice>}
    </>}

    {tab === "Research & capabilities" && <>
      <Card title="What makes a backtest reviewable">
        <p>A complete system needs point-in-time data, explicit execution assumptions, risk and cost accounting, validation outside the discovery window, and a permanent record of every trial.</p>
        <div className="table-wrap"><table><thead><tr><th>Capability</th><th>Current implementation</th></tr></thead><tbody>{catalog.data.capabilities.map((c: any) => <tr key={c.feature}><td>{c.feature}</td><td>{c.status}</td></tr>)}</tbody></table></div>
        <p className="muted">The complete feature catalog, metric conventions, repository audit and implementation boundaries are in docs/backtesting-research.md.</p>
      </Card>
      <Card title="Research sources" className="mt"><div className="bt-sources">
        <Source href="https://www.quantconnect.com/docs/v2/writing-algorithms/reality-modeling/key-concepts" title="QuantConnect · reality models" text="Fills, fees, slippage, brokerage and buying-power assumptions." />
        <Source href="https://www.backtrader.com/docu/slippage/slippage/" title="Backtrader · slippage" text="Next-open fills, limits and price-range behavior." />
        <Source href="https://nautilustrader.io/docs/latest/concepts/behavioral_models/" title="NautilusTrader · execution models" text="Separate fill, fee and latency models." />
        <Source href="https://nautilustrader.io/docs/latest/concepts/backtesting/apis-and-runs/" title="NautilusTrader · independent runs" text="Explicit configurations and fresh engine state." />
        <Source href="https://docs.alpaca.markets/us/reference/stockbars" title="Alpaca · historical data" text="Feed, adjustments, symbol mapping and pagination." />
        <Source href="https://www.quantconnect.com/docs/v2/writing-algorithms/historical-data/warm-up-periods" title="QuantConnect · warm-up" text="Initialize indicators with prior data before enabling entries." />
        <Source href="https://www.backtrader.com/docu/analyzers-reference/" title="Backtrader · analyzers" text="Returns, drawdown and risk-adjusted performance conventions." />
        <Source href="https://www.quantconnect.com/docs/v2/local-platform/backtesting/results" title="QuantConnect · reporting" text="Curves, trade records, statistics and saved artifacts." />
        <Source href="https://www.quantconnect.com/docs/v2/writing-algorithms/optimization/walk-forward-optimization" title="QuantConnect · walk-forward" text="Train on past windows; evaluate later observations." />
        <Source href="https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf" title="Bailey & López de Prado · deflated Sharpe" text="Multiple testing, selection bias and non-normal returns." />
      </div></Card>
    </>}
  </>;
}

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return <label className="bt-field"><span>{label}</span>{children}{hint && <small>{hint}</small>}</label>;
}
function Source({ href, title, text }: { href: string; title: string; text: string }) {
  return <div><a href={href} target="_blank" rel="noreferrer">{title} ↗</a><p className="muted">{text}</p></div>;
}
function Comparison({ rows }: { rows: any[] }) {
  const metrics: [string, string, (v: number) => string][] = [["Return", "return_pct", pct], ["P&L", "total_pnl", money], ["Trades", "trades", String], ["Sharpe", "sharpe_daily", num], ["Max drawdown", "max_drawdown", pct], ["Fees", "total_fees", money]];
  return <Card title="Compare saved experiments" className="mt"><div className="table-wrap"><table><thead><tr><th>Metric / configuration</th>{rows.map(r => <th key={r.id}>#{r.id} · {r.name}</th>)}</tr></thead><tbody>
    {metrics.map(([label, key, format]) => <tr key={key}><td>{label}</td>{rows.map(r => <td key={r.id}>{r.metrics[key] == null ? "—" : format(r.metrics[key])}</td>)}</tr>)}
    {["starting_equity", "start_date", "end_date", "symbols", "slippage_bps", "spread_bps", "commission_per_share", "commission_per_order", "latency_bars", "limit_fill", "holdout_pct"].map(key => <tr key={key}><td>{key.replaceAll("_", " ")}</td>{rows.map(r => <td key={r.id}>{Array.isArray(r.config[key]) ? r.config[key].join(", ") : String(r.config[key] ?? "all stored")}</td>)}</tr>)}
  </tbody></table></div><p className="muted">Check dates, universe and assumptions before ranking runs. Different windows are different experiments.</p></Card>;
}

function RunDetail({ run, owner, restore, cancel }: { run: any; owner: boolean; restore: (c: Config, parent?: number) => void; cancel: () => void }) {
  const [tab, setTab] = useState("Overview");
  const [symbol, setSymbol] = useState("");
  const [page, setPage] = useState(0);
  const report = run.report;
  const m = report?.metrics;
  const trades = report?.trades.filter((t: any) => !symbol || t.symbol === symbol) || [];
  return <Card title={<div className="row between"><span>#{run.id} · {run.name}</span><div className="row"><Pill kind={run.status === "completed" ? "good" : "accent"}>{run.status}</Pill>
    <button onClick={() => restore(run.config, run.id)}>Restore configuration</button>{owner && ["queued", "running"].includes(run.status) && <button onClick={cancel}>Cancel run</button>}
    <a className="btn" href={`/api/backtests/${run.id}/export`}>Export JSON</a>{report && <a className="btn" href={`/api/backtests/${run.id}/export?format=csv`}>Trades CSV</a>}
  </div></div>} className="mt">
    <p className="muted">{run.sid} · {run.kind} · {wall(run.created_at)} · {run.created_by}{report && ` · ${report.seconds}s`}</p>
    {run.config.notes && <p>{run.config.notes}</p>}{run.config.tags.map((t: string) => <span className="tag" key={t}>{t}</span>)}
    {["queued", "running"].includes(run.status) && <><Meter frac={run.progress} /><p aria-live="polite">{run.status === "queued" ? "Waiting for a worker" : run.stage && run.stage !== "Replaying" ? run.stage : "Replaying sessions and validation variants"} · {Math.round(run.progress * 100)}%. This run remains saved when you leave the page.</p></>}
    {run.error && <Notice kind="bad">{run.error}</Notice>}
    {report && <>
      {report.warnings?.map((w: string, i: number) => <Notice kind="warn" key={i}>{w}</Notice>)}
      <div className="tabs mt">{["Overview", "Trades", "Diagnostics", "Validation", "Configuration"].map(t => <button key={t} className={tab === t ? "on" : ""} onClick={() => setTab(t)}>{t}</button>)}</div>
      {tab === "Overview" && <>
        <div className="grid g4"><Stat label="Net return" value={pct(m.return_pct)} valueClass={pnlClass(m.return_pct)} sub={`$${money(m.total_pnl, true)} · ${report.days.length} evaluated sessions`} />
          <Stat label="Max daily drawdown" value={pct(m.max_drawdown)} sub={`${m.max_drawdown_sessions} sessions underwater`} />
          <Stat label="Daily Sharpe" value={num(m.sharpe_daily)} sub={`Sortino ${num(m.sortino)}`} />
          <Stat label="Completed trades" value={m.trades} sub={`Win rate ${pct(m.win_rate, 1)} · profit factor ${num(m.profit_factor)}`} /></div>
        <h3>Equity · strategy vs {run.config.benchmark || "cash"}</h3><EquityChart curve={report.equity_curve} benchmark={report.benchmark_curve || []} start={report.starting_equity} />
        <div className="row between muted"><span>{report.days[0]}</span><span>{report.days.at(-1)}</span></div>
        {report.equity_curve[0]?.cash != null && <><h3>Allocation · stock vs cash</h3><AllocationChart curve={report.equity_curve} intraday={report.kind === "intraday"} /></>}
        {report.signal_note && <p className="muted">{report.signal_note}</p>}
        {report.model_replay && <ReplaySummary m={report.model_replay} />}
        {report.cycles?.length > 1 && <details><summary>Holding periods ({report.cycles.length})</summary><table>
          <thead><tr><th>Start</th><th>End</th><th className="num">Sessions</th><th>Ended by</th><th className="num">Return</th></tr></thead>
          <tbody>{report.cycles.map((c: any, i: number) => <tr key={i}><td>{c.start}</td><td>{c.end}</td><td className="num">{c.sessions}</td>
            <td>{c.reason.replaceAll("_", " ")}</td><td className={`num ${pnlClass(c.return_pct)}`}>{pct(c.return_pct, 2, true)}</td></tr>)}</tbody>
        </table></details>}
        <h3>Daily drawdown</h3><LineChart values={m.drawdown_curve?.map((p: any) => p.drawdown * 100) || []} baseline={0} height={120} width={1000} />
        <p className="muted">Daily closing equity. Intraday worst marked drawdown: {pct(m.max_intrabar_drawdown)}. Same-bar price paths are unavailable.</p>
        <div className="grid g4 mt"><Stat label="Final equity · USD" value={money(m.final_equity)} /><Stat label="Gross P&L before commissions" value={money(m.gross_pnl)} /><Stat label="Total commissions" value={money(m.total_fees)} /><Stat label="Excess return vs benchmark" value={pct(m.excess_return_pct)} sub={`Benchmark ${pct(m.benchmark_return_pct)}`} />
          <Stat label="Annualized return" value={pct(m.cagr)} sub="Extrapolation; requires 20+ sessions" /><Stat label="Annualized volatility" value={pct(m.annualized_volatility)} /><Stat label="Calmar" value={num(m.calmar)} /><Stat label="Expectancy · USD / trade" value={money(m.expectancy)} /></div>
        <div className="grid g2 mt"><div><h3>Symbol attribution</h3><table><thead><tr><th>Symbol</th><th>Trades</th><th>Net P&L</th><th>Fees</th></tr></thead><tbody>{report.by_symbol?.map((s: any) => <tr key={s.symbol}><td>{s.symbol}</td><td>{s.trades}</td><td className={pnlClass(s.pnl)}>{money(s.pnl)}</td><td>{money(s.fees)}</td></tr>)}</tbody></table></div>
          <div><h3>Monthly returns</h3><table><thead><tr><th>Month</th><th>Net P&L</th><th>Return</th></tr></thead><tbody>{report.monthly?.map((p: any) => <tr key={p.month}><td>{p.month}</td><td>{money(p.pnl)}</td><td className={pnlClass(p.return_pct)}>{pct(p.return_pct)}</td></tr>)}</tbody></table></div></div>
      </>}
      {tab === "Trades" && <>
        <div className="row between"><span>{trades.length} completed trades</span><select aria-label="Filter trades by symbol" value={symbol} onChange={e => { setSymbol(e.target.value); setPage(0); }}><option value="">All symbols</option>{report.symbols.map((s: string) => <option key={s}>{s}</option>)}</select></div>
        <div className="table-wrap"><table><thead><tr><th>Day</th><th>Symbol / side</th><th>Qty</th><th>Entry</th><th>Exit</th><th>Exit reason</th><th>Fees</th><th>Net P&L</th><th>R</th></tr></thead><tbody>{trades.slice(page * 50, (page + 1) * 50).map((t: any, i: number) => <tr key={i}><td>{t.day}</td><td>{t.symbol} · {t.side}</td><td>{t.qty}</td><td>{money(t.entry_price)}</td><td>{money(t.exit_price)}</td><td>{t.exit_kind}</td><td>{money(t.fees)}</td><td className={pnlClass(t.pnl)}>{money(t.pnl, true)}</td><td>{num(t.r_multiple)}</td></tr>)}</tbody></table></div>
        {!trades.length && <Empty>No completed trades. The Diagnostics tab shows what prevented entries or fills.</Empty>}
        <div className="row mt"><button disabled={!page} onClick={() => setPage(p => p - 1)}>Previous</button><span>Page {page + 1}</span><button disabled={(page + 1) * 50 >= trades.length} onClick={() => setPage(p => p + 1)}>Next</button></div>
        <Executions rows={report.orders ?? report.fills ?? []} portfolio={report.kind === "portfolio"} symbol={symbol} />
        <details className="mt"><summary>Decision records · {report.decision_records?.length || 0} examples</summary><pre>{JSON.stringify(report.decision_records, null, 2)}</pre></details>
        {!!report.orders?.length && <details className="mt"><summary>Portfolio order ledger</summary><pre>{JSON.stringify(report.orders, null, 2)}</pre></details>}
      </>}
      {tab === "Diagnostics" && <>
        <div className="grid g3"><CountTable title="Rule / order funnel" counts={report.diagnostics?.funnel} /><CountTable title="Failed conditions" counts={report.diagnostics?.failed_conditions} /><CountTable title="Risk rejections" counts={report.diagnostics?.risk_rejections} /></div>
        <p className="muted">Counts represent observed symbol-bars or order events. A condition may fail on many bars. Zero trades is a result to investigate, not a reason to relax rules automatically.</p>
        <h3>Session coverage</h3><p>{report.diagnostics?.processed_sessions} processed · {report.diagnostics?.skipped_sessions} skipped</p>
        <div className="table-wrap bt-scroll"><table><thead><tr><th>Session</th><th>Symbols</th><th>Aligned bars</th><th>Excluded symbols</th><th>Unmatched bars</th></tr></thead><tbody>{report.coverage?.map((c: any) => <tr key={c.day}><td>{c.day}</td><td>{c.symbols.join(", ")}</td><td>{c.bars}</td><td>{c.excluded.join(", ") || "—"}</td><td>{c.unaligned_bars}</td></tr>)}</tbody></table></div>
        <details className="mt"><summary>Daily performance and raw rejections</summary><pre>{JSON.stringify({ per_day: report.per_day, rejections: report.rejected }, null, 2)}</pre></details>
      </>}
      {tab === "Validation" && <>
        <p className="muted">Each validation variant starts with fresh capital and fixed strategy rules. Rolling windows measure stability; no parameters are trained. Cost stress replays the execution engine.</p>
        <ValidationTable title="Chronological split" rows={report.validation?.holdout?.map((r: any) => ({ label: `${r.label.replaceAll("_", " ")} · ${r.days[0]} → ${r.days.at(-1)}`, ...r.metrics }))} />
        <ValidationTable title="Execution cost stress" rows={report.validation?.cost_stress?.map((r: any) => ({ label: `${r.cost_multiplier}× slippage, spread and commissions`, ...r.metrics }))} />
        <ValidationTable title="Fixed-rule windows" rows={report.validation?.rolling?.map((r: any) => ({ label: `${r.days[0] || "No usable data"} → ${r.days.at(-1) || "—"}`, ...r.metrics }))} />
        {report.bootstrap ? <><h3>Daily-return bootstrap</h3><p className="muted">{report.bootstrap.note} Seed {report.bootstrap.seed} · {report.bootstrap.samples} samples.</p><div className="grid g4"><Stat label="Return · 5th percentile" value={pct(report.bootstrap.return_p05)} /><Stat label="Return · median" value={pct(report.bootstrap.return_p50)} /><Stat label="Return · 95th percentile" value={pct(report.bootstrap.return_p95)} /><Stat label="Resampled loss frequency" value={pct(report.bootstrap.loss_probability)} /></div><p>Drawdown · 5th percentile: {pct(report.bootstrap.drawdown_p05)}</p></> : <Empty>Bootstrap was disabled or the sample was too short.</Empty>}
      </>}
      {tab === "Configuration" && <><div className="grid g2"><div><h3>Saved configuration</h3><pre>{JSON.stringify(run.config, null, 2)}</pre></div><div><h3>Run provenance & metric conventions</h3><pre>{JSON.stringify(report.manifest, null, 2)}</pre></div></div>
        <details className="mt"><summary>Frozen risk snapshot</summary><pre>{JSON.stringify(run.risk_snapshot, null, 2)}</pre></details><details className="mt"><summary>Exact strategy YAML</summary><pre>{run.strategy_text}</pre></details></>}
    </>}
  </Card>;
}
function CountTable({ title, counts }: { title: string; counts?: Record<string, number> }) {
  return <div><h3>{title}</h3><table><tbody>{Object.entries(counts || {}).map(([k, v]) => <tr key={k}><td className="mono">{k.replaceAll("_", " ")}</td><td className="num">{v}</td></tr>)}</tbody></table>{!Object.keys(counts || {}).length && <Empty>No recorded failures.</Empty>}</div>;
}
function ValidationTable({ title, rows }: { title: string; rows?: any[] }) {
  return <><h3>{title}</h3>{rows?.length ? <div className="table-wrap"><table><thead><tr><th>Variant</th><th>Trades</th><th>Return</th><th>Drawdown</th><th>Sharpe</th><th>Fees</th></tr></thead><tbody>{rows.map((r, i) => <tr key={i}><td>{r.label}</td><td>{r.trades}</td><td className={pnlClass(r.return_pct)}>{pct(r.return_pct)}</td><td>{pct(r.max_drawdown)}</td><td>{num(r.sharpe_daily)}</td><td>{money(r.total_fees)}</td></tr>)}</tbody></table></div> : <Empty>Disabled for this experiment.</Empty>}</>;
}
function EquityChart({ curve, benchmark, start }: { curve: any[]; benchmark: any[]; start: number }) {
  const [hover, setHover] = useState<number | null>(null);
  if (!curve.length) return <Empty>No evaluated sessions.</Empty>;
  const values = [start, ...curve.map(p => p.equity), ...benchmark.map(p => p.equity)];
  const low = Math.min(...values), high = Math.max(...values);
  const span = high - low || start * .01;
  const x = (i: number) => 16 + i / Math.max(curve.length - 1, 1) * 928;
  const y = (v: number) => 200 - (v - low) / span * 180;
  const points = (series: any[]) => series.map((p, i) => `${x(i)},${y(p.equity)}`).join(" ");
  return <div><div className="row muted"><span className="bt-legend strategy">Strategy</span><span className="bt-legend benchmark">Benchmark</span>{hover != null && <span>{curve[hover].day} · ${money(curve[hover].equity)}{benchmark[hover] && ` · benchmark $${money(benchmark[hover].equity)}`}</span>}</div>
    <svg viewBox="0 0 1000 230" className="chart bt-equity" role="img" aria-label="Strategy and benchmark equity by evaluated session" onMouseLeave={() => setHover(null)} onMouseMove={e => {
      const rect = e.currentTarget.getBoundingClientRect(); setHover(Math.max(0, Math.min(curve.length - 1, Math.round(((e.clientX - rect.left) / rect.width * 1000 - 16) / 928 * (curve.length - 1)))));
    }}>
      {[0, .5, 1].map(t => <g key={t}><line x1={16} x2={944} y1={20 + t * 180} y2={20 + t * 180} className="bt-grid" /><text x={950} y={24 + t * 180} className="axis">{money(high - t * span)}</text></g>)}
      <line x1={16} x2={944} y1={y(start)} y2={y(start)} className="lvl lvl-base" />
      {benchmark.length > 0 && <polyline fill="none" points={points(benchmark)} className="bt-benchmark" />}
      <polyline fill="none" points={points(curve)} className="series" />
      {hover != null && <><line x1={x(hover)} x2={x(hover)} y1={16} y2={210} className="lvl lvl-base" /><circle cx={x(hover)} cy={y(curve[hover].equity)} r={4} fill="var(--accent)" /></>}
    </svg></div>;
}

function AllocationChart({ curve, intraday }: { curve: any[]; intraday: boolean }) {
  const [hover, setHover] = useState<number | null>(null);
  // Intraday positions are flat by the close, so plot each session's peak holding instead.
  const rows = curve.map(p => {
    const stock = Math.max(0, intraday ? p.peak_stock ?? 0 : p.stock), cash = p.equity - stock;
    return { day: p.day, stock, cash, frac: p.equity > 0 ? Math.min(1, stock / p.equity) : 0 };
  });
  const x = (i: number) => 16 + i / Math.max(rows.length - 1, 1) * 928;
  const y = (f: number) => 20 + (1 - f) * 160;
  const top = rows.map((r, i) => `${x(i)},${y(r.frac)}`).join(" ");
  const h = hover != null ? rows[hover] : null;
  const avg = rows.reduce((a, r) => a + r.frac, 0) / rows.length;
  return <div><div className="row muted"><span className="bt-legend stock">{intraday ? "Peak stock in session" : "Stock"}</span><span className="bt-legend cash">Cash</span>
    {h ? <span>{h.day} · stock ${money(h.stock)} ({pct(h.frac, 1)}) · cash ${money(h.cash)} ({pct(1 - h.frac, 1)})</span> : <span>Average in stock {pct(avg, 1)}</span>}</div>
    <svg viewBox="0 0 1000 200" className="chart bt-alloc" role="img" aria-label="Share of equity in stock versus cash by evaluated session" onMouseLeave={() => setHover(null)} onMouseMove={e => {
      const rect = e.currentTarget.getBoundingClientRect(); setHover(Math.max(0, Math.min(rows.length - 1, Math.round(((e.clientX - rect.left) / rect.width * 1000 - 16) / 928 * (rows.length - 1)))));
    }}>
      <polygon points={`${x(0)},${y(1)} ${x(rows.length - 1)},${y(1)} ${x(rows.length - 1)},${y(0)} ${x(0)},${y(0)}`} className="bt-cash" />
      <polygon points={`${x(0)},${y(0)} ${top} ${x(rows.length - 1)},${y(0)}`} className="bt-stock" />
      {[0, .5, 1].map(t => <g key={t}><line x1={16} x2={944} y1={y(t)} y2={y(t)} className="bt-grid" /><text x={950} y={y(t) + 4} className="axis">{pct(t, 0)}</text></g>)}
      {h && <><line x1={x(hover!)} x2={x(hover!)} y1={16} y2={184} className="lvl lvl-base" /><circle cx={x(hover!)} cy={y(h.frac)} r={4} fill="var(--c1)" /></>}
    </svg></div>;
}

function Executions({ rows, portfolio, symbol }: { rows: any[]; portfolio: boolean; symbol: string }) {
  const [page, setPage] = useState(0);
  useEffect(() => setPage(0), [symbol]);
  const shown = rows.filter(r => r.allocated_pct !== undefined && (!symbol || r.symbol === symbol));
  if (!shown.length) return null;
  return <details className="mt" open><summary>Executions · {shown.length} fills with allocation and cash</summary>
    <p className="muted">Deployed is this fill&apos;s notional and Allocated is everything held right after it, both as a share of {portfolio ? "the strategy's sleeve capital" : "account equity at the fill"}. Cash is the account balance after the fill.</p>
    <div className="table-wrap bt-scroll"><table><thead><tr><th>Day</th><th>Symbol / side</th><th className="num">Qty</th><th className="num">Price</th><th className="num">Notional</th><th className="num">Deployed</th><th className="num">Allocated after</th><th className="num">Stock after</th><th className="num">Cash after</th><th>{portfolio ? "Reason" : "Kind"}</th></tr></thead>
      <tbody>{shown.slice(page * 100, (page + 1) * 100).map((r, i) => <tr key={i}><td>{r.day}{!portfolio && r.ts ? ` ${et(r.ts)}` : ""}</td><td>{r.symbol} · {r.side}</td><td className="num">{r.qty}</td><td className="num">{money(r.price)}</td><td className="num">{money(r.notional)}</td>
        <td className="num">{pct(r.deployed_pct, 1)}</td><td className="num">{pct(r.allocated_pct, 1)}</td><td className="num">{money(r.invested_after)}</td><td className="num">{money(r.cash_after)}</td><td>{String(r.reason ?? r.kind ?? "").replaceAll("_", " ")}</td></tr>)}</tbody></table></div>
    {shown.length > 100 && <div className="row mt"><button disabled={!page} onClick={() => setPage(p => p - 1)}>Previous</button><span>Page {page + 1} of {Math.ceil(shown.length / 100)}</span><button disabled={(page + 1) * 100 >= shown.length} onClick={() => setPage(p => p + 1)}>Next</button></div>}
  </details>;
}

function ReplayEstimate({ r, confirmed, onConfirm }: { r: any; confirmed: boolean; onConfirm: (v: boolean) => void }) {
  const inf = r.influence;
  return <>
    <Notice kind={inf.trades ? "ok" : "warn"}>{inf.trades ? "Can change trades: " : "Won't change trades: "}{inf.note}</Notice>
    {(inf.news || inf.assumptions) && <table><tbody>
      {inf.news && <tr><td>News stories in the window</td><td className="num">{r.stories}{r.news_estimated ? " (estimated; not downloaded yet)" : ""}{r.cached ? ` · ${r.cached} already assessed` : ""}</td></tr>}
      {inf.assumptions && r.mode === "models" && <tr><td>Assumption assessments</td><td className="num">{r.assumption_sessions} sessions</td></tr>}
      {r.mode === "models" && <tr><td>Estimated Claude cost</td><td className="num"><b>${r.claude_usd.toFixed(2)}</b>{r.providers.laya ? " (T1 on local Laya: free)" : " (T1 on Claude Haiku: Laya isn't running)"}</td></tr>}
      {r.mode === "offline" && <tr><td>Estimated cost</td><td className="num">$0.00 (offline model)</td></tr>}
    </tbody></table>}
    {r.mode === "models" && !r.providers.available && <Notice kind="bad">The models service isn&apos;t running, so live-model replay can&apos;t start.</Notice>}
    {r.mode === "models" && !r.providers.claude && r.providers.available && <Notice kind="warn">No Claude API key on the models service: escalations fall back to the offline model.</Notice>}
    {r.over_budget && <Notice kind="warn">The estimate is above your budget: paid calls stop at the budget and the rest of the window uses the offline model.</Notice>}
    {r.high_cost && <Notice kind="warn">
      <label className="row"><input type="checkbox" checked={confirmed} onChange={e => onConfirm(e.target.checked)} />
        I accept an estimated ${r.claude_usd.toFixed(2)} of Claude usage for this run (capped at the budget).</label>
    </Notice>}
    {r.mode === "models" && <p className="muted">Paid answers aren&apos;t deterministic, and models trained after these dates may reflect hindsight even with point-in-time evidence.</p>}
  </>;
}


function ReplaySummary({ m }: { m: any }) {
  const res = m.result ?? {};
  const statuses: Record<string, number> = {};
  (m.assumption_timeline ?? []).forEach((t: any) => { statuses[t.status] = (statuses[t.status] ?? 0) + 1; });
  return <Notice kind={m.skipped ? "warn" : "ok"}>
    <b>Model replay: {m.mode === "models" ? "live models" : "offline model"}.</b>{" "}
    {m.skipped ? m.skipped : <>
      {res.news?.stories !== undefined && <>{res.news.stories} news stories ({res.news.kept} kept as signals, {res.news.dropped} dropped, {res.news.cached} from cache). </>}
      {m.assumption_timeline && <>Assumption assessments: {Object.entries(statuses).map(([k, n]) => `${k} ${n}`).join(", ") || "none"}{m.blocked_sessions?.length ? `; buys blocked on ${m.blocked_sessions.length} sessions` : ""}. </>}
      {res.cost_usd !== undefined && <>Spent ${Number(res.cost_usd).toFixed(2)} of ${Number(m.budget_usd).toFixed(2)}{res.fallback ? `; ${res.fallback} items used the offline model after the budget` : ""}. </>}
      {m.news_note && <>{m.news_note} </>}
      <span className="muted">{m.influence?.note} {m.caveat}</span>
    </>}
  </Notice>;
}

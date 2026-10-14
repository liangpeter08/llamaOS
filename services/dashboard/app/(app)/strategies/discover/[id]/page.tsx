"use client";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { post } from "@/lib/api";
import { Fragment, useMemo, useState } from "react";
import { usePoll } from "@/lib/hooks";
import { cls, num, pct, short, wall } from "@/lib/format";
import { Card, Empty, Loading, Meter, Notice, Pill, Stat, StatusPill } from "@/components/ui";
import { isOwner, useSession } from "@/components/session";
import { DEPTHS, GATE, RunStatus, WEAKNESS as WEAKNESS_LABEL, Weaknesses, useStartRun, type Depth } from "@/components/Discovery";
import { useT } from "@/lib/i18n";

export default function DiscoveryRun() {
  const { id } = useParams<{ id: string }>();
  const { data, error } = usePoll<any>(`/api/discovery/${id}`, 2500);
  const { me } = useSession();
  const t = useT();
  if (!data) return <Loading error={error} />;
  const { run, trials } = data;
  const rep = run.report || {};
  const cfg = run.config || {};
  const owner = isOwner(me);
  const kinds: string[] = Object.keys(rep.kinds || {}).length ? Object.keys(rep.kinds) : cfg.kinds || [];

  return (
    <>
      <p className="muted" style={{ margin: "0 0 6px" }}><Link href="/strategies">← {t("Strategies")}</Link></p>
      <div className="row between">
        <div>
          <h1>{t("discovery run #{id}", { id: run.id })} · {run.ticker}</h1>
          <div className="row">
            <RunStatus status={run.status} />
            {rep.generator && <Pill>{rep.generator}</Pill>}
            {cfg.parent_run_id && <Link href={`/strategies/discover/${cfg.parent_run_id}`}><Pill kind="accent">{t("follow-up of run #{id}", { id: cfg.parent_run_id })}</Pill></Link>}
            {data.follow_ups.map((f: any) => <Link key={f.id} href={`/strategies/discover/${f.id}`}><Pill>{t("follow-up #{id}", { id: f.id })}</Pill></Link>)}
            <span className="muted">{t("started {when} by {who}", { when: wall(run.created_at), who: run.created_by })}{run.finished_at && <> · {t("finished {when}", { when: wall(run.finished_at) })}</>}</span>
          </div>
        </div>
      </div>
      {run.status === "running" && (
        <Card className="mt"><b>{run.stage ? t(run.stage) : t("Starting")}</b><Meter frac={run.progress} />
          <p className="muted" style={{ fontSize: 12, marginBottom: 0 }}>{t("{n} trials so far. This page updates on its own.", { n: trials.length })}</p></Card>
      )}
      {run.status === "failed" && <Notice kind="bad"><b>{t("Run failed:")}</b> {run.error}</Notice>}
      {(rep.seeds || []).length > 0 && (
        <Notice><details><summary style={{ color: "inherit" }}>{t(rep.seeds.length > 1 ? "Iterating on {n} candidates from earlier work." : "Iterating on {n} candidate from earlier work.", { n: rep.seeds.length })}{" "}
          {t("Unchanged seeds are not re-scored on the holdout; only revisions that beat them are.")}</summary>
          <ul className="disc-weak">{rep.seeds.map((x: string) => <li key={x}>{x.replace(/^seed: /, "")}</li>)}</ul></details></Notice>
      )}
      {Object.entries(rep.skipped || {}).map(([k, why]) => <Notice key={k} kind="warn">{t(k)}: {t("skipped.")} {String(why)}</Notice>)}
      {(rep.model_errors || []).length > 0 && <Notice kind="warn">{t("{n} model call(s) failed and fell back to templates:", { n: rep.model_errors.length })} {rep.model_errors[0]}</Notice>}

      {kinds.map((k) => <KindSection key={k} kind={k} r={rep.kinds?.[k]} trials={trials.filter((tr: any) => tr.kind === k)}
        ticker={run.ticker} owner={owner} drafts={(rep.drafts || []).filter((d: any) => d.kind === k)} />)}
      <Method />
    </>
  );
}

function KindSection({ kind, r, trials, ticker, owner, drafts }: { kind: string; r: any; trials: any[]; ticker: string; owner: boolean; drafts: any[] }) {
  const pbo = r?.pbo?.pbo;
  const t = useT();
  return (
    <section className="mt">
      <h2 style={{ fontSize: 17, marginTop: 18 }}>{t(kind === "daily" ? "Daily strategies (held overnight)" : "Intraday strategies")}</h2>
      {r && (
        <div className="grid g4">
          <Stat label={t("Trials")} value={r.trials} sub={t("{e} evaluated · {i} improving revisions", { e: r.evaluated, i: r.improved }) + (r.prior_trials ? ` · ${t("+{n} in earlier runs", { n: r.prior_trials })}` : "")} />
          <Stat label={t("Passed discovery gates")} value={r.passed_gates} sub={t("{n} sessions, {from} → {to}", { n: r.discovery_sessions[2], from: r.discovery_sessions[0], to: r.discovery_sessions[1] })} />
          <Stat label={t("Holdout")} value={t("{n} of {total} passed", { n: r.finalists.filter((f: any) => f.passed).length, total: r.finalists.length })}
            sub={t("{n} sealed sessions, {from} → {to}", { n: r.holdout_sessions[2], from: r.holdout_sessions[0], to: r.holdout_sessions[1] })}
            valueClass={r.finalists.some((f: any) => f.passed) ? "pos" : ""} />
          <Stat label={t("Probability of overfitting")} value={pbo == null ? t("n/a") : pct(pbo, 0)} valueClass={pbo == null ? "" : pbo > 0.5 ? "neg" : pbo > 0.25 ? "" : "pos"}
            sub={t(pbo == null ? "needs more trials or sessions" : pbo > 0.5 ? "the in-sample best usually loses out of sample" : "share of splits where the best candidate falls below the median")} />
        </div>
      )}
      {r?.holdout_prior_looks > 0 && (
        <Notice kind="warn">{t("This holdout was already scored {n} time(s) in earlier runs. It is no longer unseen: the deflated Sharpe counts all {total} trials, and paper trading is the real test.", { n: r.holdout_prior_looks, total: r.prior_trials + r.trials })}</Notice>
      )}
      {r && <Finalists r={r} ticker={ticker} owner={owner} drafts={drafts} followUp={r.prior_trials > 0}
        onlySeeds={trials.length > 0 && trials.every((tr) => tr.origin === "seed")} />}
      <Leaderboard trials={trials} ticker={ticker} owner={owner} kind={kind} />
    </section>
  );
}

function Finalists({ r, ticker, owner, drafts, followUp, onlySeeds }: { r: any; ticker: string; owner: boolean; drafts: any[]; followUp: boolean; onlySeeds: boolean }) {
  const { start, busy, error } = useStartRun();
  const t = useT();
  if (!r.finalists.length) {
    return <Card className="mt"><Empty>{t(onlySeeds ? "Nothing to revise: code found no weakness or losing regime to work on in the seed, so it stays as it was. With Kimi enabled, the bot can still propose alternatives."
      : followUp ? "No revision passed every gate on the discovery sessions, so the seeds stay as they were and nothing was re-scored on the holdout."
      : "No candidate passed every gate on the discovery sessions, so nothing was scored on the holdout.")}
      {r.best && <> {t("Best attempt: {name} (score {score}; weaknesses: {w}). Use {iterate} on a promising candidate below.", {
        name: <b>#{r.best.trial_id} {r.best.title}</b>, score: num(r.best.score), w: r.best.weaknesses.join(", ") || t("none"), iterate: <i>{t("Iterate further")}</i> })}</>}</Empty></Card>;
  }
  return (
    <>
      {error && <Notice kind="bad">{error}</Notice>}
      <div className="grid g2 mt">
        {r.finalists.map((f: any) => {
          const d = f.discovery, h = f.holdout, draft = drafts.find((x) => x.hash === f.strategy_hash) || (f.strategy_hash && { hash: f.strategy_hash });
          const dsr = h.dsr?.dsr;
          return (
            <Card key={f.trial_id} className={cls("disc-finalist", f.passed ? "pass" : "fail")}>
              <div className="row between">
                <b>#{f.trial_id} {f.title}</b>
                <Pill kind={f.passed ? "good" : "bad"}>{t(f.passed ? "holdout pass" : "holdout fail")}</Pill>
              </div>
              <span className="tag">{f.family}</span>
              <table className="disc-compare">
                <thead><tr><th></th><th className="num">{t("Discovery")}</th><th className="num">{t("Sealed holdout")}</th></tr></thead>
                <tbody>
                  <tr><td>{t("Sharpe")}</td><td className="num">{num(d.sharpe)}</td><td className={cls("num", h.sharpe > 0 ? "pos" : "neg")}>{num(h.sharpe)}</td></tr>
                  <tr><td>{t("Sharpe at 3x costs")}</td><td className="num">{num(d.stress_sharpe)}</td><td className="num">{num(h.stress_sharpe)}</td></tr>
                  <tr><td>{t(d.baseline_sharpe ? "Buy-and-hold Sharpe" : "Trades")}</td>
                    <td className="num">{d.baseline_sharpe ? num(d.baseline_sharpe) : d.trades}</td><td className="num">{d.baseline_sharpe ? num(h.baseline_sharpe) : h.trades}</td></tr>
                  <tr><td>{t("Return · max drawdown")}</td><td className="num">{pct(d.return_pct, 2, true)} · {pct(d.max_drawdown, 1)}</td>
                    <td className="num">{pct(h.return_pct, 2, true)} · {pct(h.max_drawdown, 1)}</td></tr>
                </tbody>
              </table>
              <p className="muted" style={{ fontSize: 12 }}>
                {t("Deflated Sharpe {v} (chance the edge is real after {n} trials; 0.95+ is convincing)", { v: dsr == null ? t("n/a") : <b className={dsr >= 0.95 ? "pos" : ""}>{num(dsr)}</b>, n: h.dsr?.n_trials })}
                {!f.passed && h.checks && <> · {t("failed:")} {Object.entries(h.checks).filter(([, v]) => !v).map(([k]) => t(GATE[k] ?? k)).join(", ")}</>}
              </p>
              {f.lineage.length > 1 && (
                <details><summary>{t(f.lineage.length > 2 ? "How the bot got here ({n} revisions)" : "How the bot got here ({n} revision)", { n: f.lineage.length - 1 })}</summary>
                  <ol className="disc-lineage">{f.lineage.map((s: string, i: number) => <li key={i}>{s}</li>)}</ol></details>
              )}
              <div className="row" style={{ marginTop: 8 }}>
                {draft && <Link className="btn primary" href={`/strategies/${draft.hash}`}>{t("Open draft")}</Link>}
                {owner && <button disabled={busy} onClick={() => start({ ticker, seed_trials: [f.trial_id], generations: DEPTHS.quick.generations, max_trials: DEPTHS.quick.max_trials })}>{t("Iterate further")}</button>}
                <TrialToggle id={f.trial_id} label={t("File & diagnostics")} />
              </div>
            </Card>
          );
        })}
      </div>
    </>
  );
}

const ORDER = ["holdout_pass", "holdout_fail", "improved", "evaluated", "not_better", "invalid", "duplicate", "pending"];

function Leaderboard({ trials, ticker, owner, kind }: { trials: any[]; ticker: string; owner: boolean; kind: string }) {
  const [sel, setSel] = useState<Set<number>>(new Set());
  const [only, setOnly] = useState(false);
  const [open, setOpen] = useState<number | null>(null);
  const [depth, setDepth] = useState<Depth>("quick");
  const { start, busy, error } = useStartRun();
  const t = useT();
  const rows = useMemo(() => [...trials]
    .filter((tr) => !only || tr.promising)
    .sort((a, b) => (b.score ?? -1e9) - (a.score ?? -1e9) || ORDER.indexOf(a.status) - ORDER.indexOf(b.status)), [trials, only]);
  const promising = trials.filter((tr) => tr.promising);
  const toggle = (id: number) => setSel((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.size < 5 && n.add(id); return n; });
  if (!trials.length) return null;
  return (
    <Card className="mt" title={<>{t("Candidates")} <span className="muted" style={{ fontWeight: 400 }}>· {t("{n} trials, best score first; ★ = worth investigating (passed every gate, or missed one with a positive score)", { n: trials.length })}</span></>}>
      {error && <Notice kind="bad">{error}</Notice>}
      <div className="filters">
        <label className="row" style={{ gap: 4 }}><input type="checkbox" checked={only} onChange={(e) => setOnly(e.target.checked)} /> {t("Only worth investigating ({n})", { n: promising.length })}</label>
        {owner && (
          <span className="row disc-iterate">
            <button className="primary" disabled={busy || !sel.size} onClick={() => start({ ticker, kinds: [kind], seed_trials: [...sel],
              generations: DEPTHS[depth].generations, max_trials: DEPTHS[depth].max_trials })}>
              {busy ? t("Starting…") : t("Iterate selected ({n})", { n: sel.size })}</button>
            <div className="seg">{(Object.keys(DEPTHS) as Depth[]).map((x) => <button type="button" key={x} className={cls(depth === x && "on")} onClick={() => setDepth(x)}>{t(DEPTHS[x].label)}</button>)}</div>
            {promising.length > 0 && <button type="button" className="link" onClick={() => setSel(new Set(promising.sort((a, b) => b.score - a.score).slice(0, 5).map((tr) => tr.id)))}>
              {t("select the top {n} worth investigating", { n: Math.min(5, promising.length) })}</button>}
          </span>
        )}
      </div>
      <div className="table-wrap"><table className="disc-board">
        <thead><tr>
          {owner && <th aria-label={t("Select")} />}<th>{t("Candidate")}</th><th>{t("Gen")}</th><th>{t("Status")}</th><th className="num">{t("Score")}</th><th className="num">{t("Sharpe")}</th>
          <th className="num">{t("3x costs")}</th><th className="num">{t("Trades")}</th><th>{t("Gates")}</th><th>{t("Weaknesses")}</th><th>{t("Change")}</th><th />
        </tr></thead>
        <tbody>{rows.map((tr) => {
          const s = tr.summary || {};
          return (
            <Fragment key={tr.id}>
              <tr className={cls(tr.status === "invalid" || tr.status === "duplicate" ? "dim" : "", sel.has(tr.id) && "bt-selected")}>
                {owner && <td><input type="checkbox" aria-label={t("Select trial {id}", { id: tr.id })} disabled={!s.gates || (!sel.has(tr.id) && sel.size >= 5)}
                  checked={sel.has(tr.id)} onChange={() => toggle(tr.id)} /></td>}
                <td className="disc-cand">{tr.promising && <span className="pos" title={t("worth investigating")}>★ </span>}<b>#{tr.id}</b> {tr.title}
                  <div className="muted" style={{ fontSize: 12 }}>{tr.family} · {t(tr.origin)}{tr.parent_id && <> · {t("from #{id}", { id: tr.parent_id })}</>}</div></td>
                <td>{tr.generation}</td>
                <td><StatusPill status={tr.status.replace("_", " ")} />{tr.strategy_hash && <> <Link href={`/strategies/${tr.strategy_hash}`}><span className="tag">{t("draft")}</span></Link></>}</td>
                <td className="num">{num(tr.score)}</td><td className="num">{num(s.sharpe)}</td><td className="num">{num(s.stress_sharpe)}</td><td className="num">{s.trades ?? "—"}</td>
                <td>{s.gates ? (s.gates.passed ? <span className="pos">{t("all ✓")}</span>
                  : <span className="neg" style={{ fontSize: 12 }}>{s.failed_gates.map((g: string) => t(GATE[g] ?? g)).join(", ")}</span>)
                  : <span className="neg" style={{ fontSize: 12 }}>{(s.errors || [])[0]?.slice(0, 60) || "—"}</span>}</td>
                <td><Weaknesses items={s.weaknesses || []} /></td>
                <td className="muted disc-change" title={tr.change || ""}>{tr.change || "—"}</td>
                <td className="row" style={{ gap: 6, flexWrap: "nowrap" }}>
                  <button type="button" className="link" onClick={() => setOpen(open === tr.id ? null : tr.id)}>{t(open === tr.id ? "Hide" : "Inspect")}</button>
                  {owner && <SaveTrial t={tr} />}</td>
              </tr>
              {open === tr.id && <tr><td colSpan={owner ? 12 : 11}><TrialDetail id={tr.id} /></td></tr>}
            </Fragment>
          );
        })}</tbody>
      </table></div>
    </Card>
  );
}

const SAVABLE = ["evaluated", "improved", "not_better", "holdout_pass", "holdout_fail"];

// Save any evaluated trial as a draft strategy, then open it (backtest and promote it from the strategy page).
function SaveTrial({ t: trial }: { t: any }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const t = useT();
  if (trial.strategy_hash) return <Link href={`/strategies/${trial.strategy_hash}`}>{t("Open strategy")}</Link>;
  if (!SAVABLE.includes(trial.status)) return null;
  return (
    <>
      <button type="button" className="link" disabled={busy} title={err || t("Save as a new draft strategy you can backtest and promote")}
        onClick={async () => {
          setBusy(true);
          setErr(null);
          try {
            const r = await post<any>(`/api/discovery/trials/${trial.id}/save`, {});
            router.push(`/strategies/${r.hash}`);
          } catch (e) {
            setErr((e as Error).message);
            setBusy(false);
          }
        }}>{t(busy ? "Saving…" : "Save as strategy")}</button>
      {err && <span className="neg" style={{ fontSize: 12 }}>{err}</span>}
    </>
  );
}

function TrialToggle({ id, label }: { id: number; label: string }) {
  const [open, setOpen] = useState(false);
  const t = useT();
  return <>
    <button type="button" className="link" onClick={() => setOpen(!open)}>{open ? t("Hide") : label}</button>
    {open && <div style={{ width: "100%" }}><TrialDetail id={id} /></div>}
  </>;
}

function TrialDetail({ id }: { id: number }) {
  const { data, error } = usePoll<any>(`/api/discovery/trials/${id}`);
  const t = useT();
  if (!data) return <Loading error={error} />;
  const ev = data.evaluation || {};
  const hyp = data.hypothesis || {};
  return (
    <div className="disc-detail">
      <div className="grid g2">
        <div>
          {hyp.mechanism && <><h3>{t("Mechanism")}</h3><p style={{ margin: 0 }}>{hyp.mechanism}</p></>}
          {hyp.falsify && <><h3>{t("Would be falsified by")}</h3><p style={{ margin: 0 }} className="muted">{hyp.falsify}</p></>}
          {hyp.why && <><h3>{t("Why this revision")}</h3><p style={{ margin: 0 }} className="muted">{hyp.why}</p></>}
          <h3>{t("Weaknesses found (discovery sessions)")}</h3>
          {(ev.weaknesses || []).length ? <ul className="disc-weak">{ev.weaknesses.map((w: any, i: number) => (
            <li key={i}><b>{t(WEAKNESS_LABEL[w.kind] ?? w.kind.replaceAll("_", " "))}</b>: {w.detail}{w.fix ? <span className="muted"> → {t("next revision:")} {w.fix.op.replace("_", " ")}{w.fix.param ? ` ${w.fix.param}` : ""}{w.fix.bucket ? ` (${w.fix.bucket})` : ""}</span> : null}</li>
          ))}</ul> : <Empty>{ev.ok === false ? (ev.errors || []).join("; ") : t("None found.")}</Empty>}
          {ev.folds !== undefined || ev.base?.folds ? <>
            <h3>{t("Folds")}</h3>
            <div className="row">{(ev.base?.folds || []).map((f: any) => <span key={f.from} className={cls("tag", f.return > 0 ? "pos" : "neg")} title={t("from {from}, {n} sessions", { from: f.from, n: f.sessions })}>
              {pct(f.return, 2, true)} · {t("Sharpe")} {num(f.sharpe, 1)}</span>)}</div></> : null}
          {ev.regimes && <><h3>{t("Where it makes and loses money")}</h3>
            <table><tbody>{["vol", "trend", "market"].filter((k) => ev.regimes[k]).map((k) => Object.entries(ev.regimes[k]).map(([b, st]: any) => (
              <tr key={b}><td>{t(b.replaceAll("_", " "))}</td><td className={cls("num", st.mean > 0 ? "pos" : "neg")}>{pct(st.mean, 3, true)} {t("/ session")}</td>
                <td className="num muted">n {st.n}</td><td className="num muted">t {num(st.t, 1)}</td></tr>)))}</tbody></table></>}
          {(ev.ablation || []).length > 0 && <><h3>{t("Ablation (remove one part)")}</h3>
            <table><tbody>{ev.ablation.map((a: any, i: number) => <tr key={i}><td><code>{a.src}</code></td>
              <td className={cls("num", a.delta >= -0.1 ? "neg" : "pos")}>{t("Sharpe")} {num(a.delta, 2, true)}</td><td className="muted">{t(a.delta >= -0.1 ? "doesn't earn its place" : "earns its place")}</td></tr>)}</tbody></table></>}
        </div>
        <div>
          <h3>{t("What the rules do")}</h3>
          <pre style={{ maxHeight: 260 }}>{data.back_translation || t("n/a: the file does not validate")}</pre>
          <h3>{t("File")} <span className="mono" style={{ textTransform: "none" }}>sha256 {short(data.hash)}…</span></h3>
          <pre style={{ maxHeight: 320 }}>{data.yaml}</pre>
        </div>
      </div>
    </div>
  );
}

const METHOD = [
  "The newest 25% of stored sessions are sealed before anything runs. Profiles, backtests and revisions only see the rest.",
  "Every candidate is replayed through the same engine as paper trading, at normal and triple costs, and split into folds.",
  "Weaknesses come from measurements: conditions whose removal doesn't hurt, fragile parameters, regimes where it loses, cost sensitivity.",
  "A revision counts only if it beats its parent after a complexity penalty. The loop stops after two generations without progress.",
  "At most two finalists per kind are scored once on the sealed sessions. Every trial, including failures, counts in the deflated Sharpe.",
  "Survivors become drafts with their evidence in the file. Promotion still needs your backtest review, paper trading and approval.",
];

function Method() {
  const t = useT();
  return (
    <details className="mt">
      <summary>{t("How discovery decides (and why it is hard to fool)")}</summary>
      <ul className="muted">{METHOD.map((m) => <li key={m}>{t(m)}</li>)}</ul>
    </details>
  );
}

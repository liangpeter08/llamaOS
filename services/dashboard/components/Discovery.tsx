"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { get, post } from "@/lib/api";
import { usePoll } from "@/lib/hooks";
import { cls, num, wall } from "@/lib/format";
import { Card, Meter, Notice, Pill, StatusPill } from "./ui";
import { useT } from "@/lib/i18n";

// Search depth presets: generations of revision and the per-kind trial budget.
export const DEPTHS = {
  quick: { label: "Quick", generations: 2, max_trials: 30, hint: "2 generations, up to 30 trials per kind" },
  standard: { label: "Standard", generations: 4, max_trials: 60, hint: "4 generations, up to 60 trials per kind" },
  deep: { label: "Deep", generations: 6, max_trials: 120, hint: "6 generations, up to 120 trials per kind (more trials = a higher bar)" },
} as const;
export type Depth = keyof typeof DEPTHS;

export const WEAKNESS: Record<string, string> = {
  missing_data: "missing data", too_few_trades: "too few trades", never_acts: "never acts", dead_condition: "dead condition",
  regime_drag: "loses in a regime", fragile_param: "fragile parameter", stop_heavy: "stopped out often",
  cost_sensitive: "costs eat the edge", inconsistent: "inconsistent across folds", exploration: "exploration",
};
export const GATE: Record<string, string> = {
  consistent: "consistent folds", survives_costs: "survives 3x costs", enough_trades: "enough trades", positive_edge: "positive edge",
  acts: "rules act", beats_holding: "beats holding", profitable: "profitable",
};

export function Weaknesses({ items }: { items: string[] }) {
  const t = useT();
  if (!items?.length) return <span className="muted">{t("none")}</span>;
  const counts = items.reduce<Record<string, number>>((m, w) => ({ ...m, [w]: (m[w] ?? 0) + 1 }), {});
  return <>{Object.entries(counts).map(([w, n]) => <span key={w} className="tag">{t(WEAKNESS[w] ?? w)}{n > 1 ? ` ×${n}` : ""}</span>)}</>;
}

export function RunStatus({ status }: { status: string }) {
  const t = useT();
  return <Pill kind={status === "completed" ? "good" : status === "failed" ? "bad" : "accent"}>{t(status)}</Pill>;
}

/** Start a run (fresh search or follow-up) and open its page. */
export function useStartRun() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function start(body: object) {
    setBusy(true);
    setError(null);
    try {
      const r = await post<any>("/api/discovery", body);
      router.push(`/strategies/discover/${r.run_id}`);
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }
  return { start, busy, error };
}

function Preflight({ ticker, peers, onData }: { ticker: string; peers: string; onData: (d: any) => void }) {
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const t = useT();
  useEffect(() => {
    if (!ticker.trim()) { setData(null); onData(null); return; }
    const id = setTimeout(async () => {
      try {
        const d = await get(`/api/discovery/preflight?ticker=${encodeURIComponent(ticker)}&peers=${encodeURIComponent(peers)}`);
        setData(d); setError(null); onData(d);
      } catch (e) {
        setData(null); setError((e as Error).message); onData(null);
      }
    }, 350);
    return () => clearTimeout(id);
  }, [ticker, peers]); // eslint-disable-line react-hooks/exhaustive-deps
  if (error) return <p className="neg disc-preflight">{error}</p>;
  if (!data) return <p className="muted disc-preflight">{t("Enter a ticker to see what stored data a run can use.")}</p>;
  return (
    <div className="disc-preflight">
      {(["daily", "intraday"] as const).map((k) => {
        const x = data.kinds[k];
        return x?.ok
          ? <Pill key={k} kind="good">{t(k)}: {t("{n} sessions to search + {h} sealed", { n: x.discovery, h: x.holdout })}</Pill>
          : <Pill key={k} kind="warn" title={x?.why}>{t(k)}: {t("not available")}</Pill>;
      })}
      {data.peers.map((p: any) => (
        <Pill key={p.symbol} kind={p.daily || p.intraday ? "" : "warn"} title={t("{d} daily sessions, {i} days of 1-minute bars", { d: p.daily, i: p.intraday })}>
          {t("peer {sym}", { sym: p.symbol })}{p.daily || p.intraday ? "" : `: ${t("no data")}`}
        </Pill>
      ))}
      {Object.values(data.kinds).some((x: any) => !x.ok) && (
        <div className="muted" style={{ marginTop: 4, fontSize: 12 }}>
          {Object.entries(data.kinds).filter(([, x]: any) => !x.ok).map(([k, x]: any) => `${t(k)}: ${x.why}`).join(" · ")}
          {data.history_download ? ` ${t("The paper broker can download missing history from a backtest of this ticker.")}` : ""}
        </div>
      )}
    </div>
  );
}

/** "Generate new strategies": the form on the Strategies page. */
export function DiscoverForm({ onDone }: { onDone?: () => void }) {
  const [ticker, setTicker] = useState("");
  const [peers, setPeers] = useState("SPY,QQQ");
  const [kinds, setKinds] = useState<string[]>(["intraday", "daily"]);
  const [depth, setDepth] = useState<Depth>("standard");
  const [claude, setClaude] = useState(true);
  const [save, setSave] = useState(true);
  const [saveAll, setSaveAll] = useState(false);
  const [pf, setPf] = useState<any>(null);
  const { start, busy, error } = useStartRun();
  const usable = kinds.filter((k) => pf?.kinds?.[k]?.ok);
  const t = useT();
  const toggle = (k: string) => setKinds((ks) => (ks.includes(k) ? ks.filter((x) => x !== k) : [...ks, k]));
  return (
    <form className="disc-form" onSubmit={(e) => {
      e.preventDefault();
      start({ ticker, peers: peers.split(",").map((p) => p.trim()).filter(Boolean), kinds: usable,
              generations: DEPTHS[depth].generations, max_trials: DEPTHS[depth].max_trials, save_drafts: save || saveAll, save_all: saveAll, offline: !claude || !pf?.llm });
      onDone?.();
    }}>
      {error && <Notice kind="bad">{error}</Notice>}
      <div className="disc-fields">
        <label className="bt-field"><span>{t("Ticker")}</span>
          <input value={ticker} onChange={(e) => setTicker(e.target.value.toUpperCase())} placeholder="AMD" maxLength={8} required autoFocus /></label>
        <label className="bt-field"><span>{t("Peers it may read")}</span>
          <input value={peers} onChange={(e) => setPeers(e.target.value.toUpperCase())} placeholder="SPY,QQQ" />
          <small>{t("market or sector references for peer() rules; never traded")}</small></label>
        <div className="bt-field"><span>{t("Strategy kinds")}</span>
          <div className="seg">
            {["intraday", "daily"].map((k) => <button type="button" key={k} className={cls(kinds.includes(k) && "on")} aria-pressed={kinds.includes(k)} onClick={() => toggle(k)}>{t(k)}</button>)}
          </div>
          <small>{t("intraday: 1-minute bars, flat by the close · daily: held overnight, one decision a day")}</small></div>
        <div className="bt-field"><span>{t("Search depth")}</span>
          <div className="seg">
            {(Object.keys(DEPTHS) as Depth[]).map((d) => <button type="button" key={d} className={cls(depth === d && "on")} aria-pressed={depth === d} onClick={() => setDepth(d)}>{t(DEPTHS[d].label)}</button>)}
          </div>
          <small>{t(DEPTHS[depth].hint)}</small></div>
      </div>
      <Preflight ticker={ticker} peers={peers} onData={setPf} />
      <div className="row" style={{ marginTop: 10 }}>
        <label className="row" style={{ gap: 4 }} title={pf && !pf.llm ? t("No model key on the models service: offline templates only") : undefined}>
          <input type="checkbox" checked={claude && !!pf?.llm} disabled={!pf?.llm} onChange={(e) => setClaude(e.target.checked)} />
          {t("Kimi proposes and revises")}{pf && !pf.llm && <span className="muted"> {t("(no key: templates only)")}</span>}
        </label>
        <label className="row" style={{ gap: 4 }}><input type="checkbox" checked={save} onChange={(e) => setSave(e.target.checked)} /> {t("Save holdout survivors as drafts")}</label>
        <label className="row" style={{ gap: 4 }} title={t("Every valid, evaluated trial becomes its own draft strategy (can be many files)")}>
          <input type="checkbox" checked={saveAll} onChange={(e) => setSaveAll(e.target.checked)} /> {t("Save every evaluated candidate")}</label>
        <span className="grow" />
        <button className="primary" disabled={busy || !usable.length || !!pf?.running}
          title={pf?.running ? t("Run #{id} is still running", { id: pf.running }) : !usable.length ? t("No selected kind has enough stored data") : undefined}>
          {busy ? t("Starting…") : pf?.running ? t("Run #{id} running…", { id: pf.running }) : t("Start discovery")}
        </button>
      </div>
    </form>
  );
}

/** Recent runs, with live progress. */
export function RunsList({ limit = 8 }: { limit?: number }) {
  const { data } = usePoll<any>("/api/discovery", 3000);
  const runs: any[] = data?.runs?.slice(0, limit) ?? [];
  const t = useT();
  if (!runs.length) return <p className="muted">{t("No discovery runs yet.")}</p>;
  return (
    <div className="table-wrap"><table>
      <thead><tr><th>{t("Run")}</th><th>{t("Ticker")}</th><th>{t("Status")}</th><th>{t("Progress")}</th><th className="num">{t("Trials")}</th><th className="num">{t("Passed gates")}</th><th className="num">{t("Holdout pass")}</th><th>{t("Started")}</th></tr></thead>
      <tbody>{runs.map((r) => (
        <tr key={r.id}>
          <td><Link href={`/strategies/discover/${r.id}`}><b>#{r.id}</b></Link>{r.follow_up && <span className="tag" title={t("follow-up of run #{id}", { id: r.parent_run_id ?? "?" })}>{t("follow-up")}</span>}</td>
          <td>{r.ticker} <span className="muted">{(r.kinds || []).map((k: string) => t(k)).join(" + ")}</span></td>
          <td><RunStatus status={r.status} /></td>
          <td style={{ minWidth: 160 }}>
            {r.status === "running" ? <><Meter frac={r.progress} /><span className="muted" style={{ fontSize: 12 }}>{r.stage}</span></>
              : r.status === "failed" ? <span className="neg" style={{ fontSize: 12 }}>{(r.error || "").slice(0, 90)}</span>
              : <span className="muted" style={{ fontSize: 12 }}>{r.generator}</span>}
          </td>
          <td className="num">{r.trials || "—"}</td><td className="num">{r.trials ? r.passed_gates : "—"}</td>
          <td className="num">{r.trials ? <span className={r.holdout_pass ? "pos" : ""}>{r.holdout_pass}{r.drafts ? ` · ${t(r.drafts > 1 ? "{n} drafts" : "{n} draft", { n: r.drafts })}` : ""}</span> : "—"}</td>
          <td className="muted">{wall(r.created_at)}</td>
        </tr>
      ))}</tbody>
    </table></div>
  );
}

/** The Strategies-page panel: generate + recent runs. */
export function DiscoverPanel({ owner }: { owner: boolean }) {
  const [open, setOpen] = useState(false);
  const t = useT();
  return (
    <Card className="mt disc-panel" title={
      <span className="row between" style={{ width: "100%" }}>
        <span>{t("Strategy discovery")} <span className="muted" style={{ fontWeight: 400 }}>· {t("the bot proposes, backtests and revises single-ticker strategies; a sealed holdout decides what survives")}</span></span>
        {owner && <button type="button" className={cls(!open && "primary")} onClick={() => setOpen(!open)}>{t(open ? "Close" : "Generate strategies…")}</button>}
      </span>}>
      {open && owner && <DiscoverForm />}
      <h3>{t("Recent runs")}</h3>
      <RunsList />
    </Card>
  );
}

/** Strategy page: continue iterating this single-ticker strategy. */
export function IterateCard({ hash, d, owner }: { hash: string; d: any; owner: boolean }) {
  const [depth, setDepth] = useState<Depth>("quick");
  const { start, busy, error } = useStartRun();
  const t = useT();
  if (!d) return null;
  const made = d.made_by;
  return (
    <Card title={t("Discovery")} className="mt">
      {made && (
        <p style={{ marginTop: 0 }}>
          {t("Found by {link} (trial #{trial})", { link: <Link href={`/strategies/discover/${made.run_id}`}>{t("discovery run #{id}", { id: made.run_id })}</Link>, trial: made.trial_id })}
          {made.holdout && <>{t(": sealed holdout Sharpe {v}", { v: num(made.holdout.sharpe) })}{made.holdout.dsr?.dsr != null && t(", deflated Sharpe {v}", { v: num(made.holdout.dsr.dsr) })}</>}.
        </p>
      )}
      {d.ticker ? (
        owner && <>
          {error && <Notice kind="bad">{error}</Notice>}
          <div className="row">
            <button className="primary" disabled={busy} onClick={() => start({ ticker: d.ticker, seed_hash: hash, kinds: [d.kind],
              generations: DEPTHS[depth].generations, max_trials: DEPTHS[depth].max_trials })}>
              {t(busy ? "Starting…" : "Let the bot iterate on this strategy")}</button>
            <div className="seg">
              {(Object.keys(DEPTHS) as Depth[]).map((x) => <button type="button" key={x} className={cls(depth === x && "on")} onClick={() => setDepth(x)}>{t(DEPTHS[x].label)}</button>)}
            </div>
          </div>
          <p className="muted" style={{ fontSize: 12 }}>
            {t("Starts from this file and revises it against its measured weaknesses on {ticker}'s discovery sessions. Survivors become new drafts; this version is never changed. The holdout is the same as earlier runs', so every earlier trial counts against the result.", { ticker: d.ticker })}
          </p>
        </>
      ) : <p className="muted" style={{ margin: 0 }}>{t(d.kind === "daily" ? "Discovery iterates single-ticker strategies; this one trades a basket." : "Discovery iterates single-ticker strategies; this one trades the watchlist.")}</p>}
      {!!d.follow_ups?.length && (
        <p className="muted" style={{ marginBottom: 0 }}>{t("Follow-up runs:")} {d.follow_ups.map((r: any, i: number) => (
          <span key={r.id}>{i ? ", " : ""}<Link href={`/strategies/discover/${r.id}`}>#{r.id}</Link> <StatusPill status={r.status} /></span>))}</p>
      )}
    </Card>
  );
}

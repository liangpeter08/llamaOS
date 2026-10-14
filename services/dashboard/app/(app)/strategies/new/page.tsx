"use client";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Fragment, Suspense, useEffect, useRef, useState } from "react";
import { get, post } from "@/lib/api";
import { pct, money, num, wall } from "@/lib/format";
import { Card, Empty, Notice, Pill } from "@/components/ui";
import { isOwner, useSession } from "@/components/session";

interface Msg { id: number; ts: number; role: "user" | "assistant"; content: string; model: string; has_context?: boolean }
interface Draft { yaml: string; ok: boolean; errors: string[]; warnings: string[]; id: string | null; kind: string | null; back_translation: string | null; hash: string }
interface Review {
  ok: boolean; error?: string; verdict?: string; score?: number; summary?: string; key_points?: string[]; positives?: string[];
  concerns?: { severity: "high" | "medium" | "low"; text: string }[]; suggestions?: { text: string; change: string }[]; seconds?: number;
}
interface View {
  thread: string; messages: Msg[]; draft: Draft | null; saved: number; reviews?: Record<string, Review>;
  improving?: { sid: string; version: number; hash: string } | null;
}

const ROLES: [string, string, string][] = [
  ["analyst", "Analyst", "market key points"], ["researcher", "Researcher", "risk / reward"],
  ["trader", "Trader", "trading strategy"], ["risk", "Risk Team", "risk exposure"],
];

const STARTERS = [
  "Buy the dip on big tech when they fall 3% in a day and sell into strength",
  "Trade opening-range breakouts on stocks with fresh earnings news",
  "Hold a basket of software stocks for a month, trimming winners",
  "Fade gaps up of more than 4% on no news",
];

const IMPROVE_STARTERS = [
  "It trades too rarely to tell whether it works",
  "The losses are bigger than the wins",
  "The drawdowns are too deep",
  "It does worse than buy & hold",
  "It gives back its gains before it exits",
];

function newThread() {
  const rand = Array.from(crypto.getRandomValues(new Uint8Array(8)), (b) => b.toString(16).padStart(2, "0")).join("");
  return `design:${rand}`;
}

export default function NewStrategyPage() {
  return <Suspense fallback={<p className="muted">Loading…</p>}><Designer /></Suspense>;
}

function Designer() {
  const params = useSearchParams();
  const router = useRouter();
  const { me } = useSession();
  const owner = isOwner(me);
  const [thread, setThread] = useState<string>(() => params.get("thread") || newThread());
  const improve = /^[0-9a-f]{64}$/.test(params.get("improve") ?? "") ? params.get("improve") : null;
  const [original, setOriginal] = useState<any>(null);
  const [view, setView] = useState<View | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [started, setStarted] = useState(0);
  const [now, setNow] = useState(Date.now());
  const [error, setError] = useState<string | null>(null);
  const [bt, setBt] = useState<any>(null);
  const [name, setName] = useState("");
  const [sessions, setSessions] = useState<any[]>([]);
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setView(null);
    setBt(null);
    get<View>(`/api/design/${thread}`).then(setView).catch(() => setView({ thread, messages: [], draft: null, saved: 0 }));
    get<{ threads: any[] }>("/api/design").then((r) => setSessions(r.threads)).catch(() => {});
  }, [thread]);
  useEffect(() => {
    setOriginal(null);
    if (improve) get<any>(`/api/strategies/${improve}`).then(setOriginal).catch((e) => setError((e as Error).message));
  }, [improve]);
  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [view?.messages.length, busy]);
  useEffect(() => {
    if (!busy) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [busy]);

  async function run<T>(label: string, fn: () => Promise<T>) {
    setBusy(label);
    setStarted(Date.now());
    setError(null);
    try {
      return await fn();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  async function send(message: string) {
    if (!message.trim()) return;
    const optimistic: Msg = { id: -1, ts: Date.now() / 1000, role: "user", content: message, model: "you" };
    setView((v) => (v ? { ...v, messages: [...v.messages, optimistic] } : v));
    setText("");
    const source = improve && !view?.messages.length ? improve : undefined;
    const r = await run("chat", () => post<View>(`/api/design/${thread}`, { message, source }));
    if (r) {
      setView(r);
      if (params.get("thread") !== thread) router.replace(`/strategies/new?thread=${thread}`);
      get<{ threads: any[] }>("/api/design").then((x) => setSessions(x.threads)).catch(() => {});
    } else {
      setText(message);
      setView((v) => (v ? { ...v, messages: v.messages.filter((m) => m.id !== -1) } : v));
    }
  }

  async function backtest() {
    if (!view?.draft) return;
    const r = await run("backtest", () => post<any>(`/api/design/${thread}/backtest`, { yaml: view.draft!.yaml }));
    if (r) {
      setBt(r.backtest);
      setView(r);
    }
  }

  async function panel() {
    if (!view?.draft) return;
    const r = await run("panel", () => post<View>(`/api/design/${thread}/review`, { yaml: view.draft!.yaml }));
    if (r) setView(r);
  }

  async function save() {
    if (!view?.draft) return;
    const r = await run("save", () => post<any>(`/api/design/${thread}/save`, { yaml: view.draft!.yaml, name }));
    if (r?.hash) router.push(`/strategies/${r.hash}`);
  }

  const msgs = view?.messages ?? [];
  const d = view?.draft;
  const starting = improve && !msgs.length;  // a new duplicate-and-improve conversation, before the first message
  const improving = view?.improving ?? (starting && original ? { sid: original.s.sid, version: original.s.version, hash: improve } : null);
  const elapsed = Math.round((now - started) / 1000);

  return (
    <>
      <div className="row between">
        <div>
          <h1 style={{ marginBottom: 4 }}>{improving ? "Improve a strategy" : "New strategy"}</h1>
          {improving ? (
            <p className="muted" style={{ marginTop: 0 }}>
              Duplicating <Link href={`/strategies/${improving.hash}`}>{improving.sid} v{improving.version}</Link> with Kimi K3. Kimi sees its file,
              backtest, decision records and trades, diagnoses what you describe and drafts an improved copy under a new id. Every draft is checked by the
              validator; backtest and review it here, then save it as a new draft strategy. The original is not changed.
            </p>
          ) : (
            <p className="muted" style={{ marginTop: 0 }}>
              Design it with Kimi K3: describe the idea, however rough. Kimi works through what&apos;s ambiguous or unsupported, points to similar
              strategies you&apos;ve already backtested, suggests improvements and drafts the file. Every draft is checked by the validator; you can
              backtest it here and save it as a new draft strategy.
            </p>
          )}
        </div>
        <div className="row">
          <Link className="btn" href="/strategies">Back to strategies</Link>
          <button onClick={() => { setThread(newThread()); router.replace("/strategies/new"); }}>New conversation</button>
        </div>
      </div>

      <div className="grid g21 mt">
        <Card title="Design conversation">
          <div className="chat" style={{ maxHeight: "62vh", minHeight: 260 }}>
            {!view && <p className="muted">Loading…</p>}
            {view && starting && !busy && (
              <div>
                {original && <OriginalSummary o={original} />}
                <p className="muted">What&apos;s wrong with it, or what should the copy do better? For example:</p>
                <div className="row">
                  {IMPROVE_STARTERS.map((s) => <button key={s} onClick={() => setText(s)} style={{ fontSize: 12.5 }}>{s}</button>)}
                </div>
              </div>
            )}
            {view && msgs.length === 0 && !starting && !busy && (
              <div>
                <p className="muted">Start with the idea in your own words. Some examples:</p>
                <div className="row">
                  {STARTERS.map((s) => <button key={s} onClick={() => setText(s)} style={{ fontSize: 12.5 }}>{s}</button>)}
                </div>
              </div>
            )}
            {msgs.map((m) => <Message key={m.id} m={m} />)}
            {busy === "chat" && (
              <div className="msg assistant muted">
                <div className="who">kimi-k3</div>
                Thinking… {elapsed}s {elapsed > 20 && "· drafting and validating can take a minute or two"}
              </div>
            )}
            <div ref={end} />
          </div>
          {error && <Notice kind="bad">{error}</Notice>}
          {owner ? (
            <form onSubmit={(e) => { e.preventDefault(); send(text); }}>
              <textarea value={text} onChange={(e) => setText(e.target.value)} disabled={!!busy}
                onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); send(text); } }}
                placeholder={msgs.length ? "Answer, push back, or ask for a draft… (⌘/Ctrl+Enter to send)"
                  : starting ? "Describe the problem with this strategy… (⌘/Ctrl+Enter to send)" : "Describe your strategy idea… (⌘/Ctrl+Enter to send)"} />
              <div className="row" style={{ marginTop: 6 }}>
                <button className="primary" disabled={!!busy || !text.trim()}>{busy === "chat" ? "Thinking…" : "Send"}</button>
                {msgs.length > 0 && !d && <button type="button" disabled={!!busy} onClick={() => send("Draft the complete strategy file now with sensible defaults for anything still open, and list them as assumptions.")}>Ask for a draft</button>}
                {d && <button type="button" disabled={!!busy} onClick={() => send("Review the current draft critically: what would you change to make it more robust, and why?")}>Critique the draft</button>}
              </div>
            </form>
          ) : <p className="muted">Viewers can read design conversations; only the owner can design strategies.</p>}
        </Card>

        <div className="grid" style={{ alignContent: "start" }}>
          <Card title="Current draft">
            {!d ? <Empty>No draft yet. Kimi drafts the file once the important choices are settled, or when you ask.</Empty> : (
              <>
                <div className="row">
                  {d.ok ? <Pill kind="good">valid</Pill> : <Pill kind="bad">{d.errors.length} validator error{d.errors.length > 1 ? "s" : ""}</Pill>}
                  {d.id && <Pill>{d.id}</Pill>}
                  {d.kind && <Pill kind={d.kind === "portfolio" ? "accent" : ""}>{d.kind}</Pill>}
                  <Pill><span className="mono">{d.hash.slice(0, 10)}</span></Pill>
                </div>
                {!d.ok && <Notice kind="bad"><ul>{d.errors.map((e) => <li key={e}>{e}</li>)}</ul></Notice>}
                {d.warnings.length > 0 && <Notice kind="warn">{d.warnings.map((w) => <div key={w}>{w}</div>)}</Notice>}
                {d.back_translation && (
                  <>
                    <h3>What the rules say</h3>
                    <pre style={{ maxHeight: 260 }}>{d.back_translation}</pre>
                  </>
                )}
                <details style={{ marginTop: 8 }}><summary>Strategy file</summary><pre style={{ maxHeight: 360 }}>{d.yaml}</pre></details>
                {owner && (
                  <>
                    <div className="row" style={{ marginTop: 10 }}>
                      <button disabled={!d.ok || !!busy} onClick={backtest}>{busy === "backtest" ? "Backtesting…" : "Backtest draft"}</button>
                      <button disabled={!d.ok || !!busy} onClick={panel}>{busy === "panel" ? `Panel reviewing… ${elapsed}s` : "Run review panel"}</button>
                    </div>
                    <p className="muted" style={{ fontSize: 12, margin: "4px 0 0" }}>The panel is four Kimi K3 agents (Analyst, Researcher, Trader, Risk Team) reviewing this exact draft in parallel{bt ? ", with its backtest" : "; backtest first so they can use the results"}.</p>
                    {bt && <BacktestSummary bt={bt} />}
                    <h3>Save</h3>
                    <div className="row">
                      <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Name (optional)" maxLength={80} style={{ flex: 1, minWidth: 140 }} />
                      <button className="primary" disabled={!d.ok || !!busy} onClick={save}>{busy === "save" ? "Saving…" : "Save as draft strategy"}</button>
                    </div>
                    <p className="muted" style={{ fontSize: 12 }}>Saving creates a new draft version; backtest, promote and fund it from its page. The conversation can go on toward a better version.</p>
                  </>
                )}
              </>
            )}
          </Card>
          <Card title="Recent design sessions">
            {!sessions.length ? <Empty>None yet.</Empty> : (
              <table><tbody>
                {sessions.map((s) => (
                  <tr key={s.thread}>
                    <td><a href={`/strategies/new?thread=${s.thread}`} onClick={(e) => { e.preventDefault(); setThread(s.thread); router.replace(`/strategies/new?thread=${s.thread}`); }}>
                      {(() => { const t = (s.title || "(untitled)").length > 70 ? `${s.title.slice(0, 70)}…` : s.title || "(untitled)"; return s.thread === thread ? <b>{t}</b> : t; })()}</a></td>
                    <td className="muted" style={{ whiteSpace: "nowrap", fontSize: 12 }}>{wall(s.updated)}</td>
                  </tr>
                ))}
              </tbody></table>
            )}
          </Card>
        </div>
      </div>
      {d && view?.reviews && Object.keys(view.reviews).length > 0 && (
        <Card title="Review panel" className="mt">
          <div className="row" style={{ marginBottom: 8 }}>
            {ROLES.map(([k, name]) => view.reviews![k] && <Verdict key={k} name={name} r={view.reviews![k]} />)}
            {owner && <button className="primary" disabled={!!busy} style={{ marginLeft: "auto" }}
              onClick={() => send("Apply the review panel's suggestions you agree with and give me a revised complete file. Say which points you rejected and why.")}>
              Ask Kimi to apply the panel&apos;s suggestions</button>}
          </div>
          <div className="grid g2">{ROLES.map(([k, name, focus]) => view.reviews![k] && <RoleCard key={k} name={name} focus={focus} r={view.reviews![k]} />)}</div>
        </Card>
      )}
    </>
  );
}

const VERDICT: Record<string, ["good" | "warn" | "bad", string]> = {
  approve: ["good", "approve"], approve_with_changes: ["warn", "approve with changes"], rework: ["bad", "rework"],
};

function Verdict({ name, r }: { name: string; r: Review }) {
  const who = name ? `${name}: ` : "";
  if (!r.ok) return <Pill kind="bad">{who}failed</Pill>;
  const [kind, label] = VERDICT[r.verdict ?? ""] ?? ["", r.verdict ?? ""];
  return <Pill kind={kind}>{who}{label} · {r.score}/10</Pill>;
}

function RoleCard({ name, focus, r }: { name: string; focus: string; r: Review }) {
  const sev = { high: "bad", medium: "warn", low: "" } as const;
  return (
    <div className="card">
      <div className="row between">
        <b>{name} <span className="muted" style={{ fontWeight: 400 }}>· {focus}</span></b>
        <Verdict name="" r={r} />
      </div>
      {!r.ok ? <Notice kind="bad">{r.error}</Notice> : (
        <>
          <p style={{ marginTop: 8 }}>{r.summary}</p>
          {!!r.key_points?.length && (<><h3>Key points</h3><ul>{r.key_points.map((x, i) => <li key={i}>{x}</li>)}</ul></>)}
          {!!r.positives?.length && (<><h3>Positives</h3><ul>{r.positives.map((x, i) => <li key={i} className="pos">{x}</li>)}</ul></>)}
          {!!r.concerns?.length && (
            <>
              <h3>Concerns</h3>
              <ul>{r.concerns.map((c, i) => <li key={i}><Pill kind={sev[c.severity]}>{c.severity}</Pill> {c.text}</li>)}</ul>
            </>
          )}
          {!!r.suggestions?.length && (
            <>
              <h3>Suggested changes</h3>
              <ul>{r.suggestions.map((x, i) => <li key={i}>{x.text}{x.change && <pre style={{ margin: "4px 0" }}>{x.change}</pre>}</li>)}</ul>
            </>
          )}
          {r.seconds != null && <p className="muted" style={{ fontSize: 11, marginBottom: 0 }}>kimi-k3 · {r.seconds}s</p>}
        </>
      )}
    </div>
  );
}

function Message({ m }: { m: Msg }) {
  const tag = m.role === "user" ? m.content.match(/^\[(validator|backtest|saved|panel)\]/)?.[1] : null;
  if (tag) {
    const body = m.content.replace(/^\[\w+\]\s*/, "");
    const label = { validator: "Validator feedback sent to Kimi", backtest: "Backtest result shared with Kimi", saved: "Saved",
      panel: "Review panel findings shared with Kimi" }[tag];
    return (
      <div className="msg" style={{ alignSelf: "center", maxWidth: "95%", fontSize: 12.5, background: "transparent", border: "1px dashed var(--border)" }}>
        <div className="who">{label} · {wall(m.ts)}</div>
        {tag === "backtest" || tag === "panel" ? <details><summary>{body.split("\n")[0]}</summary><pre>{body.split("\n").slice(1).join("\n")}</pre></details> : body}
      </div>
    );
  }
  return (
    <div className={`msg ${m.role}`}>
      <div className="who">{m.role === "user" ? "you" : m.model}{m.has_context && " · with account context"}</div>
      <Rich text={m.content} />
    </div>
  );
}

/** Minimal rendering: fenced code blocks as <pre>, **bold**, `code`; everything else keeps its line breaks. */
function Rich({ text }: { text: string }) {
  const parts = text.split(/```[a-zA-Z]*\n?/);
  return (
    <>
      {parts.map((p, i) => i % 2 === 1
        ? <pre key={i} style={{ margin: "6px 0", maxHeight: 320 }}>{p.replace(/\n$/, "")}</pre>
        : <Fragment key={i}>{inline(p)}</Fragment>)}
    </>
  );
}

function inline(s: string) {
  return s.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((t, i) =>
    t.startsWith("**") && t.endsWith("**") ? <b key={i}>{t.slice(2, -2)}</b>
      : t.startsWith("`") && t.endsWith("`") ? <code key={i}>{t.slice(1, -1)}</code> : t);
}

/** The strategy being duplicated, so the trader can describe its problem with the numbers in view. */
function OriginalSummary({ o }: { o: any }) {
  const m = o.backtest?.metrics;
  return (
    <div className="card" style={{ marginBottom: 10 }}>
      <b>{o.s.display_name || o.s.sid} <span className="muted">v{o.s.version}</span></b>{" "}
      <Pill kind={o.kind === "portfolio" ? "accent" : ""}>{o.kind === "portfolio" ? "portfolio" : "intraday"}</Pill> <Pill>{o.s.status}</Pill>
      {m ? (
        <div className="row" style={{ marginTop: 6 }}>
          {m.trades != null && <Pill>{m.trades} trades</Pill>}
          {m.win_rate != null && <Pill>win {pct(m.win_rate, 0)}</Pill>}
          {m.total_pnl != null && <Pill kind={m.total_pnl >= 0 ? "good" : "bad"}>P&amp;L {money(m.total_pnl, true)}{m.return_pct != null && ` (${pct(m.return_pct, 2, true)})`}</Pill>}
          {m.max_drawdown != null && <Pill>max DD {pct(m.max_drawdown, 1)}</Pill>}
          {m.sharpe_daily != null && <Pill>Sharpe {num(m.sharpe_daily)}</Pill>}
        </div>
      ) : <p className="muted" style={{ fontSize: 12, margin: "6px 0 0" }}>Not backtested yet: Kimi will work from the file and any paper trades.</p>}
      {o.back_translation && <details style={{ marginTop: 6 }}><summary>What the rules say</summary><pre style={{ maxHeight: 220 }}>{o.back_translation}</pre></details>}
    </div>
  );
}

function BacktestSummary({ bt }: { bt: any }) {
  const m = bt.metrics;
  return (
    <div className="card" style={{ marginTop: 8 }}>
      <div className="muted" style={{ fontSize: 12 }}>{bt.sessions} sessions {bt.from} → {bt.to} · {bt.seconds}s · shared with Kimi</div>
      <div className="row" style={{ marginTop: 6 }}>
        <Pill>{m.trades} trades</Pill>
        {m.win_rate != null && <Pill>win {pct(m.win_rate, 0)}</Pill>}
        <Pill kind={m.total_pnl >= 0 ? "good" : "bad"}>P&amp;L {money(m.total_pnl, true)} ({pct(m.return_pct, 2, true)})</Pill>
        {m.benchmark_return_pct != null && <Pill>buy &amp; hold {pct(m.benchmark_return_pct, 2, true)}</Pill>}
        <Pill>max DD {pct(m.max_drawdown, 1)}</Pill>
        {m.sharpe_daily != null && <Pill>Sharpe {num(m.sharpe_daily)}</Pill>}
      </div>
      {bt.note && <p className="muted" style={{ fontSize: 12, marginBottom: 0 }}>{bt.note}</p>}
    </div>
  );
}

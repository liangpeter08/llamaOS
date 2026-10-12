"use client";
import { useState } from "react";
import { post } from "@/lib/api";
import { usePoll } from "@/lib/hooks";
import { cls, et } from "@/lib/format";
import { Card, Loading, Notice, Pill } from "@/components/ui";
import { isOwner, useSession } from "@/components/session";
import { NewsToClaude } from "@/components/NewsToClaude";

const tierKind = (t: string | null) => (t === "escalated" ? "bad" : t === "t1" ? "accent" : "");

export default function News() {
  const [scope, setScope] = useState<"live" | "history">("live");
  const { data, error, reload } = usePoll<any>(`/api/news?scope=${scope}`, 3000);
  const { me } = useSession();
  return (
    <>
      <h1>News &amp; signals</h1>
      <div className="tabs">
        <button className={cls(scope === "live" && "on")} onClick={() => setScope("live")}>Latest (live feed)</button>
        <button className={cls(scope === "history" && "on")} onClick={() => setScope("history")}>History (recorded)</button>
      </div>
      {!data ? <Loading error={error} /> : (
        <div className="grid g21">
          <Card>
            <div className="row" style={{ marginBottom: 8 }}>
              {Object.entries(data.counts).map(([k, v]) => <Pill key={k} kind={tierKind(k) as any}>{k}: {String(v)}</Pill>)}
            </div>
            <div className="table-wrap"><table>
              <thead><tr><th>Time</th><th>Symbols</th><th>Headline</th><th>Tier</th><th>Signals written</th></tr></thead>
              <tbody>
                {data.items.map((n: any) => (
                  <tr key={n.id}>
                    <td className="mono">{et(n.ts, scope === "history")}</td>
                    <td>{n.symbols.join(" ")}</td>
                    <td>{n.url && /^https?:\/\//i.test(n.url) ? <a href={n.url} target="_blank" rel="noopener noreferrer" className="news-headline">{n.headline} <span aria-label="opens in a new tab">↗</span></a> : n.headline}<br /><span className="muted">{n.source}{n.triage_reason && ` · ${n.triage_reason}`}</span></td>
                    <td><Pill kind={tierKind(n.triage) as any}>{n.triage ?? "pending"}</Pill></td>
                    <td>
                      {n.signals.filter((s: any) => ["news.score", "news.catalyst", "news.red_flag"].includes(s.name)).map((s: any) => (
                        <span key={s.name + s.symbol} className="tag">{s.name.slice(5)}={s.value}</span>
                      ))}
                      {n.signals[0] && <div className="muted">{n.signals[0].model} · {n.signals[0].tier}</div>}
                    </td>
                  </tr>
                ))}
                {!data.items.length && <tr><td colSpan={5} className="muted">{scope === "live" ? "No news received yet for the configured watchlist." : "No recorded history yet."}</td></tr>}
              </tbody>
            </table></div>
          </Card>
          <div>
            {data.news_claude && <Card title="Claude escalation"><NewsToClaude status={data.news_claude} owner={isOwner(me)} reload={reload} /></Card>}
            <Notes notes={data.notes} owner={isOwner(me)} reload={reload} />
          </div>
        </div>
      )}
    </>
  );
}

function Notes({ notes, owner, reload }: { notes: any[]; owner: boolean; reload: () => void }) {
  const [symbol, setSymbol] = useState("");
  const [text, setText] = useState("");
  const [msg, setMsg] = useState<{ kind: "ok" | "bad"; text: string } | null>(null);
  async function add(e: React.FormEvent) {
    e.preventDefault();
    try {
      const r = await post("/api/notes", { symbol, text });
      setMsg({ kind: "ok", text: r.message });
      setSymbol("");
      setText("");
      reload();
    } catch (err) {
      setMsg({ kind: "bad", text: (err as Error).message });
    }
  }
  return (
    <Card title="Your notes">
      <p className="muted">A note sets <code>note.stance</code> for a ticker (bullish / bearish / neutral / avoid), which strategies can use to say no. Active notes also escalate that ticker's news to T2.</p>
      {msg && <Notice kind={msg.kind}>{msg.text}</Notice>}
      {owner && (
        <form onSubmit={add}>
          <input placeholder="TICKER" required value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())} style={{ width: "7em" }} />
          <textarea placeholder="Avoid CRUX until the trial readout" required value={text} onChange={(e) => setText(e.target.value)} />
          <button className="primary">Add note</button>
        </form>
      )}
      <table style={{ marginTop: 10 }}><tbody>
        {notes.map((n) => (
          <tr key={n.id}><td><b>{n.symbol}</b></td><td><Pill>{n.stance}</Pill></td><td className="muted">{n.text}</td>
            <td>{owner && <button onClick={() => post(`/api/notes/${n.id}/expire`).then(reload)}>×</button>}</td></tr>
        ))}
      </tbody></table>
    </Card>
  );
}

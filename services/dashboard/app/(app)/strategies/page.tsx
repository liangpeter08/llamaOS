"use client";
import Link from "next/link";
import { useState } from "react";
import { post } from "@/lib/api";
import { usePoll } from "@/lib/hooks";
import { money, pct, pnlClass, short } from "@/lib/format";
import { Card, Loading, Notice, StatusPill } from "@/components/ui";
import { isOwner, useSession } from "@/components/session";

export default function Strategies() {
  const { data, error } = usePoll<any>("/api/strategies", 5000);
  const { me } = useSession();
  if (!data) return <Loading error={error} />;
  return (
    <>
      <h1>Strategies</h1>
      <p className="muted">
        Lifecycle: draft → backtested → paper → live (hash-approved). Status lives in the registry, keyed by each file's SHA-256, so any edit is a
        new version that starts at draft.
      </p>
      <Card>
        <div className="table-wrap"><table>
          <thead><tr><th>Strategy</th><th>Version</th><th>Status</th><th>Mode</th><th>Valid</th><th className="num">Backtest trades</th><th className="num">Win rate</th><th className="num">Backtest P&amp;L</th><th className="num">Sim trades</th><th className="num">Sim P&amp;L</th><th>Hash</th></tr></thead>
          <tbody>
            {data.rows.map((r: any) => (
              <tr key={r.hash}>
                <td><Link href={`/strategies/${r.hash}`}><b>{r.sid}</b></Link></td><td>v{r.version}</td>
                <td><StatusPill status={r.status} /></td><td>{r.mode}</td>
                <td>{r.validation.ok ? <span className="pos">✓</span> : <span className="neg" title={(r.validation.errors || []).join("; ")}>✗ {(r.validation.errors || []).length}</span>}</td>
                {r.bt ? (
                  <>
                    <td className="num">{r.bt.metrics.trades}</td><td className="num">{pct(r.bt.metrics.win_rate, 0)}</td>
                    <td className={`num ${pnlClass(r.bt.metrics.total_pnl)}`}>{money(r.bt.metrics.total_pnl, true)}</td>
                  </>
                ) : <td className="num muted" colSpan={3}>not run</td>}
                <td className="num">{r.sim.n}</td><td className={`num ${pnlClass(r.sim.pnl)}`}>{money(r.sim.pnl, true)}</td>
                <td className="mono muted">{short(r.hash)}</td>
              </tr>
            ))}
          </tbody>
        </table></div>
      </Card>
      {isOwner(me) && <Compile />}
    </>
  );
}

function Compile() {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<any>(null);
  async function go(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setRes(null);
    try {
      setRes(await post("/api/strategies/compile", { text }));
    } catch (err) {
      setRes({ error: (err as Error).message });
    } finally {
      setBusy(false);
    }
  }
  return (
    <Card title="New strategy from plain English" className="mt">
      <p className="muted">Claude (Opus) drafts a DSL file and lists every assumption it had to make. The validator checks it, and a passing file is saved as a new draft. You approve; code trades.</p>
      <form onSubmit={go}>
        <textarea value={text} onChange={(e) => setText(e.target.value)} required
          placeholder="On liquid stocks with fresh positive earnings news, buy a break of the 15-minute high on strong volume…" />
        <div className="row" style={{ marginTop: 6 }}><button className="primary" disabled={busy}>{busy ? "Compiling…" : "Compile"}</button></div>
      </form>
      {res && (
        <div style={{ marginTop: 10 }}>
          {res.error ? <Notice kind="bad">{res.error}</Notice>
            : res.ok ? (
              <Notice kind="ok">Compiled and validated: <Link href={`/strategies/${res.hash}`}>{res.path}</Link> (draft). {res.notes}</Notice>
            ) : (
              <>
                <Notice kind="bad">The validator rejected the draft after {res.attempts?.length} attempts:<ul>{res.errors.map((e: string) => <li key={e}>{e}</li>)}</ul></Notice>
                <pre>{res.yaml}</pre>
              </>
            )}
        </div>
      )}
    </Card>
  );
}

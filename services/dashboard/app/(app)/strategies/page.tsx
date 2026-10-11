"use client";
import Link from "next/link";
import { Fragment, useMemo, useState } from "react";
import { post } from "@/lib/api";
import { usePoll } from "@/lib/hooks";
import { money, pct, pnlClass, short } from "@/lib/format";
import { Card, Empty, Loading, Notice, StatusPill } from "@/components/ui";
import { isOwner, useSession } from "@/components/session";
import { BulkBar, ViewSwitch, type View } from "@/components/Organize";
import { DiscoverPanel } from "@/components/Discovery";

const STATUSES = ["draft", "backtested", "paper", "live", "retired"];

export default function Strategies() {
  const { data, error, reload } = usePoll<any>("/api/strategies", 5000);
  const { me } = useSession();
  const owner = isOwner(me);
  const [view, setView] = useState<View>("active");
  const [q, setQ] = useState("");
  const [category, setCategory] = useState("");
  const [status, setStatus] = useState("");
  const [kind, setKind] = useState("");
  const [latest, setLatest] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ kind: "ok" | "bad"; text: string } | null>(null);

  const rows: any[] = useMemo(() => {
    if (!data) return [];
    const newest: Record<string, number> = {};
    for (const r of data.rows) if (!r.archived_at) newest[r.sid] = Math.max(newest[r.sid] ?? 0, r.version);
    const needle = q.trim().toLowerCase();
    return data.rows
      .filter((r: any) => view === "all" || (view === "archived") === !!r.archived_at)
      .filter((r: any) => !needle || [r.sid, r.display_name, r.category, r.hash].some((x) => x?.toLowerCase().includes(needle)))
      .filter((r: any) => !category || (category === "-" ? !r.category : r.category === category))
      .filter((r: any) => !status || r.status === status)
      .filter((r: any) => !kind || r.kind === kind)
      .filter((r: any) => !latest || r.archived_at || r.version === newest[r.sid])
      // Group by category (uncategorized last), then strategy line, newest version first.
      .sort((a: any, b: any) => (a.category ?? "￿").localeCompare(b.category ?? "￿") || a.sid.localeCompare(b.sid) || b.version - a.version);
  }, [data, view, q, category, status, kind, latest]);

  if (!data) return <Loading error={error} />;
  const archivedCount = data.rows.filter((r: any) => r.archived_at).length;
  const grouped = !category;
  const visible = rows.filter((r) => selected.has(r.hash));

  async function bulk(body: object) {
    setBusy(true);
    try {
      const r = await post("/api/strategies/meta", { hashes: visible.map((r) => r.hash), ...body });
      setMsg({ kind: "ok", text: r.message });
      setSelected(new Set());
    } catch (e) {
      setMsg({ kind: "bad", text: (e as Error).message });
    } finally {
      setBusy(false);
      reload();
    }
  }
  const toggle = (h: string, on: boolean) => setSelected((s) => { const n = new Set(s); on ? n.add(h) : n.delete(h); return n; });
  const allOn = rows.length > 0 && rows.every((r) => selected.has(r.hash));
  const cols = owner ? 13 : 12;

  return (
    <>
      <div className="row between">
        <h1>Strategies</h1>
        {owner && <Link className="btn primary" href="/strategies/new">+ New strategy</Link>}
      </div>
      <p className="muted">
        Lifecycle: draft → backtested → paper → live (hash-approved). Status lives in the registry, keyed by each file's SHA-256, so any edit is a
        new version that starts at draft. Archive old versions to hide them; archiving never changes the file or its status.
      </p>
      {msg && <Notice kind={msg.kind}>{msg.text}</Notice>}
      <DiscoverPanel owner={owner} />
      <Card className="mt">
        <div className="filters">
          <ViewSwitch view={view} setView={(v) => { setView(v); setSelected(new Set()); }} archived={archivedCount} />
          <input type="search" aria-label="Search strategies" placeholder="Search name, id, category or hash" value={q} onChange={(e) => setQ(e.target.value)} />
          <select aria-label="Category" value={category} onChange={(e) => setCategory(e.target.value)}>
            <option value="">All categories</option>
            {data.categories.map((c: string) => <option key={c}>{c}</option>)}
            <option value="-">Uncategorized</option>
          </select>
          <select aria-label="Status" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All statuses</option>{STATUSES.map((s) => <option key={s}>{s}</option>)}
          </select>
          <select aria-label="Kind" value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="">All kinds</option><option>intraday</option><option>portfolio</option>
          </select>
          <label className="row" style={{ gap: 4 }}><input type="checkbox" checked={latest} onChange={(e) => setLatest(e.target.checked)} /> Latest version only</label>
          <span className="muted">{rows.length} of {data.rows.length}</span>
        </div>
        {owner && <BulkBar count={visible.length} categories={data.categories} busy={busy} onClear={() => setSelected(new Set())}
          onCategory={(c) => bulk({ category: c })} onArchive={(a) => bulk({ archived: a })} />}
        <div className="table-wrap"><table>
          <thead><tr>
            {owner && <th><input type="checkbox" aria-label="Select all shown" checked={allOn} onChange={(e) => setSelected(e.target.checked ? new Set(rows.map((r) => r.hash)) : new Set())} /></th>}
            <th>Strategy</th><th>Version</th><th>Status</th><th>Mode</th><th>Valid</th><th className="num">Backtest trades</th><th className="num">Win rate</th><th className="num">Backtest P&amp;L</th><th className="num">Sim trades</th><th className="num">Sim P&amp;L</th><th>Category</th><th>Hash</th>
          </tr></thead>
          <tbody>
            {rows.map((r: any, i: number) => (
              <Fragment key={r.hash}>
                {grouped && (i === 0 || rows[i - 1].category !== r.category) && (
                  <tr className="group"><td colSpan={cols}>{r.category ?? "Uncategorized"}</td></tr>
                )}
                <tr className={r.archived_at ? "archived" : ""}>
                  {owner && <td><input type="checkbox" aria-label={`Select ${r.sid} v${r.version}`} checked={selected.has(r.hash)} onChange={(e) => toggle(r.hash, e.target.checked)} /></td>}
                  <td><Link href={`/strategies/${r.hash}`}><b>{r.display_name || r.sid}</b></Link>{r.kind === "portfolio" && <span className="muted"> · portfolio</span>}{r.archived_at && <span className="tag">archived</span>}</td><td>v{r.version}</td>
                  <td><StatusPill status={r.status} /></td><td>{r.mode}</td>
                  <td>{r.validation.ok ? <span className="pos">✓</span> : <span className="neg" title={(r.validation.errors || []).join("; ")}>✗ {(r.validation.errors || []).length}</span>}</td>
                  {r.bt ? (
                    <>
                      <td className="num">{r.bt.metrics.trades}</td><td className="num">{pct(r.bt.metrics.win_rate, 0)}</td>
                      <td className={`num ${pnlClass(r.bt.metrics.total_pnl)}`}>{money(r.bt.metrics.total_pnl, true)}</td>
                    </>
                  ) : <td className="num muted" colSpan={3}>not run</td>}
                  <td className="num">{r.sim.n}</td><td className={`num ${pnlClass(r.sim.pnl)}`}>{money(r.sim.pnl, true)}</td>
                  <td>{r.category ? <span className="tag">{r.category}</span> : <span className="muted">—</span>}</td>
                  <td className="mono muted">{short(r.hash)}</td>
                </tr>
              </Fragment>
            ))}
          </tbody>
        </table></div>
        {!rows.length && <Empty>{view === "archived" ? "No archived strategies." : "No strategies match these filters."}</Empty>}
      </Card>
    </>
  );
}

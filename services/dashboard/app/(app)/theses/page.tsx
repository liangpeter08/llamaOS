"use client";
import { useState } from "react";
import { post } from "@/lib/api";
import { usePoll } from "@/lib/hooks";
import { Card, Loading, Notice } from "@/components/ui";
import { isOwner, useSession } from "@/components/session";

export default function Theses() {
  const { data, error, reload } = usePoll<any>("/api/theses");
  const { me } = useSession();
  const [f, setF] = useState({ text: "", tags: "", confidence: 0.6, expires: "" });
  const [err, setErr] = useState<string | null>(null);
  if (!data) return <Loading error={error} />;

  async function add(e: React.FormEvent) {
    e.preventDefault();
    try {
      await post("/api/theses", f);
      setF({ text: "", tags: "", confidence: 0.6, expires: "" });
      setErr(null);
      reload();
    } catch (x) {
      setErr((x as Error).message);
    }
  }

  return (
    <>
      <h1>Theses</h1>
      <p className="muted">Your own assumptions about the market. The Idea Lab must test them and may not silently override them; T2 news reads see the ones tagged with a ticker or catalyst.</p>
      {err && <Notice kind="bad">{err}</Notice>}
      {isOwner(me) && (
        <Card>
          <form onSubmit={add}>
            <textarea required placeholder="Analyst upgrades of large caps fade within the first hour" value={f.text} onChange={(e) => setF({ ...f, text: e.target.value })} />
            <div className="row" style={{ marginTop: 6 }}>
              <input placeholder="tags: analyst_upgrade, BOLT" value={f.tags} onChange={(e) => setF({ ...f, tags: e.target.value })} />
              <label className="row">confidence <input type="number" min={0} max={1} step={0.05} style={{ width: "5em" }} value={f.confidence} onChange={(e) => setF({ ...f, confidence: Number(e.target.value) })} /></label>
              <label className="row">expires <input type="date" value={f.expires} onChange={(e) => setF({ ...f, expires: e.target.value })} /></label>
              <button className="primary">Add thesis</button>
            </div>
          </form>
        </Card>
      )}
      <Card className="mt">
        <table>
          <thead><tr><th>#</th><th>Thesis</th><th>Tags</th><th className="num">Confidence</th><th>Expires</th><th>Influenced</th><th /></tr></thead>
          <tbody>
            {data.rows.map((t: any) => (
              <tr key={t.id} className={t.active ? "" : "dim"}>
                <td>{t.id}</td><td>{t.text}</td>
                <td>{(t.tags || "").split(",").filter((g: string) => g.trim()).map((g: string) => <span className="tag" key={g}>{g.trim()}</span>)}</td>
                <td className="num">{t.confidence}</td><td>{t.expires || "—"}</td>
                <td>{t.ideas.length ? t.ideas.map((i: any) => <span key={i.id} className="tag">idea #{i.id}</span>) : <span className="muted">—</span>}</td>
                <td>{t.active ? (isOwner(me) && <button onClick={() => post(`/api/theses/${t.id}/expire`).then(reload)}>Expire</button>) : "expired"}</td>
              </tr>
            ))}
            {!data.rows.length && <tr><td colSpan={7} className="muted">No theses yet.</td></tr>}
          </tbody>
        </table>
      </Card>
    </>
  );
}

"use client";
import { useState } from "react";
import { usePoll } from "@/lib/hooks";
import { wall } from "@/lib/format";
import { Card, Loading, Notice } from "@/components/ui";
import { useT } from "@/lib/i18n";

export default function Audit() {
  const [q, setQ] = useState("");
  const [kind, setKind] = useState("");
  const [verify, setVerify] = useState(0);
  const [applied, setApplied] = useState({ q: "", kind: "" });
  const path = `/api/audit?q=${encodeURIComponent(applied.q)}&kind=${encodeURIComponent(applied.kind)}&verify=${verify}`;
  const { data, error } = usePoll<any>(path, 5000);
  const t = useT();
  return (
    <>
      <h1>{t("Audit log")}</h1>
      <p className="muted">{t("Append-only and hash-chained: each entry's hash covers the previous one, so any edit or deletion breaks verification from that point on.")}</p>
      <Card>
        <form className="row" onSubmit={(e) => { e.preventDefault(); setApplied({ q, kind }); }}>
          <input placeholder={t("search payload / actor")} value={q} onChange={(e) => setQ(e.target.value)} />
          <select value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="">{t("all kinds")}</option>
            {data?.kinds.map((k: string) => <option key={k}>{k}</option>)}
          </select>
          <button>{t("Filter")}</button>
          <button type="button" onClick={() => setVerify(verify ? 0 : 1)}>{t(verify ? "Hide verification" : "Verify chain")}</button>
          <a className="btn" href="/api/audit/export.jsonl">{t("Export JSONL")}</a>
        </form>
      </Card>
      {data?.verified && (
        <Notice kind={data.verified.ok ? "ok" : "bad"}>
          {data.verified.ok ? t("Chain verified: {n} entries, head {head}…", { n: data.verified.checked, head: data.verified.head.slice(0, 16) })
            : t("Chain BROKEN at entry #{at}: {why} (after {n} good entries)", { at: data.verified.broken_at, why: data.verified.why, n: data.verified.checked })}
        </Notice>
      )}
      {!data ? <Loading error={error} /> : (
        <Card className="mt">
          <div className="table-wrap"><table>
            <thead><tr><th>#</th><th>{t("When")}</th><th>{t("Kind")}</th><th>{t("Actor")}</th><th>{t("Payload")}</th></tr></thead>
            <tbody>{data.rows.map((r: any) => (
              <tr key={r.id}><td>{r.id}</td><td className="mono">{wall(r.ts)}</td><td>{r.kind}</td><td>{r.actor}</td>
                <td className="mono muted" style={{ maxWidth: 640, overflowWrap: "anywhere" }}>{r.payload}</td></tr>
            ))}</tbody>
          </table></div>
        </Card>
      )}
    </>
  );
}

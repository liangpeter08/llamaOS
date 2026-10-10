"use client";
import { useMemo, useState } from "react";
import { post } from "@/lib/api";
import { usePoll } from "@/lib/hooks";
import { wall } from "@/lib/format";
import { Card, Loading, Notice, Pill } from "@/components/ui";
import { isOwner, useSession } from "@/components/session";

const TITLES: Record<string, string> = {
  account: "Account", per_trade: "Per trade (intraday)", execution: "Execution", market: "Market hours & liquidity",
  portfolio: "Portfolio sleeves (multi-day)", funds: "Fund & cash controls",
};

interface Control {
  key: string; section: string; label: string; help: string; type: string; choices: string[];
  default: any; value: any; overridden: boolean; updated_by: string | null; updated_at: number | null;
}

const show = (v: any) => (typeof v === "boolean" ? (v ? "yes" : "no") : v == null ? "—" : String(v));

export default function Risk() {
  const { data, error, reload } = usePoll<any>("/api/risk", 10000);
  const { me } = useSession();
  const [edits, setEdits] = useState<Record<string, any>>({});
  const [reason, setReason] = useState("");
  const [msg, setMsg] = useState<{ kind: "ok" | "bad"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const owner = isOwner(me);
  const bySection = useMemo(() => {
    const out: Record<string, Control[]> = {};
    for (const c of (data?.controls ?? []) as Control[]) (out[c.section] ??= []).push(c);
    return out;
  }, [data]);
  if (!data) return <Loading error={error} />;
  const changed = Object.keys(edits).length;

  async function save() {
    setBusy(true);
    try {
      const r = await post("/api/risk", { changes: edits, reason });
      setMsg({ kind: "ok", text: r.message });
      setEdits({});
      setReason("");
    } catch (e) {
      setMsg({ kind: "bad", text: (e as Error).message });
    } finally {
      setBusy(false);
      reload();
    }
  }

  function input(c: Control) {
    const cur = c.key in edits ? edits[c.key] : c.value;
    const set = (v: any) => setEdits((e) => {
      const n = { ...e };
      if (String(v) === String(c.value)) delete n[c.key];
      else n[c.key] = v;
      return n;
    });
    if (!owner) return <b>{show(c.value)}</b>;
    if (c.type === "bool") return <input type="checkbox" checked={!!cur} onChange={(e) => set(e.target.checked)} />;
    if (c.type === "choice") return <select value={cur} onChange={(e) => set(e.target.value)}>{c.choices.map((x) => <option key={x}>{x}</option>)}</select>;
    return <input value={String(cur ?? "")} onChange={(e) => set(e.target.value)} style={{ width: 150 }} className="mono" />;
  }

  return (
    <>
      <h1>Risk</h1>
      <p className="muted">
        Live risk controls, stored in Postgres. {owner ? "Edit a value, give a reason and save; " : ""}the broker and watchdog apply changes within a few
        seconds. Every change is validated as a whole, kept in the history below and written to the audit log. A control you haven&apos;t set
        uses its factory default (<code>{data.path}</code> or the built-in default). Strategies can only tighten these. Resetting the kill switch
        stays host-only.
      </p>
      {msg && <Notice kind={msg.kind}>{msg.text}</Notice>}
      {data.logged_sha256 && data.logged_sha256 !== data.risk.sha256 && (
        <Notice kind="warn">The broker hasn&apos;t loaded the latest controls yet (it checks every loop; is it running?).</Notice>
      )}
      {owner && changed > 0 && (
        <Card className="mt">
          <div className="row">
            <b>{changed} unsaved change{changed > 1 ? "s" : ""}</b>
            <input placeholder="reason (required)" value={reason} onChange={(e) => setReason(e.target.value)} style={{ flex: 1, minWidth: 220 }} />
            <button className="primary" disabled={busy || reason.trim().length < 3} onClick={save}>{busy ? "Saving…" : "Save"}</button>
            <button disabled={busy} onClick={() => setEdits({})}>Discard</button>
          </div>
        </Card>
      )}
      <div className="grid g2 mt">
        {(data.sections as string[]).map((sec) => (
          <Card key={sec} title={TITLES[sec] ?? sec}>
            <table>
              <tbody>
                {(bySection[sec] ?? []).map((c) => (
                  <tr key={c.key}>
                    <td style={{ width: "42%" }}>
                      {c.label}
                      {c.help && <div className="muted" style={{ fontSize: 12 }}>{c.help}</div>}
                    </td>
                    <td>{input(c)}</td>
                    <td className="muted" style={{ fontSize: 12 }}>
                      {c.overridden ? (
                        <>
                          <Pill kind="accent">set</Pill> by {c.updated_by} {wall(c.updated_at)} · default {show(c.default)}
                          {owner && <button style={{ marginLeft: 6 }} onClick={() => setEdits((e) => ({ ...e, [c.key]: "default" }))}>reset</button>}
                        </>
                      ) : "default"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        ))}
      </div>
      <Card title="Change history" className="mt">
        <div className="table-wrap"><table>
          <thead><tr><th>When</th><th>Control</th><th>From</th><th>To</th><th>By</th><th>Reason</th></tr></thead>
          <tbody>
            {data.history.map((h: any) => (
              <tr key={h.id}><td>{wall(h.ts)}</td><td className="mono">{h.key}</td><td>{show(JSON.parse(h.old_value ?? "null") ?? "default")}</td>
                <td>{show(JSON.parse(h.new_value ?? "null") ?? "default")}</td><td>{h.actor}</td><td className="muted">{h.reason}</td></tr>
            ))}
            {!data.history.length && <tr><td colSpan={6} className="muted">No changes yet: every control is at its default.</td></tr>}
          </tbody>
        </table></div>
        <p className="muted mono" style={{ fontSize: 12 }}>effective checksum {data.risk.sha256} · revision {data.rev}</p>
      </Card>
    </>
  );
}

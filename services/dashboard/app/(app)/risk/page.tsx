"use client";
import { useMemo, useState } from "react";
import { post } from "@/lib/api";
import { usePoll } from "@/lib/hooks";
import { wall } from "@/lib/format";
import { Card, Loading, Notice, Pill } from "@/components/ui";
import { isOwner, useSession } from "@/components/session";
import { RiskHelp } from "@/components/RiskHelp";
import { useT } from "@/lib/i18n";

const TITLES: Record<string, string> = {
  account: "Account", per_trade: "Per trade (intraday)", execution: "Execution", market: "Market hours & liquidity",
  portfolio: "Portfolio sleeves (multi-day)", funds: "Fund & cash controls",
};

interface Control {
  key: string; section: string; label: string; help: string; type: string; choices: string[];
  default: any; value: any; overridden: boolean; updated_by: string | null; updated_at: number | null;
  details: { description: string; example: string; interaction: string };
}

const show = (v: any, t: (s: string) => string) => (typeof v === "boolean" ? t(v ? "yes" : "no") : v == null ? "—" : String(v));

export default function Risk() {
  const { data, error, reload } = usePoll<any>("/api/risk", 10000);
  const { me } = useSession();
  const [edits, setEdits] = useState<Record<string, any>>({});
  const [reason, setReason] = useState("");
  const [msg, setMsg] = useState<{ kind: "ok" | "bad"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const owner = isOwner(me);
  const t = useT();
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
    if (!owner) return <b>{show(c.value, t)}</b>;
    if (c.type === "bool") return <input aria-label={t(c.label)} type="checkbox" checked={!!cur} onChange={(e) => set(e.target.checked)} />;
    if (c.type === "choice") return <select aria-label={t(c.label)} value={cur} onChange={(e) => set(e.target.value)}>{c.choices.map((x) => <option key={x}>{x}</option>)}</select>;
    return <input aria-label={t(c.label)} value={String(cur ?? "")} onChange={(e) => set(e.target.value)} style={{ width: 150 }} className="mono" />;
  }

  return (
    <>
      <h1>{t("Risk")}</h1>
      <p className="muted">{t("Hover over a setting name or its ⓘ icon for detailed help. You can also focus or tap the name.")}</p>
      <p className="muted">
        {t("Live risk controls, stored in Postgres.")} {owner ? t("Edit a value, give a reason and save; the broker and watchdog apply changes within a few seconds.") : t("The broker and watchdog apply changes within a few seconds.")}{" "}
        {t("Every change is validated as a whole, kept in the history below and written to the audit log. A control you haven't set uses its factory default ({path} or the built-in default). Strategies can only tighten these. Resetting the kill switch stays host-only.",
          { path: <code>{data.path}</code> })}
      </p>
      {msg && <Notice kind={msg.kind}>{msg.text}</Notice>}
      {data.logged_sha256 && data.logged_sha256 !== data.risk.sha256 && (
        <Notice kind="warn">{t("The broker hasn't loaded the latest controls yet (it checks every loop; is it running?).")}</Notice>
      )}
      {owner && changed > 0 && (
        <Card className="mt">
          <div className="row">
            <b>{t(changed > 1 ? "{n} unsaved changes" : "{n} unsaved change", { n: changed })}</b>
            <input placeholder={t("reason (required)")} value={reason} onChange={(e) => setReason(e.target.value)} style={{ flex: 1, minWidth: 220 }} />
            <button className="primary" disabled={busy || reason.trim().length < 3} onClick={save}>{t(busy ? "Saving…" : "Save")}</button>
            <button disabled={busy} onClick={() => setEdits({})}>{t("Discard")}</button>
          </div>
        </Card>
      )}
      <div className="grid g2 mt">
        {(data.sections as string[]).map((sec) => (
          <Card key={sec} title={t(TITLES[sec] ?? sec)}>
            <table>
              <tbody>
                {(bySection[sec] ?? []).map((c) => (
                  <tr key={c.key}>
                    <td style={{ width: "42%" }}>
                      <RiskHelp label={t(c.label)} details={c.details} />
                      {c.help && <div className="muted" style={{ fontSize: 12 }}>{t(c.help)}</div>}
                    </td>
                    <td>{input(c)}</td>
                    <td className="muted" style={{ fontSize: 12 }}>
                      {c.overridden ? (
                        <>
                          <Pill kind="accent">{t("set")}</Pill> {t("by {who}", { who: c.updated_by })} {wall(c.updated_at)} · {t("default")} {show(c.default, t)}
                          {owner && <button style={{ marginLeft: 6 }} onClick={() => setEdits((e) => ({ ...e, [c.key]: "default" }))}>{t("reset")}</button>}
                        </>
                      ) : t("default")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        ))}
      </div>
      <Card title={t("Change history")} className="mt">
        <div className="table-wrap"><table>
          <thead><tr><th>{t("When")}</th><th>{t("Control")}</th><th>{t("From")}</th><th>{t("To")}</th><th>{t("By")}</th><th>{t("Reason")}</th></tr></thead>
          <tbody>
            {data.history.map((h: any) => (
              <tr key={h.id}><td>{wall(h.ts)}</td><td className="mono">{h.key}</td><td>{show(JSON.parse(h.old_value ?? "null") ?? t("default"), t)}</td>
                <td>{show(JSON.parse(h.new_value ?? "null") ?? t("default"), t)}</td><td>{h.actor}</td><td className="muted">{h.reason}</td></tr>
            ))}
            {!data.history.length && <tr><td colSpan={6} className="muted">{t("No changes yet: every control is at its default.")}</td></tr>}
          </tbody>
        </table></div>
        <p className="muted mono" style={{ fontSize: 12 }}>{t("effective checksum {sha} · revision {rev}", { sha: data.risk.sha256, rev: data.rev })}</p>
      </Card>
    </>
  );
}

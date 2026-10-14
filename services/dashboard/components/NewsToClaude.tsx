"use client";
import { useEffect, useState } from "react";
import { post } from "@/lib/api";
import { Notice, Pill } from "@/components/ui";

type Hours = { enabled: boolean; start: string; end: string; weekdays_only: boolean };
export type NewsClaudeStatus = { on: boolean; hours: Hours; active_now: boolean };

// Owner switch for sending news to Kimi, plus optional active hours (US/Eastern).
export function NewsToClaude({ status, owner, reload }: { status: NewsClaudeStatus; owner: boolean; reload: () => void }) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ kind: "ok" | "bad"; text: string } | null>(null);
  const [hours, setHours] = useState<Hours>(status.hours);
  const saved = status.hours;
  useEffect(() => setHours(saved), [saved.enabled, saved.start, saved.end, saved.weekdays_only]);
  const dirty = hours.start !== saved.start || hours.end !== saved.end || hours.weekdays_only !== saved.weekdays_only;

  async function run(path: string, body: any) {
    setBusy(true);
    setMsg(null);
    try { const r = await post(path, body); setMsg({ kind: "ok", text: r.message }); reload(); }
    catch (x) { setMsg({ kind: "bad", text: (x as Error).message }); }
    finally { setBusy(false); }
  }
  const saveHours = (h: Hours) => run("/api/settings/news-to-claude/hours", h);
  const disabled = !owner || busy;

  return (
    <div style={{ marginBottom: 12 }}>
      {msg && <Notice kind={msg.kind}>{msg.text}</Notice>}
      <div className="row" style={{ gap: 8 }}>
        <label className="row" style={{ gap: 6 }}>
          <input type="checkbox" checked={status.on} disabled={disabled}
            onChange={(e) => run("/api/settings/news-to-claude", { on: e.target.checked })} />
          <b>Send news to Kimi</b>
        </label>
        <Pill kind={status.active_now ? "good" : ""}>{status.active_now ? "Kimi active now" : "Kimi paused now"}</Pill>
      </div>
      <div className="row" style={{ gap: 6, marginTop: 6, flexWrap: "wrap" }}>
        <label className="row" style={{ gap: 6 }}>
          <input type="checkbox" checked={hours.enabled} disabled={disabled || !status.on}
            onChange={(e) => { const h = { ...hours, enabled: e.target.checked }; setHours(h); saveHours(h); }} />
          Only between
        </label>
        <input type="time" value={hours.start} disabled={disabled || !status.on || !hours.enabled}
          onChange={(e) => setHours({ ...hours, start: e.target.value })} style={{ width: "7.5em" }} />
        <span>and</span>
        <input type="time" value={hours.end} disabled={disabled || !status.on || !hours.enabled}
          onChange={(e) => setHours({ ...hours, end: e.target.value })} style={{ width: "7.5em" }} />
        <span className="muted">ET</span>
        <label className="row" style={{ gap: 6 }}>
          <input type="checkbox" checked={hours.weekdays_only} disabled={disabled || !status.on || !hours.enabled}
            onChange={(e) => setHours({ ...hours, weekdays_only: e.target.checked })} />
          weekdays only
        </label>
        {hours.enabled && dirty && <button className="primary" disabled={disabled} onClick={() => saveHours(hours)}>Save hours</button>}
      </div>
      <p className="muted" style={{ margin: "4px 0 0", fontSize: 12 }}>
        {!status.on ? "Off: news is triaged by Laya, Gemini or the offline keyword model only. Strategy authoring, chat and discovery still use Kimi."
          : saved.enabled ? `News triage may use Kimi (K2.7 quick and deep reads) only ${saved.start}–${saved.end} ET${saved.weekdays_only ? " on weekdays" : ""}; outside that window it falls back to Laya, Gemini or the offline model.`
          : "News triage may use Kimi (K2.7 quick and deep reads) at any time when it is the configured provider."}
      </p>
    </div>
  );
}

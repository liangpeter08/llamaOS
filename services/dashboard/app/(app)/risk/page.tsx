"use client";
import { Fragment } from "react";
import { usePoll } from "@/lib/hooks";
import { pct } from "@/lib/format";
import { Card, Loading, Notice } from "@/components/ui";

export default function Risk() {
  const { data, error } = usePoll<any>("/api/risk");
  if (!data) return <Loading error={error} />;
  const r = data.risk;
  const rows: [string, string][] = [
    ["Max gross exposure", `${r.max_gross_exposure}× equity`], ["Shorting", r.allow_short ? "allowed" : "not allowed"],
    ["Max open positions", r.max_open_positions], ["Max trades / day", r.max_trades_per_day], ["Max daily loss", `${pct(r.max_daily_loss)} → kill switch`],
    ["Default / max risk per trade", `${pct(r.default_risk)} / ${pct(r.max_risk)}`], ["Max position", `${pct(r.max_position, 0)} of equity`],
    ["Bracket required", r.require_bracket ? "yes" : "no"], ["Entry order", r.entry_order], ["Max slippage", pct(r.max_slippage)],
    ["Cancel unfilled after", `${r.cancel_unfilled_after}s`], ["Approval expiry", `${r.approval_expiry}s`], ["Max drift at approval", pct(r.max_drift)],
    ["Entries", `${r.entries} ET`], ["Flatten by", `${r.flatten_by} ET`], ["Min price", `$${r.min_price}`],
    ["Min ADV", `$${Number(r.min_adv).toLocaleString()}`], ["Stale feed halt", `${r.stale_feed_halt}s`], ["Reconcile every", `${r.reconcile_every}s`],
  ].map(([k, v]) => [k, String(v)]);
  return (
    <>
      <h1>Risk</h1>
      <p className="muted">
        Read-only. The dashboard can tighten (pause strategies, trigger the kill switch) but never loosen: limits change only by editing{" "}
        <code>{data.path}</code> on the host.
      </p>
      {data.logged_sha256 && data.logged_sha256 !== r.sha256 && (
        <Notice kind="warn">risk.yaml on disk differs from the checksum the broker logged at startup. Restart the broker to load it.</Notice>
      )}
      <div className="grid g2">
        <Card title="Effective limits">
          <dl className="kv">{rows.map(([k, v]) => <Fragment key={k}><dt>{k}</dt><dd>{v}</dd></Fragment>)}</dl>
          <h3>Checksum</h3>
          <p className="mono">{r.sha256}</p>
          <h3>Mirrored broker settings</h3>
          <p className="muted">Simulator: margin multiplier 1 and no-shorting are enforced by the risk guard. With Alpaca, <code>make broker-guard</code> applies and verifies them on the account.</p>
        </Card>
        <Card title="risk.yaml"><pre>{data.raw}</pre></Card>
      </div>
    </>
  );
}

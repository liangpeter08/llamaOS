"use client";
import { useState } from "react";
import { get, post } from "@/lib/api";
import { usePoll } from "@/lib/hooks";
import { isOwner, useSession } from "@/components/session";
import { cls, et, money, num, pnlClass } from "@/lib/format";
import { Card, Empty, Loading, Notice, Pill } from "@/components/ui";

type Msg = { kind: "ok" | "bad" | "warn"; text: string } | null;

// Positions are changed by the broker loop, not the API: each action queues a command and we
// follow it until the broker reports the result (usually within a couple of seconds).
function useAction(reload: () => void) {
  const [msg, setMsg] = useState<Msg>(null);
  const [busy, setBusy] = useState(false);
  async function run(path: string, body: unknown = {}, confirmText?: string) {
    if (confirmText && !window.confirm(confirmText)) return;
    setBusy(true);
    try {
      const r = await post(path, body);
      setMsg({ kind: "warn", text: r.message || "Sent." });
      if (r.command) {
        for (let i = 0; i < 90; i++) {
          await new Promise((res) => setTimeout(res, 1000));
          const c = await get(`/api/commands/${r.command}`);
          if (c.status !== "pending") {
            const bad = /^(rejected|failed)/.test(c.result || "");
            setMsg({ kind: bad ? "bad" : "ok", text: c.result || "Done." });
            break;
          }
          if (i === 89) setMsg({ kind: "warn", text: "The broker hasn't picked this up yet. Is it running?" });
        }
      }
    } catch (e: any) {
      setMsg({ kind: "bad", text: e.message });
    } finally {
      setBusy(false);
      reload();
    }
  }
  return { msg, busy, run, setMsg };
}

const OWNER_LABEL: Record<string, string> = { manual: "manual", sleeve: "portfolio sleeve", strategy: "strategy", unmanaged: "not managed" };
const KIND_LABEL: Record<string, string> = {
  sleeve: "Sleeve shares differ", closed_outside: "Closed in Alpaca", unowned_position: "Opened in Alpaca", unknown_order: "Order placed in Alpaca",
};

export default function Positions() {
  const { data, error, reload } = usePoll<any>("/api/positions/manage", 3000);
  const { me } = useSession();
  const act = useAction(reload);
  if (!data) return <Loading error={error} />;
  const owner = isOwner(me);
  const rec = data.reconcile || {};
  const items: any[] = rec.items || [];
  return (
    <>
      <h1>Positions</h1>
      <p className="muted">
        Equity {money(data.acct.equity)} · day P&amp;L <span className={pnlClass(data.acct.day_pnl)}>{money(data.acct.day_pnl, true)}</span> · exposure {money(data.acct.gross_exposure)}
        {" · "}market {data.market_open ? "open" : "closed"}
        {data.kill && <> · <Pill kind="bad">kill switch active</Pill></>}
      </p>
      {act.msg && <Notice kind={act.msg.kind}>{act.msg.text}</Notice>}

      <Card title={<>Reconcile with Alpaca <Pill kind={rec.sim_ts ? (rec.ok ? "good" : "bad") : ""}>{rec.sim_ts ? (rec.ok ? "match" : "differences") : "waiting"}</Pill></>}>
        <div className="row between">
          <label style={{ display: "flex", gap: 8, alignItems: "flex-start", flex: "1 1 320px" }}>
            <input type="checkbox" style={{ marginTop: 4 }} checked={!!data.auto} disabled={!owner || act.busy}
              onChange={(e) => act.run("/api/settings/auto-reconcile", { on: e.target.checked })} />
            <span><b>Auto-reconcile</b> <span className="muted">· closes, opens and orders you make in the Alpaca app are booked at their real fill prices about 20 seconds later</span></span>
          </label>
          {owner && items.some((x) => x.fix) && <button className="primary" disabled={act.busy} onClick={() => act.run("/api/reconcile/accept", {}, "Apply every suggested fix now?")}>Fix all</button>}
        </div>
        <p className="muted" style={{ fontSize: 12 }}>Last checked {rec.sim_ts ? et(rec.sim_ts) + " ET" : "—"}. If a strategy still runs, it may buy a position back at its next decision; retire it to stop that.</p>
        {items.length ? (
          <div className="table-wrap"><table>
            <thead><tr><th>Symbol</th><th>What</th><th className="num">llamaOS</th><th className="num">Alpaca</th><th>Suggested fix</th><th /></tr></thead>
            <tbody>{items.map((x, i) => (
              <tr key={i}>
                <td><b>{x.symbol}</b>{x.sid && <><br /><span className="muted">{x.sid}</span></>}</td>
                <td>{KIND_LABEL[x.kind] || x.kind}{x.kind === "unknown_order" && <span className="muted"> · {x.side} {num(x.qty, 0)}</span>}</td>
                <td className="num">{x.llama != null ? num(x.llama, 0) : "—"}</td>
                <td className="num">{x.alpaca != null ? num(x.alpaca, 0) : "—"}</td>
                <td>{x.fix || <span className="muted">{x.note}</span>}</td>
                <td>{owner && x.fix && <button disabled={act.busy} onClick={() => act.run("/api/reconcile/accept", { symbol: x.symbol })}>Fix</button>}
                  {owner && x.kind === "unknown_order" && <button className="link" disabled={act.busy} onClick={() => act.run(`/api/orders/${x.order_id}/cancel`, {}, `Cancel this ${x.symbol} order at Alpaca?`)}>Cancel order</button>}</td>
              </tr>
            ))}</tbody>
          </table></div>
        ) : <Empty>{rec.sim_ts ? "Our books match Alpaca." : "The broker hasn't reported yet."}</Empty>}
        {rec.portfolio_mismatch && !items.length && <p className="muted">{rec.portfolio_mismatch}</p>}
      </Card>

      {owner && <NewOrder data={data} act={act} />}

      <Card title="Open positions" className="mt">
        {data.positions.length ? (
          <div className="table-wrap"><table>
            <thead><tr><th>Symbol</th><th>Owner</th><th>Side</th><th className="num">Qty</th><th className="num">Avg</th><th className="num">Last</th><th className="num">Unrealized</th><th className="num">Stop</th><th className="num">Target</th><th /></tr></thead>
            <tbody>{data.positions.map((p: any) => <PositionRow key={p.symbol} p={p} owner={owner} act={act} />)}</tbody>
          </table></div>
        ) : <Empty>Flat.</Empty>}
      </Card>

      <Card title="Working orders" className="mt">
        {data.orders.length ? (
          <div className="table-wrap"><table>
            <thead><tr><th>Symbol</th><th>Side</th><th>Type</th><th className="num">Qty</th><th className="num">Filled</th><th className="num">Limit</th><th className="num">Stop</th><th>TIF</th><th>Placed</th><th>Source</th><th /></tr></thead>
            <tbody>{data.orders.map((o: any) => (
              <tr key={o.id}>
                <td><b>{o.symbol}</b></td><td>{o.side}</td>
                <td>{o.type}{o.order_class && o.order_class !== "simple" && <span className="tag">{o.order_class}</span>}
                  {o.legs?.length > 0 && <div className="muted" style={{ fontSize: 12 }}>{o.legs.map((l: any) => `${l.type} ${l.stop_price ?? l.limit_price}`).join(" · ")}</div>}</td>
                <td className="num">{num(Number(o.qty), 0)}</td><td className="num">{num(Number(o.filled_qty || 0), 0)}</td>
                <td className="num">{o.limit_price ? money(Number(o.limit_price)) : "—"}</td><td className="num">{o.stop_price ? money(Number(o.stop_price)) : "—"}</td>
                <td>{o.time_in_force}</td><td>{et(Date.parse(o.created_at) / 1000)}</td>
                <td>{String(o.client_order_id || "").startsWith("llama-") ? "llamaOS" : <span className="muted">Alpaca</span>}</td>
                <td>{owner && <button className="link" disabled={act.busy} onClick={() => act.run(`/api/orders/${o.id}/cancel`, {}, `Cancel ${o.side} ${o.qty} ${o.symbol}?`)}>Cancel</button>}</td>
              </tr>
            ))}</tbody>
          </table></div>
        ) : <Empty>No working orders.</Empty>}
      </Card>

      <Card title="Recent actions" className="mt">
        {data.commands.length ? (
          <div className="table-wrap"><table>
            <thead><tr><th>When</th><th>Action</th><th>Details</th><th>By</th><th>Result</th></tr></thead>
            <tbody>{data.commands.map((c: any) => (
              <tr key={c.id}>
                <td>{et(c.ts, true)}</td><td>{c.kind.replace(/^manual_/, "").replace("_", " ")}</td>
                <td className="mono" style={{ fontSize: 12 }}>{Object.entries(c.payload || {}).filter(([, v]) => v != null && v !== "").map(([k, v]) => `${k}=${v}`).join(" ")}</td>
                <td>{c.issued_by}</td>
                <td className={cls(/^(rejected|failed)/.test(c.result || "") && "neg")}>{c.status === "pending" ? <Pill kind="warn">pending</Pill> : c.result}</td>
              </tr>
            ))}</tbody>
          </table></div>
        ) : <Empty>Nothing yet.</Empty>}
      </Card>
    </>
  );
}

function NewOrder({ data, act }: { data: any; act: ReturnType<typeof useAction> }) {
  const [f, setF] = useState({ symbol: "", side: "long", qty: "", type: "market", limit_price: "", stop: "", target: "", tif: "day" });
  const set = (k: string, v: string) => setF((x) => ({ ...x, [k]: v }));
  const n = (v: string) => (v.trim() === "" ? null : Number(v));
  const ref = n(f.limit_price);
  const notional = ref && n(f.qty) ? ref * Number(f.qty) : null;
  function submit(e: React.FormEvent) {
    e.preventDefault();
    const body = { symbol: f.symbol.trim().toUpperCase(), side: f.side, qty: Number(f.qty), type: f.type, tif: f.tif,
      limit_price: f.type === "limit" ? n(f.limit_price) : null, stop: n(f.stop), target: n(f.target) };
    const what = `${f.side === "long" ? "Buy" : "Short"} ${body.qty} ${body.symbol} at ${f.type === "limit" ? `limit ${body.limit_price}` : "market"}`
      + (body.stop ? `, stop ${body.stop}` : "") + (body.target ? `, target ${body.target}` : "");
    act.run("/api/positions/order", body, `${what}?`);
  }
  return (
    <Card title="New position" className="mt">
      <form onSubmit={submit} className="row" style={{ alignItems: "flex-end" }}>
        <label>Symbol<br /><input required value={f.symbol} onChange={(e) => set("symbol", e.target.value)} placeholder="AAPL" style={{ width: 90, textTransform: "uppercase" }} /></label>
        <div>Side<br /><div className="seg">
          {["long", "short"].map((s) => <button type="button" key={s} className={cls(f.side === s && "on")} disabled={s === "short" && !data.allow_short}
            title={s === "short" && !data.allow_short ? "Shorting is off in the risk controls" : undefined} onClick={() => set("side", s)}>{s}</button>)}
        </div></div>
        <label>Qty<br /><input required type="number" min={1} step={1} value={f.qty} onChange={(e) => set("qty", e.target.value)} style={{ width: 90 }} /></label>
        <div>Type<br /><div className="seg">
          {["market", "limit"].map((s) => <button type="button" key={s} className={cls(f.type === s && "on")} onClick={() => set("type", s)}>{s}</button>)}
        </div></div>
        {f.type === "limit" && <label>Limit<br /><input required type="number" min={0.01} step={0.01} value={f.limit_price} onChange={(e) => set("limit_price", e.target.value)} style={{ width: 100 }} /></label>}
        <label>Stop <span className="muted">(optional)</span><br /><input type="number" min={0.01} step={0.01} value={f.stop} onChange={(e) => set("stop", e.target.value)} style={{ width: 100 }} /></label>
        <label>Target <span className="muted">(optional)</span><br /><input type="number" min={0.01} step={0.01} value={f.target} onChange={(e) => set("target", e.target.value)} style={{ width: 100 }} /></label>
        <label>Good for<br /><select value={f.tif} onChange={(e) => set("tif", e.target.value)}><option value="day">today</option><option value="gtc">until cancelled</option></select></label>
        <button className="primary" disabled={act.busy || data.kill}>Place order</button>
      </form>
      <p className="muted" style={{ fontSize: 12, marginBottom: 0 }}>
        {notional ? `About ${money(notional)}. ` : ""}Checked against buying power, max position size and gross exposure. Manual positions close at the
        flatten deadline like intraday trades unless you tick &ldquo;hold overnight&rdquo;; the kill switch always closes them.
        {!data.market_open && " The market is closed: only limit orders are accepted."}
      </p>
    </Card>
  );
}

function PositionRow({ p, owner, act }: { p: any; owner: boolean; act: ReturnType<typeof useAction> }) {
  const [mode, setMode] = useState<"" | "edit" | "partial">("");
  const [stop, setStop] = useState(p.stop ?? "");
  const [target, setTarget] = useState(p.target ?? "");
  const [qty, setQty] = useState("");
  const val = (v: any) => (v === "" || v == null ? null : Number(v));
  const sleeve = p.owner === "sleeve";
  return (
    <>
      <tr>
        <td><b>{p.symbol}</b></td>
        <td><span className="tag">{OWNER_LABEL[p.owner]}</span>{p.sid && p.owner !== "manual" && <><br /><span className="muted">{p.sid}</span></>}
          {p.hold && <><br /><span className="tag">held overnight</span></>}</td>
        <td>{p.side}</td><td className="num">{num(p.qty, 0)}</td><td className="num">{money(p.avg_price)}</td><td className="num">{money(p.last_price)}</td>
        <td className={`num ${pnlClass(p.upnl)}`}>{money(p.upnl, true)}</td>
        <td className="num">{p.stop != null ? money(p.stop) : "—"}</td><td className="num">{p.target != null ? money(p.target) : "—"}</td>
        <td><div className="row" style={{ gap: 2, minWidth: 170 }}>{owner && <>
          <button className="danger" disabled={act.busy} onClick={() => act.run(`/api/positions/${p.symbol}/close`, {},
            `Close all ${p.qty} ${p.symbol}${sleeve ? " (sold out of the portfolio sleeve)" : ""}?`)}>Close</button>{" "}
          <button className="link" onClick={() => setMode(mode === "partial" ? "" : "partial")}>Partial</button>
          {!sleeve && p.owner !== "unmanaged" && <button className="link" onClick={() => setMode(mode === "edit" ? "" : "edit")}>Stop/target</button>}
          {p.owner === "unmanaged" && <button className="link" disabled={act.busy} onClick={() => act.run(`/api/positions/${p.symbol}/adopt`)}>Adopt</button>}
          {p.owner === "manual" && <label className="row" style={{ display: "inline-flex", gap: 4, fontSize: 12 }}>
            <input type="checkbox" checked={p.hold} disabled={act.busy} onChange={(e) => act.run(`/api/positions/${p.symbol}/hold`, { on: e.target.checked })} />hold overnight</label>}
        </>}</div></td>
      </tr>
      {mode === "partial" && (
        <tr><td colSpan={10}>
          <form className="row" onSubmit={(e) => { e.preventDefault(); act.run(`/api/positions/${p.symbol}/close`, { qty: Number(qty) }, `Close ${qty} of ${p.qty} ${p.symbol}?`).then(() => setMode("")); }}>
            <span>Close</span><input required type="number" min={1} max={p.qty} step={1} value={qty} onChange={(e) => setQty(e.target.value)} style={{ width: 90 }} />
            <span className="muted">of {num(p.qty, 0)} shares at market{!sleeve && p.stop != null ? "; the stop/target is re-placed on the rest" : ""}</span>
            <button className="primary" disabled={act.busy}>Close shares</button><button type="button" className="link" onClick={() => setMode("")}>Cancel</button>
          </form>
        </td></tr>
      )}
      {mode === "edit" && (
        <tr><td colSpan={10}>
          <form className="row" onSubmit={(e) => { e.preventDefault(); act.run(`/api/positions/${p.symbol}/protect`, { stop: val(stop), target: val(target) }).then(() => setMode("")); }}>
            <label>Stop <input type="number" min={0.01} step={0.01} value={stop} onChange={(e) => setStop(e.target.value)} style={{ width: 100 }} /></label>
            <label>Target <input type="number" min={0.01} step={0.01} value={target} onChange={(e) => setTarget(e.target.value)} style={{ width: 100 }} /></label>
            <span className="muted">Leave a box empty to remove it. Orders are good till cancelled; the old ones are cancelled first.</span>
            <button className="primary" disabled={act.busy}>Save</button><button type="button" className="link" onClick={() => setMode("")}>Cancel</button>
          </form>
        </td></tr>
      )}
    </>
  );
}

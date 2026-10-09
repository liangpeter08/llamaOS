"use client";
import Link from "next/link";
import { usePoll } from "@/lib/hooks";
import { et, money, num, pct, pnlClass } from "@/lib/format";
import { Card, Empty, Loading } from "@/components/ui";
import { PerformanceChart } from "@/components/PerformanceChart";

export default function Portfolio() {
  const { data, error } = usePoll<any>("/api/portfolio", 3000);
  if (!data) return <Loading error={error} />;
  const { acct, risk } = data;
  const group = (g: Record<string, { n: number; pnl: number; wins: number }>) => Object.entries(g || {});
  return (
    <>
      <h1>Portfolio</h1>
      <p className="muted">
        Equity {money(acct.equity)} · day P&amp;L <span className={pnlClass(acct.day_pnl)}>{money(acct.day_pnl, true)}</span> · exposure{" "}
        {money(acct.gross_exposure)} of {money(risk.max_gross_exposure * (acct.equity ?? 0))} cap
      </p>
      <PerformanceChart title="Performance by strategy" defaultRange="1M" />
      <div className="grid g2 mt">
        <Card title="Positions & bracket legs">
          {data.positions.length ? (
            <div className="table-wrap"><table>
              <thead><tr><th>Symbol</th><th>Side</th><th className="num">Qty</th><th className="num">Avg</th><th className="num">Last</th><th className="num">Stop leg</th><th className="num">Target leg</th><th className="num">Notional / cap</th></tr></thead>
              <tbody>{data.positions.map((p: any) => (
                <tr key={p.symbol}><td><b>{p.symbol}</b><br /><span className="muted">{p.sid}</span></td><td>{p.side}</td><td className="num">{p.qty}</td>
                  <td className="num">{money(p.avg_price)}</td><td className="num">{money(p.last_price)}</td><td className="num">{money(p.stop)}</td><td className="num">{money(p.target)}</td>
                  <td className="num">{pct((p.qty * p.last_price) / (acct.equity || 1), 1)} / {pct(risk.max_position, 0)}</td></tr>
              ))}</tbody>
            </table></div>
          ) : <Empty>Flat.</Empty>}
        </Card>
        <Card title="P&L by strategy">
          <table>
            <thead><tr><th>Strategy</th><th className="num">Trades</th><th className="num">Win rate</th><th className="num">P&amp;L</th></tr></thead>
            <tbody>
              {group(data.by.strategy).map(([k, v]) => (
                <tr key={k}><td>{k}</td><td className="num">{v.n}</td><td className="num">{pct(v.wins / v.n, 0)}</td><td className={`num ${pnlClass(v.pnl)}`}>{money(v.pnl, true)}</td></tr>
              ))}
              {!group(data.by.strategy).length && <tr><td colSpan={4} className="muted">No trades yet.</td></tr>}
            </tbody>
          </table>
          <h3>By symbol</h3>
          <table><tbody>
            {group(data.by.symbol).map(([k, v]) => (
              <tr key={k}><td>{k}</td><td className="num">{v.n}</td><td className={`num ${pnlClass(v.pnl)}`}>{money(v.pnl, true)}</td></tr>
            ))}
          </tbody></table>
        </Card>
      </div>
      <Card title="Closed trades" className="mt">
        <div className="table-wrap"><table>
          <thead><tr><th>#</th><th>Strategy</th><th>Symbol</th><th className="num">Qty</th><th>In</th><th className="num">Entry</th><th>Out</th><th className="num">Exit</th><th>How</th><th className="num">P&amp;L</th><th className="num">R</th></tr></thead>
          <tbody>
            {data.trades.map((t: any) => (
              <tr key={t.id}><td><Link href={`/trades/${t.id}`}>{t.id}</Link></td><td>{t.sid}</td><td>{t.symbol}</td><td className="num">{t.qty}</td>
                <td className="mono">{et(t.entry_ts)}</td><td className="num">{money(t.entry_price)}</td><td className="mono">{et(t.exit_ts)}</td><td className="num">{money(t.exit_price)}</td>
                <td>{t.exit_kind}</td><td className={`num ${pnlClass(t.pnl)}`}>{money(t.pnl, true)}</td><td className="num">{num(t.r_multiple, 2, true)}</td></tr>
            ))}
            {!data.trades.length && <tr><td colSpan={11} className="muted">No closed trades this session.</td></tr>}
          </tbody>
        </table></div>
      </Card>
      <div className="grid g2 mt">
        <Card title="Orders">
          <div className="table-wrap"><table>
            <thead><tr><th>#</th><th>Sym</th><th>Side</th><th>Kind</th><th className="num">Qty</th><th className="num">Limit</th><th>Status</th><th className="num">Avg</th></tr></thead>
            <tbody>{data.orders.map((o: any) => (
              <tr key={o.id}><td>{o.id}</td><td>{o.symbol}</td><td>{o.side}</td><td>{o.kind}</td><td className="num">{o.qty}</td><td className="num">{money(o.limit_price)}</td><td>{o.status}</td><td className="num">{money(o.avg_price)}</td></tr>
            ))}</tbody>
          </table></div>
        </Card>
        <Card title="Fills">
          <div className="table-wrap"><table>
            <thead><tr><th>Time</th><th>Sym</th><th>Side</th><th className="num">Qty</th><th className="num">Price</th><th>Kind</th></tr></thead>
            <tbody>{data.fills.map((f: any) => (
              <tr key={f.id}><td className="mono">{et(f.ts)}</td><td>{f.symbol}</td><td>{f.side}</td><td className="num">{f.qty}</td><td className="num">{money(f.price)}</td><td>{f.kind}</td></tr>
            ))}</tbody>
          </table></div>
        </Card>
      </div>
    </>
  );
}

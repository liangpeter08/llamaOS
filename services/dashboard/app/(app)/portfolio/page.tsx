"use client";
import Link from "next/link";
import { usePoll } from "@/lib/hooks";
import { et, money, num, pct, pnlClass } from "@/lib/format";
import { Card, Empty, Loading } from "@/components/ui";
import { PerformanceChart } from "@/components/PerformanceChart";
import { useT } from "@/lib/i18n";

export default function Portfolio() {
  const { data, error } = usePoll<any>("/api/portfolio", 3000);
  const t = useT();
  if (!data) return <Loading error={error} />;
  const { acct, risk } = data;
  const group = (g: Record<string, { n: number; pnl: number; wins: number }>) => Object.entries(g || {});
  return (
    <>
      <h1>{t("Portfolio")}</h1>
      <p className="muted">
        {t("Equity {eq} · day P&L {pnl} · exposure {exp} of {cap} cap", {
          eq: money(acct.equity), pnl: <span className={pnlClass(acct.day_pnl)}>{money(acct.day_pnl, true)}</span>,
          exp: money(acct.gross_exposure), cap: money(risk.max_gross_exposure * (acct.equity ?? 0)),
        })}
      </p>
      <PerformanceChart title={t("Performance by strategy")} defaultRange="1M" />
      <div className="grid g2 mt">
        <Card title={t("Positions & bracket legs")}>
          {data.positions.length ? (
            <div className="table-wrap"><table>
              <thead><tr><th>{t("Symbol")}</th><th>{t("Side")}</th><th className="num">{t("Qty")}</th><th className="num">{t("Avg")}</th><th className="num">{t("Last")}</th><th className="num">{t("Stop leg")}</th><th className="num">{t("Target leg")}</th><th className="num">{t("Notional / cap")}</th></tr></thead>
              <tbody>{data.positions.map((p: any) => (
                <tr key={p.symbol}><td><b>{p.symbol}</b><br /><span className="muted">{p.sid}</span></td><td>{t(p.side)}</td><td className="num">{p.qty}</td>
                  <td className="num">{money(p.avg_price)}</td><td className="num">{money(p.last_price)}</td><td className="num">{money(p.stop)}</td><td className="num">{money(p.target)}</td>
                  <td className="num">{pct((p.qty * p.last_price) / (acct.equity || 1), 1)} / {pct(risk.max_position, 0)}</td></tr>
              ))}</tbody>
            </table></div>
          ) : <Empty>{t("Flat.")}</Empty>}
        </Card>
        <Card title={t("P&L by strategy")}>
          <table>
            <thead><tr><th>{t("Strategy")}</th><th className="num">{t("Trades")}</th><th className="num">{t("Win rate")}</th><th className="num">{t("P&L")}</th></tr></thead>
            <tbody>
              {group(data.by.strategy).map(([k, v]) => (
                <tr key={k}><td>{k}</td><td className="num">{v.n}</td><td className="num">{pct(v.wins / v.n, 0)}</td><td className={`num ${pnlClass(v.pnl)}`}>{money(v.pnl, true)}</td></tr>
              ))}
              {!group(data.by.strategy).length && <tr><td colSpan={4} className="muted">{t("No trades yet.")}</td></tr>}
            </tbody>
          </table>
          <h3>{t("By symbol")}</h3>
          <table><tbody>
            {group(data.by.symbol).map(([k, v]) => (
              <tr key={k}><td>{k}</td><td className="num">{v.n}</td><td className={`num ${pnlClass(v.pnl)}`}>{money(v.pnl, true)}</td></tr>
            ))}
          </tbody></table>
        </Card>
      </div>
      <Card title={t("Closed trades")} className="mt">
        <div className="table-wrap"><table>
          <thead><tr><th>#</th><th>{t("Strategy")}</th><th>{t("Symbol")}</th><th className="num">{t("Qty")}</th><th>{t("In")}</th><th className="num">{t("Entry")}</th><th>{t("Out")}</th><th className="num">{t("Exit")}</th><th>{t("How")}</th><th className="num">{t("P&L")}</th><th className="num">R</th></tr></thead>
          <tbody>
            {data.trades.map((tr: any) => (
              <tr key={tr.id}><td><Link href={`/trades/${tr.id}`}>{tr.id}</Link></td><td>{tr.sid}</td><td>{tr.symbol}</td><td className="num">{tr.qty}</td>
                <td className="mono">{et(tr.entry_ts)}</td><td className="num">{money(tr.entry_price)}</td><td className="mono">{et(tr.exit_ts)}</td><td className="num">{money(tr.exit_price)}</td>
                <td>{t(tr.exit_kind)}</td><td className={`num ${pnlClass(tr.pnl)}`}>{money(tr.pnl, true)}</td><td className="num">{num(tr.r_multiple, 2, true)}</td></tr>
            ))}
            {!data.trades.length && <tr><td colSpan={11} className="muted">{t("No closed trades this session.")}</td></tr>}
          </tbody>
        </table></div>
      </Card>
      <div className="grid g2 mt">
        <Card title={t("Orders")}>
          <div className="table-wrap"><table>
            <thead><tr><th>#</th><th>{t("Sym")}</th><th>{t("Side")}</th><th>{t("Kind")}</th><th className="num">{t("Qty")}</th><th className="num">{t("Limit")}</th><th>{t("Status")}</th><th className="num">{t("Avg")}</th></tr></thead>
            <tbody>{data.orders.map((o: any) => (
              <tr key={o.id}><td>{o.id}</td><td>{o.symbol}</td><td>{t(o.side)}</td><td>{t(o.kind)}</td><td className="num">{o.qty}</td><td className="num">{money(o.limit_price)}</td><td>{t(o.status)}</td><td className="num">{money(o.avg_price)}</td></tr>
            ))}</tbody>
          </table></div>
        </Card>
        <Card title={t("Fills")}>
          <div className="table-wrap"><table>
            <thead><tr><th>{t("Time")}</th><th>{t("Sym")}</th><th>{t("Side")}</th><th className="num">{t("Qty")}</th><th className="num">{t("Price")}</th><th>{t("Kind")}</th></tr></thead>
            <tbody>{data.fills.map((f: any) => (
              <tr key={f.id}><td className="mono">{et(f.ts)}</td><td>{f.symbol}</td><td>{t(f.side)}</td><td className="num">{f.qty}</td><td className="num">{money(f.price)}</td><td>{t(f.kind)}</td></tr>
            ))}</tbody>
          </table></div>
        </Card>
      </div>
    </>
  );
}

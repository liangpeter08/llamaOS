"use client";
import { useParams } from "next/navigation";
import { usePoll } from "@/lib/hooks";
import { et, money, num, pnlClass } from "@/lib/format";
import { LineChart } from "@/components/LineChart";
import { Chat } from "@/components/Chat";
import { Card, Checks, Conditions, Empty, Loading } from "@/components/ui";

export default function TradePage() {
  const { id } = useParams<{ id: string }>();
  const { data, error } = usePoll<any>(`/api/trades/${id}`);
  if (!data) return <Loading error={error} />;
  const { trade: t, intent: it, fills, bars } = data;
  const closes = bars.map((b: any) => b.c);
  const idx = (ts: number) => bars.findIndex((b: any) => b.ts >= ts);
  return (
    <>
      <h1>
        Trade #{t.id} · {t.side} {t.qty} {t.symbol} <span className={pnlClass(t.pnl)}>{money(t.pnl, true)}</span>
      </h1>
      <p className="muted">
        {t.sid} · {et(t.entry_ts)} → {et(t.exit_ts)} ET · exit via {t.exit_kind}
        {t.r_multiple != null && ` · ${num(t.r_multiple, 2, true)}R`}
      </p>
      <div className="grid g2">
        <Card title="Price">
          <LineChart values={closes} width={640} height={200}
            levels={{ entry: t.entry_price, stop: it?.stop, target: it?.target }}
            marks={[{ index: idx(t.entry_ts), kind: "entry" }, { index: idx(t.exit_ts), kind: "exit" }]} />
          <h3>Narrative</h3>
          <p>{t.narrative}</p>
          <h3>Fills</h3>
          <table><tbody>
            {fills.map((f: any) => (
              <tr key={f.id}><td className="mono">{et(f.ts)}</td><td>{f.side}</td><td className="num">{f.qty}</td><td className="num">{money(f.price)}</td><td>{f.kind}</td></tr>
            ))}
          </tbody></table>
        </Card>
        <Card title="Decision record">
          {it ? (
            <>
              <p className="muted">Intent #{it.id} · {it.status} · decided by {it.decided_by ?? "auto"}{it.reason && ` (${it.reason})`}</p>
              <Conditions items={it.decision?.entry} />
              <h3>Signals</h3>
              <table><tbody>
                {Object.entries(it.signals || {}).map(([name, s]: [string, any]) => (
                  <tr key={name}><td><code>{name}</code></td><td>{String(s.value)}</td><td className="muted">{s.model} {s.tier}</td><td className="muted">{s.rationale}</td></tr>
                ))}
              </tbody></table>
              <h3>Risk checks</h3>
              <Checks items={it.guard} />
              {it.recheck && (<><h3>Re-check at approval</h3><Checks items={it.recheck} /></>)}
            </>
          ) : <Empty>No intent recorded.</Empty>}
        </Card>
      </div>
      <Card title="Ask Kimi about this trade" className="mt" id="chat">
        <Chat prefix={`trade:${t.id}`} intro="A new conversation seeded with this trade, its decision record and fills." />
      </Card>
    </>
  );
}

"use client";
import Link from "next/link";
import { useState } from "react";
import { post } from "@/lib/api";
import { usePoll } from "@/lib/hooks";
import { useSession, isOwner } from "@/components/session";
import { Card, Loading, Notice, Pill } from "@/components/ui";

export default function Watchlist() {
  const [query,setQuery]=useState("");
  const {data,error,reload}=usePoll<any>(`/api/watchlist?q=${encodeURIComponent(query)}`,3000);
  const {me}=useSession();
  const [message,setMessage]=useState("");
  const [busy,setBusy]=useState(false);
  async function toggle(symbol:string,enabled:boolean,dependencies:any[]=[],held=false) {
    const confirmed=!enabled && (dependencies.length>0 || held);
    if(confirmed && !window.confirm(`Turn off monitoring for ${symbol}? ${dependencies.length ? "Strategies affected: "+dependencies.map(x=>x.sid).join(", ")+". " : ""}New entries will be blocked. Held positions keep price and news monitoring for risk management.`)) return;
    setBusy(true);
    try {const r=await post(`/api/watchlist/${encodeURIComponent(symbol)}`,{enabled,confirmed});setMessage(r.message);reload();}
    catch(e) {setMessage((e as Error).message);}
    finally {setBusy(false);}
  }
  if(!data) return <Loading error={error}/>;
  return <>
    <h1>Monitoring watchlist</h1>
    <p className="muted">Choose the stocks monitored for prices, news and new trading signals. Promoting a strategy enables its required tickers. Held positions keep risk monitoring even when you turn off new-entry monitoring.</p>
    {message && <Notice>{message}</Notice>}
    <Card title="Find a ticker">
      <input aria-label="Search tickers or company names" placeholder="Search ticker or company name, e.g. AAPL or Apple" value={query} onChange={e=>setQuery(e.target.value)} style={{width:"100%"}}/>
      {!data.catalog_ready && <p className="muted">Ticker catalog is loading from the broker. Try again shortly.</p>}
      {query && data.catalog_ready && !data.results.length && <p className="muted">No matching tradable tickers.</p>}
      {data.results.map((a:any)=>{const item=data.items.find((x:any)=>x.symbol===a.symbol);return <div className="row between" key={a.symbol} style={{padding:"8px 0"}}><span><b>{a.symbol}</b> · {a.name} <span className="muted">{a.exchange}</span></span><button disabled={busy || !isOwner(me) || item?.enabled} onClick={()=>toggle(a.symbol,true)}>{item?.enabled ? "Monitoring on" : "Enable monitoring"}</button></div>;})}
    </Card>
    <Card title={`Watchlist · ${data.items.filter((x:any)=>x.enabled).length}/${data.max_symbols} enabled`} className="mt">
      <div className="table-wrap"><table><thead><tr><th>Ticker</th><th>Monitoring</th><th>Strategies using ticker</th><th>Feed</th><th>Action</th></tr></thead><tbody>
      {data.items.map((x:any)=><tr key={x.symbol}><td><b>{x.symbol}</b>{x.held && <div className="muted">Held · risk monitoring retained</div>}</td><td><Pill kind={x.enabled ? "good" : "warn"}>{x.enabled ? "On" : "Off"}</Pill></td><td>{x.strategies.map((s:any)=><div key={s.hash}><Link href={`/strategies/${s.hash}`}>{s.sid}</Link></div>)}{!x.strategies.length && <span className="muted">No active strategy</span>}</td><td className="muted">{data.applied.includes(x.symbol) ? "Applied" : x.enabled ? "Waiting for broker" : "Inactive"}</td><td><button disabled={busy || !isOwner(me)} onClick={()=>toggle(x.symbol,!x.enabled,x.strategies,x.held)}>{x.enabled ? "Turn off" : "Turn on"}</button></td></tr>)}
      {!data.items.length && <tr><td colSpan={5}>Search above to add your first ticker.</td></tr>}
      </tbody></table></div>
    </Card>
  </>;
}

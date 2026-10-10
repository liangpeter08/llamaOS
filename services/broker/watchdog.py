"""Independent paper safety process: wall-clock stale feed and calendar-aware flatten."""
import logging
import signal
import time
from datetime import datetime

from common import audit,db,timeutil as T
from common.config import load_risk
from .alpaca import Alpaca,epoch


def main():
    from scripts.env import load,ROOT
    load(ROOT/"secrets/broker.env")
    log=logging.getLogger('watchdog')
    api=Alpaca();c=db.get();db.init(c)
    from .paper import SCHEMA
    c.script(SCHEMA)
    risk=load_risk();stopping=False
    def stop(*_):
        nonlocal stopping
        stopping=True
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    while not stopping:
        try:
            clock=api.clock();now=epoch(clock['timestamp'])
            day=T.et(now).date()
            if clock['is_open']:
                calendar=api.calendar(day,day)
                close=int(datetime.fromisoformat(f"{day}T{calendar[0]['close']}").replace(tzinfo=T.ET).timestamp())
                deadline=min(T.ts_at(day,risk.flatten_by),close-300)
                age=db.kv_age(c,'health.broker')
                bar=db.kv_get(c,'feed.last_bar') or 0
                symbol_bars=db.kv_get(c,"feed.symbols") or {}
                positions=api.positions()
                reason=None
                if now>=deadline:reason='calendar flatten deadline'
                elif now>T.open_ts(day)+risk.stale_feed_halt and (age is None or age>60):reason='broker heartbeat stale'
                elif now>T.open_ts(day)+risk.stale_feed_halt and (now-bar>risk.stale_feed_halt or any(now-symbol_bars.get(p['symbol'],0)>risk.stale_feed_halt for p in positions)):reason='market feed stale'
                ks=db.kv_get(c,'kill_switch') or {}
                if reason or ks.get('active'):
                    if reason and reason!='calendar flatten deadline' and not ks.get('active'):
                        db.kv_set(c,'kill_switch',{'active':True,'reason':reason,'ts':time.time(),'by':'watchdog'})
                        audit.log(c,'kill_switch.triggered','watchdog',{'reason':reason})
                    orders=api.orders()
                    closing=any(o.get('type')=='market' and now-epoch(o['created_at'])<60 for o in orders)
                    if (positions or orders) and not closing:
                        # Alpaca bulk endpoint cancels bracket reservations before closing.
                        import json
                        results=api.flatten() or []
                        for result in results:
                            order=result.get("body") or {}
                            if order.get("id"):
                                c.execute("INSERT INTO paper_external_exits(broker_id,body,reason) VALUES (?,?,?) ON CONFLICT(broker_id) DO NOTHING",
                                          (order["id"],json.dumps(order),"watchdog"))
                        audit.log(c,'paper.watchdog_flatten','watchdog',{'reason':reason or ks.get('reason')})
            db.kv_set(c,'health.watchdog',{'ts':time.time()})
        except Exception as e:
            log.error('Watchdog retry required: %s',type(e).__name__)
            db.kv_set(c,'watchdog.error',{'type':type(e).__name__,'ts':time.time()})
        for _ in range(10):
            if stopping:break
            time.sleep(1)

if __name__=='__main__':main()

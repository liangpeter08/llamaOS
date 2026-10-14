"""Independent paper safety process: wall-clock stale feed and calendar-aware flatten."""
import logging
import signal
import time
from datetime import datetime

import httpx

from common import audit,db,manual,sleeves,timeutil as T,broker_accounts as BA
from common.config import load_risk
from .alpaca import Alpaca,epoch


def main():
    from scripts.env import load,ROOT
    load(ROOT/"secrets/broker.env")
    log=logging.getLogger('watchdog')
    c=db.get();db.init(c)
    since=BA.rev(c)
    while True:  # no usable account yet: wait for the owner to add one on the dashboard
        load(ROOT/"secrets/broker.env")  # the broker may have just generated BROKER_VAULT_KEY
        try:
            api=Alpaca();api.clock();db.pin(api.schema);break
        except (ValueError,httpx.HTTPStatusError) as e:
            db.kv_set(c,'watchdog.error',{'type':type(e).__name__,'ts':time.time()})
            for _ in range(10):
                if BA.rev(c)!=since:raise SystemExit(BA.RESTART)
                time.sleep(1)
    from .paper import SCHEMA
    c.script(SCHEMA)
    risk=load_risk();stopping=False
    def stop(*_):
        nonlocal stopping
        stopping=True
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    while not stopping:
        if BA.rev(c)!=api.account_rev:
            log.info('Alpaca account switched; restarting on the new account')
            raise SystemExit(BA.RESTART)
        try:
            risk=load_risk()  # the owner may change controls on the dashboard at any time
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
                # Portfolio sleeves hold overnight and aren't on the 1-minute feed: the deadline and
                # stale-feed checks cover intraday positions only. A kill still closes everything.
                sleeve=set(sleeves.owned_symbols(c))
                held=set(manual.holds(c))  # manual positions the owner holds overnight (a kill still closes them)
                intraday=[p for p in positions if p['symbol'] not in sleeve and p['symbol'] not in held]
                # Stale heartbeat/feed are warnings only: the kill switch is set by the owner, never by the watchdog.
                late=now>T.open_ts(day)+risk.stale_feed_halt
                problems={'heartbeat':late and (age is None or age>60),
                          'feed':late and (now-bar>risk.stale_feed_halt or any(now-symbol_bars.get(p['symbol'],0)>risk.stale_feed_halt for p in intraday))}
                for name,bad in problems.items():
                    key=f'watchdog.{name}_stale';warned=db.kv_get(c,key)
                    if bad and not warned:
                        log.warning('%s stale; not triggering the kill switch',name)
                        db.kv_set(c,key,{'ts':time.time(),'heartbeat_age':age,'last_bar':bar})
                        audit.log(c,f'watchdog.{name}_stale','watchdog',{'heartbeat_age':age,'last_bar':bar})
                    elif warned and not bad:
                        db.kv_set(c,key,None)
                        audit.log(c,f'watchdog.{name}_recovered','watchdog',{})
                reason='calendar flatten deadline' if now>=deadline else None
                ks=db.kv_get(c,'kill_switch') or {}
                if reason or ks.get('active'):
                    orders=api.orders()
                    import json
                    if not ks.get('active') and reason=='calendar flatten deadline':
                        orders=[o for o in orders if o['symbol'] not in sleeve and o['symbol'] not in held]
                        closing=any(o.get('type')=='market' and now-epoch(o['created_at'])<60 for o in orders)
                        if (intraday or orders) and not closing:
                            for o in orders:api.cancel(o['id'])
                            for p in intraday:
                                try:order=api.close_position(p['symbol'])
                                except Exception:continue  # shares still reserved by a cancelling bracket: retry next pass
                                if order and order.get("id"):
                                    c.execute("INSERT INTO paper_external_exits(broker_id,body,reason) VALUES (?,?,?) ON CONFLICT(broker_id) DO NOTHING",
                                              (order["id"],json.dumps(order),"watchdog"))
                            audit.log(c,'paper.watchdog_flatten','watchdog',{'reason':reason,'kept_sleeves':sorted(sleeve)})
                        orders=positions=[]
                    closing=any(o.get('type')=='market' and now-epoch(o['created_at'])<60 for o in orders)
                    if (positions or orders) and not closing:
                        # Alpaca bulk endpoint cancels bracket reservations before closing.
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

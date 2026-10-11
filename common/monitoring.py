"""Persistent paper watchlist and strategy dependencies."""
import time
from common import db

MAX_SYMBOLS = 50


def ensure(c):
    from common.config import load_yaml
    with db.tx(c):
        c.execute('SELECT pg_advisory_xact_lock(?)',(0x5741544348,))
        if db.kv_get(c,'monitoring.migrated'):
            return
        for sym in load_yaml('universe.yaml').get('watchlist',[]):
            c.execute('INSERT INTO monitoring_watchlist VALUES (?,?,?,?) ON CONFLICT DO NOTHING',
                      (sym,True,time.time(),'migration'))
        db.kv_set(c,'monitoring.migrated',True)


def rows(c):
    ensure(c)
    return db.rows(c,'SELECT * FROM monitoring_watchlist ORDER BY symbol')


def enabled(c):
    return [r['symbol'] for r in rows(c) if r['enabled']]


def held(c):
    from common import sleeves
    return sorted({r['symbol'] for r in db.rows(c,'SELECT symbol FROM positions')} |
                  {sym for r in db.rows(c,'SELECT strategy_hash FROM portfolio_state')
                   for sym,p in sleeves.holdings(c,r['strategy_hash'])[0].items() if p.qty>0})


def required(c,s):
    return sorted({*s.symbols,*getattr(s,'input_symbols',[])}) if s.kind=='portfolio' else [r['symbol'] for r in rows(c)]


def coverage(c,s):
    required_symbols=required(c,s)
    on=set(enabled(c))
    return {'required':required_symbols,'unwatched':[x for x in required_symbols if x not in on]}


def dependencies(c,symbol):
    from common import registry
    from common.config import load_risk
    return [{'hash':s.hash,'sid':s.id} for r,s in registry.runnable(c,load_risk())
            if r['status']=='paper' and symbol in required(c,s)]


def set_enabled(c,symbol,on,actor):
    ensure(c)
    with db.tx(c):
        c.execute('SELECT pg_advisory_xact_lock(?)',(0x5741544348,))
        current=set(enabled(c))
        if on and symbol not in current and len(current)>=MAX_SYMBOLS:
            raise ValueError(f'At most {MAX_SYMBOLS} tickers can be enabled')
        c.execute('INSERT INTO monitoring_watchlist VALUES (?,?,?,?) ON CONFLICT(symbol) DO UPDATE SET enabled=excluded.enabled,updated_at=excluded.updated_at,updated_by=excluded.updated_by',
                  (symbol,on,time.time(),actor))


def activate(c,s,actor):
    symbols=required(c,s)
    with db.tx(c):
        c.execute('SELECT pg_advisory_xact_lock(?)',(0x5741544348,))
        if len(set(enabled(c))|set(symbols))>MAX_SYMBOLS:
            raise ValueError(f'Strategy requires more than {MAX_SYMBOLS} enabled tickers')
        for sym in symbols:
            set_enabled(c,sym,True,actor)
    return symbols

"""Initialize an isolated real-data paper database. Never seeds fictional market data."""
from __future__ import annotations
import json
import os
import secrets
import sys
import time
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from scripts.env import ROOT, load
load()
load(ROOT/'secrets/broker.env')
os.environ['BROKER_BACKEND']='alpaca'
os.environ['MODE']='paper'
# The simulator's existing database is intentionally left intact.
os.environ['DATABASE_URL']=os.environ.get('PAPER_DATABASE_URL','postgresql://llamaos:llamaos@127.0.0.1:5432/llamaos_paper_utf8')
from common import db, audit, registry, timeutil as T
from common.config import load_risk,universe
from services.broker.alpaca import Alpaca,epoch
from backtest import engine as bt
from services.api.create_owner import create


def main():
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict,make_conninfo
    parts=conninfo_to_dict(os.environ['DATABASE_URL']); name=parts['dbname']
    if 'paper' not in name.lower():raise ValueError('Paper database name must contain paper')
    admin=psycopg.connect(make_conninfo(**{**parts,'dbname':'postgres'}),autocommit=True)
    if not admin.execute('SELECT 1 FROM pg_database WHERE datname=%s',(name,)).fetchone():
        admin.execute(sql.SQL("CREATE DATABASE {} ENCODING 'UTF8' TEMPLATE template0").format(sql.Identifier(name)))
    admin.close()
    c=db.get();db.init(c);risk=load_risk();api=Alpaca();syms=universe()
    account=api.account()
    if account['status']!='ACTIVE' or account.get('trading_blocked'):raise RuntimeError('Paper account is unavailable')
    api.request('PATCH','/v2/account/configurations',json={'max_margin_multiplier':'1','no_shorting':not risk.allow_short})
    for sym in syms:
        if not api.asset(sym).get('tradable'):raise RuntimeError('Untradable symbol: '+sym)
    if not db.kv_get(c,'paper.history_loaded'):
        end=time.time()-120
        daily=api.bars(syms,'1Day',end-130*86400,end)
        minute=api.bars(syms,'1Min',end-35*86400,end)
        with db.tx(c):
            for sym,bars in daily.items():
                for b in bars:
                    d=str(T.et(epoch(b['t'])).date())
                    c.execute('INSERT INTO daily(symbol,day,o,h,l,c,v) VALUES (?,?,?,?,?,?,?) ON CONFLICT(symbol,day) DO NOTHING',
                              (sym,d,b['o'],b['h'],b['l'],b['c'],b['v']))
            for sym,bars in minute.items():
                for b in bars:
                    ts=epoch(b['t']);t=T.et(ts).time()
                    if T.OPEN<=t<T.CLOSE:
                        c.execute('INSERT INTO bars(symbol,ts,o,h,l,c,v) VALUES (?,?,?,?,?,?,?) ON CONFLICT(symbol,ts) DO NOTHING',
                                  (sym,ts,b['o'],b['h'],b['l'],b['c'],b['v']))
        db.kv_set(c,'paper.history_loaded',{'ts':time.time(),'feed':api.feed,'symbols':syms})
    registry.sync(c,risk)
    for row in db.rows(c,"SELECT * FROM strategies WHERE status='draft'"):
        s,why=registry.load(c,row['hash'],risk)
        if not s:
            print('Invalid strategy skipped:',row['sid']);continue
        report=bt.run(c,s,risk,starting_equity=float(account['equity']))
        report.update(ran_at=time.time(),ran_by='paper-setup',data_source='alpaca:'+api.feed)
        c.execute('UPDATE strategies SET backtest=? WHERE hash=?',(json.dumps(report,default=str),row['hash']))
        registry.set_status(c,row['hash'],'paper','host:paper-setup',paper_started_at=time.time())
        c.execute("UPDATE strategies SET mode='paper_auto' WHERE hash=?",(row['hash'],))
        print(row['sid']+': validated, real-data backtest saved, paper automation enabled')
    if not db.row(c,'SELECT id FROM users LIMIT 1'):
        password=secrets.token_urlsafe(24)
        secret=create(c,'paper-owner',password,'owner')
        p=ROOT/'secrets/paper-owner.txt'
        p.write_text('username: paper-owner\npassword: '+password+'\ntotp_secret: '+secret+'\n')
        p.chmod(0o600)
        print('New owner credentials and authenticator enrollment saved in secrets/paper-owner.txt')
    p=ROOT/'secrets/paper.env'
    p.write_text('DATABASE_URL='+os.environ['DATABASE_URL']+'\nBROKER_BACKEND=alpaca\nMODE=paper\nSESSION_SECRET='+secrets.token_urlsafe(32)+'\n') if not p.exists() else None
    p.chmod(0o600)
    print('Paper database ready. Start with make paper.')

if __name__=='__main__':main()

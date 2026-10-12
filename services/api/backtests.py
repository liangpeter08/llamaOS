"""Authenticated endpoints for the backtesting research workspace."""
import csv
import io
import json
import time
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from backtest import workspace as W
from common import audit, db, registry
from common import market_inputs as MI
from common.config import load_risk
from dsl.portfolio_v2 import INDEXES

INDEX_LABELS = {'NASDAQ_COMPOSITE': 'Nasdaq Composite', 'NASDAQ_100': 'Nasdaq-100', 'SP_500': 'S&P 500',
                'DOW_JONES_INDUSTRIAL': 'Dow Jones Industrial', 'RUSSELL_2000': 'Russell 2000'}


def router(user, owner):
    r = APIRouter(prefix='/api/backtests')

    def fail(message, status=400):
        raise HTTPException(status, message)

    def saved(rid):
        row = db.row(db.get(), 'SELECT * FROM backtest_runs WHERE id=?', (rid,))
        if not row:
            fail('No such backtest run.', 404)
        for key in ('config', 'risk_snapshot', 'report'):
            row[key] = json.loads(row[key]) if row[key] else None
        return row

    @r.get('/catalog')
    def catalog(u=Depends(user)):
        c, risk = db.get(), load_risk()
        registry.sync(c, risk)
        strategies = db.rows(c, 'SELECT hash,sid,version,display_name,status,archived_at IS NOT NULL archived FROM strategies ORDER BY sid,version DESC')
        for s in strategies:
            strat, why = registry.load(c, s['hash'], risk)
            s.update(valid=bool(strat), reason=why, kind=getattr(strat, 'kind', 'intraday'), symbols=getattr(strat, 'symbols', []))
        return {'strategies': strategies, 'symbols': db.rows(c, 'SELECT symbol,COUNT(*) sessions,MIN(day) first,MAX(day) last FROM daily GROUP BY symbol ORDER BY symbol'),
                'indexes': [{'instrument': i, 'label': INDEX_LABELS.get(i, i), 'feed': i in MI.feeds(),
                             **db.row(c, 'SELECT COUNT(*) sessions, MIN(day) first, MAX(day) last FROM index_daily WHERE instrument=?', (i,))}
                            for i in INDEXES],
                'risk': json.loads(W.encode(asdict(risk))),
                'defaults': W.RunConfig.model_construct(strategy_hash='').model_dump(mode='json'),
                'capabilities': [
                    {'feature': 'Stored bars, recorded signals, frozen strategy and risk', 'status': 'supported'},
                    {'feature': 'Costs, date windows, benchmarks, presets, saved reports, exports', 'status': 'supported'},
                    {'feature': 'Chronological holdout, fixed-rule rolling windows, cost stress, daily bootstrap', 'status': 'supported'},
                    {'feature': 'Latency, trade-through limits and volume rejection cap', 'status': 'intraday only'},
                    {'feature': 'Daily portfolio execution', 'status': 'same-close approximation'},
                    {'feature': 'Feed/adjustment provenance and historical universe membership', 'status': 'unavailable in stored data'},
                    {'feature': 'Corporate-action cashflows, borrow, settlement and margin', 'status': 'not modeled'},
                    {'feature': 'Order-book queues, partial fills and tick-level market impact', 'status': 'requires finer data'},
                    {'feature': 'Parameter optimization, walk-forward fitting and deflated Sharpe', 'status': 'planned; not implemented'},
                    {'feature': 'Exchange holidays and early closes', 'status': 'fixed regular-session replay; calendar integration planned'},
                ]}

    @r.post('/preflight')
    def preflight(config: W.RunConfig, u=Depends(owner)):
        try:
            return W.preflight(db.get(), config)
        except ValueError as e:
            fail(str(e))

    @r.post('')
    def create(config: W.RunConfig, u=Depends(owner)):
        c = db.get()
        try:
            # Bound outstanding work across API threads; never enqueue unbounded experiments.
            with db.tx(c):
                c.execute('SELECT pg_advisory_xact_lock(108481)')
                if db.row(c, "SELECT COUNT(*) n FROM backtest_runs WHERE status IN ('queued','running')")['n'] >= 8:
                    fail('Eight backtests are already outstanding. Wait for completion or cancel one.', 429)
                rid = W.create_run(c, config, u['username'], submit=False)
            W.POOL.submit(W.execute, db.url(), rid)
            return {'ok': True, 'id': rid, 'message': 'Backtest queued; configuration and snapshots saved.'}
        except ValueError as e:
            fail(str(e))

    @r.get('')
    def history(strategy_hash: str = '', q: str = '', offset: int = 0, status: str = '', category: str = '',
                view: str = 'active', u=Depends(user)):
        c = db.get()
        # Heartbeats identify abandoned work after an API process restart.
        c.execute("UPDATE backtest_runs SET status='failed',error='Worker stopped responding; rerun from saved configuration.',finished_at=? WHERE (status='running' AND heartbeat<?) OR (status='queued' AND created_at<?)",
                  (time.time(), time.time() - 600, time.time() - 3600))
        where, args = [], []
        if strategy_hash:
            where.append('strategy_hash=?'); args.append(strategy_hash)
        if q:
            where.append('(name ILIKE ? OR sid ILIKE ? OR config ILIKE ?)'); args.extend([f'%{q[:120]}%'] * 3)
        if status:
            where.append('status=?'); args.append(status)
        if category == '-':
            where.append('category IS NULL')
        elif category:
            where.append('category=?'); args.append(category)
        if view == 'active':
            where.append('archived_at IS NULL')
        elif view == 'archived':
            where.append('archived_at IS NOT NULL')
        sql = ' WHERE ' + ' AND '.join(where) if where else ''
        total = db.row(c, 'SELECT COUNT(*) n FROM backtest_runs' + sql, args)['n']
        rows = db.rows(c, 'SELECT id,name,strategy_hash,sid,kind,status,created_at,finished_at,created_by,progress,error,config,metrics,sessions,parent_id,category,archived_at FROM backtest_runs' + sql + ' ORDER BY id DESC LIMIT 40 OFFSET ?', [*args, max(offset, 0)])
        for row in rows:
            row['metrics'] = json.loads(row['metrics'] or '{}')
            row['config'] = json.loads(row['config'])
        return {'rows': rows, 'total': total,
                'categories': [x['category'] for x in db.rows(c, 'SELECT DISTINCT category FROM backtest_runs WHERE category IS NOT NULL ORDER BY category')],
                'archived': db.row(c, 'SELECT COUNT(*) n FROM backtest_runs WHERE archived_at IS NOT NULL')['n']}

    class Meta(BaseModel):
        ids: list[int] = Field(min_length=1, max_length=500)
        category: str | None = Field(default=None, max_length=60)  # None leaves it unchanged; "" clears it
        archived: bool | None = None

    @r.post('/meta')
    def meta(body: Meta, u=Depends(owner)):
        c = db.get()
        sets, args, parts = [], [], []
        if body.category is not None:
            category = body.category.strip() or None
            sets.append('category=?'); args.append(category)
            parts.append(f'category {"set to " + category if category else "cleared"}')
        if body.archived is not None:
            sets.append('archived_at=?'); args.append(time.time() if body.archived else None)
            parts.append('archived' if body.archived else 'unarchived')
        if not sets:
            fail('Nothing to change.')
        n = c.execute(f'UPDATE backtest_runs SET {", ".join(sets)} WHERE id = ANY(?)', (*args, body.ids)).rowcount
        if not n:
            fail('No such backtest run.', 404)
        audit.log(c, 'backtest.meta', u['username'], {'ids': body.ids, 'category': body.category, 'archived': body.archived})
        return {'ok': True, 'message': f'{n} run{"s" * (n != 1)} ' + ' and '.join(parts) + '.'}

    @r.get('/presets')
    def presets(u=Depends(user)):
        rows = db.rows(db.get(), 'SELECT * FROM backtest_presets ORDER BY id DESC')
        for row in rows:
            row['config'] = json.loads(row['config'])
        return {'rows': rows}

    class Preset(BaseModel):
        name: str = Field(min_length=1, max_length=120)
        config: W.RunConfig

    @r.post('/presets')
    def save_preset(body: Preset, u=Depends(owner)):
        rid = db.get().insert('INSERT INTO backtest_presets(name,config,created_at,created_by) VALUES (?,?,?,?)',
                              (body.name, W.encode(body.config.model_dump(mode='json')), time.time(), u['username']))
        return {'ok': True, 'id': rid, 'message': 'Configuration preset saved.'}

    @r.get('/{rid}')
    def detail(rid: int, u=Depends(user)):
        return saved(rid)

    @r.post('/{rid}/cancel')
    def cancel(rid: int, u=Depends(owner)):
        row = saved(rid)
        if row['status'] not in ('queued', 'running'):
            fail('Only queued or running backtests can be cancelled.')
        db.get().execute('UPDATE backtest_runs SET cancel_requested=TRUE WHERE id=?', (rid,))
        return {'ok': True, 'message': 'Cancellation requested.'}

    @r.post('/{rid}/delete')
    def delete(rid: int, u=Depends(owner)):
        row = saved(rid)
        if row['status'] in ('queued', 'running'):
            fail('Cancel the run before deleting it.', 409)
        c = db.get()
        with db.tx(c):
            c.execute('UPDATE backtest_runs SET parent_id=NULL WHERE parent_id=?', (rid,))
            c.execute('DELETE FROM backtest_runs WHERE id=?', (rid,))
            audit.log(c, 'backtest.deleted', u['username'], {'id': rid, 'name': row['name'], 'strategy_hash': row['strategy_hash'], 'status': row['status']})
        return {'ok': True, 'message': f'Run #{rid} deleted permanently.'}

    @r.get('/{rid}/export')
    def export(rid: int, format: str = 'json', u=Depends(user)):
        row = saved(rid)
        if format == 'json':
            return Response(W.encode(row), media_type='application/json', headers={'Content-Disposition': f'attachment; filename="backtest-{rid}.json"'})
        if format != 'csv':
            fail('Use json or csv export.')
        columns = ['run_id', 'strategy_hash', 'day', 'symbol', 'side', 'qty', 'entry_ts', 'exit_ts', 'entry_price', 'exit_price', 'exit_kind', 'gross_pnl', 'fees', 'pnl', 'r_multiple']
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=columns, extrasaction='ignore')
        writer.writeheader()
        for trade in (row['report'] or {}).get('trades', []):
            writer.writerow({'run_id': rid, 'strategy_hash': row['strategy_hash'], **trade})
        return Response(output.getvalue(), media_type='text/csv', headers={'Content-Disposition': f'attachment; filename="backtest-{rid}-trades.csv"'})

    return r

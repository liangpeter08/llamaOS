"""Regression tests for replay correctness, accounting, snapshots and research APIs."""
import json
from datetime import date

import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient

from backtest import engine, workspace as W
from common import db, marketdata as MD, registry, timeutil as T
from core.exchange import SimExchange
from dsl.validator import validate
from services.api.app import app
from services.broker.seed import seed
from tests.test_api import login


def setup(conn, risk, orb_text, n=6):
    seed(conn, history_days=n, seed_=7)
    registry.sync(conn, risk)
    s = validate(orb_text, risk).strategy
    return s, MD.bar_days(conn)


def test_missing_ticker_does_not_erase_sessions(conn, risk, orb_text):
    s, days = setup(conn, risk, orb_text, 3)
    rep = engine.run(conn, s, risk, symbols=['ACMX', 'MISSING'])
    assert len(rep['days']) == 3
    assert rep['diagnostics']['processed_sessions'] == 3
    assert all('MISSING' in r['excluded'] for r in rep['coverage'])
    with pytest.raises(ValueError, match='missing bars'):
        engine.run(conn, s, risk, symbols=['ACMX', 'MISSING'], missing_data='strict')


def test_alignment_uses_timestamps_and_reports_actual_days(conn, risk, orb_text):
    s, days = setup(conn, risk, orb_text, 3)
    conn.execute('DELETE FROM bars WHERE symbol=? AND ts=?', ('BOLT', T.open_ts(days[0]) + 60))
    conn.execute('DELETE FROM bars WHERE ts>=? AND ts<?', (T.open_ts(days[-1]), T.close_ts(days[-1])))
    rep = engine.run(conn, s, risk, days=days, symbols=['ACMX', 'BOLT'])
    assert len(rep['days']) == 2 and len(rep['requested_days']) == 3
    assert rep['coverage'][0]['bars'] == 389
    assert rep['coverage'][0]['unaligned_bars'] == 1
    assert rep['diagnostics']['skipped_sessions'] == 1
    with pytest.raises(ValueError, match='not aligned'):
        engine.run(conn, s, risk, days=[days[0]], symbols=['ACMX', 'BOLT'], missing_data='strict')


def test_fill_fees_reconcile_to_cash_and_net_trade():
    ex = SimExchange(1000, slippage=0, commission_per_share=.1, commission_per_order=1, latency_bars=1)
    ex.submit_entry('ABC', 'long', 2, 101, 90, 120, 0)
    assert not ex.on_bar('ABC', 1, 60, 100, 101, 99, 100)
    entry = ex.on_bar('ABC', 2, 120, 100, 101, 99, 100)[0]
    assert entry.kind == 'entry' and entry.cash_after == pytest.approx(1000 - 200 - 1.2) and entry.exposure_after == 200
    exit_fill = ex.flatten_now('ABC', 110, 180, 'flatten')
    assert exit_fill.exposure_after == 0 and exit_fill.cash_after == pytest.approx(1017.6)
    trade = exit_fill.trade
    assert trade['gross_pnl'] == 20
    assert trade['fees'] == pytest.approx(2.4)
    assert trade['pnl'] == pytest.approx(17.6)
    assert ex.equity() == pytest.approx(1017.6)


def test_trade_through_limit_and_cancel_latency():
    ex = SimExchange(1000, slippage=0, limit_trade_through=True, latency_bars=1, cancel_after_bars=2)
    order = ex.submit_entry('ABC', 'long', 2, 100, 90, 110, 0)
    ex.on_bar('ABC', 2, 120, 101, 102, 100, 101)
    assert order.status == 'working'
    ex.on_bar('ABC', 3, 180, 101, 102, 100, 101)
    assert order.status == 'cancelled'
    ex2 = SimExchange(1000, slippage=0, limit_trade_through=True)
    ex2.submit_entry('ABC', 'long', 2, 100, 90, 110, 0)
    assert ex2.on_bar('ABC', 1, 60, 101, 102, 99, 101)[0].price == 100


def test_metric_conventions_and_initial_drawdown():
    m = engine.metrics([], 1000, [{'day': '1', 'equity': 900}, {'day': '2', 'equity': 950}, {'day': '3', 'equity': 1000}], .04)
    assert m['max_drawdown'] == pytest.approx(-.1)
    assert m['max_drawdown_sessions'] == 2
    assert m['total_pnl'] == 0 and m['cagr'] is None
    assert m['profit_factor'] is None and m['sharpe_daily'] is not None


def test_configuration_validation():
    with pytest.raises(ValidationError):
        W.RunConfig(strategy_hash='a' * 64, starting_equity=float('nan'))
    with pytest.raises(ValidationError):
        W.RunConfig(strategy_hash='a' * 64, start_date='2026-10-10', end_date='2026-10-01')
    with pytest.raises(ValidationError):
        W.RunConfig(strategy_hash='a' * 64, rolling_window=1)
    with pytest.raises(ValidationError):
        W.RunConfig(strategy_hash='a' * 64, unknown_switch=True)


def test_saved_runs_variants_snapshot_and_bootstrap(conn, risk, orb_text):
    s, days = setup(conn, risk, orb_text)
    config = W.RunConfig(strategy_hash=s.hash, name='Snapshot test', symbols=['ACMX'],
                         start_date=days[0], end_date=days[-1], commission_per_order=1,
                         holdout_pct=.5, rolling_window=5, cost_stress=True, bootstrap_samples=100)
    rid = W.create_run(conn, config, 'pytest', submit=False)
    W.execute(db.url(), rid)
    row = db.row(conn, 'SELECT * FROM backtest_runs WHERE id=?', (rid,))
    assert row['status'] == 'completed', row['error']
    report = json.loads(row['report'])
    assert len(report['validation']['holdout']) == 2
    assert len(report['validation']['cost_stress']) == 2
    assert len(report['validation']['rolling']) == 2
    assert report['validation']['holdout'][0]['days'][-1] < report['validation']['holdout'][1]['days'][0]
    assert report['bootstrap']['samples'] == 100
    assert row['strategy_text'] == orb_text
    assert W.risk_from(row['risk_snapshot']) == risk
    second = W.create_run(conn, config, 'pytest', submit=False)
    W.execute(db.url(), second)
    other = json.loads(db.row(conn, 'SELECT report FROM backtest_runs WHERE id=?', (second,))['report'])
    assert report['manifest'] == other['manifest']
    assert report['bootstrap'] == other['bootstrap']
    assert db.row(conn, 'SELECT COUNT(*) n FROM backtest_runs')['n'] == 2
    # A genuine data revision changes the run fingerprint.
    conn.execute('UPDATE bars SET v=v+1 WHERE symbol=? AND ts=?', ('ACMX', T.open_ts(days[0])))
    third = W.create_run(conn, config, 'pytest', submit=False)
    W.execute(db.url(), third)
    changed = json.loads(db.row(conn, 'SELECT report FROM backtest_runs WHERE id=?', (third,))['report'])
    assert changed['manifest']['data_hash'] != report['manifest']['data_hash']


def test_cancelled_and_failed_runs_remain_reviewable(conn, risk, orb_text):
    s, days = setup(conn, risk, orb_text, 2)
    config = W.RunConfig(strategy_hash=s.hash, symbols=['ACMX'])
    rid = W.create_run(conn, config, 'pytest', submit=False)
    conn.execute('UPDATE backtest_runs SET cancel_requested=TRUE WHERE id=?', (rid,))
    W.execute(db.url(), rid)
    assert db.row(conn, 'SELECT status FROM backtest_runs WHERE id=?', (rid,))['status'] == 'cancelled'
    rid = W.create_run(conn, config, 'pytest', submit=False)
    conn.execute('DELETE FROM bars')
    W.execute(db.url(), rid)
    row = db.row(conn, 'SELECT status,error FROM backtest_runs WHERE id=?', (rid,))
    assert row['status'] == 'failed' and row['error']


def test_workspace_api_auth_preset_export(conn, risk, orb_text):
    s, _ = setup(conn, risk, orb_text, 2)
    c, csrf, _ = login(conn)
    config = W.RunConfig(strategy_hash=s.hash, symbols=['ACMX'])
    payload = config.model_dump(mode='json')
    assert TestClient(app).get('/api/backtests').status_code == 401
    assert c.post('/api/backtests/preflight', json=payload).status_code == 403
    assert c.post('/api/backtests/preflight', json=payload, headers={'x-csrf-token': csrf}).json()['can_run']
    r = c.post('/api/backtests/presets', json={'name': 'Baseline', 'config': payload}, headers={'x-csrf-token': csrf})
    assert r.status_code == 200, r.text
    assert c.get('/api/backtests/presets').json()['rows'][0]['config']['symbols'] == ['ACMX']
    rid = W.create_run(conn, config, 'pytest', submit=False)
    W.execute(db.url(), rid)
    assert c.get(f'/api/backtests/{rid}').json()['report']['manifest']['strategy_hash'] == s.hash
    assert c.get(f'/api/backtests/{rid}/export').json()['strategy_text'] == orb_text
    assert c.get(f'/api/backtests/{rid}/export?format=csv').text.startswith('run_id,strategy_hash,day')
    assert c.get('/api/backtests?q=Research').json()['total'] == 1
    assert c.get('/api/backtests/catalog').status_code == 200


def test_portfolio_window_and_fee_accounting(conn, risk):
    from tests.test_portfolio import strat, seed_daily
    s = strat(risk)
    seed_daily(conn, s.symbols)
    days = [date.fromisoformat(r['day']) for r in db.rows(conn, 'SELECT DISTINCT day FROM daily ORDER BY day')][-8:-3]
    report = engine.run(conn, s, risk, days=days, commission_per_order=1)
    assert set(report['days']) <= {str(d) for d in days}
    assert report['metrics']['total_fees'] > 0
    assert report['metrics']['total_pnl'] == pytest.approx(report['equity_curve'][-1]['equity'] - 100000, abs=1e-4)


def test_saved_run_categorize_archive_delete(conn, risk, orb_text):
    s, _ = setup(conn, risk, orb_text, 2)
    c, csrf, _ = login(conn)
    h = {'x-csrf-token': csrf}
    config = W.RunConfig(strategy_hash=s.hash, symbols=['ACMX'])
    parent = W.create_run(conn, config, 'pytest', submit=False)
    W.execute(db.url(), parent)
    child = W.create_run(conn, config.model_copy(update={'parent_id': parent}), 'pytest', submit=False)
    W.execute(db.url(), child)
    r = c.post('/api/backtests/meta', json={'ids': [parent, child], 'category': 'Baseline'}, headers=h)
    assert r.status_code == 200, r.text
    assert c.post('/api/backtests/meta', json={'ids': [parent], 'archived': True}, headers=h).status_code == 200
    active = c.get('/api/backtests').json()
    assert [x['id'] for x in active['rows']] == [child] and active['archived'] == 1 and active['categories'] == ['Baseline']
    assert [x['id'] for x in c.get('/api/backtests?view=archived').json()['rows']] == [parent]
    assert c.get('/api/backtests?view=all&category=Baseline').json()['total'] == 2
    assert c.get('/api/backtests?view=all&category=-').json()['total'] == 0
    assert c.post(f'/api/backtests/{parent}/delete').status_code == 403  # CSRF
    assert c.post(f'/api/backtests/{parent}/delete', headers=h).status_code == 200
    assert c.get(f'/api/backtests/{parent}').status_code == 404
    assert db.row(conn, 'SELECT parent_id FROM backtest_runs WHERE id=?', (child,))['parent_id'] is None
    running = W.create_run(conn, config, 'pytest', submit=False)
    assert c.post(f'/api/backtests/{running}/delete', headers=h).status_code == 409


def test_strategy_categorize_archive_delete(conn, risk, orb_text, tmp_path, monkeypatch):
    folder = tmp_path / 'strategies'
    folder.mkdir()
    (folder / 'orb_news_long.v1.yaml').write_text(orb_text)
    monkeypatch.setattr(registry, 'STRATEGIES_DIR', folder)
    s, _ = setup(conn, risk, orb_text, 2)
    c, csrf, _ = login(conn)
    h = {'x-csrf-token': csrf}
    assert c.post('/api/strategies/meta', json={'hashes': [s.hash], 'category': 'Momentum', 'archived': True}, headers=h).status_code == 200
    row = next(r for r in c.get('/api/strategies').json()['rows'] if r['hash'] == s.hash)
    assert row['category'] == 'Momentum' and row['archived_at']
    assert c.post(f'/api/strategies/{s.hash}/promote', json={'to': 'paper'}, headers=h).status_code == 409
    registry.set_status(conn, s.hash, 'paper', 'pytest')
    assert 'retire it first' in c.post(f'/api/strategies/{s.hash}/delete', json={'confirm': s.id}, headers=h).json()['detail']
    registry.set_status(conn, s.hash, 'retired', 'pytest')
    rid = W.create_run(conn, W.RunConfig(strategy_hash=s.hash, symbols=['ACMX']), 'pytest', submit=False)
    W.execute(db.url(), rid)
    assert c.post(f'/api/strategies/{s.hash}/delete', json={'confirm': 'wrong'}, headers=h).status_code == 400
    r = c.post(f'/api/strategies/{s.hash}/delete', json={'confirm': s.id, 'delete_runs': True}, headers=h)
    assert r.status_code == 200, r.text
    assert not (folder / 'orb_news_long.v1.yaml').exists()
    assert c.get(f'/api/strategies/{s.hash}').status_code == 404
    assert c.get(f'/api/backtests/{rid}').status_code == 404
    assert not db.row(conn, 'SELECT sid FROM strategy_categories')
    registry.sync(conn, risk)  # the file is gone, so it doesn't come back
    assert not db.row(conn, 'SELECT hash FROM strategies WHERE hash=?', (s.hash,))


def test_market_index_benchmark(conn):
    from common import market_inputs as MI
    days = ['2026-03-02', '2026-03-03', '2026-03-04']
    MI.store_history(conn, 'SP_500', [(d, None, None, None, c) for d, c in zip(days, (5000, 5100, 5250))], 'yahoo')
    report = {'equity_curve': [{'day': d, 'equity': 100_000} for d in days], 'metrics': {'return_pct': 0.0}, 'trades': []}
    cfg = W.RunConfig(strategy_hash='a' * 64, benchmark='sp_500', starting_equity=100_000)
    assert cfg.benchmark == 'SP_500' and W.bench_indexes(cfg) == ['SP_500'] and W.bench_symbols(cfg) == []
    W.enrich(conn, report, cfg)
    # closes-only index history: buy at the first close
    assert report['metrics']['benchmark_return_pct'] == pytest.approx(0.05)
    assert report['metrics']['excess_return_pct'] == pytest.approx(-0.05)
    with pytest.raises(ValidationError):
        W.RunConfig(strategy_hash='a' * 64, benchmark='NOT_AN_INDEX')  # underscores only for known indexes


def test_multiple_benchmarks_each_get_a_curve(conn):
    from common import market_inputs as MI
    days = ['2026-03-02', '2026-03-03', '2026-03-04']
    MI.store_history(conn, 'SP_500', [(d, None, None, None, c) for d, c in zip(days, (5000, 5100, 5250))], 'yahoo')
    MI.store_history(conn, 'NASDAQ_100', [(d, None, None, None, c) for d, c in zip(days, (20000, 19000, 18000))], 'yahoo')
    report = {'equity_curve': [{'day': d, 'equity': 100_000} for d in days], 'metrics': {'return_pct': 0.0}, 'trades': []}
    cfg = W.RunConfig(strategy_hash='a' * 64, benchmarks=['nasdaq_100', 'SP_500', 'NASDAQ_100', 'RUSSELL_2000'], starting_equity=100_000)
    assert cfg.benchmark == 'NASDAQ_100' and cfg.benchmarks == ['NASDAQ_100', 'SP_500', 'RUSSELL_2000']
    W.enrich(conn, report, cfg)
    got = {b['name']: b['return_pct'] for b in report['benchmark_curves']}
    assert got == pytest.approx({'NASDAQ_100': -0.1, 'SP_500': 0.05})  # Russell has no history: skipped with a warning
    assert any('RUSSELL_2000' in w for w in report['warnings'])
    assert report['metrics']['benchmark_return_pct'] == pytest.approx(-0.1)  # excess return is against the primary
    old = W.RunConfig(strategy_hash='a' * 64, benchmark='SPY')  # saved runs from before the list existed
    assert old.benchmarks == ['SPY'] and old.benchmark == 'SPY'


def test_recorded_signal_coverage_counts_sessions_with_signals(conn):
    from datetime import date
    from common import timeutil as T
    days = [date(2026, 7, 13), date(2026, 7, 14), date(2026, 7, 15)]
    conn.execute("INSERT INTO signals(ts, symbol, name, value) VALUES (?,?,?,?)", (T.open_ts(days[1]) + 600, 'SPCX', 'news.score', '0.7'))
    assert W.recorded_coverage(conn, ['SPCX'], days) == (1, 3)
    assert W.recorded_coverage(conn, ['AAPL'], days) == (0, 3)

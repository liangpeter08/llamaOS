"""Saved research experiments. This module never issues trading commands.

Missing history for a run's window is requested from the paper broker's history worker
(common/history.py) and downloaded before the replay; the replay then reads one database snapshot.
"""
from __future__ import annotations

import hashlib
import json
import random
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import date, time as clocktime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from common import audit, db, history, marketdata as MD, registry, replay as RP, timeutil as T
from common.config import RiskConfig, PortfolioRisk, FundsRisk, load_risk, universe
from dsl.portfolio_v2 import INDEXES
from dsl.validator import validate
from . import engine

POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix='backtest')


class RunConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    strategy_hash: str = Field(min_length=64, max_length=64, pattern=r'^[0-9a-f]{64}$')
    name: str = Field(default='Research run', min_length=1, max_length=120)
    notes: str = Field(default='', max_length=4000)
    tags: list[str] = Field(default_factory=list, max_length=12)
    start_date: date | None = None
    end_date: date | None = None
    symbols: list[str] = Field(default_factory=list, max_length=50)
    starting_equity: float = Field(default=100_000, ge=100, le=1_000_000_000)
    slippage_bps: float = Field(default=2, ge=0, le=1000)
    spread_bps: float = Field(default=0, ge=0, le=1000)
    commission_per_share: float = Field(default=0, ge=0, le=100)
    commission_per_order: float = Field(default=0, ge=0, le=1000)
    latency_bars: int = Field(default=0, ge=0, le=30)
    cancel_after_bars: int = Field(default=2, ge=1, le=60)
    limit_fill: Literal['touch', 'trade_through'] = 'touch'
    warmup_bars: int = Field(default=0, ge=0, le=389)
    min_history_sessions: int = Field(default=20, ge=20, le=60)
    missing_data: Literal['skip_symbol', 'strict'] = 'skip_symbol'
    max_volume_participation: float = Field(default=1, gt=0, le=1)
    benchmark: str = Field(default='', max_length=24)  # primary benchmark: excess return is measured against it
    benchmarks: list[str] = Field(default_factory=list, max_length=6)  # every benchmark drawn; the primary is first
    risk_free_rate: float = Field(default=0, ge=0, le=0.5)
    holdout_pct: float = Field(default=0, ge=0, le=0.5)
    rolling_window: int = Field(default=0, ge=0, le=252)
    cost_stress: bool = False
    bootstrap_samples: int = Field(default=0, ge=0, le=2000)
    seed: int = Field(default=42, ge=0, le=2**31-1)
    keep_records: int = Field(default=100, ge=0, le=1000)
    repeat_cycles: bool = True  # portfolio: start a new holding period when one ends before the window does
    model_replay: Literal['recorded', 'offline', 'models'] = 'recorded'  # where news/assumption answers come from (common/replay.py)
    model_budget_usd: float = Field(default=5, ge=0, le=200)  # paid model spend cap for one run
    confirm_model_cost: bool = False  # owner accepted an estimate at or above replay.HIGH_COST_USD
    replay_note: str = Field(default='', max_length=500)  # set by create_run when it switches replay mode
    parent_id: int | None = Field(default=None, gt=0)

    @model_validator(mode='after')
    def bounds(self):
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError('Start date must precede end date.')
        if self.rolling_window and self.rolling_window < 5:
            raise ValueError('Rolling windows require at least five sessions.')
        self.symbols = list(dict.fromkeys(s.strip().upper() for s in self.symbols if s.strip()))
        self.benchmark = self.benchmark.strip().upper()
        self.benchmarks = list(dict.fromkeys(([self.benchmark] if self.benchmark else []) + [b.strip().upper() for b in self.benchmarks if b.strip()]))[:6]
        self.benchmark = self.benchmarks[0] if self.benchmarks else ''
        if any(len(s) > 16 or not all(c.isalnum() or c in '.-' for c in s) for s in self.symbols + [b for b in self.benchmarks if b not in INDEXES]):
            raise ValueError('Invalid ticker symbol.')
        if any(len(t) > 40 for t in self.tags):
            raise ValueError('Tags must be at most 40 characters.')
        return self


def encode(obj):
    return json.dumps(obj, default=str, allow_nan=False)


def risk_from(snapshot):
    d = json.loads(snapshot) if isinstance(snapshot, str) else dict(snapshot)
    d['entries'] = tuple(clocktime.fromisoformat(v) for v in d['entries'])
    d['flatten_by'] = clocktime.fromisoformat(d['flatten_by'])
    if d.get('portfolio'):
        d['portfolio']['decide_at'] = clocktime.fromisoformat(d['portfolio']['decide_at'])
        d['portfolio'] = PortfolioRisk(**d['portfolio'])
    if d.get('funds'):
        d['funds'] = FundsRisk(**d['funds'])
    return RiskConfig(**d)


def session_days(c, kind, config, strat=None):
    start, end = history.window(kind, config)
    if kind == 'intraday':
        days = MD.bar_days(c)
    else:
        # A portfolio replay needs every basket symbol, market-input ticker and index on the same day, and the
        # first 20 of those common days are warm-up (ADV and prior closes), not sessions it can decide.
        syms = list(getattr(strat, 'symbols', [])) + list(getattr(strat, 'input_symbols', []))
        sets = [{r['day'] for r in db.rows(c, 'SELECT day FROM daily WHERE symbol=?', (sym,))} for sym in syms]
        sets += [{r['day'] for r in db.rows(c, 'SELECT day FROM index_daily WHERE instrument=?', (inst,))}
                 for inst in getattr(getattr(strat, 'v2', None), 'index_instruments', [])]
        common = sorted(set.intersection(*sets)) if sets else [r['day'] for r in db.rows(c, 'SELECT DISTINCT day FROM daily ORDER BY day')]
        days = [date.fromisoformat(d) for d in common[config.min_history_sessions:]]
    return [d for d in days if start <= d <= end]


def history_gaps(c, symbols, config, days=None) -> list[str]:
    """Why sessions in the window can't be replayed for lack of prior daily history, with the first start date that works."""
    start, end = history.window('intraday', config)
    out = []
    for sym in symbols:
        daily = [r['day'] for r in db.rows(c, 'SELECT day FROM daily WHERE symbol=? AND day<=? ORDER BY day', (sym, end.isoformat()))]
        first = days[0].isoformat() if days else start.isoformat()
        prior = sum(d < first for d in daily)
        if prior >= config.min_history_sessions:
            continue
        if not daily:
            out.append(f'{sym}: no daily bars up to {end}.')
            continue
        stored = [r['day'] for r in db.rows(c, 'SELECT day FROM daily WHERE symbol=? ORDER BY day', (sym,))]
        ok = stored[config.min_history_sessions] if len(stored) > config.min_history_sessions else None
        since = f'{sym}: daily history starts {daily[0]} (it may have started trading then), so it has {prior} of the {config.min_history_sessions} prior sessions a backtest needs'
        if ok and ok > end.isoformat():
            out.append(since + f'. The first testable session is {ok}, after this window: move the dates to start on {ok} or later.')
        elif ok:
            out.append(since + f'. The first testable session is {ok}: move the start date to {ok} or later.')
        else:
            out.append(since + f'. Only {len(stored)} sessions are stored; wait until it has {config.min_history_sessions + 1}.')
    return out


RECORDED_MIN_COVERAGE = 0.1  # below this share of sessions with recorded signals, 'recorded' replay can't test a news strategy


def recorded_coverage(c, symbols, days) -> tuple[int, int]:
    """Sessions in `days` with at least one live-recorded signal for any of `symbols` (prior close to close), and the total."""
    if not days or not symbols:
        return 0, len(days)
    ts = [r['ts'] for r in db.rows(c, 'SELECT ts FROM signals WHERE symbol = ANY(?) AND ts>=? AND ts<=? ORDER BY ts',
                                     (list(symbols), T.prior_close_ts(days[0]), T.close_ts(days[-1])))]
    covered = sum(1 for d in days if any(T.prior_close_ts(d) <= t <= T.close_ts(d) for t in ts))
    return covered, len(days)


def signal_gap(c, strat, config, symbols, days) -> str | None:
    """Warn when a news-gated intraday strategy would replay recorded signals that barely cover the window."""
    if config.model_replay != 'recorded' or getattr(strat, 'kind', 'intraday') != 'intraday' or not getattr(strat, 'uses_signals', False):
        return None
    covered, total = recorded_coverage(c, symbols, days)
    if not total or covered >= RECORDED_MIN_COVERAGE * total:
        return None
    return (f"Recorded news signals cover {covered} of {total} sessions for {', '.join(symbols)} (signals are recorded only while "
            "the system runs live), so news-gated rules stay false on the rest. Use model replay 'offline' (free) or 'models'.")


def preflight(c, config, strat=None, risk=None):
    risk = risk or load_risk(conn=c)
    if strat is None:
        strat, why = registry.load(c, config.strategy_hash, risk)
        if not strat:
            raise ValueError(why)
    info = _preflight(c, config, strat, risk)
    gap = signal_gap(c, strat, config, info['symbols'], session_days(c, info['kind'], config, strat))
    if gap:
        info['warnings'] = [*info['warnings'], gap + (" The run switches to 'offline' automatically." if RP.providers(c)['available'] else '')]
    return info


def _preflight(c, config, strat=None, risk=None):
    risk = risk or load_risk(conn=c)
    if strat is None:
        strat, why = registry.load(c, config.strategy_hash, risk)
        if not strat:
            raise ValueError(why)
    kind = getattr(strat, 'kind', 'intraday')
    symbols = config.symbols or (list(strat.symbols) if kind == 'portfolio' else (list(strat.universe_symbols) or universe()))
    if kind == 'portfolio':
        if set(symbols) != set(strat.symbols):
            raise ValueError('Portfolio weights are part of the strategy. Use its complete symbol basket.')
        if config.latency_bars or config.warmup_bars or config.limit_fill != 'touch' or config.max_volume_participation != 1 or config.min_history_sessions != 20:
            raise ValueError('Daily portfolio replay does not support bar latency, intraday warm-up, trade-through fills, participation caps or custom history requirements.')
    input_symbols = list(getattr(strat, 'input_symbols', []))
    index_instruments = list(getattr(getattr(strat, 'v2', None), 'index_instruments', []))
    start, end = history.window(kind, config)
    fetch = history.plan(c, kind, list(dict.fromkeys(symbols + input_symbols + bench_symbols(config))),
                         list(dict.fromkeys(index_instruments + bench_indexes(config))), config, getattr(strat, 'daily_lookback', 0))
    fetcher = history.available(c)
    days = session_days(c, kind, config, strat)
    rows, warnings = [], []
    if fetch:
        if fetcher:
            # Coverage below describes what is stored now; the run downloads the rest first.
            return {'kind': kind, 'symbols': symbols, 'input_symbols': input_symbols, 'index_instruments': index_instruments,
                    'sessions': len(days), 'first': str(start), 'last': str(end), 'coverage': [],
                    'warnings': history_gaps(c, [x for x in symbols if history.first_available(c, x)], config),
                    'fetch': {**fetch, 'note': history.describe(fetch)}, 'can_run': True,
                    'replay': replay_estimate(c, strat, config, symbols, start, end, len(days))}
        if history.live_data():
            raise ValueError(history.describe(fetch).replace('Will download', 'Missing') + ' The paper broker\'s history download '
                             'isn\'t running: restart make paper (it starts with the broker), then try again.')
        warnings.append(history.describe(fetch).replace('Will download', 'Missing') + ' The simulator can\'t download market '
                        'data, so this run uses only what is stored; use make paper for real history.')
    if not days:
        everything = session_days(c, kind, config.model_copy(update={'start_date': None, 'end_date': None}), strat)
        asked = f"{config.start_date or 'start'} to {config.end_date or 'end'}"
        warnings.append(f'No stored sessions in the requested window ({asked}). '
                        + (f'Decidable sessions for this strategy run from {everything[0]} to {everything[-1]}; clear or widen the dates.'
                           if everything else 'This database has no common daily history for the strategy\'s symbols and inputs.'))
    for sym in list(dict.fromkeys(symbols + input_symbols)):
        daily = db.rows(c, 'SELECT day FROM daily WHERE symbol=? ORDER BY day', (sym,))
        prior = sum(r['day'] < days[0].isoformat() for r in daily) if days else 0
        if kind == 'intraday' and days:
            r = db.row(c, 'SELECT COUNT(*) bars, MIN(ts) first, MAX(ts) last FROM bars WHERE symbol=? AND ts>=? AND ts<?',
                       (sym, T.open_ts(days[0]), T.close_ts(days[-1])))
            sessions = db.row(c, "SELECT COUNT(DISTINCT (to_timestamp(ts) AT TIME ZONE 'America/New_York')::date) n FROM bars WHERE symbol=? AND ts>=? AND ts<?",
                              (sym, T.open_ts(days[0]), T.close_ts(days[-1])))['n']
        else:
            selected = [r for r in daily if days and days[0].isoformat() <= r['day'] <= days[-1].isoformat()]
            r = {'bars': len(selected), 'first': selected[0]['day'] if selected else None, 'last': selected[-1]['day'] if selected else None}
            sessions = len(selected)
        signal_count = db.row(c, 'SELECT COUNT(*) n FROM signals WHERE symbol=? AND ts>=? AND ts<=?',
                              (sym, T.prior_close_ts(days[0]), T.close_ts(days[-1])))['n'] if days else 0
        rows.append({'symbol': sym, **r, 'sessions': sessions, 'prior_sessions': prior, 'signals': signal_count,
                     'role': ('reference' if kind == 'intraday' else 'market input') if sym in input_symbols and sym not in symbols else 'traded'})
        if not sessions:
            warnings.append(f'{sym}: no stored {kind} bars in this window.')
        if prior < config.min_history_sessions:
            warnings.append(f'{sym}: only {prior} prior daily sessions at the start; needs {config.min_history_sessions}.')
        elif prior < getattr(strat, 'daily_lookback', 0):
            warnings.append(f'{sym}: only {prior} prior daily sessions at the start; the rules read up to {strat.daily_lookback}, '
                            'so those values stay missing (conditions false) until enough history accumulates.')
        if getattr(strat, 'uses_signals', False) and not signal_count:
            warnings.append(f'{sym}: no recorded signals; news-gated conditions may stay false.')
    if config.benchmark and days:
        for name in config.benchmarks:
            count = db.row(c, f'SELECT COUNT(*) n FROM {bench_from(name)} AND day>=? AND day<=?',
                           (name, days[0].isoformat(), days[-1].isoformat()))['n']
            if count < len(days):
                warnings.append(f'Benchmark {name} does not cover every requested session; comparison may be unavailable.')
    for inst in index_instruments:
        count = db.row(c, 'SELECT COUNT(*) n FROM index_daily WHERE instrument=?', (inst,))['n']
        if not count:
            warnings.append(f'{inst}: no stored index history for this strategy market input.')
    return {'kind': kind, 'symbols': symbols, 'input_symbols': input_symbols, 'index_instruments': index_instruments,
            'sessions': len(days), 'first': str(days[0]) if days else None,
            'last': str(days[-1]) if days else None, 'coverage': rows, 'warnings': warnings,
            'fetch': None, 'can_run': bool(days and any(r['sessions'] for r in rows)),
            'replay': replay_estimate(c, strat, config, symbols, start, end, len(days))}


def replay_estimate(c, strat, config, symbols, start, end, sessions):
    sessions = sessions or sum(1 for k in range((end - start).days + 1) if date.fromordinal(start.toordinal() + k).weekday() < 5)
    return RP.estimate(c, strat, config, symbols, T.open_ts(start) - 86400, T.close_ts(end), sessions)


def fingerprint(c, symbols, days, index_instruments=()):
    h = hashlib.sha256()
    for sym in sorted(set(symbols)):
        for sql, args in [
            ('SELECT * FROM daily WHERE symbol=? AND day<=? ORDER BY day', (sym, str(days[-1]))),
            ('SELECT * FROM bars WHERE symbol=? AND ts>=? AND ts<? ORDER BY ts', (sym, T.open_ts(days[0]), T.close_ts(days[-1]))),
            ('SELECT ts,symbol,name,value FROM signals WHERE symbol=? AND ts>=? AND ts<=? ORDER BY ts,name,value', (sym, T.prior_close_ts(days[0]), T.close_ts(days[-1])))]:
            for r in c.execute(sql, args):
                h.update(encode(r).encode())
    for inst in sorted(index_instruments):
        for row in c.execute('SELECT * FROM index_daily WHERE instrument=? AND day<=? ORDER BY day', (inst, str(days[-1]))):
            h.update(encode(row).encode())
    return h.hexdigest()


def options(config, multiplier=1):
    return {'starting_equity': config.starting_equity, 'slippage': (config.slippage_bps + config.spread_bps / 2) * multiplier / 10000,
            'commission_per_share': config.commission_per_share * multiplier, 'commission_per_order': config.commission_per_order * multiplier,
            'latency_bars': config.latency_bars, 'cancel_after_bars': config.cancel_after_bars,
            'limit_trade_through': config.limit_fill == 'trade_through', 'warmup_bars': config.warmup_bars,
            'min_history_sessions': config.min_history_sessions, 'missing_data': config.missing_data,
            'max_volume_participation': config.max_volume_participation, 'risk_free_rate': config.risk_free_rate,
            'keep_records': config.keep_records, 'symbols': config.symbols, 'repeat_cycles': config.repeat_cycles}


def bench_symbols(config) -> list[str]:
    return [b for b in config.benchmarks if b not in INDEXES]


def bench_indexes(config) -> list[str]:
    return [b for b in config.benchmarks if b in INDEXES]


def bench_from(name: str) -> str:
    """FROM clause for benchmark closes, filtered by the benchmark (the first query parameter):
    a market index from index_daily, or a stored ticker from daily."""
    return 'index_daily WHERE instrument=?' if name in INDEXES else 'daily WHERE symbol=?'


def bench_curve(c, name: str, curve: list[dict], equity: float) -> list[dict] | None:
    """Buy-and-hold equity for one benchmark over the evaluated sessions; None when closes don't cover every session."""
    values = {r['day']: r['c'] for r in db.rows(c, f'SELECT day,c FROM {bench_from(name)} AND day>=? AND day<=?', (name, curve[0]['day'], curve[-1]['day']))}
    if not all(p['day'] in values and values[p['day']] > 0 for p in curve):
        return None
    # Intraday starts before the first open; use that open as buy-and-hold entry (some index sources publish closes only).
    base = db.row(c, f'SELECT o, c FROM {bench_from(name)} AND day=?', (name, curve[0]['day']))
    start_px = (base['o'] or base['c']) if base else None
    if not start_px or start_px <= 0:
        return None
    return [{'day': p['day'], 'equity': equity * values[p['day']] / start_px} for p in curve]


def enrich(c, report, config):
    curve = report['equity_curve']
    benchmark, curves = [], []
    if curve:
        if not config.benchmarks:
            benchmark = [{'day': p['day'], 'equity': config.starting_equity} for p in curve]  # cash
        for name in config.benchmarks:
            bc = bench_curve(c, name, curve, config.starting_equity)
            if bc is None:
                report.setdefault('warnings', []).append(f'Benchmark {name} unavailable: daily closes must cover every evaluated session.')
                continue
            curves.append({'name': name, 'curve': bc, 'return_pct': bc[-1]['equity'] / config.starting_equity - 1})
            if name == config.benchmark:
                benchmark = bc
    report['benchmark_curve'] = benchmark
    report['benchmark_curves'] = curves
    br = benchmark[-1]['equity'] / config.starting_equity - 1 if benchmark else None
    report['metrics']['benchmark_return_pct'] = br
    report['metrics']['excess_return_pct'] = report['metrics']['return_pct'] - br if br is not None else None
    groups = {}
    for t in report['trades']:
        g = groups.setdefault(t['symbol'], {'symbol': t['symbol'], 'trades': 0, 'pnl': 0, 'fees': 0})
        g['trades'] += 1
        g['pnl'] += t['pnl']
        g['fees'] += t.get('fees', 0)
    report['by_symbol'] = list(groups.values())
    monthly = {}
    prev = config.starting_equity
    for p in curve:
        g = monthly.setdefault(p['day'][:7], {'month': p['day'][:7], 'start': prev, 'end': p['equity']})
        g['end'] = p['equity']
        prev = p['equity']
    report['monthly'] = [{'month': v['month'], 'pnl': v['end'] - v['start'], 'return_pct': v['end'] / v['start'] - 1} for v in monthly.values() if v['start'] > 0]
    if config.bootstrap_samples and len(curve) >= 5:
        rng = random.Random(config.seed)
        returns, prev = [], config.starting_equity
        for p in curve:
            returns.append(p['equity'] / prev - 1)
            prev = p['equity']
        finals, drawdowns = [], []
        for _ in range(config.bootstrap_samples):
            eq, peak, dd = config.starting_equity, config.starting_equity, 0
            for r in rng.choices(returns, k=len(returns)):
                eq *= 1 + r
                peak = max(peak, eq)
                dd = min(dd, eq / peak - 1)
            finals.append(eq / config.starting_equity - 1)
            drawdowns.append(dd)
        def quantile(v, q):
            return sorted(v)[round((len(v) - 1) * q)]
        report['bootstrap'] = {'samples': config.bootstrap_samples, 'seed': config.seed, 'sessions': len(curve),
                               'return_p05': quantile(finals, .05), 'return_p50': quantile(finals, .5), 'return_p95': quantile(finals, .95),
                               'drawdown_p05': quantile(drawdowns, .05), 'loss_probability': sum(r < 0 for r in finals) / len(finals),
                               'note': 'Independent daily-return resampling, not future-price or execution simulation.'}
    elif config.bootstrap_samples:
        report.setdefault('warnings', []).append('Bootstrap needs at least five evaluated sessions.')
    return report


class Cancelled(Exception):
    pass


def model_replay(c, rid, row, config, strat, check_cancel):
    """Download the window's news (broker) and have the models service replay triage / assumptions; returns
    (report section, extra engine options)."""
    inf = RP.influence(strat)
    info = {'mode': config.model_replay, 'influence': inf, 'budget_usd': config.model_budget_usd,
            'caveat': 'Models were trained on data after many of these dates; prompts give only point-in-time evidence, but '
                      'answers may still reflect hindsight. Paid answers are not deterministic.' if config.model_replay == 'models' else
                      'Offline keyword model (heuristic@v1): free and deterministic, cruder than Laya/Claude.'}
    if not (inf['news'] or (inf['assumptions'] and config.model_replay == 'models')):
        info['skipped'] = inf['note']
        return info, {}
    start, end = history.window(row['kind'], config)
    start_ts, end_ts = T.open_ts(start) - 86400, T.close_ts(end)
    def waiting(label):
        def cb(frac, note):
            check_cancel()
            c.execute('UPDATE backtest_runs SET stage=?, heartbeat=? WHERE id=?', (f'{label}: {note} ({frac:.0%})', time.time(), rid))
        return cb
    spec = {'mode': config.model_replay, 'budget_usd': config.model_budget_usd}
    extra = {}
    if inf['news']:
        missing = [s for s in config.symbols if not ((db.kv_get(c, f'news.fetched.{s}') or {}).get('start', 1e18) <= start_ts
                                                     and (db.kv_get(c, f'news.fetched.{s}') or {}).get('end', 0) >= end_ts - 86400)]
        if missing and history.available(c):
            history.wait(c, history.request(c, {'news': {'symbols': missing, 'start_ts': start_ts, 'end_ts': end_ts}}, row['created_by']),
                         waiting('Downloading news'))
        elif missing:
            info['news_note'] = 'News for this window could not be downloaded (paper broker not running); replaying stored news only.'
        spec['news'] = {'symbols': config.symbols, 'start_ts': start_ts, 'end_ts': end_ts}
        extra['signal_source'] = RP.signal_source(config.model_replay, config.symbols)
    if inf['assumptions'] and config.model_replay == 'models':
        days = session_days(c, row['kind'], config, strat)
        spec['assumptions'] = {'strategy_hash': config.strategy_hash, 'days': [str(d) for d in days]}
        extra['assumption_days'] = True
    result = history.wait(c, history.request(c, spec, row['created_by'], worker='models'), waiting('Model replay'), timeout=3600)
    info.update(result=result)
    return info, extra


def create_run(c, config, actor, submit=True):
    risk = load_risk(conn=c)
    strat, why = registry.load(c, config.strategy_hash, risk)
    if not strat:
        raise ValueError(why)
    info = preflight(c, config, strat, risk)
    gap = signal_gap(c, strat, config, info['symbols'], session_days(c, info['kind'], config, strat))
    if gap and RP.providers(c)['available']:
        # Replaying near-empty recorded signals can only produce zero trades: re-triage the window's news offline instead.
        config = config.model_copy(update={'model_replay': 'offline', 'replay_note': gap.split(' Use model replay')[0]
                                           + " This run re-triaged the window's news with the offline keyword model instead."})
        info = preflight(c, config, strat, risk)
    if not info['can_run']:
        raise ValueError('No daily bars stored for the portfolio basket.' if info['kind'] == 'portfolio' else 'No usable stored data. Inspect data coverage before running.')
    est = info.get('replay') or {}
    if config.model_replay != 'recorded':
        if est.get('influence', {}).get('news') or est.get('influence', {}).get('assumptions'):
            if not RP.providers(c)['available']:
                raise ValueError('Model replay needs the models service running (make paper or make dev).')
        if est.get('high_cost') and not config.confirm_model_cost:
            raise ValueError(f"Estimated model cost ${est['claude_usd']:.2f} is at or above ${RP.HIGH_COST_USD:.0f}: confirm the "
                             "estimate to run with live models (the run stops paying at your budget and finishes offline).")
    config = config.model_copy(update={'symbols': info['symbols']})
    if config.parent_id and not db.row(c, 'SELECT id FROM backtest_runs WHERE id=?', (config.parent_id,)):
        raise ValueError('Parent run does not exist.')
    now = time.time()
    rid = c.insert('INSERT INTO backtest_runs(name,strategy_hash,sid,kind,status,created_at,created_by,config,strategy_text,risk_snapshot,parent_id,heartbeat) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                   (config.name, config.strategy_hash, strat.id, info['kind'], 'queued', now, actor, encode(config.model_dump(mode='json')),
                    registry.text_of(c, config.strategy_hash), encode(asdict(risk)), config.parent_id, now))
    audit.log(c, 'backtest.created', actor, {'id': rid, 'hash': config.strategy_hash, 'config': config.model_dump(mode='json')})
    if submit:
        POOL.submit(execute, db.url(), rid)
    return rid


def execute(dsn, rid):
    c = db.connect(dsn)
    data_conn = db.connect(dsn)
    row = db.row(c, 'SELECT * FROM backtest_runs WHERE id=?', (rid,))
    if not row:
        c.close(); data_conn.close()
        return
    t0 = time.time()
    last_heartbeat = 0
    stage, stages = 0, 1
    def check_cancel():
        nonlocal last_heartbeat
        now = time.time()
        if now - last_heartbeat >= .3:
            state = db.row(c, 'SELECT cancel_requested FROM backtest_runs WHERE id=?', (rid,))
            if state['cancel_requested']:
                raise Cancelled('Cancelled by owner.')
            c.execute('UPDATE backtest_runs SET heartbeat=? WHERE id=?', (now, rid))
            last_heartbeat = now
    def progress(done, total):
        check_cancel()
        c.execute('UPDATE backtest_runs SET progress=?, heartbeat=? WHERE id=?', ((stage + done / max(total, 1)) / stages, time.time(), rid))
    try:
        claimed = c.execute("UPDATE backtest_runs SET status='running', heartbeat=? WHERE id=? AND status='queued' RETURNING id", (time.time(), rid)).fetchone()
        if not claimed:
            return
        check_cancel()
        config = RunConfig.model_validate_json(row['config'])
        risk = risk_from(row['risk_snapshot'])
        v = validate(row['strategy_text'], risk)
        if not v.ok:
            raise ValueError('; '.join(v.errors))
        if v.hash != config.strategy_hash:
            raise ValueError('Saved strategy snapshot does not match the requested hash.')
        strat = v.strategy
        inputs = list(getattr(strat, 'input_symbols', []))
        fetch = history.plan(c, row['kind'], list(dict.fromkeys(config.symbols + inputs + bench_symbols(config))),
                             list(dict.fromkeys(list(getattr(getattr(strat, 'v2', None), 'index_instruments', [])) + bench_indexes(config))), config,
                             getattr(strat, 'daily_lookback', 0))
        fetched = None
        if fetch and not history.available(c) and history.live_data():
            raise ValueError(history.describe(fetch).replace('Will download', 'Missing') + ' The paper broker\'s history download '
                             'isn\'t running: restart make paper, then run again.')
        if fetch and history.available(c):
            c.execute('UPDATE backtest_runs SET stage=? WHERE id=?', (history.describe(fetch), rid))
            def downloading(frac, note):
                check_cancel()
                c.execute('UPDATE backtest_runs SET stage=?, heartbeat=? WHERE id=?', (f'Downloading {note} ({frac:.0%})', time.time(), rid))
            fetched = history.wait(c, history.request(c, fetch, row['created_by']), downloading)
        replay_info, extra = None, {}
        if config.model_replay != 'recorded':
            replay_info, extra = model_replay(c, rid, row, config, strat, check_cancel)
        c.execute('UPDATE backtest_runs SET stage=? WHERE id=?', ('Replaying', rid))
        # A stable database snapshot covers every experiment variant and data fingerprint.
        with data_conn.raw.transaction():
            data_conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            days = session_days(data_conn, row['kind'], config, strat)
            if not days:
                raise ValueError('No sessions remain in stored data.')
            stages = 1 + (2 if config.holdout_pct else 0) + (2 if config.cost_stress else 0)
            stages += (len(days) + config.rolling_window - 1) // config.rolling_window if config.rolling_window else 0
            if 'assumption_days' in extra:  # blocks from replayed assumption assessments, read inside the snapshot
                blocks, timeline = RP.assumption_blocks(data_conn, strat, config.model_replay, [str(d) for d in days])
                extra = {'assumption_blocks': blocks}
                replay_info['assumption_timeline'] = timeline
                replay_info['blocked_sessions'] = sorted(blocks)
            def replay(window, multiplier=1):
                return engine.run(data_conn, strat, risk, days=window, progress=progress, check_cancel=check_cancel,
                                  **options(config, multiplier), **extra)
            report = enrich(data_conn, replay(days), config)
            stage += 1
            if not report['days']:
                gaps = history_gaps(data_conn, list(dict.fromkeys(config.symbols or getattr(strat, 'universe_symbols', None) or getattr(strat, 'symbols', []))), config)
                raise ValueError(' '.join(gaps) if gaps else 'All sessions were excluded by missing bars or prior-history requirements.')
            report['validation'] = {}
            if replay_info is not None:
                report['model_replay'] = replay_info
            if config.holdout_pct:
                split = int(len(days) * (1 - config.holdout_pct))
                if split < 2 or len(days) - split < 2:
                    raise ValueError('Holdout requires at least two sessions on each side.')
                holdout = []
                for label, window in [('in_sample', days[:split]), ('holdout', days[split:])]:
                    r = replay(window)
                    stage += 1
                    holdout.append({'label': label, 'days': r['days'], 'metrics': r['metrics']})
                report['validation']['holdout'] = holdout
            if config.cost_stress:
                stress = []
                for multiplier in (2, 3):
                    r = replay(days, multiplier)
                    stage += 1
                    stress.append({'cost_multiplier': multiplier, 'metrics': r['metrics']})
                report['validation']['cost_stress'] = stress
            if config.rolling_window:
                rolling = []
                for offset in range(0, len(days), config.rolling_window):
                    r = replay(days[offset:offset + config.rolling_window])
                    stage += 1
                    rolling.append({'days': r['days'], 'metrics': r['metrics']})
                report['validation']['rolling'] = rolling
            check_cancel()
            root = Path(__file__).resolve().parent.parent
            files = sorted(p for folder in ('backtest', 'core', 'dsl', 'common') for p in (root / folder).glob('*.py'))
            report['manifest'] = {'engine': 'llamaOS-backtest-v1', 'engine_hash': hashlib.sha256(b''.join(p.read_bytes() for p in files)).hexdigest(),
                                  'data_hash': fingerprint(data_conn, config.symbols + list(getattr(strat, 'input_symbols', [])) + bench_symbols(config), days,
                                                           list(getattr(getattr(strat, 'v2', None), 'index_instruments', [])) + bench_indexes(config)),
                                  'config_hash': hashlib.sha256(row['config'].encode()).hexdigest(), 'risk_hash': risk.sha256,
                                  'strategy_hash': config.strategy_hash, 'resolution': '1 minute' if row['kind'] == 'intraday' else 'daily',
                                  'timezone': 'America/New_York', 'currency': 'USD',
                                  'data_source': ('Alpaca bars, split-adjusted, downloaded for this run' + (f' ({fetched})' if fetched else '')
                                                  if fetch and fetched is not None else 'stored database'),
                                  'fill_model': 'next-bar marketable limit; stop first' if row['kind'] == 'intraday' else 'same daily close approximation',
                                  'annualization_sessions': 252, 'risk_free_rate': config.risk_free_rate}
            feeds = {s: (db.kv_get(c, f'history.feed.{s}') or {}).get('feed') or 'unknown'
                     for s in dict.fromkeys(config.symbols + inputs + list(getattr(strat, 'references', [])))}
            report['manifest']['data_feed'] = feeds
            if config.replay_note:
                report.setdefault('warnings', []).insert(0, config.replay_note)
            thin = [s for s, f in feeds.items() if f != 'sip']
            if thin and history.live_data():
                report.setdefault('warnings', []).append(
                    f"Bars for {', '.join(thin)} were stored from the IEX feed (or before feeds were recorded). IEX carries about 2% "
                    "of volume, so volume filters understate liquidity and thin symbols skip minutes. Re-download with "
                    "`python scripts/refetch_history.py " + ' '.join(thin) + "`.")
        check_cancel()
        report.update(ran_at=time.time(), ran_by=row['created_by'], seconds=round(time.time() - t0, 2), run_id=rid)
        with db.tx(c):
            summary = {k: v for k, v in report['metrics'].items() if k != 'drawdown_curve'}
            c.execute("UPDATE backtest_runs SET status='completed',report=?,metrics=?,sessions=?,finished_at=?,progress=1,heartbeat=? WHERE id=?",
                      (encode(report), encode(summary), len(report['days']), time.time(), time.time(), rid))
            c.execute('UPDATE strategies SET backtest=?,updated_at=? WHERE hash=?', (encode(report), time.time(), config.strategy_hash))
            status = db.row(c, 'SELECT status FROM strategies WHERE hash=?', (config.strategy_hash,))
            if status and status['status'] == 'draft' and report['metrics']['trades'] > 0:
                registry.set_status(c, config.strategy_hash, 'backtested', row['created_by'])
            audit.log(c, 'backtest.completed', row['created_by'], {'id': rid, 'metrics': {k: v for k, v in report['metrics'].items() if k != 'drawdown_curve'}})
    except Exception as e:
        status = 'cancelled' if isinstance(e, Cancelled) else 'failed'
        c.execute('UPDATE backtest_runs SET status=?,error=?,finished_at=? WHERE id=?', (status, str(e), time.time(), rid))
        audit.log(c, f'backtest.{status}', row['created_by'], {'id': rid, 'error': str(e)})
    finally:
        data_conn.close()
        c.close()

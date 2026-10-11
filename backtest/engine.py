"""Point-in-time replay through the same deterministic TradingSession used by trading."""
from __future__ import annotations

import math
import statistics
from collections import Counter
from datetime import date

from common import marketdata as MD
from common import timeutil as T
from common.config import RiskConfig, universe
from core.session import TradingSession, SUBMIT, REJECT
from dsl.evaluator import DayData
from dsl.validator import Strategy


def simulate(conn, strategies: list[Strategy], risk: RiskConfig, days: list[date] | None = None,
             starting_equity: float = 100_000.0, slippage: float = 0.0002, keep_records: int = 25,
             on_bar=None, *, symbols=None, commission_per_share=0.0, commission_per_order=0.0,
             latency_bars=0, cancel_after_bars=None, limit_trade_through=False, warmup_bars=0,
             min_history_sessions=20, missing_data='skip_symbol', max_volume_participation=1.0,
             risk_free_rate=0.0, progress=None, check_cancel=None, signal_source=None) -> dict:
    strategies = [s for s in strategies if getattr(s, 'kind', 'intraday') == 'intraday']
    days = days if days is not None else MD.bar_days(conn)
    own = [sym for s in strategies for sym in s.universe_symbols]
    if symbols:
        syms = list(symbols)
    elif own and all(s.universe_symbols for s in strategies):
        syms = list(dict.fromkeys(own))  # every strategy names its tickers: replay just those
    else:
        syms = list(universe())
    # Reference-only symbols: read through peer(), corr_d, beta_d; loaded and replayed as-of, never traded.
    ref_syms = sorted({r for s in strategies for r in s.references} - set(syms))
    equity = starting_equity
    curve, trades, records, per_day, coverage, fills = [], [], [], [], [], []
    funnel, failed, guards = Counter(), Counter(), Counter()
    total_fees, peak_exposure, turnover, intrabar_peak, intrabar_dd = 0.0, 0.0, 0.0, starting_equity, 0.0
    for day_index, d in enumerate(days):
        if check_cancel:
            check_cancel()
        data = {s: MD.load_day(conn, s, d) for s in syms}
        missing = [s for s, x in data.items() if not len(x)]
        cold = [s for s, x in data.items() if len(x.history) < min_history_sessions]
        excluded = sorted(set(missing + cold))
        if excluded and missing_data == 'strict':
            raise ValueError(f'{d}: missing bars or fewer than {min_history_sessions} prior daily sessions: {", ".join(excluded)}')
        data = {s: x for s, x in data.items() if s not in excluded}
        # Only matching timestamps may share a decision step. Missing data is never filled forward.
        timestamps = sorted(set.intersection(*(set(x.ts) for x in data.values()))) if data else []
        lost = sum(len(x) - len(timestamps) for x in data.values())
        if lost and missing_data == 'strict':
            raise ValueError(f'{d}: ticker bars are not aligned; {lost} unmatched bars')
        coverage.append({'day': d.isoformat(), 'symbols': list(data), 'excluded': excluded,
                         'missing': missing, 'insufficient_history': cold, 'bars': len(timestamps), 'unaligned_bars': lost})
        if not timestamps:
            if progress:
                progress(day_index + 1, len(days))
            continue
        replay = {s: DayData(s, d, x.history) for s, x in data.items()}
        indices = {s: {ts: i for i, ts in enumerate(x.ts)} for s, x in data.items()}
        ref_src = {r: MD.load_day(conn, r, d) for r in ref_syms}
        refs = {r: DayData(r, d, x.history) for r, x in ref_src.items()}
        ref_next = {r: 0 for r in ref_syms}
        if ref_syms:
            coverage[-1]['references'] = {r: len(x) for r, x in ref_src.items()}
            missing_refs = [r for r, x in ref_src.items() if not len(x)]
            if missing_refs:
                if missing_data == 'strict':
                    raise ValueError(f'{d}: no bars for reference symbols {", ".join(missing_refs)}')
                funnel['reference_symbol_days_missing'] += len(missing_refs)
        book = (signal_source or MD.signal_book)(conn, T.prior_close_ts(d), T.close_ts(d))  # replayed model answers, if asked
        current_i = 0
        def gate(s, intent, checks):
            if intent.qty > replay[intent.symbol].v[current_i] * max_volume_participation:
                guards['volume_participation'] += 1
                return REJECT
            return SUBMIT
        sess = TradingSession(risk, strategies, replay, book, cash=equity, slippage=slippage, gate=gate, refs=refs)
        ex = sess.exchange
        ex.commission_per_share, ex.commission_per_order = commission_per_share, commission_per_order
        ex.latency_bars, ex.limit_trade_through = latency_bars, limit_trade_through
        if cancel_after_bars is not None:
            ex.cancel_after_bars = cancel_after_bars
        for runner in sess.runners.values():
            runner.diagnostics, runner.failed_conditions = funnel, failed
        day_trades = []
        def collect(events):
            nonlocal turnover
            for ev in events:
                funnel[ev.kind] += 1
                if ev.kind == 'fill':
                    notional = ev.data['qty'] * ev.data['price']
                    turnover += notional
                    f = ev.obj  # the account as booked by this fill; the account is the strategy's capital here
                    mark = f.equity_after
                    fills.append({**ev.data, 'day': d.isoformat(), 'notional': round(notional, 2),
                                  'deployed_pct': notional / mark if mark > 0 else None,
                                  'allocated_pct': f.exposure_after / mark if mark > 0 else None,
                                  'cash_after': round(f.cash_after, 2), 'invested_after': round(mark - f.cash_after, 2)})
                elif ev.kind == 'trade':
                    t = {k: v for k, v in ev.data.items() if k != 'intent_ref'}
                    ref = ev.data.get('intent_ref')
                    if ref is not None:
                        t['sid'] = ref.sid
                        if len(records) < keep_records:
                            records.append({'symbol': ref.symbol, 'ts': ref.ts, 'decision': ref.decision,
                                            'signals': ref.signals, 'pnl': t['pnl']})
                    t['day'] = d.isoformat()
                    day_trades.append(t)
                elif ev.kind == 'rejected':
                    guards.update(c['name'] for c in ev.data.get('guard', []) if not c['ok'])
        prev = equity
        day_peak_stock = 0.0
        for i, ts in enumerate(timestamps):
            current_i = i
            if check_cancel and i % 50 == 0:
                check_cancel()
            for s, x in data.items():
                j = indices[s][ts]
                replay[s].append(ts, x.o[j], x.h[j], x.l[j], x.c[j], x.v[j])
            for r, x in ref_src.items():  # as-of: every reference bar that started at or before this one
                while ref_next[r] < len(x) and x.ts[ref_next[r]] <= ts:
                    j = ref_next[r]
                    refs[r].append(x.ts[j], x.o[j], x.h[j], x.l[j], x.c[j], x.v[j])
                    ref_next[r] += 1
            if i < warmup_bars:
                funnel['warmup_symbol_bars'] += len(data)
                continue
            collect(sess.step(i))
            if i == len(timestamps) - 1:
                collect(sess.end_of_day())
                unfilled = ex.cancel_entries()
                funnel['cancelled'] += len(unfilled)
            mark = ex.equity()
            intrabar_peak = max(intrabar_peak, mark)
            intrabar_dd = min(intrabar_dd, mark / intrabar_peak - 1)
            peak_exposure = max(peak_exposure, ex.gross_exposure() / mark if mark > 0 else 0)
            day_peak_stock = max(day_peak_stock, ex.gross_exposure())
            if on_bar is not None:
                on_bar(sess, d, i)
        equity = ex.equity()
        total_fees += ex.total_fees
        # Positions are flattened by the close, so the session's peak holding is what shows the allocation.
        curve.append({'day': d.isoformat(), 'equity': round(equity, 4), 'cash': round(ex.cash, 2),
                      'stock': round(equity - ex.cash, 2), 'peak_stock': round(day_peak_stock, 2)})
        per_day.append({'day': d.isoformat(), 'trades': len(day_trades), 'pnl': round(equity - prev, 4)})
        trades += day_trades
        if progress:
            progress(day_index + 1, len(days))
    m = metrics(trades, starting_equity, curve, risk_free_rate)
    m.update(total_fees=round(total_fees, 4), gross_pnl=round(m['total_pnl'] + total_fees, 4),
             peak_exposure_pct=peak_exposure, turnover=turnover / starting_equity,
             max_intrabar_drawdown=intrabar_dd)
    warnings = []
    if funnel['reference_symbol_days_missing']:
        warnings.append(f"Reference symbols had no bars on {funnel['reference_symbol_days_missing']} symbol-days; peer() reads were missing there (fail closed).")
    if any(c['excluded'] or c['unaligned_bars'] for c in coverage):
        warnings.append('Some symbols or unmatched bars were excluded. Review session coverage; no bars were fabricated.')
    if not trades:
        warnings.append('No completed trades. Inspect the rule funnel, failed conditions, risk rejections and unfilled orders.')
    if len(curve) < 60:
        warnings.append('Fewer than 60 evaluated sessions: annualized statistics are unstable.')
    return {'days': [c['day'] for c in curve], 'requested_days': [d.isoformat() for d in days],
            'symbols': syms, 'starting_equity': starting_equity, 'metrics': m,
            'per_day': per_day, 'equity_curve': curve, 'trades': trades, 'fills': fills,
            'rejected_by_guard': funnel['rejected'], 'decision_records': records, 'warnings': warnings,
            'coverage': coverage, 'diagnostics': {'funnel': dict(funnel), 'failed_conditions': dict(failed.most_common()),
            'risk_rejections': dict(guards.most_common()), 'processed_sessions': len(curve),
            'skipped_sessions': len(days) - len(curve)}}


def run(conn, strat: Strategy, risk: RiskConfig, days: list[date] | None = None,
        starting_equity: float = 100_000.0, slippage: float = 0.0002, keep_records: int = 25, **options) -> dict:
    if getattr(strat, 'kind', 'intraday') == 'portfolio':
        from .portfolio import run as run_portfolio
        return run_portfolio(conn, strat, risk, days=days, starting_equity=starting_equity, slippage=slippage, **options)
    options.pop('repeat_cycles', None)  # portfolio-only
    options.pop('assumption_blocks', None)
    rep = simulate(conn, [strat], risk, days, starting_equity, slippage, keep_records, **options)
    return {'strategy': strat.id, 'version': strat.version, 'hash': strat.hash, 'kind': 'intraday',
            'uses_signals': strat.uses_signals,
            'signal_note': 'news-gated conditions read recorded signals only' if strat.uses_signals else 'price-only', **rep}


def metrics(trades: list[dict], start: float, curve: list[dict], risk_free_rate=0.0) -> dict:
    n = len(trades)
    final = curve[-1]['equity'] if curve else start
    pnl = final - start
    wins = [t['pnl'] for t in trades if t['pnl'] > 0]
    losses = [t['pnl'] for t in trades if t['pnl'] < 0]
    rs = [t['r_multiple'] for t in trades if t.get('r_multiple') is not None]
    peak, mdd, duration, max_duration = start, 0.0, 0, 0
    daily, drawdown = [], []
    previous = start
    for p in curve:
        peak = max(peak, p['equity'])
        dd = p['equity'] / peak - 1
        mdd = min(mdd, dd)
        duration = duration + 1 if dd < -1e-12 else 0
        max_duration = max(max_duration, duration)
        daily.append(p['equity'] / previous - 1 if previous > 0 else 0)
        drawdown.append({'day': p['day'], 'drawdown': dd})
        previous = p['equity']
    rf = (1 + risk_free_rate) ** (1 / 252) - 1
    excess = [r - rf for r in daily]
    vol = statistics.stdev(daily) if len(daily) > 2 else None
    sharpe = statistics.mean(excess) / vol * math.sqrt(252) if vol and vol > 1e-12 else None
    downside = math.sqrt(sum(min(r, 0) ** 2 for r in excess) / len(excess)) if len(excess) > 2 else 0
    sortino = statistics.mean(excess) / downside * math.sqrt(252) if downside > 1e-12 else None
    try:
        cagr = math.expm1(math.log(final / start) * 252 / len(curve)) if len(curve) >= 20 and final > 0 else None
    except OverflowError:
        cagr = None
    if cagr is not None and not math.isfinite(cagr):
        cagr = None
    return {'trades': n, 'win_rate': len(wins) / n if n else None, 'avg_r': statistics.mean(rs) if rs else None,
            'total_pnl': round(pnl, 4), 'return_pct': pnl / start, 'final_equity': final, 'max_drawdown': mdd,
            'sharpe_daily': sharpe, 'sortino': sortino, 'cagr': cagr, 'calmar': cagr / abs(mdd) if cagr is not None and mdd < 0 else None,
            'annualized_volatility': vol * math.sqrt(252) if vol is not None else None,
            'profit_factor': sum(wins) / abs(sum(losses)) if losses else None,
            'expectancy': sum(t['pnl'] for t in trades) / n if n else None,
            'avg_win': statistics.mean(wins) if wins else None, 'avg_loss': statistics.mean(losses) if losses else None,
            'best_trade': max((t['pnl'] for t in trades), default=None), 'worst_trade': min((t['pnl'] for t in trades), default=None),
            'max_drawdown_sessions': max_duration, 'drawdown_curve': drawdown,
            'exits': dict(Counter(t['exit_kind'] for t in trades))}

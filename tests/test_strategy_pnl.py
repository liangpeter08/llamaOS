"""Strategy P&L as the Alpaca paper broker records it: per-minute marks of each line's session so far
(trades closed this session + open positions' unrealized_pl since entry) and one trades row per exit fill."""
import json
import types
from datetime import date

import pytest

from common import db, funds
from common import strategy_pnl as P
from common import timeutil as T

D1, D2 = date(2026, 10, 12), date(2026, 10, 13)


def mark(conn, ts, session, pnl, equity=100_000.0):
    conn.execute("INSERT INTO equity_history(ts, source, session, equity, cash, strat_pnl) VALUES (?,?,?,?,?,?)",
                 (ts, "alpaca_paper", str(session), equity, equity, json.dumps(pnl)))


def trade(conn, sid, pnl, exit_ts, intent_id=None, qty=10, symbol="AAPL", r=None, kind="target"):
    conn.execute("INSERT INTO trades(intent_id, sid, symbol, side, qty, entry_ts, entry_price, exit_ts, exit_price, exit_kind, pnl, r_multiple)"
                 " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (intent_id, sid, symbol, "long", qty, exit_ts - 600, 100, exit_ts, 100 + pnl / qty, kind, pnl, r))


@pytest.fixture()
def book(conn):
    """A sleeve ('tilt') buys 10 AAPL at 100 on day 1 (marked 110 at the close), holds it overnight and sells
    at 120 on day 2; an intraday line ('orb') closes +$50 on day 1 in two exit fills of one entry."""
    o1, c1, o2 = T.open_ts(D1), T.close_ts(D1), T.open_ts(D2)
    mark(conn, o1 + 60, D1, {"tilt": 0.0})
    trade(conn, "orb", 30, o1 + 120, intent_id=7, qty=6, r=1.0)
    trade(conn, "orb", 20, o1 + 130, intent_id=7, qty=4, r=2.0)
    mark(conn, c1 - 60, D1, {"tilt": 100.0, "orb": 50.0}, 100_150)
    mark(conn, o2 + 60, D2, {"tilt": 150.0}, 100_200)
    trade(conn, "tilt", 200, o2 + 90, kind="rebalance")
    mark(conn, o2 + 120, D2, {"tilt": 200.0}, 100_250)
    return conn


def test_overnight_position_counts_once(book):
    r = P.Realized(book)
    assert P.lifetime_now(book, r) == {"tilt": 200.0, "orb": 50.0}
    # at the day-1 close the sleeve is up $100 open, the intraday line $50 closed
    assert P.lifetime_at(book, T.close_ts(D1) + 3600, r) == {"tilt": 100.0, "orb": 50.0}
    path = [m for _, m in P.marks(book, 0, realized=r)]
    assert [p["tilt"] for p in path] == [0.0, 100.0, 150.0, 200.0]


def test_since_set_counts_only_the_move_after_the_base(book, risk):
    # allocation set overnight, before base_pnl existed: the old code added the whole $200 sale to "since set"
    base_ts = T.close_ts(D1) + 3600
    book.execute("INSERT INTO strategy_funds(sid, allocation, max_drawdown, status, base_usd, base_ts, peak_pnl)"
                 " VALUES ('tilt', 0.1, 0.2, 'active', 10000, ?, 200)", (base_ts,))
    s = types.SimpleNamespace(id="tilt", kind="portfolio")
    f = funds.account(book, risk, {"tilt": s}).strategies["tilt"]
    assert f.pnl == pytest.approx(100) and f.peak_pnl == pytest.approx(100) and f.drawdown == 0
    row = db.row(book, "SELECT base_pnl, peak_pnl FROM strategy_funds WHERE sid='tilt'")
    assert row["base_pnl"] == pytest.approx(100) and row["peak_pnl"] == pytest.approx(100)


def test_setting_an_allocation_starts_since_set_at_zero_with_a_position_open(conn, risk):
    db.kv_set(conn, "account", {"equity": 100_000, "cash": 90_000})
    conn.execute("INSERT INTO positions(symbol, qty, side, avg_price, last_price, sid) VALUES ('AAPL', 50, 'long', 200, 190, 'orb')")
    s = types.SimpleNamespace(id="orb", kind="intraday")
    funds.ensure(conn, risk, s, "owner", 0.10)
    f = funds.account(conn, risk, {"orb": s}).strategies["orb"]
    assert f.pnl == pytest.approx(0) and f.drawdown == 0  # the $500 open loss predates the allocation
    conn.execute("UPDATE positions SET last_price=180")
    assert funds.account(conn, risk, {"orb": s}).strategies["orb"].pnl == pytest.approx(-500)


def test_equity_attribution_and_performance_api(book):
    from tests.test_api import login
    c, _, _ = login(book)
    eq = c.get("/api/equity?range=ALL").json()
    by = {b["strategy"]: b["pnl"] for b in eq["by_strategy"]}
    assert by == {"tilt": 200.0, "orb": 50.0}  # was tilt 300: day 1's open $100 counted again on day 2
    assert [d["by_strategy"] for d in eq["daily"]] == [{"tilt": 100.0, "orb": 50.0}, {"tilt": 100.0, "orb": 0.0}]
    day2 = c.get(f"/api/equity?range=custom&start={D2}&end={D2}").json()
    assert {b["strategy"]: b["pnl"] for b in day2["by_strategy"]} == {"tilt": 100.0}

    perf = c.get("/api/performance/strategies?range=ALL").json()
    rows = {r["sid"]: r for r in perf["strategies"]}
    assert rows["tilt"]["range_pnl"] == 200 and rows["tilt"]["realized_total"] == 200 and rows["orb"]["lifetime"] == 50
    trips = {t["sid"]: t for t in perf["trades"]}
    assert len(perf["trades"]) == 2 and trips["orb"]["pnl"] == 50 and trips["orb"]["fills"] == 2
    assert trips["orb"]["r"] == pytest.approx(1.4)  # qty-weighted over the two exit fills
    assert perf["series"]["tilt"][-1] == 200 and [d["session"] for d in perf["daily"]][:2] == [str(D1), str(D2)]
    one = c.get(f"/api/performance/strategies?range=custom&start={D2}&end={D2}").json()
    assert {r["sid"]: r["range_pnl"] for r in one["strategies"]} == {"tilt": 100.0, "orb": 0.0}

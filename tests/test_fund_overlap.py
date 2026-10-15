"""A line holding more than its allocation, and two owners in one Alpaca position (big_tech_news_1, Oct 2026)."""
import json
import time
from datetime import date

import pytest

from common import db, funds, sleeves
from core import funds as F
from core import portfolio as P


def checks(name_ok):
    return {c.name: c.ok for c in name_ok}


# ---------------------------------------------------------------- allocation lowered below holdings
def test_lowered_allocation_blocks_buys_and_says_why(risk):
    # big_tech_news_1: bought $697k under an 80% allocation, then the owner cut it to 10% of ~$1M
    f = F.StrategyFund("big_tech_news_1", "portfolio", 0.10, "set", 0.25, None, base_usd=100_385, deployed=703_000, reserved=0.003)
    a = F.Account(equity=1_003_850, cash=220_000, deployed=783_000, intraday_deployed=0, strategies={f.sid: f})
    assert not checks(F.check_buy(f.sid, "portfolio", 99_291, a, risk.funds))["strategy_allocation"]
    assert F.room_usd(f.sid, "portfolio", a, risk.funds) == 0
    note = F.over_note(f, a.equity)
    assert "holds $703,000" in note and "nothing is sold" in note and "sleeve" in note
    f.deployed = 90_000
    assert F.over_note(f, a.equity) == ""


def test_set_allocation_below_holdings_keeps_positions_and_records_them(conn, risk):
    from tests.test_funds import running_orb
    s = running_orb(conn, risk)
    db.kv_set(conn, "account", {"equity": 100_000, "cash": 60_000})
    assert funds.set_allocation(conn, risk, s, 0.5, 0.2, 0.05, "owner") == []
    conn.execute("INSERT INTO positions(symbol, qty, side, avg_price, last_price, sid) VALUES ('AAPL', 200, 'long', 200, 200, ?)", (s.id,))
    assert funds.over_note(conn, risk, s) == ""
    assert funds.set_allocation(conn, risk, s, 0.1, 0.2, 0.05, "owner") == []  # allowed: lowering is how the owner stops buys
    assert "holds $40,000, $30,000 above its $10,000 allocation" in funds.over_note(conn, risk, s)
    assert db.row(conn, "SELECT qty FROM positions WHERE symbol='AAPL'")["qty"] == 200
    a = db.row(conn, "SELECT payload FROM audit WHERE kind='funds.allocated' ORDER BY id DESC LIMIT 1")["payload"]
    assert (json.loads(a) if isinstance(a, str) else a)["deployed"] == pytest.approx(40_000)
    assert not checks(F.check_buy(s.id, "intraday", 100, funds.account(conn, risk), risk.funds))["strategy_allocation"]


# ---------------------------------------------------------------- one owner per Alpaca position
def fill(conn, h, sid, sym, qty, px=100.0):
    conn.execute("INSERT INTO portfolio_orders(strategy_hash,sid,day,ts,symbol,side,qty,filled_qty,avg_price,ref_price,reason,decision,guard,status)"
                 " VALUES (?,?,?,?,?,'buy',?,?,?,?,'initial','[]','[]','filled')", (h, sid, "2026-10-12", time.time(), sym, qty, qty, px, px))


def test_symbol_owners_sees_every_sleeve(conn):
    a, b = "a" * 64, "b" * 64
    fill(conn, a, "intra_tech_v2", "AMD", 136)
    fill(conn, b, "big_tech_news_1", "AMD", 165)
    fill(conn, b, "big_tech_news_1", "NVDA", 440)
    assert sleeves.symbol_owners(conn) == {"AMD": {a, b}, "NVDA": {b}}
    assert sleeves.owned_symbols(conn) == {"AMD": a, "NVDA": b}  # deterministic, not "last one wins"


def test_desk_keeps_a_second_sleeve_out_of_a_held_symbol(conn, risk):
    from services.broker.portfolio_desk import PortfolioDesk
    from tests.test_portfolio import FakeAlpaca
    a, b = "a" * 64, "b" * 64
    fill(conn, a, "intra_tech_v2", "AMD", 136)
    desk = PortfolioDesk(conn, FakeAlpaca({"AMD": 600.0}, {"AMD": 600.0}), risk)
    acct = {"equity": "1000000", "buying_power": "2000000"}
    pos = [{"symbol": "AMD", "qty": "136", "market_value": "81600", "current_price": "600"}]
    buy = P.Order("AMD", "buy", 10, 600.0, "initial")
    day = date(2026, 10, 13)
    assert not checks(desk.checks(b, buy, acct, pos, set(), day))["not_other_sleeve_symbol"]
    assert checks(desk.checks(a, buy, acct, pos, set(), day))["not_other_sleeve_symbol"]  # adding to its own holding
    sell = P.Order("AMD", "sell", 10, 600.0, "exit")
    assert "not_other_sleeve_symbol" not in checks(desk.checks(a, sell, acct, pos, set(), day))

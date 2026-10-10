from common import audit, db, sim
from common import marketdata as MD
from common import timeutil as T
from backtest import engine as bt
from dsl.validator import validate
from services.broker.seed import seed
from services.models.triage import backfill


def test_audit_chain_detects_tampering(conn):
    for i in range(5):
        audit.log(conn, "test", "pytest", {"i": i})
    assert audit.verify(conn)["ok"]
    conn.execute("UPDATE audit SET payload=? WHERE id=(SELECT MIN(id)+2 FROM audit)", ('{"i": 99}',))
    v = audit.verify(conn)
    assert not v["ok"] and v["why"] == "hash mismatch"


def test_kv_and_insert_returning(conn):
    db.kv_set(conn, "k", {"a": 1})
    assert db.kv_get(conn, "k") == {"a": 1}
    nid = conn.insert("INSERT INTO notes(ts, symbol, text, stance) VALUES (?,?,?,?)", (1.0, "ABC", "50% sure", "bullish"))
    assert db.row(conn, "SELECT text FROM notes WHERE id=?", (nid,))["text"] == "50% sure"


def test_seed_backfill_backtest(conn, risk, orb_text):
    info = seed(conn, history_days=12, seed_=7)
    n = backfill(conn)
    assert n > 0
    days = MD.bar_days(conn)
    assert len(days) == 12 and info["history_days"] == 12
    # signals are stamped after their news: never before the item was released
    bad = db.row(conn, "SELECT COUNT(*) n FROM signals s JOIN news n ON n.id=s.news_id WHERE s.ts < n.ts")
    assert bad["n"] == 0
    s = validate(orb_text, risk).strategy
    a = bt.run(conn, s, risk)
    b = bt.run(conn, s, risk)
    assert a["metrics"] == b["metrics"]  # deterministic
    for t in a["trades"]:
        assert t["exit_ts"] >= t["entry_ts"]


def test_sim_is_deterministic():
    d = sim.chain_for(T.session_date(), 2, 1)
    a, b = sim.simulate(d, 7), sim.simulate(d, 7)
    assert a[-1].bars["ACMX"][100] == b[-1].bars["ACMX"][100]


def test_idealab_reads_any_feed_and_guards_empty_runs(conn):
    import pytest
    from services.models import idealab

    # empty database: refuse before touching the daily quota or any model
    with pytest.raises(idealab.NoEvents):
        idealab.run(conn)
    assert not db.rows(conn, "SELECT k FROM kv WHERE k LIKE 'idealab.quota.%'")

    seed(conn, history_days=12, seed_=7)
    backfill(conn)
    conn.execute("UPDATE news SET session='alpaca'")  # e.g. the Alpaca paper feed
    study = idealab.event_study(conn)
    assert sum(g["n"] + g["n_holdout"] for g in study["groups"]) > 0


def test_reaction_uses_next_session_for_overnight_news(conn):
    from datetime import time as dtime
    from services.models import idealab

    seed(conn, history_days=3, seed_=7)
    days = MD.bar_days(conn)
    evening = T.ts_at(days[0], dtime(18, 0))
    rx = idealab._reaction(conn, {"symbol": "ACMX", "ts": evening})
    assert rx["day"] == days[1].isoformat() and rx["when"] == "premarket" and rx["r60"] is not None

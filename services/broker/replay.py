"""Rebuild the account's equity history by replaying the running strategies over stored sessions.

    python -m services.broker.replay

Runs every strategy currently in paper or live, together in one simulated account, over all
history sessions with every intent auto-approved. Rows are written to equity_history with
source='replay' (one point per bar, with per-strategy P&L) and replace any previous replay.
The dashboard labels them as replayed history; the live session continues from the last point.
"""
from __future__ import annotations

import json

from backtest.engine import simulate
from common import audit, db, registry
from common import timeutil as T
from common.config import load_risk, load_yaml


def replay(conn) -> dict:
    risk = load_risk()
    strategies = [s for _, s in registry.runnable(conn, risk)]
    start = float(load_yaml("sim.yaml").get("starting_equity", 100_000))
    rows: list[tuple] = []

    def on_bar(sess, day, i):
        d = sess.days[next(iter(sess.days))]
        ex = sess.exchange
        rows.append((d.close_ts(i), "replay", day.isoformat(), ex.equity(), ex.cash,
                     json.dumps({k: round(v, 2) for k, v in sess.pnl_by_strategy().items()})))

    rep = simulate(conn, strategies, risk, starting_equity=start, on_bar=on_bar)
    with db.tx(conn):
        conn.execute("DELETE FROM equity_history WHERE source='replay'")
        conn.executemany(
            "INSERT INTO equity_history(ts, source, session, equity, cash, strat_pnl) VALUES (?,?,?,?,?,?)", rows)
    info = {"strategies": [f"{s.id} v{s.version}" for s in strategies], "sessions": len(rep["days"]),
            "points": len(rows), "start": start, "end": round(rep["equity_curve"][-1]["equity"], 2) if rep["equity_curve"] else start,
            "trades": rep["metrics"]["trades"]}
    db.kv_set(conn, "equity.replay", info)
    audit.log(conn, "equity.replayed", "host", info)
    return info


def main():
    conn = db.get()
    db.init(conn)
    print(replay(conn))


if __name__ == "__main__":
    main()

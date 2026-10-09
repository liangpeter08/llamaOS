"""Seed simulated market history: daily bars for warmup, intraday bars + news for history days.

    python -m services.broker.seed [--days N]

Run `python -m services.models.backfill` afterwards to record signals for the history news.
"""
from __future__ import annotations

import argparse

from common import audit, db, sim
from common import marketdata as MD
from common import timeutil as T
from common.config import load_yaml


def seed(conn, history_days: int, seed_: int) -> dict:
    session = T.session_date()
    chain = sim.chain_for(session, history_days)
    days = sim.simulate(chain, seed_)
    hist_start = len(days) - 1 - history_days
    with db.tx(conn):
        for k, sd in enumerate(days[:-1]):  # never store the live session's results
            MD.store_daily(conn, sd)
            if k >= hist_start:
                MD.store_bars(conn, sd)
                MD.store_news(conn, sd, "history")
    info = {"session": session.isoformat(), "history_from": days[hist_start].day.isoformat(),
            "history_to": days[-2].day.isoformat(), "history_days": history_days, "seed": seed_}
    db.kv_set(conn, "sim.history", info)
    audit.log(conn, "sim.seeded", "broker", info)
    return info


def main():
    cfg = load_yaml("idealab.yaml")
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=int(cfg.get("history_days", 30)))
    ap.add_argument("--seed", type=int, default=int(load_yaml("sim.yaml").get("seed", 7)))
    a = ap.parse_args()
    conn = db.get()
    db.init(conn)
    print(seed(conn, a.days, a.seed))


if __name__ == "__main__":
    main()

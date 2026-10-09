"""Development only: register the example strategies, backtest them and put them in paper.

In normal use you do this from the dashboard (Strategies -> Run backtest -> Promote to paper).
"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.env import load  # noqa: E402

load()
from backtest import engine as bt  # noqa: E402
from common import audit, db, registry  # noqa: E402
from common.config import load_risk, load_yaml  # noqa: E402


def main():
    conn = db.get()
    db.init(conn)
    risk = load_risk()
    registry.sync(conn, risk)
    for row in db.rows(conn, "SELECT * FROM strategies WHERE status IN ('draft','backtested') ORDER BY sid"):
        s, why = registry.load(conn, row["hash"], risk)
        if not s:
            print(f"skip {row['sid']}: {why}")
            continue
        rep = bt.run(conn, s, risk, starting_equity=float(load_yaml("sim.yaml").get("starting_equity", 100_000)))
        rep.update(ran_at=time.time(), ran_by="host", seconds=0)
        conn.execute("UPDATE strategies SET backtest=? WHERE hash=?", (json.dumps(rep, default=str), row["hash"]))
        audit.log(conn, "strategy.backtested", "host", {"hash": row["hash"], "metrics": rep["metrics"]})
        registry.set_status(conn, row["hash"], "paper", "host", paper_started_at=time.time())
        m = rep["metrics"]
        print(f"{s.id} v{s.version}: {m['trades']} trades, P&L {m['total_pnl']:+,.2f} -> paper (mode {row['mode']})")
    from services.broker.replay import replay
    info = replay(conn)
    print(f"equity history replayed: {info['sessions']} sessions, {info['trades']} trades, "
          f"{info['start']:,.0f} -> {info['end']:,.0f}")


if __name__ == "__main__":
    main()

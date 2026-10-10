"""Load historical Alpaca news into the paper database and record signals for it offline.

    make news-backfill                 # covers the period your stored minute bars span
    python scripts/news_backfill.py --days 20

The Idea Lab measures price reactions after recorded news, so it needs news history that
overlaps stored minute bars. Live ingestion only starts at `make paper`; this fills the gap.

Triage uses the offline keyword model (heuristic@v1): free, deterministic and with no
look-ahead. Signals are stamped at publication time + 30s, like any point-in-time record.
Rows are claimed before triage, so the running models worker never spends paid calls on them.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.env import ROOT, load  # noqa: E402

load(ROOT / "secrets/paper.env")
load(ROOT / "secrets/broker.env")
load()
os.environ["BROKER_BACKEND"] = "alpaca"

from common import audit, db  # noqa: E402
from common.config import universe  # noqa: E402
from services.broker.alpaca import Alpaca, epoch  # noqa: E402
from services.models import triage  # noqa: E402

CLAIM = "backfill_queued"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, help="how far back to fetch (default: span of stored minute bars)")
    a = ap.parse_args()
    conn = db.get()
    if "paper" not in db.row(conn, "SELECT current_database() d")["d"]:
        sys.exit("refusing to run: DATABASE_URL is not a paper database (load secrets/paper.env)")
    db.init(conn)
    syms = universe()
    end = time.time()
    if a.days:
        start = end - a.days * 86400
    else:
        first = db.row(conn, "SELECT MIN(ts) t FROM bars")["t"]
        if not first:
            sys.exit("no minute bars stored yet: run make paper-setup first")
        start = first - 86400  # include the evening/overnight news before the first session
    api = Alpaca()
    items = api.news(syms, start, end)
    added = 0
    with db.tx(conn):
        for n in items:
            cur = conn.execute(
                "INSERT INTO news(ext_id, ts, session, symbols, headline, summary, source, url, content, triage)"
                " VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(ext_id) DO NOTHING",
                ("alpaca:" + str(n["id"]), epoch(n["created_at"]), "alpaca", json.dumps(n["symbols"]),
                 n["headline"], n.get("summary", ""), n.get("source", "alpaca"), n.get("url"), n.get("content"), CLAIM))
            added += cur.rowcount
    queued = db.rows(conn, "SELECT * FROM news WHERE triage=? ORDER BY ts", (CLAIM,))
    for r in queued:
        item = dict(r, symbols=json.loads(r["symbols"] or "[]"))
        triage.triage(conn, item, stamp_ts=r["ts"] + 30, allow_paid=False)
    counts = {r["triage"]: r["n"] for r in conn.execute(
        "SELECT triage, COUNT(*) n FROM news WHERE id = ANY(?) GROUP BY triage", ([r["id"] for r in queued],))} if queued else {}
    info = {"fetched": len(items), "new": added, "triaged": len(queued), "result": counts,
            "from": time.strftime("%Y-%m-%d", time.gmtime(start)), "symbols": syms, "model": "heuristic@v1"}
    audit.log(conn, "news.backfilled", "host", info)
    print(info)


if __name__ == "__main__":
    main()

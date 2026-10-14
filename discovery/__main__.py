"""python -m discovery TICKER [--peers SPY,QQQ] [--kinds intraday,daily] [--generations 3] [--no-save] [--offline]

Runs single-ticker discovery against the stored data and prints the report. Claude proposes and revises
candidates when KIMI_API_KEY is set (unless --offline); otherwise the offline templates do.
"""
from __future__ import annotations

import argparse
import json
import sys

from common import db

from .run import Config, run, summary


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m discovery", description="Single-ticker strategy discovery")
    ap.add_argument("ticker")
    ap.add_argument("--peers", default="SPY,QQQ", help="comma-separated reference tickers (default SPY,QQQ)")
    ap.add_argument("--kinds", default="intraday,daily")
    ap.add_argument("--generations", type=int, default=4)
    ap.add_argument("--max-trials", type=int, default=60)
    ap.add_argument("--holdout", type=float, default=0.25, help="share of the latest sessions sealed for the holdout")
    ap.add_argument("--min-trades", type=int, default=15)
    ap.add_argument("--no-save", action="store_true", help="don't write holdout survivors as draft strategy files")
    ap.add_argument("--offline", action="store_true", help="templates only, even when a model key is set")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    cfg = Config(ticker=a.ticker, peers=[p for p in a.peers.split(",") if p], kinds=[k for k in a.kinds.split(",") if k],
                 generations=a.generations, max_trials=a.max_trials, holdout_pct=a.holdout, min_trades=a.min_trades,
                 save_drafts=not a.no_save)
    conn = db.get()
    db.init(conn)
    generator = None
    if not a.offline:
        from services.models import llm
        if llm.available():
            from services.models.discovery import ClaudeGenerator
            generator = ClaudeGenerator()
    report = run(conn, cfg, "cli", generator=generator, progress=lambda s: print(f"  .. {s}", file=sys.stderr))
    print(json.dumps(report, default=str, indent=2) if a.json else summary(report))


if __name__ == "__main__":
    main()

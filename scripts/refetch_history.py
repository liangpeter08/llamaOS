"""Re-download stored bars from the history feed (SIP), replacing bars stored from IEX.

    python scripts/refetch_history.py            # every symbol with stored daily or 1-minute bars
    python scripts/refetch_history.py OILU UUP   # just these

Queues one request per symbol for the paper broker's history worker (make paper must be running) and
waits for each. Daily bars are rewritten over the whole stored range; 1-minute bars over the stored
1-minute range. Symbols the feed doesn't know (for example simulator tickers) are reported and skipped.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.env import ROOT, load  # noqa: E402

load(ROOT / "secrets/paper.env")  # the paper stack's DATABASE_URL, as the other host scripts
load()

from common import audit, db, history  # noqa: E402


def specs(c, only: list[str]) -> list[dict]:
    daily = {r["symbol"]: r for r in db.rows(c, "SELECT symbol, MIN(day) a, MAX(day) b FROM daily GROUP BY symbol")}
    minute = {r["symbol"]: r for r in db.rows(
        c, "SELECT symbol, (to_timestamp(MIN(ts)) AT TIME ZONE 'America/New_York')::date::text a,"
           " (to_timestamp(MAX(ts)) AT TIME ZONE 'America/New_York')::date::text b FROM bars GROUP BY symbol")}
    out = []
    for sym in only or sorted({*daily, *minute}):
        d, m = daily.get(sym), minute.get(sym)
        if not (d or m):
            print(f"{sym}: nothing stored, skipped (a backtest downloads it on demand)")
            continue
        start = min(x["a"] for x in (d, m) if x)
        end = max(x["b"] for x in (d, m) if x)
        out.append({"daily": [sym], "minute": [sym] if m else [], "index": [], "daily_from": start,
                    "start": m["a"] if m else start, "end": end})
    return out


def main():
    c = db.get()
    if not history.available(c):
        sys.exit("The paper broker's history worker isn't running: start make paper first.")
    todo = specs(c, [s.upper() for s in sys.argv[1:]])
    ok, failed = [], []
    for spec in todo:
        sym = spec["daily"][0]
        try:
            result = history.wait(c, history.request(c, spec, "refetch"), timeout=1800,
                                  on_progress=lambda f, n: print(f"\r{sym}: {n} ({f:.0%})".ljust(80), end="", flush=True))
            print(f"\r{sym}: {result.get('daily', 0)} daily, {result.get('minute', 0)} 1-minute bars from {result.get('feed', '?')}".ljust(80))
            ok.append(sym)
        except ValueError as e:
            print(f"\r{sym}: {e}".ljust(80))
            failed.append(sym)
    audit.log(c, "history.refetched", "host", {"ok": ok, "failed": failed})
    print(f"done: {len(ok)} re-downloaded, {len(failed)} failed")


if __name__ == "__main__":
    main()

"""Run inside the api container by deploy/sync-strategies.sh (piped on stdin, never copied to the host).

    python - [--watch] FILE.yaml ...

Registers new strategy files as drafts, then reports each named file's validation against the live
risk controls and the code this image runs. With --watch, enables the tickers of every valid file
on the paper watchlist (entries require it), the same as the dashboard's monitoring switch.
"""
import sys

from common import audit, db, monitoring, registry
from common.config import STRATEGIES_DIR, load_risk
from dsl.loader import file_hash
from dsl.validator import validate

ACTOR = "sync-strategies"
args = sys.argv[1:]
watch = "--watch" in args
names = [a for a in args if a != "--watch"]

c = db.get()
risk = load_risk(conn=c)
registry.sync(c, risk)

bad, wanted = 0, set()
for name in names:
    text = (STRATEGIES_DIR / name).read_text()
    row = db.row(c, "SELECT sid, version, status FROM strategies WHERE hash=?", (file_hash(text),))
    v = validate(text, risk)
    state = f"{row['status']}" if row else "not registered"
    if v.ok:
        print(f"  ok   {name}  ({state})")
        wanted |= set(v.strategy.universe_symbols) | set(v.strategy.references)
    else:
        bad += 1
        print(f"  ERR  {name}  ({state})")
        for e in v.errors:
            print(f"         {e}")

if watch and wanted:
    on = set(monitoring.enabled(c))
    for sym in sorted(wanted - on):
        try:
            monitoring.set_enabled(c, sym, True, ACTOR)
        except ValueError as e:
            print(f"  watchlist: {sym} not enabled: {e}")
            continue
        audit.log(c, "monitoring.changed", ACTOR, {"symbol": sym, "enabled": True, "strategies": []})
        print(f"  watchlist: enabled {sym}")
elif wanted:
    missing = sorted(wanted - set(monitoring.enabled(c)))
    if missing:
        print(f"  watchlist: not monitored yet: {', '.join(missing)} (rerun with --watch, or enable them on the Watchlist page)")

sys.exit(1 if bad else 0)

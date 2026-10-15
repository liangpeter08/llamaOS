"""Check strategy P&L against the raw fills (read-only).

    DATABASE_URL=... python scripts/verify_strategy_pnl.py [--schema acct_4]

Rebuilds each strategy line's realized and open P&L from the fills table alone (average cost per line and
symbol; entry/exit fills belong to the line of their intent, portfolio fills to the sleeve order they filled)
and compares it with the trades table, the positions table, the dashboard's "since set" figures and the
equity chart's per-strategy totals. Prints a table; exits 1 on a mismatch over $0.05.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common import db, funds  # noqa: E402
from common import strategy_pnl as P  # noqa: E402
from common.config import load_risk  # noqa: E402


def from_fills(c) -> tuple[dict, dict]:
    """(realized per sid, {(sid, symbol): [qty, avg cost]}) replaying the fills in time order."""
    intent_sid = {r["id"]: r["sid"] for r in db.rows(c, "SELECT id, sid FROM intents")}
    sleeve = db.rows(c, "SELECT sid, symbol, side, filled_qty, avg_price FROM portfolio_orders WHERE filled_qty>0")
    book: dict = {}
    realized: dict = {}
    for f in db.rows(c, "SELECT * FROM fills ORDER BY ts, id"):
        if f["intent_id"] is not None:
            sid = intent_sid.get(f["intent_id"]) or "unmanaged"
        else:  # a sleeve fill: the sleeve order on that symbol and side
            cands = {o["sid"] for o in sleeve if o["symbol"] == f["symbol"] and o["side"] == f["side"]}
            sid = cands.pop() if len(cands) == 1 else "ambiguous:" + ",".join(sorted(cands))
        q, px = float(f["qty"]), float(f["price"])
        pos = book.setdefault((sid, f["symbol"]), [0.0, 0.0])
        if f["side"] == "buy":
            pos[1] = (pos[0] * pos[1] + q * px) / (pos[0] + q)
            pos[0] += q
        else:
            realized[sid] = realized.get(sid, 0.0) + (px - pos[1]) * q
            pos[0] -= q
    return realized, {k: v for k, v in book.items() if abs(v[0]) > 1e-9}


def old_since_set(c, sid: str, base_ts: float) -> float:
    """What the dashboard showed before the fix: trades since base_ts + open P&L since entry."""
    r = db.row(c, "SELECT COALESCE(SUM(pnl),0) v FROM trades WHERE sid=? AND exit_ts>=?", (sid, int(base_ts)))["v"]
    return r + sum((p["last_price"] - p["avg_price"]) * p["qty"] for p in db.rows(c, "SELECT * FROM positions WHERE sid=?", (sid,)))


def old_chart_totals(c) -> dict:
    """The equity chart's per-strategy totals before the fix: the sum of every session's last strat_pnl."""
    last: dict = {}
    for r in db.rows(c, "SELECT session, strat_pnl FROM equity_history WHERE source<>'replay' ORDER BY ts"):
        last[r["session"]] = json.loads(r["strat_pnl"] or "{}")
    out: dict = {}
    for v in last.values():
        for k, x in v.items():
            out[k] = out.get(k, 0.0) + x
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--schema", help="account schema (default: the active one)")
    a = ap.parse_args()
    c = db.get()
    if a.schema:
        db.pin(a.schema)
        c = db.get()
    schema = a.schema or db.kv_get(c, "broker.account_schema") or "public"
    db.pin(schema if schema != "public" else None)
    risk = load_risk()
    realized_f, open_f = from_fills(c)
    realized_t = {r["sid"] or "unmanaged": r["v"] for r in db.rows(c, "SELECT sid, SUM(pnl) v FROM trades GROUP BY sid")}
    pos = {(p["sid"] or "unmanaged", p["symbol"]): p for p in db.rows(c, "SELECT * FROM positions")}
    unreal = P.unrealized(c)
    old_chart = old_chart_totals(c)
    # the fixed chart's ALL-range totals (same code as /api/equity)
    from services.api.app import strategy_pnl_path
    rows = db.rows(c, "SELECT ts, source, session, equity, strat_pnl FROM equity_history WHERE source<>'replay' ORDER BY ts")
    new_chart = strategy_pnl_path(c, rows)[-1] if rows else {}
    fund_rows = {r["sid"]: r for r in db.rows(c, "SELECT * FROM strategy_funds")}
    run = funds.running(c, risk)
    # read-only: compute the fixed since-set without writing the backfill
    rz = P.Realized(c)
    life = P.lifetime_now(c, rz)

    bad = 0
    print(f"schema {schema} · {time.strftime('%Y-%m-%d %H:%M')}\n")
    print("Open positions: positions table vs fills")
    for (sid, sym), (q, cost) in sorted(open_f.items()):
        p = pos.get((sid, sym))
        ok = p is not None and abs(p["qty"] - q) < 1e-6 and abs(p["avg_price"] - cost) < 1e-3
        bad += not ok
        print(f"  {'ok ' if ok else 'BAD'} {sid:28} {sym:6} fills {q:>8.0f} @ {cost:10.4f}   table {p and p['qty']} @ {p and p['avg_price']}")
    for k in set(pos) - set(open_f):
        bad += 1
        print(f"  BAD {k} in positions but not in fills")
    sids = sorted(set(realized_f) | set(realized_t) | set(unreal) | set(fund_rows) | set(old_chart) | set(new_chart))
    print("\n{:28} {:>11} {:>11} {:>10} {:>12} {:>12} {:>12} {:>12}".format(
        "strategy", "realized(f)", "trades", "open", "since set", "(old)", "chart ALL", "(old)"))
    for sid in sids:
        rf, rt = realized_f.get(sid, 0.0), realized_t.get(sid, 0.0) or 0.0
        if abs(rf - rt) > 0.05:
            bad += 1
        f = fund_rows.get(sid)
        new_ss = old_ss = None
        if f:
            base = f["base_pnl"] if f.get("base_pnl") is not None else P.lifetime_at(c, f["base_ts"], rz).get(sid, 0.0)
            new_ss, old_ss = life.get(sid, 0.0) - base, old_since_set(c, sid, f["base_ts"])
        fmt = lambda v: "—" if v is None else f"{v:,.2f}"  # noqa: E731
        print("{:28} {:>11} {:>11} {:>10} {:>12} {:>12} {:>12} {:>12}{}".format(
            sid[:28], fmt(rf), fmt(rt), fmt(unreal.get(sid, 0.0)), fmt(new_ss), fmt(old_ss),
            fmt(new_chart.get(sid)), fmt(old_chart.get(sid)), "" if abs(rf - rt) <= 0.05 else "  <- trades != fills"))
    print("\nlifetime = realized + open:", {k: round(v, 2) for k, v in life.items()})
    print("fund rows set:", {k: time.strftime("%m-%d %H:%M", time.localtime(v["base_ts"])) for k, v in fund_rows.items() if k in run})
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()

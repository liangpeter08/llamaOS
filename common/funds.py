"""Fund allocations and money monitoring in PostgreSQL (shared by broker and api).

The account view is built from what the broker persists every sync (kv 'account', the
positions table, working intents and sleeve orders), so the dashboard and the checks the
broker runs see the same numbers.
"""
from __future__ import annotations

import time
from datetime import date, time as dtime

from core import funds as F

from . import audit, db, registry
from . import strategy_pnl as P
from . import timeutil as T
from .config import RiskConfig

WORKING_INTENTS = ("approved", "submitting", "submitted")
WORKING_SLEEVE = ("approved", "submitting", "submitted")  # a "rechecking" order is the one being checked


def default_allocation(s, risk: RiskConfig) -> float:
    cfg = risk.funds
    if getattr(s, "kind", "intraday") == "portfolio":
        return min(s.capital * s.max_holding, cfg.max_strategy_allocation)
    return cfg.default_allocation


def share(r: dict, equity: float) -> float:
    """A fund row's allocation as a share of equity now (a fixed-dollar allocation moves with equity)."""
    if r.get("alloc_usd") is not None:
        return r["alloc_usd"] / equity if equity > 0 else 0.0
    return r["allocation"]


def reserved_share(r: dict, equity: float) -> float:
    """The part of a fund row held for it while idle, as a share of equity."""
    return share(r, equity) * (r["reserved"] if r.get("reserved") is not None else 1.0)


def base_usd(r: dict, equity: float) -> float:
    return r["alloc_usd"] if r.get("alloc_usd") is not None else r["allocation"] * equity


def running(conn, risk: RiskConfig) -> dict[str, object]:
    """sid -> newest running (paper/live) strategy object, plus lines that still hold positions."""
    out: dict[str, object] = {}
    for kind in ("intraday", "portfolio"):
        for _, s in registry.runnable(conn, risk, kind=kind):
            out[s.id] = s
    held = {r["sid"] for r in db.rows(conn, "SELECT DISTINCT sid FROM positions WHERE sid IS NOT NULL")}
    for sid in held - set(out):
        r = db.row(conn, "SELECT hash FROM strategies WHERE sid=? ORDER BY version DESC LIMIT 1", (sid,))
        if r:
            s, _ = registry.load(conn, r["hash"], risk)
            if s is not None:
                out[sid] = s
    return out


def equity_now(conn) -> tuple[float, float]:
    a = db.kv_get(conn, "account") or {}
    eq = float(a.get("equity") or 0.0)
    return eq, float(a.get("cash", eq) or 0.0)


def ensure(conn, risk: RiskConfig, s, actor: str = "default", allocation: float | None = None,
           alloc_usd: float | None = None) -> dict:
    """The allocation row of a strategy line, created with defaults the first time it runs.

    `alloc_usd` makes it a fixed-dollar allocation (`allocation` then records its share when set)."""
    r = db.row(conn, "SELECT * FROM strategy_funds WHERE sid=?", (s.id,))
    if r:
        return r
    cfg = risk.funds
    eq, _ = equity_now(conn)
    if alloc_usd is not None:
        alloc = alloc_usd / eq if eq > 0 else 0.0
    else:
        alloc = allocation if allocation is not None else default_allocation(s, risk)
    now = time.time()
    conn.execute("INSERT INTO strategy_funds(sid, allocation, alloc_usd, max_drawdown, daily_loss, status, base_usd, base_ts,"
                 " peak_pnl, base_pnl, source, updated_by, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(sid) DO NOTHING",
                 (s.id, alloc, alloc_usd, cfg.strategy_max_drawdown, cfg.strategy_daily_loss if s.kind == "intraday" else None,
                  "active", alloc_usd if alloc_usd is not None else alloc * eq, now, 0.0, P.lifetime_now(conn).get(s.id, 0.0),
                  "set" if actor != "default" else "default", actor, now))
    audit.log(conn, "funds.allocated", actor, {"sid": s.id, "allocation": alloc, "alloc_usd": alloc_usd, "source": actor})
    return db.row(conn, "SELECT * FROM strategy_funds WHERE sid=?", (s.id,))


def account(conn, risk: RiskConfig, strategies: dict | None = None) -> F.Account:
    """Money view of the account and every running strategy line."""
    strategies = strategies if strategies is not None else running(conn, risk)
    eq, cash = equity_now(conn)
    pos = db.rows(conn, "SELECT symbol, qty, avg_price, last_price, sid FROM positions")
    working_i = db.rows(conn, f"SELECT sid, qty, limit_price FROM intents WHERE status IN {WORKING_INTENTS} AND side='long'")
    working_p = db.rows(conn, f"SELECT sid, qty, filled_qty, ref_price FROM portfolio_orders WHERE status IN {WORKING_SLEEVE} AND side='buy'")
    deployed_by: dict[str, float] = {}
    for p in pos:
        deployed_by[p["sid"] or "unmanaged"] = deployed_by.get(p["sid"] or "unmanaged", 0.0) + abs(p["qty"] * (p["last_price"] or p["avg_price"] or 0))
    working = 0.0
    for w in working_i:
        v = (w["qty"] or 0) * (w["limit_price"] or 0)
        deployed_by[w["sid"]] = deployed_by.get(w["sid"], 0.0) + v
        working += v
    for w in working_p:
        v = (w["qty"] - w["filled_qty"]) * (w["ref_price"] or 0)
        deployed_by[w["sid"]] = deployed_by.get(w["sid"], 0.0) + v
        working += v
    today = T.ts_at(T.et(time.time()).date(), dtime(0, 0))  # since midnight ET
    rows = {r["sid"]: r for r in db.rows(conn, "SELECT * FROM strategy_funds")}
    realized = P.Realized(conn)
    life = P.lifetime_now(conn, realized)
    at_today: dict[str, float] | None = None
    out: dict[str, F.StrategyFund] = {}
    for sid, s in strategies.items():
        r = rows.get(sid) or ensure(conn, risk, s)
        if r.get("base_pnl") is None:
            r = backfill_base(conn, r, realized, life)
        # since set / today = lifetime P&L now minus lifetime P&L then: a position opened before the base
        # counts only for its move after it, and a sale after the base doesn't re-count earlier gains
        if r["base_ts"] >= today:
            start = r["base_pnl"]
        else:
            at_today = at_today if at_today is not None else P.lifetime_at(conn, today, realized)
            start = at_today.get(sid, 0.0)
        pnl = life.get(sid, 0.0) - r["base_pnl"]
        out[sid] = F.StrategyFund(
            sid=sid, kind=s.kind, allocation=share(r, eq), fixed_usd=r["alloc_usd"], source=r["source"], max_drawdown=r["max_drawdown"],
            daily_loss=r["daily_loss"], status=r["status"], reason=r["reason"] or "", base_usd=r["base_usd"],
            base_ts=r["base_ts"], peak_pnl=max(r["peak_pnl"], pnl), deployed=deployed_by.get(sid, 0.0),
            pnl=pnl, pnl_today=life.get(sid, 0.0) - start, reserved=r["reserved"] if r.get("reserved") is not None else 1.0)
    intraday = sum(f.deployed for f in out.values() if f.kind == "intraday")
    peak = max(float(db.kv_get(conn, "funds.peak_equity") or 0.0), eq)
    return F.Account(equity=eq, cash=cash, deployed=sum(deployed_by.values()), intraday_deployed=intraday,
                     working_buys=working, peak_equity=peak, strategies=out)


def backfill_base(conn, r: dict, realized: P.Realized, life: dict[str, float]) -> dict:
    """Fill base_pnl of a row set before it was recorded: the line's lifetime P&L at base_ts, from its trades and
    the last equity mark then. Its stored peak was measured on the old basis (open P&L since entry, even from
    before the base), so the peak is rebuilt from the marks since base_ts."""
    sid = r["sid"]
    base = P.lifetime_at(conn, r["base_ts"], realized).get(sid, 0.0)
    peak = max([0.0, life.get(sid, 0.0) - base] + [m.get(sid, 0.0) - base for _, m in P.marks(conn, r["base_ts"], realized=realized)])
    conn.execute("UPDATE strategy_funds SET base_pnl=?, peak_pnl=? WHERE sid=? AND base_pnl IS NULL", (base, peak, sid))
    audit.log(conn, "funds.base_pnl_filled", "system", {"sid": sid, "base_ts": r["base_ts"], "base_pnl": round(base, 2),
                                                         "peak_pnl": round(peak, 2), "old_peak_pnl": r["peak_pnl"]})
    return {**r, "base_pnl": base, "peak_pnl": peak}


def monitor(conn, risk: RiskConfig, actor: str = "broker") -> str:
    """Track peaks, pause strategies that breach their limits; returns a kill reason on an account drawdown breach."""
    if risk.funds is None:
        return ""
    acct = account(conn, risk)
    if acct.equity > 0:
        db.kv_set(conn, "funds.peak_equity", acct.peak_equity)
    today = str(T.et(time.time()).date())
    for f in acct.strategies.values():
        r = db.row(conn, "SELECT status, paused_day, peak_pnl FROM strategy_funds WHERE sid=?", (f.sid,))
        if r["status"] == "paused_today" and r["paused_day"] != today:
            conn.execute("UPDATE strategy_funds SET status='active', reason=NULL, paused_day=NULL WHERE sid=?", (f.sid,))
            audit.log(conn, "funds.resumed", actor, {"sid": f.sid, "why": "new session"})
            f.status = "active"
        if f.peak_pnl > r["peak_pnl"]:
            conn.execute("UPDATE strategy_funds SET peak_pnl=? WHERE sid=?", (f.peak_pnl, f.sid))
        why = F.breach(f)
        if why and f.status == "active":
            status = "paused" if "drawdown" in why else "paused_today"
            conn.execute("UPDATE strategy_funds SET status=?, reason=?, paused_day=?, updated_at=? WHERE sid=?",
                         (status, why, today, time.time(), f.sid))
            audit.log(conn, "funds.paused", actor, {"sid": f.sid, "status": status, "reason": why})
    cfg = risk.funds
    if acct.peak_equity and acct.drawdown >= cfg.account_max_drawdown:
        return f"account drawdown {acct.drawdown:.1%} from peak ${acct.peak_equity:,.0f} reached funds.account_max_drawdown {cfg.account_max_drawdown:.0%}"
    return ""


def status(conn, sid: str) -> tuple[str, str]:
    r = db.row(conn, "SELECT status, reason FROM strategy_funds WHERE sid=?", (sid,))
    return (r["status"], r["reason"] or "") if r else ("active", "")


def set_allocation(conn, risk: RiskConfig, s, allocation: float | None, max_drawdown: float, daily_loss: float | None, actor: str,
                   alloc_usd: float | None = None, reserved: float | None = None) -> list[str]:
    """Owner sets a strategy line's ceiling, as a share of equity or (alloc_usd) a fixed dollar amount, and
    `reserved`, the part of it held while idle (None keeps the current one; 100% for a new line).

    A fixed amount is checked against the limits at today's equity. The drawdown base restarts from now."""
    cfg = risk.funds
    eq, _ = equity_now(conn)
    if alloc_usd is not None:
        if eq <= 0:
            return ["account equity is unknown yet; set a percent allocation or wait for the first broker sync"]
        if alloc_usd <= 0:
            return ["allocation must be more than $0"]
        allocation = alloc_usd / eq
    if reserved is None:
        cur = db.row(conn, "SELECT reserved FROM strategy_funds WHERE sid=?", (s.id,))
        reserved = cur["reserved"] if cur else 1.0
    run = running(conn, risk)
    others = {r["sid"]: reserved_share(r, eq) for r in db.rows(conn, "SELECT sid, allocation, alloc_usd, reserved FROM strategy_funds")
              if r["sid"] != s.id and r["sid"] in run}
    errs = F.validate_allocation(s.id, allocation, max_drawdown, daily_loss if s.kind == "intraday" else None, others, cfg, reserved)
    if errs:
        if alloc_usd is not None:
            errs = [f"${alloc_usd:,.0f} is {allocation:.1%} of equity ${eq:,.0f}: {e}" for e in errs]
        return errs
    ensure(conn, risk, s, actor, allocation, alloc_usd)
    acct = account(conn, risk, {s.id: s})
    pnl = acct.strategies[s.id].pnl
    deployed = acct.strategies[s.id].deployed
    conn.execute("UPDATE strategy_funds SET allocation=?, alloc_usd=?, reserved=?, max_drawdown=?, daily_loss=?, base_usd=?, base_ts=?,"
                 " peak_pnl=0, base_pnl=?, source='set', updated_by=?, updated_at=? WHERE sid=?",
                 (allocation, alloc_usd, reserved, max_drawdown, daily_loss if s.kind == "intraday" else None,
                  alloc_usd if alloc_usd is not None else allocation * eq, time.time(), P.lifetime_now(conn).get(s.id, 0.0),
                  actor, time.time(), s.id))
    audit.log(conn, "funds.allocated", actor, {"sid": s.id, "allocation": allocation, "alloc_usd": alloc_usd, "reserved": reserved,
                                                "max_drawdown": max_drawdown,
                                                "daily_loss": daily_loss, "pnl_before_rebase": pnl, "deployed": round(deployed, 2)})
    return []


def over_note(conn, risk: RiskConfig, s) -> str:
    """'' or a note that a line holds more than its allocation (shown when the owner lowers it below its holdings)."""
    acct = account(conn, risk, {s.id: s})
    return F.over_note(acct.strategies[s.id], acct.equity)


def unallocated(conn, risk: RiskConfig, exclude: str | None = None) -> float:
    """Share of equity not reserved by any running strategy (new reserves must fit in it; bursts draw on it)."""
    run = running(conn, risk)
    eq, _ = equity_now(conn)
    used = sum(reserved_share(r, eq) for r in db.rows(conn, "SELECT sid, allocation, alloc_usd, reserved FROM strategy_funds")
               if r["sid"] in run and r["sid"] != exclude)
    return risk.funds.max_deployed - used


def activity(conn, sid: str, days: int = 30) -> dict:
    """How often a line actually uses its money: sessions with an entry over the last `days`, and its busiest day."""
    since = time.time() - days * 86400
    rows = db.rows(conn, "SELECT entry_ts, qty, entry_price FROM trades WHERE sid=? AND entry_ts>=?", (sid, int(since)))
    by_day: dict[str, float] = {}
    for r in rows:
        d = str(T.et(r["entry_ts"]).date())
        by_day[d] = by_day.get(d, 0.0) + abs((r["qty"] or 0) * (r["entry_price"] or 0))
    for r in db.rows(conn, "SELECT day, filled_qty, avg_price FROM portfolio_orders WHERE sid=? AND side='buy' AND filled_qty>0 AND ts>=?",
                     (sid, since)):
        by_day[r["day"]] = by_day.get(r["day"], 0.0) + r["filled_qty"] * (r["avg_price"] or 0)
    sessions = sum(1 for i in range(days) if T.et(since + i * 86400).weekday() < 5)
    return {"days": days, "sessions": sessions, "active_sessions": len(by_day), "busiest_usd": max(by_day.values(), default=0.0)}


def pause(conn, sid: str, reason: str, actor: str):
    conn.execute("UPDATE strategy_funds SET status='paused', reason=?, paused_day=?, updated_by=?, updated_at=? WHERE sid=?",
                 (reason, str(T.et(time.time()).date()), actor, time.time(), sid))
    audit.log(conn, "funds.paused", actor, {"sid": sid, "status": "paused", "reason": reason})


def resume(conn, sid: str, actor: str):
    """Owner resumes after reviewing; drawdown is measured from a fresh base so the old peak doesn't re-trigger it."""
    eq, _ = equity_now(conn)
    r = db.row(conn, "SELECT allocation, alloc_usd FROM strategy_funds WHERE sid=?", (sid,))
    conn.execute("UPDATE strategy_funds SET status='active', reason=NULL, paused_day=NULL, base_usd=?, base_ts=?, peak_pnl=0,"
                 " base_pnl=?, updated_by=?, updated_at=? WHERE sid=?",
                 (base_usd(r, eq), time.time(), P.lifetime_now(conn).get(sid, 0.0), actor, time.time(), sid))
    audit.log(conn, "funds.resumed", actor, {"sid": sid, "why": "owner"})


def sleeve_capital(conn, s, equity: float) -> float:
    """Dollars a new portfolio sleeve starts with: its rules' capital, scaled down so its peak
    (capital x max_holding) fits the strategy's fund allocation. Read once, at the sleeve's first session:
    a later allocation change doesn't resize the sleeve (its targets and drawdown peak are in dollars), so
    a lowered allocation only blocks its buys (core.funds.check_buy / over_note)."""
    rules = s.capital * equity
    r = db.row(conn, "SELECT allocation, alloc_usd FROM strategy_funds WHERE sid=?", (s.id,))
    if not r or not s.max_holding:
        return rules
    return min(rules, base_usd(r, equity) / s.max_holding)

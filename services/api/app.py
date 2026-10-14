"""Dashboard API (JSON) behind the Next.js frontend. Holds no broker or model keys.

Auth (password + TOTP), sessions, CSRF and every write the dashboard may make live here:
intent decisions, commands to the broker, registry status, theses and notes. Trading rules
(validator, backtester, risk guard) are the same Python code the broker runs. Model features
go through the internal models API.

    python -m services.api          # listens on 127.0.0.1:8080; Next.js proxies /api/* here
"""
from __future__ import annotations

import difflib
import json
import os
import re
import secrets
import time
from datetime import date, timedelta
from typing import Literal

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

from backtest import engine as bt
from common import audit, db, registry, sleeves, monitoring, broker_accounts as BA
from common import funds, market_inputs, news_claude, risk_settings as RS
from common.config import risk_defaults
from core import funds as F
from common.config import RiskConfig, load_risk, load_yaml, mode, risk_path
from dsl.explain import back_translate
from dsl.validator import validate

from . import auth

MODELS_URL = os.environ.get("MODELS_URL", "http://127.0.0.1:8081")

app = FastAPI(title="llamaOS API", docs_url=None, redoc_url=None, openapi_url=None)
_secret = os.environ.get("SESSION_SECRET") or secrets.token_urlsafe(32)
app.add_middleware(SessionMiddleware, secret_key=_secret, session_cookie="llamaos_session",
                   max_age=auth.SESSION_MAX_AGE, same_site="strict",
                   https_only=os.environ.get("COOKIE_SECURE", "false").lower() == "true")


def conn():
    return db.get()


# ---------------------------------------------------------------- dependencies
def user(request: Request) -> dict:
    u = auth.current(request)
    if not u:
        raise HTTPException(401, "login required")
    return u


def owner(request: Request) -> dict:
    u = auth.require_owner(request)
    auth.check_csrf(request, None)
    return u


def ok(msg: str = "", **kw) -> dict:
    return {"ok": True, "message": msg, **kw}


def fail(msg: str, status: int = 400):
    raise HTTPException(status, msg)


def models_api(path: str, payload: dict, timeout: float = 300) -> dict:
    try:
        with httpx.Client(trust_env=False, timeout=timeout) as client:
            r = client.post(f"{MODELS_URL}{path}", json=payload)
    except httpx.HTTPError as e:
        return {"error": f"models service unreachable ({type(e).__name__})"}
    if r.status_code >= 400:
        try:
            return {"error": r.json().get("detail", r.text)}
        except ValueError:
            return {"error": r.text}
    try:
        result = r.json()
    except ValueError:
        return {"error": "models service returned an invalid response"}
    if not isinstance(result, dict):
        return {"error": "models service returned an invalid response"}
    if result.get("detail") and not result.get("error"):
        return {"error": result["detail"]}
    return result


def command(kind: str, payload: dict, actor: str):
    conn().execute("INSERT INTO commands(ts, kind, payload, issued_by) VALUES (?,?,?,?)",
                   (time.time(), kind, json.dumps(payload), actor))


def risk_dict(r: RiskConfig) -> dict:
    d = {k: getattr(r, k) for k in r.__dataclass_fields__ if k not in ("raw", "entries", "flatten_by", "portfolio")}
    d["entries"] = f"{r.entries[0]:%H:%M}-{r.entries[1]:%H:%M}"
    d["flatten_by"] = f"{r.flatten_by:%H:%M}"
    if r.portfolio:
        p = {k: getattr(r.portfolio, k) for k in r.portfolio.__dataclass_fields__}
        p["decide_at"] = f"{r.portfolio.decide_at:%H:%M}"
        d["portfolio"] = p
    else:
        d["portfolio"] = None
    return d


def j(s, default=None):
    if s is None or s == "":
        return default
    return json.loads(s) if isinstance(s, str) else s


# ---------------------------------------------------------------- auth
class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)
    code: str = Field(default="", max_length=8)


@app.get("/api/setup")
def setup():
    return {"has_users": db.row(conn(), "SELECT COUNT(*) n FROM users")["n"] > 0}


@app.post("/api/login")
def login(request: Request, body: LoginIn):
    key = f"{request.client.host if request.client else '?'}:{body.username}"
    c = conn()
    if auth.rate_limited(key):
        audit.log(c, "login.rate_limited", body.username, {})
        fail("Too many attempts. Wait 10 minutes.", 429)
    u = auth.authenticate(c, body.username, body.password, body.code)
    if not u:
        auth.note_failure(key)
        audit.log(c, "login.failed", body.username, {})
        fail("Invalid username, password or code.", 401)
    auth.login(request, u)
    audit.log(c, "login.ok", body.username, {"role": u["role"]})
    return me(request)


@app.post("/api/logout")
def logout(request: Request, u: dict = Depends(user)):
    auth.check_csrf(request, None)
    auth.revoke(request)
    request.session.clear()
    return ok()


@app.get("/api/me")
def me(request: Request):
    u = user(request)
    return {"username": u["username"], "role": u["role"], "csrf": u["csrf"]}


@app.get("/api/status")
def status(request: Request, u: dict = Depends(user)):
    c = conn()
    return {
        "sim": db.kv_get(c, "sim.state") or {}, "sim_now": db.kv_get(c, "sim.now"),
        "kill": db.kv_get(c, "kill_switch") or {}, "mode": mode(),
        "pending_count": db.row(c, "SELECT COUNT(*) n FROM intents WHERE status='pending'")["n"],
    }


# ---------------------------------------------------------------- overview
def health(c) -> dict:
    out = {}
    for k in ("broker", "models", "watchdog", "operations", "triage"):
        age = db.kv_age(c, f"health.{k}")
        out[k] = {"age": age, "ok": age is not None and age < 30}
    # The broker loop can run long on a bad network while the process is fine: "slow", not "down".
    proc = db.kv_age(c, "health.broker_process")
    out["broker"]["process_age"] = proc
    out["broker"]["state"] = "up" if out["broker"]["ok"] else "slow" if proc is not None and proc < 30 else "down"
    out["reconcile"] = db.kv_get(c, "reconcile") or {}
    out["operations"] = {**out["operations"], **(db.kv_get(c,"health.operations") or {})}
    out["backup"] = db.kv_get(c, "backup.latest")
    out["paper_error"] = db.kv_get(c, "paper.error")
    out["last_bar"] = db.kv_get(c, "feed.last_bar")
    return out


@app.get("/api/overview")
def overview(u: dict = Depends(user)):
    c = conn()
    risk = load_risk()
    acct = db.kv_get(c, "account") or {}
    start = acct.get("start_equity")
    return {
        "acct": acct, "risk": risk_dict(risk),
        "loss_limit": risk.max_daily_loss * start if start else None,
        "exposure_cap": risk.max_gross_exposure * acct["equity"] if acct.get("equity") else None,
        "curve": [r["equity"] for r in c.execute("SELECT equity FROM equity_curve ORDER BY ts")],
        "positions": db.rows(c, "SELECT * FROM positions ORDER BY symbol"),
        "health": health(c),
        "recent": db.rows(c, "SELECT id, ts, kind, actor FROM audit ORDER BY id DESC LIMIT 10"),
        "trades": db.rows(c, "SELECT id, symbol, sid, exit_kind, pnl FROM trades ORDER BY id DESC LIMIT 6"),
    }


# ---------------------------------------------------------------- equity history
RANGES = ("1D", "1W", "1M", "3M", "YTD", "ALL", "custom")
MAX_POINTS = 600


def _shift_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    days = [31, 29 if y % 4 == 0 and (y % 100 or y % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    return date(y, m, min(d.day, days[m - 1]))


def _range_bounds(rng: str, first: date, last: date, start: str, end: str) -> tuple[date, date]:
    if rng == "custom":
        try:
            lo = date.fromisoformat(start) if start else first
            hi = date.fromisoformat(end) if end else last
        except ValueError:
            fail("dates must be YYYY-MM-DD")
        if lo > hi:
            fail("start date is after end date")
        return lo, hi
    lo = {
        "1D": last, "1W": last - timedelta(days=6), "1M": _shift_months(last, -1) + timedelta(days=1),
        "3M": _shift_months(last, -3) + timedelta(days=1), "YTD": date(last.year, 1, 1), "ALL": first,
    }[rng]
    return max(lo, first), last


def _decimate(values: list[float], max_points: int) -> list[int]:
    """Indices to keep: min and max of each bucket (keeps peaks and troughs), plus the ends."""
    n = len(values)
    if n <= max_points:
        return list(range(n))
    size = max(1, -(-n // (max_points // 2)))
    keep = {0, n - 1}
    for b in range(0, n, size):
        chunk = range(b, min(b + size, n))
        keep.add(min(chunk, key=values.__getitem__))
        keep.add(max(chunk, key=values.__getitem__))
    return sorted(keep)


def _strategy_order(c) -> list[str]:
    """Stable label order (registry creation order) so a strategy keeps its color across ranges."""
    return [f"{r['sid']} v{r['version']}" for r in c.execute("SELECT sid, version FROM strategies ORDER BY created_at, sid, version")]


@app.get("/api/equity")
def equity(range: str = "1M", start: str = "", end: str = "", u: dict = Depends(user)):
    if range not in RANGES:
        fail(f"range must be one of {', '.join(RANGES)}")
    c = conn()
    sessions = [r["session"] for r in c.execute("SELECT DISTINCT session FROM equity_history ORDER BY session")]
    if not sessions:
        return {"empty": True, "range": range}
    first, last = date.fromisoformat(sessions[0]), date.fromisoformat(sessions[-1])
    lo, hi = _range_bounds(range, first, last, start, end)
    rows = db.rows(c, "SELECT ts, source, session, equity, strat_pnl FROM equity_history"
                      " WHERE session >= ? AND session <= ? ORDER BY ts", (lo.isoformat(), hi.isoformat()))
    live_sessions = {r["session"] for r in rows if r["source"] == "live"}
    rows = [r for r in rows if r["source"] == "live" or r["session"] not in live_sessions]  # live wins over replay
    if not rows:
        return {"empty": True, "range": range, "lo": lo.isoformat(), "hi": hi.isoformat(),
                "first": first.isoformat(), "last": last.isoformat()}

    prev = db.row(c, "SELECT equity FROM equity_history WHERE ts < ? ORDER BY ts DESC LIMIT 1", (rows[0]["ts"],))
    replay_info = db.kv_get(c, "equity.replay") or {}
    base = prev["equity"] if prev else float(replay_info.get("start") or load_yaml("sim.yaml").get("starting_equity", 100_000))

    # cumulative per-strategy P&L inside the range: earlier sessions' finals + this session so far
    order = _strategy_order(c)
    labels: list[str] = []
    carry: dict[str, float] = {}
    last_pnl: dict[str, float] = {}
    cur_session = None
    eq, cum, daily, starts = [], [], [], []
    prev_close = base
    for k, r in enumerate(rows):
        if r["session"] != cur_session:
            if cur_session is not None:
                for lbl, v in last_pnl.items():
                    carry[lbl] = carry.get(lbl, 0.0) + v
                daily[-1]["close"] = eq[-1]
            cur_session, last_pnl = r["session"], {}
            starts.append(k)
            daily.append({"session": r["session"], "source": r["source"], "open": prev_close})
        pnl = json.loads(r["strat_pnl"] or "{}")
        last_pnl = pnl
        for lbl in pnl:
            if lbl not in labels:
                labels.append(lbl)
        eq.append(r["equity"])
        cum.append({lbl: carry.get(lbl, 0.0) + pnl.get(lbl, 0.0) for lbl in set(carry) | set(pnl)})
        prev_close = r["equity"]
    daily[-1]["close"] = eq[-1]
    finals = {lbl: carry.get(lbl, 0.0) + last_pnl.get(lbl, 0.0) for lbl in set(carry) | set(last_pnl)}
    session_final: dict[str, dict] = {}
    # per-session strategy P&L for the table: difference of cumulative values at session ends
    ends = starts[1:] + [len(rows)]
    before: dict[str, float] = {}
    for dday, end_i in zip(daily, ends):
        at = cum[end_i - 1]
        dday["by_strategy"] = {lbl: round(at.get(lbl, 0.0) - before.get(lbl, 0.0), 2) for lbl in labels}
        before = at
        dday["change"] = dday["close"] - dday["open"]
        dday["change_pct"] = dday["change"] / dday["open"] if dday["open"] else None
        session_final[dday["session"]] = dday
    labels.sort(key=lambda lbl: (order.index(lbl) if lbl in order else len(order), lbl))

    peak, mdd = base, 0.0
    for v in eq:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    keep = _decimate(eq, MAX_POINTS)
    pos = {k: n for n, k in enumerate(keep)}
    # session starts mapped onto the decimated index (first kept point at or after the start)
    kept_starts = []
    for sidx in starts:
        n = next((pos[k] for k in keep if k >= sidx), len(keep) - 1)
        kept_starts.append(n)
    changes = [d for d in daily if d["change_pct"] is not None]
    end_eq = eq[-1]
    return {
        "range": range, "lo": lo.isoformat(), "hi": hi.isoformat(), "first": first.isoformat(), "last": last.isoformat(),
        "intraday": len(daily) == 1, "base": base,
        "ts": [rows[k]["ts"] for k in keep],
        "equity": [round(eq[k], 2) for k in keep],
        "source": [rows[k]["source"] for k in keep],
        "strategies": labels,
        "strategy_pnl": {lbl: [round(cum[k].get(lbl, 0.0), 2) for k in keep] for lbl in labels},
        "sessions": [{"session": d["session"], "index": i, "source": d["source"]} for d, i in zip(daily, kept_starts)],
        "stats": {
            "start": base, "end": end_eq, "change": end_eq - base, "change_pct": end_eq / base - 1,
            "high": max(eq), "low": min(eq), "max_drawdown": mdd, "sessions": len(daily),
            "best_day": max(changes, key=lambda d: d["change"], default=None),
            "worst_day": min(changes, key=lambda d: d["change"], default=None),
            "points": len(rows),
        },
        "by_strategy": [{"strategy": lbl, "pnl": round(finals.get(lbl, 0.0), 2), "return_pct": finals.get(lbl, 0.0) / base,
                         "share": (finals.get(lbl, 0.0) / (end_eq - base)) if end_eq != base else None} for lbl in labels],
        "daily": [{k: v for k, v in d.items()} for d in daily],
        "replay": replay_info,
    }


# ---------------------------------------------------------------- approvals
@app.get("/api/approvals")
def approvals(request: Request, u: dict = Depends(user)):
    c = conn()
    pending = db.rows(c, "SELECT * FROM intents WHERE status='pending' ORDER BY id")
    for it in pending:
        bars = db.rows(c, "SELECT c FROM bars WHERE symbol=? AND ts<=? ORDER BY ts DESC LIMIT 90", (it["symbol"], it["ts"]))
        it["bars"] = [b["c"] for b in reversed(bars)]
        last = db.row(c, "SELECT c FROM bars WHERE symbol=? ORDER BY ts DESC LIMIT 1", (it["symbol"],))
        it["last"] = last["c"] if last else None
        for k in ("decision", "signals", "guard", "recheck"):
            it[k] = j(it[k])
    recent = db.rows(c, "SELECT id, ts, sid, symbol, qty, entry, status, decided_by, reason FROM intents"
                        " WHERE status!='pending' ORDER BY id DESC LIMIT 20")
    sleeve_pending = db.rows(c, "SELECT * FROM portfolio_orders WHERE status='pending' ORDER BY id")
    for o in sleeve_pending:
        o["decision"], o["guard"] = j(o["decision"], []), j(o["guard"], [])
    sleeve_recent = db.rows(c, "SELECT id, day, sid, symbol, side, qty, ref_price, reason, status, decided_by, note"
                               " FROM portfolio_orders WHERE status!='pending' ORDER BY id DESC LIMIT 20")
    return {"pending": pending, "recent": recent, "portfolio_pending": sleeve_pending, "portfolio_recent": sleeve_recent, "acct": db.kv_get(c, "account") or {},
            "risk": risk_dict(load_risk()), "server_time": time.time()}


class DecideIn(BaseModel):
    reason: str = ""


@app.post("/api/approvals/{iid}/{action}")
def decide(iid: int, action: str, body: DecideIn, u: dict = Depends(owner)):
    c = conn()
    it = db.row(c, "SELECT * FROM intents WHERE id=?", (iid,))
    if not it or it["status"] != "pending":
        fail(f"Intent #{iid} is no longer pending.", 409)
    if time.time() > it["expires_wall"]:
        fail(f"Intent #{iid} expired before your decision.", 409)
    if action == "approve":
        c.execute("UPDATE intents SET status='approved', decided_by=?, decided_wall=?, reason=? WHERE id=? AND status='pending'",
                  (u["username"], time.time(), body.reason, iid))
        audit.log(c, "intent.approved", u["username"], {"intent_id": iid, "reason": body.reason})
        return ok(f"Approved #{iid}. The broker re-runs the risk guard and drift check before submitting.")
    if action == "reject":
        c.execute("UPDATE intents SET status='rejected', decided_by=?, decided_wall=?, reason=? WHERE id=? AND status='pending'",
                  (u["username"], time.time(), body.reason, iid))
        audit.log(c, "intent.rejected", u["username"], {"intent_id": iid, "reason": body.reason})
        return ok(f"Rejected #{iid}.")
    fail("unknown action")


@app.post("/api/portfolio-orders/{oid}/{action}")
def decide_sleeve_order(oid: int, action: str, body: DecideIn, u: dict = Depends(owner)):
    c = conn()
    o = db.row(c, "SELECT * FROM portfolio_orders WHERE id=?", (oid,))
    if not o or o["status"] != "pending":
        fail(f"Portfolio order #{oid} is no longer pending.", 409)
    if time.time() > o["expires_wall"]:
        fail(f"Portfolio order #{oid} expired before your decision.", 409)
    if action not in ("approve", "reject"):
        fail("unknown action")
    status = "approved" if action == "approve" else "rejected"
    c.execute("UPDATE portfolio_orders SET status=?, decided_by=?, decided_wall=?, note=? WHERE id=? AND status='pending'",
              (status, u["username"], time.time(), body.reason or None, oid))
    audit.log(c, f"portfolio_order.{status}", u["username"], {"order_id": oid, "reason": body.reason})
    if status == "approved":
        return ok(f"Approved portfolio order #{oid}. The broker re-runs the portfolio risk checks before sending it.")
    return ok(f"Rejected portfolio order #{oid}.")


# ---------------------------------------------------------------- portfolio / trades
@app.get("/api/portfolio")
def portfolio(u: dict = Depends(user)):
    c = conn()
    trades = db.rows(c, "SELECT * FROM trades ORDER BY id DESC")
    by: dict = {"strategy": {}, "symbol": {}}
    for t in trades:
        for grp, key in (("strategy", t["sid"] or "—"), ("symbol", t["symbol"])):
            d = by[grp].setdefault(key, {"n": 0, "pnl": 0.0, "wins": 0})
            d["n"] += 1
            d["pnl"] += t["pnl"]
            d["wins"] += t["pnl"] > 0
    return {"positions": db.rows(c, "SELECT * FROM positions ORDER BY symbol"),
            "orders": db.rows(c, "SELECT * FROM orders ORDER BY id DESC LIMIT 50"),
            "fills": db.rows(c, "SELECT * FROM fills ORDER BY id DESC LIMIT 50"),
            "trades": trades, "by": by, "acct": db.kv_get(c, "account") or {}, "risk": risk_dict(load_risk())}


@app.get("/api/trades/{tid}")
def trade(tid: int, u: dict = Depends(user)):
    c = conn()
    t = db.row(c, "SELECT * FROM trades WHERE id=?", (tid,))
    if not t:
        fail("no such trade", 404)
    it = db.row(c, "SELECT * FROM intents WHERE id=?", (t["intent_id"],)) if t["intent_id"] else None
    if it:
        for k in ("decision", "signals", "guard", "recheck"):
            it[k] = j(it[k])
    fills = db.rows(c, "SELECT * FROM fills WHERE intent_id=? ORDER BY ts", (t["intent_id"],)) if t["intent_id"] else []
    bars = db.rows(c, "SELECT ts, c FROM bars WHERE symbol=? AND ts>=? AND ts<=? ORDER BY ts",
                   (t["symbol"], t["entry_ts"] - 45 * 60, t["exit_ts"] + 15 * 60))
    return {"trade": t, "intent": it, "fills": fills, "bars": bars}


# ---------------------------------------------------------------- strategies
@app.get("/api/strategies")
def strategies(u: dict = Depends(user)):
    c = conn()
    risk = load_risk()
    registry.sync(c, risk)
    rows = db.rows(c, "SELECT s.*, k.category FROM strategies s LEFT JOIN strategy_categories k ON k.sid=s.sid"
                      " ORDER BY s.sid, s.version DESC")
    for r in rows:
        b = j(r.pop("backtest"))
        r["bt"] = {"metrics": b["metrics"], "days": len(b["days"])} if b else None
        text = registry.text_of(c, r["hash"]) or ""
        # Validate against the current risk controls: the owner can change them at any time.
        r["validation"] = validate(text, risk).as_dict() if text else j(r["validation"], {})
        r["sim"] = db.row(c, "SELECT COUNT(*) n, COALESCE(SUM(pnl),0) pnl FROM trades WHERE strategy_hash=?", (r["hash"],))
        r["kind"] = "portfolio" if "kind: portfolio" in text else "intraday"
    return {"rows": rows, "categories": [r["category"] for r in db.rows(c, "SELECT DISTINCT category FROM strategy_categories ORDER BY category")]}


@app.get("/api/strategies/{h}")
def strategy(h: str, u: dict = Depends(user)):
    c = conn()
    risk = load_risk()
    row = db.row(c, "SELECT * FROM strategies WHERE hash=?", (h,))
    if not row:
        fail("no such strategy", 404)
    text = registry.text_of(c, h) or ""
    v = validate(text, risk)
    s = v.strategy
    kind = s.kind if s else ("portfolio" if "kind: portfolio" in text else "intraday")
    return {
        "kind": kind, "portfolio": portfolio_view(c, h) if kind == "portfolio" else None,
        "monitoring": monitoring.coverage(c, s) if s else {"required": [], "unwatched": []},
        "decide_at": f"{s.decide_at:%H:%M}" if kind == "portfolio" and s else None,
        "fund": db.row(c, "SELECT allocation, alloc_usd, status, reason, max_drawdown FROM strategy_funds WHERE sid=?", (row["sid"],)),
        "fund_suggest": fund_suggest(c, risk, s) if s and risk.funds and row["status"] in ("draft", "backtested") else None,
        "s": {k: v_ for k, v_ in row.items() if k not in ("backtest", "validation")},
        "text": text, "integrity": v.hash == h,
        "validation": v.as_dict(),
        "assumptions": s.assumptions if s else [], "suggestions": s.suggestions if s else [],
        "source": s.source if s else {}, "back_translation": back_translate(s) if s else None,
        "backtest": j(row["backtest"]),
        "versions": db.rows(c, "SELECT hash, version, status, archived_at FROM strategies WHERE sid=? ORDER BY version", (row["sid"],)),
        "category": (db.row(c, "SELECT category FROM strategy_categories WHERE sid=?", (row["sid"],)) or {}).get("category"),
        "categories": [r["category"] for r in db.rows(c, "SELECT DISTINCT category FROM strategy_categories ORDER BY category")],
        "delete_blockers": registry.delete_blockers(c, h),
        "saved_runs": db.row(c, "SELECT COUNT(*) n FROM backtest_runs WHERE strategy_hash=?", (h,))["n"],
        "intents": db.rows(c, "SELECT id, ts, symbol, qty, status FROM intents WHERE strategy_hash=? ORDER BY id DESC LIMIT 20", (h,)),
        "trades": db.rows(c, "SELECT id, symbol, exit_kind, pnl FROM trades WHERE strategy_hash=? ORDER BY id DESC LIMIT 20", (h,)),
        "lifecycle": load_yaml("lifecycle.yaml"),
        "discovery": discovery_links(c, h, s),
    }


def discovery_links(c, h: str, s) -> dict:
    """Where this file came from in discovery, follow-up runs seeded from it, and whether it can be iterated (one ticker)."""
    syms = (list(s.symbols) if s.kind == "portfolio" else list(s.universe_symbols)) if s else []
    made = db.row(c, "SELECT t.id trial_id, t.run_id, t.holdout FROM discovery_trials t WHERE t.strategy_hash=? ORDER BY t.id DESC LIMIT 1", (h,))
    follow = [r for r in db.rows(c, "SELECT id, status, created_at, config FROM discovery_runs ORDER BY id DESC LIMIT 200")
              if (j(r["config"]) or {}).get("seed_hash") == h][:10]
    return {"ticker": syms[0] if len(syms) == 1 else None, "kind": ("daily" if s.kind == "portfolio" else "intraday") if s else None,
            "made_by": {**made, "holdout": j(made["holdout"])} if made else None,
            "follow_ups": [{k: r[k] for k in ("id", "status", "created_at")} for r in follow]}


def portfolio_view(c, h: str) -> dict:
    """Live sleeve: state, shares held (replayed from fills), recent orders and decisions."""
    sl = sleeves.load_state(c, h)
    hold, realized, _ = sleeves.holdings(c, h)
    last = {r["symbol"]: r["last_price"] for r in db.rows(c, "SELECT symbol, last_price FROM positions")}
    rows = []
    for sym, x in sorted(hold.items()):
        if x.qty:
            px = last.get(sym)
            base = sl.symbols[sym].base_usd if sl and sym in sl.symbols else None
            rows.append({"symbol": sym, "qty": x.qty, "cost": x.cost, "last": px, "value": x.qty * px if px else None,
                         "unrealized": (px - x.cost) * x.qty if px else None, "holding": x.qty * px / base if px and base else None,
                         "mode": sl.symbols[sym].mode if sl and sym in sl.symbols else None})
    day = db.row(c, "SELECT day, records, event FROM portfolio_days WHERE strategy_hash=? ORDER BY day DESC LIMIT 1", (h,))
    out = {
        "state": sl.to_json() if sl else None, "realized": realized, "holdings": rows,
        "orders": db.rows(c, "SELECT id, day, symbol, side, qty, ref_price, reason, status, filled_qty, avg_price, note, reserve_qty, overlay"
                             " FROM portfolio_orders WHERE strategy_hash=? ORDER BY id DESC LIMIT 60", (h,)),
        "last_day": {"day": day["day"], "event": day["event"], "records": j(day["records"], [])} if day else None,
    }
    s, _ = registry.load(c, h, load_risk())
    v2 = getattr(s, "v2", None) if s else None
    if v2 is not None:
        locked = sleeves.locked_cash(c, h)
        out["overlays"] = {
            "file_mode": v2.mode, "enforced": sleeves.enforced(c, s), "owner_enabled": bool(db.kv_get(c, f"overlays.enforce.{h}")),
            "review_required": v2.review_required, "authoring": v2.authoring,
            "catalog_pinned": db.kv_get(c, f"scenario_catalog.pin.{h}") == v2.catalog_hash,
            "missing_feeds": market_inputs.missing_feeds(s), "input_symbols": s.input_symbols,
            "rules": [{"id": o.id, "scenario": o.scenario, "action": o.action, "metric": o.metric, "on": o.on,
                       "phase": (sl.overlays.get(o.id) or {}).get("phase", "normal") if sl else "normal",
                       "state": (sl.overlays.get(o.id) or {}) if sl else {}} for o in v2.overlays],
            "locked_cash": locked, "released_usd": sl.released_usd if sl else 0.0,
            "locked_effective": max(0.0, locked - (sl.released_usd if sl else 0.0)),
            "inputs": db.kv_get(c, f"portfolio.inputs.{h}"), "observed": db.kv_get(c, f"portfolio.observed.{h}"),
            "events": [{**e, "payload": j(e["payload"], {}), "context": j(e["context"], None)}
                       for e in db.rows(c, "SELECT id, day, ts, kind, overlay, mode, payload, context FROM portfolio_events"
                                           " WHERE strategy_hash=? ORDER BY id DESC LIMIT 60", (h,))],
            "assumptions": [{"id": a.id, "statement": a.statement, "monitored": a.monitored, "critical": a.critical,
                             "latest": db.row(c, "SELECT ts, tier, status, answers, actions, mode, error FROM assumption_checks"
                                                 " WHERE strategy_hash=? AND assumption_id=? ORDER BY id DESC LIMIT 1", (h, a.id))}
                            for a in v2.assumptions],
            "assumption_block": db.kv_get(c, f"assumption.block.{h}"),
        }
    return out


@app.get("/api/strategies/{h}/diff")
def strategy_diff(h: str, against: str, u: dict = Depends(user)):
    c = conn()
    a, b = registry.text_of(c, against) or "", registry.text_of(c, h) or ""
    d = "\n".join(difflib.unified_diff(a.splitlines(), b.splitlines(), against[:10], h[:10], lineterm=""))
    return {"diff": d or "(identical)"}


@app.post("/api/strategies/{h}/backtest")
def strategy_backtest(h: str, u: dict = Depends(owner)):
    from backtest import workspace as W
    c = conn()
    try:
        config = W.RunConfig(strategy_hash=h, name='Strategy quick backtest',
                             starting_equity=float(load_yaml('sim.yaml').get('starting_equity', 100_000)))
        rid = W.create_run(c, config, u['username'], submit=False)
        W.execute(db.url(), rid)
    except ValueError as e:
        fail(f"Can't backtest: {e}")
    run = db.row(c, 'SELECT report,error FROM backtest_runs WHERE id=?', (rid,))
    if run['error']:
        fail(f"Can't backtest: {run['error']}")
    report = j(run['report'])
    if report.get("kind") == "portfolio":
        m = report["metrics"]
        return ok(f"Backtest finished: {m['orders']} orders over {len(report['days'])} sessions, {m['return_pct']:+.2%} "
                  f"vs {m['basket_buy_hold_return_pct']:+.2%} for plain buy-and-hold. "
                  f"Saved as run #{rid}.", run_id=rid)
    return ok(f"Backtest finished: {report['metrics']['trades']} trades over {len(report['days'])} evaluated sessions. "
              f"Saved as run #{rid}; inspect diagnostics in Backtesting.", run_id=rid)


class PromoteIn(BaseModel):
    to: str
    reviewed: bool = False
    allocation: str | None = None  # paper only: '10%' of equity, or a dollar amount with alloc_mode 'usd'
    alloc_mode: Literal["pct", "usd"] = "pct"
    reserved: str | None = None  # share of the allocation held while idle ('25%'); omitted: 100%


def parse_reserved(text: str | None) -> float | None:
    """'25%' or '25' -> 0.25 of the allocation; None keeps the current reserve."""
    from common.units import parse_pct
    if text is None or not str(text).strip():
        return None
    v = str(text).strip()
    try:
        return parse_pct(v if v.endswith("%") else v + "%")
    except ValueError as e:
        fail(str(e))


def parse_allocation(text: str, mode: str) -> tuple[float | None, float | None]:
    """(share of equity, fixed dollars) from the owner's input; exactly one is set."""
    from common.units import parse_pct, parse_usd
    try:
        if mode == "usd":
            return None, parse_usd(text)
        v = text.strip()
        return parse_pct(v if v.endswith("%") else v + "%"), None
    except ValueError as e:
        fail(str(e))


@app.post("/api/strategies/{h}/promote")
def strategy_promote(h: str, body: PromoteIn, u: dict = Depends(owner)):
    c = conn()
    row = db.row(c, "SELECT * FROM strategies WHERE hash=?", (h,))
    if not row:
        fail("no such strategy", 404)
    if body.to == "retired":
        registry.set_status(c, h, "retired", u["username"])
        command("reload", {}, u["username"])
        if sleeves.load_state(c, h) is not None:
            return ok("Retired. The portfolio sleeve sells its holdings at the next daily decision time.")
        return ok("Retired. The broker stops new entries (open positions keep their bracket).")
    s, why = registry.load(c, h, load_risk())
    if not s:
        fail(f"Blocked: {why}")
    if body.to == "paper":
        if row["archived_at"]:
            fail("This version is archived. Unarchive it before promoting.", 409)
        if row["status"] not in ("draft", "backtested", "live"):
            fail(f"Only a draft or backtested version can go to paper (this one is {row['status']}).")
        v2 = getattr(s, "v2", None)
        if v2 is not None:
            gaps = market_inputs.missing_feeds(s)
            if gaps:
                fail(f"Blocked: no index feed configured for {', '.join(gaps)} (config/market_inputs.yaml). "
                     "The strategy can't silently use a proxy.")
            if v2.review_required and not body.reviewed:
                fail("This file's authoring.status is proposed_requires_review: confirm you reviewed its proposed defaults "
                     "(reviewed: true) to promote it.", 409)
            if v2.overlays and not v2.catalog_hash:
                fail("Blocked: config/scenario-catalog.yaml is missing.")
        risk = load_risk()
        fund_note = ""
        if risk.funds and body.allocation:
            share, usd = parse_allocation(body.allocation, body.alloc_mode)
            fr = db.row(c, "SELECT max_drawdown, daily_loss FROM strategy_funds WHERE sid=?", (s.id,))
            rsv = parse_reserved(body.reserved)
            errs = funds.set_allocation(c, risk, s, share, fr["max_drawdown"] if fr else risk.funds.strategy_max_drawdown,
                                        (fr["daily_loss"] if fr else risk.funds.strategy_daily_loss) if s.kind == "intraday" else None,
                                        u["username"], alloc_usd=usd, reserved=rsv)
            if errs:
                fail("Can't allocate: " + "; ".join(errs))
            fund_note = (f" Fund allocation: ${usd:,.0f} fixed" if usd is not None else f" Fund allocation: {share:.1%} of equity") + (
                f", {rsv:.0%} reserved." if rsv is not None and rsv < 1 else ".")
        elif risk.funds and not db.row(c, "SELECT sid FROM strategy_funds WHERE sid=?", (s.id,)):
            want = funds.default_allocation(s, risk)
            room = funds.unallocated(c, risk, exclude=s.id)
            need = s.capital if s.kind == "portfolio" else min(want, risk.funds.default_allocation)
            if room + 1e-9 < need:
                fail(f"Not enough unreserved capital: {s.id} needs at least {need:.1%} of equity and {max(room, 0):.1%} is free. "
                     "Enter an allocation with a smaller reserved part (the rest comes from the shared pool when it trades), "
                     "lower other strategies' reserves on the Funds page, or raise max deployed capital on the Risk page.")
            alloc = min(want, room)
            funds.ensure(c, risk, s, u["username"], alloc)
            fund_note = f" Fund allocation: {alloc:.1%} of equity" + (f" (it could use up to {want:.1%}; adjust on Funds)." if alloc < want - 1e-9 else ".")
        try:
            with db.tx(c):
                monitoring.activate(c, s, u["username"])
                db.kv_set(c, 'monitoring.strategy.' + h, True)
                if v2 is not None and v2.overlays:
                    db.kv_set(c, f"scenario_catalog.pin.{h}", v2.catalog_hash)
                    audit.log(c, "scenario_catalog.pinned", u["username"], {"hash": h, "catalog_hash": v2.catalog_hash,
                                                                           "reviewed_defaults": body.reviewed})
                registry.set_status(c, h, "paper", u["username"], paper_started_at=time.time())
                if row["status"] == "draft":
                    audit.log(c, "strategy.untested_promotion", u["username"], {"hash": h, "sid": row["sid"]})
        except ValueError as e:
            fail(str(e))
        command("reload", {}, u["username"])
        untested = " It was promoted without a backtest." if row["status"] == "draft" else ""
        return ok("Promoted to paper. The broker picks it up within a few seconds." + untested + fund_note)
    if body.to == "live":
        fail("Real-money execution is disabled; this release supports paper trading only.", 403)
    fail("unknown target")


class OverlayModeIn(BaseModel):
    enforce: bool
    reason: str = Field(default="", max_length=500)


@app.post("/api/strategies/{h}/overlays")
def strategy_overlays(h: str, body: OverlayModeIn, u: dict = Depends(owner)):
    """Owner paper enforcement of a 0.2 file's overlays. Only files that declare monitoring.mode: enforce can act."""
    c = conn()
    s, why = registry.load(c, h, load_risk())
    if not s or getattr(s, "v2", None) is None or not s.v2.overlays:
        fail(f"No portfolio overlays to enforce: {why if not s else 'not a DSL 0.2 file with overlays'}")
    if body.enforce and s.v2.mode != "enforce":
        fail("This version declares monitoring.mode: shadow, so its overlays only log decisions. "
             "Save a new version with mode: enforce to let them trade.", 409)
    if body.enforce and (db.row(c, "SELECT status FROM strategies WHERE hash=?", (h,)) or {}).get("status") != "paper":
        fail("Enforcement is for strategies running on paper.", 409)
    db.kv_set(c, f"overlays.enforce.{h}", body.enforce)
    audit.log(c, "portfolio.overlays_enforce", u["username"], {"hash": h, "enforce": body.enforce, "reason": body.reason})
    command("reload", {}, u["username"])
    return ok("Overlays enforced on paper: numeric reductions, buy pauses and rebuilds now place orders through the "
              "normal approval mode." if body.enforce else "Overlays back in shadow mode: decisions are logged only.")


@app.post("/api/strategies/{h}/release-cash")
def strategy_release_cash(h: str, u: dict = Depends(owner)):
    """Release the sleeve's locked rotation proceeds so buys may use them again."""
    c = conn()
    sl = sleeves.load_state(c, h)
    if sl is None:
        fail("No sleeve state for this strategy yet.", 404)
    locked = sleeves.locked_cash(c, h)
    if locked - sl.released_usd <= 0.01:
        fail("No locked cash to release.", 409)
    with db.tx(c):
        c.execute("SELECT state FROM portfolio_state WHERE strategy_hash=? FOR UPDATE", (h,))
        sl = sleeves.load_state(c, h)
        released, sl.released_usd = locked - sl.released_usd, locked
        sleeves.save_state(c, h, (db.row(c, "SELECT sid FROM strategies WHERE hash=?", (h,)) or {}).get("sid", ""), sl)
    audit.log(c, "portfolio.cash_released", u["username"], {"hash": h, "usd": released})
    return ok(f"Released ${released:,.0f} of locked rotation proceeds.")


class ModeIn(BaseModel):
    mode: str
    confirm: str = ""


@app.post("/api/strategies/{h}/mode")
def strategy_mode(h: str, body: ModeIn, u: dict = Depends(owner)):
    c = conn()
    if body.mode not in registry.MODES:
        fail(f"Mode must be one of {', '.join(registry.MODES)}.")
    row = db.row(c, "SELECT sid, status, mode FROM strategies WHERE hash=?", (h,))
    if not row:
        fail("no such strategy", 404)
    if body.mode.startswith("live_") and row["status"] != "live":
        fail("Live modes need this exact file approved for live first (status live).")
    if body.mode == "live_auto" and body.confirm.strip() != row["sid"]:
        fail(f"Live auto submits every trade without your approval. Type the strategy id ({row['sid']}) to confirm.")
    c.execute("UPDATE strategies SET mode=?, updated_at=? WHERE hash=?", (body.mode, time.time(), h))
    audit.log(c, "strategy.mode", u["username"], {"hash": h, "mode": body.mode, "from": row["mode"]})
    command("mode", {"hash": h, "mode": body.mode}, u["username"])
    return ok(f"Execution mode set to {body.mode.replace('_', ' ')}.")


class StrategyMetaIn(BaseModel):
    hashes: list[str] = Field(min_length=1, max_length=500)
    category: str | None = Field(default=None, max_length=60)  # None leaves it unchanged; "" clears it
    archived: bool | None = None


@app.post("/api/strategies/meta")
def strategy_meta(body: StrategyMetaIn, u: dict = Depends(owner)):
    """Categorize and archive/unarchive one or more versions. Category applies to each version's whole line (sid)."""
    c = conn()
    found = db.rows(c, "SELECT hash, sid, status FROM strategies WHERE hash = ANY(?)", (body.hashes,))
    if not found:
        fail("no such strategy", 404)
    if body.category is not None:
        for sid in sorted({r["sid"] for r in found}):
            registry.set_category(c, sid, body.category, u["username"])
    skipped = []
    if body.archived is not None:
        for r in found:
            try:
                registry.set_archived(c, r["hash"], body.archived, u["username"])
            except ValueError:
                skipped.append(r["sid"])
    parts = []
    if body.category is not None:
        parts.append(f"Category {'set to ' + body.category.strip() if body.category.strip() else 'cleared'}")
    if body.archived is not None:
        n = len(found) - len(skipped)
        parts.append(f"{'Archived' if body.archived else 'Unarchived'} {n} version{'s' * (n != 1)}")
    msg = "; ".join(parts) + "."
    if skipped:
        msg += f" Skipped {len(skipped)} running on paper/live (retire first): {', '.join(sorted(set(skipped)))}."
    return ok(msg)


class StrategyDeleteIn(BaseModel):
    confirm: str
    delete_runs: bool = False


@app.post("/api/strategies/{h}/delete")
def strategy_delete(h: str, body: StrategyDeleteIn, u: dict = Depends(owner)):
    c = conn()
    row = db.row(c, "SELECT sid FROM strategies WHERE hash=?", (h,))
    if not row:
        fail("no such strategy", 404)
    if body.confirm != row["sid"]:
        fail(f"Type the strategy id ({row['sid']}) to confirm permanent deletion.")
    try:
        res = registry.delete(c, h, u["username"], delete_runs=body.delete_runs)
    except ValueError as e:
        fail(str(e), 409)
    runs = f" and {res['runs']} saved backtest run{'s' * (res['runs'] != 1)}" if res["runs"] else ""
    return ok(f"Deleted {res['sid']} v{res['version']}{runs} permanently. Trade history and the audit log are kept.",
              last_version=res["last_version"])


class TextIn(BaseModel):
    text: str
    name: str = Field(default="", max_length=80)


@app.post("/api/strategies/compile")
def strategy_compile(body: TextIn, u: dict = Depends(owner)):
    # Up to two Opus calls (draft + one repair round) at 240s each.
    return models_api("/compile", {"text": body.text, "name": body.name.strip(), "actor": u["username"]}, timeout=600)


class DuplicateIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)


@app.post("/api/strategies/{h}/duplicate")
def strategy_duplicate(h: str, body: DuplicateIn, u: dict = Depends(owner)):
    """Copy a strategy file unchanged under a new id: a separate draft strategy (v1) with its own lifecycle."""
    from services.models.authoring import named_draft
    c = conn()
    row = db.row(c, "SELECT sid, version FROM strategies WHERE hash=?", (h,))
    text = registry.text_of(c, h) if row else None
    if text is None:
        fail("no such strategy", 404)
    risk = load_risk()
    copy = named_draft(text, body.name)
    v = validate(copy, risk)
    if not v.ok:
        fail("The copy doesn't validate: " + "; ".join(v.errors))
    if registry.next_version(v.strategy.id) > 1:
        fail(f"A strategy with id {v.strategy.id} already exists. Choose another name.", 409)
    path = registry.write_new_version(v.strategy.id, copy)
    registry.sync(c, risk)
    nh = validate(path.read_text(), risk).hash
    c.execute("UPDATE strategies SET display_name=? WHERE hash=?", (body.name.strip(), nh))
    cat = db.row(c, "SELECT category FROM strategy_categories WHERE sid=?", (row["sid"],))
    if cat:
        registry.set_category(c, v.strategy.id, cat["category"], u["username"])
    audit.log(c, "strategy.duplicated", u["username"], {"from": h, "sid": row["sid"], "version": row["version"], "to": path.name, "hash": nh})
    return ok(f"Duplicated {row['sid']} v{row['version']} as {path.name} (draft).", hash=nh, path=path.name)


class DiffIn(BaseModel):
    diff: str


class EditIn(BaseModel):
    text: str = Field(min_length=1, max_length=200_000)


@app.post("/api/strategies/{h}/edit")
def strategy_edit(h: str, body: EditIn, u: dict = Depends(owner)):
    """Save an edit. The file is written as the next version (versions are never changed in place, so backtests,
    approvals and trades stay pinned to their hash); a running (paper) strategy switches to the edited version and
    keeps its status, mode, allocation and monitoring, so the edit takes effect without promoting it again."""
    c = conn()
    row = db.row(c, "SELECT * FROM strategies WHERE hash=?", (h,))
    if not row:
        fail("no such strategy", 404)
    old = registry.text_of(c, h) or ""
    text = body.text.replace("\r\n", "\n")
    if not text.endswith("\n"):
        text += "\n"
    if text == old:
        fail("No changes to save.")
    risk = load_risk()
    v = validate(text, risk)
    if not v.ok:
        fail("The edited file doesn't validate: " + "; ".join(v.errors))
    if v.strategy.id != row["sid"]:
        fail(f"Keep id: {row['sid']}. To make a separate strategy, use Duplicate instead.")
    path = registry.write_new_version(row["sid"], text)
    registry.sync(c, risk)
    nh = validate(path.read_text(), risk).hash
    if row["display_name"]:
        c.execute("UPDATE strategies SET display_name=? WHERE hash=?", (row["display_name"], nh))
    audit.log(c, "strategy.edited", u["username"], {"from": h, "sid": row["sid"], "version": row["version"], "to": path.name, "hash": nh})
    if row["status"] != "paper":
        return ok(f"Saved your edit as {path.name} (new draft version).", hash=nh, path=path.name)
    why = _carry_blocker(c, h, v.strategy)
    if why:
        return ok(f"Saved your edit as {path.name} as a draft; {row['sid']} v{row['version']} keeps running because {why}. "
                  "Promote the new version when ready.", hash=nh, path=path.name)
    with db.tx(c):
        monitoring.activate(c, v.strategy, u["username"])
        db.kv_set(c, "monitoring.strategy." + nh, True)
        enforce = db.kv_get(c, f"overlays.enforce.{h}")
        if enforce is not None:
            db.kv_set(c, f"overlays.enforce.{nh}", enforce)
        v2 = getattr(v.strategy, "v2", None)
        if v2 is not None and v2.overlays:
            db.kv_set(c, f"scenario_catalog.pin.{nh}", v2.catalog_hash)
        registry.set_status(c, nh, "paper", u["username"], paper_started_at=row["paper_started_at"] or time.time())
        c.execute("UPDATE strategies SET mode=? WHERE hash=?", (row["mode"], nh))
        registry.set_status(c, h, "retired", u["username"])
        audit.log(c, "strategy.edited_in_place", u["username"], {"from": h, "to": nh, "sid": row["sid"], "mode": row["mode"]})
    command("reload", {}, u["username"])
    return ok(f"Saved as {path.name} and switched {row['sid']} to it: still in paper ({row['mode'].replace('_', ' ')}), "
              "same allocation. Open positions from the previous version keep their exits.", hash=nh, path=path.name)


def _carry_blocker(c, h: str, s) -> str | None:
    """Why a running strategy can't switch to its edited version in place (None = it can)."""
    v2 = getattr(s, "v2", None)
    if v2 is not None:
        if market_inputs.missing_feeds(s):
            return "the edit needs an index feed that isn't configured"
        if v2.review_required:
            return "the edit's authoring.status needs your review before it can run"
        if v2.overlays and not v2.catalog_hash:
            return "config/scenario-catalog.yaml is missing"
    if s.kind == "portfolio" and (sleeves.holdings(c, h)[0] or db.row(
            c, f"SELECT id FROM portfolio_orders WHERE strategy_hash=? AND status IN ({','.join('?' * len(sleeves.OPEN))})", (h, *sleeves.OPEN))):
        return "its portfolio sleeve holds positions or working orders, which stay with the version that placed them"
    return None


@app.post("/api/strategies/{h}/apply-diff")
def strategy_apply_diff(h: str, body: DiffIn, u: dict = Depends(owner)):
    res = models_api("/diff", {"hash": h, "diff": body.diff, "actor": u["username"]})
    if res.get("ok"):
        return ok(f"Created new draft {res['path']} from the proposed diff.")
    fail(f"Diff not applied: {res.get('error') or '; '.join(res.get('errors', []))}")


# ---------------------------------------------------------------- chat
THREAD = re.compile(r"^(strategy:[0-9a-f]{64}|trade:\d+):[A-Za-z0-9]{6,32}$")


@app.get("/api/chats/{thread}")
def chat_get(thread: str, u: dict = Depends(user)):
    if not THREAD.match(thread):
        fail("bad thread id")
    rows = db.rows(conn(), "SELECT id, role, content, model FROM chats WHERE thread=? ORDER BY id", (thread,))
    if rows and rows[0]["role"] == "user":
        rows[0]["content"] = rows[0]["content"].split("My question: ", 1)[-1]
    return {"messages": rows}


class ChatIn(BaseModel):
    thread: str
    message: str


@app.post("/api/chat")
def chat_post(body: ChatIn, u: dict = Depends(owner)):
    if not THREAD.match(body.thread):
        fail("bad thread id")
    res = models_api("/chat", {"thread": body.thread, "message": body.message, "actor": u["username"]})
    if res.get("error"):
        fail(res["error"], 503)
    return chat_get(body.thread, u)


# ---------------------------------------------------------------- strategy design chat (architect)
DESIGN = re.compile(r"^design:[A-Za-z0-9]{6,32}$")


def _design_thread(thread: str) -> str:
    if not DESIGN.match(thread):
        fail("bad design thread id")
    return thread


def _improving(content: str) -> dict | None:
    """The strategy a design thread was started from ("duplicate and improve"), read from its first message."""
    from services.models.authoring import CONTEXT_MARK, IMPROVE_TAG
    m = IMPROVE_TAG.search(content.split(CONTEXT_MARK, 1)[0])
    return {"sid": m.group(1), "version": int(m.group(2)), "hash": m.group(3)} if m else None


def _design_view(c, thread: str) -> dict:
    from services.models.authoring import CONTEXT_MARK, check_draft
    risk = load_risk()
    rows = db.rows(c, "SELECT id, ts, role, content, model FROM chats WHERE thread=? ORDER BY id", (thread,))
    draft, improving = None, None
    for r in rows:
        if r["role"] == "user" and CONTEXT_MARK in r["content"]:
            improving = _improving(r["content"])
            r["content"] = r["content"].split(CONTEXT_MARK, 1)[1].removesuffix("\n</my_idea>")
            r["has_context"] = True
        if r["role"] == "assistant":
            d = check_draft(r["content"], risk)
            if d:
                draft = d | {"message_id": r["id"]}
    saved = [r for r in rows if r["role"] == "user" and r["content"].startswith("[saved]")]
    reviews = {}
    if draft:
        for r in db.rows(c, "SELECT role, ts, result FROM design_reviews WHERE thread=? AND draft_hash=? ORDER BY id", (thread, draft["hash"])):
            reviews[r["role"]] = {**json.loads(r["result"]), "ts": r["ts"]}  # latest run per role wins
    return {"thread": thread, "messages": rows, "draft": draft, "saved": len(saved), "reviews": reviews, "improving": improving}


@app.get("/api/design")
def design_list(u: dict = Depends(user)):
    from services.models.authoring import CONTEXT_MARK
    rows = db.rows(conn(), "SELECT thread, MIN(ts) started, MAX(ts) updated, COUNT(*) n FROM chats WHERE thread LIKE 'design:%'"
                           " GROUP BY thread ORDER BY MAX(ts) DESC LIMIT 30")
    for r in rows:
        first = db.row(conn(), "SELECT content FROM chats WHERE thread=? ORDER BY id LIMIT 1", (r["thread"],))
        r["title"] = (first["content"].split(CONTEXT_MARK, 1)[-1].removesuffix("\n</my_idea>") if first else "")[:140]
        if first and (imp := _improving(first["content"])):
            r["title"] = f"Improve {imp['sid']} v{imp['version']}: {r['title']}"
    return {"threads": rows}


@app.get("/api/design/{thread}")
def design_get(thread: str, u: dict = Depends(user)):
    return _design_view(conn(), _design_thread(thread))


class DesignMsgIn(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    source: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")  # duplicate-and-improve: the strategy to start from


@app.post("/api/design/{thread}")
def design_post(thread: str, body: DesignMsgIn, u: dict = Depends(owner)):
    _design_thread(thread)
    if body.source and not db.row(conn(), "SELECT 1 FROM strategies WHERE hash=?", (body.source,)):
        fail("no such strategy", 404)
    # Opus at high effort, plus one automatic repair round when the draft doesn't validate.
    res = models_api("/design", {"thread": thread, "message": body.message, "actor": u["username"], "source": body.source}, timeout=900)
    if res.get("error"):
        fail(res["error"], 503)
    return _design_view(conn(), thread)


class DraftIn(BaseModel):
    yaml: str = Field(min_length=10, max_length=40_000)
    name: str = Field(default="", max_length=80)


def _summarize_backtest(rep: dict) -> dict:
    m = rep["metrics"]
    out = {"sessions": len(rep["days"]), "from": rep["days"][0] if rep["days"] else None, "to": rep["days"][-1] if rep["days"] else None,
           "note": rep.get("signal_note"), "rejected_by_limits": rep.get("rejected_by_guard"),
           "metrics": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in m.items()}}
    if rep.get("kind") == "portfolio":
        out["orders_by_reason"] = {}
        for o in rep["orders"]:
            out["orders_by_reason"][o["reason"]] = out["orders_by_reason"].get(o["reason"], 0) + 1
    out["sample_trades"] = [{k: t.get(k) for k in ("day", "symbol", "qty", "entry_price", "exit_price", "exit_kind", "pnl", "r_multiple")}
                            for t in rep["trades"][:12]]
    return out


@app.post("/api/design/{thread}/backtest")
def design_backtest(thread: str, body: DraftIn, u: dict = Depends(owner)):
    """Backtest an unsaved draft and add the result to the conversation, so Kimi can iterate on it."""
    c = conn()
    _design_thread(thread)
    risk = load_risk()
    v = validate(body.yaml, risk)
    if not v.ok:
        fail("The draft doesn't validate: " + "; ".join(v.errors))
    t0 = time.time()
    try:
        rep = bt.run(c, v.strategy, risk, starting_equity=float(load_yaml("sim.yaml").get("starting_equity", 100_000)))
    except ValueError as e:
        if "No daily bars" in str(e):
            fail(f"Can't backtest yet: {e} Unsaved drafts don't trigger downloads; save the draft or use symbols that have stored bars.")
        fail(f"Can't backtest: {e}")
    summary = _summarize_backtest(rep)
    summary["seconds"] = round(time.time() - t0, 2)
    c.execute("INSERT INTO chats(ts, thread, role, content, model) VALUES (?,?,?,?,?)",
              (time.time(), thread, "user", f"[backtest] Draft {v.strategy.id} (sha256 {v.hash[:12]}):\n{json.dumps(summary, default=str)}", u["username"]))
    audit.log(c, "strategy.design_backtest", u["username"], {"thread": thread, "hash": v.hash, "metrics": summary["metrics"]})
    return {"backtest": summary, **_design_view(c, thread)}


@app.post("/api/design/{thread}/review")
def design_review(thread: str, body: DraftIn, u: dict = Depends(owner)):
    """Four Opus agents (Analyst, Researcher, Trader, Risk Team) review the draft in parallel."""
    c = conn()
    _design_thread(thread)
    v = validate(body.yaml, load_risk())
    if not v.ok:
        fail("The draft doesn't validate: " + "; ".join(v.errors))
    backtest = None  # the latest backtest of this exact draft, if the trader ran one
    for r in db.rows(c, "SELECT content FROM chats WHERE thread=? AND content LIKE '[backtest]%' ORDER BY id DESC", (thread,)):
        if f"sha256 {v.hash[:12]}" in r["content"].split("\n", 1)[0]:
            backtest = json.loads(r["content"].split("\n", 1)[1])
            break
    res = models_api("/review", {"thread": thread, "yaml": body.yaml, "actor": u["username"], "backtest": backtest}, timeout=900)
    if res.get("error"):
        fail(res["error"], 503)
    return _design_view(c, thread)


@app.post("/api/design/{thread}/save")
def design_save(thread: str, body: DraftIn, u: dict = Depends(owner)):
    from services.models.authoring import named_draft
    c = conn()
    _design_thread(thread)
    risk = load_risk()
    text = named_draft(body.yaml.strip() + "\n", body.name)
    v = validate(text, risk)
    if not v.ok:
        fail("The draft doesn't validate: " + "; ".join(v.errors))
    path = registry.write_new_version(v.strategy.id, text)
    registry.sync(c, risk)
    h = validate(path.read_text(), risk).hash
    if body.name.strip():
        c.execute("UPDATE strategies SET display_name=? WHERE hash=?", (body.name.strip(), h))
    c.execute("INSERT INTO chats(ts, thread, role, content, model) VALUES (?,?,?,?,?)",
              (time.time(), thread, "user", f"[saved] The trader saved this draft as {path.name} (a new draft strategy).", u["username"]))
    audit.log(c, "strategy.designed", u["username"], {"thread": thread, "file": path.name, "hash": h})
    return ok(f"Saved as {path.name} (draft). Backtest it on its page, then promote to paper.", hash=h, path=path.name)


# ---------------------------------------------------------------- idea lab / theses
@app.get("/api/ideas")
def ideas(u: dict = Depends(user)):
    from services.models import idealab  # deterministic event studies; no keys involved
    c = conn()
    study = idealab.event_study(c)
    study.pop("_raw", None)
    rows = db.rows(c, "SELECT * FROM ideas ORDER BY id DESC")
    for r in rows:
        r["passed"] = idealab.holdout_passed(r)
        for k in ("holdout", "event_ids", "thesis_ids", "evidence", "spec"):
            r[k] = j(r[k])
    return {"study": study, "ideas": rows, "counter": idealab.counter(c),
            "theses": db.rows(c, "SELECT id, text FROM theses WHERE active=1"),
            "min_events": int(load_yaml("idealab.yaml").get("min_events", 8))}


class FocusIn(BaseModel):
    focus: str = ""


@app.post("/api/ideas/run")
def ideas_run(body: FocusIn, u: dict = Depends(owner)):
    res = models_api("/ideas/run", {"focus": body.focus, "actor": u["username"]})
    if res.get("error"):
        fail(f"Idea Lab run failed: {res['error']}", 503)
    if not isinstance(res.get("run_id"), str) or not isinstance(res.get("model"), str) or not isinstance(res.get("ideas"), list):
        fail("Idea Lab returned an incomplete result. Please retry.", 502)
    detail = f" from {res['events']} measured events" if res.get("events") is not None else ""
    if res.get("dropped"):
        detail += f"; {res['dropped']} proposal(s) dropped for citing groups without discovery events"
    if res.get("note"):
        detail += f" ({res['note']})"
    return ok(f"Run {res['run_id']} ({res['model']}): {len(res['ideas'])} new hypotheses{detail}.")


# ---------------------------------------------------------------- strategy discovery (single ticker)
class DiscoveryStartIn(BaseModel):
    ticker: str = Field(min_length=1, max_length=8)
    peers: list[str] = Field(default_factory=lambda: ["SPY", "QQQ"], max_length=4)
    kinds: list[str] = Field(default_factory=lambda: ["intraday", "daily"], min_length=1, max_length=2)
    generations: int = Field(default=4, ge=0, le=8)
    max_trials: int = Field(default=60, ge=5, le=300)
    holdout_pct: float = Field(default=0.25, ge=0.1, le=0.5)
    save_drafts: bool = True
    save_all: bool = False
    offline: bool = False
    seed_trials: list[int] = Field(default_factory=list, max_length=5)
    seed_hash: str | None = None
    templates: bool | None = None  # default: on for new searches, off for follow-ups


@app.get("/api/discovery/preflight")
def discovery_preflight(ticker: str, peers: str = "SPY,QQQ", u: dict = Depends(user)):
    """What a run for this ticker can use: stored sessions per kind, peers with data, history download, model generator."""
    from common import history
    from discovery.run import Config, sessions
    try:
        cfg = Config(ticker=ticker, peers=[p for p in peers.split(",") if p.strip()])
    except ValueError as e:
        fail(str(e).splitlines()[-1] if str(e) else "invalid ticker")
    c = conn()
    wins = sessions(c, cfg)
    kinds = {k: ({"ok": False, "why": w["error"]} if "error" in w else
                 {"ok": True, "discovery": len(w["discovery"]), "holdout": len(w["holdout"]),
                  "from": str(w["discovery"][0]), "to": str(w["holdout"][-1])}) for k, w in wins.items()}
    peer_rows = [{"symbol": p, "daily": db.row(c, "SELECT COUNT(*) n FROM daily WHERE symbol=?", (p,))["n"],
                  "intraday": db.row(c, "SELECT COUNT(DISTINCT ts/86400) n FROM bars WHERE symbol=?", (p,))["n"]} for p in cfg.peers]
    try:
        with httpx.Client(trust_env=False, timeout=3) as client:
            llm_on = bool(client.get(f"{MODELS_URL}/health").json().get("llm"))
    except (httpx.HTTPError, ValueError):
        llm_on = False
    if load_risk().portfolio is None:
        kinds["daily"] = {"ok": False, "why": "portfolio strategies are disabled on the Risk page"}
    running = db.row(c, "SELECT id FROM discovery_runs WHERE status='running' ORDER BY id DESC LIMIT 1")
    return {"ticker": cfg.ticker, "kinds": kinds, "peers": peer_rows, "history_download": history.available(c),
            "llm": llm_on, "running": running["id"] if running else None}


@app.post("/api/discovery")
def discovery_start(body: DiscoveryStartIn, u: dict = Depends(owner)):
    cfg = body.model_dump(exclude={"offline", "templates"})
    cfg["templates"] = body.templates if body.templates is not None else not (body.seed_trials or body.seed_hash)
    res = models_api("/discovery/run", {"config": cfg, "actor": u["username"], "offline": body.offline}, timeout=30)
    if res.get("error"):
        fail(f"Discovery didn't start: {res['error']}", 409 if "still running" in str(res["error"]) else 503)
    audit.log(conn(), "discovery.started", u["username"], {"run_id": res.get("run_id"), "config": cfg})
    return ok(f"Discovery run #{res['run_id']} started for {body.ticker.upper()} ({res.get('model')}).", run_id=res.get("run_id"))


@app.get("/api/discovery")
def discovery_runs(u: dict = Depends(user)):
    rows = db.rows(conn(), "SELECT id, created_at, created_by, ticker, status, stage, progress, error, finished_at, config, report"
                           " FROM discovery_runs ORDER BY id DESC LIMIT 50")
    for r in rows:  # list view: counts only
        cfg, rep = j(r.pop("config")) or {}, j(r.pop("report")) or {}
        kinds = rep.get("kinds") or {}
        r.update(kinds=cfg.get("kinds"), parent_run_id=cfg.get("parent_run_id"), follow_up=bool(cfg.get("seed_trials") or cfg.get("seed_hash")),
                 generator=rep.get("generator"), trials=sum(k["trials"] for k in kinds.values()),
                 passed_gates=sum(k["passed_gates"] for k in kinds.values()),
                 holdout_pass=sum(f["passed"] for k in kinds.values() for f in k["finalists"]), drafts=len(rep.get("drafts") or []))
    return {"runs": rows}


@app.get("/api/discovery/{rid}")
def discovery_run(rid: int, u: dict = Depends(user)):
    c = conn()
    r = db.row(c, "SELECT * FROM discovery_runs WHERE id=?", (rid,))
    if not r:
        fail("No such discovery run.", 404)
    for k in ("config", "profile", "report"):
        r[k] = j(r[k])
    trials = db.rows(c, "SELECT id, kind, generation, parent_id, origin, family, title, change, status, score, hash, strategy_hash,"
                        " evaluation, holdout FROM discovery_trials WHERE run_id=? ORDER BY id", (rid,))
    for t in trials:
        ev = j(t.pop("evaluation")) or {}
        t["holdout"] = j(t["holdout"])
        gates = ev.get("gates") or {}
        failed = [k for k, v in gates.items() if k not in ("passed", "valid") and v is False]
        t["summary"] = {"sharpe": (ev.get("base") or {}).get("sharpe"), "trades": (ev.get("base") or {}).get("trades"),
                        "stress_sharpe": (ev.get("stress") or {}).get("sharpe"), "consistency": (ev.get("base") or {}).get("consistency"),
                        "gates": gates, "failed_gates": failed, "weaknesses": [w["kind"] for w in ev.get("weaknesses") or []],
                        "errors": ev.get("errors")}
        # worth iterating: passed every gate on discovery data, or missed by one gate with a positive score
        t["promising"] = bool(ev.get("ok") and (gates.get("passed") or (len(failed) == 1 and (t["score"] or 0) > 0)))
    children = [x for x in db.rows(c, "SELECT id, status, created_at, config FROM discovery_runs WHERE id > ? ORDER BY id", (rid,))
                if (j(x["config"]) or {}).get("parent_run_id") == rid]
    return {"run": r, "trials": trials, "follow_ups": [{k: x[k] for k in ("id", "status", "created_at")} for x in children]}


@app.get("/api/discovery/trials/{tid}")
def discovery_trial(tid: int, u: dict = Depends(user)):
    t = db.row(conn(), "SELECT * FROM discovery_trials WHERE id=?", (tid,))
    if not t:
        fail("No such trial.", 404)
    for k in ("evaluation", "holdout", "hypothesis"):
        t[k] = j(t[k])
    v = validate(t["yaml"], load_risk())
    t["back_translation"] = back_translate(v.strategy) if v.ok else None
    return t


@app.post("/api/discovery/trials/{tid}/save")
def discovery_trial_save(tid: int, u: dict = Depends(owner)):
    from discovery.run import save_trial
    c = conn()
    try:
        r = save_trial(c, tid, load_risk(), u["username"])
    except (ValueError, FileExistsError) as e:
        fail(str(e))
    msg = f"Trial #{tid} is already saved as {r['path']}." if r["existing"] else f"Saved trial #{tid} as draft {r['path']}."
    return ok(msg, hash=r["hash"], path=r["path"])


class IdeaIn(BaseModel):
    note: str = ""
    thesis_id: int | None = None


@app.post("/api/ideas/{iid}/{action}")
def idea_action(iid: int, action: str, body: IdeaIn, u: dict = Depends(owner)):
    from services.models import idealab
    c = conn()
    idea = db.row(c, "SELECT * FROM ideas WHERE id=?", (iid,))
    if not idea:
        fail("no such idea", 404)
    if action in ("accept", "reject"):
        c.execute("UPDATE ideas SET status=?, note=? WHERE id=?", ("accepted" if action == "accept" else "rejected", body.note, iid))
        audit.log(c, f"idea.{action}ed", u["username"], {"idea_id": iid, "note": body.note})
        return ok(f"Idea #{iid} {action}ed.")
    if action == "attach" and body.thesis_id:
        ids = j(idea["thesis_ids"], {})
        ids.setdefault("attached", []).append(body.thesis_id)
        c.execute("UPDATE ideas SET thesis_ids=? WHERE id=?", (json.dumps(ids), iid))
        return ok(f"Attached thesis #{body.thesis_id} to idea #{iid}.")
    if action == "compile":
        if idea["anecdotal"]:
            fail("Anecdotal ideas (too few events) can't be promoted.")
        if idea["status"] != "accepted":
            fail("Accept the idea before compiling it.")
        text, why = idealab.template_strategy(idea)
        if not text:
            fail(f"Can't compile: {why}")
        risk = load_risk()
        s = validate(text, risk).strategy
        path = registry.write_new_version(s.id if s else f"idea{iid}", text)
        registry.sync(c, risk)
        h = validate(path.read_text(), risk).hash
        c.execute("UPDATE ideas SET status='compiled', strategy_hash=? WHERE id=?", (h, iid))
        audit.log(c, "idea.compiled", u["username"], {"idea_id": iid, "file": path.name})
        return ok(f"Compiled to {path.name}.", hash=h)
    fail("unknown action")


@app.get("/api/theses")
def theses(u: dict = Depends(user)):
    c = conn()
    rows = db.rows(c, "SELECT * FROM theses ORDER BY active DESC, id DESC")
    ideas_ = db.rows(c, "SELECT id, title, thesis_ids FROM ideas")
    for t in rows:
        t["ideas"] = [{"id": i["id"], "title": i["title"]} for i in ideas_
                      if t["id"] in sum((v for v in j(i["thesis_ids"], {}).values() if isinstance(v, list)), [])]
    return {"rows": rows}


class ThesisIn(BaseModel):
    text: str
    tags: str = ""
    confidence: float = 0.5
    expires: str = ""


@app.post("/api/theses")
def theses_add(body: ThesisIn, u: dict = Depends(owner)):
    c = conn()
    c.execute("INSERT INTO theses(created_at, text, tags, confidence, expires, active, created_by) VALUES (?,?,?,?,?,1,?)",
              (time.time(), body.text, body.tags, max(0.0, min(1.0, body.confidence)), body.expires, u["username"]))
    audit.log(c, "thesis.added", u["username"], {"text": body.text, "tags": body.tags})
    return ok("Thesis added.")


@app.post("/api/theses/{tid}/expire")
def theses_expire(tid: int, u: dict = Depends(owner)):
    conn().execute("UPDATE theses SET active=0 WHERE id=?", (tid,))
    audit.log(conn(), "thesis.expired", u["username"], {"id": tid})
    return ok()


# ---------------------------------------------------------------- news & notes
@app.get("/api/news")
def news(scope: str = "live", u: dict = Depends(user)):
    c = conn()
    sessions = ["live", "alpaca"] if scope == "live" else ["history"]
    items = db.rows(c, "SELECT * FROM news WHERE session = ANY(?) ORDER BY ts DESC LIMIT 150", (sessions,))
    ids = [n["id"] for n in items]
    sigs: dict[int, list] = {}
    if ids:
        for s in db.rows(c, "SELECT news_id, symbol, name, value, model, tier FROM signals WHERE news_id = ANY(?) ORDER BY name", (ids,)):
            sigs.setdefault(s["news_id"], []).append(s)
    for n in items:
        n["symbols"] = j(n["symbols"], [])
        n["tier_detail"] = j(n["tier_detail"])
        n["signals"] = sigs.get(n["id"], [])
    counts = {r["triage"] or "pending": r["n"] for r in c.execute(
        "SELECT triage, COUNT(*) n FROM news WHERE session = ANY(?) GROUP BY triage", (sessions,))}
    return {"items": items, "counts": counts, "notes": db.rows(c, "SELECT * FROM notes WHERE active=1 ORDER BY id DESC"),
            "news_claude": news_claude.status(c)}


class NoteIn(BaseModel):
    symbol: str
    text: str


@app.post("/api/notes")
def notes_add(body: NoteIn, u: dict = Depends(owner)):
    sym = body.symbol.upper().strip()
    if not re.fullmatch(r"[A-Z.]{1,8}", sym):
        fail("bad symbol")
    res = models_api("/notes/parse", {"text": body.text}, timeout=20)
    stance = res.get("stance", "neutral")
    c = conn()
    c.execute("INSERT INTO notes(ts, symbol, text, stance, parsed_by, created_by, active) VALUES (?,?,?,?,?,?,1)",
              (time.time(), sym, body.text, stance, res.get("model", "unparsed"), u["username"]))
    # note.stance is a signal like any other: point-in-time, from notes@v1
    now = db.kv_get(c, "sim.now") or int(time.time())
    c.execute("INSERT INTO signals(ts, symbol, name, value, confidence, source, model, tier, rationale) VALUES (?,?,?,?,?,?,?,?,?)",
              (now, sym, "note.stance", json.dumps(stance), 1.0, "notes@v1", res.get("model"), "note", body.text[:300]))
    audit.log(c, "note.added", u["username"], {"symbol": sym, "stance": stance, "text": body.text})
    return ok(f"Note added for {sym}: stance {stance}.")


@app.post("/api/notes/{nid}/expire")
def notes_expire(nid: int, u: dict = Depends(owner)):
    conn().execute("UPDATE notes SET active=0 WHERE id=?", (nid,))
    audit.log(conn(), "note.expired", u["username"], {"id": nid})
    return ok()


# ---------------------------------------------------------------- system / audit / risk
@app.get("/api/system")
def system(u: dict = Depends(user)):
    c = conn()
    try:
        with httpx.Client(trust_env=False, timeout=3) as client:
            response = client.get(f"{MODELS_URL}/health")
            response.raise_for_status()
            prov = response.json()
    except (httpx.HTTPError, ValueError):
        prov = {"error": "models service unreachable"}
    calls = db.rows(c, "SELECT provider, model, task, COUNT(*) n, AVG(latency_ms) lat, SUM(1-ok) errors,"
                       " COALESCE(SUM(cost_usd),0) cost FROM model_calls GROUP BY provider, model, task ORDER BY n DESC")
    triage = {r["triage"] or "pending": r["n"] for r in c.execute(
        "SELECT triage, COUNT(*) n FROM news WHERE session IN ('live','alpaca') GROUP BY triage")}
    db_size = db.row(c, "SELECT pg_size_pretty(pg_database_size(current_database())) s")["s"]
    return {"health": health(c), "providers": prov, "calls": calls, "triage": triage,
            "risk_sha256": load_risk().sha256, "acct": db.kv_get(c, "account") or {},
            "source": (db.kv_get(c, "sim.state") or {}).get("source", "simulator"),
            "history": db.kv_get(c, "sim.history") or db.kv_get(c,"paper.history_loaded") or {}, "suspend_trade": db.kv_get(c, "broker.suspend_trade"),
            "errors": db.rows(c, "SELECT ts, provider, model, task, error FROM model_calls WHERE ok=0 ORDER BY id DESC LIMIT 10"),
            "news_to_claude": news_claude.settings(c)["on"], "news_claude": news_claude.status(c),
            "database": {"engine": "PostgreSQL", "size": db_size,
                         "version": db.row(c, "SHOW server_version")["server_version"]}}


class NewsClaudeIn(BaseModel):
    on: bool


@app.post("/api/settings/news-to-claude")
def set_news_to_claude(body: NewsClaudeIn, u: dict = Depends(owner)):
    """Owner switch: send news items to Kimi for triage (off = Laya, Gemini or the offline keyword model only)."""
    c = conn()
    db.kv_set(c, news_claude.ON_KEY, body.on)
    audit.log(c, "settings.news_to_claude", u["username"], {"on": body.on})
    return ok("News is now sent to Kimi." if body.on else "News is no longer sent to Kimi.")


class NewsClaudeHoursIn(BaseModel):
    enabled: bool
    start: str = "09:00"
    end: str = "17:00"
    weekdays_only: bool = False


@app.post("/api/settings/news-to-claude/hours")
def set_news_to_claude_hours(body: NewsClaudeHoursIn, u: dict = Depends(owner)):
    """Owner schedule: only send news to Kimi between start and end (US/Eastern). Disabled = any time."""
    if not (news_claude.valid_hhmm(body.start) and news_claude.valid_hhmm(body.end)):
        fail("times must be HH:MM (24-hour)")
    c = conn()
    hours = body.model_dump()
    db.kv_set(c, news_claude.HOURS_KEY, hours)
    audit.log(c, "settings.news_to_claude_hours", u["username"], hours)
    if not body.enabled:
        return ok("Active hours off: news goes to Kimi whenever the switch is on.")
    days = "weekdays" if body.weekdays_only else "every day"
    return ok(f"News goes to Kimi only {body.start}–{body.end} ET, {days}.")


# ---------------------------------------------------------------- Alpaca paper accounts
@app.get("/api/broker-accounts")
def broker_accounts(u: dict = Depends(user)):
    c = conn()
    st = db.kv_get(c, "sim.state") or {}
    return {"accounts": BA.listing(c), "vault_ready": bool(db.kv_get(c, "broker.vault_pub")),
            "paper": st.get("source") == "alpaca_paper" or os.environ.get("BROKER_BACKEND") == "alpaca",
            "open_positions": db.row(c, "SELECT COUNT(*) n FROM positions")["n"],
            "error": (db.kv_get(c, "paper.error") or {}).get("type")}


class BrokerAccountIn(BaseModel):
    label: str = Field(min_length=1, max_length=60)
    key_id: str = Field(min_length=10, max_length=64, pattern=r"^[A-Z0-9]+$")
    secret: str = Field(min_length=20, max_length=128)
    code: str = Field(min_length=6, max_length=8)
    activate: bool = False


@app.post("/api/broker-accounts")
def broker_account_add(body: BrokerAccountIn, u: dict = Depends(owner)):
    auth.require_code(u, body.code)
    c = conn()
    try:
        acct = BA.check(body.key_id.strip(), body.secret.strip())
        aid = BA.add(c, body.label.strip(), body.key_id.strip(), body.secret.strip(), u["username"], acct)
        if body.activate:
            BA.activate(c, aid, u["username"])
    except ValueError as e:
        fail(str(e))
    msg = f"Saved paper account {acct.get('account_number', '')} (equity ${float(acct.get('equity') or 0):,.2f})."
    return ok(msg + (" The broker is restarting on it." if body.activate else ""))


class BrokerSwitchIn(BaseModel):
    code: str = Field(min_length=6, max_length=8)
    confirm_open: bool = False


@app.post("/api/broker-accounts/{aid}/activate")
def broker_account_activate(aid: int, body: BrokerSwitchIn, u: dict = Depends(owner)):
    auth.require_code(u, body.code)
    c = conn()
    n = db.row(c, "SELECT COUNT(*) n FROM positions")["n"]
    if n and not body.confirm_open:
        fail(f"The current account holds {n} position(s); they stay open there, unmanaged. Confirm to switch anyway.")
    try:
        BA.activate(c, aid, u["username"])
    except ValueError as e:
        fail(str(e))
    return ok("Switched. Pending and approved intents were expired; the broker restarts on the new account in a few seconds.")


@app.post("/api/broker-accounts/{aid}/delete")
def broker_account_delete(aid: int, u: dict = Depends(owner)):
    try:
        BA.remove(conn(), aid, u["username"])
    except ValueError as e:
        fail(str(e))
    return ok("Account removed.")


@app.get("/api/audit")
def audit_page(q: str = "", kind: str = "", verify: int = 0, u: dict = Depends(user)):
    c = conn()
    sql, args = "SELECT * FROM audit WHERE 1=1", []
    if kind:
        sql += " AND kind LIKE ?"
        args.append(f"{kind}%")
    if q:
        sql += " AND (payload ILIKE ? OR actor ILIKE ?)"
        args += [f"%{q}%", f"%{q}%"]
    return {"rows": db.rows(c, sql + " ORDER BY id DESC LIMIT 300", args),
            "kinds": [r["kind"] for r in c.execute("SELECT DISTINCT kind FROM audit ORDER BY kind")],
            "verified": audit.verify(c) if verify else None}


@app.get("/api/audit/export.jsonl")
def audit_export(u: dict = Depends(user)):
    c = db.connect()

    def gen():
        try:
            for r in c.execute("SELECT * FROM audit ORDER BY id"):
                yield json.dumps(dict(r)) + "\n"
        finally:
            c.close()

    return StreamingResponse(gen(), media_type="application/x-ndjson",
                             headers={"content-disposition": "attachment; filename=audit.jsonl"})


@app.get("/api/risk")
def risk_page(u: dict = Depends(user)):
    from common.risk_help import DETAILS
    c = conn()
    r = load_risk()
    base, _ = risk_defaults()
    over = RS.overrides(c)
    defaults = RS.merged(base, {})
    controls = []
    for ctl in RS.CATALOG:
        o = over.get(ctl.key)
        controls.append({"key": ctl.key, "section": ctl.section, "label": ctl.label, "help": ctl.help, "type": ctl.type,
                         "details": dict(zip(("description", "example", "interaction"), DETAILS[ctl.key])),
                         "choices": list(ctl.choices), "default": defaults[ctl.section].get(ctl.name),
                         "value": o["value"] if o else defaults[ctl.section].get(ctl.name), "overridden": bool(o),
                         "updated_by": o["updated_by"] if o else None, "updated_at": o["updated_at"] if o else None})
    return {"risk": risk_dict(r), "controls": controls, "sections": list(RS.SECTIONS),
            "history": db.rows(c, "SELECT * FROM risk_settings_history ORDER BY id DESC LIMIT 50"),
            "logged_sha256": db.kv_get(c, "risk.sha256"), "rev": db.kv_get(c, "risk.rev") or 0, "path": str(risk_path())}


class RiskIn(BaseModel):
    changes: dict
    reason: str = Field(min_length=3, max_length=500)


@app.post("/api/risk")
def risk_save(body: RiskIn, u: dict = Depends(owner)):
    c = conn()
    base, _ = risk_defaults()
    res = RS.save(c, body.changes, u["username"], body.reason, base)
    if not res["ok"]:
        fail("; ".join(res["errors"]))
    # Strategies the new limits invalidate are skipped by the broker; say which.
    risk = load_risk()
    broken = []
    for row in db.rows(c, "SELECT hash, sid, version FROM strategies WHERE status IN ('paper','live')"):
        s, why = registry.load(c, row["hash"], risk)
        if s is None:
            broken.append(f"{row['sid']} v{row['version']} ({why})")
    msg = f"Saved {len(body.changes)} risk control(s); the broker applies them within a few seconds."
    if broken:
        msg += " These running strategies no longer pass validation and will be skipped: " + "; ".join(broken)
    return ok(msg)


# ---------------------------------------------------------------- funds
def fund_suggest(c, risk: RiskConfig, s) -> dict:
    """Defaults for the promote form: what the rules can use, what's free, and any existing allocation of the line."""
    eq, _ = funds.equity_now(c)
    room = max(0.0, funds.unallocated(c, risk, exclude=s.id))
    want = funds.default_allocation(s, risk)
    cur = db.row(c, "SELECT allocation, alloc_usd, reserved FROM strategy_funds WHERE sid=?", (s.id,))
    # the rules' full ceiling, with as much reserved as is free (the rest is drawn from the shared pool when it trades)
    ceiling = min(want, risk.funds.max_strategy_allocation)
    return {"equity": eq, "wants": want, "room": room, "suggested": ceiling, "kind": s.kind,
            "suggested_reserved": min(1.0, room / ceiling) if ceiling > 0 else 1.0,
            "current": {"mode": "usd" if cur["alloc_usd"] is not None else "pct", "reserved": cur["reserved"],
                        "allocation": funds.share(cur, eq), "usd": funds.base_usd(cur, eq)} if cur else None}


def fund_view(c, risk: RiskConfig) -> dict:
    run = funds.running(c, risk)
    acct = funds.account(c, risk, run)
    cfg = risk.funds
    eq = acct.equity
    rows = []
    for sid, f in sorted(acct.strategies.items()):
        s = run[sid]
        want = funds.default_allocation(s, risk) if cfg else None
        hashes = db.rows(c, "SELECT hash, version, status FROM strategies WHERE sid=? AND status IN ('paper','live') ORDER BY version", (sid,))
        burst = min(F.room_usd(sid, f.kind, acct, cfg) or 0.0, max(0.0, f.cap_usd(eq) - f.deployed)) if cfg else None
        rows.append({
            "sid": sid, "kind": f.kind, "versions": hashes, "allocation": f.allocation, "allocation_usd": f.cap_usd(eq),
            "reserved": f.reserved, "reserved_usd": f.reserved_usd(eq), "committed_usd": f.committed(eq), "can_buy_now": burst,
            "activity": funds.activity(c, sid),
            "alloc_mode": "usd" if f.fixed_usd is not None else "pct", "pnl_pct": f.pnl / f.base_usd if f.base_usd else None,
            "source": f.source, "deployed": f.deployed, "used": f.deployed / f.cap_usd(eq) if f.cap_usd(eq) else None,
            "pnl": f.pnl, "pnl_today": f.pnl_today, "peak_pnl": f.peak_pnl, "drawdown": f.drawdown, "max_drawdown": f.max_drawdown,
            "daily_loss": f.daily_loss, "status": f.status, "reason": f.reason, "base_usd": f.base_usd, "base_ts": f.base_ts,
            "wants": want, "warning": (f"its rules can use up to {want:.1%} of equity; orders above {f.allocation:.1%} are refused"
                                       if want and want > f.allocation + 1e-9 and f.kind == "portfolio" else None),
        })
    allocated = sum(f.reserved_usd(eq) for f in acct.strategies.values()) / eq if eq > 0 else 0.0  # reserved share of equity
    ceilings = sum(f.allocation for f in acct.strategies.values())
    committed = acct.committed()
    return {
        "enabled": cfg is not None, "limits": {k: getattr(cfg, k) for k in cfg.__dataclass_fields__} if cfg else None,
        "account": {"equity": eq, "cash": acct.cash, "deployed": acct.deployed, "working_buys": acct.working_buys,
                    "intraday_deployed": acct.intraday_deployed, "sleeve_deployed": acct.deployed - acct.intraday_deployed,
                    "reserve_usd": cfg.cash_reserve * eq if cfg else None,
                    "available": max(0.0, min(acct.cash - acct.working_buys - cfg.cash_reserve * eq, cfg.max_deployed * eq - acct.deployed)) if cfg else None,
                    "max_deployed_usd": cfg.max_deployed * eq if cfg else None, "peak_equity": acct.peak_equity, "drawdown": acct.drawdown,
                    "allocated": allocated, "unallocated": (cfg.max_deployed - allocated) if cfg else None, "ceilings": ceilings,
                    "committed": committed, "pool_free_usd": max(0.0, cfg.max_deployed * eq - committed) if cfg else None,
                    "gross_cap_usd": risk.max_gross_exposure * eq, "sleeve_cap_usd": risk.portfolio.max_allocation * eq if risk.portfolio else None},
        "strategies": rows,
        "history": db.rows(c, "SELECT id, ts, kind, actor, payload FROM audit WHERE kind LIKE 'funds.%' ORDER BY id DESC LIMIT 30"),
    }


@app.get("/api/funds")
def funds_get(u: dict = Depends(user)):
    return fund_view(conn(), load_risk())


class FundIn(BaseModel):
    allocation: str
    alloc_mode: Literal["pct", "usd"] = "pct"
    reserved: str | None = None  # share of the allocation held while idle; omitted: keep the current one
    max_drawdown: str | None = None  # omitted: keep the current stop
    daily_loss: str | None = None  # omitted: keep the current stop; null clears it


def _strategy_for(c, risk, sid):
    s = funds.running(c, risk).get(sid)
    if s is None:
        fail(f"{sid} isn't running (promote a version to paper first).", 404)
    return s


@app.post("/api/funds/{sid}")
def funds_set(sid: str, body: FundIn, u: dict = Depends(owner)):
    from common.units import parse_pct
    c = conn()
    risk = load_risk()
    if risk.funds is None:
        fail("Fund controls are off.")
    s = _strategy_for(c, risk, sid)
    cur = funds.ensure(c, risk, s)
    share, usd = parse_allocation(body.allocation, body.alloc_mode)
    try:
        dd = parse_pct(body.max_drawdown) if body.max_drawdown else cur["max_drawdown"]
        dl = (parse_pct(body.daily_loss) if body.daily_loss else None) if "daily_loss" in body.model_fields_set else cur["daily_loss"]
    except ValueError as e:
        fail(str(e))
    rsv = parse_reserved(body.reserved)
    errs = funds.set_allocation(c, risk, s, share, dd, dl, u["username"], alloc_usd=usd, reserved=rsv)
    if errs:
        fail("; ".join(errs))
    rsv = db.row(c, "SELECT reserved FROM strategy_funds WHERE sid=?", (sid,))["reserved"]
    what = (f"${usd:,.0f} fixed" if usd is not None else f"{share:.1%} of equity") + (
        f", {rsv:.0%} reserved (the rest from the shared pool)" if rsv < 1 else ", fully reserved")
    return ok(f"{sid}: allocation {what}, drawdown stop {dd:.0%} of it"
              + (f", daily loss stop {dl:.0%}" if dl and s.kind == "intraday" else "") + ". Drawdown is measured from now.")


@app.post("/api/funds/{sid}/{action}")
def funds_action(sid: str, action: str, body: DecideIn, u: dict = Depends(owner)):
    c = conn()
    risk = load_risk()
    s = _strategy_for(c, risk, sid)
    funds.ensure(c, risk, s)
    if action == "pause":
        funds.pause(c, sid, body.reason or "paused by owner", u["username"])
        return ok(f"{sid} paused: no new buys" + ("; the sleeve is sold at its next decision time." if s.kind == "portfolio" else "; open positions keep their brackets."))
    if action == "resume":
        funds.resume(c, sid, u["username"])
        return ok(f"{sid} resumed; its drawdown is measured from now.")
    fail("unknown action")


# ---------------------------------------------------------------- kill switch & sim controls
class KillIn(BaseModel):
    reason: str = ""


@app.post("/api/kill")
def kill(body: KillIn, u: dict = Depends(owner)):
    c = conn()
    command("kill", {"reason": body.reason}, u["username"])
    audit.log(c, "kill_switch.requested", u["username"], {"reason": body.reason})
    return ok("Kill switch sent: cancelling orders, closing positions, suspending trading. Reset is host-only.")


class SimIn(BaseModel):
    value: float = 0


@app.post("/api/sim/{action}")
def sim_control(action: str, body: SimIn, u: dict = Depends(owner)):
    if action not in ("pause", "resume", "speed"):
        fail("unknown action")
    command(action, {"value": body.value} if action == "speed" else {}, u["username"])
    return ok()


@app.get("/api/healthz")
def healthz():
    try:
        c=conn()
        c.execute("SELECT 1")
        return {"ok": True, "dependencies": health(c)}
    except Exception:
        return JSONResponse({"ok":False,"detail":"database unavailable"},status_code=503)


@app.exception_handler(HTTPException)
async def http_err(request: Request, exc: HTTPException):
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


@app.get('/api/watchlist')
def watchlist(q: str = '', u: dict = Depends(user)):
    c=conn()
    current=monitoring.rows(c)
    assets=db.kv_get(c,'monitoring.assets') or []
    search=q.strip().upper()[:80]
    results=[a for a in assets if search and (search in a['symbol'].upper() or search in a['name'].upper())][:30]
    held=set(monitoring.held(c))
    feed=db.kv_get(c,'feed.symbols') or {}
    for r in current:
        r['strategies']=monitoring.dependencies(c,r['symbol'])
        r['held']=r['symbol'] in held
        r['last_bar']=feed.get(r['symbol'])
    return {'items':current,'results':results,'catalog_ready':bool(assets),'max_symbols':monitoring.MAX_SYMBOLS,
            'held':sorted(held),'applied':db.kv_get(c,'monitoring.applied') or []}


class WatchIn(BaseModel):
    enabled: bool
    confirmed: bool = False


@app.post('/api/watchlist/{symbol}')
def watchlist_update(symbol: str, body: WatchIn, u: dict = Depends(owner)):
    symbol=symbol.strip().upper()
    if not re.fullmatch(r'[A-Z0-9][A-Z0-9.\-]{0,14}',symbol):
        fail('Invalid ticker')
    c=conn()
    current={r['symbol'] for r in monitoring.rows(c)}
    catalog={a['symbol'] for a in db.kv_get(c,'monitoring.assets') or []}
    if symbol not in current and symbol not in catalog:
        fail('Choose an active tradable ticker from search results')
    deps=monitoring.dependencies(c,symbol)
    if not body.enabled and (deps or symbol in monitoring.held(c)) and not body.confirmed:
        fail('Disabling monitoring blocks new entries for strategies using this ticker. Held positions retain risk monitoring. Confirm to continue.',409)
    try:
        monitoring.set_enabled(c,symbol,body.enabled,u['username'])
    except ValueError as e:
        fail(str(e))
    audit.log(c,'monitoring.changed',u['username'],{'symbol':symbol,'enabled':body.enabled,'strategies':deps})
    return ok(f"{symbol} monitoring {'enabled' if body.enabled else 'disabled'}. Changes apply automatically within a few seconds.")

# Keep research routes separate from the trading lifecycle API.
from .backtests import router as backtest_router
app.include_router(backtest_router(user, owner))

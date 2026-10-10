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

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

from backtest import engine as bt
from common import audit, db, registry, sleeves, monitoring
from common import funds, risk_settings as RS
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
    rows = db.rows(c, "SELECT * FROM strategies ORDER BY sid, version DESC")
    for r in rows:
        b = j(r.pop("backtest"))
        r["bt"] = {"metrics": b["metrics"], "days": len(b["days"])} if b else None
        text = registry.text_of(c, r["hash"]) or ""
        # Validate against the current risk controls: the owner can change them at any time.
        r["validation"] = validate(text, risk).as_dict() if text else j(r["validation"], {})
        r["sim"] = db.row(c, "SELECT COUNT(*) n, COALESCE(SUM(pnl),0) pnl FROM trades WHERE strategy_hash=?", (r["hash"],))
        r["kind"] = "portfolio" if "kind: portfolio" in text else "intraday"
    return {"rows": rows}


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
        "fund": db.row(c, "SELECT allocation, status, reason, max_drawdown FROM strategy_funds WHERE sid=?", (row["sid"],)),
        "s": {k: v_ for k, v_ in row.items() if k not in ("backtest", "validation")},
        "text": text, "integrity": v.hash == h,
        "validation": v.as_dict(),
        "assumptions": s.assumptions if s else [], "suggestions": s.suggestions if s else [],
        "source": s.source if s else {}, "back_translation": back_translate(s) if s else None,
        "backtest": j(row["backtest"]),
        "versions": db.rows(c, "SELECT hash, version, status FROM strategies WHERE sid=? ORDER BY version", (row["sid"],)),
        "intents": db.rows(c, "SELECT id, ts, symbol, qty, status FROM intents WHERE strategy_hash=? ORDER BY id DESC LIMIT 20", (h,)),
        "trades": db.rows(c, "SELECT id, symbol, exit_kind, pnl FROM trades WHERE strategy_hash=? ORDER BY id DESC LIMIT 20", (h,)),
        "lifecycle": load_yaml("lifecycle.yaml"),
    }


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
    return {
        "state": sl.to_json() if sl else None, "realized": realized, "holdings": rows,
        "orders": db.rows(c, "SELECT id, day, symbol, side, qty, ref_price, reason, status, filled_qty, avg_price, note"
                             " FROM portfolio_orders WHERE strategy_hash=? ORDER BY id DESC LIMIT 60", (h,)),
        "last_day": {"day": day["day"], "event": day["event"], "records": j(day["records"], [])} if day else None,
    }


@app.get("/api/strategies/{h}/diff")
def strategy_diff(h: str, against: str, u: dict = Depends(user)):
    c = conn()
    a, b = registry.text_of(c, against) or "", registry.text_of(c, h) or ""
    d = "\n".join(difflib.unified_diff(a.splitlines(), b.splitlines(), against[:10], h[:10], lineterm=""))
    return {"diff": d or "(identical)"}


@app.post("/api/strategies/{h}/backtest")
def strategy_backtest(h: str, u: dict = Depends(owner)):
    c = conn()
    risk = load_risk()
    s, why = registry.load(c, h, risk)
    if not s:
        fail(f"Can't backtest: {why}")
    t0 = time.time()
    try:
        report = bt.run(c, s, risk, starting_equity=float(load_yaml("sim.yaml").get("starting_equity", 100_000)))
    except ValueError as e:
        fail(f"Can't backtest: {e}")
    report.update(ran_at=time.time(), ran_by=u["username"], seconds=round(time.time() - t0, 2))
    status_ = db.row(c, "SELECT status FROM strategies WHERE hash=?", (h,))["status"]
    c.execute("UPDATE strategies SET backtest=?, updated_at=? WHERE hash=?", (json.dumps(report, default=str), time.time(), h))
    if status_ == "draft":
        registry.set_status(c, h, "backtested", u["username"])
    audit.log(c, "strategy.backtested", u["username"], {"hash": h, "metrics": report["metrics"]})
    if report.get("kind") == "portfolio":
        m = report["metrics"]
        return ok(f"Backtest finished: {m['orders']} orders over {len(report['days'])} sessions, {m['return_pct']:+.2%} "
                  f"vs {m['benchmark_return_pct']:+.2%} for plain buy-and-hold.")
    return ok(f"Backtest finished: {report['metrics']['trades']} trades over {len(report['days'])} sessions.")


class PromoteIn(BaseModel):
    to: str
    reviewed: bool = False


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
        if row["status"] not in ("backtested", "live"):
            fail("Paper needs a passing validation and a backtest first.")
        risk = load_risk()
        fund_note = ""
        if risk.funds and not db.row(c, "SELECT sid FROM strategy_funds WHERE sid=?", (s.id,)):
            want = funds.default_allocation(s, risk)
            room = funds.unallocated(c, risk, exclude=s.id)
            need = s.capital if s.kind == "portfolio" else min(want, risk.funds.default_allocation)
            if room + 1e-9 < need:
                fail(f"Not enough unallocated capital: {s.id} needs at least {need:.1%} of equity and {max(room, 0):.1%} is free. "
                     "Lower other strategies' allocations on the Funds page or raise max deployed capital on the Risk page.")
            alloc = min(want, room)
            funds.ensure(c, risk, s, u["username"], alloc)
            fund_note = f" Fund allocation: {alloc:.1%} of equity" + (f" (it could use up to {want:.1%}; adjust on Funds)." if alloc < want - 1e-9 else ".")
        try:
            with db.tx(c):
                monitoring.activate(c, s, u["username"])
                db.kv_set(c, 'monitoring.strategy.' + h, True)
                registry.set_status(c, h, "paper", u["username"], paper_started_at=time.time())
        except ValueError as e:
            fail(str(e))
        command("reload", {}, u["username"])
        return ok("Promoted to paper. The broker picks it up within a few seconds." + fund_note)
    if body.to == "live":
        fail("Real-money execution is disabled; this release supports paper trading only.", 403)
    fail("unknown target")


class ModeIn(BaseModel):
    mode: str


@app.post("/api/strategies/{h}/mode")
def strategy_mode(h: str, body: ModeIn, u: dict = Depends(owner)):
    c = conn()
    if body.mode not in ("manual", "auto", "paper"):
        fail("bad mode")
    c.execute("UPDATE strategies SET mode=?, updated_at=? WHERE hash=?", (body.mode, time.time(), h))
    audit.log(c, "strategy.mode", u["username"], {"hash": h, "mode": body.mode})
    command("mode", {"hash": h, "mode": body.mode}, u["username"])
    return ok(f"Execution mode set to {body.mode}.")


class TextIn(BaseModel):
    text: str
    name: str = Field(default="", max_length=80)


@app.post("/api/strategies/compile")
def strategy_compile(body: TextIn, u: dict = Depends(owner)):
    # Up to two Opus calls (draft + one repair round) at 240s each.
    return models_api("/compile", {"text": body.text, "name": body.name.strip(), "actor": u["username"]}, timeout=600)


class DiffIn(BaseModel):
    diff: str


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
    return {"items": items, "counts": counts, "notes": db.rows(c, "SELECT * FROM notes WHERE active=1 ORDER BY id DESC")}


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
            "database": {"engine": "PostgreSQL", "size": db_size,
                         "version": db.row(c, "SHOW server_version")["server_version"]}}


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
        rows.append({
            "sid": sid, "kind": f.kind, "versions": hashes, "allocation": f.allocation, "allocation_usd": f.cap_usd(eq),
            "source": f.source, "deployed": f.deployed, "used": f.deployed / f.cap_usd(eq) if f.cap_usd(eq) else None,
            "pnl": f.pnl, "pnl_today": f.pnl_today, "peak_pnl": f.peak_pnl, "drawdown": f.drawdown, "max_drawdown": f.max_drawdown,
            "daily_loss": f.daily_loss, "status": f.status, "reason": f.reason, "base_usd": f.base_usd, "base_ts": f.base_ts,
            "wants": want, "warning": (f"its rules can use up to {want:.1%} of equity; orders above {f.allocation:.1%} are refused"
                                       if want and want > f.allocation + 1e-9 and f.kind == "portfolio" else None),
        })
    allocated = sum(f.allocation for f in acct.strategies.values())
    return {
        "enabled": cfg is not None, "limits": {k: getattr(cfg, k) for k in cfg.__dataclass_fields__} if cfg else None,
        "account": {"equity": eq, "cash": acct.cash, "deployed": acct.deployed, "working_buys": acct.working_buys,
                    "intraday_deployed": acct.intraday_deployed, "sleeve_deployed": acct.deployed - acct.intraday_deployed,
                    "reserve_usd": cfg.cash_reserve * eq if cfg else None,
                    "available": max(0.0, min(acct.cash - acct.working_buys - cfg.cash_reserve * eq, cfg.max_deployed * eq - acct.deployed)) if cfg else None,
                    "max_deployed_usd": cfg.max_deployed * eq if cfg else None, "peak_equity": acct.peak_equity, "drawdown": acct.drawdown,
                    "allocated": allocated, "unallocated": (cfg.max_deployed - allocated) if cfg else None,
                    "gross_cap_usd": risk.max_gross_exposure * eq, "sleeve_cap_usd": risk.portfolio.max_allocation * eq if risk.portfolio else None},
        "strategies": rows,
        "history": db.rows(c, "SELECT id, ts, kind, actor, payload FROM audit WHERE kind LIKE 'funds.%' ORDER BY id DESC LIMIT 30"),
    }


@app.get("/api/funds")
def funds_get(u: dict = Depends(user)):
    return fund_view(conn(), load_risk())


class FundIn(BaseModel):
    allocation: str
    max_drawdown: str
    daily_loss: str | None = None


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
    try:
        vals = [parse_pct(body.allocation), parse_pct(body.max_drawdown), parse_pct(body.daily_loss) if body.daily_loss else None]
    except ValueError as e:
        fail(str(e))
    errs = funds.set_allocation(c, risk, s, *vals, u["username"])
    if errs:
        fail("; ".join(errs))
    return ok(f"{sid}: allocation {vals[0]:.1%} of equity, drawdown stop {vals[1]:.0%} of it"
              + (f", daily loss stop {vals[2]:.0%}" if vals[2] and s.kind == "intraday" else "") + ". Drawdown is measured from now.")


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

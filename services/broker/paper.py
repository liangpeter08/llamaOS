"""Durable paper executor using real Alpaca bars, account state and broker brackets.

REST polling is deliberate: cursors and client IDs survive restarts, all catch-up bars
are recorded, and only a fresh latest completed bar may create a new entry.
"""
from __future__ import annotations

import json
import logging
import os
import signal
import time
from dataclasses import asdict
from datetime import date, datetime, timedelta

import httpx

from common import audit, db, marketdata as MD, registry, timeutil as T
from common.config import load_risk, mode, universe
from core.engine import Intent, StrategyRunner
from core import risk_guard as G
from dsl.evaluator import DayData, SignalBook
from .alpaca import Alpaca, epoch, iso, price

log = logging.getLogger("paper")
LOCK = 0x5041504552
TERMINAL = {"filled", "canceled", "expired", "rejected", "replaced"}
SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_intents(
 intent_id BIGINT PRIMARY KEY, body TEXT NOT NULL, client_id TEXT UNIQUE NOT NULL,
 broker_id TEXT, submitted_at DOUBLE PRECISION);
CREATE TABLE IF NOT EXISTS paper_orders(
 broker_id TEXT PRIMARY KEY, intent_id BIGINT, local_id BIGINT,
 filled_qty DOUBLE PRECISION NOT NULL DEFAULT 0, filled_value DOUBLE PRECISION NOT NULL DEFAULT 0,
 raw TEXT NOT NULL, kind TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS paper_exits(symbol TEXT PRIMARY KEY, reason TEXT, requested_at DOUBLE PRECISION);
CREATE TABLE IF NOT EXISTS paper_external_exits(broker_id TEXT PRIMARY KEY, body TEXT NOT NULL, reason TEXT NOT NULL);
"""


class PaperBroker:
    def __init__(self, api=None, conn=None):
        self.conn = conn or db.get()
        db.init(self.conn)
        self.conn.script(SCHEMA)
        if not self.conn.execute("SELECT pg_try_advisory_lock(?) AS locked", (LOCK,)).fetchone()["locked"]:
            raise RuntimeError("Another paper executor already owns this database")
        self.api = api or Alpaca()
        self.risk = load_risk()
        self.syms = universe()
        self.stop = False
        self.day = None
        self.runners = {}
        self.rows = {}
        self.last_news = float(db.kv_get(self.conn, "paper.news_cursor") or time.time() - 86400)
        self.last_poll = 0
        self.paused = bool(db.kv_get(self.conn, "paper.paused"))
        self.book = SignalBook()
        self.days = {}
        self.positions = []
        self.open_orders = []
        self.account_data = {}
        self.close_ts = 0
        self.flatten_ts = 0
        self.unmanaged = False
        self.last_bar = {}
        self.last_error = ""
        db.kv_set(self.conn, "risk.sha256", self.risk.sha256)

    def setup_day(self, day):
        calendar = self.api.calendar(day, day)
        self.day = day
        self.book = SignalBook()
        self.days = {s: DayData(s, day, MD.history(self.conn, s, day)) for s in self.syms}
        self.last_bar = {}
        self.runners = {}
        self.rows = {}
        if calendar:
            close = datetime.fromisoformat(f"{day}T{calendar[0]['close']}").replace(tzinfo=T.ET)
            self.close_ts = int(close.timestamp())
            hard = T.ts_at(day, self.risk.flatten_by)
            self.flatten_ts = min(hard, self.close_ts - 5 * 60)
            daily = self.api.bars(self.syms, "1Day", time.time() - 110 * 86400, T.open_ts(day))
            with db.tx(self.conn):
                for sym, bars in daily.items():
                    for b in bars:
                        d = T.et(epoch(b["t"])).date()
                        if d < day:
                            self.conn.execute("INSERT INTO daily(symbol,day,o,h,l,c,v) VALUES (?,?,?,?,?,?,?)"
                                              " ON CONFLICT(symbol,day) DO UPDATE SET o=excluded.o,h=excluded.h,l=excluded.l,c=excluded.c,v=excluded.v",
                                              (sym, str(d), b["o"], b["h"], b["l"], b["c"], b["v"]))
            self.days = {s: DayData(s, day, MD.history(self.conn, s, day)) for s in self.syms}
            bars = self.api.bars(self.syms, "1Min", T.open_ts(day), max(T.open_ts(day)+1, min(time.time()-2, self.close_ts))) if time.time() > T.open_ts(day) else {}
            self.store_bars(bars)
        else:
            self.close_ts = self.flatten_ts = 0
        self.reload()
        audit.log(self.conn, "paper.session", "broker", {"day": str(day), "calendar_open": bool(calendar), "flatten_ts": self.flatten_ts})

    def reload(self):
        registry.sync(self.conn, self.risk)
        current = {s.hash: (r, s) for r, s in registry.runnable(self.conn, self.risk) if r["status"] == "paper"}
        # Retired strategies still manage positions until their broker brackets/exit close them.
        owned = {r["strategy_hash"] for r in db.rows(self.conn, "SELECT DISTINCT strategy_hash FROM positions")}
        for h in list(self.runners):
            if h not in current and h not in owned:
                self.runners.pop(h)
                self.rows.pop(h, None)
        for h, (r, s) in current.items():
            self.rows[h] = r
            if h not in self.runners:
                runner = StrategyRunner(s, self.risk)
                runner.start_day(self.days, self.book)
                self.runners[h] = runner
        counts = db.rows(self.conn, "SELECT strategy_hash, COUNT(*) n FROM paper_intents p JOIN intents i ON i.id=p.intent_id"
                        " WHERE p.broker_id IS NOT NULL AND i.ts>=? GROUP BY strategy_hash", (T.open_ts(self.day),))
        for r in counts:
            if r["strategy_hash"] in self.runners:
                self.runners[r["strategy_hash"]].trades_today = r["n"]
        return "paper strategies: " + ", ".join(r.s.id for r in self.runners.values())

    def store_bars(self, grouped):
        for sym, bars in grouped.items():
            if sym not in self.days:
                continue
            d = self.days[sym]
            for b in sorted(bars, key=lambda x: x["t"]):
                ts = epoch(b["t"])
                if ts < T.open_ts(self.day) or ts + 60 > min(time.time()-2, self.close_ts):
                    continue
                self.conn.execute("INSERT INTO bars(symbol,ts,o,h,l,c,v) VALUES (?,?,?,?,?,?,?) ON CONFLICT(symbol,ts) DO NOTHING",
                                  (sym, ts, b["o"], b["h"], b["l"], b["c"], b["v"]))
                if not d.ts or ts > d.ts[-1]:
                    d.append(ts, b["o"], b["h"], b["l"], b["c"], b["v"])
                    self.last_bar[sym] = ts + 60

    def signals_and_news(self, now):
        for n in self.api.news(self.syms, self.last_news-120, now):
            self.conn.execute("INSERT INTO news(ext_id,ts,session,symbols,headline,summary,source,url,content) VALUES (?,?,?,?,?,?,?,?,?)"
                              " ON CONFLICT(ext_id) DO UPDATE SET url=COALESCE(EXCLUDED.url,news.url),content=COALESCE(NULLIF(EXCLUDED.content,''),news.content)", ("alpaca:"+str(n["id"]), epoch(n["created_at"]), "alpaca",
                              json.dumps(n["symbols"]), n["headline"], n.get("summary", ""), n.get("source", "alpaca"), n.get("url"), n.get("content")))
        self.last_news = now
        db.kv_set(self.conn, "paper.news_cursor", now)
        self.book = MD.signal_book(self.conn, T.prior_close_ts(self.day), int(now))
        for runner in self.runners.values():
            for ev in runner.evals.values():
                ev.signals = self.book
                ev.memo.clear()

    def track_order(self, order, iid, kind):
        oid = order["id"]
        row = db.row(self.conn, "SELECT * FROM paper_orders WHERE broker_id=?", (oid,))
        qty = float(order.get("filled_qty") or 0)
        value = qty * float(order.get("filled_avg_price") or 0)
        with db.tx(self.conn):
            if row is None:
                local = self.conn.insert("INSERT INTO orders(intent_id,ts,symbol,side,qty,kind,status,updated_ts) VALUES (?,?,?,?,?,?,?,?)",
                                         (iid, epoch(order["created_at"]), order["symbol"], order["side"], int(float(order["qty"])), kind,
                                          order["status"], time.time()))
                self.conn.execute("INSERT INTO paper_orders(broker_id,intent_id,local_id,raw,kind) VALUES (?,?,?,?,?)",
                                  (oid, iid, local, json.dumps(order), kind))
                previous_qty = previous_value = 0
            else:
                local, previous_qty, previous_value = row["local_id"], row["filled_qty"], row["filled_value"]
            self.conn.execute("UPDATE orders SET status=?,filled_qty=?,avg_price=?,limit_price=?,stop=?,updated_ts=? WHERE id=?",
                              (order["status"], qty, order.get("filled_avg_price"), order.get("limit_price"), order.get("stop_price"), time.time(), local))
            if qty > previous_qty:
                delta = qty - previous_qty
                px = (value - previous_value) / delta
                ts = epoch(order.get("filled_at") or order.get("updated_at") or order["created_at"])
                self.conn.execute("INSERT INTO fills(order_id,intent_id,ts,symbol,side,qty,price,kind) VALUES (?,?,?,?,?,?,?,?)",
                                  (local, iid, ts, order["symbol"], order["side"], delta, px, kind))
                audit.log(self.conn, "paper.fill", "broker", {"broker_id": oid, "intent_id": iid, "qty": delta, "price": px, "kind": kind})
                if kind != "entry" and iid:
                    intent = db.row(self.conn, "SELECT * FROM intents WHERE id=?", (iid,))
                    entry = db.row(self.conn, "SELECT filled_qty,filled_value,raw FROM paper_orders WHERE intent_id=? AND kind='entry'", (iid,))
                    if intent and entry and entry["filled_qty"]:
                        ep = entry["filled_value"] / entry["filled_qty"]
                        pnl = (px-ep) * delta * (1 if intent["side"] == "long" else -1)
                        rps = abs(ep-intent["stop"])
                        er = json.loads(entry["raw"])
                        self.conn.execute("INSERT INTO trades(intent_id,sid,strategy_hash,symbol,side,qty,entry_ts,entry_price,exit_ts,exit_price,exit_kind,pnl,r_multiple,narrative)"
                                          " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                          (iid, intent["sid"], intent["strategy_hash"], intent["symbol"], intent["side"], delta,
                                           epoch(er.get("filled_at") or er["created_at"]), ep, ts, px, kind, pnl,
                                           pnl/(rps*delta) if rps else None, "Actual Alpaca paper fill; broker order " + oid))
            self.conn.execute("UPDATE paper_orders SET filled_qty=?,filled_value=?,raw=? WHERE broker_id=?", (qty, value, json.dumps(order), oid))
        for leg in order.get("legs") or []:
            self.track_order(leg, iid, "stop" if leg["type"].startswith("stop") else "target")
        return local

    def sync(self):
        self.account_data = self.api.account()
        for external in db.rows(self.conn,"SELECT * FROM paper_external_exits"):
            order=json.loads(external["body"])
            ref=self.owner_intent(order["symbol"])
            self.track_order(order,ref["id"] if ref else None,external["reason"])
            self.conn.execute("DELETE FROM paper_external_exits WHERE broker_id=?",(external["broker_id"],))
        self.positions = self.api.positions()
        self.open_orders = self.api.orders()
        for row in db.rows(self.conn, "SELECT p.* FROM paper_intents p JOIN intents i ON i.id=p.intent_id WHERE i.status IN ('submitting','submitted') OR (p.broker_id IS NOT NULL AND (i.ts>=? OR EXISTS (SELECT 1 FROM paper_orders o WHERE o.intent_id=i.id AND o.kind='entry' AND o.filled_qty>COALESCE((SELECT SUM(x.filled_qty) FROM paper_orders x WHERE x.intent_id=i.id AND x.kind!='entry'),0))))", (T.open_ts(self.day),)):
            order = self.api.order(row["broker_id"]) if row["broker_id"] else self.api.by_client_id(row["client_id"])
            if order:
                self.conn.execute("UPDATE paper_intents SET broker_id=? WHERE intent_id=?", (order["id"], row["intent_id"]))
                self.track_order(order, row["intent_id"], "entry")
                status = "filled" if order["status"] == "filled" else "submitted" if order["status"] not in TERMINAL else "unfilled"
                self.conn.execute("UPDATE intents SET status=? WHERE id=? AND status IN ('submitting','submitted')", (status, row["intent_id"]))
                if order["status"] not in TERMINAL and time.time()-epoch(order["created_at"]) > self.risk.cancel_unfilled_after:
                    self.api.cancel(order["id"])
        for row in db.rows(self.conn, "SELECT * FROM paper_orders WHERE kind NOT IN ('entry','stop','target')"):
            raw = json.loads(row["raw"])
            if raw["status"] not in TERMINAL:
                self.track_order(self.api.order(row["broker_id"]), row["intent_id"], row["kind"])
        self.unmanaged = any(not o.get("client_order_id", "").startswith("llama-") and
                             not db.row(self.conn, "SELECT broker_id FROM paper_orders WHERE broker_id=?", (o["id"],)) for o in self.open_orders)
        for pos in self.positions:
            ref = self.owner_intent(pos["symbol"])
            if not ref:
                self.unmanaged = True
        self.persist_account()
        self.restore_runner_positions()

    def owner_intent(self, sym):
        return db.row(self.conn, "SELECT i.* FROM intents i JOIN paper_intents p ON p.intent_id=i.id JOIN paper_orders o"
                      " ON o.intent_id=i.id AND o.kind='entry' WHERE i.symbol=? AND o.filled_qty>0"
                      " AND o.filled_qty>COALESCE((SELECT SUM(x.filled_qty) FROM paper_orders x WHERE x.intent_id=i.id AND x.kind!='entry'),0)"
                      " ORDER BY i.id DESC LIMIT 1", (sym,))

    def restore_runner_positions(self):
        for runner in self.runners.values():
            for sym in self.syms:
                runner.clear_position(sym)
        for p in self.positions:
            ref = self.owner_intent(p["symbol"])
            if ref and ref["strategy_hash"] in self.runners and p["symbol"] in self.days:
                d = self.days[p["symbol"]]
                idx = next((i for i,t in enumerate(d.ts) if t >= ref["ts"]), max(0,len(d)-1))
                self.runners[ref["strategy_hash"]].set_position(p["symbol"], ref["side"], float(p["avg_entry_price"]), ref["stop"], idx)

    def state(self, sym=None):
        now = time.time()
        opened = {p["symbol"] for p in self.positions}
        entry_orders = [o for o in self.open_orders if o.get("order_class") == "bracket"]
        opened |= {o["symbol"] for o in entry_orders}
        exposure = sum(abs(float(p["market_value"])) for p in self.positions)
        exposure += sum(max(0,float(o["qty"])-float(o.get("filled_qty") or 0))*float(o.get("limit_price") or 0) for o in entry_orders)
        counts = db.rows(self.conn, "SELECT i.sid,COUNT(*) n FROM paper_intents p JOIN intents i ON i.id=p.intent_id"
                        " WHERE p.broker_id IS NOT NULL AND i.ts>=? GROUP BY i.sid", (T.open_ts(self.day),))
        return G.AccountState(float(self.account_data["equity"]), float(self.account_data["last_equity"]), exposure,
                              opened, sum(r["n"] for r in counts), {r["sid"]:r["n"] for r in counts},
                              bool((db.kv_get(self.conn,"kill_switch") or {}).get("active")),
                              max(0,now-self.last_bar.get(sym,0)) if sym else 0)

    def persist_account(self):
        a = self.account_data
        eq,cash,start = float(a["equity"]),float(a["cash"]),float(a["last_equity"])
        pnl = {}
        for r in db.rows(self.conn,"SELECT sid,SUM(pnl) pnl FROM trades WHERE exit_ts>=? GROUP BY sid",(T.open_ts(self.day),)):
            pnl[r["sid"]] = r["pnl"]
        with db.tx(self.conn):
            self.conn.execute("DELETE FROM positions")
            for p in self.positions:
                ref = self.owner_intent(p["symbol"]) or {}
                pnl[ref.get("sid","unmanaged")] = pnl.get(ref.get("sid","unmanaged"),0)+float(p.get("unrealized_pl") or 0)
                self.conn.execute("INSERT INTO positions(symbol,qty,side,avg_price,stop,target,strategy_hash,sid,intent_id,opened_ts,last_price) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                                  (p["symbol"],float(p["qty"]),p["side"],float(p["avg_entry_price"]),ref.get("stop"),ref.get("target"),ref.get("strategy_hash"),ref.get("sid"),ref.get("id"),ref.get("ts"),float(p["current_price"])))
            ts=int(time.time()//60)*60
            self.conn.execute("INSERT INTO equity_history(ts,source,session,equity,cash,strat_pnl) VALUES (?,?,?,?,?,?) ON CONFLICT(source,ts) DO UPDATE SET equity=excluded.equity,cash=excluded.cash,strat_pnl=excluded.strat_pnl",
                              (ts,"alpaca_paper",str(self.day),eq,cash,json.dumps(pnl)))
        db.kv_set(self.conn,"account",{"equity":eq,"cash":cash,"start_equity":start,"day_pnl":eq-start,
                   "gross_exposure":sum(abs(float(p["market_value"])) for p in self.positions),"open_positions":len(self.positions),
                   "trades_today":self.state().trades_today,"sim_ts":time.time(),"source":"alpaca_paper"})
        db.kv_set(self.conn,"reconcile",{"ok":not self.unmanaged,"sim_ts":time.time(),"broker":"alpaca_paper","unmanaged":self.unmanaged})

    def checks(self, it):
        state = self.state(it.symbol)
        d = self.days[it.symbol]
        hist = d.history[-20:]
        adv = sum(x.c*x.v for x in hist)/20 if len(hist)==20 else None
        # Risk is measured at the worst permitted fill, using submitted tick prices.
        worst=float(price(it.limit)); stop=float(price(it.stop)); target=float(price(it.target))
        current = Intent(**{**asdict(it),"ts":int(time.time()),"entry":worst,"stop":stop,"target":target,
                            "risk_usd":it.qty*abs(worst-stop)})
        runner = self.runners.get(it.strategy_hash)
        checks=G.check_intent(current,state,self.risk,runner.s.limits if runner else {},adv)
        checks += [G.Check("paper_enabled",mode()=="paper","paper only"),
                   G.Check("strategy_eligible",bool(runner and self.rows.get(it.strategy_hash,{}).get("status")=="paper"),"registered paper strategy"),
                   G.Check("market_open",bool(self.clock["is_open"]),"regular session"),
                   G.Check("before_flatten",time.time()<self.flatten_ts,"calendar-aware flatten deadline"),
                   G.Check("strategy_window",bool(runner and T.in_window(time.time(),runner.s.entry_window)),"current strategy window"),
                   G.Check("not_paused",not self.paused,"entry pause"),
                   G.Check("account_available",not any(self.account_data.get(k) for k in ("trading_blocked","account_blocked","trade_suspended_by_user")),"account status"),
                   G.Check("managed_account",not self.unmanaged,"no unowned orders/positions"),
                   G.Check("buying_power",it.qty*it.limit<=float(self.account_data.get("buying_power") or 0),"broker buying power")]
        if d.c:
            snapshot=self.api.snapshots([it.symbol]).get(it.symbol,{})
            latest=snapshot.get("latestTrade") or {}
            if latest.get("t") and time.time()-epoch(latest["t"])<=self.risk.stale_feed_halt:
                checks.append(G.drift_check(it.entry,float(latest["p"]),self.risk))
            else:
                checks.append(G.Check("price_fresh",False,"no fresh quote for submission"))
        return checks

    def create_intent(self,it,checks):
        iid=self.conn.insert("INSERT INTO intents(ts,strategy_hash,sid,symbol,side,qty,entry,limit_price,stop,target,risk_usd,risk_pct,decision,signals,guard,status,mode,created_wall,expires_wall) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                             (it.ts,it.strategy_hash,it.sid,it.symbol,it.side,it.qty,it.entry,it.limit,it.stop,it.target,it.risk_usd,it.risk_pct,
                              json.dumps({"entry":it.decision,"universe":it.universe}),json.dumps({name:{"value":value,**(db.row(self.conn,"SELECT model,tier,rationale FROM signals WHERE symbol=? AND name=? AND ts<=? ORDER BY ts DESC LIMIT 1",(it.symbol,name,it.ts)) or {})} for name,value in it.signals.items()}),json.dumps([c.as_dict() for c in checks]),
                              "pending",self.rows[it.strategy_hash]["mode"],time.time(),time.time()+self.risk.approval_expiry))
        it.id=iid
        self.conn.execute("INSERT INTO paper_intents(intent_id,body,client_id) VALUES (?,?,?)",(iid,json.dumps(asdict(it)),f"llama-{iid}-{it.strategy_hash[:12]}"))
        audit.log(self.conn,"intent.pending","broker",{"intent_id":iid,"symbol":it.symbol,"source":"alpaca_paper"})
        if self.rows[it.strategy_hash]["mode"] in ("auto","paper"):
            self.conn.execute("UPDATE intents SET status='approved',decided_by='paper-auto',decided_wall=? WHERE id=?",(time.time(),iid))

    def approvals(self):
        for row in db.rows(self.conn,"SELECT i.*,p.body,p.client_id FROM intents i JOIN paper_intents p ON p.intent_id=i.id WHERE i.status IN ('pending','approved','submitting') ORDER BY i.id"):
            if row["status"]=="submitting":
                # Never blindly repeat an uncertain POST. sync() discovers it by client ID.
                self.last_error="Uncertain order submission requires reconciliation: intent " + str(row["id"])
                continue
            if time.time()>row["expires_wall"]:
                self.conn.execute("UPDATE intents SET status='expired' WHERE id=?",(row["id"],))
                continue
            if row["status"]!="approved":
                continue
            it=Intent(**json.loads(row["body"]))
            self.sync()
            checks=self.checks(it)
            if not G.passed(checks):
                self.conn.execute("UPDATE intents SET status='auto_rejected',reason=?,recheck=? WHERE id=?",
                                  (", ".join(c.name for c in checks if not c.ok),json.dumps([c.as_dict() for c in checks]),it.id))
                continue
            self.conn.execute("UPDATE intents SET status='submitting',recheck=? WHERE id=?",(json.dumps([c.as_dict() for c in checks]),it.id))
            try:
                order=self.api.bracket(it,row["client_id"])
            except httpx.HTTPStatusError as e:
                if e.response.status_code>=500 or e.response.status_code==429:
                    # A server error is not proof that a POST was rejected.
                    raise
                # An explicit client rejection is definitive; connection failures are not.
                self.conn.execute("UPDATE intents SET status='broker_rejected',reason=? WHERE id=?",(f"Alpaca HTTP {e.response.status_code}",it.id))
                audit.log(self.conn,"paper.order_rejected","broker",{"intent_id":it.id,"http_status":e.response.status_code})
                continue
            self.conn.execute("UPDATE paper_intents SET broker_id=?,submitted_at=? WHERE intent_id=?",(order["id"],time.time(),it.id))
            self.track_order(order,it.id,"entry")
            self.conn.execute("UPDATE intents SET status='submitted' WHERE id=?",(it.id,))
            audit.log(self.conn,"paper.order_submitted","broker",{"intent_id":it.id,"broker_id":order["id"]})
            self.sync()

    def exit_position(self,sym,reason):
        existing=db.row(self.conn,"SELECT * FROM paper_exits WHERE symbol=?",(sym,))
        if existing and time.time()-existing["requested_at"]<30:
            return
        ref=self.owner_intent(sym)
        for order in self.open_orders:
            if order["symbol"]==sym:
                self.api.cancel(order["id"])
        # Wait until cancel acknowledgments release the bracket's reserved shares.
        if any(o["symbol"]==sym for o in self.api.orders()):
            return
        self.conn.execute("INSERT INTO paper_exits(symbol,reason,requested_at) VALUES (?,?,?) ON CONFLICT(symbol) DO UPDATE SET reason=excluded.reason,requested_at=excluded.requested_at",(sym,reason,time.time()))
        order=self.api.close_position(sym)
        if order:
            self.track_order(order,ref["id"] if ref else None,reason)
        audit.log(self.conn,"paper.exit_requested","broker",{"symbol":sym,"reason":reason})

    def kill(self,reason,actor="broker"):
        db.kv_set(self.conn,"kill_switch",{"active":True,"reason":reason,"ts":time.time(),"by":actor})
        self.conn.execute("UPDATE intents SET status='void',reason='kill switch' WHERE status IN ('pending','approved')")
        self.api.cancel_all()
        for p in self.api.positions():
            self.exit_position(p["symbol"],"kill")
        audit.log(self.conn,"kill_switch.triggered",actor,{"reason":reason,"source":"alpaca_paper"})

    def commands(self):
        for c in db.rows(self.conn,"SELECT * FROM commands WHERE status='pending' ORDER BY id"):
            payload=json.loads(c["payload"] or "{}")
            result="ok"
            if c["kind"]=="kill":
                self.kill(payload.get("reason") or "dashboard",c["issued_by"])
            elif c["kind"] in ("pause","resume"):
                self.paused=c["kind"]=="pause"
                db.kv_set(self.conn,"paper.paused",self.paused)
            elif c["kind"] in ("reload","mode"):
                result=self.reload()
            else:
                result="unsupported in real-time paper mode"
            self.conn.execute("UPDATE commands SET status='done',processed_at=?,result=? WHERE id=?",(time.time(),result,c["id"]))
            audit.log(self.conn,"command."+c["kind"],c["issued_by"],{"result":result})

    def tick(self):
        self.clock=self.api.clock()
        now=epoch(self.clock["timestamp"])
        day=T.et(now).date()
        if day!=self.day:
            self.setup_day(day)
        self.sync()
        self.commands()
        db.kv_set(self.conn,"sim.now",now)  # existing models/dashboard consume this clock
        if time.time()-self.last_poll>=15:
            start=min((d.ts[-1] for d in self.days.values() if d.ts),default=T.open_ts(day))
            if self.close_ts and time.time()>T.open_ts(day):
                self.store_bars(self.api.bars(self.syms,"1Min",start,min(time.time()-2,self.close_ts)))
            try:
                self.signals_and_news(now)
            except httpx.HTTPError:
                self.last_error="news feed unavailable"
            self.last_poll=time.time()
        ks=(db.kv_get(self.conn,"kill_switch") or {}).get("active")
        if self.clock["is_open"]:
            if not ks and G.daily_loss_breached(self.state(),self.risk):
                self.kill("daily loss limit")
                ks=True
            if ks or time.time()>=self.flatten_ts:
                for p in self.positions:
                    self.exit_position(p["symbol"],"kill" if ks else "flatten")
                for o in self.open_orders:
                    if o.get("order_class")=="bracket":
                        self.api.cancel(o["id"])
            else:
                for p in self.positions:
                    ref=self.owner_intent(p["symbol"])
                    if ref and ref["strategy_hash"] in self.runners and p["symbol"] in self.days:
                        d=self.days[p["symbol"]]
                        if d.ts:
                            req=self.runners[ref["strategy_hash"]].check_exit(p["symbol"],len(d)-1)
                            if req:self.exit_position(p["symbol"],req.reason)
                self.approvals()
                if not self.paused and not self.unmanaged and mode()=="paper":
                    for sym,d in self.days.items():
                        if not d.ts or time.time()-d.close_ts(len(d)-1)>min(90,self.risk.stale_feed_halt):
                            continue
                        cursor=f"paper.evaluated.{day}.{sym}"
                        if (db.kv_get(self.conn,cursor) or 0)>=d.ts[-1]:continue
                        db.kv_set(self.conn,cursor,d.ts[-1])
                        busy=sym in self.state(sym).open_symbols or bool(db.row(self.conn,"SELECT id FROM intents WHERE symbol=? AND status IN ('pending','approved','submitting','submitted')",(sym,)))
                        for h,runner in self.runners.items():
                            if self.rows.get(h,{}).get("status")!="paper":continue
                            it=runner.check_entry(sym,len(d)-1,float(self.account_data["equity"]),busy)
                            if it:
                                checks=self.checks(it)
                                if G.passed(checks):
                                    with db.tx(self.conn):self.create_intent(it,checks)
                                    busy=True
                                else:runner.reject(sym,len(d)-1)
                self.approvals()
        else:
            self.conn.execute("UPDATE intents SET status='expired' WHERE status IN ('pending','approved') AND expires_wall<?",(time.time(),))
        db.kv_set(self.conn,"feed.last_bar",max(self.last_bar.values(),default=None))
        db.kv_set(self.conn,"feed.symbols",self.last_bar)
        db.kv_set(self.conn,"paper.error",None)
        db.kv_set(self.conn,"health.broker",{"ts":time.time()})
        db.kv_set(self.conn,"sim.state",{"session":str(day),"phase":"open" if self.clock["is_open"] else "closed",
                      "source":"alpaca_paper","paused":self.paused,"kill":bool(ks),"next_open":self.clock["next_open"],
                      "feed":self.api.feed,"error":self.last_error,"strategies":[r.s.id for r in self.runners.values()]})

    def run(self):
        for sym in self.syms:
            asset=self.api.asset(sym)
            if not asset.get("tradable") or asset.get("status")!="active":raise ValueError("Untradable watchlist symbol: "+sym)
        cfg=self.api.request("GET","/v2/account/configurations")
        if float(cfg.get("max_margin_multiplier",0))!=1 or (not self.risk.allow_short and not cfg.get("no_shorting")):
            raise RuntimeError("Paper account guards are not applied; run make broker-guard")
        audit.log(self.conn,"broker.start","broker",{"source":"alpaca_paper","mode":mode(),"risk_sha256":self.risk.sha256})
        while not self.stop:
            try:
                self.last_error=""
                self.tick()
            except Exception as e:
                self.last_error=type(e).__name__
                log.error("Paper loop failed (%s); entries remain blocked until a successful refresh",type(e).__name__)
                db.kv_set(self.conn,"paper.error",{"type":type(e).__name__,"ts":time.time()})
            time.sleep(5)


def main():
    logging.basicConfig(level=logging.INFO,format="%(asctime)s paper %(levelname)s %(message)s")
    broker=PaperBroker()
    def stop(*_):broker.stop=True
    signal.signal(signal.SIGTERM,stop)
    signal.signal(signal.SIGINT,stop)
    broker.run()

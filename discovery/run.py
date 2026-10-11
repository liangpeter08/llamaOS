"""Single-ticker discovery run: the recursive loop and its overfitting guards.

    sessions -> [discovery | sealed holdout]
    profile (discovery data only)
    generation 0: hypotheses (templates, plus Claude when available)
    generations 1..G: revise the best candidates against their measured weaknesses; a revision is kept only
                      if it beats its parent's score by a margin (the score already penalises complexity)
    finalists: passed every gate on discovery data, at most two per kind, different families preferred
    holdout: each finalist replayed ONCE on the sealed sessions; nothing is tuned after that
    statistics: deflated Sharpe over all trials of that kind, CSCV probability of backtest overfitting
    drafts: holdout survivors are written as new strategy files (status draft; the owner reviews and approves)

Every candidate, including invalid and duplicate ones, is a row in discovery_trials.
"""
from __future__ import annotations

import json
import statistics
import time
from datetime import date
from typing import Callable, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from common import audit, db, registry
from common.config import RiskConfig, load_risk
from dsl.loader import file_hash
from dsl.validator import validate

from . import evaluate as EV
from . import mutate as M
from . import profile as PF
from . import stats as ST
from . import templates as TPL

IMPROVE_MARGIN = 0.05  # a revision must beat its parent's score by this much to count as an improvement
PATIENCE = 2  # generations in a row without a better best candidate before the loop stops


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ticker: str = Field(min_length=1, max_length=8)
    peers: list[str] = Field(default_factory=lambda: ["SPY", "QQQ"], max_length=4)
    kinds: list[Literal["intraday", "daily"]] = Field(default_factory=lambda: ["intraday", "daily"], min_length=1)
    generations: int = Field(default=4, ge=0, le=8)
    parents: int = Field(default=3, ge=1, le=5)
    children: int = Field(default=2, ge=1, le=4)
    max_trials: int = Field(default=60, ge=5, le=300)  # per kind
    holdout_pct: float = Field(default=0.25, ge=0.1, le=0.5)
    folds: int = Field(default=4, ge=2, le=8)
    min_trades: int = Field(default=15, ge=3, le=500)
    slippage_bps: float = Field(default=2, ge=0, le=100)
    save_drafts: bool = True
    save_all: bool = False  # also save every other valid, evaluated trial as a draft (not only holdout survivors)
    # follow-up runs: continue from candidates of an earlier run, or from a saved strategy file
    seed_trials: list[int] = Field(default_factory=list, max_length=5)
    seed_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    parent_run_id: int | None = Field(default=None, gt=0)
    templates: bool = True  # also propose the template families in generation 0 (off by default for follow-ups)

    @model_validator(mode="after")
    def clean(self):
        self.ticker = self.ticker.strip().upper()
        self.peers = [p.strip().upper() for p in self.peers if p.strip() and p.strip().upper() != self.ticker]
        if not all(c.isalnum() or c == "." for c in self.ticker + "".join(self.peers)):
            raise ValueError("invalid ticker symbol")
        return self


class OfflineGenerator:
    """templates@v1 hypotheses; revisions apply the evaluator's suggested fix for each weakness."""
    name = TPL.ORIGIN

    def propose(self, conn, cfg: Config, kind: str, prof: dict, peers: list[str], risk: RiskConfig) -> list[dict]:
        return [h for h in TPL.hypotheses(cfg.ticker, risk, peers, prof, (kind,)) if h["kind"] == kind]

    def revise(self, conn, cfg: Config, trial: dict, ev: dict, history: list[dict], risk: RiskConfig, n: int) -> list[dict]:
        out = []
        for w in [*(ev.get("weaknesses") or []), *EV.explorations(ev, trial["kind"])]:  # fixes first, then exploration
            if not w.get("fix"):
                continue
            r = M.apply(trial["yaml"], w["fix"])
            if r and all(r[0] != o["yaml"] for o in out):
                out.append({"yaml": r[0], "change": r[1], "addresses": w["kind"], "origin": TPL.ORIGIN})
            if len(out) >= n:
                break
        return out


def resolve_seeds(conn, cfg: Config, risk: RiskConfig) -> tuple[list[dict], int | None]:
    """Generation-0 candidates for a follow-up run, and the run it continues (if any)."""
    seeds, parent = [], cfg.parent_run_id
    rows = [db.row(conn, "SELECT * FROM discovery_trials WHERE id=?", (tid,)) for tid in cfg.seed_trials]
    for tid, r in zip(cfg.seed_trials, rows):
        if r is None:
            raise ValueError(f"no discovery trial #{tid}")
        hyp = json.loads(r["hypothesis"] or "{}")
        seeds.append({"kind": r["kind"], "yaml": r["yaml"], "family": r["family"], "title": r["title"], "mechanism": hyp.get("mechanism"),
                      "falsify": hyp.get("falsify"), "origin": "seed", "change": f"seed: trial #{tid} of run #{r['run_id']}"
                      + (f" (last change: {r['change']})" if r["change"] else "")})
        parent = parent or r["run_id"]
    if cfg.seed_hash:
        text = registry.text_of(conn, cfg.seed_hash)
        if text is None:
            raise ValueError("no strategy with that hash")
        made = db.row(conn, "SELECT run_id, family, title, hypothesis FROM discovery_trials WHERE strategy_hash=? ORDER BY id DESC LIMIT 1", (cfg.seed_hash,))
        v = validate(text, risk)
        if not v.ok:
            raise ValueError("the strategy file doesn't validate: " + "; ".join(v.errors[:3]))
        s = v.strategy
        kind = "daily" if s.kind == "portfolio" else "intraday"
        seeds.append({"kind": kind, "yaml": text, "family": (made or {}).get("family") or s.id, "title": (made or {}).get("title") or f"{s.id} v{s.version}",
                      "mechanism": json.loads((made or {}).get("hypothesis") or "{}").get("mechanism") or (s.source or {}).get("text"),
                      "origin": "seed", "change": f"seed: strategy {s.id} v{s.version} (sha256 {cfg.seed_hash[:12]})"})
        parent = parent or (made or {}).get("run_id")
    for sd in seeds:
        v = validate(sd["yaml"], risk)
        s = v.strategy
        syms = (s.symbols if s.kind == "portfolio" else s.universe_symbols) if v.ok else []
        if syms != [cfg.ticker]:
            raise ValueError(f"discovery iterates single-ticker strategies for {cfg.ticker}; this one trades {', '.join(syms) or 'the whole watchlist'}")
    return seeds, parent


def ancestry(conn, run_id: int | None) -> list[int]:
    """This run's parent, grandparent, ... (follow-ups re-use the same holdout sessions, so their trials add up)."""
    out, seen = [], set()
    while run_id and run_id not in seen:
        seen.add(run_id)
        out.append(run_id)
        r = db.row(conn, "SELECT config FROM discovery_runs WHERE id=?", (run_id,))
        run_id = json.loads(r["config"]).get("parent_run_id") if r else None
    return out


def _bar_days(conn, sym: str) -> list[date]:
    return [date.fromisoformat(r["d"]) for r in db.rows(
        conn, "SELECT DISTINCT (to_timestamp(ts) AT TIME ZONE 'America/New_York')::date::text d FROM bars WHERE symbol=? ORDER BY d", (sym,))]


def _split(days: list[date], pct: float) -> tuple[list[date], list[date]]:
    n = max(5, int(round(len(days) * pct)))
    return days[:-n], days[-n:]


def sessions(conn, cfg: Config) -> dict:
    """Discovery and holdout sessions per kind, or the reason a kind can't run."""
    out = {}
    if "daily" in cfg.kinds:
        rows = PF.daily_rows(conn, cfg.ticker)
        usable = [date.fromisoformat(r["day"]) for r in rows[60:]]  # 60 sessions of warm-up for daily features and ADV
        out["daily"] = ({"error": f"{cfg.ticker} has {len(rows)} daily sessions stored; daily discovery needs at least 140"}
                        if len(usable) < 80 else dict(zip(("discovery", "holdout"), _split(usable, cfg.holdout_pct))))
    if "intraday" in cfg.kinds:
        days = _bar_days(conn, cfg.ticker)
        out["intraday"] = ({"error": f"{cfg.ticker} has {len(days)} sessions of 1-minute bars; intraday discovery needs at least 20"}
                           if len(days) < 20 else dict(zip(("discovery", "holdout"), _split(days, cfg.holdout_pct))))
    return out


def _available_peers(conn, cfg: Config, kind: str, days: list[date]) -> list[str]:
    ok = []
    for p in cfg.peers:
        if kind == "daily":
            n = db.row(conn, "SELECT COUNT(*) n FROM daily WHERE symbol=? AND day>=? AND day<=?", (p, days[0].isoformat(), days[-1].isoformat()))["n"]
        else:
            n = len([d for d in _bar_days(conn, p) if days[0] <= d <= days[-1]])
        if n >= 0.9 * len(days):
            ok.append(p)
    return ok


class Ledger:
    """discovery_trials rows for one run; the hash set rejects exact duplicates (still counted)."""

    def __init__(self, conn, run_id: int):
        self.conn, self.run_id, self.seen = conn, run_id, set()

    def add(self, kind: str, gen: int, cand: dict, parent: dict | None = None) -> dict:
        h = file_hash(cand["yaml"])
        status = "duplicate" if h in self.seen else "pending"
        self.seen.add(h)
        tid = self.conn.insert(
            "INSERT INTO discovery_trials(run_id, created_at, kind, generation, parent_id, origin, family, title, hypothesis, change, yaml, hash, status)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (self.run_id, time.time(), kind, gen, parent["id"] if parent else None, cand.get("origin") or "unknown",
             cand.get("family") or (parent or {}).get("family"), cand.get("title") or (parent or {}).get("title"),
             json.dumps({**{k: cand.get(k) or (parent or {}).get(k) for k in ("mechanism", "falsify")},  # revisions inherit the hypothesis
                         **{k: cand.get(k) for k in ("addresses", "why")}}), cand.get("change"), cand["yaml"], h, status))
        return {"id": tid, "kind": kind, "generation": gen, "parent": parent["id"] if parent else None, "yaml": cand["yaml"], "hash": h,
                "family": cand.get("family") or (parent or {}).get("family"), "title": cand.get("title") or (parent or {}).get("title"),
                "change": cand.get("change"), "origin": cand.get("origin"), "status": status,
                "mechanism": cand.get("mechanism") or (parent or {}).get("mechanism"),
                "falsify": cand.get("falsify") or (parent or {}).get("falsify")}

    def record(self, t: dict, ev: dict, status: str):
        t.update(status=status, ev=ev, score=ev.get("score") if ev.get("ok") else None)
        stored = {k: v for k, v in ev.items() if k != "series"}
        if ev.get("ok"):  # per-session Sharpe: follow-up runs need every ancestor trial's for the deflated Sharpe
            stored["sr"] = ST.sharpe(ev["series"], annualize=False) or 0.0
        self.conn.execute("UPDATE discovery_trials SET status=?, score=?, evaluation=? WHERE id=?",
                          (status, t["score"], json.dumps(stored, default=str), t["id"]))


def _lineage(t: dict, by_id: dict[int, dict]) -> list[str]:
    chain = []
    while t:
        chain.append(t.get("change") or f"origin: {t.get('title')}")
        t = by_id.get(t.get("parent"))
    return list(reversed(chain))


def _brief(ev: dict) -> dict:
    if not ev or not ev.get("ok"):
        return {"ok": False, "errors": (ev or {}).get("errors")}
    b = ev["base"]
    return {"score": round(ev["score"], 3), "sharpe": round(b["sharpe"], 3), "fold_median_sharpe": round(ev["fold_median_sharpe"], 3),
            "stress_sharpe": round(ev["stress"]["sharpe"], 3), "trades": b["trades"], "return_pct": b["return_pct"],
            "max_drawdown": b["max_drawdown"], "consistency": b["consistency"], "baseline_sharpe": round(ev["baseline_sharpe"], 3),
            "complexity": ev["complexity"], "gates": ev["gates"], "weaknesses": [w["kind"] for w in ev.get("weaknesses") or []]}


def run_kind(conn, cfg: Config, kind: str, win: dict, prof: dict, closes: dict, peers: list[str], risk: RiskConfig,
             gen_: OfflineGenerator, ledger: Ledger, progress: Callable[[str], None], seeds: list[dict] = (),
             prior: dict | None = None) -> dict:
    prior = prior or {"trials": 0, "looks": 0, "srs": []}
    ctx = EV.Context(cfg.ticker, kind, win["discovery"], peers=peers, closes=closes, folds=cfg.folds,
                     slippage=cfg.slippage_bps / 10_000, min_trades=cfg.min_trades,
                     min_history_sessions=20)
    trials: list[dict] = []

    def evaluate(t: dict):
        if t["status"] == "duplicate":
            return
        ev = EV.evaluate(conn, t["yaml"], risk, ctx)
        ledger.record(t, ev, "evaluated" if ev.get("ok") else "invalid")

    progress(f"{kind}: generating hypotheses")
    initial = [sd for sd in seeds if sd["kind"] == kind]
    if cfg.templates or not initial:
        initial += gen_.propose(conn, cfg, kind, prof, peers, risk)
    for cand in initial:
        if len(trials) >= cfg.max_trials:
            break
        t = ledger.add(kind, 0, cand)
        trials.append(t)
        progress(f"{kind}: evaluating {t['title']}")
        evaluate(t)
    revised: set[int] = set()
    best = max((EV.score_or(t.get("ev") or {}) for t in trials), default=float("-inf"))
    stale = 0
    for g in range(1, cfg.generations + 1):
        def revisable(t):
            return (t["status"] in ("evaluated", "improved", "not_better") and t["id"] not in revised
                    and (getattr(gen_, "free_revisions", False) or any(w.get("fix") for w in t["ev"].get("weaknesses") or [])
                         or bool(EV.explorations(t["ev"], kind))))
        pool = sorted([t for t in trials if revisable(t)], key=lambda t: -EV.score_or(t["ev"]))
        # polish the leaders, and repair the best candidate that fails a gate (a poor result with a known cause)
        parents = pool[:max(1, cfg.parents - 1)]
        repair = next((t for t in pool if not t["ev"]["gates"]["passed"] and t not in parents), None)
        if repair is not None:
            parents.append(repair)
        parents += [t for t in pool if t not in parents][:cfg.parents - len(parents)]
        if not parents or len(trials) >= cfg.max_trials:
            break
        for p in parents:
            revised.add(p["id"])
            progress(f"{kind}: generation {g}, revising {p['title']}")
            for cand in gen_.revise(conn, cfg, p, p["ev"], trials, risk, cfg.children):
                if len(trials) >= cfg.max_trials:
                    break
                t = ledger.add(kind, g, cand, parent=p)
                trials.append(t)
                evaluate(t)
                if t["status"] == "evaluated" and EV.score_or(t["ev"]) > EV.score_or(p["ev"]) + IMPROVE_MARGIN:
                    ledger.record(t, t["ev"], "improved")
                elif t["status"] == "evaluated":
                    ledger.record(t, t["ev"], "not_better")
        new_best = max((EV.score_or(t.get("ev") or {}) for t in trials), default=float("-inf"))
        stale = stale + 1 if new_best <= best + IMPROVE_MARGIN else 0
        best = max(best, new_best)
        if stale >= PATIENCE:
            progress(f"{kind}: {PATIENCE} generations without a better candidate; stopping")
            break

    ok = [t for t in trials if (t.get("ev") or {}).get("ok")]
    # an unchanged seed was already judged in its own run; only revisions earn a (new) holdout look
    passed = sorted([t for t in ok if t["ev"]["gates"]["passed"] and t["origin"] != "seed"], key=lambda t: -t["ev"]["score"])
    # candidates that trade identically (same per-session returns) are one candidate: keep the first
    distinct, seen_series = [], set()
    for t in passed:
        key = tuple(round(x, 10) for x in t["ev"]["series"])
        if key not in seen_series:
            seen_series.add(key)
            distinct.append(t)
    finalists, families = [], set()
    for t in distinct:  # one per family first, then fill
        if t["family"] not in families and len(finalists) < 2:
            finalists.append(t)
            families.add(t["family"])
    finalists += [t for t in distinct if t not in finalists][:2 - len(finalists)]

    # sealed holdout: each finalist replayed once, nothing changes afterwards
    hctx = EV.Context(cfg.ticker, kind, win["holdout"], peers=peers, closes=closes, folds=2,
                      slippage=cfg.slippage_bps / 10_000, min_trades=max(3, cfg.min_trades // 3), thorough=False)
    n_trials = len(trials) + prior["trials"]  # follow-ups share the holdout with their ancestors: count every trial
    trial_srs = [ST.sharpe(t["ev"]["series"], annualize=False) or 0.0 for t in ok] + list(prior.get("srs") or [])
    by_id = {t["id"]: t for t in trials}
    out_finalists = []
    for t in finalists:
        progress(f"{kind}: holdout for {t['title']}")
        hev = EV.evaluate(conn, t["yaml"], risk, hctx)
        hb = hev.get("base") or {}
        checks = {"profitable": bool(hev.get("ok") and hb.get("sharpe", 0) > 0),
                  "survives_costs": bool(hev.get("ok") and (hev["stress"]["return_pct"] or 0) > 0)}
        if kind == "daily":
            checks["beats_holding"] = bool(hev.get("ok") and hb["sharpe"] > hev["baseline_sharpe"])
        verdict = all(checks.values())
        dsr = ST.deflated_sharpe(t["ev"]["series"], n_trials, trial_srs)
        hold = {"passed": verdict, "checks": checks, **_brief(hev), "sessions": len(win["holdout"]), "dsr": dsr}
        conn.execute("UPDATE discovery_trials SET status=?, holdout=? WHERE id=?",
                     ("holdout_pass" if verdict else "holdout_fail", json.dumps(hold, default=str), t["id"]))
        out_finalists.append({"trial_id": t["id"], "title": t["title"], "family": t["family"], "yaml": t["yaml"], "origin": t["origin"],
                              "discovery": _brief(t["ev"]), "holdout": hold, "lineage": _lineage(t, by_id), "passed": verdict})
    matrix = [list(row) for row in zip(*[t["ev"]["series"] for t in ok])] if len(ok) >= 2 else []
    best_t = max(ok, key=lambda t: t["ev"]["score"], default=None)
    return {
        "kind": kind, "discovery_sessions": [str(win["discovery"][0]), str(win["discovery"][-1]), len(win["discovery"])],
        "holdout_sessions": [str(win["holdout"][0]), str(win["holdout"][-1]), len(win["holdout"])],
        "peers": peers, "trials": len(trials), "prior_trials": prior["trials"], "holdout_prior_looks": prior["looks"],
        "evaluated": len(ok), "invalid": sum(t["status"] == "invalid" for t in trials),
        "duplicates": sum(t["status"] == "duplicate" for t in trials),
        "improved": sum(t["status"] == "improved" for t in trials), "passed_gates": len(passed),
        "pbo": ST.pbo(matrix) if matrix else {"pbo": None},
        "finalists": out_finalists,
        "best": {"trial_id": best_t["id"], "title": best_t["title"], **_brief(best_t["ev"]), "lineage": _lineage(best_t, by_id)} if best_t else None,
        "families": {f: max(EV.score_or(t["ev"]) for t in ok if t["family"] == f) for f in {t["family"] for t in ok}},
    }


def _strings(d: dict) -> None:
    """A model-written `- key: text` list item parses as a mapping; keep assumptions/suggestions plain strings."""
    for k in ("assumptions", "suggestions"):
        if isinstance(d.get(k), list):
            d[k] = [x if isinstance(x, str) else "; ".join(f"{a}: {b}" for a, b in x.items()) if isinstance(x, dict) else str(x)
                    for x in d[k]]


def _annotate(text: str, run_id: int, f: dict, rep: dict) -> str:
    d = yaml.safe_load(text)
    _strings(d)
    disc, hold = f["discovery"], f["holdout"]
    dsr = hold.get("dsr") or {}
    d.setdefault("assumptions", []).append(
        f"discovery run #{run_id}, trial #{f['trial_id']}: one of {rep['trials'] + rep.get('prior_trials', 0)} {rep['kind']} trials"
        + (f" (holdout already scored {rep['holdout_prior_looks']} time(s) in earlier runs)" if rep.get("holdout_prior_looks") else "")
        + f"; discovery Sharpe {disc['sharpe']:.2f} "
        f"over {rep['discovery_sessions'][2]} sessions, sealed holdout Sharpe {hold['sharpe']:.2f} over {rep['holdout_sessions'][2]} sessions"
        + (f", deflated Sharpe probability {dsr['dsr']:.2f}" if dsr.get("dsr") is not None else "")
        + (f", PBO {rep['pbo']['pbo']:.2f}" if (rep.get("pbo") or {}).get("pbo") is not None else ""))
    d.setdefault("suggestions", []).append("paper trade before relying on it: the holdout is short and was scored once")
    return TPL.dump(d)


SAVABLE = ("evaluated", "improved", "not_better", "holdout_pass", "holdout_fail")


def save_trial(conn, tid: int, risk: RiskConfig, actor: str) -> dict:
    """Save any evaluated trial as a new draft strategy file (idempotent: returns the existing one if already saved).

    Survivors keep the file's id; other trials get `<id>_t<trial>` so each saved trial is its own strategy line.
    The draft still goes through the normal lifecycle (backtest, paper, approval) before it can trade."""
    t = db.row(conn, "SELECT * FROM discovery_trials WHERE id=?", (tid,))
    if not t:
        raise ValueError("no such trial")
    if t["strategy_hash"] and db.row(conn, "SELECT hash FROM strategies WHERE hash=?", (t["strategy_hash"],)):
        s = db.row(conn, "SELECT sid, path FROM strategies WHERE hash=?", (t["strategy_hash"],))
        return {"trial_id": tid, "hash": t["strategy_hash"], "id": s["sid"], "path": s["path"], "existing": True}
    if t["status"] not in SAVABLE:
        raise ValueError(f"trial #{tid} is {t['status']}: only evaluated trials can be saved")
    ev = json.loads(t["evaluation"] or "{}")
    hold = json.loads(t["holdout"]) if t["holdout"] else None
    d = yaml.safe_load(t["yaml"])
    _strings(d)
    if t["status"] != "holdout_pass":
        d["id"] = f"{d['id']}_t{tid}"[:60]
    b = ev.get("base") or {}
    note = f"discovery run #{t['run_id']}, trial #{tid} ({t['status'].replace('_', ' ')}), saved by {actor}"
    if b:
        note += f"; discovery Sharpe {b.get('sharpe', 0):.2f} over {b.get('trades', 0)} trades"
    failed = [k for k, v in (ev.get("gates") or {}).items() if k not in ("passed", "valid") and v is False]
    if failed:
        note += f"; failed gates: {', '.join(failed)}"
    if hold:
        note += f"; sealed holdout Sharpe {hold.get('sharpe') or 0:.2f} ({'passed' if hold.get('passed') else 'failed'})"
    else:
        note += "; never scored on the holdout"
    d.setdefault("assumptions", []).append(note)
    d.setdefault("suggestions", []).append("backtest and paper trade before relying on it")
    text = TPL.dump(d)
    v = validate(text, risk)
    if not v.ok:
        raise ValueError("the trial's file does not validate: " + "; ".join(v.errors[:3]))
    path = registry.write_new_version(d["id"], text)
    registry.sync(conn, risk)
    h = file_hash(path.read_text())
    conn.execute("UPDATE discovery_trials SET strategy_hash=? WHERE id=?", (h, tid))
    audit.log(conn, "discovery.trial_saved", actor, {"trial_id": tid, "run_id": t["run_id"], "hash": h, "path": path.name})
    return {"trial_id": tid, "hash": h, "id": d["id"], "path": path.name, "existing": False}


def run(conn, cfg: Config, actor: str, generator=None, progress: Callable[[str], None] | None = None,
        run_id: int | None = None, risk: RiskConfig | None = None) -> dict:
    risk = risk or load_risk(conn=conn)
    gen_ = generator or OfflineGenerator()
    if run_id is None:
        run_id = conn.insert("INSERT INTO discovery_runs(created_at, created_by, ticker, config, status) VALUES (?,?,?,?,?)",
                             (time.time(), actor, cfg.ticker, cfg.model_dump_json(), "running"))
    steps = [0]

    def note(stage: str):
        steps[0] += 1
        conn.execute("UPDATE discovery_runs SET stage=?, progress=? WHERE id=?", (stage, min(0.95, steps[0] / 80), run_id))
        if progress:
            progress(stage)

    try:
        note("loading sessions and seeds")
        seeds, parent = resolve_seeds(conn, cfg, risk)
        if seeds:
            refs = [r for sd in seeds for r in (yaml.safe_load(sd["yaml"]).get("references") or [])]
            cfg = cfg.model_copy(update={"kinds": sorted({sd["kind"] for sd in seeds}), "parent_run_id": parent,
                                         "peers": list(dict.fromkeys([*refs, *cfg.peers]))[:4]})
            conn.execute("UPDATE discovery_runs SET config=? WHERE id=?", (cfg.model_dump_json(), run_id))
        chain = ancestry(conn, cfg.parent_run_id)
        wins = sessions(conn, cfg)
        if "daily" in wins and risk.portfolio is None:
            wins["daily"] = {"error": "portfolio strategies are disabled on the Risk page, so daily strategies can't be built"}
        runnable = {k: w for k, w in wins.items() if "error" not in w}
        if not runnable:
            raise ValueError("; ".join(w["error"] for w in wins.values()))
        note(f"profiling {cfg.ticker} on its discovery sessions")
        # the profile only sees sessions before the earliest holdout of any kind
        prof_end = min(w["discovery"][-1] for w in runnable.values())
        peers_all = sorted({p for k, w in runnable.items() for p in _available_peers(conn, cfg, k, w["discovery"])})
        prof = {"daily": PF.daily_profile(conn, cfg.ticker, peers_all, (runnable.get("daily") or runnable.get("intraday"))["discovery"][0], prof_end),
                "intraday": PF.intraday_profile(conn, cfg.ticker, runnable["intraday"]["discovery"]) if "intraday" in runnable else None}
        conn.execute("UPDATE discovery_runs SET profile=? WHERE id=?", (json.dumps(prof, default=str), run_id))
        ledger = Ledger(conn, run_id)
        report = {"run_id": run_id, "ticker": cfg.ticker, "generator": gen_.name, "kinds": {}, "skipped": {},
                  "parent_run_id": cfg.parent_run_id, "seeds": [sd["change"] for sd in seeds]}
        for kind in cfg.kinds:
            if kind not in runnable:
                report["skipped"][kind] = wins[kind]["error"]
                continue
            w = runnable[kind]
            peers = _available_peers(conn, cfg, kind, w["discovery"])
            end = w["holdout"][-1].isoformat()
            closes = {s: [(r["day"], r["c"]) for r in PF.daily_rows(conn, s, date.fromisoformat(end))] for s in [cfg.ticker, *peers]}
            prior = {"trials": 0, "looks": 0, "srs": []}
            if chain:
                marks = ",".join("?" * len(chain))
                prior = db.row(conn, f"SELECT COUNT(*) trials, COUNT(holdout) looks FROM discovery_trials WHERE kind=? AND run_id IN ({marks})",
                               (kind, *chain))
                # trials recorded before per-trial Sharpes were stored are skipped rather than counted as zero
                prior["srs"] = [sr for r in db.rows(
                    conn, f"SELECT evaluation FROM discovery_trials WHERE kind=? AND status NOT IN ('invalid','duplicate','pending') AND run_id IN ({marks})",
                    (kind, *chain)) if r["evaluation"] and (sr := json.loads(r["evaluation"]).get("sr")) is not None]
            report["kinds"][kind] = run_kind(conn, cfg, kind, w, prof, closes, peers, risk, gen_, ledger, note, seeds, prior)
        drafts = []
        if cfg.save_drafts:
            for kind, rep in report["kinds"].items():
                for f in rep["finalists"]:
                    if not f["passed"] or f.get("origin") == "seed":
                        continue  # failed, or an unchanged seed that already exists
                    text = _annotate(f["yaml"], run_id, f, rep)
                    if not validate(text, risk).ok:
                        continue
                    sid = yaml.safe_load(text)["id"]
                    path = registry.write_new_version(sid, text)
                    registry.sync(conn, risk)
                    h = file_hash(path.read_text())
                    conn.execute("UPDATE discovery_trials SET strategy_hash=? WHERE id=?", (h, f["trial_id"]))
                    f["strategy_hash"], f["path"] = h, str(path.name)
                    drafts.append({"kind": kind, "id": sid, "hash": h, "path": path.name})
        if cfg.save_drafts and cfg.save_all:
            for r in db.rows(conn, "SELECT id, kind FROM discovery_trials WHERE run_id=? AND strategy_hash IS NULL AND status IN"
                                   f" ({','.join('?' * len(SAVABLE))}) ORDER BY id", (run_id, *SAVABLE)):
                try:
                    sv = save_trial(conn, r["id"], risk, actor)
                    drafts.append({"kind": r["kind"], "id": sv["id"], "hash": sv["hash"], "path": sv["path"]})
                except (ValueError, FileExistsError):
                    continue  # e.g. a trial whose file no longer validates against the current risk controls
        report["drafts"] = drafts
        report["profile"] = prof
        if hasattr(gen_, "calls"):
            report["model_calls"], report["model_errors"] = gen_.calls, gen_.errors
        conn.execute("UPDATE discovery_runs SET status='completed', stage='done', progress=1, report=?, finished_at=? WHERE id=?",
                     (json.dumps(report, default=str), time.time(), run_id))
        audit.log(conn, "discovery.completed", actor, {"run_id": run_id, "ticker": cfg.ticker, "drafts": drafts,
                                                       "trials": {k: r["trials"] for k, r in report["kinds"].items()}})
        return report
    except Exception as e:
        conn.execute("UPDATE discovery_runs SET status='failed', error=?, finished_at=? WHERE id=?", (str(e)[:2000], time.time(), run_id))
        audit.log(conn, "discovery.failed", actor, {"run_id": run_id, "error": str(e)[:500]})
        raise


def summary(report: dict) -> str:
    """Plain-text report for the CLI."""
    lines = [f"Discovery run #{report['run_id']} for {report['ticker']} (generator {report['generator']})"]
    for kind, why in report.get("skipped", {}).items():
        lines.append(f"  {kind}: skipped: {why}")
    for kind, r in report["kinds"].items():
        d, h = r["discovery_sessions"], r["holdout_sessions"]
        lines.append(f"\n{kind.upper()}: {r['trials']} trials ({r['evaluated']} evaluated, {r['invalid']} invalid, {r['duplicates']} duplicates, "
                     f"{r['improved']} improving revisions); discovery {d[0]}..{d[1]} ({d[2]}), sealed holdout {h[0]}..{h[1]} ({h[2]})")
        if r.get("holdout_prior_looks"):
            lines.append(f"  follow-up: {r['prior_trials']} earlier trials share this holdout, which was already scored "
                         f"{r['holdout_prior_looks']} time(s); the deflated Sharpe counts them all")
        lines.append(f"  peers: {', '.join(r['peers']) or 'none'}; passed discovery gates: {r['passed_gates']}; "
                     f"PBO {r['pbo']['pbo']:.2f}" if r["pbo"].get("pbo") is not None else
                     f"  peers: {', '.join(r['peers']) or 'none'}; passed discovery gates: {r['passed_gates']}; PBO n/a")
        if r["best"]:
            b = r["best"]
            lines.append(f"  best on discovery: #{b['trial_id']} {b['title']}: score {b['score']:.2f}, Sharpe {b['sharpe']:.2f}, "
                         f"trades {b['trades']}, weaknesses {', '.join(b['weaknesses']) or 'none'}")
        for f in r["finalists"]:
            ho = f["holdout"]
            dsr = (ho.get("dsr") or {}).get("dsr")
            lines.append(f"  finalist #{f['trial_id']} {f['title']}: discovery Sharpe {f['discovery']['sharpe']:.2f} -> holdout "
                         + (f"Sharpe {ho['sharpe']:.2f}, {ho['trades']} trades" if ho.get("ok", True) and "sharpe" in ho else "n/a")
                         + (f", DSR {dsr:.2f}" if dsr is not None else "") + f": {'PASS' if f['passed'] else 'FAIL'}"
                         + ("" if f["passed"] else f" ({', '.join(k for k, v in (ho.get('checks') or {}).items() if not v)})"))
            for step in f["lineage"][1:]:
                lines.append(f"      - {step}")
        if not r["finalists"]:
            lines.append("  no candidate passed every gate on discovery data; nothing was scored on the holdout")
    if report.get("model_errors"):
        lines.append(f"\nModel calls: {report['model_calls']}; {len(report['model_errors'])} failed and fell back to templates: "
                     + "; ".join(report["model_errors"][:3]))
    for dft in report.get("drafts", []):
        lines.append(f"\nSaved draft {dft['path']} ({dft['kind']}, sha256 {dft['hash'][:12]}): review it on the Strategies page.")
    return "\n".join(lines)

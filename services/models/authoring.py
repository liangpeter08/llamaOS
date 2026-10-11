"""Authoring lane (offline, model-drafted, human-approved): strategy chat and the compiler."""
from __future__ import annotations

import json
import re
import time

from common import audit, db, registry
from common.config import PROMPTS_DIR, STRATEGIES_DIR, load_risk
from dsl.explain import back_translate
from dsl.validator import validate

from . import llm
from .quicklayer import _model_id, prompt

COMPILE_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "yaml": {"type": "string"},
        "notes": {"type": "string"},
    },
    "required": ["id", "yaml", "notes"],
    "additionalProperties": False,
}


class Offline(RuntimeError):
    pass


def _need_llm():
    if not llm.available():
        raise Offline("Claude isn't configured: set ANTHROPIC_API_KEY for the models service to enable this.")


# ---------------------------------------------------------------- chat
def strategy_context(conn, h: str) -> str:
    risk = load_risk()
    row = db.row(conn, "SELECT * FROM strategies WHERE hash=?", (h,))
    if not row:
        raise KeyError(h)
    text = registry.text_of(conn, h) or ""
    v = validate(text, risk)
    bt = json.loads(row["backtest"] or "null")
    if bt:
        bt = {k: bt[k] for k in ("metrics", "per_day", "rejected_by_guard", "signal_note", "days") if k in bt} | {
            "sample_trades": bt.get("trades", [])[:15]}
    intents = db.rows(conn, "SELECT id, ts, symbol, qty, entry, stop, target, status, decision, reason FROM intents"
                            " WHERE strategy_hash=? ORDER BY id DESC LIMIT 10", (h,))
    trades = db.rows(conn, "SELECT id, symbol, qty, entry_price, exit_price, exit_kind, pnl, r_multiple FROM trades"
                           " WHERE strategy_hash=? ORDER BY id DESC LIMIT 20", (h,))
    parts = [
        f"<strategy_file status=\"{row['status']}\" mode=\"{row['mode']}\" sha256=\"{h}\">\n{text}\n</strategy_file>",
        f"<back_translation>\n{back_translate(v.strategy) if v.strategy else 'n/a (invalid)'}\n</back_translation>",
        f"<validation>{json.dumps(v.as_dict())}</validation>",
        f"<backtest_report>{json.dumps(bt, default=str) if bt else 'none yet'}</backtest_report>",
        f"<decision_records>{json.dumps(intents, default=str)}</decision_records>",
        f"<live_and_paper_trades>{json.dumps(trades, default=str)}</live_and_paper_trades>",
    ]
    if v.strategy is not None and v.strategy.kind == "portfolio":
        from common import sleeves
        sl = sleeves.load_state(conn, h)
        hold, realized, _ = sleeves.holdings(conn, h)
        orders = db.rows(conn, "SELECT id, day, symbol, side, qty, ref_price, reason, status, filled_qty, avg_price"
                               " FROM portfolio_orders WHERE strategy_hash=? ORDER BY id DESC LIMIT 30", (h,))
        sleeve = {"state": sl.to_json() if sl else None, "realized": realized,
                  "holdings": {k: vars(x) for k, x in hold.items() if x.qty}, "recent_orders": orders}
        parts.append(f"<portfolio_sleeve>{json.dumps(sleeve, default=str)}</portfolio_sleeve>")
    return "\n".join(parts)


def trade_context(conn, trade_id: int) -> str:
    t = db.row(conn, "SELECT * FROM trades WHERE id=?", (trade_id,))
    if not t:
        raise KeyError(trade_id)
    it = db.row(conn, "SELECT * FROM intents WHERE id=?", (t["intent_id"],)) if t["intent_id"] else None
    fills = db.rows(conn, "SELECT * FROM fills WHERE symbol=? AND (intent_id=? OR intent_id IS NULL) ORDER BY ts", (t["symbol"], t["intent_id"]))
    return (f"<trade>{json.dumps(t, default=str)}</trade>\n<intent_decision_record>{json.dumps(it, default=str)}</intent_decision_record>\n"
            f"<fills>{json.dumps(fills, default=str)}</fills>")


def chat(conn, thread: str, message: str, actor: str) -> dict:
    """thread: 'strategy:<hash>:<n>' or 'trade:<id>:<n>'. Each thread is a fresh Claude conversation."""
    _need_llm()
    kind, ref, _ = thread.split(":", 2)
    history = db.rows(conn, "SELECT role, content FROM chats WHERE thread=? ORDER BY id", (thread,))
    if not history:
        ctx = strategy_context(conn, ref) if kind == "strategy" else trade_context(conn, int(ref))
        first = f"Context for this conversation:\n{ctx}\n\nMy question: {message}"
        msgs = [{"role": "user", "content": first}]
        stored_user = first
    else:
        msgs = [{"role": r["role"], "content": r["content"]} for r in history] + [{"role": "user", "content": message}]
        stored_user = message
    model = _model_id("claude_opus")
    text, _, _ = llm.call(conn, model=model, task="chat", system=prompt("chat@v1"), messages=msgs,
                          max_tokens=16000, effort="medium", cache=True, timeout=180)
    now = time.time()
    conn.execute("INSERT INTO chats(ts, thread, role, content, model) VALUES (?,?,?,?,?)", (now, thread, "user", stored_user, actor))
    conn.execute("INSERT INTO chats(ts, thread, role, content, model) VALUES (?,?,?,?,?)", (now, thread, "assistant", text, model))
    audit.log(conn, "chat", actor, {"thread": thread, "question": message[:2000], "answer": text[:4000], "model": model})
    diff = re.search(r"```diff\n(.*?)```", text, re.S)
    return {"answer": text, "model": model, "has_diff": bool(diff)}


# ---------------------------------------------------------------- compiler
def named_draft(text: str, name: str) -> str:
    """Apply a caller's name deterministically; blank keeps the model's identity."""
    if not name.strip():
        return text
    sid = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")[:60]
    if not sid or not sid[0].isalpha():
        sid = "strategy_" + sid
    if len(sid) < 2:
        sid += "_strategy"
    sid = sid[:63]
    import yaml
    try:
        draft = yaml.safe_load(text)
    except yaml.YAMLError:
        return text
    if not isinstance(draft, dict):
        return text  # validator reports malformed drafts
    draft['id'] = sid
    updated, count = re.subn(r"(?m)^id:.*$", f"id: {sid}", text, count=1)
    return updated if count else yaml.safe_dump(draft, sort_keys=False)


def compiler_reference(risk) -> str:
    """The DSL reference (compiler@v2) filled in with the live risk controls. Shared by the compiler and the design chat."""
    example = (STRATEGIES_DIR / "orb_news_long.v1.yaml").read_text() if (STRATEGIES_DIR / "orb_news_long.v1.yaml").exists() else ""
    pex = PROMPTS_DIR / "examples" / "portfolio.yaml"
    pr = risk.portfolio
    if pr:
        status = "Portfolio files are enabled on this account."
        limits = (f"all sleeves together at most {pr.max_allocation:.0%} of equity, one symbol at most {pr.max_position:.0%} of equity, "
                  f"at most {pr.max_symbols} symbols, hold_sessions at most {pr.max_hold_sessions}, decide_at no later than "
                  f"{pr.decide_at:%H:%M} ET, sleeve drawdown stop {pr.max_drawdown:.0%} (limits.max_drawdown may only tighten it), long only")
    else:
        status = ("Portfolio files are DISABLED on this account (turned off on the dashboard Risk page): compile the closest intraday "
                  "file instead and mark every multi-day part NOT SUPPORTED, saying the owner can enable portfolio strategies on the Risk page.")
        limits = "disabled"
    v2 = ""
    v2_example = STRATEGIES_DIR / "big_tech_software_tilt_hold.v2.yaml"
    if pr and v2_example.exists():  # DSL 0.2 overlays, kept outside the versioned compiler@v2 text
        v2 = "\n\n" + prompt("portfolio_v2@v1").replace("{portfolio_v2_example}", v2_example.read_text())
    features = "\n\n" + prompt("features@v1")  # builtins@v2 vocabulary, kept outside the versioned compiler@v2 text
    return ((prompt("compiler@v2") + v2 + features)
              .replace("{portfolio_status}", status).replace("{portfolio_limits}", limits)
              .replace("{portfolio_example}", pex.read_text() if pex.exists() else "")
              .replace("{today}", time.strftime("%Y-%m-%d"))
              .replace("{max_risk}", f"{risk.max_risk:.2%}").replace("{max_position}", f"{risk.max_position:.0%}")
              .replace("{max_trades}", str(risk.max_trades_per_day))
              .replace("{entries}", f"{risk.entries[0]:%H:%M}-{risk.entries[1]:%H:%M} ET")
              .replace("{flatten_by}", f"{risk.flatten_by:%H:%M} ET").replace("{long_only}", str(not risk.allow_short).lower())
              .replace("{example}", example))


def compile_text(conn, request: str, actor: str, name: str = "") -> dict:
    """English -> DSL draft via Claude Opus, validated; one repair round on validator errors."""
    _need_llm()
    risk = load_risk()
    model = _model_id("claude_opus")
    system = compiler_reference(risk)
    msgs = [{"role": "user", "content": f"Compiler model id: {model}\n\n<request>\n{request}\n</request>"}]
    attempts = []
    for _ in range(2):
        _, parsed, resp = llm.call(conn, model=model, task="compile", system=system, messages=msgs,
                                   max_tokens=16000, effort="high", schema=COMPILE_SCHEMA, timeout=240)
        text = named_draft(parsed["yaml"].strip() + "\n", name)
        v = validate(text, risk)
        attempts.append({"ok": v.ok, "errors": v.errors})
        if v.ok:
            break
        msgs += [{"role": "assistant", "content": json.dumps(parsed)},
                 {"role": "user", "content": "The validator rejected it:\n- " + "\n- ".join(v.errors) + "\nReturn a corrected file."}]
    sid = re.sub(r"[^a-z0-9_]", "_", (v.strategy.id if v.ok else parsed["id"]).lower())[:60] or "draft"
    result = {"ok": v.ok, "errors": v.errors, "warnings": v.warnings, "notes": parsed["notes"], "attempts": attempts}
    if v.ok:
        path = registry.write_new_version(sid, text)
        registry.sync(conn, risk)
        result["path"] = str(path.name)
        result["hash"] = validate(path.read_text(), risk).hash
        if name.strip():
            conn.execute("UPDATE strategies SET display_name=? WHERE hash=?", (name.strip(), result["hash"]))
        result["name"] = name.strip() or v.strategy.id
    else:
        result["yaml"] = text
    audit.log(conn, "strategy.compiled", actor, {"request": request, "model": model, **{k: v for k, v in result.items() if k != "yaml"}})
    return result


def apply_diff(conn, h: str, diff_text: str, actor: str) -> dict:
    """Accept a chat-proposed unified diff: creates a NEW draft version; the original is untouched."""
    risk = load_risk()
    old = registry.text_of(conn, h)
    if old is None:
        raise KeyError(h)
    new = _patch(old, diff_text)
    sid = db.row(conn, "SELECT sid FROM strategies WHERE hash=?", (h,))["sid"]
    v = validate(re.sub(r"(?m)^version:\s*\d+\s*$", f"version: {registry.next_version(sid)}", new, count=1), risk)
    if not v.ok:
        return {"ok": False, "errors": v.errors}
    path = registry.write_new_version(sid, new)
    registry.sync(conn, risk)
    audit.log(conn, "strategy.diff_applied", actor, {"from": h, "to": path.name})
    return {"ok": True, "path": path.name}


def _patch(text: str, diff: str) -> str:
    """Minimal unified-diff applier (context must match exactly)."""
    lines = text.splitlines()
    out, i = [], 0
    hunks = re.split(r"(?m)^@@.*@@.*$", diff)[1:]
    if not hunks:
        raise ValueError("no hunks in diff")
    for h in hunks:
        body = [ln for ln in h.strip("\n").splitlines() if not ln.startswith(("---", "+++"))]
        old_seq = [ln[1:] for ln in body if ln.startswith((" ", "-"))]
        new_seq = [ln[1:] for ln in body if ln.startswith((" ", "+"))]
        for k in range(i, len(lines) - len(old_seq) + 1):
            if lines[k:k + len(old_seq)] == old_seq:
                out += lines[i:k] + new_seq
                i = k + len(old_seq)
                break
        else:
            raise ValueError("diff context does not match the strategy file")
    out += lines[i:]
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- design chat (architect)
YAML_BLOCK = re.compile(r"```ya?ml[^\n]*\n(.*?)```", re.S)
DESIGN_THREAD = re.compile(r"^design:[A-Za-z0-9]{6,32}$")
CONTEXT_MARK = "\n\n<my_idea>\n"


def design_context(conn, risk) -> str:
    """What the architect needs to know about this account, gathered once when a design thread starts."""
    from common import funds, monitoring

    rows = db.rows(conn, "SELECT * FROM strategies ORDER BY sid, version DESC")
    seen, strategies = set(), []
    for r in rows:
        if r["sid"] in seen and r["status"] not in ("paper", "live"):
            continue
        seen.add(r["sid"])
        text = registry.text_of(conn, r["hash"]) or ""
        v = validate(text, risk) if text else None
        bt = json.loads(r["backtest"] or "null")
        m = (bt or {}).get("metrics") or {}
        paper = db.row(conn, "SELECT COUNT(*) n, COALESCE(SUM(pnl),0) pnl FROM trades WHERE strategy_hash=?", (r["hash"],))
        strategies.append({
            "id": r["sid"], "version": r["version"], "status": r["status"], "name": r.get("display_name"),
            "kind": v.strategy.kind if v and v.strategy else ("portfolio" if "kind: portfolio" in text else "intraday"),
            "valid": bool(v and v.ok), "rules": (back_translate(v.strategy)[:900] if v and v.strategy else None),
            "backtest": {k: m.get(k) for k in ("trades", "win_rate", "avg_r", "total_pnl", "return_pct", "max_drawdown",
                                               "sharpe_daily", "benchmark_return_pct") if k in m} | {"sessions": len(bt["days"])} if bt else None,
            "paper_trades": paper["n"], "paper_pnl": round(paper["pnl"], 2),
        })
    daily = db.rows(conn, "SELECT symbol, COUNT(*) n, MIN(day) first, MAX(day) last FROM daily GROUP BY symbol ORDER BY symbol")
    intraday = db.rows(conn, "SELECT symbol, COUNT(*) bars, MIN(ts) first, MAX(ts) last FROM bars GROUP BY symbol ORDER BY symbol")
    for r in intraday:
        r["first"], r["last"] = time.strftime("%Y-%m-%d", time.gmtime(r["first"])), time.strftime("%Y-%m-%d", time.gmtime(r["last"]))
        r["sessions_approx"] = round(r.pop("bars") / 390)
    sig = db.row(conn, "SELECT COUNT(*) n, MIN(ts) first FROM signals")
    ideas = db.rows(conn, "SELECT id, title, status, sample_size, anecdotal, holdout FROM ideas ORDER BY id DESC LIMIT 12")
    theses = db.rows(conn, "SELECT text, confidence, expires FROM theses WHERE active=1 ORDER BY id DESC LIMIT 10")
    try:
        acct = funds.account(conn, risk) if risk.funds else None
        money = ({"equity": round(acct.equity), "cash_reserve": f"{risk.funds.cash_reserve:.0%}",
                  "unallocated_for_new_strategies": f"{funds.unallocated(conn, risk):.1%} of equity",
                  "default_allocation": f"{risk.funds.default_allocation:.0%}",
                  "max_allocation_per_strategy": f"{risk.funds.max_strategy_allocation:.0%}"} if acct else None)
    except Exception:  # noqa: BLE001 - context is best effort; the conversation still works without it
        money = None
    ctx = {
        "today": time.strftime("%Y-%m-%d (%A)"),
        "monitored_watchlist": monitoring.enabled(conn),
        "daily_bars": daily, "intraday_1min_bars": intraday,
        "recorded_news_signals": {"count": sig["n"], "since": time.strftime("%Y-%m-%d", time.gmtime(sig["first"])) if sig["first"] else None},
        "funds": money,
        "existing_strategies": strategies,
        "idea_lab": ideas, "trader_theses": theses,
    }
    return json.dumps(ctx, default=str, indent=1)


def check_draft(text: str, risk) -> dict | None:
    """The last ```yaml block in a message, validated against the live risk controls."""
    blocks = YAML_BLOCK.findall(text or "")
    if not blocks:
        return None
    y = blocks[-1].strip() + "\n"
    v = validate(y, risk)
    s = v.strategy
    return {"yaml": y, "ok": v.ok, "errors": v.errors, "warnings": v.warnings, "hash": v.hash,
            "id": s.id if s else None, "kind": s.kind if s else None, "back_translation": back_translate(s) if s else None}


def design_messages(conn, thread: str) -> list[dict]:
    return db.rows(conn, "SELECT id, ts, role, content, model FROM chats WHERE thread=? ORDER BY id", (thread,))


IMPROVE_TAG = re.compile(r'<strategy_to_improve sid="([^"]+)" version="(\d+)" sha256="([0-9a-f]{64})"')


def improve_context(conn, h: str) -> str:
    """The strategy a design thread starts from ("duplicate and improve"), with its results and the task."""
    row = db.row(conn, "SELECT sid, version FROM strategies WHERE hash=?", (h,))
    if not row:
        raise KeyError(h)
    return (f"\n\n<strategy_to_improve sid=\"{row['sid']}\" version=\"{row['version']}\" sha256=\"{h}\">\n{strategy_context(conn, h)}\n</strategy_to_improve>\n\n"
            f"<task>The trader duplicated the strategy above and wants a better copy of it. Their description of what is wrong "
            f"follows in <my_idea>. Diagnose the cause from the file, the backtest report and the trades (cite numbers), say "
            f"what you would change and why, keeping what works. Unless the trader's answer is needed first, draft the complete "
            f"improved file in the same turn. The copy is a NEW strategy: give it a new id (for example {row['sid']}_improved, "
            f"or a descriptive one), version 1, keep `source.text` of the original and append the trader's complaint to it, set "
            f"`source.prompt` to architect@v1, and list every change you made as an assumption starting with \"CHANGED:\".</task>")


def design(conn, thread: str, message: str, actor: str, source: str | None = None) -> dict:
    """One turn of the design conversation with Claude Opus; validates any draft, with one automatic repair round.

    source: a strategy hash to duplicate and improve; only used on the first turn of a thread.
    """
    _need_llm()
    if not DESIGN_THREAD.match(thread):
        raise KeyError(thread)
    risk = load_risk()
    model = _model_id("claude_opus")
    system = prompt("architect@v1") + f"\n\nYour model id is {model}.\n\n# DSL reference\n\n" + compiler_reference(risk)
    history = design_messages(conn, thread)
    if not history:
        extra = improve_context(conn, source) if source else ""
        message = f"Context about this account (JSON):\n{design_context(conn, risk)}{extra}{CONTEXT_MARK}{message}\n</my_idea>"
    msgs = [{"role": r["role"], "content": r["content"]} for r in history] + [{"role": "user", "content": message}]

    def turn(msgs):
        text, _, _ = llm.call(conn, model=model, task="design", system=system, messages=msgs, max_tokens=16000,
                              max_output=16000, effort="high", cache=True, timeout=300, max_context_chars=400_000)
        return text

    text = turn(msgs)
    now = time.time()
    conn.execute("INSERT INTO chats(ts, thread, role, content, model) VALUES (?,?,?,?,?)", (now, thread, "user", message, actor))
    conn.execute("INSERT INTO chats(ts, thread, role, content, model) VALUES (?,?,?,?,?)", (now, thread, "assistant", text, model))
    draft = check_draft(text, risk)
    if draft and not draft["ok"]:
        feedback = "[validator] The draft failed validation:\n- " + "\n- ".join(draft["errors"]) + "\nReturn the complete corrected file."
        msgs += [{"role": "assistant", "content": text}, {"role": "user", "content": feedback}]
        fixed = turn(msgs)
        conn.execute("INSERT INTO chats(ts, thread, role, content, model) VALUES (?,?,?,?,?)", (time.time(), thread, "user", feedback, "validator"))
        conn.execute("INSERT INTO chats(ts, thread, role, content, model) VALUES (?,?,?,?,?)", (time.time(), thread, "assistant", fixed, model))
        draft = check_draft(fixed, risk) or draft
    audit.log(conn, "strategy.design_turn", actor, {"thread": thread, "message": message.split(CONTEXT_MARK)[-1][:2000],
                                                     "draft_ok": draft["ok"] if draft else None, "model": model,
                                                     **({"improving": source} if source and not history else {})})
    return {"ok": True, "model": model, "draft": draft}


# ---------------------------------------------------------------- review panel (four Opus agents)
PANEL_ROLES = {
    "analyst": ("Analyst", "market key points",
                "Your role: ANALYST. Gather the market key points that matter for this strategy: trend and volatility of its symbols, "
                "how often the conditions its rules react to actually occurred in the stored history (use the threshold counts), "
                "the regime the stored period covers and how it might differ from the coming weeks, and what recent headlines and recorded "
                "news signals say about the symbols. Flag symbols with thin or missing data."),
    "researcher": ("Researcher", "risk/reward",
                   "Your role: RESEARCHER. Evaluate risk versus reward: where an edge could come from and whether the evidence supports it, "
                   "expected value per trade or per period versus a simple benchmark (buy-and-hold when given), how many independent "
                   "observations the backtest really has, overfitting and look-ahead risk, which parameters the result depends on most, and "
                   "what would falsify the idea. If no backtest was run, say what it should be checked against."),
    "trader": ("Trader", "trading strategy",
               "Your role: TRADER. Judge the strategy as someone who has to execute it: entry and exit logic, timing (decision times, fills, "
               "slippage, liquidity), how the rules interact over a sequence of days or bars (for example trimming then adding, re-entry "
               "loops, conflicting rules), sizing, and what the trader will actually experience. Propose the concrete DSL changes you would "
               "make, with exact values."),
    "risk": ("Risk Team", "risk exposure",
             "Your role: RISK TEAM. Manage exposure: capital at risk at the peak (with every add filled), concentration by symbol and group, "
             "how correlated the symbols are and what the basket's worst day was, the strategy's fund allocation versus what its rules can "
             "use, drawdown and loss stops, overlap with other running strategies and symbols, the kill switch and what happens on a gap "
             "or a stale feed. Say which limits you would tighten and to what."),
}
PANEL_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["approve", "approve_with_changes", "rework"]},
        "score": {"type": "integer"},
        "summary": {"type": "string"},
        "key_points": {"type": "array", "items": {"type": "string"}},
        "positives": {"type": "array", "items": {"type": "string"}},
        "concerns": {"type": "array", "items": {
            "type": "object", "properties": {"severity": {"type": "string", "enum": ["high", "medium", "low"]}, "text": {"type": "string"}},
            "required": ["severity", "text"], "additionalProperties": False}},
        "suggestions": {"type": "array", "items": {
            "type": "object", "properties": {"text": {"type": "string"}, "change": {"type": "string"}},
            "required": ["text", "change"], "additionalProperties": False}},
    },
    "required": ["verdict", "score", "summary", "key_points", "positives", "concerns", "suggestions"],
    "additionalProperties": False,
}


def panel_evidence(conn, s, risk) -> dict:
    """Role evidence computed by code: per-symbol stats, correlations, basket worst day, headlines, signals, exposure."""
    from common import funds, monitoring
    from core import review_stats as RS

    symbols = list(s.symbols) if s.kind == "portfolio" else monitoring.enabled(conn)
    series, days = {}, {}
    for sym in symbols:
        rows = db.rows(conn, "SELECT day, c FROM daily WHERE symbol=? ORDER BY day", (sym,))
        series[sym], days[sym] = [r["c"] for r in rows], [r["day"] for r in rows]
    stats = {sym: RS.symbol_stats(series[sym], days[sym]) for sym in symbols}
    weights = s.weights if s.kind == "portfolio" else {sym: 1 / max(len(symbols), 1) for sym in symbols}
    since = int(time.time()) - 10 * 86400
    news = {}
    for sym in symbols:
        rows = db.rows(conn, "SELECT ts, headline FROM news WHERE symbols LIKE ? AND ts>=? ORDER BY ts DESC LIMIT 5", (f'%"{sym}"%', since))
        if rows:
            news[sym] = [f"{time.strftime('%m-%d', time.gmtime(r['ts']))} {r['headline'][:160]}" for r in rows]
    sigs = db.rows(conn, "SELECT symbol, name, value, COUNT(*) n FROM signals WHERE ts>=? AND name IN ('news.catalyst','news.red_flag')"
                         " GROUP BY symbol, name, value ORDER BY n DESC LIMIT 40", (since,))
    exposure = None
    if s.kind == "portfolio":
        top = max(s.weights, key=s.weights.get)
        exposure = {"capital": s.capital, "max_holding": s.max_holding, "peak_share_of_equity": s.capital * s.max_holding,
                    "largest_symbol_peak": {top: s.capital * s.weights[top] * s.max_holding},
                    "groups": {g: {"weight": w, "symbols": syms} for g, (w, syms) in s.groups.items()}}
    running = []
    for r, other in [*registry.runnable(conn, risk), *registry.runnable(conn, risk, kind="portfolio")]:
        syms = list(other.symbols) if other.kind == "portfolio" else ["watchlist"]
        running.append({"id": other.id, "kind": other.kind, "symbols": syms,
                        "overlap": sorted(set(syms) & set(symbols)) if other.kind == "portfolio" else None})
    try:
        fund = funds.account(conn, risk) if risk.funds else None
        fund_view = {"equity": round(fund.equity), "deployed": round(fund.deployed), "unallocated": round(funds.unallocated(conn, risk), 4),
                     "cash_reserve": risk.funds.cash_reserve, "strategy_drawdown_cap": risk.funds.strategy_max_drawdown} if fund else None
    except Exception:  # noqa: BLE001 - evidence is best effort
        fund_view = None
    facts = [  # how the system behaves, so reviewers don't have to guess
        "Portfolio sleeves are decided once a day at decide_at (15:45 ET) with day market orders; nothing reacts intraday or to overnight gaps until the next decision.",
        "A symbol with no fresh quote at decide_at is skipped that day (fails closed); the backtest uses daily closes for decisions and fills.",
        "max_daily_loss (kill switch) measures intraday strategies only: sleeve P&L is excluded. Sleeves are protected by their own drawdown stop (risk control portfolio.max_drawdown or limits.max_drawdown) and by the strategy's fund drawdown stop.",
        "The kill switch, whatever triggers it, sells everything including sleeves. The watchdog's stale-feed and 15:55 flatten checks ignore sleeve positions.",
        "Promoting a strategy to paper turns on market-data monitoring (1-minute bars, news) for all of its symbols; it also gets a fund allocation, and buys beyond it are refused.",
        "While a sleeve holds a symbol, intraday strategies can't trade it (Alpaca nets one position per symbol).",
    ] if s.kind == "portfolio" else [
        "Intraday strategies decide at each 1-minute bar close, enter with a bracket (stop and target at the broker) and are flat by the flatten time.",
        "Entries are sized by risk per trade, capped by max position and by what is left of the strategy's fund allocation.",
        "max_daily_loss on intraday P&L triggers the kill switch, which sells everything; the strategy's own daily loss stop blocks new entries for the day.",
    ]
    return {
        "system_facts": facts,
        "symbols": symbols, "symbol_stats": stats, "correlation": RS.correlations(series),
        "basket": RS.basket_worst_day(series, weights), "recent_headlines_10d": news, "recent_signals_10d": sigs,
        "exposure": exposure, "running_strategies": running, "funds": fund_view,
        "risk_controls": {"max_gross_exposure": risk.max_gross_exposure, "max_daily_loss": risk.max_daily_loss,
                          "portfolio": {k: str(getattr(risk.portfolio, k)) for k in risk.portfolio.__dataclass_fields__} if risk.portfolio else None},
    }


def review(conn, thread: str, yaml_text: str, actor: str, backtest: dict | None = None) -> dict:
    """Run the four-agent review panel on a validated draft, in parallel; store and post the results to the thread."""
    from concurrent.futures import ThreadPoolExecutor

    _need_llm()
    if not DESIGN_THREAD.match(thread):
        raise KeyError(thread)
    risk = load_risk()
    v = validate(yaml_text, risk)
    if not v.ok:
        raise ValueError("the draft doesn't validate: " + "; ".join(v.errors))
    s = v.strategy
    model = _model_id("claude_opus")
    evidence = panel_evidence(conn, s, risk)
    first = db.row(conn, "SELECT content FROM chats WHERE thread=? ORDER BY id LIMIT 1", (thread,))
    context = first["content"].split(CONTEXT_MARK)[0] if first and CONTEXT_MARK in first["content"] else design_context(conn, risk)
    material = (f"<strategy_file sha256=\"{v.hash}\">\n{yaml_text}\n</strategy_file>\n"
                f"<back_translation>\n{back_translate(s)}\n</back_translation>\n"
                f"<validator_warnings>{json.dumps(v.warnings)}</validator_warnings>\n"
                f"<backtest>{json.dumps(backtest, default=str) if backtest else 'not run for this draft'}</backtest>\n"
                f"<evidence>\n{json.dumps(evidence, default=str, indent=1)}\n</evidence>\n"
                f"<account_context>\n{context}\n</account_context>")
    base = prompt("panel@v1")
    reference = compiler_reference(risk)

    def one(role: str):
        name, focus, brief = PANEL_ROLES[role]
        c = db.get()  # one connection per worker thread
        system = base.replace("{role}", brief) + "\n\n# DSL reference\n\n" + reference
        t0 = time.time()
        try:
            _, parsed, _ = llm.call(c, model=model, task=f"panel.{role}", system=system,
                                    messages=[{"role": "user", "content": material + f"\n\nReview this draft as the {name}."}],
                                    max_tokens=16000, max_output=16000, effort="high", schema=PANEL_SCHEMA, timeout=300,
                                    max_context_chars=400_000)
            return role, {"ok": True, **parsed, "seconds": round(time.time() - t0, 1)}
        except Exception as e:  # noqa: BLE001 - one agent failing shouldn't sink the panel
            return role, {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}

    with ThreadPoolExecutor(max_workers=len(PANEL_ROLES)) as pool:
        results = dict(pool.map(one, PANEL_ROLES))
    now = time.time()
    for role, res in results.items():
        conn.execute("INSERT INTO design_reviews(thread, draft_hash, ts, role, model, result, actor) VALUES (?,?,?,?,?,?,?)",
                     (thread, v.hash, now, role, model, json.dumps(res), actor))
    lines = [f"[panel] Review panel on draft {s.id} (sha256 {v.hash[:12]}):"]
    for role, res in results.items():
        name = PANEL_ROLES[role][0]
        if not res["ok"]:
            lines.append(f"- {name}: failed ({res['error']})")
            continue
        lines.append(f"- {name} ({res['verdict']}, {res['score']}/10): {res['summary']}")
        lines += [f"    concern [{c['severity']}]: {c['text']}" for c in res["concerns"] if c["severity"] != "low"]
        lines += [f"    suggestion: {x['text']}" + (f" -> {x['change']}" if x["change"] else "") for x in res["suggestions"][:4]]
    conn.execute("INSERT INTO chats(ts, thread, role, content, model) VALUES (?,?,?,?,?)", (now, thread, "user", "\n".join(lines), "panel"))
    audit.log(conn, "strategy.panel_review", actor, {"thread": thread, "hash": v.hash,
                                                      "verdicts": {r: x.get("verdict") for r, x in results.items()}})
    return {"ok": True, "hash": v.hash, "results": results}

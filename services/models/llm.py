"""Claude access for the models service (the only service that holds model keys).

Every call is logged to model_calls with latency, tokens and estimated cost.
"""
from __future__ import annotations

import json
import os
import time

from common import db
from common.config import load_yaml

# $ per 1M tokens (input, output)
PRICES = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
}
# Models whose requests opt into server-side refusal fallbacks ("default" routing).
FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5"}

_client = None
# Set by backtest replays: calls get a "replay." task prefix and their own daily quota, so a replay can
# never use up the calls live triage needs.
import contextvars
REPLAY = contextvars.ContextVar("llm_replay", default=False)


class LLMUnavailable(RuntimeError):
    pass


def available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def client():
    global _client
    if _client is None:
        if not available():
            raise LLMUnavailable("ANTHROPIC_API_KEY is not set for the models service")
        import anthropic

        _client = anthropic.Anthropic(timeout=60.0, max_retries=1, default_headers={"anthropic-workspace-id": os.environ["ANTHROPIC_WORKSPACE_ID"]} if os.environ.get("ANTHROPIC_WORKSPACE_ID") else {})
    return _client


def _log(conn, model, task, t0, ok, err=None, usage=None):
    it = getattr(usage, "input_tokens", 0) or 0
    ot = getattr(usage, "output_tokens", 0) or 0
    pin, pout = PRICES.get(model, (0, 0))
    conn.execute(
        "INSERT INTO model_calls(ts, provider, model, task, latency_ms, ok, error, input_tokens, output_tokens, cost_usd)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (time.time(), "anthropic", model, task, (time.time() - t0) * 1000, int(ok), err, it, ot,
         it / 1e6 * pin + ot / 1e6 * pout),
    )


def _request(model: str, system: str, messages: list, max_tokens: int, effort: str, schema: dict | None,
             cache: bool = False, timeout: float | None = None):
    kwargs = dict(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=messages,
        output_config={"effort": effort},
    )
    if timeout is not None:
        kwargs["timeout"] = timeout  # high-effort authoring calls outlast the client's 60s default
    if cache:
        kwargs["cache_control"] = {"type": "ephemeral"}  # multi-turn chats resend the same context
    if schema is not None:
        kwargs["output_config"]["format"] = {"type": "json_schema", "schema": schema}
    if model in FALLBACK_MODELS:
        # On a policy decline the API re-runs the request on a fallback model in the same call.
        kwargs["betas"] = ["server-side-fallback-2026-07-01"]
        kwargs["fallbacks"] = "default"
        return client().beta.messages.create(**kwargs)
    return client().messages.create(**kwargs)


def call(conn, *, model: str, task: str, system: str, messages: list, max_tokens: int = 16000,
         effort: str = "medium", schema: dict | None = None, cache: bool = False, timeout: float | None = None,
         max_output: int = 8000, max_context_chars: int = 64000):
    """Returns (text, parsed_json_or_None, response). Raises LLMUnavailable or anthropic errors."""
    import anthropic

    # Reserve the daily call quota before making the external request.
    cfg=load_yaml("models.yaml")
    replay=REPLAY.get()
    if replay and not task.startswith("replay."):
        task="replay."+task
    cap=int(cfg.get("budgets",{}).get("replay_calls_per_day" if replay else "total_calls_per_day",2000 if replay else 200))
    day=time.strftime("%Y-%m-%d",time.gmtime())
    key=("models.replay_quota." if replay else "models.call_quota.")+day
    with db.tx(conn):
        conn.execute("SELECT pg_advisory_xact_lock(?)",(0x4d4f44454c,))
        used=int(db.kv_get(conn,key) or 0)
        if used>=cap:raise LLMUnavailable("Daily model call limit reached")
        db.kv_set(conn,key,used+1)
    if len(json.dumps(messages))>max_context_chars:raise LLMUnavailable("Model context is too large; start a new conversation")
    max_tokens=min(max_tokens,max_output)
    t0 = time.time()
    try:
        resp = _request(model, system, messages, max_tokens, effort, schema, cache, timeout)
    except anthropic.APIStatusError as e:
        _log(conn, model, task, t0, False, f"{e.status_code}: {e.message}"[:500])
        raise
    except anthropic.APIConnectionError as e:
        _log(conn, model, task, t0, False, f"connection: {e}"[:500])
        raise
    if resp.stop_reason == "refusal":
        _log(conn, model, task, t0, False, "refusal", resp.usage)
        raise LLMUnavailable("the model declined this request")
    if schema is not None and resp.stop_reason == "max_tokens":
        _log(conn, model, task, t0, False, "max_tokens", resp.usage)
        raise LLMUnavailable("the model's answer was cut off before it finished; try a shorter request")
    text = "".join(b.text for b in resp.content if b.type == "text")
    _log(conn, model, task, t0, True, None, resp.usage)
    parsed = None
    if schema is not None:
        parsed = json.loads(text)
    return text, parsed, resp


def calls_today(conn, task: str) -> int:
    start = time.time() - 24 * 3600
    r = db.row(conn, "SELECT COUNT(*) n FROM model_calls WHERE task=? AND ts>=? AND provider='anthropic'", (task, start))
    return r["n"] if r else 0

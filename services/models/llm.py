"""Kimi (Moonshot) access for the models service (the only service that holds model keys).

Kimi speaks the OpenAI chat-completions protocol; this module wraps it behind call() so the rest of the
service is provider-agnostic. Every call is logged to model_calls with latency, tokens and estimated cost.
"""
from __future__ import annotations

import json
import os
import time
from types import SimpleNamespace

import httpx

from common import db
from common.config import load_yaml

PROVIDER = "kimi"
DEFAULT_BASE_URL = "https://api.moonshot.cn/v1"
# $ per 1M tokens (input, output)
PRICES = {
    "kimi-k3": (3.0, 15.0),
    "kimi-k2.7-code": (0.95, 4.0),
    "kimi-k2.7-code-highspeed": (0.95, 4.0),
}
# Kimi's finish_reason -> the stop reasons callers check
_STOP = {"stop": "end_turn", "length": "max_tokens", "content_filter": "refusal", "tool_calls": "tool_use"}

_client = None
# Set by backtest replays: calls get a "replay." task prefix and their own daily quota, so a replay can
# never use up the calls live triage needs.
import contextvars
REPLAY = contextvars.ContextVar("llm_replay", default=False)


class LLMUnavailable(RuntimeError):
    pass


class APIError(RuntimeError):
    """The Kimi API answered with an error, or couldn't be reached."""
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class APIConnectionError(APIError):
    pass


class APITimeoutError(APIConnectionError):
    pass


def _key() -> str | None:
    return os.environ.get("KIMI_API_KEY") or os.environ.get("MOONSHOT_API_KEY")


def available() -> bool:
    return bool(_key())


def client() -> httpx.Client:
    global _client
    if _client is None:
        if not available():
            raise LLMUnavailable("KIMI_API_KEY is not set for the models service")
        base = (os.environ.get("KIMI_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        _client = httpx.Client(base_url=base, timeout=60.0, headers={"Authorization": f"Bearer {_key()}"})
    return _client


def _log(conn, model, task, t0, ok, err=None, usage=None):
    it = getattr(usage, "input_tokens", 0) or 0
    ot = getattr(usage, "output_tokens", 0) or 0
    pin, pout = PRICES.get(model, (0, 0))
    conn.execute(
        "INSERT INTO model_calls(ts, provider, model, task, latency_ms, ok, error, input_tokens, output_tokens, cost_usd)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (time.time(), PROVIDER, model, task, (time.time() - t0) * 1000, int(ok), err, it, ot,
         it / 1e6 * pin + ot / 1e6 * pout),
    )


def _request(model: str, system: str, messages: list, max_tokens: int, effort: str, schema: dict | None,
             cache: bool = False, timeout: float | None = None):
    """One chat completion, returned in the shape callers use: .content[].text, .usage, .stop_reason."""
    body = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "system", "content": system}, *messages],
        "reasoning_effort": effort,
    }
    if schema is not None:
        body["response_format"] = {"type": "json_schema", "json_schema": {"name": "output", "strict": True, "schema": schema}}
    # Kimi caches repeated prompt prefixes automatically, so `cache` needs no request field.
    for attempt in range(2):  # one retry on dropped connections, rate limits and server errors
        try:
            r = client().post("/chat/completions", json=body, timeout=timeout or 60.0)
        except httpx.TimeoutException as e:
            raise APITimeoutError(f"timeout: {e}", 504) from None
        except httpx.HTTPError as e:
            if attempt:
                raise APIConnectionError(str(e), 503) from None
            time.sleep(1)
            continue
        if r.status_code in (429, 500, 502, 503, 504) and not attempt:
            time.sleep(2)
            continue
        break
    if r.status_code >= 400:
        try:
            msg = r.json().get("error", {}).get("message") or r.text
        except ValueError:
            msg = r.text
        raise APIError(msg[:500], r.status_code)
    d = r.json()
    choice = (d.get("choices") or [{}])[0]
    u = d.get("usage") or {}
    return SimpleNamespace(
        model=d.get("model", model),
        content=[SimpleNamespace(type="text", text=(choice.get("message") or {}).get("content") or "")],
        stop_reason=_STOP.get(choice.get("finish_reason"), choice.get("finish_reason")),
        usage=SimpleNamespace(input_tokens=u.get("prompt_tokens", 0), output_tokens=u.get("completion_tokens", 0)),
    )


def call(conn, *, model: str, task: str, system: str, messages: list, max_tokens: int = 16000,
         effort: str = "medium", schema: dict | None = None, cache: bool = False, timeout: float | None = None,
         max_output: int = 8000, max_context_chars: int = 64000):
    """Returns (text, parsed_json_or_None, response). Raises LLMUnavailable or APIError."""
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
    except APIConnectionError as e:
        _log(conn, model, task, t0, False, f"connection: {e.message}"[:500])
        raise
    except APIError as e:
        _log(conn, model, task, t0, False, f"{e.status_code}: {e.message}"[:500])
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
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            raise LLMUnavailable("the model returned malformed JSON; try again") from None
    return text, parsed, resp


def calls_today(conn, task: str) -> int:
    start = time.time() - 24 * 3600
    r = db.row(conn, "SELECT COUNT(*) n FROM model_calls WHERE task=? AND ts>=? AND provider=?", (task, start, PROVIDER))
    return r["n"] if r else 0

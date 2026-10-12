"""Models service: triage worker + internal HTTP API (chat, compile, Idea Lab, notes).

Holds the model API keys. Listens on the internal network only (default 127.0.0.1:8081).

    python -m services.models
"""
from __future__ import annotations

import logging
import os
import threading
import time

import anthropic
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from common import audit, db, timeutil as T
from common.config import load_yaml

from . import assumption_monitor, authoring, heuristic, idealab, llm, triage
from .quicklayer import provider_status

log = logging.getLogger("models")
app = FastAPI(title="models (internal)", docs_url=None, redoc_url=None)


def conn():
    return db.get()


class ChatIn(BaseModel):
    thread: str
    message: str
    actor: str


class CompileIn(BaseModel):
    text: str
    actor: str
    name: str = Field(default="", max_length=80)


class IdeasIn(BaseModel):
    focus: str = ""
    actor: str


class NoteIn(BaseModel):
    text: str


class DiffIn(BaseModel):
    hash: str
    diff: str
    actor: str


def _offline(e: Exception):
    raise HTTPException(503, str(e))


def _api_error(e: anthropic.APIError):
    if isinstance(e, anthropic.APITimeoutError):
        raise HTTPException(504, "Claude took too long to answer. Try again.") from None
    if isinstance(e, anthropic.APIConnectionError):
        raise HTTPException(503, "Couldn't reach the Claude API (network error). Check the connection and try again.") from None
    status = getattr(e, "status_code", 502)
    raise HTTPException(503 if status in (429, 529) or status >= 500 else 502, f"Claude API error {status}: {e.message}") from None


@app.get("/health")
def health():
    return {"ok": True, "providers": provider_status(), "llm": llm.available()}


@app.post("/chat")
def chat(body: ChatIn):
    try:
        return authoring.chat(conn(), body.thread, body.message, body.actor)
    except authoring.Offline as e:
        _offline(e)
    except KeyError as e:
        raise HTTPException(404, f"not found: {e}") from None
    except llm.LLMUnavailable as e:
        _offline(e)
    except anthropic.APIError as e:
        _api_error(e)


class DesignIn(BaseModel):
    thread: str
    message: str = Field(min_length=1, max_length=8000)
    actor: str
    source: str | None = None


@app.post("/design")
def design(body: DesignIn):
    try:
        return authoring.design(conn(), body.thread, body.message, body.actor, body.source)
    except KeyError:
        raise HTTPException(400, "bad design thread id") from None
    except (authoring.Offline, llm.LLMUnavailable) as e:
        _offline(e)
    except anthropic.APIError as e:
        _api_error(e)


class ReviewIn(BaseModel):
    thread: str
    yaml: str = Field(min_length=10, max_length=40_000)
    actor: str
    backtest: dict | None = None


@app.post("/review")
def review(body: ReviewIn):
    try:
        return authoring.review(conn(), body.thread, body.yaml, body.actor, body.backtest)
    except KeyError:
        raise HTTPException(400, "bad design thread id") from None
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    except (authoring.Offline, llm.LLMUnavailable) as e:
        _offline(e)


@app.post("/compile")
def compile_(body: CompileIn):
    try:
        return authoring.compile_text(conn(), body.text, body.actor, body.name)
    except (authoring.Offline, llm.LLMUnavailable) as e:
        _offline(e)
    except anthropic.APIError as e:
        _api_error(e)


@app.post("/diff")
def diff(body: DiffIn):
    try:
        return authoring.apply_diff(conn(), body.hash, body.diff, body.actor)
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e)) from None


@app.post("/ideas/run")
def ideas_run(body: IdeasIn):
    try:
        return idealab.run(conn(), body.focus, body.actor)
    except idealab.NoEvents as e:
        raise HTTPException(409, str(e)) from None
    except llm.LLMUnavailable as e:
        _offline(e)
    except anthropic.APIError as e:
        _api_error(e)


class DiscoveryIn(BaseModel):
    config: dict
    actor: str
    offline: bool = False


@app.post("/discovery/run")
def discovery_run(body: DiscoveryIn):
    """Start a single-ticker discovery run in the background; returns its id at once (progress is in discovery_runs)."""
    from discovery.run import Config, run as run_discovery
    try:
        cfg = Config(**body.config)
    except Exception as e:  # noqa: BLE001 - pydantic validation errors become a 400
        raise HTTPException(400, str(e)) from None
    c = conn()
    busy = db.row(c, "SELECT id FROM discovery_runs WHERE status='running' AND created_at > ?", (time.time() - 6 * 3600,))
    if busy:
        raise HTTPException(409, f"discovery run #{busy['id']} is still running; one run at a time")
    rid = c.insert("INSERT INTO discovery_runs(created_at, created_by, ticker, config, status, stage) VALUES (?,?,?,?,?,?)",
                   (time.time(), body.actor, cfg.ticker, cfg.model_dump_json(), "running", "queued"))

    def work():
        wc = db.connect()
        try:
            gen = None
            if not body.offline and llm.available():
                from .discovery import ClaudeGenerator
                gen = ClaudeGenerator()
            run_discovery(wc, cfg, body.actor, generator=gen, run_id=rid)
        except Exception:  # noqa: BLE001 - the run row already records the failure
            log.exception("discovery run %s failed", rid)
        finally:
            wc.close()

    threading.Thread(target=work, name=f"discovery-{rid}", daemon=True).start()
    return {"run_id": rid, "model": "claude" if (llm.available() and not body.offline) else "templates@v1"}


@app.post("/notes/parse")
def notes_parse(body: NoteIn):
    # one-word stance; the offline parser keeps this free and instant
    return {"stance": heuristic.parse_stance(body.text), "model": heuristic.MODEL_ID}


def worker(stop: threading.Event):
    c = db.connect()
    db.init(c)
    audit.log(c, "models.start", "models", {"llm": llm.available(), "providers": provider_status()})
    assumptions_at = 0.0
    while not stop.is_set():
        try:
            if time.time() - assumptions_at >= 30:  # DSL 0.2 monitored assumptions and overlay context
                assumptions_at = time.time()
                assumption_monitor.run(c)
            now = db.kv_get(c, "sim.now")
            if now is not None:
                triage.run_pending(c, int(now))
                db.kv_set(c,"health.triage",{"ts":time.time()})
                state=db.kv_get(c,"sim.state") or {}
                if state.get("source")=="alpaca_paper" and T.et(now).weekday()<5:
                    local=T.et(now)
                    schedule=load_yaml("idealab.yaml").get("schedule",{})
                    for name,clock in schedule.items():
                        due=local.strftime("%H:%M")>=clock
                        if name=="premarket_brief":due=due and local.hour<9
                        if name=="post_close_review":due=due and state.get("phase")=="closed"
                        key=f"models.schedule.{local.date()}.{name}"
                        if due and not db.kv_get(c,key):
                            db.kv_set(c,key,{"started":time.time()})
                            try:
                                idealab.run(c,name.replace("_"," "),"scheduler")
                            except idealab.NoEvents as e:
                                audit.log(c,"idealab.skipped","scheduler",{"run":name,"why":str(e)})
            db.kv_set(c, "health.models", {"ts": time.time()})
        except Exception:  # noqa: BLE001 - keep the worker alive; errors land in the log
            log.exception("triage loop error")
        stop.wait(0.5)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s models %(levelname)s %(message)s")
    db.init(db.get())
    from discovery.run import fail_orphans
    if orphans := fail_orphans(db.get()):  # their worker threads died with the previous process
        log.warning("marked interrupted discovery runs as failed: %s", orphans)
    stop = threading.Event()
    t = threading.Thread(target=worker, args=(stop,), daemon=True)
    t.start()
    from .replay_worker import ReplayWorker
    ReplayWorker().start()  # backtest model replay, on its own connection
    # Inference may be slow on first load; process liveness is separate from triage progress.
    def heartbeat():
        c=db.connect()
        while not stop.is_set():
            db.kv_set(c,"health.models",{"ts":time.time()})
            stop.wait(2)
    threading.Thread(target=heartbeat,daemon=True).start()
    host = os.environ.get("MODELS_HOST", "127.0.0.1")
    port = int(os.environ.get("MODELS_PORT", "8081"))
    try:
        uvicorn.run(app, host=host, port=port, log_level="warning")
    finally:
        stop.set()


if __name__ == "__main__":
    main()

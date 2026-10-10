"""Models service: triage worker + internal HTTP API (chat, compile, Idea Lab, notes).

Holds the model API keys. Listens on the internal network only (default 127.0.0.1:8081).

    python -m services.models
"""
from __future__ import annotations

import logging
import os
import threading
import time

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from common import audit, db, timeutil as T
from common.config import load_yaml

from . import authoring, heuristic, idealab, llm, triage
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


@app.post("/compile")
def compile_(body: CompileIn):
    try:
        return authoring.compile_text(conn(), body.text, body.actor)
    except (authoring.Offline, llm.LLMUnavailable) as e:
        _offline(e)


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
    except llm.LLMUnavailable as e:
        _offline(e)


@app.post("/notes/parse")
def notes_parse(body: NoteIn):
    # one-word stance; the offline parser keeps this free and instant
    return {"stance": heuristic.parse_stance(body.text), "model": heuristic.MODEL_ID}


def worker(stop: threading.Event):
    c = db.connect()
    db.init(c)
    audit.log(c, "models.start", "models", {"llm": llm.available(), "providers": provider_status()})
    while not stop.is_set():
        try:
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
                            idealab.run(c,name.replace("_"," "),"scheduler")
            db.kv_set(c, "health.models", {"ts": time.time()})
        except Exception:  # noqa: BLE001 - keep the worker alive; errors land in the log
            log.exception("triage loop error")
        stop.wait(0.5)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s models %(levelname)s %(message)s")
    db.init(db.get())
    stop = threading.Event()
    t = threading.Thread(target=worker, args=(stop,), daemon=True)
    t.start()
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

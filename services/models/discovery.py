"""Claude as the discovery generator: proposes hypotheses for one ticker and revises candidates against
their measured weaknesses. Code still measures, scores and decides; this module only writes DSL files.

Falls back to the offline templates (and their deterministic fixes) whenever a call fails, so a run
never stops on a model error. Each run has its own call cap on top of the account's daily quota.
"""
from __future__ import annotations

import json
import logging

import yaml

from discovery.run import Config, OfflineGenerator
from discovery.templates import PROMPT, sid_for
from dsl.explain import back_translate
from dsl.validator import validate

from . import llm
from .authoring import compiler_reference
from .quicklayer import _model_id, prompt

log = logging.getLogger("models.discovery")

PROPOSE_SCHEMA = {
    "type": "object",
    "properties": {"hypotheses": {"type": "array", "items": {
        "type": "object",
        "properties": {"family": {"type": "string"}, "title": {"type": "string"}, "mechanism": {"type": "string"},
                       "falsify": {"type": "string"}, "evidence": {"type": "string"}, "yaml": {"type": "string"}},
        "required": ["family", "title", "mechanism", "falsify", "evidence", "yaml"], "additionalProperties": False}}},
    "required": ["hypotheses"], "additionalProperties": False,
}
REVISE_SCHEMA = {
    "type": "object",
    "properties": {"change": {"type": "string"}, "addresses": {"type": "string"}, "why": {"type": "string"}, "yaml": {"type": "string"}},
    "required": ["change", "addresses", "why", "yaml"], "additionalProperties": False,
}


class ClaudeGenerator(OfflineGenerator):
    free_revisions = True  # can revise a candidate even when code found no mechanical fix

    def __init__(self, max_calls: int = 24, hypotheses_per_kind: int = 4):
        self.model = _model_id("claude_opus")
        self.name = f"{self.model} + templates@v1"
        self.max_calls, self.calls, self.per_kind = max_calls, 0, hypotheses_per_kind
        self.errors: list[str] = []

    # ---------------------------------------------------------------- plumbing
    def _call(self, conn, task: str, risk, content: str, schema: dict) -> dict | None:
        if self.calls >= self.max_calls:
            return None
        self.calls += 1
        try:
            _, parsed, _ = llm.call(conn, model=self.model, task=task, max_tokens=16000, max_output=16000, effort="high",
                                    system=prompt("discovery@v1") + "\n\n# DSL reference\n\n" + compiler_reference(risk),
                                    messages=[{"role": "user", "content": content}], schema=schema, timeout=300,
                                    max_context_chars=120000)
            return parsed
        except Exception as e:  # noqa: BLE001 - a model failure falls back to templates, never stops the run
            self.errors.append(f"{task}: {type(e).__name__}: {e}"[:300])
            log.warning("discovery %s failed: %s", task, e)
            return None

    def _repair(self, conn, risk, text: str, errors: list[str], sid: str) -> str | None:
        parsed = self._call(conn, "discovery.repair", risk,
                            f"Model id: {self.model}. This file was rejected by the validator. Keep id {sid}.\n\n```yaml\n{text}\n```\n\n"
                            "Errors:\n- " + "\n- ".join(errors) + "\n\nReturn the corrected complete file (change, addresses and why can be brief).",
                            REVISE_SCHEMA)
        return parsed["yaml"].strip() + "\n" if parsed else None

    def _fix_ids(self, text: str, sid: str) -> str:
        try:
            d = yaml.safe_load(text)
        except yaml.YAMLError:
            return text
        if isinstance(d, dict):
            d["id"], d["version"] = sid, 1
            d.setdefault("source", {})
            if isinstance(d["source"], dict):
                d["source"].update(compiled_by=self.model, prompt=PROMPT)
            return yaml.safe_dump(d, sort_keys=False, width=1000, allow_unicode=True)
        return text

    def _valid(self, conn, risk, text: str, sid: str) -> str:
        text = self._fix_ids(text, sid)
        v = validate(text, risk)
        if v.ok:
            return text
        fixed = self._repair(conn, risk, text, v.errors, sid)
        return self._fix_ids(fixed, sid) if fixed else text  # still invalid: evaluated (and counted) as invalid

    def _shape(self, cfg: Config, kind: str, peers: list[str], risk) -> str:
        if kind == "intraday":
            return (f"kind intraday; universe.symbols: [{cfg.ticker}]; entries within {risk.entries[0]:%H:%M}-{risk.entries[1]:%H:%M} ET, "
                    f"flat by {risk.flatten_by:%H:%M}; risk_per_trade at most {risk.max_risk:.2%}; available peers: {peers or 'none'}")
        pr = risk.portfolio
        cap = int(min(pr.max_allocation, pr.max_position / 1.5) * 100)
        return (f"kind portfolio; holdings capital {cap}%, max_holding 150%, one group `focus` with [{cfg.ticker}] at 100%; "
                f"schedule.hold_sessions {pr.max_hold_sessions}, decide_at \"{pr.decide_at:%H:%M}\"; available peers: {peers or 'none'}")

    # ---------------------------------------------------------------- generator API
    def propose(self, conn, cfg: Config, kind: str, prof: dict, peers: list[str], risk) -> list[dict]:
        out = super().propose(conn, cfg, kind, prof, peers, risk)
        tried = sorted({h["family"] for h in out})
        parsed = self._call(conn, f"discovery.propose.{kind}", risk,
                            f"Model id: {self.model}. Focus ticker: {cfg.ticker}. Write {self.per_kind} {kind} hypotheses.\n"
                            f"File shape: {self._shape(cfg, kind, peers, risk)}.\n"
                            f"Families already covered by templates (propose different ones): {tried}.\n"
                            f"Use ids disc_{cfg.ticker.lower()}_<family> with a short snake_case family.\n\n"
                            f"<profile>\n{json.dumps(prof, default=lambda x: round(x, 5) if isinstance(x, float) else str(x))}\n</profile>",
                            PROPOSE_SCHEMA)
        for h in (parsed or {}).get("hypotheses", [])[:self.per_kind]:
            fam = "".join(c if c.isalnum() else "_" for c in h["family"].lower())[:30].strip("_") or "claude"
            sid = sid_for(cfg.ticker, fam)
            out.append({"kind": kind, "family": fam, "title": h["title"], "mechanism": h["mechanism"], "falsify": h["falsify"],
                        "origin": self.model, "yaml": self._valid(conn, risk, h["yaml"].strip() + "\n", sid)})
        return out

    def revise(self, conn, cfg: Config, trial: dict, ev: dict, history: list[dict], risk, n: int) -> list[dict]:
        out = []
        v = validate(trial["yaml"], risk)
        diag = {k: ev.get(k) for k in ("base", "stress", "fold_median_sharpe", "baseline_sharpe", "complexity", "ablation",
                                       "plateau", "regimes", "weaknesses", "gates", "score")}
        others = [{"id": t["id"], "family": t["family"], "change": t.get("change"), "score": round(t["score"], 2) if t.get("score") is not None else None,
                   "status": t["status"]} for t in history if t["kind"] == trial["kind"]][-30:]
        current = yaml.safe_load(trial["yaml"]) or {}
        sid = current.get("id") or sid_for(cfg.ticker, trial["family"] or "claude")
        peers = sorted(set(current.get("references") or []) | ({ev["regimes"]["peer"]} if (ev.get("regimes") or {}).get("peer") else set()))
        parsed = self._call(conn, f"discovery.revise.{trial['kind']}", risk,
                            f"Model id: {self.model}. Revise this {trial['kind']} candidate for {cfg.ticker}. Keep id {sid}.\n"
                            f"File shape: {self._shape(cfg, trial['kind'], peers, risk)}.\n\n"
                            f"```yaml\n{trial['yaml']}\n```\n\n<back_translation>\n{back_translate(v.strategy) if v.ok else ''}\n</back_translation>\n"
                            f"<mechanism>{trial.get('mechanism')}</mechanism>\n"
                            f"<diagnostics>\n{json.dumps(diag, default=lambda x: round(x, 5) if isinstance(x, float) else str(x))}\n</diagnostics>\n"
                            f"<other_trials>\n{json.dumps(others)}\n</other_trials>",
                            REVISE_SCHEMA)
        if parsed:
            out.append({"yaml": self._valid(conn, risk, parsed["yaml"].strip() + "\n", sid), "change": parsed["change"],
                        "addresses": parsed["addresses"], "why": parsed["why"], "origin": self.model})
        out += super().revise(conn, cfg, trial, ev, history, risk, max(0, n - len(out)))
        return out[:n]

"""Index quotes and daily history from the account-configured feed (config/market_inputs.yaml).

Only the broker calls out for market data. A quote is usable only when it is positive, timestamped
inside the current regular session and fresh (the strategy's max_age); anything else is unknown.
"""
from __future__ import annotations

import math
from datetime import date

import httpx

from common import market_inputs as MI
from common import timeutil as T

YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"


class IndexFeed:
    def __init__(self, client=None):
        self.http = client or httpx.Client(timeout=10, headers={"User-Agent": "llamaOS/0.3 (paper trading)"},
                                           follow_redirects=False)

    def _chart(self, symbol: str, params: dict) -> dict:
        r = self.http.get(YAHOO.format(symbol=symbol), params=params)
        r.raise_for_status()
        res = (r.json().get("chart") or {}).get("result") or []
        if not res:
            raise ValueError(f"no chart data for {symbol}")
        return res[0]

    def quote(self, instrument: str) -> tuple[float, float] | None:
        """(value, epoch seconds of the value) or None when the index has no configured feed."""
        spec = MI.feeds().get(instrument)
        if spec is None:
            return None
        meta = self._chart(spec["symbol"], {"interval": "1m", "range": "1d"}).get("meta") or {}
        value, ts = meta.get("regularMarketPrice"), meta.get("regularMarketTime")
        if not isinstance(value, (int, float)) or not isinstance(ts, (int, float)) or not math.isfinite(value) or value <= 0:
            return None
        return float(value), float(ts)

    def history(self, instrument: str, start_ts: float, before: date) -> list[tuple]:
        """Completed daily sessions (day, o, h, l, c) before `before`."""
        spec = MI.feeds().get(instrument)
        if spec is None:
            return []
        res = self._chart(spec["symbol"], {"interval": "1d", "period1": int(start_ts), "period2": int(T.open_ts(before))})
        q = ((res.get("indicators") or {}).get("quote") or [{}])[0]
        out = []
        for k, ts in enumerate(res.get("timestamp") or []):
            d = T.et(ts).date()
            c = (q.get("close") or [None] * (k + 1))[k]
            if d >= before or c is None:
                continue
            o, h, lo = ((q.get(f) or [None] * (k + 1))[k] for f in ("open", "high", "low"))
            out.append((d.isoformat(), o, h, lo, float(c)))
        return out

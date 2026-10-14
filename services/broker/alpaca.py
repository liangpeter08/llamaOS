"""Paper-only Alpaca HTTP adapter. No live endpoint or live credential path."""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

import httpx

PAPER_URL = "https://paper-api.alpaca.markets"
DATA_URL = "https://data.alpaca.markets"
SIP_DELAY_S = 16 * 60  # the free data plan serves SIP bars older than 15 minutes
log = logging.getLogger("alpaca")


def epoch(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def iso(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def price(value: float) -> str:
    return str(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


class Alpaca:
    def __init__(self, client=None):
        base = os.environ.get("ALPACA_BASE_URL", PAPER_URL).rstrip("/")
        if base not in (PAPER_URL, PAPER_URL + "/v2") or os.environ.get("ALPACA_PAPER", "true").lower() != "true":
            raise ValueError("Only Alpaca paper trading is supported; live endpoints are refused")
        if os.environ.get("MODE", "paper") not in ("paper", "record_only"):
            raise ValueError("Alpaca mode must be paper or record_only")
        from common import broker_accounts as BA
        key, secret, self.account_rev, self.source, self.schema = BA.credentials()
        if not key or not secret:
            raise ValueError("No Alpaca paper account: add one on the dashboard System page or in secrets/broker.env")
        self.feed = os.environ.get("ALPACA_DATA_FEED", "iex")  # live bars
        # Stored history (daily bars, backtest downloads): the consolidated SIP tape. IEX alone is ~2% of
        # volume, so liquidity filters fail and thin symbols print a bar only every few minutes.
        self.history_feed = os.environ.get("ALPACA_HISTORY_FEED", "sip")
        self._headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
        self._own_client = client is None
        self._read_timeout = 10
        self.http = client or self._new_client()

    def _new_client(self):
        # Fail fast on a stuck connection so one bad request doesn't hold the trading loop; the next loop retries.
        # Clones (history downloads) keep a longer read timeout for large pages.
        return httpx.Client(headers=self._headers, timeout=httpx.Timeout(self._read_timeout, connect=5), follow_redirects=False)

    def clone(self):
        """The same account on its own connection pool, so one thread's stuck request can't block another's."""
        other = object.__new__(Alpaca)
        other.__dict__.update(self.__dict__)
        other._read_timeout = 30
        if self._own_client:
            other.http = other._new_client()
        return other

    def request(self, method, path, *, data=False, **kw):
        url = (DATA_URL if data else PAPER_URL) + path
        try:
            r = self.http.request(method, url, **kw)
        except httpx.PoolTimeout:
            # A pool left holding phantom connections (seen after a TLS reset) never recovers: replace it.
            if not self._own_client:
                raise
            log.warning("Alpaca connection pool stuck; reconnecting")
            old, self.http = self.http, self._new_client()
            old.close()
            if method != "GET":
                raise  # an order may need recovery by client ID; never resend blindly
            r = self.http.request(method, url, **kw)
        r.raise_for_status()
        return r.json() if r.content else None

    def apply_guard(self, allow_short: bool) -> None:
        """Account-side mirror of the risk limits: 1x margin, and no shorting unless the risk controls allow it."""
        cfg = self.request("GET", "/v2/account/configurations")
        if float(cfg.get("max_margin_multiplier", 0)) == 1 and (allow_short or cfg.get("no_shorting")):
            return
        body = {"max_margin_multiplier": "1", **({} if allow_short else {"no_shorting": True})}
        cfg = self.request("PATCH", "/v2/account/configurations", json=body)
        if float(cfg.get("max_margin_multiplier", 0)) != 1 or (not allow_short and not cfg.get("no_shorting")):
            raise RuntimeError("Paper account guard verification failed")

    def account(self):
        return self.request("GET", "/v2/account")

    def clock(self):
        return self.request("GET", "/v2/clock")

    def calendar(self, start, end):
        return self.request("GET", "/v2/calendar", params={"start": str(start), "end": str(end)})

    def asset(self, symbol):
        return self.request("GET", f"/v2/assets/{symbol}")

    def positions(self):
        return self.request("GET", "/v2/positions")

    def orders(self, status="open", after=None):
        params = {"status": status, "nested": "true", "limit": 500, "direction": "desc"}
        if after:
            params["after"] = iso(after)
        return self.request("GET", "/v2/orders", params=params)

    def order(self, oid):
        return self.request("GET", f"/v2/orders/{oid}", params={"nested": "true"})

    def by_client_id(self, cid):
        try:
            return self.request("GET", "/v2/orders:by_client_order_id", params={"client_order_id": cid})
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise

    def bracket(self, intent, cid):
        if os.environ.get("MODE", "paper") != "paper":
            raise ValueError("Orders are disabled in record_only")
        body = {"symbol": intent.symbol, "qty": str(intent.qty), "side": "buy" if intent.side == "long" else "sell",
                "type": "limit", "time_in_force": "day", "order_class": "bracket", "extended_hours": False,
                "limit_price": price(intent.limit), "take_profit": {"limit_price": price(intent.target)},
                "stop_loss": {"stop_price": price(intent.stop)}, "client_order_id": cid}
        # A timeout may mean the order was accepted. Recover by the same durable client ID.
        try:
            return self.request("POST", "/v2/orders", json=body)
        except httpx.HTTPStatusError as e:
            if e.response.status_code>=500 or e.response.status_code in (409,429):
                found=self.by_client_id(cid)
                if found:return found
            raise
        except (httpx.TimeoutException, httpx.NetworkError):
            found = self.by_client_id(cid)
            if found:
                return found
            raise

    def market(self, symbol, qty, side, cid):
        """Day market order for a portfolio sleeve (no bracket: sleeves hold overnight)."""
        if os.environ.get("MODE", "paper") != "paper":
            raise ValueError("Orders are disabled in record_only")
        body = {"symbol": symbol, "qty": str(int(qty)), "side": side, "type": "market", "time_in_force": "day",
                "extended_hours": False, "client_order_id": cid}
        try:
            return self.request("POST", "/v2/orders", json=body)
        except httpx.HTTPStatusError as e:
            if e.response.status_code >= 500 or e.response.status_code in (409, 429):
                found = self.by_client_id(cid)
                if found:
                    return found
            raise
        except (httpx.TimeoutException, httpx.NetworkError):
            found = self.by_client_id(cid)
            if found:
                return found
            raise

    def submit(self, body: dict):
        """Any order body (owner-placed trades); recovered by its durable client ID on uncertain failures."""
        if os.environ.get("MODE", "paper") != "paper":
            raise ValueError("Orders are disabled in record_only")
        cid = body["client_order_id"]
        try:
            return self.request("POST", "/v2/orders", json=body)
        except httpx.HTTPStatusError as e:
            if e.response.status_code >= 500 or e.response.status_code in (409, 429):
                found = self.by_client_id(cid)
                if found:
                    return found
            raise
        except (httpx.TimeoutException, httpx.NetworkError):
            found = self.by_client_id(cid)
            if found:
                return found
            raise

    def cancel(self, oid):
        return self.request("DELETE", f"/v2/orders/{oid}")

    def cancel_all(self):
        return self.request("DELETE", "/v2/orders")

    def close_position(self, symbol):
        return self.request("DELETE", f"/v2/positions/{symbol}")

    def flatten(self):
        return self.request("DELETE", "/v2/positions", params={"cancel_orders": "true"})

    def snapshots(self, symbols):
        return self.request("GET", "/v2/stocks/snapshots", data=True,
                            params={"symbols": ",".join(symbols), "feed": self.feed})

    def history_bars(self, symbols, timeframe, start, end, adjustment="split"):
        """Completed bars from the history feed. Falls back to the live feed when the account can't read SIP."""
        if self.history_feed == "sip":
            end = min(end, time.time() - SIP_DELAY_S)
            if end <= start:
                return {s: [] for s in symbols}
        try:
            return self.bars(symbols, timeframe, start, end, adjustment, feed=self.history_feed)
        except httpx.HTTPStatusError as e:
            if e.response.status_code not in (401, 403, 422) or self.history_feed == self.feed:
                raise
            log.warning("History feed %s refused (%s); using %s", self.history_feed, e.response.status_code, self.feed)
            self.history_feed = self.feed
            return self.bars(symbols, timeframe, start, end, adjustment)

    def bars(self, symbols, timeframe, start, end, adjustment="raw", feed=None):
        out = {s: [] for s in symbols}
        params = {"symbols": ",".join(symbols), "timeframe": timeframe, "start": iso(start), "end": iso(end),
                  "feed": feed or self.feed, "adjustment": adjustment, "limit": 10000, "sort": "asc"}
        while True:
            page = self.request("GET", "/v2/stocks/bars", data=True, params=params)
            for s, rows in page.get("bars", {}).items():
                out.setdefault(s, []).extend(rows)
            token = page.get("next_page_token")
            if not token:
                return out
            params["page_token"] = token

    def news(self, symbols, start, end):
        result = []
        params = {"symbols": ",".join(symbols), "start": iso(start), "end": iso(end), "sort": "asc",
                  "limit": 50, "include_content": "true", "exclude_contentless": "false"}
        while True:
            page = self.request("GET", "/v1beta1/news", data=True, params=params)
            result.extend(page.get("news", []))
            token = page.get("next_page_token")
            if not token:
                return result
            params["page_token"] = token

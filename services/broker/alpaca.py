"""Paper-only Alpaca HTTP adapter. No live endpoint or live credential path."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

import httpx

PAPER_URL = "https://paper-api.alpaca.markets"
DATA_URL = "https://data.alpaca.markets"


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
        key, secret = os.environ.get("ALPACA_KEY_ID"), os.environ.get("ALPACA_SECRET_KEY")
        if not key or not secret:
            raise ValueError("Missing Alpaca paper credentials in secrets/broker.env")
        self.feed = os.environ.get("ALPACA_DATA_FEED", "iex")
        self.http = client or httpx.Client(headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret},
                                           timeout=15, follow_redirects=False)

    def request(self, method, path, *, data=False, **kw):
        r = self.http.request(method, (DATA_URL if data else PAPER_URL) + path, **kw)
        r.raise_for_status()
        return r.json() if r.content else None

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

    def bars(self, symbols, timeframe, start, end):
        out = {s: [] for s in symbols}
        params = {"symbols": ",".join(symbols), "timeframe": timeframe, "start": iso(start), "end": iso(end),
                  "feed": self.feed, "adjustment": "raw", "limit": 10000, "sort": "asc"}
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
                  "limit": 50, "include_content": "false", "exclude_contentless": "false"}
        while True:
            page = self.request("GET", "/v1beta1/news", data=True, params=params)
            result.extend(page.get("news", []))
            token = page.get("next_page_token")
            if not token:
                return result
            params["page_token"] = token

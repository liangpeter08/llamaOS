"""Deterministic market + news simulator for the prototype.

Generates 1-minute bars and a news stream for fictional tickers. Events move prices with
catalyst-specific behaviour (fundamental news tends to continue, analyst notes tend to fade)
so the pipeline, strategies and Idea Lab have something real to find. Ground truth stays
inside this module: models only ever see headline text.
"""
from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass, field
from datetime import date, time

from . import timeutil as T

# symbol: (name, sector, start price, avg shares/day, daily vol)
SYMBOLS: dict[str, tuple[str, str, float, float, float]] = {
    "ACMX": ("Acme Matrix", "Industrials", 48.0, 2.5e6, 0.022),
    "BOLT": ("Bolt Robotics", "Technology", 112.0, 3.0e6, 0.030),
    "CRUX": ("Crux Therapeutics", "Healthcare", 23.0, 4.5e6, 0.040),
    "DYNA": ("Dynamo Energy", "Energy", 67.0, 1.8e6, 0.020),
    "ECHX": ("Echo Exchange Systems", "Financials", 155.0, 1.2e6, 0.017),
    "FLUX": ("Flux Semiconductor", "Technology", 89.0, 5.0e6, 0.032),
    "GRVY": ("Gravity Foods", "Consumer Staples", 36.0, 2.2e6, 0.015),
    "HALO": ("Halo Networks", "Communication", 14.0, 6.0e6, 0.035),
}
SOURCES = ("SimWire", "SimNews", "MarketSim")

CATALYST_W = {
    "earnings": 0.20, "guidance": 0.10, "analyst_upgrade": 0.14, "analyst_downgrade": 0.11,
    "product": 0.12, "contract": 0.10, "m_and_a": 0.04, "legal": 0.08, "other": 0.11,
}
# (min, max) absolute impact
IMPACT = {
    "earnings": (0.025, 0.08), "guidance": (0.02, 0.07), "analyst_upgrade": (0.01, 0.03),
    "analyst_downgrade": (0.01, 0.03), "product": (0.012, 0.045), "contract": (0.012, 0.04),
    "m_and_a": (0.08, 0.20), "legal": (0.02, 0.06), "other": (0.0, 0.008),
}

HEADLINES = {
    ("earnings", 1): [
        "{name} Q{q} EPS ${eps:.2f} beats ${est:.2f} estimate; revenue up {pct}% year over year",
        "{name} posts record quarterly results, beats on revenue and earnings",
        "{name} quarterly earnings top expectations as margins expand",
    ],
    ("earnings", -1): [
        "{name} Q{q} EPS ${eps:.2f} misses ${est:.2f} estimate; revenue falls {pct}%",
        "{name} quarterly results miss expectations as costs rise",
        "{name} earnings fall short; revenue declines {pct}% year over year",
    ],
    ("guidance", 1): [
        "{name} raises full-year guidance above consensus",
        "{name} lifts outlook, sees revenue growth of {pct}% next year",
    ],
    ("guidance", -1): [
        "{name} cuts full-year guidance, cites weaker demand",
        "{name} lowers outlook below consensus; shares indicated lower",
    ],
    ("analyst_upgrade", 1): [
        "{broker} upgrades {name} to Buy, raises price target to ${pt}",
        "{name} upgraded to Outperform at {broker}",
    ],
    ("analyst_downgrade", -1): [
        "{broker} downgrades {name} to Sell, cuts price target to ${pt}",
        "{name} downgraded to Underperform at {broker}",
    ],
    ("product", 1): [
        "{name} launches next-generation platform, early orders strong",
        "{name} wins regulatory approval for flagship product",
        "{name} unveils new product line, expands into {sector2}",
    ],
    ("product", -1): [
        "{name} delays flagship product launch to next year",
        "{name} announces recall of product line after defects found",
    ],
    ("contract", 1): [
        "{name} awarded ${amt}M multi-year contract",
        "{name} signs supply deal worth ${amt}M with major customer",
    ],
    ("contract", -1): [
        "{name} loses key contract to rival; ${amt}M in annual revenue at risk",
    ],
    ("m_and_a", 1): [
        "{name} to be acquired for ${pt} per share in cash",
        "{name} agrees to buyout at ${pt} per share, a {pct}% premium",
    ],
    ("m_and_a", -1): [
        "{name} acquisition talks collapse; deal terminated",
    ],
    ("legal", -1): [
        "SEC opens investigation into {name} accounting practices",
        "{name} hit with class-action lawsuit over disclosures",
        "{name} faces regulator probe; files late quarterly report",
    ],
    ("legal", 1): [
        "{name} wins dismissal of patent lawsuit",
    ],
    ("other", 1): ["{name} to present at industry conference next week", "{name} names new chief operating officer"],
    ("other", -1): ["{name} chief financial officer to step down", "{name} announces $50M share offering"],
}
MIXED = [
    "{name} beats on Q{q} EPS but cuts full-year guidance",
    "{name} revenue misses estimates; raises buyback and dividend",
]
BROKERS = ("Northgate", "Halberd & Co", "Kestrel Securities", "Bluewater Capital")
MACRO = [
    "Fed minutes show officials divided on pace of rate cuts",
    "CPI rises {pct2}% in latest month, in line with forecasts",
    "Jobs report: payrolls rise more than expected; unemployment steady",
    "Treasury yields climb ahead of policy decision",
]
ROUNDUP = [
    "Premarket movers: {syms}",
    "Stocks to watch today: {syms}",
    "Midday recap: biggest movers include {syms}",
]


@dataclass
class SimNews:
    ext_id: str
    ts: int
    symbols: list[str]
    headline: str
    summary: str
    source: str


@dataclass
class Event:
    symbol: str
    catalyst: str
    direction: int
    impact: float
    ts: int
    premarket: bool


@dataclass
class SimDay:
    day: date
    bars: dict[str, list[tuple]] = field(default_factory=dict)  # sym -> [(ts,o,h,l,c,v)]
    daily: dict[str, tuple] = field(default_factory=dict)  # sym -> (o,h,l,c,v)
    news: list[SimNews] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)  # ground truth; not exposed to models


def _rng(*parts) -> random.Random:
    return random.Random("|".join(str(p) for p in parts))


def _pick_catalyst(r: random.Random) -> str:
    x, acc = r.random(), 0.0
    for k, w in CATALYST_W.items():
        acc += w
        if x <= acc:
            return k
    return "other"


def _vol_profile() -> list[float]:
    from dsl.evaluator import VOLUME_W  # single source of truth for the intraday curve
    return VOLUME_W


def _events_for(day: date, sym: str, seed: int) -> list[Event]:
    r = _rng(seed, day, sym, "events")
    out = []
    if r.random() > 0.35:
        return out
    cat = _pick_catalyst(r)
    if cat == "analyst_upgrade":
        d = 1
    elif cat == "analyst_downgrade":
        d = -1
    elif cat == "legal":
        d = -1 if r.random() < 0.85 else 1
    elif cat == "m_and_a":
        d = 1 if r.random() < 0.85 else -1
    else:
        d = 1 if r.random() < 0.55 else -1
    lo, hi = IMPACT[cat]
    impact = r.uniform(lo, hi)
    premarket = r.random() < 0.65 or cat == "earnings"
    if premarket:
        t = time(7, 0)
        ts = T.ts_at(day, t) + r.randrange(0, 140) * 60
    else:
        ts = T.ts_at(day, time(9, 45)) + r.randrange(0, 285) * 60
    out.append(Event(sym, cat, d, impact, ts, premarket))
    return out


def _headline(r: random.Random, ev: Event, name: str, sector: str, price: float) -> tuple[str, str]:
    q = (T.et(ev.ts).month - 1) // 3 + 1
    est = round(r.uniform(0.4, 3.0), 2)
    eps = est * (1 + ev.direction * r.uniform(0.05, 0.25))
    fmt = dict(
        name=name, q=q, eps=eps, est=est, pct=r.randrange(3, 30), broker=r.choice(BROKERS),
        pt=round(price * (1 + ev.direction * r.uniform(0.1, 0.35))), amt=r.randrange(40, 900),
        sector2=r.choice(["services", "Europe", "Asia", "data centers", "consumer markets"]),
    )
    if ev.catalyst == "earnings" and r.random() < 0.15:
        text = r.choice(MIXED).format(**fmt)
    else:
        options = HEADLINES.get((ev.catalyst, ev.direction)) or HEADLINES[(ev.catalyst, -ev.direction)]
        text = r.choice(options).format(**fmt)
    summary = f"{text}. ({sector}) Shares of {name} are in focus."
    return text, summary


def simulate(days: list[date], seed: int = 7) -> list[SimDay]:
    """Simulate a contiguous chain of sessions. Prices carry over from day to day."""
    prof = _vol_profile()
    last_close = {s: v[2] for s, v in SYMBOLS.items()}
    out = []
    for day in days:
        sd = SimDay(day)
        r_day = _rng(seed, day, "market")
        market_move = r_day.gauss(0, 0.008)
        for sym, (name, sector, _, adv_sh, dvol) in SYMBOLS.items():
            r = _rng(seed, day, sym, "path")
            evs = _events_for(day, sym, seed)
            sd.events.extend(evs)
            pc = last_close[sym]
            sigma_m = dvol / math.sqrt(T.BARS_PER_DAY)
            drift = [0.0] * T.BARS_PER_DAY
            vmult = [1.0] * T.BARS_PER_DAY
            gap = r.gauss(0, 0.35 * dvol) + market_move * 0.6
            for ev in evs:
                sgn, imp = ev.direction, ev.impact
                fade = ev.catalyst in ("analyst_upgrade", "analyst_downgrade")
                if ev.premarket:
                    g = sgn * imp * (0.75 if fade else 0.5)
                    gap += g
                    # continuation for fundamental news, fade for analyst notes
                    cont = -0.55 * g if fade else sgn * imp * r.uniform(0.05, 0.75)
                    if ev.catalyst == "m_and_a" and sgn > 0:
                        cont = 0.0
                    span = r.randrange(45, 150)
                    for m in range(5, 5 + span):
                        drift[m] += cont / span
                    for m in range(T.BARS_PER_DAY):
                        vmult[m] *= 1 + (2.0 + 40 * imp) * math.exp(-m / 120) + 0.6 * 40 * imp / 10
                else:
                    m0 = (ev.ts - T.open_ts(day)) // 60
                    jump = sgn * imp * 0.45
                    cont = sgn * imp * (r.uniform(-0.2, 0.2) if fade else r.uniform(0.2, 0.5))
                    for m in range(m0, min(m0 + 3, T.BARS_PER_DAY)):
                        drift[m] += jump / 3
                    for m in range(m0 + 3, min(m0 + 63, T.BARS_PER_DAY)):
                        drift[m] += cont / 60
                    for m in range(m0, T.BARS_PER_DAY):
                        vmult[m] *= 1 + (3.0 + 40 * imp) * math.exp(-(m - m0) / 40)
                    if ev.catalyst == "m_and_a" and sgn > 0:
                        for m in range(m0 + 3, T.BARS_PER_DAY):
                            drift[m] = 0.0
            open_ = pc * math.exp(gap)
            p = open_
            bars = []
            for m in range(T.BARS_PER_DAY):
                ts = T.open_ts(day) + m * 60
                vol_m = sigma_m * (1.8 if m < 15 else 1.0) * (1.3 if m > 375 else 1.0)
                o = p if m else open_
                path = [o]
                step = drift[m] / 4
                for _ in range(4):
                    path.append(path[-1] * math.exp(step + r.gauss(0, vol_m / 2)))
                c = path[-1]
                h = max(path) * (1 + abs(r.gauss(0, vol_m / 4)))
                l = min(path) * (1 - abs(r.gauss(0, vol_m / 4)))
                v = max(100, int(adv_sh * prof[m] * vmult[m] * math.exp(r.gauss(0, 0.35))))
                bars.append((ts, round(o, 4), round(h, 4), round(l, 4), round(c, 4), v))
                p = c
            sd.bars[sym] = bars
            sd.daily[sym] = (
                bars[0][1], max(b[2] for b in bars), min(b[3] for b in bars), bars[-1][4], sum(b[5] for b in bars)
            )
            last_close[sym] = bars[-1][4]

            # news text
            for k, ev in enumerate(evs):
                rn = _rng(seed, day, sym, "news", k)
                head, summ = _headline(rn, ev, name, sector, pc)
                src = rn.choice(SOURCES)
                nid = hashlib.sha1(f"{day}-{sym}-{k}-{src}".encode()).hexdigest()[:12]
                sd.news.append(SimNews(nid, ev.ts, [sym], head, summ, src))
                if rn.random() < 0.3:  # syndicated duplicate a few minutes later
                    src2 = rn.choice([s for s in SOURCES if s != src])
                    sd.news.append(SimNews(nid + "d", ev.ts + rn.randrange(1, 6) * 60, [sym],
                                           head.replace(";", ",") + " ", summ, src2))

        r = _rng(seed, day, "misc")
        for k in range(r.randrange(1, 3)):
            ts = T.ts_at(day, time(8, 30)) + r.randrange(0, 400) * 60
            text = r.choice(MACRO).format(pct2=round(r.uniform(0.1, 0.5), 1))
            sd.news.append(SimNews(f"{day}-macro-{k}", ts, [], text, text, r.choice(SOURCES)))
        movers = sorted({e.symbol for e in sd.events}) or ["ACMX", "BOLT"]
        for k, tmpl in enumerate(ROUNDUP[:2]):
            ts = T.ts_at(day, time(8, 45 + k * 5)) if k == 0 else T.ts_at(day, time(12, 0))
            sd.news.append(SimNews(f"{day}-roundup-{k}", ts, movers[:4], tmpl.format(syms=", ".join(movers[:4])),
                                   "A roundup of today's notable movers.", r.choice(SOURCES)))
        sd.news.sort(key=lambda n: n.ts)
        out.append(sd)
    return out


def chain_for(session: date, history_days: int, warmup_days: int = 25) -> list[date]:
    """Dates from the start of warmup through `session` (inclusive)."""
    return T.weekdays_before(session, history_days + warmup_days) + [session]


def to_daily_rows(sd: SimDay) -> list[tuple]:
    return [(sym, sd.day.isoformat(), *v) for sym, v in sd.daily.items()]


def symbol_info(sym: str) -> dict:
    name, sector, *_ = SYMBOLS[sym]
    return {"symbol": sym, "name": name, "sector": sector}


def prior_dates(d: date, n: int) -> list[date]:
    return T.weekdays_before(d, n)

"""Read Polymarket event pages through ScrapingBee and return typed records.

Polymarket's site is a Next.js app router build. It does not ship a
__NEXT_DATA__ blob. The event data lives in the React Server Components
stream, as string chunks pushed through `self.__next_f.push([1, "..."])`
script tags. This module joins those chunks, scans them for objects that
carry a `markets` list with `outcomePrices`, and turns each one into an
`Event` with its `Market` rows.

A plain request (render_js=false, classic proxy) is enough: 1 credit per page.

    export SCRAPINGBEE_API_KEY=...
    python polymarket_client.py event fed-decision-in-december-20260729232808632
    python polymarket_client.py listing predictions/fed --tag fed --full --csv fed.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

import requests

API_URL = "https://app.scrapingbee.com/api/v1/"
SITE = "https://polymarket.com"
RETRY_STATUSES = {500, 502, 503, 504}  # ScrapingBee does not bill a 500

FLIGHT_RE = re.compile(r"self\.__next_f\.push\((\[.*?\])\)</script>", re.S)
JSONLD_RE = re.compile(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', re.S)


# --------------------------------------------------------------------------
# records
# --------------------------------------------------------------------------

@dataclass
class Market:
    event_slug: str
    market_id: int
    slug: str
    question: str
    label: str                 # groupItemTitle, e.g. "25 bps increase"
    outcomes: list[str]
    prices: list[float]        # same order as outcomes, 0..1
    best_bid: float | None
    best_ask: float | None
    spread: float | None
    volume: float
    day_change: float | None   # oneDayPriceChange
    end_date: str | None
    active: bool
    closed: bool
    accepting_orders: bool
    condition_id: str | None

    @property
    def yes_price(self) -> float | None:
        """Price of the first outcome. On Yes/No markets that is Yes."""
        return self.prices[0] if self.prices else None


@dataclass
class Event:
    event_id: str
    slug: str
    title: str
    url: str
    start_date: str | None
    end_date: str | None
    volume: float
    volume_24h: float | None
    neg_risk: bool
    closed: bool
    tags: list[str]
    markets: list[Market] = field(default_factory=list)
    source: str = ""           # "event" (full page) or "listing" (trimmed)


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

class ScrapingBeeError(RuntimeError):
    pass


class Client:
    def __init__(self, api_key: str | None = None, retries: int = 3, timeout: int = 90):
        self.api_key = api_key or os.environ.get("SCRAPINGBEE_API_KEY", "")
        if not self.api_key:
            raise SystemExit("Set SCRAPINGBEE_API_KEY first.")
        self.retries = retries
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {self.api_key}"
        self.credits = 0

    def get(self, url: str, **params: Any) -> str:
        query = {"url": url, "render_js": "false", **params}
        last = None
        for attempt in range(1, self.retries + 1):
            resp = self.session.get(API_URL, params=query, timeout=self.timeout)
            if resp.status_code == 200:
                self.credits += int(resp.headers.get("spb-cost", 0) or 0)
                return resp.text
            last = f"HTTP {resp.status_code}: {resp.text[:200]}"
            if resp.status_code not in RETRY_STATUSES:
                break
            time.sleep(2 * attempt)
        raise ScrapingBeeError(f"{url} failed after {attempt} attempt(s), {last}")


# --------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------

def flight_payload(html: str) -> str:
    """Join the RSC string chunks that Next.js streams into the page."""
    parts = []
    for match in FLIGHT_RE.finditer(html):
        try:
            chunk = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if len(chunk) > 1 and isinstance(chunk[1], str):
            parts.append(chunk[1])
    return "".join(parts)


def raw_events(payload: str) -> dict[str, dict]:
    """Every object in the payload that looks like a Polymarket event."""
    decoder = json.JSONDecoder()
    found: dict[str, dict] = {}
    for match in re.finditer(r'\{"id":', payload):
        try:
            obj, _ = decoder.raw_decode(payload, match.start())
        except json.JSONDecodeError:
            continue
        markets = obj.get("markets") if isinstance(obj, dict) else None
        if (
            isinstance(markets, list)
            and markets
            and isinstance(markets[0], dict)
            and "outcomePrices" in markets[0]
        ):
            found.setdefault(obj["slug"], obj)
    return found


def _as_list(value: Any) -> list:
    # the page sends real lists; the Gamma API sends JSON encoded strings
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return []
    return list(value or [])


DASHES = str.maketrans({"\u2013": "-", "\u2014": "-"})


def _text(value: Any) -> str:
    """Titles use typographic dashes ("Sep\u2013Dec"); flatten them for CSV."""
    return str(value or "").translate(DASHES)


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_market(event_slug: str, m: dict) -> Market:
    return Market(
        event_slug=event_slug,
        market_id=int(m["id"]),
        slug=m.get("slug", ""),
        question=_text(m.get("question")),
        label=_text(m.get("groupItemTitle") or m.get("question")),
        outcomes=[str(o) for o in _as_list(m.get("outcomes"))],
        prices=[float(p) for p in _as_list(m.get("outcomePrices"))],
        best_bid=_num(m.get("bestBid")),
        best_ask=_num(m.get("bestAsk")),
        spread=_num(m.get("spread")),
        volume=_num(m.get("volume")) or 0.0,
        day_change=_num(m.get("oneDayPriceChange")),
        end_date=m.get("endDate"),
        active=bool(m.get("active")),
        closed=bool(m.get("closed")),
        accepting_orders=bool(m.get("acceptingOrders")),
        condition_id=m.get("conditionId"),
    )


def parse_event(raw: dict, source: str) -> Event:
    slug = raw["slug"]
    return Event(
        event_id=str(raw.get("id")),
        slug=slug,
        title=_text(raw.get("title")),
        url=f"{SITE}/event/{slug}",
        start_date=raw.get("startDate"),
        end_date=raw.get("endDate"),
        volume=_num(raw.get("volume")) or 0.0,
        volume_24h=_num(raw.get("volume24hr")),
        neg_risk=bool(raw.get("negRisk")),
        closed=bool(raw.get("closed")),
        tags=[t.get("slug", "") for t in raw.get("tags") or [] if isinstance(t, dict)],
        markets=[parse_market(slug, m) for m in raw["markets"]],
        source=source,
    )


def json_ld(html: str) -> list[dict]:
    blocks = []
    for match in JSONLD_RE.finditer(html):
        try:
            blocks.append(json.loads(match.group(1)))
        except json.JSONDecodeError:
            pass
    return blocks


# --------------------------------------------------------------------------
# high level
# --------------------------------------------------------------------------

def get_event(client: Client, slug: str) -> tuple[Event, list[dict]]:
    """One event page: every market of the event, plus its JSON-LD blocks."""
    html = client.get(f"{SITE}/event/{slug}")
    events = raw_events(flight_payload(html))
    if slug not in events:
        raise LookupError(f"no event object for {slug!r} in the page payload")
    return parse_event(events[slug], "event"), json_ld(html)


def list_events(client: Client, path: str, tag: str | None = None) -> list[Event]:
    """Events embedded in a listing page such as the home page or predictions/fed.

    Listing pages trim each event to its top markets (4 or 5 in testing), and
    they mix in unrelated events, so filter by tag and re-read event pages
    when you need every outcome.
    """
    html = client.get(f"{SITE}/{path.strip('/')}")
    events = [parse_event(raw, "listing") for raw in raw_events(flight_payload(html)).values()]
    if tag:
        events = [e for e in events if tag in e.tags]
    return events


def market_rows(events: Iterable[Event]) -> list[dict]:
    rows = []
    for ev in events:
        for m in ev.markets:
            row = {
                "event_slug": ev.slug,
                "event_title": ev.title,
                "event_end": ev.end_date,
                "event_volume": ev.volume,
                "neg_risk": ev.neg_risk,
                **{k: v for k, v in asdict(m).items() if k not in ("event_slug", "outcomes", "prices")},
                "outcomes": "|".join(m.outcomes),
                "prices": "|".join(f"{p:g}" for p in m.prices),
                "yes_price": m.yes_price,
                "source": ev.source,
            }
            rows.append(row)
    return rows


def write_csv(rows: list[dict], path: str) -> None:
    if not rows:
        return
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def print_event(ev: Event) -> None:
    print(f"{ev.title}  [{ev.slug}]")
    print(f"  ends {ev.end_date}  volume ${ev.volume:,.0f}  markets {len(ev.markets)}  ({ev.source})")
    for m in sorted(ev.markets, key=lambda x: -(x.yes_price or 0)):
        yes = f"{m.yes_price * 100:5.1f}%" if m.yes_price is not None else "   n/a"
        print(f"    {yes}  {m.label[:48]:<48}  vol ${m.volume:,.0f}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_ev = sub.add_parser("event", help="read one /event/<slug> page")
    p_ev.add_argument("slug")

    p_ls = sub.add_parser("listing", help="read a listing page, e.g. predictions/fed")
    p_ls.add_argument("path")
    p_ls.add_argument("--tag", help="keep events carrying this tag slug")
    p_ls.add_argument("--full", action="store_true", help="re-read each event page for all markets")
    p_ls.add_argument("--limit", type=int, default=10)

    for p in (p_ev, p_ls):
        p.add_argument("--csv", help="write one row per market")
        p.add_argument("--json", help="write the event records as JSON")

    args = ap.parse_args(argv)
    client = Client()

    if args.cmd == "event":
        ev, ld = get_event(client, args.slug)
        events = [ev]
        faq = next((b for b in ld if b.get("@type") == "FAQPage"), None)
        if faq:
            print("FAQ JSON-LD:", faq["mainEntity"][0]["acceptedAnswer"]["text"][:160], "...")
    else:
        events = list_events(client, args.path, args.tag)[: args.limit]
        if args.full:
            full = []
            for ev in events:
                try:
                    full.append(get_event(client, ev.slug)[0])
                except (LookupError, ScrapingBeeError) as exc:
                    print(f"warning: {exc}", file=sys.stderr)
                    full.append(ev)
            events = full

    for ev in events:
        print_event(ev)
    if args.csv:
        write_csv(market_rows(events), args.csv)
        print(f"wrote {args.csv}")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump([asdict(e) for e in events], fh, indent=2)
        print(f"wrote {args.json}")
    print(f"credits used: {client.credits}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

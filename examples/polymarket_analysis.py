"""Turn scraped Polymarket events into a pandas table and a short report.

Reads one listing page (for example predictions/fed), keeps the events that
carry a tag, re-reads each event page so every outcome is present, and then:

  * builds one row per outcome market with its Yes price as implied probability
  * sums the Yes prices of each multi-outcome event (the "book" total)
  * ranks events by volume and shows the leading outcome of each
  * writes everything to CSV

    export SCRAPINGBEE_API_KEY=...
    python polymarket_analysis.py predictions/fed --tag fed --limit 8 --out fed_markets.csv

Cost: 1 credit for the listing plus 1 credit per event page.
The numbers are a snapshot of public prices, not a forecast and not advice.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

import pandas as pd

from polymarket_client import Client, ScrapingBeeError, get_event, list_events, market_rows


def collect(path: str, tag: str | None, limit: int) -> tuple[pd.DataFrame, int, int]:
    client = Client()
    listed = list_events(client, path, tag)[:limit]
    trimmed = 0
    events = []
    for ev in listed:
        try:
            full, _ = get_event(client, ev.slug)
        except (LookupError, ScrapingBeeError) as exc:
            print(f"warning: kept the trimmed listing copy of {ev.slug}: {exc}", file=sys.stderr)
            events.append(ev)
            continue
        if len(full.markets) > len(ev.markets):
            trimmed += 1
        events.append(full)
    df = pd.DataFrame(market_rows(events))
    return df, trimmed, client.credits


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    now = pd.Timestamp(datetime.now(timezone.utc))
    df = df.copy()
    df["implied_pct"] = (df["yes_price"] * 100).round(1)
    df["event_end"] = pd.to_datetime(df["event_end"], utc=True, errors="coerce")
    df["days_left"] = (df["event_end"] - now).dt.days
    df["volume_share_pct"] = (
        df["volume"] / df.groupby("event_slug")["volume"].transform("sum") * 100
    ).round(1)
    return df


def report(df: pd.DataFrame) -> None:
    open_df = df[~df["closed"]]

    per_event = (
        open_df.groupby(["event_slug", "event_title"], as_index=False)
        .agg(
            markets=("market_id", "count"),
            volume=("event_volume", "first"),
            days_left=("days_left", "first"),
            book_total=("yes_price", "sum"),
            neg_risk=("neg_risk", "first"),
        )
        .sort_values("volume", ascending=False)
    )
    leaders = open_df.loc[open_df.groupby("event_slug")["yes_price"].idxmax(), ["event_slug", "label", "implied_pct"]]
    per_event = per_event.merge(leaders, on="event_slug")

    pd.set_option("display.width", 140)
    pd.set_option("display.max_colwidth", 44)
    print("\nEvents by volume (open markets only)")
    print(
        per_event[["event_title", "markets", "volume", "days_left", "label", "implied_pct"]]
        .rename(columns={"label": "leader", "implied_pct": "leader_pct"})
        .to_string(index=False, formatters={"volume": "${:,.0f}".format})
    )

    # negRisk events hold mutually exclusive outcomes, so their Yes prices
    # should add up to about 1. Other multi-market events are not exclusive.
    books = per_event[per_event["neg_risk"] & (per_event["markets"] > 1)]
    if not books.empty:
        print("\nExclusive (negRisk) books: sum of Yes prices, 1.000 = outcomes add to 100%")
        print(books[["event_title", "markets", "book_total"]].to_string(index=False, formatters={"book_total": "{:.3f}".format}))

    # "by <date>" events are cumulative: a later deadline can not be less likely
    ladders = open_df[~open_df["neg_risk"]].groupby("event_slug").filter(lambda g: len(g) > 2)
    for slug, g in ladders.groupby("event_slug"):
        g = g.sort_values("end_date")
        steps = g["yes_price"].diff().dropna()
        verdict = "monotonic" if (steps >= -0.005).all() else "NOT monotonic"
        print(f"\nDeadline ladder, {g['event_title'].iloc[0]} ({verdict})")
        print(g[["label", "end_date", "implied_pct"]].to_string(index=False))

    movers = open_df.dropna(subset=["day_change"]).reindex(
        open_df["day_change"].abs().sort_values(ascending=False).index
    ).head(5)
    print("\nLargest 24h price moves")
    print(movers[["event_title", "label", "implied_pct", "day_change"]].to_string(index=False))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Polymarket listing to pandas report")
    ap.add_argument("path", help="listing page path, e.g. predictions/fed or crypto")
    ap.add_argument("--tag", help="keep events carrying this tag slug")
    ap.add_argument("--limit", type=int, default=8, help="max events to re-read")
    ap.add_argument("--out", default="polymarket_markets.csv")
    args = ap.parse_args(argv)

    df, trimmed, credits = collect(args.path, args.tag, args.limit)
    if df.empty:
        print("no events found on that page", file=sys.stderr)
        return 1
    df = enrich(df)
    report(df)
    df.to_csv(args.out, index=False)
    print(f"\n{len(df)} market rows from {df['event_slug'].nunique()} events -> {args.out}")
    print(f"{trimmed} event(s) had more markets on the event page than on the listing")
    print(f"credits used: {credits}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

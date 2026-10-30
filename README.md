# Polymarket API

<p align="center">
  <a href="https://www.scrapingbee.com/">
    <img src="https://github.com/user-attachments/assets/f665721a-f488-4d56-8175-d5f4f3b4e112" alt="polymarket-api" />
  </a>
</p>



[![checks](https://github.com/ScrapingBee/polymarket-api/workflows/checks/badge.svg)](https://github.com/ScrapingBee/polymarket-api/actions)
[![license](https://img.shields.io/github/license/ScrapingBee/polymarket-api.svg)](LICENSE)

This repo treats polymarket.com as a dataset. A [Polymarket API](https://www.scrapingbee.com/scrapers/polymarket-api/) call through ScrapingBee reads a public event page for 1 credit, and the Python code here turns what comes back into typed records, a pandas DataFrame and a CSV, then runs a few checks a data analyst would actually want: who leads each market, whether a multi-outcome book adds up to 100%, and whether a deadline ladder is priced consistently.

All numbers below were produced by the scripts in [`examples/`](examples) on 2026-10-29 against live pages. Prices on Polymarket move constantly, so yours will differ.

## What the page actually ships

Most guides for this site tell you to read `__NEXT_DATA__`. Polymarket no longer has one. The site is a Next.js app router build, so the data arrives as React Server Components chunks pushed from inline scripts:

```html
<script>self.__next_f.push([1,"1:\"$Sreact.fragment\"\n2:I[97441,[\"/_next/static/immutable/chunks/1pvzqcruq6397.js\"],\ ...
```

The Fed event page carried four of these pushes, about 644,000 characters once joined. Somewhere inside sits the full event object, escaped twice. Trimmed, from `/event/fed-decision-in-december-20260729232808632`:

```text
\"series\":[{\"id\":35,\"slug\":\"fomc\",\"title\":\"FOMC\",\"commentCount\":9715}],
\"markets\":[{\"id\":3215005,
  \"slug\":\"will-the-fed-decrease-interest-rates-by-50-bps-after-the-december-2026-meeting-20260729232808633\",
  \"question\":\"Will the Fed decrease interest rates by 50+ bps after the December 2026 meeting?\",
  \"groupItemTitle\":\"50+ bps decrease\",
  \"resolution_source\":\"https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm\",
  \"conditionId\":\"0xb9aee520bf51ac5f00b5c43e54ef9245bc9fcdef866ce8a33025f9a441cc651d\",
  \"outcomes\":[\"Yes\",\"No\"],\"outcomePrices\":[\"0.003\",\"0.998\"],
  \"bestAsk\":0.003,\"bestBid\":0.002,\"spread\":0.001, ...
```

Once decoded, an event has 30 keys (`title`, `slug`, `endDate`, `volume`, `volume24hr`, `negRisk`, `tags`, `markets` and more) and each market has 43 (`groupItemTitle`, `outcomes`, `outcomePrices`, `bestBid`, `bestAsk`, `spread`, `volume`, `oneDayPriceChange`, `endDate`, `closed`, `acceptingOrders`, `conditionId`, `clobTokenIds` and more).

The page also has seven `application/ld+json` blocks. Two are worth knowing about. An `Event` block gives the name and the dates (trimmed):

```json
{"@type":"Event","name":"Fed Decision in December?","startDate":"2026-07-30T00:37:14.000Z","endDate":"2026-12-10T04:59:00.000Z","eventStatus":"https://schema.org/EventScheduled"}
```

A `FAQPage` block restates the market in prose, prices included:

```text
"Fed Decision in December?" is a prediction market on Polymarket with 5 possible outcomes ...
The current leading outcome is "25 bps increase" at 78%, followed by "No change" at 21%.
```

There is also an `Article` headed "Market Context" with a paragraph of commentary on the event. Neither block replaces the market objects for numbers, but the FAQ text is handy as a cross check and the Article is the only place the page explains the market in words.

## Reading the payload in Python

[`examples/polymarket_client.py`](examples/polymarket_client.py) does the fetch, the decode and the typing. Three functions carry the work.

Join the chunks:

```python
FLIGHT_RE = re.compile(r"self\.__next_f\.push\((\[.*?\])\)</script>", re.S)

def flight_payload(html: str) -> str:
    parts = []
    for match in FLIGHT_RE.finditer(html):
        chunk = json.loads(match.group(1))
        if len(chunk) > 1 and isinstance(chunk[1], str):
            parts.append(chunk[1])
    return "".join(parts)
```

Find every object that looks like an event. `json.JSONDecoder.raw_decode` parses one value starting at an offset and ignores whatever follows, which is exactly what a stream full of objects needs:

```python
def raw_events(payload: str) -> dict[str, dict]:
    decoder = json.JSONDecoder()
    found = {}
    for match in re.finditer(r'\{"id":', payload):
        try:
            obj, _ = decoder.raw_decode(payload, match.start())
        except json.JSONDecodeError:
            continue
        markets = obj.get("markets") if isinstance(obj, dict) else None
        if isinstance(markets, list) and markets and "outcomePrices" in markets[0]:
            found.setdefault(obj["slug"], obj)
    return found
```

Then map each one onto `Event` and `Market` dataclasses. The request itself is plain HTML, no browser:

```python
resp = session.get(
    "https://app.scrapingbee.com/api/v1/",
    params={"url": "https://polymarket.com/event/anthropic-ipo-by", "render_js": "false"},
    headers={"Authorization": f"Bearer {api_key}"},
)
```

`render_js` defaults to true on ScrapingBee, which bills 5 credits. The data is in the server response, so turning it off brings the call to 1 credit with the same payload. The client retries 5xx answers, which ScrapingBee does not bill.

From the command line:

```bash
export SCRAPINGBEE_API_KEY=...
python examples/polymarket_client.py event fed-decision-in-december-20260729232808632 --csv fed.csv
```

```
Fed Decision in December?  [fed-decision-in-december-20260729232808632]
  ends 2026-12-10T04:59:00Z  volume $7,847,260  markets 5  (event)
     78.0%  25 bps increase                                   vol $1,864,613
     20.0%  No change                                         vol $1,877,905
      1.9%  50+ bps increase                                  vol $1,067,970
      0.5%  25 bps decrease                                   vol $1,587,080
      0.3%  50+ bps decrease                                  vol $1,449,692
```

## Polymarket data in pandas

[`examples/polymarket_analysis.py`](examples/polymarket_analysis.py) reads a listing page, keeps the events with a given tag, re-reads every event page, builds a DataFrame with one row per outcome market and writes it to CSV. The run below used the Fed category:

```bash
python examples/polymarket_analysis.py predictions/fed --tag fed --limit 8 --out fed_markets.csv
```

```
Events by volume (open markets only)
                                  event_title  markets      volume  days_left                         leader  leader_pct
              How many Fed rate cuts in 2026?       13 $55,040,443         63                      0 (0 bps)        97.1
                    Fed Decision in December?        5  $7,847,260         41                25 bps increase        78.0
What will the Fed rate be at the end of 2026?       15  $7,127,158         63                          4.25%        80.3
                          Fed rate cut by...?        6  $3,835,988        456              July 2027 Meeting        80.0
               Another Fed rate hike in 2026?        1  $1,214,773         41 Another Fed rate hike in 2026?        79.0
                     Fed Decision in January?        5  $1,022,096         90                      No change        68.0
                      Fed decisions (Sep-Dec)        3    $537,252         41                Hike-Pause-Hike        78.0
                      Fed decisions (Oct-Jan)        5     $88,433         90               Pause-Hike-Pause        61.0

Exclusive (negRisk) books: sum of Yes prices, 1.000 = outcomes add to 100%
                                  event_title  markets book_total
              How many Fed rate cuts in 2026?       13      0.993
                    Fed Decision in December?        5      1.007
What will the Fed rate be at the end of 2026?       15      1.088
                     Fed Decision in January?        5      1.020
                      Fed decisions (Sep-Dec)        3      1.021
                      Fed decisions (Oct-Jan)        5      1.066

Deadline ladder, Fed rate cut by...? (monotonic)
                label             end_date  implied_pct
December 2026 Meeting 2027-01-29T04:59:00Z          2.6
 January 2027 Meeting 2027-02-08T04:59:00Z         14.3
   March 2027 Meeting 2027-04-08T03:59:00Z         16.0
   April 2027 Meeting 2027-05-08T03:59:00Z         49.0
    June 2027 Meeting 2027-07-08T03:59:00Z         49.0
    July 2027 Meeting 2027-08-08T03:59:00Z         80.0

70 market rows from 8 events -> fed_markets.csv
5 event(s) had more markets on the event page than on the listing
credits used: 9
```

How to read it:

- **`negRisk` decides which sums mean anything.** On a `negRisk` event the outcomes exclude each other, so the Yes prices should add to roughly 1. "What will the Fed rate be at the end of 2026?" came to 1.088 across 15 outcomes, which says more about thin far-out strikes than about the leader. "Fed rate cut by...?" is not `negRisk`: its markets are cumulative deadlines, and their Yes prices summed to 2.109. Adding those up is a mistake the flag lets you avoid.
- **Deadline ladders should only climb.** A cut "by April" cannot be less likely than a cut "by March". The script sorts each non-exclusive event by market `endDate` and flags any step down. This one was clean.
- **Closed markets stay in the event.** "Fed rate cut by...?" still lists seven earlier meetings with `closed: true` and a price of 0. The report filters them; the CSV keeps them with the `closed` column so you can decide.

The CSV header, as written:

```
event_slug,event_title,event_end,event_volume,neg_risk,market_id,slug,question,label,best_bid,best_ask,spread,volume,day_change,end_date,active,closed,accepting_orders,condition_id,outcomes,prices,yes_price,source
```

## Listing pages are trimmed

The home page and the `/predictions/<topic>` pages embed event objects too, so one request returns many events. They are not complete:

| Page | Events found | Markets per event |
|---|---|---|
| `https://polymarket.com/` | 41 | Capped. The Fed December event showed 4 of its 5 |
| `/predictions/fed` | 20 | Capped at 5. "How many Fed rate cuts in 2026?" has 13 |
| `/event/<slug>` | 1 | All of them |

The Fed listing also mixed in events that had nothing to do with the Fed: three quarterly earnings markets and a college football game. Filtering on the event's tag slugs (`fed`, `fed-rates`, `fomc`) removes them. The analysis script re-reads each event page for this reason, and it reports how many events grew when it did (5 of 8 in the run above).

## Node.js version

[`examples/polymarket_event.js`](examples/polymarket_event.js) does the same decode without `raw_decode`: it scans for `{"id":`, checks that the next 400 characters contain the event slug, and walks braces to find where the object ends. Node 18 or later.

```bash
node examples/polymarket_event.js anthropic-ipo-by
```

```
Anthropic IPO by __?  ends 2027-01-01T04:59:00Z  volume $6,078,055  [1 credit]
   94.5%  December 31, 2026                         bid 0.944 / ask 0.945
   94.0%  December 15, 2026                         bid 0.93 / ask 0.94
   88.0%  November 30, 2026                         bid 0.87 / ask 0.88
   81.0%  November 25, 2026                         bid 0.8 / ask 0.81
   ...
    0.1%  October 31, 2026                          bid null / ask 0.001
    0.0%  June 30, 2026                             closed
```

Add `--json` to get the rows as JSON instead.

## Gamma, CLOB, or the page?

Polymarket documents its own public APIs at [docs.polymarket.com](https://docs.polymarket.com/getting-started/api). From that page: the Gamma API at `gamma-api.polymarket.com` is for discovering events and markets and their metadata, the CLOB API at `clob.polymarket.com` serves prices and order books and also handles orders, and the docs state that public market data needs no credentials. We called Gamma once with the same slug to compare:

| | Event page via ScrapingBee | Gamma `/events?slug=` |
|---|---|---|
| Keys per market | 43 | 89 (adds `liquidity`, `lastTradePrice`, `volume24hr`, `volume1wk`, `oneWeekPriceChange` and others) |
| `outcomePrices` | a JSON list, rounded (`0.78`) | a JSON encoded string, finer (`"0.775"`, between bid 0.77 and ask 0.78) |
| Freshness | Served from cache: `x-vercel-cache: HIT`, `s-maxage=300`; the home page we fetched was 46 seconds old | Live |
| Prose | FAQ and Market Context text in JSON-LD | None |
| Cost | 1 credit | Free |

So if Polymarket's numbers are all you need, the Gamma API returns more fields with less delay, and the client here will read its `outcomePrices` strings too (see `_as_list`). The page route earns its place when you want what a visitor saw: which events Polymarket chose to show under a topic, the prose summaries, or a capture that goes into the same ScrapingBee pipeline you already run for other sites. It is also a fallback that does not depend on an API's field names staying put.

## Costs

| Job | Calls | Credits |
|---|---|---|
| One event, all outcomes | 1 | 1 |
| A topic listing, trimmed markets | 1 | 1 |
| A topic listing plus 8 full event pages | 9 | 9 |
| The same with `render_js` left at its default | 9 | 45 |

Measured from the `spb-cost` header on every call. ScrapingBee keeps no cache of its own, so polling an event every minute is billed every minute. New accounts get 1,000 free credits; plan sizes are on the [pricing page](https://www.scrapingbee.com/pricing/).

## Scope and caveats

Only logged out, public pages are read. Portfolios, positions and anything tied to a wallet or an account are out of scope, and ScrapingBee's terms forbid scraping behind a login anyway. Polymarket's own terms govern use of its site. Prices are a snapshot of what traders were paying when the page was cached; nothing in this repo is a forecast, a recommendation, or advice to trade.

## Related

Other ScrapingBee scrapers that sit near this one: [odds API](https://www.scrapingbee.com/scrapers/odds-api/), [crypto API](https://www.scrapingbee.com/scrapers/crypto-api/), [CoinGecko API](https://www.scrapingbee.com/scrapers/coingecko-api/), [Yahoo Finance scraper](https://www.scrapingbee.com/scrapers/yahoo-finance-scraper-api/), [Nasdaq API](https://www.scrapingbee.com/scrapers/nasdaq-api/), [TradingView API](https://www.scrapingbee.com/scrapers/tradingview-api/), [crypto news API](https://www.scrapingbee.com/scrapers/crypto-news-api/).

Docs used here: [HTML API reference](https://www.scrapingbee.com/documentation/), [data extraction](https://www.scrapingbee.com/documentation/data-extraction/), [Markdown scraper](https://www.scrapingbee.com/features/markdown-scraper/) (handy if you want the Market Context text for an LLM).

## FAQ

**Does Polymarket have a public API?**
Yes. Its docs list the Gamma API for events and markets and the CLOB API for prices and order books, and public market data needs no key. This repo reads the public website instead, which adds the page's own summaries and topic curation.

**Where is the Polymarket data on the page if there is no `__NEXT_DATA__`?**
In the `self.__next_f.push` script chunks. Join their string parts and parse the objects that carry a `markets` list.

**Is there a Polymarket data API for Python and pandas?**
`polymarket_client.py` returns dataclasses and CSV rows, and `polymarket_analysis.py` loads them into pandas. Each event page costs 1 ScrapingBee credit.

**Why do my probabilities add up to more than 100%?**
On exclusive (`negRisk`) events a small excess is normal: 1.007 for the December Fed decision. On deadline events such as "Fed rate cut by...?" the outcomes overlap, so their sum means nothing.

**How fresh are scraped Polymarket prices?**
The event page is served from a cache with `s-maxage=300`. Expect seconds to minutes of delay. For live prices use the CLOB API that Polymarket documents.

## License

MIT. See [LICENSE](LICENSE).

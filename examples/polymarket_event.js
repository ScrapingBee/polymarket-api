// Print the outcome prices of one Polymarket event, read from the public page.
// Node 18+ (global fetch), no dependencies.
//
//   export SCRAPINGBEE_API_KEY=...
//   node polymarket_event.js anthropic-ipo-by
//   node polymarket_event.js anthropic-ipo-by --json > event.json

const API = "https://app.scrapingbee.com/api/v1/";
const KEY = process.env.SCRAPINGBEE_API_KEY;

async function fetchPage(url, tries = 3) {
  const qs = new URLSearchParams({ url, render_js: "false" });
  for (let i = 1; i <= tries; i++) {
    const res = await fetch(`${API}?${qs}`, { headers: { Authorization: `Bearer ${KEY}` } });
    if (res.ok) return { html: await res.text(), cost: Number(res.headers.get("spb-cost") || 0) };
    if (res.status < 500 || i === tries) throw new Error(`HTTP ${res.status}: ${(await res.text()).slice(0, 200)}`);
    await new Promise((r) => setTimeout(r, 2000 * i)); // 5xx answers are not billed
  }
}

// Next.js app router: the data rides in self.__next_f.push([1,"..."]) chunks
function flightPayload(html) {
  const re = /self\.__next_f\.push\((\[.*?\])\)<\/script>/gs;
  let out = "";
  for (const m of html.matchAll(re)) {
    try {
      const chunk = JSON.parse(m[1]);
      if (typeof chunk[1] === "string") out += chunk[1];
    } catch { /* skip non JSON chunk */ }
  }
  return out;
}

// JSON.parse needs exact bounds, so walk braces from each {"id": start
function readObject(text, start) {
  let depth = 0, inStr = false, esc = false;
  for (let i = start; i < text.length; i++) {
    const c = text[i];
    if (inStr) {
      if (esc) esc = false;
      else if (c === "\\") esc = true;
      else if (c === '"') inStr = false;
    } else if (c === '"') inStr = true;
    else if (c === "{") depth++;
    else if (c === "}" && --depth === 0) return text.slice(start, i + 1);
  }
  return null;
}

function findEvent(payload, slug) {
  const marker = `"slug":"${slug}"`;
  for (let i = payload.indexOf('{"id":'); i !== -1; i = payload.indexOf('{"id":', i + 1)) {
    const head = payload.slice(i, i + 400);
    if (!head.includes(marker)) continue;
    const raw = readObject(payload, i);
    if (!raw) continue;
    try {
      const obj = JSON.parse(raw);
      if (Array.isArray(obj.markets) && obj.markets[0]?.outcomePrices) return obj;
    } catch { /* keep scanning */ }
  }
  return null;
}

const asList = (v) => (typeof v === "string" ? JSON.parse(v) : v || []);
const flat = (s) => String(s || "").replace(/[\u2013\u2014]/g, "-");

async function main() {
  const [slug, flag] = process.argv.slice(2);
  if (!KEY || !slug) {
    console.error("usage: SCRAPINGBEE_API_KEY=... node polymarket_event.js <event-slug> [--json]");
    process.exit(1);
  }
  const { html, cost } = await fetchPage(`https://polymarket.com/event/${slug}`);
  const ev = findEvent(flightPayload(html), slug);
  if (!ev) throw new Error(`no event object for ${slug} in the page`);

  const rows = ev.markets.map((m) => {
    const prices = asList(m.outcomePrices).map(Number);
    return {
      label: flat(m.groupItemTitle || m.question),
      yes: prices[0],
      bid: m.bestBid ?? null,
      ask: m.bestAsk ?? null,
      volume: Number(m.volume || 0),
      endDate: m.endDate,
      closed: Boolean(m.closed),
    };
  });

  if (flag === "--json") {
    console.log(JSON.stringify({ slug, title: flat(ev.title), endDate: ev.endDate, volume: ev.volume, markets: rows }, null, 2));
    return;
  }
  console.log(`${flat(ev.title)}  ends ${ev.endDate}  volume $${Math.round(ev.volume).toLocaleString("en-US")}  [${cost} credit]`);
  for (const r of rows.sort((a, b) => b.yes - a.yes)) {
    const state = r.closed ? "closed" : `bid ${r.bid} / ask ${r.ask}`;
    console.log(`  ${(r.yes * 100).toFixed(1).padStart(5)}%  ${r.label.padEnd(40).slice(0, 40)}  ${state}`);
  }
}

main().catch((err) => {
  console.error(err.message);
  process.exit(1);
});

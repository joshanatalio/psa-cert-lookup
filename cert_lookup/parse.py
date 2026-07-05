"""Parse structured data from the loaded CardLadder / Alt result pages.

Both sites are SPAs whose CSS class names churn, so we parse from `innerText` text patterns
("Date Sold", "Price $", "VALUE $") rather than brittle selectors — the labels are stable. Each
function takes a Playwright page that has already been navigated by the drivers.
"""

from __future__ import annotations

import re

_MONEY = re.compile(r"[\d,]+(?:\.\d+)?")

# A CardLadder sale row reads like:
#   "EBAY - MUROZOND COLLECTION <title...> Date Sold Jul 1, 2026 Type Auction Price $1,438.00"
_CL_ROW = re.compile(
    r"^(?P<title>.+?)\s+Date Sold\s+(?P<date>.+?)\s+Type\s+(?P<type>.+?)\s+Price\s*\$?(?P<price>[\d,]+(?:\.\d+)?)",
    re.I,
)

_CL_JS = r"""() => {
  const rows = [...document.querySelectorAll('a.list-item.clickable')]
    .slice(0, 25).map(a => {
      const img = a.querySelector('img');
      return {
        text: (a.innerText || '').replace(/\s+/g, ' ').trim(),
        href: a.getAttribute('href') || null,
        image: img ? img.src : null,
      };
    });
  return { rows };
}"""

# Alt's listings/sales are real <a href="https://www.ebay.com/itm/...">, so scan anchors directly
# (simpler and more precise than the earlier any-element text scan).
_ALT_JS = r"""() => {
  let value = null, range = null;
  const listings = [], txns = [];
  for (const e of document.querySelectorAll('*')) {
    if (e.children.length > 4) continue;
    const t = (e.innerText || '').replace(/\s+/g, ' ').trim();
    if (!value && t && /VALUE\s*\$[\d,]/i.test(t) && t.length < 70) {
      const m = t.match(/\$[\d,]+(?:\.\d+)?/g) || [];
      if (m.length) value = m[0];
      if (m.length >= 3) range = m[1] + ' – ' + m[2];
    }
  }
  // Sale "type" varies more than the live-listing format (Auction/Fixed price) does — sold
  // transactions can read Auction, Buy now, Best offer, etc. — so listings are identified by the
  // "listing in <source>" phrase (specific, unlikely to false-positive) while sales are identified
  // by the trailing "<Month Day, Year> $<price>" shape, with the leading type left unconstrained.
  const seen = new Set();
  for (const a of document.querySelectorAll('a[href]')) {
    const t = (a.innerText || '').replace(/\s+/g, ' ').trim();
    const href = a.getAttribute('href');
    if (!t || !href || t.length > 90 || seen.has(t) || !/\$[\d,]/.test(t)) continue;
    if (/listing in/i.test(t)) {
      seen.add(t); listings.push({ text: t, href });
    } else if (/^[A-Za-z][A-Za-z ]{1,20}?\s+[A-Z][a-z]{2}\s+\d{1,2},\s*\d{4}\s*\$[\d,]/.test(t)) {
      seen.add(t); txns.push({ text: t, href });
    }
  }
  return { value, range, listings: listings.slice(0, 15), txns: txns.slice(0, 25) };
}"""

# Alt collapses "Recent Transactions" to 4 by default with a "VIEW ALL" control that expands it
# client-side (confirmed live: no navigation, same URL) to its real full history (~15 for a
# typical card — clicking again doesn't add more, so that's the actual ceiling Alt exposes, not a
# limit we're imposing). Click it before scraping so sales aren't capped at 4.
_CLICK_VIEW_ALL_JS = r"""(headingRe) => {
  const rx = new RegExp(headingRe, 'i');
  const heading = [...document.querySelectorAll('*')].find(e =>
    e.children.length === 0 && rx.test((e.innerText || '').trim()));
  if (!heading) return false;
  let container = heading;
  for (let i = 0; i < 6 && container; i++) container = container.parentElement;
  if (!container) return false;
  const btn = [...container.querySelectorAll('button, a')].find(e =>
    /view all/i.test((e.innerText || '').trim()));
  if (!btn) return false;
  btn.click();
  return true;
}"""

# "Auction 3 bids |3d 23h $510 live listing in eBay" / "Fixed price $2,082 live listing in eBay"
# Listing FORMAT is consistently Auction/Fixed price, so that whitelist stays narrow here.
_ALT_LISTING = re.compile(
    r"(?P<type>Fixed price|Auction)\s*(?:(?P<bids>\d+)\s*bids)?\s*(?:\|\s*(?P<time>[^$|]+?)\s*)?"
    r"\$(?P<price>[\d,]+)(?:\D+listing in\s*(?P<source>\w+))?",
    re.I,
)
# "Auction Jul 1, 2026 $1,438" / "Buy now Jul 2, 2026 $7,000" / "Best offer Jun 29, 2026 $6,000"
# — sale completion type is NOT restricted to Auction/Fixed price (unlike listing format above).
_ALT_TXN = re.compile(
    r"^(?P<type>[A-Za-z][A-Za-z ]{1,20}?)\s+(?P<date>[A-Z][a-z]{2}\s+\d{1,2},\s*\d{4})\s*\$(?P<price>[\d,]+)",
    re.I,
)


def _money(text: str | None) -> float | None:
    if not text:
        return None
    m = _MONEY.search(text)
    return float(m.group().replace(",", "")) if m else None


async def parse_cardladder(page) -> dict:
    # Wait for an actual sale ROW (not just "Date Sold", which is also a static column-header
    # label present before any row loads — waiting on that text alone is a false-positive "ready"
    # signal, especially right after a grade switch's full page reload with no other incidental
    # wait to mask it).
    for _ in range(30):
        if await page.evaluate("() => document.querySelectorAll('a.list-item.clickable').length > 0"):
            break
        await page.wait_for_timeout(200)
    raw = await page.evaluate(_CL_JS)
    sales = []
    for row in raw.get("rows", []):
        m = _CL_ROW.search(row["text"])
        if not m:
            continue
        title = m.group("title").strip()
        # Rows read "PLATFORM - <seller/card...>"; split the leading marketplace off.
        platform = None
        if " - " in title:
            head, rest = title.split(" - ", 1)
            if len(head) <= 20:
                platform, title = head.strip(), rest.strip()
        sales.append(
            {
                "platform": platform,
                "title": title,
                "date": m.group("date").strip(),
                "type": m.group("type").strip(),
                "price": _money(m.group("price")),
                "url": row.get("href"),  # opens the original marketplace listing (eBay, etc.)
                "image": row.get("image"),
            }
        )
    prices = [s["price"] for s in sales if s["price"] is not None]
    recent_avg = round(sum(prices) / len(prices)) if prices else None
    last = sales[0] if sales else None
    return {
        "last_sale": last["price"] if last else None,
        "last_date": last["date"] if last else None,
        "recent_avg": recent_avg,
        "image": last["image"] if last else None,  # first sale's card photo, for the header
        "sales": sales,  # all rendered sales, full info
    }


async def _wait_for_ebay_links_stable(page, max_checks: int = 30) -> None:
    """Poll the COUNT of eBay links until it stops growing (stable across 3 checks), not just
    until it's nonzero — sales and listings render at different times, so "any link present" can
    be satisfied by one section while the other is still loading (confirmed live)."""
    prev_count, stable = -1, 0
    for _ in range(max_checks):
        count = await page.evaluate("() => document.querySelectorAll('a[href*=\"ebay.com/itm\"]').length")
        if count > 0 and count == prev_count:
            stable += 1
            if stable >= 3:
                return
        else:
            stable = 0
        prev_count = count
        await page.wait_for_timeout(200)


async def parse_alt(page) -> dict:
    # Listings/recent-transactions render on an intersection observer, so scroll once to bring
    # them into view, then wait for both sections to settle (see _wait_for_ebay_links_stable).
    await page.mouse.wheel(0, 2400)
    await _wait_for_ebay_links_stable(page)

    # Alt collapses "Recent Transactions" to 4 by default; expand it (client-side, no navigation)
    # so sales aren't artificially capped — verified live this gets close to CardLadder's count
    # (4 -> 15 on a real cert). No-op if the control isn't there (e.g. <=4 sales already shown).
    try:
        expanded = await page.evaluate(_CLICK_VIEW_ALL_JS, "^recent transactions$")
    except Exception:  # noqa: BLE001
        expanded = False
    if expanded:
        await _wait_for_ebay_links_stable(page, max_checks=20)

    await page.evaluate("() => window.scrollTo(0, 0)")
    raw = await page.evaluate(_ALT_JS)

    listings = []
    for item in raw.get("listings", []):
        m = _ALT_LISTING.search(item["text"])
        if m:
            listings.append(
                {
                    "type": m.group("type").strip().title(),
                    "bids": int(m.group("bids")) if m.group("bids") else None,
                    "time_left": (m.group("time") or "").strip() or None,
                    "price": _money(m.group("price")),
                    "source": m.group("source"),
                    "url": item.get("href"),
                }
            )
    sales = []
    for item in raw.get("txns", []):
        m = _ALT_TXN.search(item["text"])
        if m:
            sales.append(
                {
                    "type": m.group("type").strip().title(),
                    "date": m.group("date").strip(),
                    "price": _money(m.group("price")),
                    "url": item.get("href"),
                }
            )
    return {
        "alt_value": _money(raw.get("value")),
        "alt_range": raw.get("range"),
        "listings": listings,
        "sales": sales,
    }

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
    .slice(0, 25).map(r => (r.innerText || '').replace(/\s+/g, ' ').trim());
  return { rows };
}"""

_ALT_JS = r"""() => {
  let out = { value: null, range: null };
  for (const e of document.querySelectorAll('*')) {
    if (e.children.length > 4) continue;
    const t = (e.innerText || '').replace(/\s+/g, ' ').trim();
    if (/VALUE\s*\$[\d,]/i.test(t) && t.length < 70) {
      const m = t.match(/\$[\d,]+(?:\.\d+)?/g) || [];
      if (m.length) out.value = m[0];
      if (m.length >= 3) out.range = m[1] + ' – ' + m[2];
      break;
    }
  }
  return out;
}"""


def _money(text: str | None) -> float | None:
    if not text:
        return None
    m = _MONEY.search(text)
    return float(m.group().replace(",", "")) if m else None


async def parse_cardladder(page) -> dict:
    raw = await page.evaluate(_CL_JS)
    sales = []
    for line in raw.get("rows", []):
        m = _CL_ROW.search(line)
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
            }
        )
    prices = [s["price"] for s in sales if s["price"] is not None]
    recent_avg = round(sum(prices) / len(prices)) if prices else None
    last = sales[0] if sales else None
    return {
        "last_sale": last["price"] if last else None,
        "last_date": last["date"] if last else None,
        "recent_avg": recent_avg,
        "sales": sales,  # all rendered sales, full info
    }


async def parse_alt(page) -> dict:
    raw = await page.evaluate(_ALT_JS)
    return {"alt_value": _money(raw.get("value")), "alt_range": raw.get("range")}

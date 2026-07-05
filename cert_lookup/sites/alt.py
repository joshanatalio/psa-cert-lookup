"""Alt driver: navigate to the query results page, then open the first result.

Alt's URL scheme (`browse?query=<cert>`) lands on a search-results page, so we navigate and then
click the first result to reach the actual card page.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from playwright.async_api import Page

from .. import config

_GRADE_IN_URL = re.compile(r"grade=PSA-([\d.]+)", re.I)

# --- Selectors (confirmed live against the logged-in results page) -------------------------
# Alt renders each result as a Material-UI <button> row whose title is a
# span.MuiTypography-vegaButton1 (not an anchor — that's why link selectors found nothing).
# The first such button is the first result. Results are login-gated and load asynchronously,
# so we wait for them to appear.
FIRST_RESULT_SELECTORS = [
    "button:has(.MuiTypography-vegaButton1)",
    "main [class*='MuiGrid-item'] button.MuiButton-text",
]


class AltDriver:
    name = "alt"

    def __init__(self, page: Page) -> None:
        self.page = page

    async def search(self, cert: str) -> None:
        await self.page.goto(config.alt_url(cert), wait_until="domcontentloaded")
        await self._click_first_result()

    async def apply_grade(self, grade: str) -> None:
        """Switch the open item page to a specific grade view (Alt defaults to PSA 10)."""
        # Poll for the item URL (SPA navigation from the result click); avoid wait_for_url's
        # load-event wait, which can hang on an SPA.
        for _ in range(30):
            if "/itm/" in (self.page.url or ""):
                break
            await self.page.wait_for_timeout(150)
        url = self.page.url or ""
        if "/itm/" not in url:
            return  # not on an item page

        grade_value = grade if "." in grade else f"{grade}.0"
        m = _GRADE_IN_URL.search(url)
        if m and m.group(1) == grade_value:
            return  # already showing the correct grade

        # Force-set the grade param (replacing any existing/mismatched one) rather than just
        # bailing on "a grade param is present" — a stale grade from a previous search on this
        # same page would otherwise never get corrected.
        parts = urlsplit(url)
        query = dict(parse_qsl(parts.query))
        query["grade"] = f"PSA-{grade_value}"
        new_url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
        await self.page.goto(new_url, wait_until="domcontentloaded")

    async def _click_first_result(self) -> None:
        for selector in FIRST_RESULT_SELECTORS:
            locator = self.page.locator(selector).first
            try:
                await locator.wait_for(state="visible", timeout=12_000)
            except Exception:
                continue
            await locator.click()
            return
        # No result matched — likely no listings for this cert. Leave the page up for the user.
        raise RuntimeError(
            "Alt: no result rows appeared (this cert may have no listings, or update "
            "FIRST_RESULT_SELECTORS in sites/alt.py)."
        )

"""Alt driver: navigate to the query results page, then open the first result.

Alt's URL scheme (`browse?query=<cert>`) lands on a search-results page, so we navigate and then
click the first result to reach the actual card page.
"""

from __future__ import annotations

from playwright.async_api import Page

from .. import config

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

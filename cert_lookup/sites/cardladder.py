"""CardLadder driver: open the search overlay, type the cert, submit.

CardLadder has no cert-based URL (its filter URLs use a resolved profileId, not the cert), so we
drive the UI: click the overlay trigger, type the cert into the overlay input, press Enter.

Selectors are the main risk and must be confirmed while logged in. They live in the SELECTORS
lists below so they're easy to update after live inspection.
"""

from __future__ import annotations

from playwright.async_api import Page

from .. import config

# --- Selectors (confirmed live against the logged-in app) ----------------------------------
# The nav "tag" icon button opens the CERT lookup (NOT the magnifying-glass search-icon, which
# does a keyword search). Verified: entering a cert here resolves to the
# ?filters=...profileId:psa-<n> URL — the real cert->profile lookup.
SEARCH_TRIGGER_SELECTORS = [
    'button:has(i.material-icons:has-text("tag"))',
    'button:has(i:text-is("tag"))',
]

# After clicking the trigger, the cert input appears (text input, maxlength 300, placeholder is
# an example cert like "63444200"). It is NOT auto-focused, so we click it before filling.
# NB: scope to :visible so `.first` skips the hidden maxlength-300 inputs that also exist in
# offscreen modals — otherwise it waits ~6s for a hidden element before falling through.
OVERLAY_INPUT_SELECTORS = [
    "input[type='text'][maxlength='300']:visible",
    "input[placeholder='63444200']:visible",
    "input[type='text']:visible",
]


class CardLadderDriver:
    name = "cardladder"

    def __init__(self, page: Page) -> None:
        self.page = page

    async def search(self, cert: str) -> None:
        await self._ensure_on_base_page()
        await self._open_overlay()
        input_locator = await self._find_first_visible(
            OVERLAY_INPUT_SELECTORS,
            error_hint="OVERLAY_INPUT_SELECTORS",
        )
        await input_locator.click()
        await input_locator.fill(cert)
        await input_locator.press("Enter")

    async def _ensure_on_base_page(self) -> None:
        # Re-navigate only if we've drifted off the CardLadder app (e.g. clicked into a card).
        if "cardladder.com" not in (self.page.url or ""):
            await self.page.goto(config.CARDLADDER_BASE_URL, wait_until="domcontentloaded")

    async def _open_overlay(self) -> None:
        trigger = await self._find_first_visible(
            SEARCH_TRIGGER_SELECTORS,
            error_hint="SEARCH_TRIGGER_SELECTORS",
        )
        await trigger.click()

    async def _find_first_visible(self, selectors: list[str], error_hint: str):
        for selector in selectors:
            locator = self.page.locator(selector).first
            try:
                await locator.wait_for(state="visible", timeout=6_000)
                return locator
            except Exception:
                continue
        raise RuntimeError(
            f"CardLadder: no element matched {error_hint}; "
            "update the selector list in sites/cardladder.py after inspecting the page."
        )

"""CardLadder driver: open the search overlay, type the cert, submit.

CardLadder has no cert-based URL (its filter URLs use a resolved profileId, not the cert), so we
drive the UI: click the overlay trigger, type the cert into the overlay input, press Enter.

Selectors are the main risk and must be confirmed while logged in. They live in the SELECTORS
lists below so they're easy to update after live inspection.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from playwright.async_api import Page

from .. import config


@dataclass
class CardLadderResult:
    """What a CardLadder lookup resolved to."""

    cert: str
    grade: str | None = None    # e.g. "10", "7", "8.5" (from the resolved filter URL)
    grader: str | None = None   # e.g. "PSA"
    title: str | None = None    # profile/card name, cleaned (no "(Pop N)" / "close")
    url: str = ""


# The resolved summary chip reads "Grade: 10, Grader: PSA, Profile: <name> …". The profile part
# first shows the raw "psa-<id>" then resolves to the readable name, so we poll for the name.
_SUMMARY_JS = r"""() => {
  let fallback = null;
  for (const e of document.querySelectorAll('*')) {
    if (e.children.length > 3) continue;
    const t = (e.innerText || '').trim().replace(/\s+/g, ' ');
    if (/^Grade:\s*\S+,\s*Grader:\s*\w+,\s*Profile:/i.test(t)) {
      const prof = t.split(/Profile:/i)[1].trim();
      if (prof && !/^psa-/i.test(prof)) return t;  // resolved name
      fallback = fallback || t;
    }
  }
  return fallback;
}"""

_GRADE_IN_URL = re.compile(r"grade(?:%3A|:)g([0-9.]+)", re.I)
_SUMMARY_RE = re.compile(r"Grade:\s*(\S+),\s*Grader:\s*(\w+),\s*Profile:\s*(.+)", re.I)

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

    async def search(self, cert: str) -> CardLadderResult:
        await self._ensure_on_base_page()
        # Capture the URL BEFORE submitting: on a repeat search, the page is already sitting on
        # the PREVIOUS cert's resolved ?...profileId=... URL, so "contains profileId" is true
        # instantly and would read stale (previous-cert) data. Requiring the URL to actually
        # CHANGE from this baseline is what makes the wait below correct.
        pre_nav_url = self.page.url or ""
        await self._open_overlay()
        input_locator = await self._find_first_visible(
            OVERLAY_INPUT_SELECTORS,
            error_hint="OVERLAY_INPUT_SELECTORS",
        )
        await input_locator.click()
        await input_locator.fill(cert)
        await input_locator.press("Enter")
        return await self._read_grade(cert, pre_nav_url)

    async def _read_grade(self, cert: str, pre_nav_url: str) -> CardLadderResult:
        """Fast path: wait for the URL to change to a NEWLY resolved filter URL and read the
        grade from it.

        This deliberately does NOT wait for the (slower) profile-name resolution, so the caller
        can apply the grade to Alt without delay. Call resolve_details() afterwards for the name.
        """
        # Poll the URL — CardLadder is an SPA (client-side routing), so wait_for_url's default
        # "load" wait never resolves and would burn the full timeout even though the URL changes
        # to the resolved ?filters=...profileId within ~1-2s. Must differ from pre_nav_url (see
        # search() above) or a repeat search reads the previous cert's stale URL.
        for _ in range(40):
            url = self.page.url or ""
            if "profileId" in url and url != pre_nav_url:
                break
            await self.page.wait_for_timeout(150)
        result = CardLadderResult(cert=cert, url=self.page.url)
        m = _GRADE_IN_URL.search(self.page.url)
        if m:
            result.grade = m.group(1)
        return result

    async def resolve_details(self, result: CardLadderResult) -> CardLadderResult:
        """Slow path (history only): poll until the profile name resolves, fill grader/title."""
        summary = None
        for _ in range(16):
            summary = await self.page.evaluate(_SUMMARY_JS)
            if summary:
                sm = _SUMMARY_RE.match(summary)
                prof = (sm.group(3).strip() if sm else "")
                # Require both the name to be resolved (not the raw "psa-<id>") AND the grade in
                # the summary chip to match the grade we already confirmed from the (verified
                # fresh) URL — otherwise we can read a stale chip left over from the PREVIOUS
                # search that just happens to already look "resolved".
                if sm and prof and not prof.lower().startswith("psa-") and (
                    result.grade is None or sm.group(1) == result.grade
                ):
                    break
            await self.page.wait_for_timeout(400)

        sm = _SUMMARY_RE.match(summary or "")
        if sm:
            result.grade = result.grade or sm.group(1)
            result.grader = sm.group(2)
            name = sm.group(3).strip()
            # Strip the trailing filter-chip "close" icon text and any "(Pop N)" suffix.
            for _ in range(3):
                cleaned = re.sub(r"\s*\(Pop\s*[\d,]+\)\s*$", "", name, flags=re.I)
                cleaned = re.sub(r"\s+close$", "", cleaned, flags=re.I).strip()
                if cleaned == name:
                    break
                name = cleaned
            if name and not name.lower().startswith("psa-"):
                result.title = name
        return result

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

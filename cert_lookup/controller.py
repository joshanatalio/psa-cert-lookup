"""LookupController: the UI-agnostic entry point.

Owns the window manager and both site drivers. Interface layers (CLI, web, Mac app) only need:

    controller = LookupController()
    await controller.start()
    await controller.run(cert)   # repeat per cert
    await controller.close()
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from . import config
from .sites import AltDriver, CardLadderDriver
from .sites.cardladder import CardLadderResult
from .windows import WindowManager


@dataclass
class LookupResult:
    cert: str
    status: dict[str, str]          # {"cardladder": "ok"|<err>, "alt": "ok"|<err>}
    grade: str | None               # resolved PSA grade, e.g. "10"
    label: str                      # display label for history


class LookupController:
    def __init__(self) -> None:
        self.windows = WindowManager()
        self._cardladder: CardLadderDriver | None = None
        self._alt: AltDriver | None = None

    async def start(self, refresh_profile: bool = False) -> None:
        await self.windows.start(refresh_profile=refresh_profile)
        self._cardladder = CardLadderDriver(self.windows.page("cardladder"))
        self._alt = AltDriver(self.windows.page("alt"))
        # Land each window on its site so you can log in (first run) or are ready to query.
        await asyncio.gather(
            self._goto(self.windows.page("cardladder"), config.CARDLADDER_BASE_URL),
            self._goto(self.windows.page("alt"), config.ALT_BASE_URL),
        )

    @staticmethod
    async def _goto(page, url: str) -> None:
        try:
            await page.goto(url, wait_until="domcontentloaded")
        except Exception:
            pass  # login redirects / slow SPA loads are fine; the window is still usable

    async def run(self, cert: str) -> LookupResult:
        """Look up a cert on both sites. One site failing does not stop the other.

        Runs both searches concurrently, then (if CardLadder resolved the grade) switches Alt's
        item page to that grade. Returns a LookupResult with per-site status, the grade, and a
        display label for history.
        """
        if self._cardladder is None or self._alt is None:
            raise RuntimeError("LookupController.start() must be called before run().")

        # Raise both windows so a lookup is visible even if they were behind other apps.
        await self.windows.bring_to_front()

        cl_out, alt_out = await asyncio.gather(
            self._cardladder.search(cert),
            self._alt.search(cert),
            return_exceptions=True,
        )

        status: dict[str, str] = {}
        cl_result: CardLadderResult | None = None
        if isinstance(cl_out, Exception):
            status["cardladder"] = str(cl_out)
        else:
            status["cardladder"] = "ok"
            cl_result = cl_out
        status["alt"] = "ok" if not isinstance(alt_out, Exception) else str(alt_out)

        # Switch Alt to the resolved grade (Alt defaults to PSA 10) as soon as the grade is
        # known — before the slower card-name resolution, so there's no visible lag.
        grade = cl_result.grade if cl_result else None
        if grade and status["alt"] == "ok":
            try:
                await self._alt.apply_grade(grade)
            except Exception as exc:  # noqa: BLE001
                status["alt"] = f"graded-nav failed: {exc}"

        # Resolve the card name (history label only) after the grade is applied.
        if cl_result is not None:
            try:
                await self._cardladder.resolve_details(cl_result)
            except Exception:  # noqa: BLE001
                pass

        return LookupResult(
            cert=cert,
            status=status,
            grade=grade,
            label=self._build_label(cert, cl_result),
        )

    @staticmethod
    def _build_label(cert: str, cl_result: CardLadderResult | None) -> str:
        if cl_result and cl_result.title:
            grader = cl_result.grader or "PSA"
            grade = cl_result.grade or ""
            return " ".join(p for p in (cl_result.title, grader, grade) if p).strip()
        return f"{cert} ？"  # unresolved → cert with a fullwidth question mark

    async def close(self) -> None:
        await self.windows.close()

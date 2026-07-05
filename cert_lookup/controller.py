"""LookupController: the UI-agnostic entry point.

Owns the window manager and both site drivers. Interface layers (CLI, web, Mac app) only need:

    controller = LookupController()
    await controller.start()     # optional: eager-open the windows (the CLI does this)
    await controller.run(cert)   # opens windows lazily if needed; reopens if closed
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
        self._current_cert: str | None = None  # cert currently open on both pages, if any
        self._current_title: str | None = None  # its resolved name — grade-invariant, so cached
        self._current_grader: str | None = None

    async def start(self, refresh_profile: bool = False) -> None:
        """Eagerly open the windows (used by the CLI). The menu-bar app skips this and lets the
        first run() open them lazily."""
        await self._ensure_windows(refresh_profile=refresh_profile)
        # Land each window on its site (nice for the CLI; lazy callers let the drivers navigate).
        await asyncio.gather(
            self._goto(self.windows.page("cardladder"), config.CARDLADDER_BASE_URL),
            self._goto(self.windows.page("alt"), config.ALT_BASE_URL),
        )

    async def _ensure_windows(self, refresh_profile: bool = False) -> None:
        """Open the two windows if they aren't already up. If they were closed, relaunch."""
        if self.windows.is_alive():
            return
        await self.windows.close()  # tear down any dead/closed state before relaunching
        await self.windows.start(refresh_profile=refresh_profile)
        self._cardladder = CardLadderDriver(self.windows.page("cardladder"))
        self._alt = AltDriver(self.windows.page("alt"))
        self._current_cert = None  # fresh pages show nothing yet
        self._current_title = None
        self._current_grader = None

    @staticmethod
    async def _goto(page, url: str) -> None:
        try:
            await page.goto(url, wait_until="domcontentloaded")
        except Exception:
            pass  # login redirects / slow SPA loads are fine; the window is still usable

    async def run(self, cert: str, grade: str | None = None) -> LookupResult:
        """Look up a cert on both sites, or (if `grade` is given and this cert is already open)
        just re-render the same card at a different PSA grade.

        One site failing does not stop the other. Returns a LookupResult with per-site status,
        the grade, and a display label for history.
        """
        # Open the windows on first use, or reopen them if they were closed.
        await self._ensure_windows()
        if self._cardladder is None or self._alt is None:
            raise RuntimeError("Windows failed to open.")

        # Raise both windows so a lookup is visible even if they were behind other apps.
        await self.windows.bring_to_front()

        if grade and cert == self._current_cert:
            # Fast path: same card already open on both pages — just switch grade (verified live:
            # swapping CardLadder's grade param on the same profileId shows the same card's data
            # at the new grade; Alt's apply_grade already supports this directly).
            return await self._switch_grade(cert, grade)

        self._current_cert = cert
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

        # Target grade: an explicitly requested one, else whatever CardLadder resolved by default.
        target_grade = grade or (cl_result.grade if cl_result else None)

        # Switch Alt to the target grade (Alt defaults to PSA 10) as soon as it's known — before
        # the slower card-name resolution, so there's no visible lag.
        if target_grade and status["alt"] == "ok":
            try:
                await self._alt.apply_grade(target_grade)
            except Exception as exc:  # noqa: BLE001
                status["alt"] = f"graded-nav failed: {exc}"

        # If a specific (non-default) grade was requested up front, also switch CardLadder to it.
        if grade and cl_result and status["cardladder"] == "ok" and grade != cl_result.grade:
            try:
                cl_result = await self._cardladder.switch_grade(cert, grade)
            except Exception as exc:  # noqa: BLE001
                status["cardladder"] = f"grade-switch failed: {exc}"

        # Resolve the card name (history label only) after the grade is applied.
        if cl_result is not None:
            try:
                await self._cardladder.resolve_details(cl_result)
            except Exception:  # noqa: BLE001
                pass
            # Cache the name: it's grade-invariant (verified: same title at PSA 8 vs PSA 10), so
            # a later grade switch on this same cert can skip the slow re-resolution entirely —
            # a full page reload (required to change CardLadder's grade) takes much longer to
            # re-resolve the name than a fresh cert search does.
            if cl_result.title:
                self._current_title = cl_result.title
                self._current_grader = cl_result.grader

        return LookupResult(
            cert=cert,
            status=status,
            grade=target_grade,
            label=self._build_label(cert, cl_result),
        )

    async def _switch_grade(self, cert: str, grade: str) -> LookupResult:
        """Fast path: the requested card is already open — just re-render it at a new grade.

        Skips resolve_details entirely: the card's name doesn't change with grade, and the
        cached name from the original search is reused directly.
        """
        status = {"cardladder": "ok", "alt": "ok"}
        cl_result = CardLadderResult(
            cert=cert, grade=grade, title=self._current_title, grader=self._current_grader
        )
        try:
            cl_result = await self._cardladder.switch_grade(cert, grade)
            cl_result.title = self._current_title
            cl_result.grader = self._current_grader
        except Exception as exc:  # noqa: BLE001
            status["cardladder"] = str(exc)
        try:
            await self._alt.apply_grade(grade)
        except Exception as exc:  # noqa: BLE001
            status["alt"] = str(exc)
        return LookupResult(
            cert=cert, status=status, grade=grade, label=self._build_label(cert, cl_result)
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

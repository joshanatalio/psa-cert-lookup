"""LookupController: the UI-agnostic entry point.

Owns the window manager and both site drivers. Interface layers (CLI, web, Mac app) only need:

    controller = LookupController()
    await controller.start()
    await controller.run(cert)   # repeat per cert
    await controller.close()
"""

from __future__ import annotations

import asyncio

from . import config
from .sites import AltDriver, CardLadderDriver
from .windows import WindowManager


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

    async def run(self, cert: str) -> dict[str, str]:
        """Fan out to both sites concurrently. One site failing does not stop the other.

        Returns a per-site status map ("ok" or an error message) so the interface layer can
        report results without needing to know about browser internals.
        """
        if self._cardladder is None or self._alt is None:
            raise RuntimeError("LookupController.start() must be called before run().")

        # Raise both windows so a lookup is visible even if they were behind other apps.
        await self.windows.bring_to_front()

        results = await asyncio.gather(
            self._cardladder.search(cert),
            self._alt.search(cert),
            return_exceptions=True,
        )
        names = ("cardladder", "alt")
        status: dict[str, str] = {}
        for name, result in zip(names, results):
            status[name] = "ok" if not isinstance(result, Exception) else str(result)
        return status

    async def close(self) -> None:
        await self.windows.close()

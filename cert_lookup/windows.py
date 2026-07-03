"""Launch one Chrome (on the copied profile) and drive two side-by-side windows.

Both sites share your single logged-in profile, so we use ONE persistent context and open a
second OS window from it via CDP (Target.createTarget newWindow). After launch we read the
primary display's available size from the page and set exact window bounds over CDP, so
placement is resolution-independent and needs no macOS-specific dependencies.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from playwright.async_api import BrowserContext, Page, async_playwright

from . import config, profile
from .config import WindowSlot


@dataclass
class ManagedWindow:
    """A single OS window (page) belonging to the shared context."""

    name: str
    page: Page


class WindowManager:
    """Owns the Playwright instance, the shared context, and both windows."""

    def __init__(self) -> None:
        self._playwright = None
        self._context: BrowserContext | None = None
        self.windows: dict[str, ManagedWindow] = {}

    async def start(self, refresh_profile: bool = False) -> None:
        if profile.ensure_copy(refresh=refresh_profile):
            print(f"  Copied Chrome profile '{config.SOURCE_CHROME_PROFILE}' → {config.TOOL_PROFILE}")

        self._playwright = await async_playwright().start()
        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(config.TOOL_PROFILE),
            headless=False,
            channel=config.BROWSER_CHANNEL,
            no_viewport=True,  # let the real OS window drive size; we position it via CDP
            # Strip signals that mark this as automated (Cloudflare/Google block them) AND drop
            # --use-mock-keychain so the copied, encrypted cookies can actually be decrypted.
            ignore_default_args=["--enable-automation", "--use-mock-keychain"],
            args=[
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        self._context.set_default_timeout(config.DEFAULT_TIMEOUT_MS)
        await self._context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )

        # Window 1 (CardLadder) = the context's initial page.
        page1 = self._context.pages[0] if self._context.pages else await self._context.new_page()
        # Window 2 (Alt) = a brand-new OS window created via CDP.
        page2 = await self._open_new_window(page1)

        self.windows["cardladder"] = ManagedWindow("cardladder", page1)
        self.windows["alt"] = ManagedWindow("alt", page2)
        await self._position_all()

    async def _open_new_window(self, existing: Page) -> Page:
        cdp = await self._context.new_cdp_session(existing)
        async with self._context.expect_page() as new_page_info:
            await cdp.send("Target.createTarget", {"url": "about:blank", "newWindow": True})
        return await new_page_info.value

    async def _position_all(self) -> None:
        any_page = next(iter(self.windows.values())).page
        metrics = await any_page.evaluate(
            "() => ({ w: window.screen.availWidth, h: window.screen.availHeight,"
            " x: window.screen.availLeft || 0, y: window.screen.availTop || 0 })"
        )
        await asyncio.gather(
            self._position(self.windows["cardladder"], config.CARDLADDER_SLOT, metrics),
            self._position(self.windows["alt"], config.ALT_SLOT, metrics),
        )

    async def _position(self, win: ManagedWindow, slot: WindowSlot, metrics: dict) -> None:
        left = int(metrics["x"] + slot.left_fraction * metrics["w"])
        top = int(metrics["y"])
        width = int(slot.width_fraction * metrics["w"])
        height = int(metrics["h"])
        cdp = await self._context.new_cdp_session(win.page)
        target = await cdp.send("Browser.getWindowForTarget")
        window_id = target["windowId"]
        await cdp.send(
            "Browser.setWindowBounds",
            {"windowId": window_id, "bounds": {"windowState": "normal"}},
        )
        await cdp.send(
            "Browser.setWindowBounds",
            {
                "windowId": window_id,
                "bounds": {"left": left, "top": top, "width": width, "height": height},
            },
        )

    def page(self, name: str) -> Page:
        return self.windows[name].page

    async def close(self) -> None:
        if self._context is not None:
            try:
                await self._context.close()
            except Exception:
                pass
            self._context = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

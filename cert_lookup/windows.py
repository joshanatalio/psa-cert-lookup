"""Launch one Chrome (on the copied profile) and drive two side-by-side windows.

Both sites share your single logged-in profile, so we use ONE persistent context and open a
second OS window from it via CDP (Target.createTarget newWindow). After launch we read the
primary display's available size from the page and set exact window bounds over CDP, so
placement is resolution-independent and needs no macOS-specific dependencies.
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import time
from dataclasses import dataclass

from playwright.async_api import BrowserContext, Page, async_playwright

from . import config, profile
from .config import WindowSlot


def _our_chrome_pids() -> list[int]:
    """PIDs of Chrome processes launched on OUR profile (never anyone else's)."""
    marker = f"--user-data-dir={config.TOOL_PROFILE}"
    try:
        out = subprocess.run(
            ["ps", "-ax", "-o", "pid=,command="], capture_output=True, text=True
        ).stdout
    except Exception:
        return []
    pids: list[int] = []
    for line in out.splitlines():
        if marker in line:  # literal substring match on our unique profile path
            head = line.strip().split(None, 1)[0]
            if head.isdigit():
                pids.append(int(head))
    return pids


def _kill_our_chrome() -> None:
    """Terminate leftover Chrome from a previous run of THIS tool. Safe: matches our profile
    path only, so it can never close the user's normal Chrome windows."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        pids = _our_chrome_pids()
        if not pids:
            return
        for pid in pids:
            try:
                os.kill(pid, sig)
            except OSError:
                pass
        time.sleep(0.6)


def _clear_singleton_locks() -> None:
    for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        try:
            (config.TOOL_PROFILE / name).unlink()
        except OSError:
            pass


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
        self._context = await self._launch_context_with_repair()
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

    async def _launch_context_with_repair(self) -> BrowserContext:
        """Launch the persistent context, self-repairing a locked/leftover profile.

        The common failure ("Opening in existing browser session" / TargetClosedError /
        ProcessSingleton) means a previous run's Chrome is still holding our profile. We kill
        only OUR leftover Chrome and clear stale lock files, then retry.
        """
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                return await self._create_context()
            except Exception as error:  # noqa: BLE001
                last_error = error
                if attempt == 2:
                    break
                print(f"  Browser launch failed ({error}); self-repairing profile and retrying…")
                _kill_our_chrome()
                _clear_singleton_locks()
                await asyncio.sleep(1.0)
        assert last_error is not None
        raise last_error

    async def _create_context(self) -> BrowserContext:
        # Headless (phone server): use a fixed viewport for screenshots. Headful (Mac apps): let
        # the real OS window drive size and position it via CDP.
        viewport_kwargs = (
            {"viewport": config.HEADLESS_VIEWPORT} if config.HEADLESS else {"no_viewport": True}
        )
        return await self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(config.TOOL_PROFILE),
            headless=config.HEADLESS,
            channel=config.BROWSER_CHANNEL,
            # Strip signals that mark this as automated (Cloudflare/Google block them) AND drop
            # --use-mock-keychain so the copied, encrypted cookies can actually be decrypted.
            ignore_default_args=["--enable-automation", "--use-mock-keychain"],
            args=[
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-blink-features=AutomationControlled",
            ],
            **viewport_kwargs,
        )

    async def _open_new_window(self, existing: Page) -> Page:
        cdp = await self._context.new_cdp_session(existing)
        async with self._context.expect_page() as new_page_info:
            await cdp.send("Target.createTarget", {"url": "about:blank", "newWindow": True})
        return await new_page_info.value

    async def _position_all(self) -> None:
        if config.HEADLESS:
            return  # no OS windows to position in headless
        if config.HIDE_WINDOWS:
            await self._hide_windows()
            return
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

    async def _hide_windows(self) -> None:
        """Hide both windows off-screen (phone server) so they don't clutter the Mac. We position
        them off the visible area rather than minimizing — a minimized window stops rendering and
        screenshots come back blank, whereas an off-screen "normal" window still renders."""
        for win in self.windows.values():
            try:
                cdp = await self._context.new_cdp_session(win.page)
                target = await cdp.send("Browser.getWindowForTarget")
                await cdp.send(
                    "Browser.setWindowBounds",
                    {
                        "windowId": target["windowId"],
                        "bounds": {"windowState": "normal", "left": -4000, "top": 0,
                                   "width": 1280, "height": 1600},
                    },
                )
            except Exception:
                pass

    def page(self, name: str) -> Page:
        return self.windows[name].page

    def is_alive(self) -> bool:
        """True only if the context and both windows are still open (user hasn't closed them)."""
        if self._context is None or len(self.windows) < 2:
            return False
        try:
            return all(not win.page.is_closed() for win in self.windows.values())
        except Exception:
            return False

    async def bring_to_front(self) -> None:
        """Raise both browser windows above other apps (best-effort)."""
        if config.HIDE_WINDOWS:
            return  # keep them minimized (phone server); don't pop them up on lookups
        for win in self.windows.values():
            try:
                await win.page.bring_to_front()
            except Exception:
                pass

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

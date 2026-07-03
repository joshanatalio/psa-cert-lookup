#!/usr/bin/env python3
"""Interactive terminal loop over LookupController.

Paste a PSA cert, press Enter, and both windows update. Blank line or 'q' quits.

This file is intentionally thin — all browser logic lives in the cert_lookup package so a web or
Mac front-end can reuse it unchanged.
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading

from cert_lookup import LookupController
from cert_lookup.config import clean_cert


async def ainput(prompt: str) -> str:
    """Read a line from stdin without blocking the event loop or shutdown.

    input() runs in a DAEMON thread so that a pending read never blocks Ctrl+C or interpreter
    exit. (asyncio.to_thread uses the default executor, whose threads are joined at shutdown —
    a blocked input() there deadlocks the whole program on quit.)
    """
    loop = asyncio.get_running_loop()
    fut: asyncio.Future = loop.create_future()

    def _worker() -> None:
        try:
            value = input(prompt)
        except BaseException as exc:  # EOFError and friends
            loop.call_soon_threadsafe(fut.set_exception, exc)
        else:
            loop.call_soon_threadsafe(fut.set_result, value)

    threading.Thread(target=_worker, daemon=True).start()
    return await fut


async def main() -> None:
    refresh_profile = "--refresh-profile" in sys.argv

    controller = LookupController()
    if refresh_profile:
        print("Refreshing the Chrome profile copy…")
    print("Preparing profile and launching browser windows…")
    await controller.start(refresh_profile=refresh_profile)

    print(
        "\nBoth windows are open on a copy of your Chrome profile — you should already be\n"
        "logged into CardLadder (left) and Alt (right). If a site shows you logged out, quit\n"
        "and re-run with --refresh-profile to re-copy your current session.\n"
    )
    try:
        await ainput("Press Enter when you're ready… ")
        print("\nPaste a PSA cert number and press Enter. Blank line or 'q' to quit.\n")
        while True:
            raw = await ainput("cert> ")
            if raw.strip().lower() in ("", "q", "quit", "exit"):
                break
            cert = clean_cert(raw)
            if not cert:
                print("  (no digits found — try again)")
                continue
            print(f"  Looking up {cert}…")
            status = await controller.run(cert)
            for site, result in status.items():
                mark = "✓" if result == "ok" else "✗"
                detail = "" if result == "ok" else f" — {result}"
                print(f"  {mark} {site}{detail}")
    except (EOFError, KeyboardInterrupt):
        pass
    finally:
        print("\nClosing…")
        try:
            await asyncio.wait_for(controller.close(), timeout=10)
        except Exception:
            pass  # never let a slow/hung browser close block the exit


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    finally:
        # Guarantee the shell prompt returns even if a daemon input thread or a browser handle
        # is still lingering. Cleanup already ran in main()'s finally.
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(0)

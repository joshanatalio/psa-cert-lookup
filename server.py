#!/usr/bin/env python3
"""FastAPI server exposing the cert lookup to your phone (over Tailscale).

Reuses the exact same automation as the Mac app (LookupController): a request runs the lookup in
two Chrome windows on a logged-in profile, then returns screenshots of the two result pages
(Phase 1). Parsed JSON and photo upload come in later phases.

Run:  python3 -m uvicorn server:app --host 0.0.0.0 --port 8000
Then, from the phone (same Tailscale network):  http://<mac-name>:8000
"""

from __future__ import annotations

import asyncio
import base64
import io
from contextlib import asynccontextmanager
from pathlib import Path

from PIL import Image

# The server gets its OWN profile copy so it can run alongside the menu-bar app (Chromium locks a
# user-data-dir). Both are copies of your one logged-in Chrome. Must be set before the controller
# opens the browser.
from cert_lookup import config

config.TOOL_PROFILE = config.TOOL_DATA_ROOT / "chrome-profile-server"

from cert_lookup import LookupController, parse  # noqa: E402 - after the profile override above
from cert_lookup.config import clean_cert  # noqa: E402

from fastapi import FastAPI, Query  # noqa: E402
from fastapi.responses import HTMLResponse, JSONResponse  # noqa: E402

WEB_DIR = Path(__file__).parent / "web"

controller = LookupController()
# Serialize requests — one browser drives both sites, so lookups can't overlap.
lock = asyncio.Lock()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    await controller.close()


app = FastAPI(lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return (WEB_DIR / "index.html").read_text()


@app.get("/lookup")
async def lookup(cert: str = Query(...)):
    cleaned = clean_cert(cert)
    if not cleaned:
        return JSONResponse({"error": "No cert number in input."}, status_code=400)
    async with lock:
        try:
            result = await controller.run(cleaned)
            data, shots = await _collect()
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": str(exc)}, status_code=500)
    return {
        "cert": cleaned,
        "label": result.label,
        "grade": result.grade,
        "status": result.status,
        "data": data,
        "screenshots": shots,
    }


# The key Alt data (LT VALUE + listings) renders below the fold and a beat after navigation, so
# we wait for a marker then capture the full page. CardLadder's data is at the top.
_SETTLE_MARKERS = {"cardladder": "Date Sold", "alt": "LT VALUE"}
# Cap the captured height (device px) so a phone isn't scrolling an 8000px image — the useful data
# is near the top for both sites. ~2500 CSS px at 2x Retina.
_MAX_SHOT_HEIGHT = 5000


async def _collect() -> tuple[dict, dict[str, str | None]]:
    """Settle both pages, then parse structured data + capture screenshots."""
    parsers = {"cardladder": parse.parse_cardladder, "alt": parse.parse_alt}
    data: dict = {}
    shots: dict[str, str | None] = {}
    for name in ("cardladder", "alt"):
        page = controller.windows.page(name)
        marker = _SETTLE_MARKERS.get(name)
        if marker:
            try:
                await page.get_by_text(marker, exact=False).first.wait_for(timeout=6000)
            except Exception:  # noqa: BLE001 - fall back to a fixed settle below
                pass
        await page.wait_for_timeout(1200)  # let lazy sections (transactions) render
        try:
            data[name] = await parsers[name](page)
        except Exception as exc:  # noqa: BLE001
            data[name] = {"error": str(exc)}
        try:
            png = await page.screenshot(full_page=True)
            shots[name] = "data:image/png;base64," + base64.b64encode(_cap_height(png)).decode()
        except Exception:  # noqa: BLE001
            shots[name] = None
    return data, shots


def _cap_height(png: bytes) -> bytes:
    img = Image.open(io.BytesIO(png))
    if img.height <= _MAX_SHOT_HEIGHT:
        return png
    img = img.crop((0, 0, img.width, _MAX_SHOT_HEIGHT))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)

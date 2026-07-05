#!/usr/bin/env python3
"""FastAPI server exposing the cert lookup to your phone (over Tailscale).

Reuses the exact same automation as the Mac app (LookupController): a request runs the lookup in
two Chrome windows on a logged-in profile, then returns parsed CardLadder sales and Alt
listings/sales as structured JSON (cert_lookup/parse.py). Also accepts a slab photo, OCRs the
cert (Vision), then runs the same lookup.

Run:  python3 -m uvicorn server:app --host 0.0.0.0 --port 8000
Then, from the phone (same Tailscale network):  http://<mac-name>:8000
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

# The server gets its OWN profile copy so it can run alongside the menu-bar app (Chromium locks a
# user-data-dir). Both are copies of your one logged-in Chrome. Must be set before the controller
# opens the browser.
from cert_lookup import config

config.TOOL_PROFILE = config.TOOL_DATA_ROOT / "chrome-profile-server"
config.HIDE_WINDOWS = True  # headful (Cloudflare needs it) but off-screen so nothing clutters the Mac

from cert_lookup import (  # noqa: E402 - after the profile override above
    LookupController,
    cert_extraction,
    history,
    parse,
)
from cert_lookup.config import clean_cert  # noqa: E402

from fastapi import FastAPI, File, Query, UploadFile  # noqa: E402
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


@app.get("/history")
async def get_history():
    # Shared with the menu-bar app's history (same ~/.cert-dual-lookup/history.db) — one unified
    # recent-lookups list across Mac and phone use.
    return [{"cert": e.cert, "label": e.label} for e in history.recent(20)]


@app.get("/lookup")
async def lookup(cert: str = Query(...), grade: Optional[str] = Query(None)):
    cleaned = clean_cert(cert)
    if not cleaned:
        return JSONResponse({"error": "No cert number in input."}, status_code=400)
    try:
        return await _run_lookup(cleaned, grade)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.post("/lookup_image")
async def lookup_image(photo: UploadFile = File(...)):
    """Phone photo of a slab -> OCR the cert (Vision) -> look it up."""
    raw = await photo.read()
    try:
        certs = await asyncio.to_thread(cert_extraction.extract_certs, raw)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": f"OCR failed: {exc}"}, status_code=500)
    if not certs:
        return JSONResponse({"error": "No cert number found in the photo."}, status_code=422)
    if len(certs) > 1:
        return {"candidates": certs}  # ambiguous — let the phone confirm which one
    try:
        return await _run_lookup(certs[0])
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


async def _run_lookup(cert: str, grade: str | None = None) -> dict:
    async with lock:
        result = await controller.run(cert, grade)
        data = await _parse_all()
    history.record(cert, result.status, result.label)
    return {
        "cert": cert,
        "label": result.label,
        "grade": result.grade,
        "status": result.status,
        "data": data,
    }


async def _parse_one(name: str, parser) -> tuple[str, dict]:
    # Each parser waits for its own precise data-presence signal internally (an actual sale row /
    # eBay link, not a text label) — a generic label-text wait here previously gave false
    # positives, since e.g. "Date Sold" is also a static column header shown before any row loads.
    page = controller.windows.page(name)
    try:
        return name, await parser(page)
    except Exception as exc:  # noqa: BLE001
        return name, {"error": str(exc)}


async def _parse_all() -> dict:
    """Settle + parse both pages concurrently (they're independent browser tabs)."""
    results = await asyncio.gather(
        _parse_one("cardladder", parse.parse_cardladder),
        _parse_one("alt", parse.parse_alt),
    )
    return dict(results)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)

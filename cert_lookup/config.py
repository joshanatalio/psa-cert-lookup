"""Central configuration: profile locations, site URLs, and window layout.

Keeping these in one place makes it easy to retarget profiles, tweak URLs, or change the
side-by-side layout without touching the driver logic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# Browser profile (copied from your real Chrome so logins are inherited)
# ---------------------------------------------------------------------------
# The tool runs on a COPY of your Chrome profile. This inherits your existing CardLadder + Alt
# sessions, so there is no login/OAuth/MFA to pass. Your normal Chrome stays usable because we
# never touch the original. Re-copy (run with --refresh-profile) if a session later expires.
TOOL_DATA_ROOT = Path.home() / ".cert-dual-lookup"
TOOL_PROFILE = TOOL_DATA_ROOT / "chrome-profile"  # Playwright user-data-dir (the working copy)

# Source: your real Chrome. SOURCE_CHROME_PROFILE is the profile folder to copy logins from —
# "Default" for most people; change it if you use a named Chrome profile.
CHROME_USER_DATA_DIR = Path.home() / "Library" / "Application Support" / "Google" / "Chrome"
SOURCE_CHROME_PROFILE = "Default"

# Large, regenerated cache dirs skipped during the copy (keeps it fast and small; sessions live
# in Cookies / Local Storage / IndexedDB, which are always copied).
PROFILE_COPY_SKIP_DIRS = {
    # Pure caches
    "Cache", "Code Cache", "GPUCache", "DawnCache", "DawnGraphiteCache", "GraphiteDawnCache",
    "DawnWebGPUCache", "GrShaderCache", "ShaderCache", "component_crx_cache",
    "extensions_crx_cache", "optimization_guide_model_store", "blob_storage", "Download Service",
    # Large and not needed for auth (asset caches / extensions / OPFS). Sessions live in
    # Cookies, Local Storage, and IndexedDB, which are always copied. If a login somehow doesn't
    # carry over, remove "Service Worker" from this set and re-copy.
    "Service Worker", "File System", "Extensions",
}

# ---------------------------------------------------------------------------
# Site URLs
# ---------------------------------------------------------------------------
CARDLADDER_BASE_URL = "https://app.cardladder.com/sales-history"
ALT_BASE_URL = "https://alt.xyz/"
ALT_QUERY_URL = "https://alt.xyz/browse?query={cert}"


def alt_url(cert: str) -> str:
    return ALT_QUERY_URL.format(cert=cert)


# ---------------------------------------------------------------------------
# Window layout
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class WindowSlot:
    """Fractional placement within the primary display's available area."""

    name: str
    left_fraction: float  # 0.0 = screen left edge, 0.5 = midpoint
    width_fraction: float  # 0.5 = half the screen width


# CardLadder on the left half, Alt on the right half.
CARDLADDER_SLOT = WindowSlot(name="cardladder", left_fraction=0.0, width_fraction=0.5)
ALT_SLOT = WindowSlot(name="alt", left_fraction=0.5, width_fraction=0.5)

# ---------------------------------------------------------------------------
# Behavior
# ---------------------------------------------------------------------------
# Drive the installed Google Chrome (not Playwright's bundled Chromium). Real Chrome plus the
# de-automation flags in windows.py is what gets past Cloudflare (CardLadder) and Google OAuth
# (Alt), which both hard-block obviously-automated browsers. Set to None to fall back to
# bundled Chromium.
BROWSER_CHANNEL: str | None = "chrome"

# Headless doesn't work here — CardLadder's Cloudflare hard-blocks headless Chrome. Kept as a
# flag but the phone server uses MINIMIZE_WINDOWS instead (headful, but windows minimized so they
# don't clutter the Mac; Playwright screenshots render from the page, not the OS window, so they
# still work minimized).
HEADLESS = False
HEADLESS_VIEWPORT = {"width": 1280, "height": 1600}
# Hide the windows off-screen (phone server) so they don't clutter the Mac, while still
# rendering for screenshots. (Minimizing would stop rendering; headless trips Cloudflare.)
HIDE_WINDOWS = False

# Max time (ms) to wait for a page element/state before treating an action as failed.
DEFAULT_TIMEOUT_MS = 15_000

_DIGITS = re.compile(r"\D+")


def clean_cert(raw: str) -> str:
    """Strip everything but digits from a pasted cert string."""
    return _DIGITS.sub("", raw.strip())

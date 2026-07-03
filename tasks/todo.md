# PSA Cert Dual-Lookup — Task Checklist

## Goal / Acceptance Criteria
- Paste a PSA cert into a terminal prompt → two equal side-by-side windows.
- CardLadder (left): opens search overlay, types cert, submits.
- Alt (right): navigates to `browse?query=<cert>`, clicks the first result.
- Uses cached logins (dedicated Playwright profiles); windows stay open and re-query in place.
- Core is UI-agnostic (importable by a future web/Mac app); CLI is a thin layer.

## Checklist
- [x] Scaffold project structure
- [x] `config.py` — profile paths, URLs, window layout
- [x] `windows.py` — launch + side-by-side positioning of two persistent contexts
- [x] `sites/base.py` — SiteDriver protocol
- [x] `sites/alt.py` — navigate + click first result
- [x] `sites/cardladder.py` — overlay search (selectors need live confirmation)
- [x] `controller.py` — LookupController fan-out
- [x] `run.py` — interactive async loop
- [x] `requirements.txt`, `README.md`
- [x] Install deps: `pip install -r requirements.txt && playwright install chromium`
- [x] Smoke-test window launch + side-by-side positioning (no login) — PASS
- [x] Verify login reuse via profile copy — WORKS (user confirmed)
- [x] Finalize live selectors (CardLadder overlay + Alt first result) — CONFIRMED end-to-end

## Working Notes
- Python 3.9.6 at /usr/bin/python3 (`asyncio.to_thread` available).
- Two separate user-data-dirs required (Chromium locks a profile dir).
- Selectors are the main risk — implemented with resilient role/placeholder locators + fallbacks
  and a `SELECTORS` block in each site driver so they're easy to update after live inspection.

## Update — session-reuse pivot (bot detection)
- Fresh login was blocked on both sites: CardLadder looped on Cloudflare "security verification";
  Alt's Google OAuth said "browser may not be secure"; then Alt/Stytch returned 401 on MFA SMS.
- Root cause: Playwright's bundled Chromium advertises automation (`--enable-automation`,
  `navigator.webdriver`). Switched to real Chrome (`channel="chrome"`) + de-automation flags.
- Google/Stytch still blocked fresh login → pivoted (user chose) to **reuse existing Chrome
  session via a profile copy** instead of logging in fresh.
- New `profile.py` copies the real Chrome `Default` profile → `~/.cert-dual-lookup/chrome-profile`
  (skips cache/extensions/service-worker; keeps Cookies/Local Storage/IndexedDB). Verified: 764 MB,
  ~3s, session files present.
- `windows.py` rewritten: ONE persistent context on the copied profile driving TWO windows via
  CDP `Target.createTarget newWindow` (both sites share the one logged-in profile). Dropped
  `--use-mock-keychain` so copied encrypted cookies can decrypt on the same Mac.
- `run.py` gains `--refresh-profile` to re-copy when a session expires.
- STILL TO VERIFY LIVE (needs user): launch → confirm both sites show logged-in (cookie
  decryption / keychain) → then finalize CardLadder overlay + Alt first-result selectors.

## Results
- Built the full tool under `cert-dual-lookup/`. Core in `cert_lookup/` (config, windows,
  sites/, controller); thin CLI in `run.py`.
- Verified: all files compile; deps + Chromium installed; window smoke test opened two
  persistent-context windows and positioned them as equal halves
  (cardladder left=0/w=864, alt left=864/w=864 on a 1728px display) and closed cleanly.
- Finding: Alt browse RESULTS are login-gated — anonymous loads show only nav, no cards.
  URL scheme itself works (title reflects the query). Hardened `alt.py` to skip nav tabs
  (`/browse/fixed-price`, `/browse/auctions`) so a logged-in run clicks a real result.
- Confirmed selectors (verified end-to-end with the real drivers on the logged-in profile):
  - CardLadder trigger is the nav "tag" button `button:has(i.material-icons:has-text("tag"))`
    (the CERT lookup — NOT the magnifying-glass search-icon, which only does a `?q=` keyword
    search). Reveals cert input `input[type=text][maxlength=300]` (placeholder e.g. "63444200",
    not auto-focused). Cert resolves to `?filters=...profileId:psa-<n>` — the correct lookup.
  - Alt first result `button:has(.MuiTypography-vegaButton1)` (MUI button row, NOT an anchor).
    Click navigates to `alt.xyz/itm/<id>/research`.
- Status: FULLY WORKING. User must restart their running `run.py` (PID from earlier session ran
  the pre-selector-fix code); the profile copy already exists so restart is fast.

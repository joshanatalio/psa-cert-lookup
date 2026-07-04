# PSA Cert Dual-Lookup

Enter a PSA cert number once; it drives **CardLadder** and **Alt** in two equal side-by-side
Chrome windows using your existing logged-in sessions.

- **CardLadder** (left): clicks the cert ("tag") button, types the cert, submits → resolves to the
  card's profile page. The resolved URL carries the grade (`grade:g10`); the summary chip gives the
  card name.
- **Alt** (right): navigates to `browse?query=<cert>`, clicks the first result, then switches to the
  slab's grade view via `?grade=PSA-<n>.0` (Alt otherwise defaults to PSA 10) using the grade
  resolved from CardLadder.
- Windows open once and stay open — each new cert re-queries in place.
- **Self-repairing startup:** if a previous instance left the profile locked ("Opening in existing
  browser session" / TargetClosedError), launch kills only *our* leftover Chrome (matched by our
  unique `--user-data-dir`) and clears stale locks, then retries. It never touches other Chrome
  windows.

Three front-ends, same core:
- **`run.py`** — interactive terminal loop.
- **`menubar.py`** — macOS menu-bar app with history and image/clipboard cert extraction.
- **`server.py`** — FastAPI server for phone access (screenshots of both result pages).

## Setup

```bash
cd "cert-dual-lookup"
python3 -m pip install -r requirements.txt
python3 -m playwright install chromium   # only needed if you ever fall back to bundled Chromium
```

The tool runs on a **copy of your real Chrome profile**, so it inherits your existing CardLadder
and Alt logins — no login, OAuth, or MFA to pass. Your normal Chrome stays usable (the copy is
independent). The copy lives at `~/.cert-dual-lookup/chrome-profile`; if a session ever expires,
re-copy your current one with `--refresh-profile` (see below).

## Run — terminal

```bash
python3 run.py                 # normal run
python3 run.py --refresh-profile   # re-copy your Chrome session first (if logged out)
```

Press Enter when the windows are up, then paste a cert and press Enter. Blank line, `q`, or
Ctrl-C quits.

## Run — menu-bar app

```bash
python3 menubar.py
```

A Great Ball icon (`assets/greatball.png`) appears in the menu bar. The two Chrome windows open
**lazily on your first lookup** (not at app launch), and are raised to the front on every lookup.
If you close the windows while the app keeps running, the next lookup just reopens them. Menu:

- **Text field (top)** — type a cert and press ⏎. (Typing + Enter work; ⌘V paste and a blinking
  cursor do not — an NSMenu limitation — so use "Look Up from Clipboard" to paste.)
- **Look Up from Clipboard** — reads a cert from clipboard text, or OCRs a copied image
  (e.g. `Cmd+Ctrl+Shift+4` screenshots straight to the clipboard). This is the paste path.
- **Look Up from Image…** — pick an image file; OCR extracts the cert.
- **Drag & drop** — drop an image file onto the menu-bar icon to look it up (same as Image…).
- **History** — recent lookups shown as "<card name> PSA <grade>" (or "<cert> ？" if the name
  couldn't be resolved); click one to re-run it.
- **Start at Login** — toggle a LaunchAgent so the app auto-starts at login.
- **Quit** — closes the browser windows cleanly.

The app runs as a menu-bar-only accessory (hidden from the Dock / Cmd-Tab switcher); flip
`HIDE_FROM_APP_SWITCHER` in `menubar.py` if you want it visible.

Cert extraction OCRs the whole label with Apple's Vision framework (same engine as Live Text —
offline, private, no extra install) and picks the one 7-10 digit purely-numeric token, which is
always the PSA cert. If more than one number qualifies, it asks you to confirm rather than guess.

## Phone access (`server.py`)

Look up certs from your phone. The Mac does the automation (as always) and sends back
**screenshots** of the two result pages — which sidesteps CardLadder/Alt not being mobile-friendly.

```bash
python3 -m uvicorn server:app --host 0.0.0.0 --port 8000
```

Then, from a browser (Mac or phone), open `http://<host>:8000`, type a cert, and get the label +
two screenshots (CardLadder sales/value, Alt value/populations). The first lookup opens the
browser (~10-15s); later ones reuse it.

- **From your phone:** install **Tailscale** (free) on the Mac and phone, then open
  `http://<mac-name>:8000` on the phone (its Tailscale MagicDNS name). No port-forwarding, no App
  Store. "Add to Home Screen" in Safari for an app-like icon.
- The server uses its **own** profile copy (`~/.cert-dual-lookup/chrome-profile-server`), so it runs
  fine alongside the menu-bar app (Chromium locks a user-data-dir). Both are copies of your one
  logged-in Chrome.
- Requests are serialized (one browser drives both sites). Screenshots are capped in height so the
  phone isn't scrolling a huge image; the key data is near the top.
- Roadmap: Phase 2 adds parsed JSON (prices/sales as structured data — verified feasible), Phase 3
  adds photographing a slab on the phone (reuses the Vision OCR in `cert_extraction.py`).

## Architecture

```
cert_lookup/            # UI-agnostic core (imported by all front-ends)
  config.py             # profile copy source/dest, site URLs, window layout, cert cleaning
  profile.py            # copy the real Chrome profile so logins are inherited
  windows.py            # one Chrome, two side-by-side windows (CDP), on the copied profile
  sites/base.py         # SiteDriver protocol
  sites/cardladder.py   # cert ("tag") button → cert input → submit
  sites/alt.py          # query URL + first-result click (MUI button rows)
  controller.py         # LookupController — fans out to both sites concurrently
  cert_extraction.py    # image → cert via Vision OCR + digit-run filter (macOS-only)
  history.py            # sqlite lookup history (~/.cert-dual-lookup/history.db)
run.py                  # thin interactive CLI
menubar.py              # thin macOS menu-bar front-end
server.py               # FastAPI phone server (screenshots); web/index.html is the mobile page
web/index.html          # mobile page (text box + result screenshots)
```

Any front-end just does:

```python
controller = LookupController()
await controller.start()
await controller.run(cert)   # -> {"cardladder": "ok"|<err>, "alt": "ok"|<err>}
```

## Notes / tuning

- **Selectors** (CardLadder's cert button/input, Alt's first result) are the fragile part, grouped
  at the top of `sites/cardladder.py` and `sites/alt.py` as ordered fallback lists. If a lookup
  reports "no element matched …", inspect the live page and update the matching list. Scope
  input/first-match selectors with `:visible` to avoid dead-waits on hidden duplicates.
- Uses your installed Google **Chrome** (`BROWSER_CHANNEL = "chrome"` in `config.py`) plus
  de-automation flags — real Chrome is what gets past Cloudflare/Google bot checks. The launch
  drops `--use-mock-keychain` so the copied, encrypted cookies decrypt (same-Mac keychain).
- One copied profile drives both windows (second window opened via CDP `Target.createTarget`),
  since Chromium locks a user-data-dir.
```

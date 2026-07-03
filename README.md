# PSA Cert Dual-Lookup

Paste a PSA cert number once; it drives **CardLadder** and **Alt** in two equal side-by-side
browser windows using your logged-in sessions.

- **CardLadder** (left): opens the search overlay, types the cert, submits.
- **Alt** (right): navigates to `browse?query=<cert>` and clicks the first result.
- Windows open once and stay open — each new cert re-queries in place.

## Setup

```bash
cd "cert-dual-lookup"
python3 -m pip install -r requirements.txt
python3 -m playwright install chromium
```

## Run

```bash
python3 run.py
```

On the **first run**, log into CardLadder and Alt in the two windows that open, then press Enter
in the terminal. Sessions are cached in dedicated profiles under
`~/.cert-dual-lookup/profiles/`, so later runs skip straight to the prompt.

Then just paste a cert and press Enter. Blank line or `q` quits.

## Architecture

```
cert_lookup/            # UI-agnostic core (importable by a future web/Mac app)
  config.py             # profile paths, URLs, window layout
  windows.py            # launch + side-by-side positioning (persistent contexts)
  sites/base.py         # SiteDriver protocol
  sites/cardladder.py   # overlay search
  sites/alt.py          # query URL + first-result click
  controller.py         # LookupController — fan out to both sites
run.py                  # thin interactive CLI
```

A future front-end just does:

```python
controller = LookupController()
await controller.start()
await controller.run(cert)
```

## Tuning selectors

The site selectors (CardLadder's overlay trigger/input, Alt's first result) are the fragile part
and are grouped at the top of `sites/cardladder.py` and `sites/alt.py` as ordered fallback lists.
If a lookup fails with a "no element matched …" message, inspect the live page and update the
matching list.

## Notes

- Uses the bundled Chromium. If a site flags automation, set `BROWSER_CHANNEL = "chrome"` in
  `config.py` to drive installed Google Chrome instead.
- Two separate profiles are used because Chromium locks a user-data-dir (two windows can't share
  one).

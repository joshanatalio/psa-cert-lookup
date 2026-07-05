# Lessons

## L1 — Automated fresh login is a losing battle vs. reusing an existing session
- **Failure mode:** Fought Cloudflare (CardLadder) and Google OAuth + Stytch MFA (Alt) trying to
  log in from a clean automated browser. Each wall led to another.
- **Detection signal:** "Performing security verification" loops; "browser may not be secure";
  401 on the MFA send endpoint.
- **Prevention rule:** When a target requires login and the user already has a live session,
  reuse it (copy the real browser profile) instead of automating login. Reserve fresh-login
  automation for sites without strong bot/MFA defenses.

## L2 — Copied Chrome profile needs the real keychain to decrypt cookies
- **Failure mode:** Playwright's default `--use-mock-keychain` makes copied, encrypted cookies
  undecryptable → sites show logged-out despite a correct copy.
- **Prevention rule:** Launch with `ignore_default_args=["--use-mock-keychain"]` (plus
  `--enable-automation`) so OSCrypt uses the real "Chrome Safe Storage" keychain item. Same-Mac
  copies then decrypt. Also: the session lives in Cookies + Local Storage + **IndexedDB** — never
  skip IndexedDB when copying (skipping it silently logs you out; found this while probing).

## L3 — Don't assume result rows are anchors
- **Failure mode:** Alt's search results are Material-UI `<button>` rows
  (`span.MuiTypography-vegaButton1` title), not `<a>` links. Every href-based selector matched
  only nav tabs, so "click first result" silently did nothing.
- **Prevention rule:** Confirm the clickable element type by walking up from the visible title
  text (getComputedStyle cursor, onclick, tag) rather than guessing anchors. Prefer stable
  design-system class names (`MuiTypography-vegaButton1`) over generated hashes (`css-…`).

## L5 — Scope "first visible" selectors with :visible to avoid dead-waits
- **Failure mode:** `page.locator("input[type=text][maxlength=300]").first` resolved to a HIDDEN
  duplicate in an offscreen modal, then `wait_for(state="visible")` dead-waited the full 6s
  timeout before falling through — a ~6s pause on every CardLadder lookup.
- **Detection signal:** consistent multi-second pause right after an action that should reveal an
  element; measured old=6.00s vs new=0.01s.
- **Prevention rule:** when picking "the first visible match", put `:visible` IN the selector
  (`...:visible`) so `.first` only considers visible elements, instead of selecting a hidden one
  and waiting for it. Applies whenever a page keeps hidden duplicates of an input/button in modals.

## L6 — asyncio.to_thread(input) deadlocks shutdown on Ctrl+C
- **Failure mode:** Ctrl+C printed "Closing…" then hung forever. `asyncio.to_thread(input, …)`
  runs input() in the DEFAULT executor; on shutdown asyncio joins those threads, but the input()
  read is still blocked on the terminal → the join never returns.
- **Prevention rule:** read stdin in a `threading.Thread(daemon=True)` bridged to an
  asyncio.Future (daemon threads are never joined), guard `close()` with `asyncio.wait_for(...)`,
  and end the process with `sys.stdout.flush(); os._exit(0)` so a lingering thread/handle can't
  block the shell prompt from returning. Verified: hung 30s close → capped at the timeout.

## L7 — rumps submenus: NSMenu is lazily created on first add()
- **Failure mode:** `MenuItem.clear()` on a freshly-created submenu MenuItem raises
  `AttributeError: 'NoneType' object has no attribute 'removeAllItems'` — its `_menu` (NSMenu)
  doesn't exist until the first `.add()`.
- **Prevention rule:** when rebuilding a rumps submenu, guard the initial `clear()` with
  try/except AttributeError (or only clear if it already has items).

## L8 — Bridge rumps (main-thread Cocoa) and asyncio without freezing or racing
- **Pattern:** run the asyncio loop in a `daemon` thread (`loop.run_forever()`); submit work with
  `asyncio.run_coroutine_threadsafe`; in the future's done-callback (runs on the loop thread) push
  a UI closure onto a `queue.Queue`; drain that queue from a `rumps.Timer` (fires on the main
  thread) so every AppKit mutation happens on the main thread. Lookups stay non-blocking (no
  beachball) and UI updates stay thread-safe.

## L9 — "Opening in existing browser session" = self-repairable profile lock
- **Failure mode:** `launch_persistent_context` intermittently fails with `TargetClosedError` and
  a browser log line `Opening in existing browser session` — a leftover Chrome from a prior/crashed
  run still holds the profile's Singleton lock, so the new Chrome defers to it and the launched
  process exits.
- **Prevention rule:** wrap the launch in a retry that, on failure, kills ONLY our Chrome (match
  processes by our unique `--user-data-dir=<profile>` string — never a broad `pkill chrome`) and
  deletes stale `Singleton*` files, then retries. Safe because the match is scoped to our profile
  path; the user's normal Chrome uses a different user-data-dir. See windows.py `_kill_our_chrome`.

## L10 — Alt item pages default to PSA 10; drive grade from CardLadder
- Alt's `/itm/<id>/research` shows the PSA 10 grade unless you append `?grade=PSA-<n>.0`. The slab's
  grade isn't in the Alt URL — resolve it from CardLadder's filter URL (`grade%3Ag<n>`) and apply it
  to Alt after the first-result click. The readable card name resolves asynchronously in the
  CardLadder summary chip (first shows `psa-<id>`, then the name), so poll for the resolved name.

## L11 — LaunchAgent must use the interpreter with deps installed, not generic `python3`
- **Failure mode:** `which python3` resolves to `/usr/bin/python3`, but the actual running
  interpreter (`sys.executable`) — where `fastapi`/`uvicorn`/`playwright`/`pyobjc` are actually
  installed — is a different path (`/Library/Developer/CommandLineTools/.../Python3.framework/...`).
  A LaunchAgent built with the wrong path fails with `ModuleNotFoundError` since `pip install`
  targets the interpreter you ran it with, not whatever's first on PATH.
- **Prevention rule:** when generating a LaunchAgent/cron `ProgramArguments`, use `sys.executable`
  (captured at the time deps were installed) — never assume `python3`/`python` on PATH matches.

## L12 — Headless Chrome CAN still parse the DOM while off-screen (but not minimized)
- Same finding as the earlier screenshot lesson, reconfirmed for parsing: a window positioned
  off-screen (`left: -4000`, `windowState: "normal"`) still renders and its DOM is fully readable
  by `page.evaluate()`; a **minimized** window does not render and reads as empty. Off-screen, not
  minimized, is the trick for "hidden but functional."

## L13 — FastAPI route signatures need real Python 3.9-compatible types, even with `from __future__ import annotations`
- **Failure mode:** `async def lookup(cert: str, grade: str | None = Query(None))` compiled fine
  (`py_compile` doesn't catch it) but crashed at request time: `TypeError: Unable to evaluate type
  annotation 'str | None'`. FastAPI/pydantic calls `get_type_hints()` on route handlers to build
  request validation, which actually `eval()`s the string — and PEP 604 `X | Y` isn't valid at
  runtime before Python 3.10, regardless of the `__future__` import deferring *when* it's evaluated.
- **Prevention rule:** in FastAPI (or any framework that runtime-introspects annotations) route
  signatures on Python <3.10, use `typing.Optional[str]`/`Union`, not `str | None` — even though
  `X | None` is fine everywhere else in the same file for plain functions nothing ever `eval()`s.
  Also: **actually start the server** after a route change, don't just `py_compile` — this class of
  bug only surfaces at runtime.

## L14 — A "settle" wait must check for real data, not a label that exists before data loads
- **Failure mode:** waited for the text "Date Sold" to appear before parsing CardLadder's sales —
  but "Date Sold" is *also* the static column-header label, rendered immediately as part of the
  page's skeleton, before any actual sale row loads. This gave a false-positive "ready" signal.
  It "worked" on the normal search path only by accident (other incidental waits elsewhere padded
  enough real time), and broke on a faster path (grade-switch) that had no such padding, returning
  zero sales.
- **Prevention rule:** wait for the presence of an actual **data element** (a real row/link — e.g.
  `document.querySelectorAll('a.list-item.clickable').length > 0`, or an anchor to the external
  source like `a[href*="ebay.com/itm"]`), never a label/heading string, which can exist in a
  page's static template before any data has loaded. Put this wait *inside* the parser itself so
  it can't be skipped by a caller that takes a different, faster path.

## L4 — Inspect a locked profile via a throwaway copy
- When the user's tool holds the profile lock, copy the (already logged-in) profile to a scratch
  dir and inspect there — no need to interrupt their running session. Include IndexedDB so the
  copy stays authenticated (see L2).

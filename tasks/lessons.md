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

## L4 — Inspect a locked profile via a throwaway copy
- When the user's tool holds the profile lock, copy the (already logged-in) profile to a scratch
  dir and inspect there — no need to interrupt their running session. Include IndexedDB so the
  copy stays authenticated (see L2).

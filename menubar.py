#!/usr/bin/env python3
"""macOS menu-bar front-end for the cert dual-lookup tool.

Thin UI layer over the existing core (LookupController), exactly like run.py — no browser logic
lives here. Adds: type/paste a cert, look up from the clipboard or an image (Vision OCR), and a
History submenu of recent certs you can re-run.

Threading model: rumps owns the Cocoa main thread; the async core runs on a background asyncio
loop. Menu callbacks (main thread) submit coroutines to that loop with
run_coroutine_threadsafe and never block — when a coroutine finishes, its UI follow-up is pushed
onto a queue that a main-thread rumps.Timer drains, so all AppKit mutations stay on the main
thread.
"""

from __future__ import annotations

import asyncio
import queue
import sys
import threading
import traceback

import objc
import rumps
from AppKit import NSDragOperationCopy, NSImage, NSStatusBarButton, NSTextField, NSView
from Foundation import NSObject

from cert_lookup import LookupController, cert_extraction, history
from cert_lookup.config import clean_cert

# Set to the running app instance so the (class-level) drag category can reach it.
_APP = None


class _FieldTarget(NSObject):
    """Target for the inline menu text field; fires the handler on Enter."""

    def initWithHandler_(self, handler):
        self = objc.super(_FieldTarget, self).init()
        if self is None:
            return None
        self._handler = handler
        return self

    def submit_(self, sender):
        try:
            self._handler(sender.stringValue())
        except Exception:
            traceback.print_exc()


# Add drag-and-drop handling to the menu-bar status button. Only our button registers for
# dragged types, so only it receives these callbacks. Guarded so a category failure can't stop
# the app from importing/launching.
try:

    class NSStatusBarButton(objc.Category(NSStatusBarButton)):  # noqa: F811 - category must match class name
        def draggingEntered_(self, sender):
            return NSDragOperationCopy

        def draggingUpdated_(self, sender):
            return NSDragOperationCopy

        def prepareForDragOperation_(self, sender):
            return True

        def performDragOperation_(self, sender):
            if _APP is not None:
                try:
                    _APP._handle_drop(sender.draggingPasteboard())
                except Exception:
                    traceback.print_exc()
            return True

    _DRAG_CATEGORY_OK = True
except Exception:  # noqa: BLE001
    traceback.print_exc()
    _DRAG_CATEGORY_OK = False


class CertLookupApp(rumps.App):
    def __init__(self) -> None:
        super().__init__("Cert Lookup", title="🔎", quit_button=None)
        global _APP
        _APP = self
        self._loop = asyncio.new_event_loop()
        self._controller: LookupController | None = None
        self._ready = False
        self._ui_queue: queue.Queue = queue.Queue()
        self._drag_registered = False
        self._cert_field = None

        self._history_menu = rumps.MenuItem("History")
        self.menu = [
            self._build_field_item(),
            None,
            rumps.MenuItem("Look Up Cert…", callback=self.on_lookup_cert),
            rumps.MenuItem("Look Up from Clipboard", callback=self.on_lookup_clipboard),
            rumps.MenuItem("Look Up from Image…", callback=self.on_lookup_image),
            None,
            self._history_menu,
            None,
            rumps.MenuItem("Quit", callback=self.on_quit),
        ]
        self._refresh_history()

        # Background asyncio loop + one-shot startup (opens the two browser windows).
        threading.Thread(target=self._run_loop, daemon=True).start()
        self._submit(self._startup(), self._on_startup_done)

        # Drain UI-update callbacks on the main thread.
        self._timer = rumps.Timer(self._drain_ui, 0.3)
        self._timer.start()

    # ---- asyncio bridge -------------------------------------------------------------------
    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    async def _startup(self) -> None:
        self._controller = LookupController()
        await self._controller.start()

    def _submit(self, coro, on_done=None):
        """Schedule a coroutine on the background loop; on_done(result, error) runs on main thread."""
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        if on_done is not None:
            def _cb(f):
                try:
                    result, error = f.result(), None
                except Exception as exc:  # noqa: BLE001 - surfaced to the user via alert
                    result, error = None, exc
                    # Full traceback to the terminal so failures are debuggable, not just a
                    # terse modal.
                    traceback.print_exception(type(exc), exc, exc.__traceback__)
                    sys.stderr.flush()
                self._ui_queue.put(lambda: on_done(result, error))
            fut.add_done_callback(_cb)
        return fut

    def _drain_ui(self, _timer) -> None:
        if not self._drag_registered:
            self._register_drag()
        while True:
            try:
                fn = self._ui_queue.get_nowait()
            except queue.Empty:
                return
            try:
                fn()
            except Exception as exc:  # noqa: BLE001
                print("UI update error:", exc)

    # ---- inline text field + drag/drop ----------------------------------------------------
    def _build_field_item(self):
        """Top menu item hosting an editable text field: paste a cert, press Enter."""
        item = rumps.MenuItem("cert-field")
        try:
            self._field_target = _FieldTarget.alloc().initWithHandler_(self._field_submitted)
            container = NSView.alloc().initWithFrame_(((0, 0), (240, 30)))
            field = NSTextField.alloc().initWithFrame_(((14, 4), (212, 22)))
            field.setPlaceholderString_("Paste cert #, press ⏎")
            field.setTarget_(self._field_target)
            field.setAction_("submit:")
            container.addSubview_(field)
            item._menuitem.setView_(container)
            self._cert_field = field
        except Exception:  # noqa: BLE001 - fall back to the "Look Up Cert…" dialog item
            traceback.print_exc()
            item.title = "Look Up Cert…"
            item.set_callback(self.on_lookup_cert)
        return item

    def _field_submitted(self, value: str) -> None:
        value = (value or "").strip()
        try:
            self._menu._menu.cancelTracking()  # close the open menu
        except Exception:  # noqa: BLE001
            pass
        if self._cert_field is not None:
            self._cert_field.setStringValue_("")
        if value:
            self._do_lookup(value)

    def _register_drag(self) -> None:
        # Attempt once (after launch, when the status button exists); never retry-spam.
        self._drag_registered = True
        if not _DRAG_CATEGORY_OK:
            return
        try:
            button = self._nsapp.nsstatusitem.button()
            if button is not None:
                button.registerForDraggedTypes_(
                    ["public.file-url", "public.png", "public.tiff", "public.jpeg"]
                )
        except Exception:  # noqa: BLE001
            traceback.print_exc()

    def _handle_drop(self, pasteboard) -> None:
        """Called on the main thread when an image is dropped on the menu-bar icon."""
        image = NSImage.alloc().initWithPasteboard_(pasteboard)
        if image is None:
            rumps.alert("Couldn't read drop", "That wasn't a readable image.")
            return
        tiff = image.TIFFRepresentation()
        if tiff is None:
            rumps.alert("Couldn't read drop", "No image data in the drop.")
            return
        try:
            candidates = cert_extraction.extract_certs(bytes(tiff))
        except Exception as exc:  # noqa: BLE001
            rumps.alert("OCR error", str(exc))
            return
        cert = self._pick_candidate(candidates)
        if cert:
            self._do_lookup(cert)

    def _on_startup_done(self, _result, error) -> None:
        if error is not None:
            self.title = "🔎⚠️"
            hint = ""
            if "ProcessSingleton" in str(error) or "already in use" in str(error):
                hint = (
                    "\n\nThe browser profile is already in use — another copy of this app or "
                    "run.py is probably still running. Quit it, then relaunch.\nYou can still "
                    "Quit this window from the menu."
                )
            rumps.alert("Startup failed", f"{error}{hint}")
        else:
            self._ready = True
            self.title = "🔎"

    # ---- lookups --------------------------------------------------------------------------
    def _do_lookup(self, raw_cert: str) -> None:
        if not self._ready:
            rumps.alert("Still starting", "The browser windows are still opening — try again in a moment.")
            return
        cert = clean_cert(raw_cert)
        if not cert:
            rumps.alert("No cert", "That didn't contain a cert number.")
            return

        self.title = "🔎…"

        def done(status, error):
            self.title = "🔎"
            if error is not None:
                rumps.alert("Lookup error", str(error))
                return
            history.record(cert, status or {})
            self._refresh_history()
            failed = {k: v for k, v in (status or {}).items() if v != "ok"}
            if failed:
                detail = "\n".join(f"• {k}: {v}" for k, v in failed.items())
                rumps.alert(f"Looked up {cert} (with issues)", detail)

        self._submit(self._controller.run(cert), done)

    def on_lookup_cert(self, _) -> None:
        resp = rumps.Window(
            message="Enter a PSA cert number:",
            title="Look Up Cert",
            dimensions=(220, 22),
        ).run()
        if resp.clicked and resp.text.strip():
            self._do_lookup(resp.text)

    def on_lookup_clipboard(self, _) -> None:
        cert = self._cert_from_clipboard()
        if cert:
            self._do_lookup(cert)

    def on_lookup_image(self, _) -> None:
        path = self._choose_image()
        if not path:
            return
        try:
            candidates = cert_extraction.extract_certs(path)
        except Exception as exc:  # noqa: BLE001
            rumps.alert("OCR error", str(exc))
            return
        cert = self._pick_candidate(candidates)
        if cert:
            self._do_lookup(cert)

    # ---- clipboard / image / candidate helpers --------------------------------------------
    def _cert_from_clipboard(self):
        from AppKit import NSImage, NSPasteboard, NSPasteboardTypeString

        pb = NSPasteboard.generalPasteboard()
        text = pb.stringForType_(NSPasteboardTypeString)
        if text:
            certs = cert_extraction.certs_from_text(text)
            if certs:
                return self._pick_candidate(certs)

        image = NSImage.alloc().initWithPasteboard_(pb)
        if image is not None:
            tiff = image.TIFFRepresentation()
            if tiff is not None:
                try:
                    certs = cert_extraction.extract_certs(bytes(tiff))
                except Exception as exc:  # noqa: BLE001
                    rumps.alert("OCR error", str(exc))
                    return None
                if certs:
                    return self._pick_candidate(certs)

        rumps.alert("No cert found", "No cert number in the clipboard text or image.")
        return None

    def _choose_image(self):
        from AppKit import NSOpenPanel

        panel = NSOpenPanel.openPanel()
        panel.setAllowsMultipleSelection_(False)
        panel.setCanChooseDirectories_(False)
        panel.setAllowedFileTypes_(["png", "jpg", "jpeg", "tiff", "tif", "heic", "gif", "bmp"])
        if panel.runModal() == 1:  # NSModalResponseOK
            urls = panel.URLs()
            if urls and len(urls):
                return urls[0].path()
        return None

    def _pick_candidate(self, candidates):
        if not candidates:
            rumps.alert("No cert found", "No cert-shaped number (7-10 digits) was detected.")
            return None
        if len(candidates) == 1:
            return candidates[0]
        resp = rumps.Window(
            message="Multiple numbers matched: " + ", ".join(candidates) + "\nConfirm the cert:",
            title="Pick cert",
            default_text=candidates[0],
            dimensions=(220, 22),
        ).run()
        if resp.clicked and resp.text.strip():
            return clean_cert(resp.text)
        return None

    # ---- history --------------------------------------------------------------------------
    def _refresh_history(self) -> None:
        # rumps lazily creates a submenu's NSMenu on the first add(), so clear() has nothing to
        # act on the very first time — guard it.
        try:
            self._history_menu.clear()
        except AttributeError:
            pass
        entries = history.recent(10)
        if not entries:
            self._history_menu.add(rumps.MenuItem("(no history yet)"))
            return
        for entry in entries:
            mark = "✓" if (entry.cardladder == "ok" and entry.alt == "ok") else "•"
            item = rumps.MenuItem(f"{mark} {entry.cert}", callback=self._make_history_cb(entry.cert))
            self._history_menu.add(item)

    def _make_history_cb(self, cert: str):
        def _cb(_):
            self._do_lookup(cert)
        return _cb

    # ---- quit -----------------------------------------------------------------------------
    def on_quit(self, _) -> None:
        if self._controller is not None:
            try:
                fut = asyncio.run_coroutine_threadsafe(self._controller.close(), self._loop)
                fut.result(timeout=8)
            except Exception:  # noqa: BLE001
                pass
        rumps.quit_application()


if __name__ == "__main__":
    CertLookupApp().run()

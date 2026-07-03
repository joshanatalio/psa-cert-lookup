"""Extract a PSA cert number from an image using Apple's Vision framework OCR.

A PSA slab/label, OCR'd, yields every printed token (title, HP, set number, grade, illustrator,
attack text…). The cert is reliably the ONE long, purely-numeric token: PSA certs are 7-10 digits
with no letters or separators, while every other number on a card is short (10, 40, 106) or has
non-digit characters (106/112, AF5-B8B-OFN). So extraction is just OCR + a digit-run regex — no
layout parsing or CV needed.

Vision (VNRecognizeTextRequest) is the same engine behind macOS Live Text: free, offline, private,
and needs no brew/tesseract. This module is macOS-only and imported only by the UI layer, so the
rest of cert_lookup stays platform-agnostic.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Union

import Quartz
import Vision
from Foundation import NSData

# PSA cert numbers seen so far: 7-10 digits, purely numeric (67919646, 72678096, 122192990,
# 157341727). \b ensures we don't grab a slice of a longer alphanumeric token.
CERT_PATTERN = re.compile(r"\b\d{7,10}\b")


class ExtractionError(Exception):
    """OCR could not run (bad/unreadable image)."""


def certs_from_text(text: str) -> list[str]:
    """Return candidate cert strings found in OCR text, deduped, in first-seen order."""
    seen: list[str] = []
    for match in CERT_PATTERN.findall(text):
        if match not in seen:
            seen.append(match)
    return seen


def ocr_text(source: Union[str, Path, bytes]) -> str:
    """Run Vision OCR on an image (file path or raw bytes) and return all recognized text."""
    cg_image = _load_cgimage(source)
    if cg_image is None:
        raise ExtractionError("Could not decode the image (unsupported format or corrupt data).")

    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    # Disable language correction — it can "helpfully" mangle raw digit strings.
    request.setUsesLanguageCorrection_(False)

    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg_image, {})
    success, error = handler.performRequests_error_([request], None)
    if not success:
        raise ExtractionError(f"Vision OCR failed: {error}")

    lines: list[str] = []
    for observation in request.results() or []:
        candidates = observation.topCandidates_(1)
        if candidates:
            lines.append(candidates[0].string())
    return "\n".join(lines)


def extract_certs(source: Union[str, Path, bytes]) -> list[str]:
    """OCR an image and return the candidate cert strings (deduped, in reading order).

    Callers decide what to do with the result:
      - exactly one  -> use it
      - zero         -> no cert found
      - more than one -> ask the user to pick (don't guess silently)
    """
    return certs_from_text(ocr_text(source))


def _load_cgimage(source: Union[str, Path, bytes]):
    if isinstance(source, (bytes, bytearray)):
        data = NSData.dataWithBytes_length_(bytes(source), len(source))
        img_source = Quartz.CGImageSourceCreateWithData(data, None)
    else:
        from Foundation import NSURL

        url = NSURL.fileURLWithPath_(str(source))
        img_source = Quartz.CGImageSourceCreateWithURL(url, None)
    if img_source is None:
        return None
    return Quartz.CGImageSourceCreateImageAtIndex(img_source, 0, None)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("usage: python -m cert_lookup.cert_extraction <image-path>")
        raise SystemExit(2)
    path = sys.argv[1]
    print("--- OCR text ---")
    text = ocr_text(path)
    print(text)
    print("--- cert candidates ---")
    print(certs_from_text(text))

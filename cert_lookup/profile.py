"""Copy the user's real Chrome profile so the tool inherits existing logins.

We copy the chosen Chrome profile folder into the tool's own user-data-dir once. Because it's
the same Mac (same "Chrome Safe Storage" keychain item), the copied, encrypted cookies still
decrypt when the browser is launched WITHOUT --use-mock-keychain (see windows.py). The copy is
tolerant of files changing mid-copy, so your normal Chrome can stay open.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from . import config


def available_profiles() -> list[str]:
    base = config.CHROME_USER_DATA_DIR
    if not base.exists():
        return []
    return sorted(
        p.name
        for p in base.iterdir()
        if p.is_dir() and (p.name == "Default" or p.name.startswith("Profile "))
    )


def ensure_copy(refresh: bool = False) -> bool:
    """Ensure a working copy of the Chrome profile exists. Returns True if a copy was made."""
    dest_root = config.TOOL_PROFILE
    dest_profile = dest_root / "Default"
    src_profile = config.CHROME_USER_DATA_DIR / config.SOURCE_CHROME_PROFILE

    if dest_profile.exists() and not refresh:
        return False

    if not src_profile.exists():
        found = ", ".join(available_profiles()) or "none found"
        raise FileNotFoundError(
            f"Chrome profile not found at {src_profile}.\n"
            f"Set SOURCE_CHROME_PROFILE in config.py to one of: {found}."
        )

    if dest_profile.exists():
        shutil.rmtree(dest_profile, ignore_errors=True)
    dest_root.mkdir(parents=True, exist_ok=True)

    errors = _copy_tree_tolerant(src_profile, dest_profile, config.PROFILE_COPY_SKIP_DIRS)

    # Local State lives at the user-data-dir root (profile list + os_crypt key material).
    local_state = config.CHROME_USER_DATA_DIR / "Local State"
    if local_state.exists():
        try:
            shutil.copy2(local_state, dest_root / "Local State")
        except OSError as e:
            errors.append(f"Local State: {e}")

    if errors:
        # A handful of skipped files (locked/vanished while Chrome is open) is expected and
        # harmless; surface the count so real problems are visible.
        print(f"  (profile copy skipped {len(errors)} transient file(s) — normal with Chrome open)")
    return True


def _copy_tree_tolerant(src: Path, dst: Path, skip_dirs: set[str]) -> list[str]:
    """Copy src -> dst, pruning skip_dirs and skipping any file that errors mid-copy."""
    errors: list[str] = []
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        rel = Path(root).relative_to(src)
        target_dir = dst / rel
        target_dir.mkdir(parents=True, exist_ok=True)
        for name in files:
            source = Path(root) / name
            try:
                shutil.copy2(source, target_dir / name)
            except OSError as e:
                errors.append(f"{source}: {e}")
    return errors

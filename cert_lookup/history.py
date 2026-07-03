"""Lookup history, persisted with stdlib sqlite3 (no new dependency).

Stores one row per lookup at ~/.cert-dual-lookup/history.db so the menu-bar app can show recent
certs and let you re-run them. Kept deliberately tiny; the UI layer owns presentation.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass

from . import config

DB_PATH = config.TOOL_DATA_ROOT / "history.db"


@dataclass
class HistoryEntry:
    cert: str
    ts: float          # unix seconds of the most recent lookup
    cardladder: str    # "ok" or an error message
    alt: str
    label: str         # display label (card name + grade), or "<cert> ？" if unresolved


def _connect() -> sqlite3.Connection:
    config.TOOL_DATA_ROOT.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS lookups (
            cert       TEXT PRIMARY KEY,
            ts         REAL NOT NULL,
            cardladder TEXT NOT NULL DEFAULT '',
            alt        TEXT NOT NULL DEFAULT '',
            label      TEXT NOT NULL DEFAULT '',
            count      INTEGER NOT NULL DEFAULT 1
        )
        """
    )
    # Migrate older DBs that predate the label column.
    cols = {row[1] for row in conn.execute("PRAGMA table_info(lookups)")}
    if "label" not in cols:
        conn.execute("ALTER TABLE lookups ADD COLUMN label TEXT NOT NULL DEFAULT ''")
    return conn


def record(cert: str, status: dict[str, str], label: str = "") -> None:
    """Upsert a lookup. Repeated certs update the timestamp/status/label and bump a count."""
    now = time.time()
    cl = status.get("cardladder", "")
    alt = status.get("alt", "")
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO lookups (cert, ts, cardladder, alt, label, count)
            VALUES (?, ?, ?, ?, ?, 1)
            ON CONFLICT(cert) DO UPDATE SET
                ts = excluded.ts,
                cardladder = excluded.cardladder,
                alt = excluded.alt,
                label = excluded.label,
                count = count + 1
            """,
            (cert, now, cl, alt, label),
        )


def recent(limit: int = 10) -> list[HistoryEntry]:
    """Return the most-recently-looked-up certs, newest first."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT cert, ts, cardladder, alt, label FROM lookups ORDER BY ts DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [
        HistoryEntry(cert=r[0], ts=r[1], cardladder=r[2], alt=r[3], label=r[4] or r[0])
        for r in rows
    ]


def clear() -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM lookups")

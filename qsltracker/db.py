"""SQLite database schema and data access layer for qsltracker.

Tables:

- ``QSOs``: one row per logged contact, keyed by an ``id`` primary key
  plus the 20 tracked ADIF columns. Uniqueness is enforced on
  ``(CALL, STATION_CALLSIGN, QSO_DATE, TIME_ON, BAND, MODE)``.
- ``batch``: named label-export batches mapping a batch name to QSO ids.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("qsltracker.db")

QSO_COLUMNS = (
    "CALL",
    "STATION_CALLSIGN",
    "QSO_DATE",
    "TIME_ON",
    "BAND",
    "MODE",
    "SUBMODE",
    "RST_SENT",
    "MY_GRIDSQUARE",
    "MY_CQ_ZONE",
    "MY_ITU_ZONE",
    "MY_CNTY",
    "MY_STATE",
    "QSLRDATE",
    "QSLSDATE",
    "QSL_RCVD",
    "QSL_RCVD_VIA",
    "QSL_SENT",
    "QSL_SENT_VIA",
    "QSL_VIA",
)

DUPLICATE_KEY_COLUMNS = (
    "CALL",
    "STATION_CALLSIGN",
    "QSO_DATE",
    "TIME_ON",
    "BAND",
    "MODE",
)

QSL_UPDATE_COLUMNS = frozenset(
    {
        "QSLRDATE",
        "QSLSDATE",
        "QSL_RCVD",
        "QSL_RCVD_VIA",
        "QSL_SENT",
        "QSL_SENT_VIA",
        "QSL_VIA",
    }
)

RECEIVED_VIA_DIRECT = "D"
RECEIVED_VIA_BUREAU = "B"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS QSOs (
    id INTEGER PRIMARY KEY,
    CALL TEXT,
    STATION_CALLSIGN TEXT,
    QSO_DATE TEXT,
    TIME_ON TEXT,
    BAND TEXT,
    MODE TEXT,
    SUBMODE TEXT,
    RST_SENT TEXT,
    MY_GRIDSQUARE TEXT,
    MY_CQ_ZONE TEXT,
    MY_ITU_ZONE TEXT,
    MY_CNTY TEXT,
    MY_STATE TEXT,
    QSLRDATE TEXT,
    QSLSDATE TEXT,
    QSL_RCVD TEXT,
    QSL_RCVD_VIA TEXT,
    QSL_SENT TEXT,
    QSL_SENT_VIA TEXT,
    QSL_VIA TEXT,
    UNIQUE (CALL, STATION_CALLSIGN, QSO_DATE, TIME_ON, BAND, MODE)
);

CREATE TABLE IF NOT EXISTS batch (
    name TEXT,
    qso_id INTEGER REFERENCES QSOs(id)
);
"""


def get_default_db_path() -> Path:
    """Return the default database path, honoring the current HOME."""
    return Path.home() / ".config" / "qsltracker" / "qsos.db"


def init_schema(conn: sqlite3.Connection) -> None:
    """Create the ``QSOs`` and ``batch`` tables if they do not exist."""
    conn.executescript(SCHEMA_SQL)
    conn.commit()


def connect(path: str | Path) -> sqlite3.Connection:
    """Open a database, enabling foreign keys and creating the schema."""
    if isinstance(path, Path):
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_schema(conn)
    return conn


def utc_today_str() -> str:
    """Return today's date in UTC as an ADIF ``YYYYMMDD`` string."""
    return datetime.now(timezone.utc).strftime("%Y%m%d")


UPPERCASE_COLUMNS = ("CALL", "MODE", "BAND")


def normalize_qso(data: Mapping[str, Any]) -> dict[str, Any]:
    """Project a record onto the 20 tracked columns, blanking missing values.

    ``CALL``, ``MODE``, and ``BAND`` are canonicalized to uppercase so
    case variants share one identity for duplicate detection, search,
    and label grouping.
    """
    record = {}
    for column in QSO_COLUMNS:
        value = data.get(column)
        record[column] = "" if value is None else value
    for column in UPPERCASE_COLUMNS:
        record[column] = str(record[column]).upper()
    return record


def duplicate_key(record: Mapping[str, Any]) -> tuple[Any, ...]:
    """Return the 6-column uniqueness key for a (normalized) QSO record."""
    return tuple(record.get(column, "") for column in DUPLICATE_KEY_COLUMNS)


def insert_qsos(
    conn: sqlite3.Connection, qsos: Iterable[Mapping[str, Any]]
) -> tuple[int, list[tuple[Any, ...]]]:
    """Insert QSOs, skipping duplicates.

    Returns ``(inserted_count, duplicate_keys)`` where each duplicate key
    is a ``(CALL, STATION_CALLSIGN, QSO_DATE, TIME_ON, BAND, MODE)`` tuple
    for a record that was already present (or repeated within the batch).
    """
    columns = ", ".join(QSO_COLUMNS)
    placeholders = ", ".join("?" for _ in QSO_COLUMNS)
    inserted = 0
    duplicates: list[tuple[Any, ...]] = []
    for qso in qsos:
        record = normalize_qso(qso)
        try:
            conn.execute(
                f"INSERT INTO QSOs ({columns}) VALUES ({placeholders})",
                [record[column] for column in QSO_COLUMNS],
            )
        except sqlite3.IntegrityError:
            duplicates.append(duplicate_key(record))
        else:
            inserted += 1
    conn.commit()
    return inserted, duplicates


def get_qso(conn: sqlite3.Connection, qso_id: int) -> dict[str, Any] | None:
    """Fetch a single QSO by id, or None when it does not exist."""
    row = conn.execute("SELECT * FROM QSOs WHERE id = ?", (qso_id,)).fetchone()
    return dict(row) if row is not None else None


def search_qsos(conn: sqlite3.Connection, call: str) -> list[dict[str, Any]]:
    """Search QSOs by ``CALL``, case-insensitively."""
    call = (call or "").strip()
    if not call:
        return []
    rows = conn.execute(
        "SELECT * FROM QSOs WHERE CALL = ? COLLATE NOCASE ORDER BY id",
        (call,),
    ).fetchall()
    return [dict(row) for row in rows]


def list_all_qsos(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return every QSO ordered by id (used for a blank search)."""
    rows = conn.execute("SELECT * FROM QSOs ORDER BY id").fetchall()
    return [dict(row) for row in rows]


def _format_key(record: Mapping[str, Any]) -> str:
    """Render the 6-column uniqueness key of a QSO record for log lines."""
    return " ".join(str(record.get(column) or "") for column in DUPLICATE_KEY_COLUMNS)


def _log_row_changes(
    before: dict[str, Any] | None, after: dict[str, Any] | None, columns: Iterable[str]
) -> None:
    """Log one line per changed column (key columns, then the change)."""
    if before is None or after is None:
        return
    changes = [
        f"{column} {before.get(column) or ''!r}->{after.get(column) or ''!r}"
        for column in columns
        if (before.get(column) or "") != (after.get(column) or "")
    ]
    if changes:
        logger.info("%s: %s", _format_key(before), ", ".join(changes))


def _update_ids(
    conn: sqlite3.Connection,
    set_clause: str,
    params: list[Any],
    columns: Iterable[str],
    qso_ids: Iterable[int],
) -> int:
    ids = list(qso_ids)
    if not ids:
        return 0
    before = {qso_id: get_qso(conn, qso_id) for qso_id in ids}
    placeholders = ", ".join("?" for _ in ids)
    cursor = conn.execute(
        f"UPDATE QSOs SET {set_clause} WHERE id IN ({placeholders})",
        [*params, *ids],
    )
    conn.commit()
    for qso_id in ids:
        _log_row_changes(before[qso_id], get_qso(conn, qso_id), columns)
    return cursor.rowcount


def mark_received(
    conn: sqlite3.Connection, qso_ids: Iterable[int], via: str
) -> int:
    """Mark QSOs as received (``QSL_RCVD = 'Y'`` with today's UTC date).

    ``via`` is ``'D'`` (Direct) or ``'B'`` (Bureau); anything else raises
    ``ValueError``. Returns the number of rows updated.
    """
    via = (via or "").upper()
    if via not in (RECEIVED_VIA_DIRECT, RECEIVED_VIA_BUREAU):
        raise ValueError(f"via must be 'D' or 'B', got {via!r}")
    return _update_ids(
        conn,
        "QSL_RCVD = 'Y', QSLRDATE = ?, QSL_RCVD_VIA = ?",
        [utc_today_str(), via],
        ("QSL_RCVD", "QSLRDATE", "QSL_RCVD_VIA"),
        qso_ids,
    )


def mark_queued_to_send(conn: sqlite3.Connection, qso_ids: Iterable[int]) -> int:
    """Mark QSOs queued to send (``QSL_SENT = 'Q'``)."""
    return _update_ids(conn, "QSL_SENT = 'Q'", [], ("QSL_SENT",), qso_ids)


def update_qso_fields(
    conn: sqlite3.Connection, updates: Mapping[int, Mapping[str, Any]]
) -> int:
    """Bulk-update QSL routing fields, e.g. from QRZ lookups.

    ``updates`` maps a QSO id to a ``{column: value}`` mapping. Only the
    ``QSL_*`` routing/status columns may be updated; any other column
    raises ``ValueError``. Returns the number of rows updated.
    """
    total = 0
    for qso_id, fields in updates.items():
        for column in fields:
            if column not in QSL_UPDATE_COLUMNS:
                raise ValueError(f"Cannot bulk-update column {column!r}")
        if not fields:
            continue
        before = get_qso(conn, qso_id)
        set_clause = ", ".join(f"{column} = ?" for column in fields)
        cursor = conn.execute(
            f"UPDATE QSOs SET {set_clause} WHERE id = ?",
            [*fields.values(), qso_id],
        )
        total += cursor.rowcount
        _log_row_changes(before, get_qso(conn, qso_id), fields)
    conn.commit()
    return total

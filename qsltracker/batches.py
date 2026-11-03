"""Batch management validation and CSV export engine for qsltracker.

Batches group QSOs for label printing. Adding QSOs to a batch validates:

- Every selected QSO has ``QSL_SENT == 'Q'`` (queued to send).
- A batch never mixes bureau (``QSL_SENT_VIA == 'B'``) and non-bureau
  cards, checking both the newly selected QSOs and the QSOs already in
  the target batch.

Export writes ``<base>_qsos.csv`` label records (grouped QSOs sharing one
address label, split into chunks of ``qsos_per_label``) and, for
non-bureau batches, a 1-to-1 ``<base>_addresses.csv`` with postal
addresses from the cached QRZ service. Exported QSOs are marked sent,
and a DB-wide ``all-sent.csv`` log can be rewritten transactionally.
"""

from __future__ import annotations

import csv
import logging
import os
import sqlite3
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from qsltracker.config import DEFAULT_QSOS_PER_LABEL
from qsltracker.db import get_qso, update_qso_fields, utc_today_str

logger = logging.getLogger("qsltracker.export")

LABEL_GROUP_COLUMNS = (
    "CALL",
    "STATION_CALLSIGN",
    "QSL_VIA",
    "MY_GRIDSQUARE",
    "MY_CQ_ZONE",
    "MY_ITU_ZONE",
    "MY_CNTY",
    "MY_STATE",
)

QSO_SLOT_COLUMNS = ("QSO_DATE", "TIME_ON", "BAND", "MODE", "SUBMODE", "RST_SENT")

ADDRESS_COLUMNS = ("name_fmt", "addr1", "addr2", "state", "zip", "country")


class BatchError(ValueError):
    """Raised when QSOs fail batch assignment validation."""


@dataclass
class ExportResult:
    """Outcome of a batch export."""

    qsos_path: Path
    addresses_path: Path | None
    labels: int
    is_bureau: bool
    marked_sent: int = 0


SENT_LOG_COLUMNS = (
    "CALL",
    "STATION_CALLSIGN",
    "QSO_DATE",
    "TIME_ON",
    "BAND",
    "MODE",
    "SUBMODE",
    "QSL_SENT_VIA",
)


def mark_batch_sent(
    conn: sqlite3.Connection, qsos: list[dict[str, Any]]
) -> int:
    """Mark exported QSOs sent (``QSL_SENT = 'Y'`` with today's UTC date).

    ``QSLSDATE`` is set to today and blank ``QSL_SENT_VIA`` values default
    to ``'D'`` (Direct). Returns the number of rows updated.
    """
    today = utc_today_str()
    updates: dict[int, dict[str, str]] = {}
    for qso in qsos:
        if qso.get("QSL_SENT") == "Y":
            continue
        fields = {"QSL_SENT": "Y", "QSLSDATE": today}
        if not (qso.get("QSL_SENT_VIA") or ""):
            fields["QSL_SENT_VIA"] = "D"
        updates[qso["id"]] = fields
    return update_qso_fields(conn, updates)


def write_sent_log(conn: sqlite3.Connection, path: str | Path) -> Path:
    """Write the DB-wide sent-QSO log (``QSL_SENT = 'Y'``) transactionally.

    The file is written to a temp file in the target directory and moved
    into place, so readers never see a partial log.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    rows = conn.execute(
        "SELECT * FROM QSOs WHERE QSL_SENT = 'Y' ORDER BY id"
    ).fetchall()
    fd, tmp_name = tempfile.mkstemp(
        dir=target.parent, prefix=target.name + ".", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(list(SENT_LOG_COLUMNS))
            for row in rows:
                writer.writerow(
                    ["" if row[column] is None else str(row[column])
                     for column in SENT_LOG_COLUMNS]
                )
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return target


def list_batches(conn: sqlite3.Connection) -> list[str]:
    """Return known batch names in first-use order."""
    rows = conn.execute(
        "SELECT DISTINCT name FROM batch ORDER BY rowid"
    ).fetchall()
    return [row[0] for row in rows]


def get_batch_qsos(conn: sqlite3.Connection, name: str) -> list[dict[str, Any]]:
    """Return the QSOs in ``name`` ordered by QSO id."""
    rows = conn.execute(
        "SELECT qso_id FROM batch WHERE name = ? ORDER BY qso_id", (name,)
    ).fetchall()
    qsos = []
    for row in rows:
        qso = get_qso(conn, row[0])
        if qso is not None:
            qsos.append(qso)
    return qsos


def _is_bureau(qso: dict[str, Any]) -> bool:
    return (qso.get("QSL_SENT_VIA") or "") == "B"


def add_qsos_to_batch(
    conn: sqlite3.Connection, name: str, qso_ids: Iterable[int]
) -> int:
    """Validate and add QSOs to ``name``. Returns the number added.

    Raises :class:`BatchError` when a QSO is missing, not queued
    (``QSL_SENT != 'Q'``), or would mix bureau and non-bureau cards.
    Re-adding a QSO that is already in the batch is a no-op.
    """
    ids = list(qso_ids)
    if not ids:
        return 0
    selected = []
    for qso_id in ids:
        qso = get_qso(conn, qso_id)
        if qso is None:
            raise BatchError(f"QSO id {qso_id} does not exist")
        selected.append(qso)
    not_queued = [qso["id"] for qso in selected
                  if (qso.get("QSL_SENT") or "") != "Q"]
    if not_queued:
        raise BatchError(
            f"QSOs not queued to send (QSL_SENT != 'Q'): {not_queued}"
        )
    existing = get_batch_qsos(conn, name)
    bureaus = {qso["id"] for qso in selected + existing if _is_bureau(qso)}
    directs = {qso["id"] for qso in selected + existing if not _is_bureau(qso)}
    if bureaus and directs:
        raise BatchError(
            f"Batch {name!r} would mix bureau and non-bureau cards"
        )
    present = {row[0] for row in
               conn.execute("SELECT qso_id FROM batch WHERE name = ?",
                            (name,)).fetchall()}
    added = 0
    for qso in selected:
        if qso["id"] in present:
            continue
        conn.execute(
            "INSERT INTO batch (name, qso_id) VALUES (?, ?)",
            (name, qso["id"]),
        )
        present.add(qso["id"])
        added += 1
    conn.commit()
    return added


def _blank(value: Any) -> str:
    return "" if value is None else str(value)


def _format_submode(value: Any) -> str:
    text = _blank(value)
    return f"-{text}" if text else ""


def _group_key(qso: dict[str, Any]) -> tuple[str, ...]:
    return tuple(_blank(qso.get(column)) for column in LABEL_GROUP_COLUMNS)


def chunk_labels(
    qsos: list[dict[str, Any]], qsos_per_label: int
) -> list[list[dict[str, Any]]]:
    """Group QSOs by label key, splitting groups into label-sized chunks."""
    if qsos_per_label < 1:
        raise ValueError("qsos_per_label must be at least 1")
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for qso in qsos:
        groups.setdefault(_group_key(qso), []).append(qso)
    labels = []
    for group in groups.values():
        labels.extend(
            group[index:index + qsos_per_label]
            for index in range(0, len(group), qsos_per_label)
        )
    return labels


def label_row(label: list[dict[str, Any]], qsos_per_label: int) -> list[str]:
    """Build one ``<base>_qsos.csv`` row for a label's QSOs."""
    first = label[0]
    row = [_blank(first.get(column)) for column in LABEL_GROUP_COLUMNS]
    for slot in range(qsos_per_label):
        if slot < len(label):
            qso = label[slot]
            row.extend(
                _format_submode(qso.get("SUBMODE"))
                if column == "SUBMODE"
                else _blank(qso.get(column))
                for column in QSO_SLOT_COLUMNS
            )
        else:
            row.extend([""] * len(QSO_SLOT_COLUMNS))
    received = any((qso.get("QSL_RCVD") or "") == "Y" for qso in label)
    row.append("TNX" if received else "PSE")
    return row


def qsos_header(qsos_per_label: int) -> list[str]:
    """Build the ``<base>_qsos.csv`` header for the label size."""
    header = list(LABEL_GROUP_COLUMNS)
    for slot in range(1, qsos_per_label + 1):
        header.extend(f"{column}{slot}" for column in QSO_SLOT_COLUMNS)
    header.append("PSETNX")
    return header


def export_batch(
    conn: sqlite3.Connection,
    name: str,
    base: str | Path,
    qsos_per_label: int = DEFAULT_QSOS_PER_LABEL,
    qrz_service: Any = None,
    mark_sent: bool = True,
    sent_log_path: str | Path | None = None,
) -> ExportResult:
    """Export ``name`` to ``<base>_qsos.csv`` (and addresses when direct).

    Bureau batches (every QSO sent via ``'B'``) produce no
    ``<base>_addresses.csv``. Direct batches need a QRZ service exposing
    ``address_for_qso(qso)``; one address row is written per label row.
    Exported QSOs are marked sent (``QSL_SENT = 'Y'``), and when
    ``sent_log_path`` is given the DB-wide sent-QSO log is rewritten
    there transactionally.
    """
    qsos = get_batch_qsos(conn, name)
    if not qsos:
        raise BatchError(f"Batch {name!r} is empty or does not exist")
    if any(qso.get("QSL_SENT") not in ("Q", "Y") for qso in qsos):
        raise BatchError("Batch members must be queued or already sent")
    if len({_is_bureau(qso) for qso in qsos}) != 1:
        raise BatchError("Batch mixes bureau and non-bureau cards")
    is_bureau = all(_is_bureau(qso) for qso in qsos)
    labels = chunk_labels(qsos, qsos_per_label)
    base_path = Path(base).expanduser()
    qsos_path = base_path.parent / f"{base_path.name}_qsos.csv"
    addresses_path = None
    outputs = [(qsos_path, qsos_header(qsos_per_label),
                [label_row(label, qsos_per_label) for label in labels])]
    if not is_bureau:
        if qrz_service is None:
            raise ValueError("A QRZ service is required to export a non-bureau batch")
        addresses_path = base_path.parent / f"{base_path.name}_addresses.csv"
        addresses = [qrz_service.address_for_qso(label[0]) for label in labels]
        outputs.append((addresses_path, list(ADDRESS_COLUMNS),
                        [[_blank(address.get(column)) for column in ADDRESS_COLUMNS]
                         for address in addresses]))

    staged = []
    originals = {}
    published = []
    try:
        for target, header, rows in outputs:
            target.parent.mkdir(parents=True, exist_ok=True)
            originals[target] = target.read_bytes() if target.exists() else None
            fd, temporary = tempfile.mkstemp(dir=target.parent,
                                            prefix=target.name + ".", suffix=".tmp")
            staged.append((target, temporary))
            with os.fdopen(fd, "w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(header)
                writer.writerows(rows)
        for target, temporary in staged:
            os.replace(temporary, target)
            published.append(target)
    except BaseException:
        for target in reversed(published):
            original = originals[target]
            if original is None:
                target.unlink()
            else:
                target.write_bytes(original)
        raise
    finally:
        for _, temporary in staged:
            Path(temporary).unlink(missing_ok=True)
    marked_sent = mark_batch_sent(conn, qsos) if mark_sent else 0
    if sent_log_path is not None:
        write_sent_log(conn, sent_log_path)
    logger.info(
        "exported batch %r: %d labels -> %s%s; marked %d QSOs sent",
        name,
        len(labels),
        qsos_path,
        f" and {addresses_path}" if addresses_path is not None else "",
        marked_sent,
    )
    return ExportResult(
        qsos_path=qsos_path,
        addresses_path=addresses_path,
        labels=len(labels),
        is_bureau=is_bureau,
        marked_sent=marked_sent,
    )

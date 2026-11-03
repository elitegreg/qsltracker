"""Batch management validation and CSV export engine for qsltracker.

Batches group QSOs for label printing. Adding QSOs to a batch validates:

- Every selected QSO has ``QSL_SENT == 'Q'`` (queued to send).
- A batch never mixes bureau (``QSL_SENT_VIA == 'B'``) and non-bureau
  cards, checking both the newly selected QSOs and the QSOs already in
  the target batch.

Export writes ``<base>_qsos.csv`` label records (grouped QSOs sharing one
address label, split into chunks of ``qsos_per_label``) and, for
non-bureau batches, a 1-to-1 ``<base>_addresses.csv`` with postal
addresses from the cached QRZ service.
"""

from __future__ import annotations

import csv
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from qsltracker.config import DEFAULT_QSOS_PER_LABEL
from qsltracker.db import get_qso

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
) -> ExportResult:
    """Export ``name`` to ``<base>_qsos.csv`` (and addresses when direct).

    Bureau batches (every QSO sent via ``'B'``) produce no
    ``<base>_addresses.csv``. Direct batches need a QRZ service exposing
    ``address_for_qso(qso)``; one address row is written per label row.
    """
    qsos = get_batch_qsos(conn, name)
    is_bureau = bool(qsos) and all(_is_bureau(qso) for qso in qsos)
    labels = chunk_labels(qsos, qsos_per_label)

    base_path = Path(base)
    qsos_path = base_path.parent / f"{base_path.name}_qsos.csv"
    qsos_path.parent.mkdir(parents=True, exist_ok=True)
    with qsos_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(qsos_header(qsos_per_label))
        for label in labels:
            writer.writerow(label_row(label, qsos_per_label))

    addresses_path = None
    if not is_bureau:
        if qrz_service is None:
            raise ValueError(
                "A QRZ service is required to export a non-bureau batch"
            )
        addresses_path = base_path.parent / f"{base_path.name}_addresses.csv"
        with addresses_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(list(ADDRESS_COLUMNS))
            for label in labels:
                address = qrz_service.address_for_qso(label[0])
                writer.writerow([_blank(address.get(column))
                                 for column in ADDRESS_COLUMNS])
    return ExportResult(
        qsos_path=qsos_path,
        addresses_path=addresses_path,
        labels=len(labels),
        is_bureau=is_bureau,
    )

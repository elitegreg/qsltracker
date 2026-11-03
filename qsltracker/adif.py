"""ADIF 3.1.8 parser and QSO import logic for qsltracker.

Parses ``<TAG:LENGTH[:TYPE]>VALUE`` fields (tag names matched
case-insensitively), skips length-delimited field contents and recognizes ``<EOH>`` and
``<EOR>`` only outside values, and keeps only the 20 tracked columns. Import applies the
field normalization rules before inserting into the database:

- Unset ``MY_GRIDSQUARE``/``MY_CQ_ZONE``/``MY_ITU_ZONE``/``MY_CNTY`` fall
  back to the configured defaults.
- ``MY_STATE`` is derived as ``MY_CNTY.split(',', 1)[0]`` when the county
  is present and no state is set.
- ``QSL_SENT == 'R'`` (Requested) becomes ``'Q'`` (Queued).
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from qsltracker.config import Config
from qsltracker.db import QSO_COLUMNS, duplicate_key, insert_qsos

MY_FALLBACK_FIELDS = ("MY_GRIDSQUARE", "MY_CQ_ZONE", "MY_ITU_ZONE", "MY_CNTY")


@dataclass
class ImportResult:
    """Outcome of an ADIF import: parsed, inserted, and duplicate keys."""

    parsed: int = 0
    inserted: int = 0
    duplicates: list[tuple[Any, ...]] = field(default_factory=list)


def parse_adif(text: str) -> list[dict[str, str]]:
    """Parse sequentially, skipping declared lengths even for untracked fields."""
    records = []
    record: dict[str, str] = {}
    position = 0
    token = re.compile(r"<EOH>|<EOR>|<([A-Za-z0-9_]+):(\d+)(?::[^<>]*)?>", re.I)
    while match := token.search(text, position):
        position = match.end()
        marker = match.group(0).upper()
        if marker == "<EOH>":
            records.clear()
            record = {}
        elif marker == "<EOR>":
            if record:
                records.append(record)
            record = {}
        else:
            end = position + int(match.group(2))
            if end > len(text):
                raise ValueError(f"Truncated ADIF field {match.group(1)}")
            tag = match.group(1).upper()
            if tag in QSO_COLUMNS:
                record[tag] = text[position:end]
            position = end
    if record:
        records.append(record)
    return records


def parse_adif_file(path: str | Path) -> list[dict[str, str]]:
    """Parse an ADIF file from disk."""
    return parse_adif(Path(path).expanduser().read_text(encoding="utf-8"))


def _is_set(value: Any) -> bool:
    return value is not None and str(value) != ""


def apply_import_rules(
    record: dict[str, Any], config: Config
) -> dict[str, Any]:
    """Apply field normalization and default rules to a parsed record."""
    normalized = dict(record)
    for column in MY_FALLBACK_FIELDS:
        if not _is_set(normalized.get(column)):
            fallback = getattr(config, column.lower())
            if _is_set(fallback):
                normalized[column] = str(fallback)
    county = normalized.get("MY_CNTY")
    if _is_set(county) and not _is_set(normalized.get("MY_STATE")):
        normalized["MY_STATE"] = str(county).split(",", 1)[0].strip()
    if normalized.get("QSL_SENT") == "R":
        normalized["QSL_SENT"] = "Q"
    return normalized


def import_records(
    conn: sqlite3.Connection,
    records: list[dict[str, Any]],
    config: Config,
) -> ImportResult:
    """Apply import rules to parsed records and insert them."""
    normalized = [apply_import_rules(record, config) for record in records]
    inserted, duplicates = insert_qsos(conn, normalized)
    return ImportResult(parsed=len(records), inserted=inserted,
                        duplicates=duplicates)


def import_adif_text(
    conn: sqlite3.Connection, text: str, config: Config
) -> ImportResult:
    """Parse ADIF text and import the records into the database."""
    return import_records(conn, parse_adif(text), config)


def import_adif_file(
    conn: sqlite3.Connection, path: str | Path, config: Config
) -> ImportResult:
    """Parse an ADIF file and import the records into the database."""
    return import_records(conn, parse_adif_file(path), config)


__all__ = [
    "ImportResult",
    "apply_import_rules",
    "duplicate_key",
    "import_adif_file",
    "import_adif_text",
    "import_records",
    "parse_adif",
    "parse_adif_file",
]

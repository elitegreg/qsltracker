"""QRZ integration and cached callsign lookup service for qsltracker.

Authenticates with ``qrzlib`` using the QRZ credentials from the
configuration. Callsign lookups are cached in memory with
``functools.lru_cache(maxsize=1024)`` so repeated references to the same
callsign (label grouping, address export) do not hit the network.

Update-from-QRZ rules applied per selected QSO:

- ``mqsl == 0`` sets ``QSL_SENT = 'I'``.
- ``qslmgr`` containing ``Buro``/``Bureau`` (case-insensitive) sets
  ``QSL_SENT_VIA = 'B'``.
- ``qslmgr`` that is a single callsign-like word sets ``QSL_VIA`` to it.
- ``qslmgr`` containing ``via <callsign>`` (case-insensitive) sets
  ``QSL_VIA`` to that callsign.
"""

from __future__ import annotations

import functools
import logging
import re
import sqlite3
from collections.abc import Iterable
from typing import Any
from xml.parsers.expat import ExpatError

from qrzlib import QRZ

logger = logging.getLogger("qsltracker.qrz")

from qsltracker.config import Config
from qsltracker.db import get_qso, update_qso_fields

_NotFound = QRZ.NotFound

ADDRESS_FIELDS = ("name_fmt", "addr1", "addr2", "state", "zip", "country")

CALLSIGN_RE = re.compile(r"[\w\d]+\d+\w+")
SINGLE_CALLSIGN_RE = re.compile(r"\s*([\w\d]+\d+\w+)\s*")
VIA_CALLSIGN_RE = re.compile(r"via\s+([\w\d]+\d+\w+)", re.IGNORECASE)
BUREAU_RE = re.compile(r"buro|bureau", re.IGNORECASE)


def _is_zero(value: Any) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    try:
        return int(value) == 0
    except (TypeError, ValueError):
        return False


def qsl_updates_from_record(record: Any) -> dict[str, str]:
    """Derive QSO field updates from a QRZ record (pure function)."""
    updates: dict[str, str] = {}
    if _is_zero(getattr(record, "mqsl", None)):
        updates["QSL_SENT"] = "I"
    mgr = getattr(record, "qslmgr", None) or ""
    mgr = str(mgr)
    if BUREAU_RE.search(mgr):
        updates["QSL_SENT_VIA"] = "B"
    via_match = VIA_CALLSIGN_RE.search(mgr)
    if via_match:
        updates["QSL_VIA"] = via_match.group(1).upper()
    else:
        single_match = SINGLE_CALLSIGN_RE.fullmatch(mgr)
        if single_match:
            updates["QSL_VIA"] = single_match.group(1).upper()
    if "QSL_VIA" in updates:
        logger.info(
            "qslmgr=%r -> QSL_VIA=%r bureau=%s",
            mgr,
            updates["QSL_VIA"],
            updates.get("QSL_SENT_VIA") == "B",
        )
    return updates


def address_from_record(record: Any) -> dict[str, str]:
    """Project a QRZ record onto the label address fields."""
    address = {}
    for column in ADDRESS_FIELDS:
        value = getattr(record, column, None)
        address[column] = "" if value is None else str(value)
    return address


class UncachedQRZ(QRZ):
    """qrzlib 2.1 network transport without its SQLite cache."""

    def __init__(self) -> None:
        # Do not call QRZ.__init__: it unconditionally opens a SQLite cache.
        self.key = None
        self.error = None
        self.count = None

    def __repr__(self) -> str:
        return f"<UncachedQRZ counter={self.count}>"

    def get_call(self, callsign: str) -> Any:
        try:
            return self._get_call(callsign)
        except KeyError as exc:
            raise QRZ.NotFound(str(exc)) from exc
        except ExpatError as exc:
            raise QRZ.XMLError(str(exc)) from exc


class QrzService:
    """Authenticated QRZ lookup service with an in-memory LRU cache."""

    def __init__(self, username: str, password: str) -> None:
        self._username = username
        self._password = password
        self._session: QRZ | None = None
        self._fetch = functools.lru_cache(maxsize=1024)(self._fetch_uncached)

    @classmethod
    def from_config(cls, config: Config) -> QrzService:
        """Build a service from the QRZ credentials in ``config``."""
        return cls(config.qrz_username, config.qrz_password)

    def _session_for_lookup(self) -> QRZ:
        if self._session is None:
            session = UncachedQRZ()
            session.authenticate(self._username, self._password)
            self._session = session
        return self._session

    def lookup(self, callsign: str) -> Any:
        """Fetch the QRZ record for ``callsign`` (cached, case-insensitive)."""
        callsign = (callsign or "").strip().upper()
        if not callsign:
            raise ValueError("callsign must not be blank")
        return self._fetch(callsign)

    def _fetch_uncached(self, callsign: str) -> Any:
        """Fetch a normalized callsign from QRZ (one cache entry per callsign)."""
        return self._session_for_lookup().get_call(callsign)

    def update_qsos_from_qrz(
        self, conn: sqlite3.Connection, qso_ids: Iterable[int]
    ) -> int:
        """Apply Update-from-QRZ rules to the given QSOs.

        QSOs whose callsign has no QRZ record (or no callsign at all)
        are skipped. Returns the number of rows updated.
        """
        updates: dict[int, dict[str, str]] = {}
        for qso_id in qso_ids:
            qso = get_qso(conn, qso_id)
            if qso is None:
                continue
            try:
                record = self.lookup(qso.get("CALL", ""))
            except (_NotFound, ValueError):
                continue
            fields = qsl_updates_from_record(record)
            if fields:
                updates[qso_id] = fields
        return update_qso_fields(conn, updates)

    def address_for_callsign(self, callsign: str) -> dict[str, str]:
        """Return the label address fields for ``callsign``."""
        return address_from_record(self.lookup(callsign))

    def address_for_qso(self, qso: dict[str, Any]) -> dict[str, str]:
        """Return the label address for a QSO: ``QSL_VIA`` if set, else ``CALL``."""
        key = (qso.get("QSL_VIA") or qso.get("CALL") or "").strip()
        if not key:
            return {column: "" for column in ADDRESS_FIELDS}
        return self.address_for_callsign(key)

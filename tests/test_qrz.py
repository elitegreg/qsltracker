"""Unit tests for the QRZ lookup service with mocked qrzlib."""

import logging
import sqlite3
from types import SimpleNamespace

import pytest
from qrzlib import QRZ

import qsltracker.qrz as qrz_module
from qsltracker.config import Config
from qsltracker.db import get_qso, init_schema, insert_qsos, search_qsos
from qsltracker.qrz import (
    ADDRESS_FIELDS,
    QrzService,
    address_from_record,
    qsl_updates_from_record,
)


def make_record(**overrides):
    fields = {
        "mqsl": 1,
        "qslmgr": "",
        "name_fmt": "John Doe",
        "addr1": "1 Main St",
        "addr2": "Apt 2",
        "state": "VA",
        "zip": "20101",
        "country": "USA",
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


class FakeQRZ:
    """Stand-in for qrzlib.QRZ recording authentication and lookups."""

    instances: list["FakeQRZ"] = []

    def __init__(self):
        self.authenticated_with = None
        self.lookups: list[str] = []
        self.records: dict[str, SimpleNamespace] = {}
        FakeQRZ.instances.append(self)

    def authenticate(self, username, password):
        self.authenticated_with = (username, password)

    def get_call(self, callsign):
        self.lookups.append(callsign)
        try:
            return self.records[callsign]
        except KeyError:
            raise QRZ.NotFound(callsign) from None


@pytest.fixture
def service(monkeypatch):
    FakeQRZ.instances.clear()
    monkeypatch.setattr(qrz_module, "QRZ", FakeQRZ)
    QrzService._fetch.cache_clear()
    svc = QrzService("testuser", "testpass")
    yield svc
    QrzService._fetch.cache_clear()


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    init_schema(connection)
    yield connection
    connection.close()


def test_lru_cache_uses_maxsize_1024():
    assert QrzService._fetch.cache_info().maxsize == 1024


def test_lookup_authenticates_uppercase_and_caches(service):
    record = make_record()
    session = service._session_for_lookup()
    assert isinstance(session, FakeQRZ)
    session.records["W1AW"] = record
    assert service.lookup("w1aw") is record
    assert service.lookup("W1AW") is record
    assert session.lookups == ["W1AW"]
    assert session.authenticated_with == ("testuser", "testpass")
    info = QrzService._fetch.cache_info()
    assert info.hits >= 1


def test_lookup_blank_callsign_raises(service):
    with pytest.raises(ValueError):
        service.lookup("  ")


def test_from_config_uses_qrz_credentials(monkeypatch):
    monkeypatch.setattr(qrz_module, "QRZ", FakeQRZ)
    QrzService._fetch.cache_clear()
    try:
        svc = QrzService.from_config(
            Config(qrz_username="alice", qrz_password="hunter2")
        )
        assert (svc._username, svc._password) == ("alice", "hunter2")
    finally:
        QrzService._fetch.cache_clear()


def test_mqsl_zero_sets_qsl_sent_ignore():
    assert qsl_updates_from_record(make_record(mqsl=0))["QSL_SENT"] == "I"
    assert qsl_updates_from_record(make_record(mqsl="0"))["QSL_SENT"] == "I"
    assert "QSL_SENT" not in qsl_updates_from_record(make_record(mqsl=1))
    assert "QSL_SENT" not in qsl_updates_from_record(make_record(mqsl=None))


def test_bureau_words_set_sent_via_bureau():
    for mgr in ("Bureau", "via bureau", "BURO OQRS", "QSL via Bureau W3JR"):
        updates = qsl_updates_from_record(make_record(mqsl=1, qslmgr=mgr))
        assert updates["QSL_SENT_VIA"] == "B", mgr
    updates = qsl_updates_from_record(make_record(mqsl=1, qslmgr="direct"))
    assert "QSL_SENT_VIA" not in updates


def test_single_word_callsign_sets_qsl_via():
    updates = qsl_updates_from_record(make_record(mqsl=1, qslmgr="W3JR"))
    assert updates["QSL_VIA"] == "W3JR"
    updates = qsl_updates_from_record(make_record(mqsl=1, qslmgr="direct"))
    assert "QSL_VIA" not in updates


def test_via_prefix_sets_qsl_via():
    for mgr, expected in (
        ("Please QSL via W3JR", "W3JR"),
        ("QSL VIA K9abc", "K9ABC"),
        ("via N0call direct only", "N0CALL"),
    ):
        updates = qsl_updates_from_record(make_record(mqsl=1, qslmgr=mgr))
        assert updates["QSL_VIA"] == expected, mgr


def test_qsl_via_parse_is_logged(caplog):
    with caplog.at_level(logging.INFO, logger="qsltracker"):
        updates = qsl_updates_from_record(
            make_record(mqsl=0, qslmgr="Bureau or via W3JR"))
    assert updates["QSL_VIA"] == "W3JR"
    assert "qslmgr='Bureau or via W3JR'" in caplog.text
    assert "QSL_VIA='W3JR'" in caplog.text
    assert "bureau=True" in caplog.text


def test_no_log_when_no_qsl_via_parsed(caplog):
    with caplog.at_level(logging.INFO, logger="qsltracker"):
        qsl_updates_from_record(make_record(mqsl=1, qslmgr="direct"))
    assert caplog.text == ""


def test_combined_mqsl_bureau_and_via_rules():
    updates = qsl_updates_from_record(
        make_record(mqsl=0, qslmgr="Bureau or via W3JR")
    )
    assert updates == {
        "QSL_SENT": "I",
        "QSL_SENT_VIA": "B",
        "QSL_VIA": "W3JR",
    }


def test_address_from_record_projects_six_fields():
    address = address_from_record(make_record(addr2=None))
    assert address == {
        "name_fmt": "John Doe",
        "addr1": "1 Main St",
        "addr2": "",
        "state": "VA",
        "zip": "20101",
        "country": "USA",
    }
    assert set(address) == set(ADDRESS_FIELDS)


def test_address_uses_qsl_via_over_call(service):
    session = service._session_for_lookup()
    assert isinstance(session, FakeQRZ)
    session.records["W3JR"] = make_record(name_fmt="Via Manager")
    session.records["W1AW"] = make_record(name_fmt="Station")
    qso = {"CALL": "W1AW", "QSL_VIA": "W3JR"}
    assert service.address_for_qso(qso)["name_fmt"] == "Via Manager"
    assert session.lookups == ["W3JR"]


def test_address_falls_back_to_call(service):
    session = service._session_for_lookup()
    session.records["K1ZZ"] = make_record(name_fmt="Station")
    qso = {"CALL": "K1ZZ", "QSL_VIA": ""}
    assert service.address_for_qso(qso)["name_fmt"] == "Station"
    assert session.lookups == ["K1ZZ"]


def test_update_qsos_from_qrz_end_to_end(service, conn):
    insert_qsos(
        conn,
        [
            {"CALL": "W1AW", "STATION_CALLSIGN": "N0CALL",
             "QSO_DATE": "20240101", "TIME_ON": "1200",
             "BAND": "20m", "MODE": "SSB"},
            {"CALL": "K1ZZ", "STATION_CALLSIGN": "N0CALL",
             "QSO_DATE": "20240202", "TIME_ON": "1300",
             "BAND": "40m", "MODE": "CW"},
            {"CALL": "N0PE", "STATION_CALLSIGN": "N0CALL",
             "QSO_DATE": "20240303", "TIME_ON": "1400",
             "BAND": "20m", "MODE": "SSB"},
        ],
    )
    session = service._session_for_lookup()
    session.records["W1AW"] = make_record(mqsl=0, qslmgr="QSL via W3JR")
    session.records["K1ZZ"] = make_record(mqsl=1, qslmgr="Bureau")
    # N0PE has no record: skipped without aborting the batch.
    ids = [row["id"] for row in (
        search_qsos(conn, "W1AW") + search_qsos(conn, "K1ZZ")
        + search_qsos(conn, "N0PE")
    )]
    assert service.update_qsos_from_qrz(conn, ids) == 2
    first = get_qso(conn, ids[0])
    assert (first["QSL_SENT"], first["QSL_VIA"]) == ("I", "W3JR")
    second = get_qso(conn, ids[1])
    assert second["QSL_SENT_VIA"] == "B"
    assert get_qso(conn, ids[2])["QSL_SENT"] == ""

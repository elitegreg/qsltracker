"""Unit tests for the SQLite schema and data access layer."""

import logging
import sqlite3

import pytest

from qsltracker import db


def make_qso(call="W1AW", station="N0CALL", date="20240101", time="1200",
             band="20m", mode="SSB", **extra):
    record = {
        "CALL": call,
        "STATION_CALLSIGN": station,
        "QSO_DATE": date,
        "TIME_ON": time,
        "BAND": band,
        "MODE": mode,
    }
    record.update(extra)
    return record


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    db.init_schema(connection)
    yield connection
    connection.close()


def test_schema_has_qsos_and_batch_tables(conn):
    tables = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    assert {"QSOs", "batch"} <= tables


def test_qsos_table_has_id_plus_20_tracked_columns(conn):
    columns = [
        row["name"] for row in conn.execute("PRAGMA table_info(QSOs)").fetchall()
    ]
    assert columns[0] == "id"
    assert columns[1:] == list(db.QSO_COLUMNS)
    assert len(db.QSO_COLUMNS) == 20


def test_batch_table_references_qsos(conn):
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'batch'"
    ).fetchone()["sql"]
    assert "qso_id INTEGER REFERENCES QSOs(id)" in sql


def test_uniqueness_enforced_on_six_key_columns(conn):
    db.insert_qsos(conn, [make_qso()])
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO QSOs (CALL, STATION_CALLSIGN, QSO_DATE, TIME_ON, BAND, MODE)"
            " VALUES ('W1AW', 'N0CALL', '20240101', '1200', '20m', 'SSB')"
        )


def test_insert_returns_count_and_duplicate_keys(conn):
    inserted, duplicates = db.insert_qsos(
        conn, [make_qso(), make_qso(band="40m"), make_qso(call="K1ZZ")]
    )
    assert inserted == 3
    assert duplicates == []

    inserted, duplicates = db.insert_qsos(
        conn,
        [
            make_qso(),
            make_qso(band="40m"),
            make_qso(call="K1ZZ"),
            make_qso(call="N0NEW"),
        ],
    )
    assert inserted == 1
    assert duplicates == [
        ("W1AW", "N0CALL", "20240101", "1200", "20m", "SSB"),
        ("W1AW", "N0CALL", "20240101", "1200", "40m", "SSB"),
        ("K1ZZ", "N0CALL", "20240101", "1200", "20m", "SSB"),
    ]


def test_insert_detects_within_batch_duplicates(conn):
    inserted, duplicates = db.insert_qsos(conn, [make_qso(), make_qso()])
    assert inserted == 1
    assert duplicates == [
        ("W1AW", "N0CALL", "20240101", "1200", "20m", "SSB")
    ]


def test_insert_blanks_missing_values(conn):
    inserted, _ = db.insert_qsos(conn, [make_qso(SUBMODE=None)])
    assert inserted == 1
    row = db.search_qsos(conn, "W1AW")[0]
    assert row["SUBMODE"] == ""
    assert row["MY_STATE"] == ""


def test_search_is_case_insensitive(conn):
    db.insert_qsos(conn, [make_qso(call="W1AW"), make_qso(call="K1ZZ")])
    assert [row["CALL"] for row in db.search_qsos(conn, "w1aw")] == ["W1AW"]
    assert [row["CALL"] for row in db.search_qsos(conn, "W1AW")] == ["W1AW"]
    assert db.search_qsos(conn, "N0PE") == []
    assert db.search_qsos(conn, "  ") == []


def test_get_qso_returns_row_or_none(conn):
    db.insert_qsos(conn, [make_qso()])
    row = db.search_qsos(conn, "W1AW")[0]
    fetched = db.get_qso(conn, row["id"])
    assert fetched is not None
    assert fetched["CALL"] == "W1AW"
    assert db.get_qso(conn, 9999) is None


def test_mark_received_sets_fields_with_utc_today(conn):
    db.insert_qsos(conn, [make_qso(), make_qso(call="K1ZZ")])
    ids = [row["id"] for row in db.search_qsos(conn, "W1AW")]
    ids += [row["id"] for row in db.search_qsos(conn, "K1ZZ")]
    assert db.mark_received(conn, ids, "B") == 2
    for qso_id in ids:
        row = db.get_qso(conn, qso_id)
        assert row["QSL_RCVD"] == "Y"
        assert row["QSLRDATE"] == db.utc_today_str()
        assert row["QSL_RCVD_VIA"] == "B"


def test_mark_received_rejects_bad_via_and_empty_ids(conn):
    db.insert_qsos(conn, [make_qso()])
    qso_id = db.search_qsos(conn, "W1AW")[0]["id"]
    with pytest.raises(ValueError):
        db.mark_received(conn, [qso_id], "X")
    assert db.mark_received(conn, [], "D") == 0
    assert db.get_qso(conn, qso_id)["QSL_RCVD"] == ""


def test_mark_queued_to_send(conn):
    db.insert_qsos(conn, [make_qso()])
    qso_id = db.search_qsos(conn, "W1AW")[0]["id"]
    assert db.mark_queued_to_send(conn, [qso_id]) == 1
    assert db.get_qso(conn, qso_id)["QSL_SENT"] == "Q"
    assert db.mark_queued_to_send(conn, []) == 0


def test_update_qsl_routing_fields(conn):
    db.insert_qsos(conn, [make_qso(), make_qso(call="K1ZZ")])
    first = db.search_qsos(conn, "W1AW")[0]["id"]
    second = db.search_qsos(conn, "K1ZZ")[0]["id"]
    updated = db.update_qso_fields(
        conn,
        {
            first: {"QSL_SENT": "I"},
            second: {"QSL_SENT_VIA": "B", "QSL_VIA": "W3JR"},
        },
    )
    assert updated == 2
    assert db.get_qso(conn, first)["QSL_SENT"] == "I"
    routed = db.get_qso(conn, second)
    assert routed["QSL_SENT_VIA"] == "B"
    assert routed["QSL_VIA"] == "W3JR"


def test_update_qso_fields_rejects_non_qsl_columns(conn):
    db.insert_qsos(conn, [make_qso()])
    qso_id = db.search_qsos(conn, "W1AW")[0]["id"]
    with pytest.raises(ValueError):
        db.update_qso_fields(conn, {qso_id: {"CALL": "K1ZZ"}})
    assert db.get_qso(conn, qso_id)["CALL"] == "W1AW"


def test_mark_received_logs_key_columns_and_changes(conn, caplog):
    db.insert_qsos(conn, [make_qso()])
    qso_id = db.search_qsos(conn, "W1AW")[0]["id"]
    with caplog.at_level(logging.INFO, logger="qsltracker"):
        assert db.mark_received(conn, [qso_id], "D") == 1
    assert "W1AW N0CALL 20240101 1200 20m SSB" in caplog.text
    assert "QSL_RCVD ''->'Y'" in caplog.text
    assert "QSL_RCVD_VIA ''->'D'" in caplog.text


def test_mark_queued_logs_change(conn, caplog):
    db.insert_qsos(conn, [make_qso()])
    qso_id = db.search_qsos(conn, "W1AW")[0]["id"]
    with caplog.at_level(logging.INFO, logger="qsltracker"):
        db.mark_queued_to_send(conn, [qso_id])
    assert "QSL_SENT ''->'Q'" in caplog.text


def test_no_log_when_update_changes_nothing(conn, caplog):
    db.insert_qsos(conn, [make_qso(QSL_SENT="Q")])
    qso_id = db.search_qsos(conn, "W1AW")[0]["id"]
    with caplog.at_level(logging.INFO, logger="qsltracker"):
        db.mark_queued_to_send(conn, [qso_id])
    assert caplog.text == ""


def test_update_qso_fields_logs_routing_changes(conn, caplog):
    db.insert_qsos(conn, [make_qso()])
    qso_id = db.search_qsos(conn, "W1AW")[0]["id"]
    with caplog.at_level(logging.INFO, logger="qsltracker"):
        db.update_qso_fields(conn, {qso_id: {"QSL_SENT": "I"}})
    assert "QSL_SENT ''->'I'" in caplog.text


def test_inserts_are_not_logged(conn, caplog):
    with caplog.at_level(logging.INFO, logger="qsltracker"):
        db.insert_qsos(conn, [make_qso()])
    assert caplog.text == ""


def test_connect_creates_schema_and_default_db_path(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    path = db.get_default_db_path()
    assert path == tmp_path / ".config" / "qsltracker" / "qsos.db"
    connection = db.connect(path)
    try:
        inserted, _ = db.insert_qsos(connection, [make_qso()])
        assert inserted == 1
    finally:
        connection.close()
    assert path.is_file()

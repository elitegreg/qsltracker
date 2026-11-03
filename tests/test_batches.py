"""Unit tests for batch validation and CSV export."""

import csv
import sqlite3

import pytest

from qsltracker.batches import (
    SENT_LOG_COLUMNS,
    BatchError,
    add_qsos_to_batch,
    chunk_labels,
    export_batch,
    get_batch_qsos,
    label_row,
    list_batches,
    mark_batch_sent,
    qsos_header,
    write_sent_log,
)
from qsltracker.db import (
    get_qso,
    init_schema,
    insert_qsos,
    search_qsos,
    utc_today_str,
)


class StubQrzService:
    """Stand-in for QrzService recording address lookups."""

    def __init__(self):
        self.looked_up: list[dict] = []

    def address_for_qso(self, qso):
        self.looked_up.append(dict(qso))
        via = qso.get("QSL_VIA") or qso.get("CALL")
        return {
            "name_fmt": f"Name {via}",
            "addr1": "1 Main St",
            "addr2": "",
            "state": "VA",
            "zip": "20101",
            "country": "USA",
        }


def make_qso(call="W1AW", date="20240101", time="1200", band="20m",
             mode="SSB", via="", **extra):
    record = {
        "CALL": call,
        "STATION_CALLSIGN": "N0CALL",
        "QSO_DATE": date,
        "TIME_ON": time,
        "BAND": band,
        "MODE": mode,
        "QSL_SENT": "Q",
        "QSL_SENT_VIA": via,
    }
    record.update(extra)
    return record


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    init_schema(connection)
    yield connection
    connection.close()


@pytest.fixture
def queued_ids(conn):
    records = [
        make_qso(date="20240101", time="1200"),
        make_qso(date="20240102", time="1200"),
        make_qso(call="K1ZZ", date="20240103", time="1300"),
    ]
    insert_qsos(conn, records)
    ids = [row["id"] for row in search_qsos(conn, "W1AW")]
    ids += [row["id"] for row in search_qsos(conn, "K1ZZ")]
    return ids


def read_csv(path):
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.reader(handle))


def test_add_and_list_batches(conn, queued_ids):
    assert add_qsos_to_batch(conn, "spring", queued_ids[:2]) == 2
    assert list_batches(conn) == ["spring"]
    assert [q["CALL"] for q in get_batch_qsos(conn, "spring")] == ["W1AW", "W1AW"]
    # Re-adding is idempotent.
    assert add_qsos_to_batch(conn, "spring", queued_ids[:2]) == 0
    assert len(get_batch_qsos(conn, "spring")) == 2


def test_add_rejects_unqueued_qsos(conn, queued_ids):
    insert_qsos(conn, [make_qso(call="N0PE", date="20240404",
                                time="1400", QSL_SENT="Y")])
    from qsltracker.db import search_qsos
    pending = search_qsos(conn, "N0PE")[0]["id"]
    with pytest.raises(BatchError):
        add_qsos_to_batch(conn, "spring", [queued_ids[0], pending])
    assert get_batch_qsos(conn, "spring") == []


def test_add_rejects_missing_qsos(conn, queued_ids):
    with pytest.raises(BatchError):
        add_qsos_to_batch(conn, "spring", [queued_ids[0], 9999])
    assert get_batch_qsos(conn, "spring") == []


def test_add_rejects_mixed_selection(conn, queued_ids):
    insert_qsos(conn, [make_qso(call="W2BU", date="20240505",
                                time="1500", via="B")])
    from qsltracker.db import search_qsos
    bureau = search_qsos(conn, "W2BU")[0]["id"]
    with pytest.raises(BatchError):
        add_qsos_to_batch(conn, "mixed", [queued_ids[0], bureau])
    assert get_batch_qsos(conn, "mixed") == []


def test_add_rejects_mixing_with_existing_batch(conn, queued_ids):
    assert add_qsos_to_batch(conn, "bureau", []) == 0
    insert_qsos(conn, [make_qso(call="W2BU", date="20240505",
                                time="1500", via="B")])
    from qsltracker.db import search_qsos
    bureau = search_qsos(conn, "W2BU")[0]["id"]
    assert add_qsos_to_batch(conn, "bureau", [bureau]) == 1
    with pytest.raises(BatchError):
        add_qsos_to_batch(conn, "bureau", [queued_ids[0]])


def test_same_via_batches_succeed(conn, queued_ids):
    insert_qsos(conn, [make_qso(call="W2BU", date="20240505",
                                time="1500", via="B"),
                       make_qso(call="W2BX", date="20240606",
                                time="1600", via="B")])
    from qsltracker.db import search_qsos
    ids = [search_qsos(conn, call)[0]["id"] for call in ("W2BU", "W2BX")]
    assert add_qsos_to_batch(conn, "bureau", ids) == 2
    assert add_qsos_to_batch(conn, "direct", queued_ids) == 3


def test_grouping_and_splitting(conn):
    records = [make_qso(date=f"202401{d:02d}", time="1200")
               for d in range(1, 8)]
    records.append(make_qso(call="K1ZZ", date="20240101", time="1200"))
    insert_qsos(conn, records)
    from qsltracker.db import search_qsos
    qsos = search_qsos(conn, "W1AW") + search_qsos(conn, "K1ZZ")
    labels = chunk_labels(qsos, 5)
    assert [len(label) for label in labels] == [5, 2, 1]


def test_qsos_header_shape():
    header = qsos_header(2)
    assert header[:8] == ["CALL", "STATION_CALLSIGN", "QSL_VIA",
                          "MY_GRIDSQUARE", "MY_CQ_ZONE", "MY_ITU_ZONE",
                          "MY_CNTY", "MY_STATE"]
    assert header[8:20] == ["QSO_DATE1", "TIME_ON1", "BAND1", "MODE1",
                            "SUBMODE1", "RST_SENT1", "QSO_DATE2",
                            "TIME_ON2", "BAND2", "MODE2", "SUBMODE2",
                            "RST_SENT2"]
    assert header[-1] == "PSETNX"
    assert [name for name in header if name.startswith("QSL")] == ["QSL_VIA"]
    assert len(header) == 8 + 6 * 2 + 1


def test_submode_and_psetnx_formatting():
    qso = make_qso(SUBMODE="FT4", QSL_RCVD="Y")
    row = label_row([qso], 1)
    assert row[8:14] == ["20240101", "1200", "20m", "SSB", "-FT4", ""]
    assert row[-1] == "TNX"
    row = label_row([make_qso(SUBMODE="", QSL_RCVD="")], 2)
    assert row[12] == ""
    assert row[-1] == "PSE"
    assert len(row) == 8 + 12 + 1


def test_direct_export_writes_both_csv_files(conn, queued_ids, tmp_path):
    add_qsos_to_batch(conn, "direct", queued_ids)
    service = StubQrzService()
    result = export_batch(conn, "direct", tmp_path / "spring",
                          qsos_per_label=5, qrz_service=service)
    assert result.is_bureau is False
    assert result.labels == 2
    assert result.addresses_path is not None
    qsos_rows = read_csv(result.qsos_path)
    assert qsos_rows[0] == qsos_header(5)
    assert len(qsos_rows) == 3
    addr_rows = read_csv(result.addresses_path)
    assert addr_rows[0] == ["name_fmt", "addr1", "addr2", "state",
                            "zip", "country"]
    assert len(addr_rows) == 3
    assert [row[0] for row in addr_rows[1:]] == ["Name W1AW", "Name K1ZZ"]


def test_bureau_export_skips_addresses(conn, tmp_path):
    insert_qsos(conn, [make_qso(call="W2BU", date="20240505",
                                time="1500", via="B")])
    from qsltracker.db import search_qsos
    bureau = search_qsos(conn, "W2BU")[0]["id"]
    add_qsos_to_batch(conn, "bureau", [bureau])
    result = export_batch(conn, "bureau", tmp_path / "fall",
                          qrz_service=StubQrzService())
    assert result.is_bureau is True
    assert result.addresses_path is None
    assert result.qsos_path.is_file()
    assert not (tmp_path / "fall_addresses.csv").exists()


def test_direct_export_requires_qrz_service(conn, queued_ids, tmp_path):
    add_qsos_to_batch(conn, "direct", queued_ids)
    with pytest.raises(ValueError):
        export_batch(conn, "direct", tmp_path / "spring")


def test_export_uses_qsl_via_for_addresses(conn, tmp_path):
    insert_qsos(conn, [make_qso(QSL_VIA="W3JR")])
    from qsltracker.db import search_qsos
    qso_id = search_qsos(conn, "W1AW")[0]["id"]
    add_qsos_to_batch(conn, "direct", [qso_id])
    service = StubQrzService()
    export_batch(conn, "direct", tmp_path / "via", qrz_service=service)
    assert service.looked_up[0]["QSL_VIA"] == "W3JR"
    addr_rows = read_csv(tmp_path / "via_addresses.csv")
    assert addr_rows[1][0] == "Name W3JR"
    qsos_rows = read_csv(tmp_path / "via_qsos.csv")
    assert qsos_rows[1][2] == "W3JR"


def test_unset_values_export_as_blank(conn, tmp_path):
    insert_qsos(conn, [{"CALL": "W1AW", "STATION_CALLSIGN": "N0CALL",
                        "QSO_DATE": "20240101", "TIME_ON": "1200",
                        "BAND": "20m", "MODE": "SSB",
                        "QSL_SENT": "Q"}])
    from qsltracker.db import search_qsos
    qso_id = search_qsos(conn, "W1AW")[0]["id"]
    add_qsos_to_batch(conn, "direct", [qso_id])
    export_batch(conn, "direct", tmp_path / "blank",
                 qrz_service=StubQrzService())
    row = read_csv(tmp_path / "blank_qsos.csv")[1]
    assert row[2:8] == ["", "", "", "", "", ""]


def test_export_marks_qsos_sent_with_direct_default(conn, tmp_path):
    insert_qsos(conn, [make_qso(date="20240101", time="1200")])
    qso_id = search_qsos(conn, "W1AW")[0]["id"]
    add_qsos_to_batch(conn, "direct", [qso_id])
    result = export_batch(conn, "direct", tmp_path / "run",
                          qrz_service=StubQrzService())
    assert result.marked_sent == 1
    row = get_qso(conn, qso_id)
    assert row["QSL_SENT"] == "Y"
    assert row["QSLSDATE"] == utc_today_str()
    assert row["QSL_SENT_VIA"] == "D"


def test_mark_sent_keeps_bureau_via(conn):
    insert_qsos(conn, [make_qso(via="B")])
    qso = search_qsos(conn, "W1AW")[0]
    assert mark_batch_sent(conn, [qso]) == 1
    row = get_qso(conn, qso["id"])
    assert (row["QSL_SENT"], row["QSLSDATE"], row["QSL_SENT_VIA"]) == (
        "Y", utc_today_str(), "B")


def test_export_without_mark_sent_leaves_db_untouched(conn, tmp_path):
    insert_qsos(conn, [make_qso()])
    qso_id = search_qsos(conn, "W1AW")[0]["id"]
    add_qsos_to_batch(conn, "direct", [qso_id])
    result = export_batch(conn, "direct", tmp_path / "run",
                          qrz_service=StubQrzService(), mark_sent=False)
    assert result.marked_sent == 0
    assert get_qso(conn, qso_id)["QSL_SENT"] == "Q"


def test_write_sent_log_lists_sent_qsos_only(conn, tmp_path):
    insert_qsos(conn, [make_qso(date="20240101", time="1200"),
                       make_qso(date="20240102", time="1200"),
                       make_qso(call="K1ZZ", date="20240103", time="1300")])
    ids = ([r["id"] for r in search_qsos(conn, "W1AW")]
           + [r["id"] for r in search_qsos(conn, "K1ZZ")])
    from qsltracker.db import mark_queued_to_send
    mark_queued_to_send(conn, ids[:2])
    mark_batch_sent(conn, [search_qsos(conn, "W1AW")[0]])
    path = write_sent_log(conn, tmp_path / "all-sent.csv")
    rows = read_csv(path)
    assert rows[0] == list(SENT_LOG_COLUMNS)
    assert [row[0] for row in rows[1:]] == ["W1AW"]
    assert rows[1][:8] == ["W1AW", "N0CALL", "20240101", "1200",
                           "20m", "SSB", "", "D"]


def test_export_rewrites_sent_log_without_leftovers(conn, tmp_path):
    insert_qsos(conn, [make_qso()])
    qso_id = search_qsos(conn, "W1AW")[0]["id"]
    add_qsos_to_batch(conn, "direct", [qso_id])
    log = tmp_path / "all-sent.csv"
    export_batch(conn, "direct", tmp_path / "run",
                 qrz_service=StubQrzService(), sent_log_path=log)
    export_batch(conn, "direct", tmp_path / "run",
                 qrz_service=StubQrzService(), sent_log_path=log)
    rows = read_csv(log)
    assert len(rows) == 2
    leftovers = [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]
    assert leftovers == []

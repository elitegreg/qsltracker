"""Unit tests for ADIF parsing, import rules, and duplicate handling."""

import sqlite3

import pytest

from qsltracker import adif
from qsltracker.adif import (
    ImportResult,
    apply_import_rules,
    import_adif_file,
    import_adif_text,
    parse_adif,
)
from qsltracker.config import Config
from qsltracker.db import search_qsos

SAMPLE = """ADIF 3.1.8 test export
<ADIF_VER:5>3.1.8
<PROGRAMID:10>QSLSAMPLE
<EOH>
<CALL:4>W1AW <STATION_CALLSIGN:6>N0CALL <QSO_DATE:8>20240101 <TIME_ON:4>1200
<BAND:3>20m <MODE:3>SSB <SUBMODE:2>FT <RST_SENT:2>59 <QSL_SENT:1>R
<COMMENT:11>hello world <EOR>
<call:4>k1zz<qso_date:8:d>20240202<time_on:4>1300<band:3>40m<mode:2>CW<EOR>
"""


@pytest.fixture
def conn():
    from qsltracker.db import init_schema

    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    init_schema(connection)
    yield connection
    connection.close()


@pytest.fixture
def config():
    return Config(
        my_gridsquare="FN20hf",
        my_cq_zone=5,
        my_itu_zone=8,
        my_cnty="VA,Fairfax",
    )


def test_parse_skips_header_and_extracts_records():
    records = parse_adif(SAMPLE)
    assert len(records) == 2
    first, second = records
    assert first["CALL"] == "W1AW"
    assert first["STATION_CALLSIGN"] == "N0CALL"
    assert first["QSO_DATE"] == "20240101"
    assert first["SUBMODE"] == "FT"
    assert second["CALL"] == "k1zz"
    assert second["BAND"] == "40m"


def test_parse_ignores_untracked_fields_and_type_indicators():
    records = parse_adif(SAMPLE)
    assert "COMMENT" not in records[0]
    assert "ADIF_VER" not in records[0]
    assert records[1]["QSO_DATE"] == "20240202"


def test_parse_without_header_and_without_trailing_eor():
    records = parse_adif("<CALL:4>W1AW<QSO_DATE:8>20240101")
    assert len(records) == 1
    assert records[0]["CALL"] == "W1AW"


def test_parse_respects_length_not_delimiters():
    records = parse_adif("<CALL:12>W1<W>AW Test<QSO_DATE:8>20240101<EOR>")
    assert records[0]["CALL"] == "W1<W>AW Test"


def test_parse_skips_segments_without_tracked_fields():
    assert parse_adif("<EOH><COMMENT:3>hi<EOR>") == []


def test_my_config_fallbacks_fill_unset_fields(config):
    out = apply_import_rules({"CALL": "W1AW"}, config)
    assert out["MY_GRIDSQUARE"] == "FN20hf"
    assert out["MY_CQ_ZONE"] == "5"
    assert out["MY_ITU_ZONE"] == "8"
    assert out["MY_CNTY"] == "VA,Fairfax"


def test_my_config_fallbacks_do_not_overwrite_set_values(config):
    out = apply_import_rules(
        {"MY_GRIDSQUARE": "EM10", "MY_CQ_ZONE": "3"}, config
    )
    assert out["MY_GRIDSQUARE"] == "EM10"
    assert out["MY_CQ_ZONE"] == "3"
    assert out["MY_ITU_ZONE"] == "8"


def test_my_state_derived_from_county(config):
    out = apply_import_rules({"MY_CNTY": "VA,Fairfax"}, config)
    assert out["MY_STATE"] == "VA"


def test_my_state_derived_from_config_county(config):
    out = apply_import_rules({"CALL": "W1AW"}, config)
    assert out["MY_STATE"] == "VA"


def test_my_state_preserved_when_set(config):
    out = apply_import_rules(
        {"MY_CNTY": "VA,Fairfax", "MY_STATE": "MD"}, config
    )
    assert out["MY_STATE"] == "MD"


def test_no_my_state_without_county():
    out = apply_import_rules({"CALL": "W1AW"}, Config())
    assert out.get("MY_STATE", "") == ""


def test_qsl_sent_requested_becomes_queued(config):
    out = apply_import_rules({"QSL_SENT": "R"}, config)
    assert out["QSL_SENT"] == "Q"
    out = apply_import_rules({"QSL_SENT": "r"}, config)
    assert out["QSL_SENT"] == "Q"
    out = apply_import_rules({"QSL_SENT": "Y"}, config)
    assert out["QSL_SENT"] == "Y"


def test_import_counts_inserts_and_duplicates(conn, config):
    first = import_adif_text(conn, SAMPLE, config)
    assert isinstance(first, ImportResult)
    assert first.parsed == 2
    assert first.inserted == 2
    assert first.duplicates == []

    second = import_adif_text(conn, SAMPLE, config)
    assert second.parsed == 2
    assert second.inserted == 0
    assert second.duplicates == [
        ("W1AW", "N0CALL", "20240101", "1200", "20M", "SSB"),
        ("K1ZZ", "", "20240202", "1300", "40M", "CW",),
    ]


def test_import_applies_rules_before_insert(conn, config):
    result = import_adif_text(conn, SAMPLE, config)
    assert result.inserted == 2
    row = search_qsos(conn, "W1AW")[0]
    assert row["MY_GRIDSQUARE"] == "FN20hf"
    assert row["MY_CQ_ZONE"] == "5"
    assert row["MY_STATE"] == "VA"
    assert row["QSL_SENT"] == "Q"


def test_import_adif_file(tmp_path, conn, config):
    path = tmp_path / "log.adi"
    path.write_text(SAMPLE, encoding="utf-8")
    result = import_adif_file(conn, path, config)
    assert result.parsed == 2
    assert result.inserted == 2

@pytest.mark.parametrize('value', ['<CALL:4>K1ZZ', '<EOR>', '<EOH>'])
def test_delimiters_in_values_are_literal(value):
    text = f'<CALL:4>W1AW<COMMENT:{len(value)}>{value}<MODE:2>CW<EOR>'
    assert parse_adif(text) == [{'CALL': 'W1AW', 'MODE': 'CW'}]


def test_truncated_import_is_not_partially_inserted(conn, config):
    with pytest.raises(ValueError, match='Truncated'):
        import_adif_text(conn, '<CALL:4>W1AW<EOR><CALL:99>K1ZZ', config)
    assert conn.execute('SELECT COUNT(*) FROM QSOs').fetchone()[0] == 0


def test_import_adif_file_falls_back_to_latin1(tmp_path, conn, config):
    path = tmp_path / "latin1.adi"
    raw = (
        b"<EOH><CALL:4>W1AW<QSO_DATE:8>20240101<TIME_ON:4>1200"
        b"<BAND:3>20m<MODE:3>SSB<NAME:4>Jos\xe9<EOR>"
    )
    path.write_bytes(raw)
    result = import_adif_file(conn, path, config)
    assert result.parsed == 1
    assert result.inserted == 1

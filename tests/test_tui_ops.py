"""Pilot tests for search, selection filters, QSL ops, and batch UI."""

import pytest
from qrzlib import QRZ
from textual.widgets import Button, Input, Select, Static, DataTable
from types import SimpleNamespace

import qsltracker.qrz as qrz_module
from qsltracker.batches import get_batch_qsos, list_batches
from qsltracker.config import Config
from qsltracker.db import connect, get_qso, insert_qsos, utc_today_str
from qsltracker.qrz import QrzService
from qsltracker.tui import (
    BatchModal,
    ExportModal,
    MainScreen,
    QsltrackerApp,
    ViaModal,
    is_unreceived,
    is_unsent,
)

SIZE = (100, 60)


class FakeQRZ:
    records: dict[str, SimpleNamespace] = {}

    def __init__(self):
        self.authenticated_with = None
        self.lookups: list[str] = []

    def authenticate(self, username, password):
        self.authenticated_with = (username, password)

    def get_call(self, callsign):
        self.lookups.append(callsign)
        try:
            return FakeQRZ.records[callsign]
        except KeyError:
            raise QRZ.NotFound(callsign) from None


def seed_db(db_path, records):
    conn = connect(db_path)
    try:
        return insert_qsos(conn, records)
    finally:
        conn.close()


def base_records():
    return [
        {"CALL": "W1AW", "STATION_CALLSIGN": "N0CALL", "QSO_DATE": "20240101",
         "TIME_ON": "1200", "BAND": "20m", "MODE": "SSB", "QSL_SENT": "Q"},
        {"CALL": "W1AW", "STATION_CALLSIGN": "N0CALL", "QSO_DATE": "20240102",
         "TIME_ON": "1200", "BAND": "20m", "MODE": "SSB",
         "QSL_SENT": "Q", "QSL_RCVD": "Y", "QSLRDATE": "20240103",
         "QSL_RCVD_VIA": "D"},
        {"CALL": "K1ZZ", "STATION_CALLSIGN": "N0CALL", "QSO_DATE": "20240202",
         "TIME_ON": "1300", "BAND": "40m", "MODE": "CW", "QSL_SENT": "Y"},
    ]


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    seed_db(path, base_records())
    return path


def make_app(tmp_path, db_path, **kwargs):
    return QsltrackerApp(
        config=Config(qrz_username="u", qrz_password="p"),
        config_path=tmp_path / "config.yaml",
        db_path=db_path,
        **kwargs,
    )


@pytest.fixture
def clean_qrz_cache(monkeypatch):
    monkeypatch.setattr(qrz_module, "UncachedQRZ", FakeQRZ)
    FakeQRZ.records = {}
    yield
    FakeQRZ.records = {}


def selection_text(app):
    return app.screen.query_one("#selection-line", Static).content


def op_result(app):
    return app.screen.query_one("#op-result", Static).content


def test_selection_predicates():
    assert is_unreceived({"QSL_RCVD": ""})
    assert is_unreceived({"QSL_RCVD": "Q"})
    assert not is_unreceived({"QSL_RCVD": "Y"})
    assert not is_unreceived({"QSL_RCVD": "I"})
    assert not is_unreceived({"QSL_RCVD": "V"})
    assert is_unsent({"QSL_SENT": "Q"})
    assert is_unsent({"QSL_SENT": ""})
    assert not is_unsent({"QSL_SENT": "Y"})


async def test_search_filters_by_callsign(tmp_path, db_path):
    app = make_app(tmp_path, db_path)
    async with app.run_test(size=SIZE) as pilot:
        assert isinstance(app.screen, MainScreen)
        app.screen.query_one("#search-input", Input).value = "w1aw"
        await pilot.click("#search-btn")
        await pilot.pause()
        table = app.screen.query_one("#qso-table", DataTable)
        assert table.row_count == 2
        assert selection_text(app) == "2 QSOs shown, 0 selected."
        app.screen.query_one("#search-input", Input).value = ""
        await pilot.click("#search-input")
        await pilot.pause()
        await pilot.click("#search-btn")
        await pilot.pause()
        assert table.row_count == 3


async def test_enter_toggles_row_and_filters(tmp_path, db_path):
    app = make_app(tmp_path, db_path)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.click("#search-btn")  # blank search shows all
        await pilot.pause()
        table = app.screen.query_one("#qso-table", DataTable)
        assert table.row_count == 3
        table.focus()
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert selection_text(app) == "3 QSOs shown, 1 selected."
        await pilot.click("#select-all")
        await pilot.pause()
        assert selection_text(app) == "3 QSOs shown, 3 selected."
        await pilot.click("#select-unreceived")
        await pilot.pause()
        assert selection_text(app) == "3 QSOs shown, 2 selected."
        await pilot.click("#select-unsent")
        await pilot.pause()
        assert selection_text(app) == "3 QSOs shown, 2 selected."
        await pilot.click("#select-clear")
        await pilot.pause()
        assert selection_text(app) == "3 QSOs shown, 0 selected."


async def test_mark_received_flow(tmp_path, db_path):
    app = make_app(tmp_path, db_path)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.click("#search-btn")
        await pilot.pause()
        await pilot.click("#select-all")
        await pilot.pause()
        await pilot.click("#op-received")
        await pilot.pause()
        assert isinstance(app.screen, ViaModal)
        await pilot.click("#via-bureau")
        await pilot.pause()
        assert isinstance(app.screen, MainScreen)
        assert op_result(app) == "Marked 3 QSOs received via B."
        conn = connect(db_path)
        try:
            for row in conn.execute("SELECT * FROM QSOs").fetchall():
                assert row["QSL_RCVD"] == "Y"
                assert row["QSLRDATE"] == utc_today_str()
                assert row["QSL_RCVD_VIA"] == "B"
        finally:
            conn.close()


async def test_mark_queued_flow(tmp_path, db_path):
    app = make_app(tmp_path, db_path)
    async with app.run_test(size=SIZE) as pilot:
        app.screen.query_one("#search-input", Input).value = "k1zz"
        await pilot.click("#search-btn")
        await pilot.pause()
        await pilot.click("#select-all")
        await pilot.pause()
        # K1ZZ has QSL_SENT=Y; queue it instead.
        await pilot.click("#op-queued")
        await pilot.pause()
        assert op_result(app) == "Marked 1 QSOs queued to send."
        conn = connect(db_path)
        try:
            assert conn.execute(
                "SELECT QSL_SENT FROM QSOs WHERE CALL='K1ZZ'").fetchone()[0] == "Q"
        finally:
            conn.close()


async def test_ops_require_selection(tmp_path, db_path):
    app = make_app(tmp_path, db_path)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.click("#search-btn")
        await pilot.pause()
        for button in ("#op-received", "#op-queued", "#op-batch",
                       "#op-last-batch", "#op-qrz"):
            await pilot.click(button)
            await pilot.pause()
            assert op_result(app) == "Select at least one QSO first.", button


async def test_add_to_batch_flow_and_last_batch(tmp_path, db_path):
    app = make_app(tmp_path, db_path)
    async with app.run_test(size=SIZE) as pilot:
        app.screen.query_one("#search-input", Input).value = "w1aw"
        await pilot.click("#search-btn")
        await pilot.pause()
        await pilot.click("#select-all")
        await pilot.pause()
        await pilot.click("#op-batch")
        await pilot.pause()
        assert isinstance(app.screen, BatchModal)
        app.screen.query_one("#batch-name", Input).value = "spring"
        await pilot.click("#batch-add")
        await pilot.pause()
        assert isinstance(app.screen, MainScreen)
        assert op_result(app) == "Added 2 QSOs to batch 'spring'."
        assert app.last_batch_name == "spring"
        assert list_batches(connect(db_path)) == ["spring"]
        # Queue K1ZZ, then add it via the last-batch shortcut.
        app.screen.query_one("#search-input", Input).value = "k1zz"
        await pilot.click("#search-btn")
        await pilot.pause()
        await pilot.click("#select-all")
        await pilot.pause()
        await pilot.click("#op-queued")
        await pilot.pause()
        await pilot.click("#op-last-batch")
        await pilot.pause()
        assert op_result(app) == "Added 1 QSOs to batch 'spring'."
        assert len(get_batch_qsos(connect(db_path), "spring")) == 3


async def test_last_batch_prompts_when_unknown(tmp_path, db_path):
    app = make_app(tmp_path, db_path)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.click("#search-btn")
        await pilot.pause()
        await pilot.click("#select-all")
        await pilot.pause()
        await pilot.click("#op-last-batch")
        await pilot.pause()
        assert isinstance(app.screen, BatchModal)


async def test_batch_validation_error_shown(tmp_path, db_path):
    app = make_app(tmp_path, db_path)
    async with app.run_test(size=SIZE) as pilot:
        app.screen.query_one("#search-input", Input).value = "k1zz"
        await pilot.click("#search-btn")
        await pilot.pause()
        await pilot.click("#select-all")
        await pilot.pause()
        await pilot.click("#op-batch")
        await pilot.pause()
        app.screen.query_one("#batch-name", Input).value = "fall"
        await pilot.click("#batch-add")
        await pilot.pause()
        assert "QSL_SENT != 'Q'" in op_result(app)
        assert list_batches(connect(db_path)) == []


async def test_update_from_qrz_flow(tmp_path, db_path, clean_qrz_cache):
    FakeQRZ.records = {
        "W1AW": SimpleNamespace(mqsl=0, qslmgr="via W3MB"),
        "K1ZZ": SimpleNamespace(mqsl=1, qslmgr="Bureau"),
    }
    app = make_app(tmp_path, db_path)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.click("#search-btn")
        await pilot.pause()
        await pilot.click("#select-all")
        await pilot.pause()
        await pilot.click("#op-qrz")
        await pilot.pause()
        assert op_result(app) == "Updated 3 QSOs from QRZ."
        conn = connect(db_path)
        try:
            rows = {r["CALL"]: dict(r) for r in
                    conn.execute("SELECT * FROM QSOs").fetchall()}
        finally:
            conn.close()
        assert rows["W1AW"]["QSL_SENT"] == "I"
        assert rows["W1AW"]["QSL_VIA"] == "W3MB"
        assert rows["K1ZZ"]["QSL_SENT_VIA"] == "B"


async def test_export_flow(tmp_path, db_path, clean_qrz_cache):
    FakeQRZ.records = {
        "W1AW": SimpleNamespace(
            mqsl=1, qslmgr="", name_fmt="Station W",
            addr1="1 Main", addr2="", state="VA", zip="20101", country="USA"),
        "K1ZZ": SimpleNamespace(
            mqsl=1, qslmgr="", name_fmt="Station K",
            addr1="2 Main", addr2="", state="MD", zip="20202", country="USA"),
    }
    app = make_app(tmp_path, db_path)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.click("#search-btn")
        await pilot.pause()
        await pilot.click("#select-all")
        await pilot.pause()
        await pilot.click("#op-queued")
        await pilot.pause()
        await pilot.click("#op-batch")
        await pilot.pause()
        app.screen.query_one("#batch-name", Input).value = "spring"
        await pilot.click("#batch-add")
        await pilot.pause()
        assert op_result(app) == "Added 3 QSOs to batch 'spring'."
        app.screen.query_one("#op-export", Button).focus()
        await pilot.pause()
        await pilot.click("#op-export")
        await pilot.pause()
        assert isinstance(app.screen, ExportModal)
        app.screen.query_one("#export-batch", Select).value = "spring"
        app.screen.query_one("#export-base", Input).value = str(
            tmp_path / "labels" / "run")
        await pilot.click("#export-go")
        await pilot.pause()
        assert isinstance(app.screen, MainScreen)
        assert "Exported 2 labels" in op_result(app)
        qsos_csv = tmp_path / "labels" / "run_qsos.csv"
        addr_csv = tmp_path / "labels" / "run_addresses.csv"
        assert qsos_csv.is_file()
        assert addr_csv.is_file()
        assert len(addr_csv.read_text().strip().splitlines()) == 3
        sent_log = tmp_path / "all-sent.csv"
        assert sent_log.is_file()
        lines = sent_log.read_text().strip().splitlines()
        assert lines[0].startswith("CALL,STATION_CALLSIGN,QSO_DATE")
        assert len(lines) == 4


async def test_export_bureau_batch_notes_no_addresses(tmp_path):
    from qsltracker.batches import add_qsos_to_batch
    bureau_db = tmp_path / "bureau.db"
    seed_db(bureau_db, [{
        "CALL": "W2BU", "STATION_CALLSIGN": "N0CALL", "QSO_DATE": "20240505",
        "TIME_ON": "1500", "BAND": "20m", "MODE": "SSB",
        "QSL_SENT": "Q", "QSL_SENT_VIA": "B"}])
    conn = connect(bureau_db)
    try:
        qso_id = conn.execute("SELECT id FROM QSOs").fetchone()[0]
        add_qsos_to_batch(conn, "bureau", [qso_id])
    finally:
        conn.close()
    app = make_app(tmp_path, bureau_db)
    async with app.run_test(size=SIZE) as pilot:
        app.screen.query_one("#op-export", Button).focus()
        await pilot.pause()
        await pilot.click("#op-export")
        await pilot.pause()
        app.screen.query_one("#export-batch", Select).value = "bureau"
        app.screen.query_one("#export-base", Input).value = str(
            tmp_path / "out")
        await pilot.click("#export-go")
        await pilot.pause()
        assert "no address labels" in op_result(app)
        assert (tmp_path / "out_qsos.csv").is_file()
        assert not (tmp_path / "out_addresses.csv").exists()
        sent_rows = (tmp_path / "all-sent.csv").read_text().strip().splitlines()
        assert len(sent_rows) == 2
        assert sent_rows[1].startswith("W2BU,N0CALL")


def test_yaml_round_trip_of_saved_prompt(tmp_path):
    from qsltracker.tui import config_from_fields
    from qsltracker.config import save_config, load_config
    config = config_from_fields({"qsos_per_label": "5", "my_gridsquare": "EM10",
                                 "my_cq_zone": "", "my_itu_zone": "",
                                 "my_cnty": "", "qrz_username": "",
                                 "qrz_password": ""})
    path = tmp_path / "config.yaml"
    save_config(config, path)
    assert load_config(path) == config
    with path.open(encoding="utf-8") as handle:
        assert "my_state" not in handle.read()


async def test_small_terminal_can_reach_export(tmp_path, db_path):
    app = make_app(tmp_path, db_path)
    async with app.run_test(size=(80, 24)) as pilot:
        button = app.screen.query_one('#op-export', Button)
        button.focus()
        await pilot.pause()
        assert await pilot.click('#op-export')
        assert isinstance(app.screen, ExportModal)


@pytest.mark.parametrize('error', [QRZ.SessionError, QRZ.NotFound, QRZ.XMLError])
async def test_export_qrz_errors_are_recoverable(tmp_path, db_path, monkeypatch, error):
    from qsltracker.batches import add_qsos_to_batch
    conn = connect(db_path)
    ids = [row['id'] for row in conn.execute('SELECT id FROM QSOs')]
    from qsltracker.db import mark_queued_to_send
    mark_queued_to_send(conn, ids)
    add_qsos_to_batch(conn, 'test', ids)
    conn.close()
    def fail(*args, **kwargs):
        raise error('QRZ failed')
    monkeypatch.setattr(QrzService, 'address_for_qso', fail)
    app = make_app(tmp_path, db_path)
    async with app.run_test(size=(100, 80)) as pilot:
        app.screen.export_prompt()
        await pilot.pause()
        app.screen.query_one('#export-batch', Select).value = 'test'
        app.screen.query_one('#export-base', Input).value = str(tmp_path / 'run')
        await pilot.click('#export-go')
        await pilot.pause()
        assert isinstance(app.screen, MainScreen)
        assert 'QRZ failed' in op_result(app)

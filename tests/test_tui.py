"""Textual pilot tests for the first-run config prompt and ADIF import."""

import pytest
import yaml
from textual.widgets import Button, Input, Static

from qsltracker.adif import ImportResult
from qsltracker.config import Config
from qsltracker.tui import (
    ConfigScreen,
    DuplicatesScreen,
    MainScreen,
    QsltrackerApp,
    config_from_fields,
    format_duplicate_key,
    format_import_result,
)

SAMPLE = """<EOH>
<CALL:4>W1AW<STATION_CALLSIGN:6>N0CALL<QSO_DATE:8>20240101
<TIME_ON:4>1200<BAND:3>20m<MODE:3>SSB<EOR>
"""


def make_app(tmp_path, config=None, needs_config=False):
    return QsltrackerApp(
        config=config or Config(),
        config_path=tmp_path / "config.yaml",
        db_path=tmp_path / "test.db",
        needs_config=needs_config,
    )


def test_config_from_fields_parses_and_cleans():
    config = config_from_fields({
        "qsos_per_label": "3",
        "my_gridsquare": " FN20 ",
        "my_cq_zone": "5",
        "my_itu_zone": "",
        "my_cnty": "VA,Fairfax",
        "qrz_username": "alice",
        "qrz_password": "s3cret",
    })
    assert config.qsos_per_label == 3
    assert config.my_gridsquare == "FN20"
    assert config.my_cq_zone == 5
    assert config.my_itu_zone is None
    assert config.my_cnty == "VA,Fairfax"


def test_config_from_fields_falls_back_on_bad_input():
    config = config_from_fields({"qsos_per_label": "many",
                                 "my_cq_zone": "xx"})
    assert config.qsos_per_label == 5
    assert config.my_cq_zone is None
    assert not hasattr(config, "my_state")


def test_format_helpers():
    result = ImportResult(
        parsed=2, inserted=1,
        duplicates=[("W1AW", "N0CALL", "20240101", "1200", "20m", "SSB")],
    )
    assert format_import_result(result) == "Imported 1 of 2 QSOs (1 duplicates)."
    assert format_duplicate_key(result.duplicates[0]) == (
        "W1AW | N0CALL | 20240101 | 1200 | 20m | SSB"
    )


async def test_first_run_config_prompt_saves_and_continues(tmp_path):
    app = make_app(tmp_path, needs_config=True)
    async with app.run_test(size=(80, 60)) as pilot:
        assert isinstance(app.screen, ConfigScreen)
        app.screen.query_one("#qsos-per-label", Input).value = "3"
        app.screen.query_one("#my-gridsquare", Input).value = "FN20hf"
        app.screen.query_one("#my-cq-zone", Input).value = "5"
        app.screen.query_one("#my-itu-zone", Input).value = "8"
        app.screen.query_one("#my-cnty", Input).value = "VA,Fairfax"
        app.screen.query_one("#qrz-username", Input).value = "alice"
        app.screen.query_one("#qrz-password", Input).value = "hunter2"
        await pilot.click("#save-btn")
        await pilot.pause()
        assert isinstance(app.screen, MainScreen)
    path = tmp_path / "config.yaml"
    assert path.is_file()
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    assert data["qsos_per_label"] == 3
    assert data["my_gridsquare"] == "FN20hf"
    assert data["my_cq_zone"] == 5
    assert data["qrz_username"] == "alice"
    assert "my_state" not in data
    assert app.config.my_cnty == "VA,Fairfax"


async def test_existing_config_skips_prompt(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("qsos_per_label: 7\n", encoding="utf-8")
    from qsltracker.config import load_config
    app = make_app(tmp_path, config=load_config(path))
    async with app.run_test():
        assert isinstance(app.screen, MainScreen)
        assert "QSOs/label: 7" in app.screen.query_one("#status-line", Static).content


async def test_import_workflow_reports_counts_and_duplicates(tmp_path):
    log = tmp_path / "log.adi"
    log.write_text(SAMPLE, encoding="utf-8")
    app = make_app(tmp_path)
    async with app.run_test() as pilot:
        assert isinstance(app.screen, MainScreen)
        app.screen.query_one("#adif-path", Input).value = str(log)
        await pilot.click("#import-btn")
        await pilot.pause()
        assert app.screen.query_one("#import-result", Static).content == (
            "Imported 1 of 1 QSOs (0 duplicates)."
        )
        assert app.screen.query_one("#duplicates-btn", Button).disabled
        # Import again: everything is now a duplicate. Refocus first,
        # as a user moving back to the button would.
        await pilot.click("#adif-path")
        await pilot.pause()
        await pilot.click("#import-btn")
        await pilot.pause()
        assert app.screen.query_one("#import-result", Static).content == (
            "Imported 0 of 1 QSOs (1 duplicates)."
        )
        assert not app.screen.query_one("#duplicates-btn", Button).disabled
        await pilot.click("#duplicates-btn")
        await pilot.pause()
        assert isinstance(app.screen, DuplicatesScreen)
        listing = app.screen.query_one("#duplicates-list", Static).content
        assert "W1AW | N0CALL | 20240101 | 1200 | 20m | SSB" in listing
        await pilot.click("#close-btn")
        await pilot.pause()
        assert isinstance(app.screen, MainScreen)


async def test_import_missing_file_shows_error(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test() as pilot:
        app.screen.query_one("#adif-path", Input).value = str(tmp_path / "nope.adi")
        await pilot.click("#import-btn")
        await pilot.pause()
        assert "File not found" in app.screen.query_one("#import-result", Static).content


async def test_import_empty_path_prompts_for_filename(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.click("#import-btn")
        await pilot.pause()
        assert "Enter an ADIF filename" in (
            app.screen.query_one("#import-result", Static).content
        )

"""Textual TUI shell for qsltracker.

First run (no ``config.yaml`` yet) opens the configuration prompt so the
user can enter station defaults and QRZ credentials, which are persisted
to disk. Otherwise the main screen offers ADIF import with an
insert/duplicate summary and a modal to inspect duplicate keys. Callsign
search and QSL/batch operations live on the main screen in a later issue.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from textual import on
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, Footer, Header, Input, Label, Static

from qsltracker.adif import ImportResult, import_adif_file
from qsltracker.config import (
    DEFAULT_QSOS_PER_LABEL,
    Config,
    save_config,
)
from qsltracker.db import connect


def config_from_fields(values: dict[str, str]) -> Config:
    """Build a Config from raw config-screen input values."""
    try:
        per_label = int((values.get("qsos_per_label") or "").strip())
    except ValueError:
        per_label = DEFAULT_QSOS_PER_LABEL
    if per_label < 1:
        per_label = DEFAULT_QSOS_PER_LABEL

    def zone(key: str) -> int | None:
        text = (values.get(key) or "").strip()
        if not text:
            return None
        try:
            return int(text)
        except ValueError:
            return None

    return Config(
        qsos_per_label=per_label,
        my_gridsquare=(values.get("my_gridsquare") or "").strip(),
        my_cq_zone=zone("my_cq_zone"),
        my_itu_zone=zone("my_itu_zone"),
        my_cnty=(values.get("my_cnty") or "").strip(),
        qrz_username=(values.get("qrz_username") or "").strip(),
        qrz_password=values.get("qrz_password") or "",
    )


def format_import_result(result: ImportResult) -> str:
    """Summarize an import as ``Imported X of Y QSOs (Z duplicates).``"""
    return (
        f"Imported {result.inserted} of {result.parsed} QSOs "
        f"({len(result.duplicates)} duplicates)."
    )


def format_duplicate_key(key: tuple[Any, ...]) -> str:
    """Render one 6-column duplicate key for display."""
    return " | ".join(str(part) if part != "" else "-" for part in key)


class ConfigScreen(Screen):
    """First-run prompt collecting defaults and persisting ``config.yaml``."""

    def compose(self) -> ComposeResult:
        config = self.app.config
        yield Header()
        yield Vertical(
            Label("No config file found. Enter defaults to get started:"),
            Label("QSOs per label"),
            Input(str(config.qsos_per_label), id="qsos-per-label"),
            Label("My gridsquare"),
            Input(config.my_gridsquare, id="my-gridsquare"),
            Label("My CQ zone"),
            Input(
                "" if config.my_cq_zone is None else str(config.my_cq_zone),
                id="my-cq-zone",
            ),
            Label("My ITU zone"),
            Input(
                "" if config.my_itu_zone is None else str(config.my_itu_zone),
                id="my-itu-zone",
            ),
            Label("My county (STATE,County)"),
            Input(config.my_cnty, id="my-cnty"),
            Label("QRZ username"),
            Input(config.qrz_username, id="qrz-username"),
            Label("QRZ password"),
            Input(config.qrz_password, id="qrz-password", password=True),
            Button("Save configuration", id="save-btn", variant="primary"),
            id="config-form",
        )
        yield Footer()

    @on(Button.Pressed, "#save-btn")
    def save_configuration(self) -> None:
        app = self.app
        values = {
            key: self.query_one(f"#{key}", Input).value
            for key in (
                "qsos-per-label",
                "my-gridsquare",
                "my-cq-zone",
                "my-itu-zone",
                "my-cnty",
                "qrz-username",
                "qrz-password",
            )
        }
        app.config = config_from_fields(
            {key.replace("-", "_"): value for key, value in values.items()}
        )
        save_config(app.config, app.config_path)
        app.needs_config = False
        app.pop_screen()
        app.push_screen(MainScreen())


class DuplicatesScreen(ModalScreen):
    """Modal listing the duplicate keys from the last import."""

    def __init__(self, duplicates: list[tuple[Any, ...]]) -> None:
        super().__init__()
        self._duplicates = duplicates

    def compose(self) -> ComposeResult:
        lines = [format_duplicate_key(key) for key in self._duplicates]
        yield Vertical(
            Label(f"{len(lines)} duplicate QSOs (not imported):"),
            Static("\n".join(lines) or "(none)", id="duplicates-list"),
            Button("Close", id="close-btn", variant="primary"),
            id="duplicates-dialog",
        )

    @on(Button.Pressed, "#close-btn")
    def close_dialog(self) -> None:
        self.app.pop_screen()


class MainScreen(Screen):
    """Main screen: status, ADIF import, and duplicate inspection."""

    def compose(self) -> ComposeResult:
        yield Header()
        yield Vertical(
            Static("", id="status-line"),
            Label("ADIF file to import"),
            Input(placeholder="/path/to/log.adi", id="adif-path"),
            Button("Import ADIF", id="import-btn", variant="primary"),
            Static("", id="import-result"),
            Button("View duplicates", id="duplicates-btn", disabled=True),
            Button("Quit", id="quit-btn"),
            id="main",
        )
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_status()

    def refresh_status(self) -> None:
        app = self.app
        try:
            total = app.db.execute("SELECT COUNT(*) FROM QSOs").fetchone()[0]
        except sqlite3.Error:
            total = "?"
        self.query_one("#status-line", Static).update(
            f"Config: {app.config_path} | DB: {app.db_path} | "
            f"QSOs/label: {app.config.qsos_per_label} | QSOs in DB: {total}"
        )

    @on(Button.Pressed, "#import-btn")
    def import_adif(self) -> None:
        app = self.app
        path = self.query_one("#adif-path", Input).value.strip()
        result_widget = self.query_one("#import-result", Static)
        if not path:
            result_widget.update("Enter an ADIF filename first.")
            return
        try:
            result = import_adif_file(app.db, path, app.config)
        except FileNotFoundError:
            result_widget.update(f"File not found: {path}")
            return
        except OSError as exc:
            result_widget.update(f"Import failed: {exc}")
            return
        app.last_result = result
        result_widget.update(format_import_result(result))
        self.query_one("#duplicates-btn", Button).disabled = not result.duplicates
        self.refresh_status()

    @on(Button.Pressed, "#duplicates-btn")
    def view_duplicates(self) -> None:
        result = self.app.last_result
        if result is not None and result.duplicates:
            self.app.push_screen(DuplicatesScreen(result.duplicates))

    @on(Button.Pressed, "#quit-btn")
    def quit_app(self) -> None:
        self.app.exit()


class QsltrackerApp(App):
    """qsltracker Textual application."""

    TITLE = "qsltracker"
    BINDINGS = [("q", "quit", "Quit")]

    def __init__(
        self,
        config: Config,
        config_path: str | Path,
        db_path: str | Path,
        needs_config: bool = False,
    ) -> None:
        super().__init__()
        self.config = config
        self.config_path = Path(config_path).expanduser()
        self.db_path = Path(db_path).expanduser()
        self.needs_config = needs_config
        self._db: sqlite3.Connection | None = None
        self.last_result: ImportResult | None = None

    @property
    def db(self) -> sqlite3.Connection:
        """The app's database connection, opened lazily and kept open."""
        if self._db is None:
            self._db = connect(self.db_path)
        return self._db

    def on_mount(self) -> None:
        if self.needs_config:
            self.push_screen(ConfigScreen())
        else:
            self.push_screen(MainScreen())

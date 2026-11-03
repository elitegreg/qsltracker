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

from qrzlib import QRZ
from textual import on
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Select,
    Static,
)

from qsltracker.adif import ImportResult, import_adif_file
from qsltracker.batches import (
    BatchError,
    add_qsos_to_batch,
    export_batch,
    list_batches,
)
from qsltracker.config import (
    DEFAULT_QSOS_PER_LABEL,
    Config,
    save_config,
)
from qsltracker.db import (
    connect,
    list_all_qsos,
    mark_queued_to_send,
    mark_received,
    search_qsos,
)
from qsltracker.logging_setup import setup_file_logging
from qsltracker.qrz import QrzService


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


def is_unreceived(qso: dict[str, Any]) -> bool:
    """True when a QSO has no received QSL card."""
    return (qso.get("QSL_RCVD") or "") not in ("Y", "I", "V")


def is_unsent(qso: dict[str, Any]) -> bool:
    """True when a QSO has no sent QSL card."""
    return (qso.get("QSL_SENT") or "") != "Y"


class ViaModal(ModalScreen):
    """Prompt for the received-via channel (Direct or Bureau)."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label("Card received via:"),
            Button("Direct (D)", id="via-direct", variant="primary"),
            Button("Bureau (B)", id="via-bureau", variant="primary"),
            Button("Cancel", id="via-cancel"),
            id="via-dialog",
        )

    def _choose(self, via: str | None) -> None:
        self.app.pop_screen()
        screen = self.app.screen
        if via is not None and isinstance(screen, MainScreen):
            screen.apply_mark_received(via)

    @on(Button.Pressed, "#via-direct")
    def choose_direct(self) -> None:
        self._choose("D")

    @on(Button.Pressed, "#via-bureau")
    def choose_bureau(self) -> None:
        self._choose("B")

    @on(Button.Pressed, "#via-cancel")
    def cancel(self) -> None:
        self._choose(None)

    def action_cancel(self) -> None:
        self._choose(None)


class BatchModal(ModalScreen):
    """Prompt for the batch name to add the selected QSOs to."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        app = self.app
        initial = app.last_batch_name if isinstance(app, QsltrackerApp) else ""
        yield Vertical(
            Label("Batch name:"),
            Input(initial or "", id="batch-name"),
            Static("", id="batch-error"),
            Button("Add to batch", id="batch-add", variant="primary"),
            Button("Cancel", id="batch-cancel"),
            id="batch-dialog",
        )

    @on(Button.Pressed, "#batch-add")
    @on(Input.Submitted, "#batch-name")
    def add_to_batch(self) -> None:
        name = self.query_one("#batch-name", Input).value.strip()
        if not name:
            self.query_one("#batch-error", Static).update("Enter a batch name.")
            return
        self.app.pop_screen()
        screen = self.app.screen
        if isinstance(screen, MainScreen):
            try:
                added = add_qsos_to_batch(screen.app.db, name, screen.selected)
            except BatchError as exc:
                screen.set_op_result(str(exc))
                return
            screen.app.last_batch_name = name
            screen.set_op_result(f"Added {added} QSOs to batch {name!r}.")

    @on(Button.Pressed, "#batch-cancel")
    def cancel(self) -> None:
        self.app.pop_screen()

    def action_cancel(self) -> None:
        self.app.pop_screen()


class ExportModal(ModalScreen):
    """Prompt for the batch to export and the base output path."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        batches = list_batches(self.app.db)
        yield Vertical(
            Label("Batch to export:"),
            Select(
                [(name, name) for name in batches],
                id="export-batch",
                prompt="Select a batch",
            )
            if batches
            else Static("No batches yet — add QSOs to a batch first."),
            Label("Base output path:"),
            Input(placeholder="/tmp/labels/spring", id="export-base"),
            Static("", id="export-error"),
            Button("Export", id="export-go", variant="primary"),
            Button("Cancel", id="export-cancel"),
            id="export-dialog",
        )

    @on(Button.Pressed, "#export-go")
    @on(Input.Submitted, "#export-base")
    def run_export(self) -> None:
        selectors = self.query(Select)
        name = selectors.first().value if selectors else Select.BLANK
        base = self.query_one("#export-base", Input).value.strip()
        if name is Select.BLANK or not name:
            self.query_one("#export-error", Static).update("Select a batch first.")
            return
        if not base:
            self.query_one("#export-error", Static).update(
                "Enter a base output path."
            )
            return
        self.app.pop_screen()
        screen = self.app.screen
        if not isinstance(screen, MainScreen):
            return
        app = screen.app
        try:
            result = export_batch(
                app.db,
                str(name),
                base,
                qsos_per_label=app.config.qsos_per_label,
                qrz_service=QrzService.from_config(app.config),
                sent_log_path=app.config_path.parent / "all-sent.csv",
            )
        except (BatchError, ValueError, OSError) as exc:
            screen.set_op_result(str(exc))
            return
        except (QRZ.SessionError, QRZ.NotFound, QRZ.XMLError) as exc:
            screen.set_op_result(f"QRZ lookup failed: {exc}")
            return
        screen.run_search(screen.query_one("#search-input", Input).value)
        if result.addresses_path is None:
            screen.set_op_result(
                f"Exported {result.labels} labels to {result.qsos_path} "
                "(bureau batch: no address labels)."
            )
        else:
            screen.set_op_result(
                f"Exported {result.labels} labels to {result.qsos_path} "
                f"and {result.addresses_path}."
            )

    @on(Button.Pressed, "#export-cancel")
    def cancel(self) -> None:
        self.app.pop_screen()

    def action_cancel(self) -> None:
        self.app.pop_screen()


class ConfigScreen(Screen):
    """First-run prompt collecting defaults and persisting ``config.yaml``."""

    def compose(self) -> ComposeResult:
        config = self.app.config
        yield Header()
        yield VerticalScroll(
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
        setup_file_logging(app.config_path)
        app.needs_config = False
        app.pop_screen()
        app.push_screen(MainScreen())


class DuplicatesScreen(ModalScreen):
    """Modal listing the duplicate keys from the last import."""

    BINDINGS = [("escape", "close_dialog", "Close")]

    def __init__(self, duplicates: list[tuple[Any, ...]]) -> None:
        super().__init__()
        self._duplicates = duplicates

    def compose(self) -> ComposeResult:
        lines = [format_duplicate_key(key) for key in self._duplicates]
        yield VerticalScroll(
            Label(f"{len(lines)} duplicate QSOs (not imported):"),
            Static("\n".join(lines) or "(none)", id="duplicates-list"),
            Button("Close", id="close-btn", variant="primary"),
            id="duplicates-dialog",
        )

    @on(Button.Pressed, "#close-btn")
    def close_dialog(self) -> None:
        self.app.pop_screen()

    def action_close_dialog(self) -> None:
        self.app.pop_screen()


TABLE_COLUMNS = (
    ("sel", "✓"),
    ("CALL", "Call"),
    ("QSO_DATE", "Date"),
    ("TIME_ON", "Time"),
    ("BAND", "Band"),
    ("MODE", "Mode"),
    ("QSL_RCVD", "Rcvd"),
    ("QSL_SENT", "Sent"),
    ("QSL_SENT_VIA", "Via"),
    ("QSL_VIA", "Mgr"),
)


class MainScreen(Screen):
    """Main screen: import, search, selection, and QSL/batch operations."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[dict[str, Any]] = []
        self.selected: set[int] = set()

    def compose(self) -> ComposeResult:
        yield Header()
        yield VerticalScroll(
            Static("", id="status-line"),
            Label("ADIF file to import"),
            Horizontal(
                Input(placeholder="/path/to/log.adi", id="adif-path"),
                Button("Import ADIF", id="import-btn", variant="primary"),
                Button("View duplicates", id="duplicates-btn", disabled=True),
                classes="input-row",
            ),
            Static("", id="import-result"),
            Label("Callsign search"),
            Horizontal(
                Input(placeholder="W1AW (blank shows all)", id="search-input"),
                Button("Search", id="search-btn", variant="primary"),
                classes="input-row",
            ),
            DataTable(id="qso-table"),
            Static("", id="selection-line"),
            Horizontal(
                Button("Select all", id="select-all"),
                Button("Select unreceived", id="select-unreceived"),
                Button("Select unsent", id="select-unsent"),
                Button("Clear selection", id="select-clear"),
                classes="button-row",
            ),
            Horizontal(
                Button("Mark received…", id="op-received", variant="primary"),
                Button("Mark queued to send", id="op-queued"),
                Button("Add to batch…", id="op-batch"),
                classes="button-row",
            ),
            Horizontal(
                Button("Add to last batch", id="op-last-batch"),
                Button("Update from QRZ", id="op-qrz"),
                Button("Export batch…", id="op-export"),
                Button("Quit", id="quit-btn"),
                classes="button-row",
            ),
            Static("", id="op-result"),
            id="main",
        )
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#qso-table", DataTable)
        table.cursor_type = "row"
        for key, label in TABLE_COLUMNS:
            table.add_column(label, key=key)
        self.refresh_status()
        self.refresh_table()

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

    def refresh_table(self) -> None:
        """Reload the table from the last search, keeping valid selections."""
        table = self.query_one("#qso-table", DataTable)
        table.clear()
        shown = set()
        for qso in self.rows:
            shown.add(qso["id"])
            table.add_row(
                "✓" if qso["id"] in self.selected else "",
                *(str(qso.get(column) or "") for _, column in TABLE_COLUMNS[1:]),
                key=str(qso["id"]),
            )
        self.selected &= shown
        self.update_selection_line()

    def update_selection_line(self) -> None:
        self.query_one("#selection-line", Static).update(
            f"{len(self.rows)} QSOs shown, {len(self.selected)} selected."
        )

    def set_op_result(self, message: str) -> None:
        self.query_one("#op-result", Static).update(message)

    def run_search(self, call: str) -> None:
        call = call.strip()
        if call:
            self.rows = search_qsos(self.app.db, call)
        else:
            self.rows = list_all_qsos(self.app.db)
        self.refresh_table()

    @on(Button.Pressed, "#search-btn")
    @on(Input.Submitted, "#search-input")
    def perform_search(self) -> None:
        self.run_search(self.query_one("#search-input", Input).value)

    def toggle_row(self, qso_id: int) -> None:
        if qso_id in self.selected:
            self.selected.discard(qso_id)
        else:
            self.selected.add(qso_id)
        self.query_one("#qso-table", DataTable).update_cell(
            str(qso_id), "sel", "✓" if qso_id in self.selected else ""
        )
        self.update_selection_line()

    @on(DataTable.RowSelected, "#qso-table")
    def toggle_selected_row(self, event: DataTable.RowSelected) -> None:
        try:
            self.toggle_row(int(event.row_key.value))
        except (TypeError, ValueError):
            return

    def select_where(self, predicate) -> None:
        self.selected = {
            qso["id"] for qso in self.rows if predicate(qso)
        }
        self.refresh_table()

    @on(Button.Pressed, "#select-all")
    def select_all(self) -> None:
        self.select_where(lambda _qso: True)

    @on(Button.Pressed, "#select-unreceived")
    def select_unreceived(self) -> None:
        self.select_where(is_unreceived)

    @on(Button.Pressed, "#select-unsent")
    def select_unsent(self) -> None:
        self.select_where(is_unsent)

    @on(Button.Pressed, "#select-clear")
    def clear_qso_selection(self) -> None:
        # Named to avoid overriding Screen.clear_selection(), which the
        # framework calls on every forwarded mouse event.
        self.select_where(lambda _qso: False)

    def apply_mark_received(self, via: str) -> None:
        if not self.selected:
            self.set_op_result("Select at least one QSO first.")
            return
        count = mark_received(self.app.db, sorted(self.selected), via)
        self.set_op_result(f"Marked {count} QSOs received via {via}.")
        self.run_search(self.query_one("#search-input", Input).value)

    @on(Button.Pressed, "#op-received")
    def mark_received_prompt(self) -> None:
        if not self.selected:
            self.set_op_result("Select at least one QSO first.")
            return
        self.app.push_screen(ViaModal())

    @on(Button.Pressed, "#op-queued")
    def mark_queued(self) -> None:
        if not self.selected:
            self.set_op_result("Select at least one QSO first.")
            return
        count = mark_queued_to_send(self.app.db, sorted(self.selected))
        self.set_op_result(f"Marked {count} QSOs queued to send.")
        self.run_search(self.query_one("#search-input", Input).value)

    @on(Button.Pressed, "#op-batch")
    def add_to_batch_prompt(self) -> None:
        if not self.selected:
            self.set_op_result("Select at least one QSO first.")
            return
        self.app.push_screen(BatchModal())

    @on(Button.Pressed, "#op-last-batch")
    def add_to_last_batch(self) -> None:
        if not self.selected:
            self.set_op_result("Select at least one QSO first.")
            return
        name = self.app.last_batch_name
        if not name:
            self.app.push_screen(BatchModal())
            return
        try:
            added = add_qsos_to_batch(self.app.db, name, self.selected)
        except BatchError as exc:
            self.set_op_result(str(exc))
            return
        self.set_op_result(f"Added {added} QSOs to batch {name!r}.")

    @on(Button.Pressed, "#op-qrz")
    def update_from_qrz(self) -> None:
        if not self.selected:
            self.set_op_result("Select at least one QSO first.")
            return
        try:
            service = QrzService.from_config(self.app.config)
            updated = service.update_qsos_from_qrz(
                self.app.db, sorted(self.selected)
            )
        except Exception as exc:
            self.set_op_result(f"QRZ update failed: {exc}")
            return
        self.set_op_result(f"Updated {updated} QSOs from QRZ.")
        self.run_search(self.query_one("#search-input", Input).value)

    @on(Button.Pressed, "#op-export")
    def export_prompt(self) -> None:
        self.app.push_screen(ExportModal())

    @on(Button.Pressed, "#import-btn")
    @on(Input.Submitted, "#adif-path")
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
        except (OSError, ValueError) as exc:
            result_widget.update(f"Import failed: {exc}")
            return
        app.last_result = result
        result_widget.update(format_import_result(result))
        self.query_one("#duplicates-btn", Button).disabled = not result.duplicates
        self.refresh_status()
        self.run_search(self.query_one("#search-input", Input).value)

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

    CSS = """
    #qso-table { height: 12; }
    #main, #config-form, #duplicates-dialog { height: 1fr; }
    .input-row, .button-row { height: auto; }
    .input-row Input { width: 1fr; }
    """
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
        self.last_batch_name: str | None = None

    @property
    def db(self) -> sqlite3.Connection:
        """The app's database connection, opened lazily and kept open."""
        if self._db is None:
            self._db = connect(self.db_path)
        return self._db

    def close_db(self) -> None:
        """Close the database connection if open."""
        if self._db is not None:
            self._db.close()
            self._db = None

    def on_mount(self) -> None:
        if self.needs_config:
            self.push_screen(ConfigScreen())
        else:
            self.push_screen(MainScreen())

    def on_unmount(self) -> None:
        self.close_db()

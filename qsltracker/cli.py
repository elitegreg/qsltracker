"""Command-line interface for qsltracker."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from qsltracker.config import (
    DEFAULT_QSOS_PER_LABEL,
    Config,
    config_file_exists,
    get_default_config_path,
    load_config,
)
from qsltracker.db import get_default_db_path
from qsltracker.logging_setup import setup_file_logging
from qsltracker.tui import QsltrackerApp

QSOS_PER_LABEL_FLAG = "--qsos-per-label"


def build_parser() -> argparse.ArgumentParser:
    """Build the ``qsltracker`` argument parser."""
    parser = argparse.ArgumentParser(
        prog="qsltracker",
        description="Track QSL cards, batches, and label exports.",
        allow_abbrev=False,
    )
    parser.add_argument(
        "--config",
        default=str(get_default_config_path()),
        help="Path to config.yaml (default: %(default)s)",
    )
    parser.add_argument(
        QSOS_PER_LABEL_FLAG,
        type=int,
        default=DEFAULT_QSOS_PER_LABEL,
        metavar="N",
        help="QSOs printed per label (default: %(default)s). "
        "Overrides the config file value when given explicitly.",
    )
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments, defaulting to ``sys.argv[1:]``."""
    return build_parser().parse_args(argv)


def _flag_given_explicitly(raw_args: list[str], flag: str) -> bool:
    return any(arg == flag or arg.startswith(flag + "=") for arg in raw_args)


def build_app(argv: list[str] | None = None) -> QsltrackerApp:
    """Build the Textual app from CLI options without running it.

    When the config file does not exist, in-memory defaults are used and
    the app opens the first-run configuration prompt, which persists the
    file on save.
    """
    args = parse_args(argv)
    raw_args = list(sys.argv[1:] if argv is None else argv)
    config_path = Path(args.config).expanduser()
    if config_file_exists(config_path):
        config = load_config(config_path)
        needs_config = False
        if _flag_given_explicitly(raw_args, QSOS_PER_LABEL_FLAG):
            config.qsos_per_label = args.qsos_per_label
        setup_file_logging(config_path)
    else:
        config = Config(qsos_per_label=args.qsos_per_label)
        needs_config = True
    return QsltrackerApp(
        config=config,
        config_path=config_path,
        db_path=get_default_db_path(config_path),
        needs_config=needs_config,
    )


def main(argv: list[str] | None = None) -> None:
    """Entry point for the ``qsltracker`` console script."""
    build_app(argv).run()


if __name__ == "__main__":
    main()

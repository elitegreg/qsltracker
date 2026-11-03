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
    save_config,
)

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


def main(argv: list[str] | None = None) -> int:
    """Entry point for the ``qsltracker`` console script."""
    args = parse_args(argv)
    raw_args = list(sys.argv[1:] if argv is None else argv)
    config_path = Path(args.config).expanduser()

    if config_file_exists(config_path):
        config = load_config(config_path)
        if _flag_given_explicitly(raw_args, QSOS_PER_LABEL_FLAG):
            config.qsos_per_label = args.qsos_per_label
    else:
        config = Config(qsos_per_label=args.qsos_per_label)
        save_config(config, config_path)
        print(f"No config file found; wrote defaults to {config_path}")

    print(
        f"Using config: {config_path} "
        f"(qsos_per_label={config.qsos_per_label})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

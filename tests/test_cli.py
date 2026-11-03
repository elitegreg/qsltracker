"""Unit tests for CLI argument parsing and app startup wiring."""

import yaml

from qsltracker.cli import build_app, parse_args
from qsltracker.config import Config, get_default_config_path
from qsltracker.db import get_default_db_path


def test_config_option_defaults_to_standard_path():
    args = parse_args([])
    assert args.config == str(get_default_config_path())
    assert args.config.endswith(".config/qsltracker/config.yaml")


def test_config_option_accepts_custom_path(tmp_path):
    custom = tmp_path / "custom.yaml"
    args = parse_args(["--config", str(custom)])
    assert args.config == str(custom)


def test_qsos_per_label_defaults_to_five():
    assert parse_args([]).qsos_per_label == 5


def test_qsos_per_label_cli_override():
    assert parse_args(["--qsos-per-label", "10"]).qsos_per_label == 10
    assert parse_args(["--qsos-per-label=3"]).qsos_per_label == 3


def test_build_app_missing_config_uses_defaults_without_writing(tmp_path):
    path = tmp_path / "sub" / "config.yaml"
    app = build_app(["--config", str(path)])
    assert app.config == Config()
    assert app.config.qsos_per_label == 5
    assert app.needs_config is True
    assert not path.exists()
    assert app.db_path == get_default_db_path()


def test_build_app_loads_existing_config_value(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("qsos_per_label: 7\n", encoding="utf-8")
    app = build_app(["--config", str(path)])
    assert app.config.qsos_per_label == 7
    assert app.needs_config is False
    with path.open(encoding="utf-8") as handle:
        assert yaml.safe_load(handle)["qsos_per_label"] == 7


def test_build_app_cli_override_wins_over_config_file(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("qsos_per_label: 7\n", encoding="utf-8")
    app = build_app(["--config", str(path), "--qsos-per-label", "2"])
    assert app.config.qsos_per_label == 2
    with path.open(encoding="utf-8") as handle:
        assert yaml.safe_load(handle)["qsos_per_label"] == 7


def test_build_app_seeds_defaults_with_cli_override(tmp_path):
    path = tmp_path / "config.yaml"
    app = build_app(["--config", str(path), "--qsos-per-label", "4"])
    assert app.config.qsos_per_label == 4
    assert app.needs_config is True
    assert not path.exists()

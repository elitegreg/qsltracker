"""Unit tests for CLI argument parsing and startup config handling."""

import yaml

from qsltracker.cli import main, parse_args
from qsltracker.config import Config, get_default_config_path


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


def test_main_creates_missing_config_with_defaults(tmp_path, capsys):
    path = tmp_path / "sub" / "config.yaml"
    assert main(["--config", str(path)]) == 0
    assert path.is_file()
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    assert data["qsos_per_label"] == 5
    assert "my_state" not in data
    out = capsys.readouterr().out
    assert "qsos_per_label=5" in out


def test_main_loads_existing_config_value(tmp_path, capsys):
    path = tmp_path / "config.yaml"
    path.write_text("qsos_per_label: 7\n", encoding="utf-8")
    assert main(["--config", str(path)]) == 0
    out = capsys.readouterr().out
    assert "qsos_per_label=7" in out
    with path.open(encoding="utf-8") as handle:
        assert yaml.safe_load(handle)["qsos_per_label"] == 7


def test_main_cli_override_wins_over_config_file(tmp_path, capsys):
    path = tmp_path / "config.yaml"
    path.write_text("qsos_per_label: 7\n", encoding="utf-8")
    assert main(["--config", str(path), "--qsos-per-label", "2"]) == 0
    out = capsys.readouterr().out
    assert "qsos_per_label=2" in out
    with path.open(encoding="utf-8") as handle:
        assert yaml.safe_load(handle)["qsos_per_label"] == 7


def test_main_seeds_new_config_with_cli_override(tmp_path):
    path = tmp_path / "config.yaml"
    assert main(["--config", str(path), "--qsos-per-label", "4"]) == 0
    with path.open(encoding="utf-8") as handle:
        assert yaml.safe_load(handle)["qsos_per_label"] == 4


def test_main_reports_effective_config(tmp_path, capsys):
    path = tmp_path / "config.yaml"
    assert main(["--config", str(path)]) == 0
    out = capsys.readouterr().out
    assert str(path) in out
    assert Config().qsos_per_label == 5

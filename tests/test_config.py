"""Unit tests for YAML configuration loading and saving."""

from pathlib import Path

import yaml

from qsltracker.config import (
    CONFIG_FIELDS,
    DEFAULT_QSOS_PER_LABEL,
    Config,
    config_file_exists,
    ensure_config,
    get_default_config_path,
    load_config,
    save_config,
)

EXPECTED_FIELDS = (
    "qsos_per_label",
    "my_gridsquare",
    "my_cq_zone",
    "my_itu_zone",
    "my_cnty",
    "qrz_username",
    "qrz_password",
)


def test_config_field_inventory_matches_spec():
    assert CONFIG_FIELDS == EXPECTED_FIELDS
    assert "my_state" not in CONFIG_FIELDS


def test_defaults():
    config = Config()
    assert config.qsos_per_label == 5
    assert config.qsos_per_label == DEFAULT_QSOS_PER_LABEL
    assert config.my_gridsquare == ""
    assert config.my_cq_zone is None
    assert config.my_itu_zone is None
    assert config.my_cnty == ""
    assert config.qrz_username == ""
    assert config.qrz_password == ""
    assert not hasattr(config, "my_state")


def test_default_config_path():
    assert get_default_config_path() == (
        Path.home() / ".config" / "qsltracker" / "config.yaml"
    )


def test_config_file_exists(tmp_path):
    missing = tmp_path / "config.yaml"
    assert not config_file_exists(missing)
    missing.write_text("qsos_per_label: 5\n", encoding="utf-8")
    assert config_file_exists(missing)
    assert not config_file_exists(tmp_path)


def test_load_missing_file_returns_defaults(tmp_path):
    config = load_config(tmp_path / "nope" / "config.yaml")
    assert config == Config()


def test_round_trip_preserves_all_fields(tmp_path):
    path = tmp_path / "config.yaml"
    original = Config(
        qsos_per_label=3,
        my_gridsquare="FN20hf",
        my_cq_zone=5,
        my_itu_zone=8,
        my_cnty="VA,Fairfax",
        qrz_username="callsign",
        qrz_password="s3cret",
    )
    save_config(original, path)
    assert load_config(path) == original


def test_saved_yaml_has_exact_keys_and_no_my_state(tmp_path):
    path = tmp_path / "config.yaml"
    save_config(Config(), path)
    text = path.read_text(encoding="utf-8")
    assert "my_state" not in text
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    assert set(data.keys()) == set(EXPECTED_FIELDS)


def test_save_creates_parent_directories(tmp_path):
    path = tmp_path / "deep" / "nested" / "config.yaml"
    save_config(Config(qsos_per_label=2), path)
    assert load_config(path).qsos_per_label == 2


def test_load_ignores_unknown_keys_including_my_state(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "qsos_per_label: 4\nmy_state: VA\nbogus_key: 1\n",
        encoding="utf-8",
    )
    config = load_config(path)
    assert config.qsos_per_label == 4
    assert not hasattr(config, "my_state")
    assert not hasattr(config, "bogus_key")


def test_load_empty_file_returns_defaults(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("", encoding="utf-8")
    assert load_config(path) == Config()


def test_invalid_qsos_per_label_falls_back_to_default(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("qsos_per_label: not-a-number\n", encoding="utf-8")
    assert load_config(path).qsos_per_label == DEFAULT_QSOS_PER_LABEL
    path.write_text("qsos_per_label: 0\n", encoding="utf-8")
    assert load_config(path).qsos_per_label == DEFAULT_QSOS_PER_LABEL


def test_ensure_config_creates_defaults_once(tmp_path):
    path = tmp_path / "config.yaml"
    config, created = ensure_config(path)
    assert created is True
    assert config == Config()
    assert path.is_file()
    loaded, created_again = ensure_config(path)
    assert created_again is False
    assert loaded == config

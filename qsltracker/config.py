"""YAML configuration management for qsltracker.

The configuration lives in ``config.yaml`` (by default
``~/.config/qsltracker/config.yaml``) and holds the QSO label defaults,
the operator's ``my_*`` station defaults, and the QRZ credentials.

There is intentionally no default for ``my_state``: per the ADIF import
rules it is derived from ``my_cnty`` when present, never configured.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

DEFAULT_QSOS_PER_LABEL = 5

CONFIG_FIELDS = (
    "qsos_per_label",
    "my_gridsquare",
    "my_cq_zone",
    "my_itu_zone",
    "my_cnty",
    "qrz_username",
    "qrz_password",
)


def get_default_config_path() -> Path:
    """Return the default ``config.yaml`` path, honoring the current HOME."""
    return Path.home() / ".config" / "qsltracker" / "config.yaml"


DEFAULT_CONFIG_PATH = get_default_config_path()


def _expand(path: str | Path) -> Path:
    return Path(path).expanduser()


def _coerce_qsos_per_label(value: Any) -> int:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_QSOS_PER_LABEL
    if number < 1:
        return DEFAULT_QSOS_PER_LABEL
    return number


def _coerce_zone(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _coerce_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


@dataclass
class Config:
    """In-memory configuration with the documented defaults.

    Note: there is deliberately no ``my_state`` field; it is derived
    from ``my_cnty`` during ADIF import instead of being configured.
    """

    qsos_per_label: int = DEFAULT_QSOS_PER_LABEL
    my_gridsquare: str = ""
    my_cq_zone: int | None = None
    my_itu_zone: int | None = None
    my_cnty: str = ""
    qrz_username: str = ""
    qrz_password: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> Config:
        """Build a Config from parsed YAML data, ignoring unknown keys."""
        if not isinstance(data, dict):
            return cls()
        return cls(
            qsos_per_label=_coerce_qsos_per_label(
                data.get("qsos_per_label", DEFAULT_QSOS_PER_LABEL)
            ),
            my_gridsquare=_coerce_str(data.get("my_gridsquare", "")),
            my_cq_zone=_coerce_zone(data.get("my_cq_zone")),
            my_itu_zone=_coerce_zone(data.get("my_itu_zone")),
            my_cnty=_coerce_str(data.get("my_cnty", "")),
            qrz_username=_coerce_str(data.get("qrz_username", "")),
            qrz_password=_coerce_str(data.get("qrz_password", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to the exact keys stored in ``config.yaml``."""
        return {
            "qsos_per_label": self.qsos_per_label,
            "my_gridsquare": self.my_gridsquare,
            "my_cq_zone": self.my_cq_zone,
            "my_itu_zone": self.my_itu_zone,
            "my_cnty": self.my_cnty,
            "qrz_username": self.qrz_username,
            "qrz_password": self.qrz_password,
        }


def config_file_exists(path: str | Path) -> bool:
    """Detect whether a config file already exists at ``path``.

    The startup flow uses this to decide whether to prompt the user for
    initial defaults (missing) or load the saved file (present).
    """
    return _expand(path).is_file()


def load_config(path: str | Path) -> Config:
    """Load configuration from ``path``, returning defaults when missing.

    A missing, empty, or malformed file yields a default ``Config``
    rather than raising, so first-run startup can proceed to prompting.
    Use :func:`config_file_exists` when the caller needs to distinguish
    "no config file yet" from "config file with default values".
    """
    expanded = _expand(path)
    if not expanded.is_file():
        return Config()
    with expanded.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    return Config.from_dict(data)


def save_config(config: Config, path: str | Path) -> Path:
    """Persist ``config`` to ``path``, creating parent directories."""
    expanded = _expand(path)
    expanded.parent.mkdir(parents=True, exist_ok=True)
    with expanded.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(config.to_dict(), handle, sort_keys=False)
    return expanded


def ensure_config(path: str | Path) -> tuple[Config, bool]:
    """Load the config file, writing defaults first if it does not exist.

    Returns ``(config, created)`` where ``created`` is True when the file
    was missing and a default one was persisted to disk.
    """
    expanded = _expand(path)
    if expanded.is_file():
        return load_config(expanded), False
    config = Config()
    save_config(config, expanded)
    return config, True

"""Unit tests for file logging setup."""

import logging

from qsltracker.logging_setup import LOG_FILENAME, setup_file_logging


def _handlers():
    return logging.getLogger("qsltracker").handlers


def test_setup_creates_log_parallel_to_config(tmp_path):
    config_path = tmp_path / "sub" / "config.yaml"
    log_path = setup_file_logging(config_path)
    try:
        assert log_path == tmp_path / "sub" / LOG_FILENAME
        assert log_path.is_file()
    finally:
        for handler in list(_handlers()):
            if isinstance(handler, logging.FileHandler):
                logging.getLogger("qsltracker").removeHandler(handler)
                handler.close()


def test_setup_does_not_add_duplicate_handlers(tmp_path):
    config_path = tmp_path / "config.yaml"
    try:
        setup_file_logging(config_path)
        count = len(_handlers())
        setup_file_logging(config_path)
        assert len(_handlers()) == count
    finally:
        for handler in list(_handlers()):
            if isinstance(handler, logging.FileHandler):
                logging.getLogger("qsltracker").removeHandler(handler)
                handler.close()


def test_records_reach_the_log_file(tmp_path, caplog):
    config_path = tmp_path / "config.yaml"
    log_path = setup_file_logging(config_path)
    try:
        logging.getLogger("qsltracker.db").info("probe line")
        for handler in _handlers():
            handler.flush()
        assert "probe line" in log_path.read_text(encoding="utf-8")
    finally:
        for handler in list(_handlers()):
            if isinstance(handler, logging.FileHandler):
                logging.getLogger("qsltracker").removeHandler(handler)
                handler.close()

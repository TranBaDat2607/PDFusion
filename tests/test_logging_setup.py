"""`configure_logging()` (`utils/logging_setup.py`) — the one rotating setup
shared by every sidecar entry point (`main.py`, `api/server.py::main`, and the
`pdfusion-sidecar` console script). See #26.

The root logger is a process-wide singleton, so every test here passes an
explicit `tmp_path` as `log_dir` (never touching the developer's real
`~/AppData/Local/PDFusion/logs/`) and restores the root logger's handlers
afterward so nothing leaks into other tests or pytest's own log capture.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

from desktop_pdf_translator.utils.logging_setup import (
    _BACKUP_COUNT,
    _MAX_BYTES,
    _NOISY_LOGGERS,
    configure_logging,
)


@pytest.fixture(autouse=True)
def _restore_root_logger():
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    saved_noisy_levels = {name: logging.getLogger(name).level for name in _NOISY_LOGGERS}
    yield
    for handler in root.handlers:
        if handler not in saved_handlers:
            handler.close()
    root.handlers = saved_handlers
    root.setLevel(saved_level)
    for name, level in saved_noisy_levels.items():
        logging.getLogger(name).setLevel(level)


def test_creates_rotating_file_and_stream_handler(tmp_path: Path) -> None:
    configure_logging(log_dir=tmp_path)

    root = logging.getLogger()
    assert len(root.handlers) == 2

    file_handlers = [h for h in root.handlers if isinstance(h, RotatingFileHandler)]
    stream_handlers = [
        h for h in root.handlers if isinstance(h, logging.StreamHandler) and not isinstance(h, RotatingFileHandler)
    ]
    assert len(file_handlers) == 1
    assert len(stream_handlers) == 1

    handler = file_handlers[0]
    assert handler.maxBytes == 5 * 1024 * 1024 == _MAX_BYTES
    assert handler.backupCount == 5 == _BACKUP_COUNT
    base = Path(handler.baseFilename)
    assert base.name == "app.log"
    assert base.parent.resolve() == tmp_path.resolve()


def test_writes_a_formatted_line(tmp_path: Path) -> None:
    configure_logging(log_dir=tmp_path)

    logging.getLogger("some.module").info("hello from a test")

    log_file = tmp_path / "app.log"
    assert log_file.exists()
    contents = log_file.read_text(encoding="utf-8")
    assert " - some.module - INFO - hello from a test" in contents


def test_creates_log_dir_if_missing(tmp_path: Path) -> None:
    log_dir = tmp_path / "not-yet-created"
    assert not log_dir.exists()

    configure_logging(log_dir=log_dir)

    assert log_dir.exists()
    assert (log_dir / "app.log").exists()


def test_idempotent_when_called_twice(tmp_path: Path) -> None:
    """Both main.py and server.py::main call this in-process when the bundled
    exe runs; force=True must make the second call a no-op re-application,
    not a duplicate set of handlers."""
    configure_logging(log_dir=tmp_path)
    configure_logging(log_dir=tmp_path)

    assert len(logging.getLogger().handlers) == 2


def test_suppresses_noisy_loggers(tmp_path: Path) -> None:
    configure_logging(log_dir=tmp_path)

    for name in ("urllib3", "requests", "uvicorn.access"):
        assert logging.getLogger(name).level == logging.WARNING

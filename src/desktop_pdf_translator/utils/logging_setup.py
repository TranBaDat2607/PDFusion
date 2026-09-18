"""One rotating logging config, shared by every sidecar entry point.

Before this existed, `main.py` and `api/server.py::main` each called their own
`logging.basicConfig` with different formats, and only `main.py`'s attached a
file handler at all (a plain unbounded `FileHandler`, never rotated). Since
`logging.basicConfig` is a no-op once the root logger already has handlers,
whichever entry point ran first silently "won" — and the sidecar has three of
them: the bundled `pdfusion-sidecar.exe` and `python main.py` (both go through
`main.py`), `python -m desktop_pdf_translator.api.server` (what `pnpm tauri
dev` actually spawns), and the pip-installed `pdfusion-sidecar` console script
(`server.py:main` directly, per `pyproject.toml`). Only the first of those
three ever produced a file at all.

`configure_logging()` is now called from both `main.py` (before it imports
`server.py`, so an import failure there still lands in `app.log`) and
`server.py::main` (so the other two entry points get identical logging).
`force=True` on `basicConfig` makes the second call, when both run in the same
process, a harmless no-op re-application of the same handlers — and is what
makes this function safely callable more than once from tests.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .paths import logs_dir

_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
_NOISY_LOGGERS = ("urllib3", "requests", "uvicorn.access")
_MAX_BYTES = 5 * 1024 * 1024  # 5 MB
_BACKUP_COUNT = 5  # app.log + 5 rotated backups


def configure_logging(log_dir: Path | None = None) -> None:
    """Configure the root logger: rotating file handler + stderr.

    `log_dir` defaults to `logs_dir()` — `logs/` under the platform's data root
    (`utils/paths.appdata_dir`).
    Tests should always pass an explicit `tmp_path` — this is a process-wide
    singleton (the root logger), and never touches the real AppData dir when
    given one.
    """
    target = log_dir if log_dir is not None else logs_dir()
    target.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format=_FORMAT,
        handlers=[
            RotatingFileHandler(
                target / "app.log",
                maxBytes=_MAX_BYTES,
                backupCount=_BACKUP_COUNT,
                encoding="utf-8",
            ),
            logging.StreamHandler(sys.stderr),
        ],
        force=True,
    )
    for noisy in _NOISY_LOGGERS:
        logging.getLogger(noisy).setLevel(logging.WARNING)

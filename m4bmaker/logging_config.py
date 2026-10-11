"""Application-wide logging configuration with rotating file handler and crash reporting."""

from __future__ import annotations

import io
import logging
from logging.handlers import RotatingFileHandler
import os
import sys
from pathlib import Path

from platformdirs import user_data_dir

_log = logging.getLogger("m4bmaker")
_LOG_FILE: Path | None = None


def get_log_file_path() -> Path:
    """Return the path to the active log file.

    Prefers portable folder next to executable if running frozen and writable,
    otherwise user application data directory.
    """
    global _LOG_FILE
    if _LOG_FILE is not None:
        return _LOG_FILE

    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).parent
        portable_logs = exe_dir / "logs"
        try:
            portable_logs.mkdir(parents=True, exist_ok=True)
            _LOG_FILE = portable_logs / "m4bmaker.log"
            return _LOG_FILE
        except Exception:
            pass

    app_logs = Path(user_data_dir("m4bmaker")) / "logs"
    app_logs.mkdir(parents=True, exist_ok=True)
    _LOG_FILE = app_logs / "m4bmaker.log"
    return _LOG_FILE


class _StreamToLogger:
    """Fake file-like stream that redirects stdout/stderr writes to logging."""

    def __init__(self, logger: logging.Logger, level: int) -> None:
        self._logger = logger
        self._level = level

    def write(self, buf: str) -> None:
        for line in buf.rstrip().splitlines():
            line_str = line.strip()
            if line_str:
                self._logger.log(self._level, line_str)

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return False

    def fileno(self) -> int:
        raise io.UnsupportedOperation("fileno")

    def writable(self) -> bool:
        return True

    def readable(self) -> bool:
        return False

    def seekable(self) -> bool:
        return False


def setup_logging() -> Path:
    """Initialize rotating file handler, console handler, and crash hooks."""
    log_path = get_log_file_path()

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    # Avoid duplicate handlers if setup_logging is called multiple times
    for h in list(root_logger.handlers):
        root_logger.removeHandler(h)

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] [%(name)s:%(threadName)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Rotating file handler (5 MB, 3 backups)
    try:
        file_handler = RotatingFileHandler(
            str(log_path),
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(logging.INFO)
        root_logger.addHandler(file_handler)
    except Exception as exc:
        sys.stderr.write(f"Failed to create RotatingFileHandler at {log_path}: {exc}\n")

    # Console stream handler if terminal attached
    if sys.stderr and not getattr(sys, "frozen", False):
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setFormatter(formatter)
        console_handler.setLevel(logging.INFO)
        root_logger.addHandler(console_handler)

    # Redirect sys.stdout and sys.stderr in frozen builds so output isn't lost
    if getattr(sys, "frozen", False):
        sys.stdout = _StreamToLogger(logging.getLogger("stdout"), logging.INFO)  # type: ignore[assignment]
        sys.stderr = _StreamToLogger(logging.getLogger("stderr"), logging.ERROR)  # type: ignore[assignment]

    # Global uncaught exception hook
    def excepthook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        root_logger.critical("Uncaught top-level exception:", exc_info=(exc_type, exc_value, exc_tb))
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox
            if QApplication.instance():
                import traceback
                tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
                box = QMessageBox()
                box.setIcon(QMessageBox.Icon.Critical)
                box.setWindowTitle("Application Error")
                box.setText(f"An unexpected error occurred:\n{exc_value}")
                box.setInformativeText(f"Full log saved to:\n{log_path}\n\nPress Ctrl+C to copy this error.")
                box.setDetailedText(tb_text)
                box.exec()
        except Exception:
            pass

    sys.excepthook = excepthook
    _log.info("Logging initialized. Writing to %s", log_path)
    return log_path

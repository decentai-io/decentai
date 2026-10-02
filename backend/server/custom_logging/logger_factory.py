"""Unified logger.

One logger for every environment. It always writes to stdout, so any
container runtime collects the logs — no platform branching, no
mandatory log directory.

Configuration (all optional, read from the environment):

    LOG_LEVEL        DEBUG | INFO | WARNING | ERROR   (default INFO)
    LOG_FORMAT       text | json                      (default text)
                     Use "json" in the cloud for structured log ingestion.
    LOG_COLOR        true | false                     (default: auto — color
                     only when stdout is an interactive TTY)
    LOG_TO_FILE      true | false                     (default false)
                     When true, also writes rotating files under LOGS_DIR.
    LOGS_DIR         directory for file logs           (default ./logs)
    LOG_MAX_BYTES    rotating file size in bytes       (default 10_000_000)
    LOG_BACKUP_COUNT number of rotated files kept      (default 5)
"""

import json
import logging
import os
import sys
from logging.handlers import RotatingFileHandler


_LINE_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def _env(name: str, default: str = "") -> str:
    value = os.getenv(name)
    return value if value is not None and value.strip() != "" else default


def _truthy(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "y", "on")


class _SafeStreamHandler(logging.StreamHandler):
    """A StreamHandler that never crashes on flush (e.g. closed stdout)."""

    def flush(self):
        try:
            if self.stream and hasattr(self.stream, "flush"):
                self.stream.flush()
        except Exception:
            pass


class _TextFormatter(logging.Formatter):
    COLOR_CODES = {
        logging.DEBUG: "\033[94m",
        logging.INFO: "\033[92m",
        logging.WARNING: "\033[93m",
        logging.ERROR: "\033[91m",
        logging.CRITICAL: "\033[95m",
    }
    RESET = "\033[0m"

    def __init__(self, use_color: bool):
        super().__init__(fmt=_LINE_FORMAT, datefmt=_DATE_FORMAT)
        self.use_color = use_color

    def format(self, record: logging.LogRecord) -> str:
        message = super().format(record)
        if not self.use_color:
            return message
        color = self.COLOR_CODES.get(record.levelno, "")
        return f"{color}{message}{self.RESET}" if color else message


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def _stdout_formatter() -> logging.Formatter:
    if _env("LOG_FORMAT", "text").lower() == "json":
        return _JsonFormatter()

    if os.getenv("LOG_COLOR") is not None:
        use_color = _truthy("LOG_COLOR", False)
    else:
        # Auto: color for an interactive terminal, plain for containers/pipes.
        use_color = bool(getattr(sys.stdout, "isatty", lambda: False)())

    return _TextFormatter(use_color=use_color)


def _build_handlers(level: int) -> list[logging.Handler]:
    handlers: list[logging.Handler] = []

    # Log text is UTF-8 wherever it goes. A Windows console defaults to
    # a legacy code page, where one "→" in a message becomes a
    # "Logging error" traceback that buries the real lines.
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    stream_handler = _SafeStreamHandler(sys.stdout)
    stream_handler.setLevel(level)
    stream_handler.setFormatter(_stdout_formatter())
    handlers.append(stream_handler)

    if _truthy("LOG_TO_FILE", False):
        logs_dir = _env("LOGS_DIR", "./logs")
        os.makedirs(logs_dir, exist_ok=True)

        file_handler = RotatingFileHandler(
            os.path.join(logs_dir, "app.log"),
            maxBytes=int(_env("LOG_MAX_BYTES", "10000000")),
            backupCount=int(_env("LOG_BACKUP_COUNT", "5")),
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(
            logging.Formatter(fmt=_LINE_FORMAT, datefmt=_DATE_FORMAT)
        )
        handlers.append(file_handler)

    return handlers


class CustomLoggerFactory:
    @staticmethod
    def _resolve_level() -> int:
        name = _env("LOG_LEVEL", "INFO").upper()
        return getattr(logging, name, logging.INFO)

    @staticmethod
    def get_logger(name: str) -> logging.Logger:
        logger = logging.getLogger(name)
        level = CustomLoggerFactory._resolve_level()
        logger.setLevel(level)
        # Keep app logs isolated from uvicorn's root handlers (no duplicates).
        logger.propagate = False

        if getattr(logger, "_configured_by_custom_factory", False):
            return logger

        for handler in list(logger.handlers):
            logger.removeHandler(handler)

        for handler in _build_handlers(level):
            logger.addHandler(handler)

        logger._configured_by_custom_factory = True
        return logger

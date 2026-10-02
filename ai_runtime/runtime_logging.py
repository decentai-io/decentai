import logging
import os


class RuntimeLoggerFactory:
    """Runtime-local logger factory with no backend package dependency."""

    _configured = False

    @classmethod
    def get_logger(cls, name: str) -> logging.Logger:
        if not cls._configured:
            logging.basicConfig(
                level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
                format="%(asctime)s %(levelname)s %(name)s %(message)s",
            )
            cls._configured = True
        return logging.getLogger(name)

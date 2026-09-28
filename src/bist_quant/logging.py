"""Structured (JSON-lines) logging.

Usage::

    from bist_quant.logging import get_logger, log_event
    log = get_logger(__name__)
    log_event(log, "signal_generated", symbol="THYAO", score=86, signal="BUY_CANDIDATE")

Logs are written to stderr so that CLI tables on stdout stay machine-readable.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import UTC, datetime
from typing import Any

_RESERVED = set(vars(logging.makeLogRecord({})).keys()) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(level: str | int | None = None) -> None:
    """Configure the ``bist_quant`` logger hierarchy. Idempotent."""
    level = level or os.environ.get("LOG_LEVEL", "WARNING")
    root = logging.getLogger("bist_quant")
    root.setLevel(level if isinstance(level, int) else level.upper())
    if not any(getattr(h, "_bist_quant", False) for h in root.handlers):
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(JsonFormatter())
        handler._bist_quant = True  # type: ignore[attr-defined]
        root.addHandler(handler)
    root.propagate = False


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name if name.startswith("bist_quant") else f"bist_quant.{name}")


def log_event(logger: logging.Logger, event: str, level: int = logging.INFO, **fields: Any) -> None:
    """Emit one structured event; ``fields`` become top-level JSON keys."""
    safe = {k if k not in _RESERVED else f"{k}_": v for k, v in fields.items()}
    logger.log(level, event, extra=safe)

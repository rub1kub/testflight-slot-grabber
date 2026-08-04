from __future__ import annotations

import gzip
import json
import logging
import os
import shutil
import threading
import time
import uuid
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, Optional

from .models import utc_now


SENSITIVE_KEY_PARTS = ("authorization", "cookie", "password", "secret", "token", "chat_id")
MAX_LOGGED_STRING = 64 * 1024


def _is_sensitive_key(key: object) -> bool:
    normalized = str(key).casefold().replace("-", "_")
    return any(part in normalized for part in SENSITIVE_KEY_PARTS)


def sanitize_for_log(value: object, key: Optional[object] = None) -> object:
    """Recursively redact secrets and bound untrusted text in structured logs."""
    if key is not None and _is_sensitive_key(key):
        return "<redacted>"
    if isinstance(value, dict):
        return {str(item_key): sanitize_for_log(item, item_key) for item_key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [sanitize_for_log(item) for item in value]
    if isinstance(value, bytes):
        return {"type": "bytes", "length": len(value)}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, str) and len(value) > MAX_LOGGED_STRING:
        removed = len(value) - MAX_LOGGED_STRING
        return f"{value[:MAX_LOGGED_STRING]}…<truncated {removed} chars>"
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


class AuditContextFilter(logging.Filter):
    def __init__(self) -> None:
        super().__init__()
        self.session_id = uuid.uuid4().hex
        self._sequence = 0
        self._lock = threading.Lock()

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "audit_sequence"):
            with self._lock:
                self._sequence += 1
                sequence = self._sequence
            record.audit_sequence = sequence
            record.audit_session_id = self.session_id
            record.audit_monotonic_ns = time.monotonic_ns()
            record.audit_time_ns = time.time_ns()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "timestamp": utc_now(),
            "time_ns": getattr(record, "audit_time_ns", time.time_ns()),
            "monotonic_ns": getattr(record, "audit_monotonic_ns", time.monotonic_ns()),
            "session_id": getattr(record, "audit_session_id", None),
            "sequence": getattr(record, "audit_sequence", None),
            "process_id": record.process,
            "thread": record.threadName,
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        event = getattr(record, "event", None)
        fields = getattr(record, "fields", None)
        if event:
            payload["event"] = event
        if isinstance(fields, dict):
            payload.update(sanitize_for_log(fields))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)


def _gzip_namer(name: str) -> str:
    return f"{name}.gz"


def _gzip_rotator(source: str, destination: str) -> None:
    with open(source, "rb") as source_handle, gzip.open(destination, "wb", compresslevel=6) as destination_handle:
        shutil.copyfileobj(source_handle, destination_handle)
    os.remove(source)


def setup_logging(log_dir: Path, verbose: bool = False) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("testflight_grabber")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    logger.filters.clear()
    logger.propagate = False

    audit_filter = AuditContextFilter()
    logger.addFilter(audit_filter)

    json_handler = RotatingFileHandler(
        log_dir / "events.jsonl", maxBytes=25 * 1024 * 1024, backupCount=168, encoding="utf-8"
    )
    json_handler.setLevel(logging.DEBUG)
    json_handler.setFormatter(JsonFormatter())
    json_handler.namer = _gzip_namer
    json_handler.rotator = _gzip_rotator

    human_handler = RotatingFileHandler(
        log_dir / "monitor.log", maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    human_handler.setLevel(logging.INFO)
    human_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))

    stream = logging.StreamHandler()
    stream.setLevel(logging.DEBUG if verbose else logging.INFO)
    stream.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))

    logger.addHandler(json_handler)
    logger.addHandler(human_handler)
    logger.addHandler(stream)
    return logger


def log_event(logger: logging.Logger, level: int, event: str, message: str, **fields: object) -> None:
    logger.log(level, message, extra={"event": event, "fields": fields})


def log_exception(logger: logging.Logger, event: str, message: str, **fields: object) -> None:
    logger.exception(message, extra={"event": event, "fields": fields})

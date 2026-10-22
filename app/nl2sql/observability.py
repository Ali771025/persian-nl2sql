from __future__ import annotations

import contextvars
import json
import logging
import re
import uuid
from datetime import UTC, datetime

_request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


class JsonLogFormatter(logging.Formatter):
    """Minimal structured formatter that never serializes request payloads."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": _request_id.get(),
        }
        for name in ("method", "path", "status_code", "duration_ms"):
            value = getattr(record, name, None)
            if value is not None:
                payload[name] = value
        if record.exc_info:
            payload["exception"] = record.exc_info[0].__name__ if record.exc_info[0] else "Exception"
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging(level: str, *, json_logs: bool) -> None:
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler()
    handler.setFormatter(
        JsonLogFormatter()
        if json_logs
        else logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))


def bind_request_id(value: str | None) -> tuple[str, contextvars.Token[str]]:
    request_id = value if value and _SAFE_REQUEST_ID.fullmatch(value) else uuid.uuid4().hex
    return request_id, _request_id.set(request_id)


def reset_request_id(token: contextvars.Token[str]) -> None:
    _request_id.reset(token)


def current_request_id() -> str:
    return _request_id.get()

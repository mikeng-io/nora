"""JSON-line logging with secret redaction (R-16, spec C.9) and a periodic heartbeat."""

from __future__ import annotations

import asyncio
import json
import logging
import traceback
from pathlib import Path

_STANDARD_ATTRS = set(vars(logging.makeLogRecord({})).keys())


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = record.exc_text or self.formatException(record.exc_info)
        for key, value in record.__dict__.items():
            if key in _STANDARD_ATTRS or key in ("message", "asctime"):
                continue
            payload[key] = value
        return json.dumps(payload, default=str)


class SecretFilter(logging.Filter):
    """Redacts every configured secret from a record's message, args, and exception text."""

    def __init__(self, secrets: list[str]) -> None:
        super().__init__()
        self._secrets = [s for s in secrets if s]

    def _redact(self, text):
        if isinstance(text, str):
            for secret in self._secrets:
                text = text.replace(secret, "***REDACTED***")
            return text
        s = str(text)
        if any(secret in s for secret in self._secrets):
            for secret in self._secrets:
                s = s.replace(secret, "***REDACTED***")
            return s
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        if not self._secrets:
            return True
        record.msg = self._redact(record.msg)
        if isinstance(record.args, dict):
            record.args = {k: self._redact(v) for k, v in record.args.items()}
        elif record.args:
            record.args = tuple(self._redact(a) for a in record.args)
        if record.exc_info:
            text = record.exc_text or "".join(traceback.format_exception(*record.exc_info))
            record.exc_text = self._redact(text)
        for key, value in list(record.__dict__.items()):
            if key in ("msg", "args", "exc_info", "exc_text"):
                continue
            setattr(record, key, self._redact(value))
        return True


def setup_logging(*, level: str, path: Path | str, format: str, secrets: list[str]) -> None:
    # `format` mirrors LoggingConfig.format (spec C.9's config shape) but, like the
    # code this replaced, only one on-disk format exists today (JSONFormatter) — the
    # field is accepted for parity with the config and future use, not read yet.
    del format
    root = logging.getLogger()
    root.setLevel(level)
    for h in root.handlers[:]:
        root.removeHandler(h)
        h.close()
    handler = logging.FileHandler(Path(path).expanduser())
    handler.addFilter(SecretFilter(secrets))
    handler.setFormatter(JSONFormatter())
    root.addHandler(handler)


async def heartbeat(metrics, *, interval: float = 60.0) -> None:
    """Logs ingest counters every `interval` seconds until cancelled."""
    log = logging.getLogger("nora.heartbeat")
    while True:
        await asyncio.sleep(interval)
        log.info(
            "heartbeat",
            extra={
                "messages_total": metrics.get("nora_messages_total"),
                "tags_total": metrics.get("nora_tags_total"),
            },
        )

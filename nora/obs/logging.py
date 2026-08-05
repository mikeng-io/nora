"""structlog-backed JSON-line logging with secret redaction (R-16, spec C.9)
and a periodic heartbeat.

Every log call in this codebase goes through plain stdlib `logging.getLogger`
— there is no `structlog.get_logger()` call site anywhere, and this module
doesn't add one. structlog sits in front of the root logger's handler
instead, via `structlog.stdlib.ProcessorFormatter`'s `foreign_pre_chain`: any
stdlib `LogRecord` — this codebase's own loggers and a third-party library's
alike — is converted into a structlog event dict (adding `logger`, `level`,
`ts`, exception text, redaction) before being rendered to JSON. That
`foreign_pre_chain` list is exactly "the stdlib bridge" a third-party
library's `logging` calls ride into the same JSON-lines stream.

`tag_id` rides `structlog.contextvars`: `bind_tag_id`/`clear_tag_id` set and
clear a context-local value that `merge_contextvars` copies into every log
line emitted while it's bound, crossing `await` boundaries within the same
task without being threaded through every call signature in between.
`nora.engagement.pipeline` binds it for the lifetime of one tag.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import structlog

_Processor = Any  # structlog.typing.Processor isn't exported in every version


def _make_redactor(secrets: list[str]) -> _Processor:
    """Redacts every configured secret from every value in the event dict —
    the message, the formatted exception text, and any extra field a call
    site attaches (`extra={...}`) — generalising to however many secrets
    `setup_logging` is given, same contract as before."""
    active = [s for s in secrets if s]

    def redact(_logger: Any, _method_name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
        if not active:
            return event_dict
        for key, value in list(event_dict.items()):
            text = value if isinstance(value, str) else str(value)
            if any(secret in text for secret in active):
                for secret in active:
                    text = text.replace(secret, "***REDACTED***")
                event_dict[key] = text
        return event_dict

    return redact


def _uppercase_level(_logger: Any, _method_name: str, event_dict: dict[str, Any]) -> dict:
    # structlog.stdlib.add_log_level renders "info"/"error"/...; the old
    # JSONFormatter emitted `record.levelname` ("INFO"/"ERROR"/...) and
    # docs/runbook.md's saved queries were written against that casing.
    if "level" in event_dict:
        event_dict["level"] = str(event_dict["level"]).upper()
    return event_dict


def bind_tag_id(tag_id: str) -> None:
    """Every log line emitted while this is bound carries `tag_id` — cleared
    at the end of the tag by `clear_tag_id`."""
    structlog.contextvars.bind_contextvars(tag_id=tag_id)


def clear_tag_id() -> None:
    structlog.contextvars.clear_contextvars()


def _shared_processors(secrets: list[str]) -> list[_Processor]:
    return [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        _uppercase_level,
        structlog.stdlib.ExtraAdder(),
        structlog.processors.TimeStamper(fmt="%Y-%m-%dT%H:%M:%S", key="ts"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        _make_redactor(secrets),
    ]


def setup_logging(*, level: str, path: Path | str, format: str, secrets: list[str]) -> None:
    # `format` mirrors LoggingConfig.format (spec C.9's config shape). Only one
    # on-disk format exists today (JSON, now rendered by structlog's
    # JSONRenderer), so there is nothing to switch on yet — but an
    # accepted-and-silently-ignored parameter is exactly the shape that
    # invites a bug report ("I set format = 'text' and nothing changed"), so
    # this honours it as a validated precondition instead: an operator who
    # sets anything but "json" gets a loud, immediate error rather than a
    # silent no-op.
    if format != "json":
        raise ValueError(f"unsupported obs.logging.format: {format!r} (only 'json' exists)")

    shared = _shared_processors(secrets)
    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.EventRenamer("message"),
            structlog.processors.JSONRenderer(),
        ],
        foreign_pre_chain=shared,
    )

    root = logging.getLogger()
    root.setLevel(level)
    for h in root.handlers[:]:
        root.removeHandler(h)
        h.close()
    handler = logging.FileHandler(Path(path).expanduser())
    handler.setFormatter(formatter)
    root.addHandler(handler)


async def heartbeat(metrics: Any, *, interval: float = 60.0) -> None:
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

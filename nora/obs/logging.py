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
import re
import sys
from typing import Any

import structlog

_Processor = Any  # structlog.typing.Processor isn't exported in every version


def _make_redactor(secrets: list[str]) -> _Processor:
    """Redacts every configured secret from every value in the event dict —
    the message, the formatted exception text, and any extra field a call
    site attaches (`extra={...}`), for however many secrets `setup_logging`
    is given."""
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


#: OpenTelemetry's SeverityNumber, by stdlib level name.
#:
#: A comparable severity. `level` is a STRING that sorts alphabetically, so
#: "at least a warning" is not a range query. The OTel Logs Data Model defines
#: the answer as a NUMBER in fixed bands: TRACE 1-4, DEBUG 5-8, INFO 9-12,
#: WARN 13-16, ERROR 17-20, FATAL 21-24.
#:
#: Each level emits the BASE of its band: nothing here distinguishes "a
#: slightly worse ERROR", and a higher number would imply a scale we do not
#: have.
#:
#: `level` stays, uppercase: it is what a human greps and what
#: `docs/runbook.md`'s saved queries use; `severity_number` is the field a
#: machine filters on.
SEVERITY_NUMBERS: dict[str, int] = {
    "DEBUG": 5,
    "INFO": 9,
    "WARNING": 13,
    "ERROR": 17,
    "CRITICAL": 21,
}


def _severity_number(_logger: Any, _method_name: str, event_dict: dict[str, Any]) -> dict:
    """`severity_number` beside `level`, when the level is one we map.

    An UNMAPPED level gets no field rather than a guessed one, which would put
    it in a band nobody chose: absent means "not stated", never "zero".
    """
    number = SEVERITY_NUMBERS.get(str(event_dict.get("level", "")).upper())
    if number is not None:
        event_dict["severity_number"] = number
    return event_dict


def _exception_fields(_logger: Any, _method_name: str, event_dict: dict[str, Any]) -> dict:
    """Split the formatted traceback into OTel's three exception fields.

    `format_exc_info` renders one `exception` string — the whole traceback,
    type and message buried in its last line. The OTel exception semantic
    conventions name the three parts `exception.type`, `exception.message` and
    `exception.stacktrace`. The stacktrace is kept WHOLE and the other two are
    derived from its last line, so type and message become field lookups.

    The original `exception` key is left in place, because `docs/runbook.md`'s
    saved greps read it.
    """
    blob = event_dict.get("exception")
    if not isinstance(blob, str) or not blob:
        return event_dict
    event_dict["exception.stacktrace"] = blob
    # The last non-empty line of a traceback is `Type: message`, or a bare
    # `Type` for an exception raised with no arguments. Anything else — a
    # traceback whose formatting we do not recognise — sets neither field
    # rather than half-parsing one.
    last = next((ln for ln in reversed(blob.splitlines()) if ln.strip()), "")
    head, sep, tail = last.partition(":")
    if head and not head.startswith((" ", "\t")):
        event_dict["exception.type"] = head.strip()
        if sep:
            event_dict["exception.message"] = tail.strip()
    return event_dict


def _uppercase_level(_logger: Any, _method_name: str, event_dict: dict[str, Any]) -> dict:
    # structlog.stdlib.add_log_level renders "info"/"error"/...; the output
    # is uppercase ("INFO"/"ERROR"/...) because docs/runbook.md's saved queries
    # use that casing.
    if "level" in event_dict:
        event_dict["level"] = str(event_dict["level"]).upper()
    return event_dict


def bind_tag_id(tag_id: str) -> None:
    """Every log line emitted while this is bound carries `tag_id` — cleared
    at the end of the tag by `clear_tag_id`."""
    structlog.contextvars.bind_contextvars(tag_id=tag_id)


def current_tag_id() -> str:
    """The tag bound by `bind_tag_id` in this context, or `""` when none is.

    The one reader of the binding outside the log pipeline: a tool closure
    runs in a task that copied the tag's context (see
    `nora.agent.tools._log_call`), so this names the turn a tool call ran
    under without the tool taking it as an argument. Empty outside a tag
    (a fire turn or a Dreamer pass binds none)."""
    value = structlog.contextvars.get_contextvars().get("tag_id")
    return value if isinstance(value, str) else ""


def clear_tag_id() -> None:
    structlog.contextvars.clear_contextvars()


def _shared_processors(secrets: list[str]) -> list[_Processor]:
    return [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        _uppercase_level,
        structlog.stdlib.ExtraAdder(),
        # RFC 3339: `ts` is machine-read (a log store windows and sorts on it),
        # so it must carry a UTC offset rather than a naive timestamp.
        # `utc=True` means the value IS UTC, so the literal `Z` in the format
        # is the correct offset marker.
        structlog.processors.TimeStamper(fmt="%Y-%m-%dT%H:%M:%SZ", utc=True, key="ts"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        # AFTER `format_exc_info`, which CREATES the `exception` blob this
        # splits: ordered before it, the key does not exist yet and the three
        # fields are silently never written
        # (`test_the_split_runs_after_the_traceback_is_formatted`).
        #
        # Its position relative to the redactor does not matter: the redactor
        # walks every key, so it rewrites derived fields that carry a secret,
        # and fields copied from an already-redacted blob are clean.
        _exception_fields,
        _severity_number,
        _make_redactor(secrets),
    ]


#: A URL's query string, up to the next space or quote: where a signed link
#: carries its signature and a provider may carry a key.
_QUERY = re.compile(r"(https?://[^\s?\"']+)\?[^\s\"']*")


class _DropUrlQueries(logging.Filter):
    """Removes the query string from every URL `httpx` logs. Its own `HTTP Request: GET <url>`
    line is INFO, on every request any client makes, and a fetched video clip's URL is a signed
    Aliyun OSS link whose `Signature=` grants the clip to anyone holding it. The method, host,
    path and status stay. A filter on the LOGGER, not a handler, so every handler (and pytest's
    capture) gets the redacted record."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(
                _QUERY.sub(r"\1", str(a)) if "?" in str(a) else a for a in record.args
            )
        elif isinstance(record.msg, str):
            record.msg = _QUERY.sub(r"\1", record.msg)
        return True


def drop_http_client_url_queries() -> None:
    """Install `_DropUrlQueries` on the `httpx` logger, once however often it is called."""
    target = logging.getLogger("httpx")
    if not any(isinstance(f, _DropUrlQueries) for f in target.filters):
        target.addFilter(_DropUrlQueries())


def setup_logging(*, level: str, format: str, secrets: list[str]) -> None:
    # `format` mirrors LoggingConfig.format. Only JSON exists, so it is a
    # validated precondition rather than an accepted-and-ignored parameter:
    # anything but "json" raises instead of silently doing nothing.
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
    # STDOUT, not a file: the process runs in a container, so
    # `docker compose logs nora` must show the events, and a file would need
    # its own rotation. Unprompted paths (a participation determination, a
    # proactive decline) have nobody waiting, so the log line IS the evidence.
    #
    # `sys.stdout` explicitly, not the `StreamHandler` default of stderr: these
    # are events, not diagnostics, and the compose `json-file` driver captures
    # both the same way only if you never want to tell them apart.
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root.addHandler(handler)
    drop_http_client_url_queries()


async def heartbeat(metrics: Any, *, interval: float = 60.0) -> None:
    """Ingest counters every `interval` seconds until cancelled — **INFO when
    they MOVED, DEBUG when they did not.**

    "The process is alive" is answered by `/health` (polled by the container
    healthcheck and `deploy/watchdog.sh`), so the log does not restate it every
    minute. "Work happened, and here is how much" is an event and stays at INFO.

    Consequence: an idle-but-alive deployment emits nothing at the default
    level, so the log alone does not distinguish idle from dead; use `/health`
    and the `nora_*` gauges.
    """
    log = logging.getLogger("nora.heartbeat")
    previous: tuple[Any, ...] | None = None
    while True:
        await asyncio.sleep(interval)
        counters = {
            "messages_total": metrics.get("nora_messages_total"),
            "tags_total": metrics.get("nora_tags_total"),
        }
        current = tuple(counters.values())
        # First beat is INFO whatever it says: it is the one that records where
        # the counters STARTED, which is what makes every later delta readable.
        moved = previous is None or current != previous
        previous = current
        log.log(logging.INFO if moved else logging.DEBUG, "heartbeat", extra=counters)

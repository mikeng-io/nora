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
import sys
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


#: OpenTelemetry's SeverityNumber, by stdlib level name.
#:
#: **A severity you can compare.** `level` is a STRING, so "is this at least a
#: warning" is a set-membership test against a vocabulary the reader has to
#: know, and it sorts alphabetically — ERROR before INFO before WARNING. The
#: OTel Logs Data Model exists because every backend needs that comparison, and
#: it defines the answer as a NUMBER in fixed bands: TRACE 1-4, DEBUG 5-8,
#: INFO 9-12, WARN 13-16, ERROR 17-20, FATAL 21-24. The band is what carries
#: the meaning, so a level sitting between two of ours still lands in the right
#: half of a `>= 17` query.
#:
#: We emit the BASE of each band rather than the middle: nothing here
#: distinguishes "a slightly worse ERROR", and picking 17 says exactly that,
#: where 18 would imply a scale we do not have.
#:
#: `level` stays, unchanged and uppercase. It is what a human greps and what
#: `docs/runbook.md`'s saved queries were written against; this is the field a
#: MACHINE filters on, and the two are not in competition.
SEVERITY_NUMBERS: dict[str, int] = {
    "DEBUG": 5,
    "INFO": 9,
    "WARNING": 13,
    "ERROR": 17,
    "CRITICAL": 21,
}


def _severity_number(_logger: Any, _method_name: str, event_dict: dict[str, Any]) -> dict:
    """`severity_number` beside `level`, when the level is one we map.

    An UNMAPPED level gets no field rather than a guessed one — a custom level
    somebody adds is genuinely outside this vocabulary, and inventing a number
    for it would put it in a band nobody chose. Same three-state discipline the
    instruments use: absent means "not stated", never "zero".
    """
    number = SEVERITY_NUMBERS.get(str(event_dict.get("level", "")).upper())
    if number is not None:
        event_dict["severity_number"] = number
    return event_dict


def _exception_fields(_logger: Any, _method_name: str, event_dict: dict[str, Any]) -> dict:
    """Split the formatted traceback into OTel's three exception fields.

    `format_exc_info` renders one `exception` string — the whole traceback,
    type and message buried in its last line. That is readable by a human and
    opaque to everything else: "how many `TimeoutError`s since the deploy" is a
    substring search over multi-line blobs.

    The OTel exception semantic conventions name the three parts —
    `exception.type`, `exception.message`, `exception.stacktrace` — and say an
    unhandled exception SHOULD be recorded at ERROR. The stacktrace is kept
    WHOLE and the other two are derived from its last line, so nothing is lost
    and the two cheap questions become field lookups.

    The original `exception` key is left in place. Removing it would break
    `docs/runbook.md`'s saved greps and every operator reflex built on them,
    for a duplication that costs bytes in a 1.7 MB/day stream.
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
        # so it must carry a UTC offset, not a naive `2026-08-06T17:03:34` whose
        # zone a reader has to assume. `utc=True` (the default, stated) means the
        # value IS UTC, so the literal `Z` in the format is the correct offset
        # marker — second precision is preserved, only the ambiguity is removed.
        structlog.processors.TimeStamper(fmt="%Y-%m-%dT%H:%M:%SZ", utc=True, key="ts"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        # AFTER `format_exc_info`, which is what CREATES the `exception` blob
        # this splits — ordered before it, the key does not exist yet and the
        # three fields are silently never written. That is the ordering with
        # teeth, and `test_the_split_runs_after_the_traceback_is_formatted`
        # holds it.
        #
        # Its position relative to the redactor does NOT matter, and the first
        # version of this comment claimed it did: before, the derived fields
        # carry the secret and the redactor then rewrites them (it walks every
        # key); after, they are copied from an already-redacted blob. Both are
        # safe. The claim was checked by moving it and watching the test stay
        # green, which is how a comment gets to be wrong for a whole review.
        _exception_fields,
        _severity_number,
        _make_redactor(secrets),
    ]


def setup_logging(*, level: str, format: str, secrets: list[str]) -> None:
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
    # STDOUT, not a file (2026-09-09). The `FileHandler` this replaces predated
    # the container by ONE DAY — `nora/obs/logging.py` landed 2026-08-03 and the
    # Dockerfile 2026-08-04 — so a host process's natural sink was never
    # revisited once the process moved into a container. No decision row ever
    # covered it.
    #
    # What it cost, twice. `docker compose logs nora` was empty, which a session
    # investigating the dream tier read as «no dream lines, so nothing ran»; it
    # had run (docs/CONVENTIONS.md keeps that trap). The repo's answer was to
    # document the surprise in three files rather than remove it. And the file
    # was a plain `FileHandler` — no rotation anywhere, no logrotate rule — so
    # it reached 66 MB in five weeks beside the media and database volumes.
    #
    # The unprompted paths are what this hurt most: a tag has a human waiting on
    # it, so a lost line is noticed. A participation determination and a
    # proactive decline have nobody waiting, and the instrument IS the evidence.
    #
    # `sys.stdout` explicitly, not the `StreamHandler` default of stderr: these
    # are events, not diagnostics, and the compose default `json-file` driver
    # captures both the same way only if you never want to tell them apart.
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root.addHandler(handler)


async def heartbeat(metrics: Any, *, interval: float = 60.0) -> None:
    """Ingest counters every `interval` seconds until cancelled — **INFO when
    they MOVED, DEBUG when they did not.**

    It was INFO unconditionally, and that made it 51,964 lines of the
    deployment's 205,099 (25%, measured over 37 days) whose entire content was
    two numbers that had not changed since the previous minute. Together with
    the access log this file's sibling change silences, 78% of the log was
    saying that nothing had happened.

    **The split is between two different claims.** "The process is alive" is
    answered by `/health`, which the container healthcheck already polls and
    `deploy/watchdog.sh` is written against; restating it once a minute in the
    log is a third copy. "Work happened, and here is how much" is a real event,
    and it stays at INFO.

    The known cost, stated rather than discovered later: an idle-but-alive
    deployment now emits nothing at the default level, so the log alone no
    longer distinguishes idle from dead. That distinction was always better
    served by `/health` and by the `nora_*` gauges, and it comes back the
    moment anything scrapes them.
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

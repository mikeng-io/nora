"""Private durable evidence for the existing eval CLI and OTel pipeline."""
from __future__ import annotations

import base64
import contextlib
import contextvars
import dataclasses
import hashlib
import json
import logging
import os
import stat
import subprocess
import threading
import time
import uuid
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from opentelemetry import baggage, context, trace
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor

from nora.obs.logging import SEVERITY_NUMBERS
from nora.obs.tracing import EVAL_EVIDENCE_KEY

_CURRENT: contextvars.ContextVar[RunEvidence | None] = contextvars.ContextVar(
    "eval_evidence", default=None)
_CASE: contextvars.ContextVar[str | None] = contextvars.ContextVar("eval_case", default=None)


class EvidenceError(RuntimeError):
    """A measurement whose evidence could not be kept must not pass."""


def current() -> RunEvidence | None:
    return _CURRENT.get()


def _json_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    if isinstance(value, bytes):
        return {"bytes_base64": base64.b64encode(value).decode(),
                "sha256": hashlib.sha256(value).hexdigest()}
    raise TypeError(f"unserializable eval evidence: {type(value).__name__}")


def fingerprint(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


class RunEvidence(SpanProcessor, logging.Handler):
    """Append/fsync before returning; a started case survives an interrupted run.

    The processor is attached to the SAME provider as the existing in-memory
    evaluator. Context propagation confines records to this run and its cases.
    """

    def __init__(self, *, run_id: str, metadata: Mapping[str, Any], lock_fd: int) -> None:
        logging.Handler.__init__(self)
        self.metadata = dict(metadata)
        self.metadata.update(lock_inode=os.fstat(lock_fd).st_ino, pid=os.getpid())
        root = Path(os.environ.get("NORA_EVAL_EXCHANGES") or (
            Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "nora/evals"))
        root = root.expanduser()
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = root.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise EvidenceError("eval evidence directory must be owned and private (0700)")
        self.path = root / f"{run_id}-{uuid.uuid4().hex[:8]}"
        self.path.mkdir(mode=0o700)
        self._fd = os.open(self.path / "events.jsonl", os.O_CREAT | os.O_EXCL | os.O_WRONLY
                           | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        self._write_lock = threading.RLock()
        self.failure: str | None = None
        self._open = True
        self._active: set[str] = set()
        self.run_id = run_id
        self.exit_code: int | None = None

    def check(self) -> None:
        if self.failure:
            raise EvidenceError(self.failure)

    def record(self, kind: str, **payload: Any) -> None:
        self.check()
        row = {"kind": kind, "run_id": self.run_id, "case_id": _CASE.get(),
               "time_ns": time.time_ns(), **payload}
        span_context = trace.get_current_span().get_span_context()
        if span_context.is_valid:
            row.setdefault("trace_id", format(span_context.trace_id, "032x"))
            row.setdefault("span_id", format(span_context.span_id, "016x"))
        try:
            data = (json.dumps(row, ensure_ascii=False, default=_json_value) + "\n").encode()
            with self._write_lock:
                if not self._open:
                    raise OSError("eval evidence is already closed")
                view = memoryview(data)
                while view:
                    written = os.write(self._fd, view)
                    if not written:
                        raise OSError("short eval evidence write")
                    view = view[written:]
                os.fsync(self._fd)
        except (OSError, ValueError, TypeError) as exc:
            self.failure = f"eval evidence write failed ({type(exc).__name__}); run is invalid"
            raise EvidenceError(self.failure) from exc

    def on_start(self, span: Any, parent_context: Any = None) -> None:
        if current() is self:
            self.record("span_started", **self._span(span))

    def on_end(self, span: ReadableSpan) -> None:
        if current() is self:
            self.record("span_finished", **self._span(span),
                        end_time=span.end_time, status=str(span.status.status_code),
                        events=[{"name": e.name, "timestamp": e.timestamp,
                                 "attributes": dict(e.attributes or {})} for e in span.events])

    @staticmethod
    def _span(span: Any) -> dict[str, Any]:
        ctx = span.get_span_context() if hasattr(span, "get_span_context") else span.context
        return {"name": span.name, "trace_id": format(ctx.trace_id, "032x"),
                "span_id": format(ctx.span_id, "016x"),
                "parent_span_id": format(span.parent.span_id, "016x") if span.parent else None,
                "start_time": span.start_time, "attributes": dict(span.attributes or {})}

    def emit(self, record: logging.LogRecord) -> None:
        if current() is self:
            severity = SEVERITY_NUMBERS.get(record.levelname)
            self.record("log", logger=record.name, level=record.levelname,
                        **({"severity_number": severity} if severity is not None else {}),
                        message=self.format(record))

    def shutdown(self) -> None:
        pass  # provider owns no file; the run context closes it after terminal evidence

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        self.check()
        return True

    @contextlib.contextmanager
    def case(self, name: str, *, input: Any, metadata: Any) -> Iterator[None]:
        case_id = uuid.uuid4().hex
        token = _CASE.set(case_id)
        self._active.add(case_id)
        try:
            self.record("case_started", name=name, input=input, metadata=metadata)
            with trace.get_tracer(__name__).start_as_current_span("eval.case"):
                yield
            self.record("case_finished")
        except BaseException as exc:
            if self.failure is None:
                self.record("case_failed", error_type=type(exc).__name__)
            raise
        finally:
            self._active.discard(case_id)
            _CASE.reset(token)

    def result(self, result: Any) -> None:
        self.record("case_result", result=result)

    @contextlib.contextmanager
    def capture(self, provider: Any) -> Iterator[RunEvidence]:
        token = _CURRENT.set(self)
        capture_token = context.attach(context.set_value(
            EVAL_EVIDENCE_KEY, self.record, baggage.set_baggage("nora.eval.capture", "true")))
        root_logger = logging.getLogger()
        provider.add_span_processor(self)
        root_logger.addHandler(self)
        try:
            self.record("run_started", metadata=self.metadata)
            with trace.get_tracer(__name__).start_as_current_span("eval.run"):
                yield self
            self.check()
            self.record("run_finished", exit_code=self.exit_code, active_cases=sorted(self._active))
        except BaseException as exc:
            if self.failure is None:
                self.record("run_failed", error_type=type(exc).__name__,
                            exit_code=getattr(exc, "code", None), active_cases=sorted(self._active))
            raise
        finally:
            root_logger.removeHandler(self)
            context.detach(capture_token)
            _CURRENT.reset(token)
            self._open = False
            os.close(self._fd)
            # MultiSpanProcessor cannot remove processors. Closed ones remain
            # inert: current() can never select them on a later run.


def revision(root: Path) -> dict[str, Any]:
    def git(*args: str) -> bytes:
        return subprocess.check_output(  # noqa: S603 - fixed read-only git arguments
            ["git", "-C", str(root), *args], stderr=subprocess.DEVNULL)  # noqa: S607
    try:
        return {"head": git("rev-parse", "HEAD").decode().strip(),
                "diff_sha256": hashlib.sha256(git("diff", "HEAD", "--binary")).hexdigest(),
                "lockfile_sha256": fingerprint(root / "uv.lock")}
    except (OSError, subprocess.CalledProcessError):
        return {"head": None, "lockfile_sha256": fingerprint(root / "uv.lock")}

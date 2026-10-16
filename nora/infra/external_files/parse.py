"""Parse a file's bytes into text in a child process that can be killed.

A parse has no time bound of its own (a small crafted PDF can hold a parser
for many seconds), so every parse of fetched bytes happens HERE, never on the
event loop:

- one child process per parse (`python -m nora.infra.external_files.parse`),
  fed the bytes on stdin, answering one JSON object on stdout;
- a wall-clock deadline (`PARSE_DEADLINE_SECONDS`): past it the child is
  killed and the open is `refused` (`TOO_SLOW`);
- an address-space cap (`PARSE_MEMORY_BYTES`, `RLIMIT_AS`) and a CPU-time
  cap set inside the child before it reads a byte; a child that runs out of
  memory is `refused` (`TOO_LARGE`);
- the child gets an EMPTY environment but for `PATH`, `PYTHONPATH` and the
  locale: Nora's secrets (`GOOGLE_OAUTH_*` and every other `*_env` value)
  never reach the process that reads untrusted bytes.

The child keeps nothing: one parse, one answer, exit (never cached).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from nora.domain.external_files import NOT_TEXT, TOO_LARGE, TOO_SLOW, ExternalFileError

log = logging.getLogger("nora.external_files")

__all__ = ["KINDS", "PARSE_DEADLINE_SECONDS", "PARSE_MEMORY_BYTES", "parse_isolated"]

#: How long one parse may take, wall clock, before the child is killed.
PARSE_DEADLINE_SECONDS = 20.0
#: The child's address space. pypdf and the OOXML readers stay far below it
#: on a 10 MB file; a crafted one that would not is stopped by the kernel.
PARSE_MEMORY_BYTES = 1024 * 1024 * 1024
#: The parsers the child knows, by the name the adapter passes.
KINDS = frozenset({"pdf", "docx", "xlsx", "pptx", "plain", "sheets"})
#: The stdout a child may answer: `max_chars` of text, JSON-escaped, is far
#: below it; more is a child misbehaving.
_MAX_ANSWER_BYTES = 64 * 1024 * 1024
_ROOT = str(Path(__file__).resolve().parents[3])


async def parse_isolated(
    kind: str, data: bytes, *, max_bytes: int, max_chars: int,
    titles: list[str] | None = None,
    deadline_seconds: float = PARSE_DEADLINE_SECONDS,
    memory_bytes: int = PARSE_MEMORY_BYTES,
) -> str:
    """`data` read as text by the `kind` parser, in a killable child.

    Raises `ExternalFileError` only: the parser's own refusal, `refused`
    `TOO_SLOW` past the deadline, `refused` `TOO_LARGE` when the child runs out
    of memory, `refused` `NOT_TEXT` for anything else the child did."""
    if kind not in KINDS:
        raise ExternalFileError("refused", NOT_TEXT)
    header = json.dumps({
        "kind": kind, "max_bytes": max_bytes, "max_chars": max_chars,
        "titles": titles or [], "memory_bytes": memory_bytes,
        "cpu_seconds": int(deadline_seconds) + 5,
    }).encode()
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": _ROOT, "LANG": "C.UTF-8"}
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-s", "-m", "nora.infra.external_files.parse",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, env=env, cwd=_ROOT,
        )
    except OSError as exc:
        log.error("external files: the parse worker could not start: %s", type(exc).__name__)
        raise ExternalFileError("unavailable", "the file reader could not start") from exc
    try:
        out, _ = await asyncio.wait_for(
            proc.communicate(header + b"\n" + data), deadline_seconds,
        )
    except TimeoutError:
        log.warning("external files: a parse passed %.0fs and was killed", deadline_seconds)
        raise ExternalFileError("refused", TOO_SLOW) from None
    finally:
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
    return _answer(proc.returncode, out)


def _answer(returncode: int | None, out: bytes) -> str:
    try:
        answer = json.loads(out) if 0 < len(out) <= _MAX_ANSWER_BYTES else None
    except ValueError:
        answer = None
    if not isinstance(answer, dict):
        log.warning("external files: the parse worker exited %s without an answer", returncode)
        raise ExternalFileError("refused", NOT_TEXT)
    text = answer.get("text")
    if isinstance(text, str):
        return text
    status, reason = answer.get("status"), answer.get("reason")
    if status in ("refused", "unavailable", "nothing_found") and isinstance(reason, str):
        raise ExternalFileError(status, reason)
    raise ExternalFileError("refused", NOT_TEXT)


# ---- the child --------------------------------------------------------------

def _limit(memory_bytes: int, cpu_seconds: int) -> None:
    """Caps set before a byte is read. `RLIMIT_AS` is not enforced on macOS
    (a dev machine); the container is Linux, where it is."""
    import resource

    for which, value in ((resource.RLIMIT_AS, memory_bytes), (resource.RLIMIT_CPU, cpu_seconds)):
        with contextlib.suppress(ValueError, OSError):
            resource.setrlimit(which, (value, value))


def _parse(header: dict[str, Any], data: bytes) -> str:
    from . import text

    kind, max_bytes, max_chars = header["kind"], header["max_bytes"], header["max_chars"]
    if kind == "pdf":
        return text.pdf_text(data, max_chars)
    if kind == "docx":
        return text.docx_text(data, max_bytes, max_chars)
    if kind == "xlsx":
        return text.xlsx_text(data, max_bytes, max_chars)
    if kind == "pptx":
        return text.pptx_text(data, max_bytes, max_chars)
    if kind == "plain":
        return text.plain_text(data, max_chars)
    if kind == "sheets":
        # `data` is the provider's batch of value ranges, as it answered.
        try:
            ranges = json.loads(data).get("valueRanges", [])
        except (ValueError, AttributeError):
            raise ExternalFileError("unavailable", "the provider did not answer JSON") from None
        ranges = ranges if isinstance(ranges, list) else []
        tabs = []
        for i, title in enumerate(header["titles"]):
            got = ranges[i] if i < len(ranges) and isinstance(ranges[i], dict) else {}
            rows = got.get("values", [])
            tabs.append((str(title), rows if isinstance(rows, list) else []))
        return text.sheets_text(tabs, max_chars)
    raise ExternalFileError("refused", NOT_TEXT)


def main() -> None:
    raw = sys.stdin.buffer.readline()
    header = json.loads(raw)
    _limit(int(header["memory_bytes"]), int(header["cpu_seconds"]))
    data = sys.stdin.buffer.read(int(header["max_bytes"]) + 1)
    try:
        answer: dict[str, Any] = {"text": _parse(header, data)}
    except ExternalFileError as err:
        answer = {"status": err.status, "reason": err.reason}
    except MemoryError:
        answer = {"status": "refused", "reason": TOO_LARGE}
    except Exception:  # noqa: BLE001 - any parser failure on untrusted bytes is "not text"
        answer = {"status": "refused", "reason": NOT_TEXT}
    sys.stdout.write(json.dumps(answer))
    sys.stdout.flush()


if __name__ == "__main__":
    main()

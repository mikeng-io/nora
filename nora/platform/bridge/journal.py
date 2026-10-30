"""The bridge capture journal: an append-only fsync'd spool of envelopes.

Every bridge writes each accepted upstream transition here BEFORE enqueueing to
Redis, so a bridge restart replays the tail instead of losing or re-deriving
events. Format: one record per line, `{length} {crc32} {json}` — length-prefixed
so a torn tail record (the acknowledged crash window) truncates cleanly on
recover instead of poisoning the spool.

The journal is also the Signal capture preflight: `probe()` must succeed —
create dir, open, append+fsync — BEFORE signal-cli is launched on every restart.
"""

from __future__ import annotations

import json
import logging
import os
import zlib
from pathlib import Path
from typing import Any

log = logging.getLogger("nora.platform.bridge.journal")


class JournalError(RuntimeError):
    """Capture is durably broken — the supervising bridge must stop upstream."""


class Journal:
    def __init__(self, path: Path, *, max_bytes: int = 512 * 1024 * 1024) -> None:
        self._path = path
        self._max = max_bytes

    def probe(self) -> None:
        """Preflight: prove append+fsync works. Called before upstream starts."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise JournalError(f"journal dir cannot be created: {exc}") from exc
        self.append({"probe": True})

    def append(self, record: dict[str, Any]) -> None:
        payload = json.dumps(record, separators=(",", ":")).encode()
        line = (str(len(payload)).encode() + b" "
                + str(zlib.crc32(payload)).encode() + b" " + payload + b"\n")
        try:
            if self._path.exists() and self._path.stat().st_size > self._max:
                raise JournalError(f"journal {self._path} over capacity {self._max}")
            with self._path.open("ab") as fh:
                fh.write(line)
                fh.flush()
                os.fsync(fh.fileno())
        except JournalError:
            raise
        except OSError as exc:
            raise JournalError(f"journal append failed: {exc}") from exc

    def recover(self) -> list[dict[str, Any]]:
        """Read all intact records. Two failure shapes are NOT the same:

        * a TORN TAIL — the last line has no newline, or fails its length/CRC
          check and nothing follows it. That is the acknowledged crash window
          of append+fsync; the bytes after `intact_end` are truncated and
          replay continues from the intact prefix.
        * INTERIOR CORRUPTION — a malformed line with more data after it. A
          crashed writer can only tear at the end, so corruption followed by
          valid bytes is disk/operator damage, not a torn write. Refusing to
          continue is the honest answer: the intact prefix is replayable but
          silently discarding the rest would lose or reorder events, so this
          raises JournalError instead of "recovering" a lie.
        """
        if not self._path.exists():
            return []
        records: list[dict[str, Any]] = []
        with self._path.open("rb") as fh:
            data = fh.read()
        pos = intact_end = 0
        while pos < len(data):
            nl = data.find(b"\n", pos)
            if nl < 0:
                break  # torn tail — unterminated last line
            line = data[pos:nl]
            parts = line.split(b" ", 2)
            try:
                ok = len(parts) == 3 and int(parts[0]) == len(parts[2]) and (
                    int(parts[1]) == zlib.crc32(parts[2]))
            except (ValueError, json.JSONDecodeError):
                ok = False
            if not ok:
                if nl + 1 >= len(data):
                    break  # malformed LAST line — torn tail
                raise JournalError(
                    f"journal {self._path}: interior corruption at byte {pos}; "
                    "refusing to replay past it")
            records.append(json.loads(parts[2]))
            pos = nl + 1
            intact_end = pos
        if intact_end < len(data):
            log.warning("journal %s: truncating torn tail of %d bytes",
                        self._path, len(data) - intact_end)
            with self._path.open("r+b") as fh:
                fh.truncate(intact_end)
        return records

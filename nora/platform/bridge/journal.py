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


def _transition_of(
    record: dict[str, Any],
) -> tuple[str, str, int, str | None] | None:
    """The `(key, state, seq, upstream)` a record's `transition` field
    commits, or None.

    A transition rides ON its envelope's record — one append+fsync persists
    the ledger step and the event atomically, so a crash can never leave a
    committed sequence for an event that was never spooled (the phantom-bump
    window the per-target ledger exists to refuse). `upstream` is the
    proven upstream revision the event published under (`g:{session}:{s}`);
    records predating the field read as `None` and resolve to the `l:`
    identity they were minted with.
    """
    t = record.get("transition")
    if not isinstance(t, dict):
        return None
    key, state, seq = t.get("key"), t.get("state"), t.get("seq")
    if not isinstance(key, str) or not isinstance(state, str):
        return None
    upstream = t.get("upstream")
    try:
        return key, state, int(seq), upstream if isinstance(upstream, str) else None
    except (TypeError, ValueError):
        return None


class Journal:
    def __init__(self, path: Path, *, max_bytes: int = 512 * 1024 * 1024) -> None:
        self._path = path
        self._max = max_bytes
        # The durable transition ledger — `key -> (last_state, seq,
        # upstream)` — rebuilt lazily from records so a restart replays the
        # same answer the process had when it journaled. `None` until first
        # read; every successful `append` keeps it current.
        self._transitions: dict[str, tuple[str, int, str | None]] | None = None

    def probe(self) -> None:
        """Preflight: prove append+fsync works. Called before upstream starts."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise JournalError(
                f"journal dir cannot be created: {type(exc).__name__}") from exc
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
            t = _transition_of(record)
            if t is not None and self._transitions is not None:
                # Cache coherence: the durable step counts only AFTER fsync —
                # a failed append must not advance the sequence a later
                # resolve would hand to a different event.
                self._transitions[t[0]] = (t[1], t[2], t[3])
        except JournalError:
            raise
        except OSError as exc:
            raise JournalError(
                f"journal append failed: {type(exc).__name__}") from exc

    def transition_ledger(self) -> dict[str, tuple[str, int, str | None]]:
        """The clockless-transition ledger:
        `key -> (last_state, seq, upstream_identity)`.

        Rebuilt from the journal's own records on first read — the same
        bytes the publisher replays, so restart recovery and live capture
        answer the same question. Interior corruption raises `JournalError`
        through `recover()`: a ledger that cannot be proven is not a ledger.
        """
        if self._transitions is None:
            states: dict[str, tuple[str, int, str | None]] = {}
            for record in self.recover():
                t = _transition_of(record)
                if t is not None:
                    states[t[0]] = (t[1], t[2], t[3])
            self._transitions = states
        return self._transitions

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

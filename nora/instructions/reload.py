"""Watch a directory of instruction files and keep serving the last good scan.

The reload state machine shared by `nora.doctrine.loader` and the Dreamer's
instruction store. It knows nothing about doctrine: the metric name and the log
subject are the caller's to name.

**The property it exists for is the failure path, not the happy one.** A live
edit that breaks a file must not take the running system's instructions away:
the previous good scan keeps being served, a counter goes up, and the operator
sees one `..._reload_failed` line. But the *first* scan has no previous good
value, so a broken corpus at startup RAISES — a process that starts with no
instructions and says nothing about it is the `unavailable`-reported-as-
`nothing_found` failure, one layer down. That asymmetry is the whole design.

`mtime` vs `startup` is the caller's `reload` string, matching
`[doctrine] reload` — `startup` freezes the corpus after the first successful
scan and is what tests and one-shot tools want; `mtime` re-fingerprints on every
read.
"""

from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Generic, TypeVar

from ..obs.metrics import Metrics
from .frontmatter import texts_under

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Loaded:
    """What one successful scan loaded: the text of every file it matched,
    keyed by path relative to the watched directory, and when (wall clock,
    epoch seconds). Read by `nora.app.revisions` to record each load, so
    "which instructions were in force at T" is answerable.

    Read from disk BESIDE the scan, not by it: `scan` is the caller's and
    returns parsed sections, not bytes. An edit landing between the two
    moves the fingerprint, so the next read rescans and records again.
    """

    generation: int
    loaded_at: float
    #: `None` when a file could not be read beside a scan that succeeded (a
    #: pull landing mid-read): the scan is still served, nothing is counted
    #: as a reload failure, and the next read rescans so the load that IS
    #: recorded has its files.
    files: tuple[tuple[str, str], ...] | None


class DirectoryWatcher(Generic[T]):
    """One directory, one scan function, one last-good value.

    `scan` is the caller's whole parse-and-validate pass, returning whatever the
    caller wants to cache — a tuple of doctrine sections, a mapping of Dreamer
    objectives. It is a plain `__init__` collaborator, not a subclass hook: the
    watcher never inspects the value it holds, which is what lets two callers
    with unrelated record types share one reload machine.

    `failure_counter` is the caller's own Prometheus counter name, so each corpus
    counts its failures separately (the doctrine uses
    `nora_doctrine_reload_failures_total`). `subject` names the corpus in the log
    line for the same reason. Both are required — a shared
    machine that defaulted them would attribute one corpus's failures to
    another.
    """

    def __init__(
        self,
        dir: Path,
        *,
        scan: Callable[[], T],
        metrics: Metrics,
        subject: str,
        failure_counter: str,
        error: type[Exception],
        log: logging.Logger,
        glob: str = "*.md",
        reload: str = "mtime",
    ) -> None:
        self._dir = Path(dir)
        self._scan = scan
        self._metrics = metrics
        self._subject = subject
        self._failure_counter = failure_counter
        self._error = error
        self._log = log
        self._glob = glob
        self._reload = reload
        self._value: T | None = None
        self._loaded = False
        self._fingerprint: tuple[tuple[str, int, int], ...] | None = None
        self._generation = 0
        self._loaded_record: Loaded | None = None
        self.failures = 0

    @property
    def generation(self) -> int:
        """Increments on every SUCCESSFUL scan, and never on a failed one.

        A caller holding derived state (the doctrine's per-audience assembled
        cache) invalidates it by watching this rather than by being called back:
        the value changed iff the generation moved, and a failed reload — which
        deliberately leaves the last good value in place — must not invalidate
        anything.
        """
        return self._generation

    def fingerprint(self) -> tuple[tuple[str, int, int], ...]:
        """Path, mtime and size of every matching file, sorted.

        Size is in there beside mtime because an edit written back at the file's
        original timestamp — a restored backup, `rsync --times`, an editor that
        preserves timestamps — moves no clock at all, and the resulting failure
        is the worst kind: the operator sees their edit on disk while the running
        system serves the old text with nothing logged.

        The full path rather than `p.name`, because `glob` may be nested: the
        Dreamer's tree is `doctrine/*.md` plus `objectives/*.md`, where two
        files can share a basename. For a flat directory the two are the same
        string modulo the prefix.
        """
        return tuple(sorted((str(p), p.stat().st_mtime_ns, p.stat().st_size)
                            for p in self._dir.glob(self._glob)))

    def loaded(self) -> Loaded:
        """The load currently served: rescans first if the directory moved,
        exactly as `current` does."""
        self._maybe_rescan()
        if self._loaded_record is None:  # pragma: no cover - _maybe_rescan raises first
            raise self._error(f"no {self._subject} loaded from {self._dir}")
        return self._loaded_record

    def current(self) -> T:
        """The freshest good scan: rescan if the directory moved, else the cache."""
        self._maybe_rescan()
        if not self._loaded:            # pragma: no cover - _maybe_rescan raises first
            raise self._error(f"no {self._subject} loaded from {self._dir}")
        return self._value              # type: ignore[return-value]

    def _maybe_rescan(self) -> None:
        if self._loaded and self._reload == "startup":
            return
        try:
            fingerprint = self.fingerprint()
        except OSError as exc:                       # directory vanished mid-run
            self._on_failure(exc)
            return
        if fingerprint == self._fingerprint:
            return
        try:
            value = self._scan()
        except self._error as exc:
            # Only the caller's OWN error is tolerated. A `scan` that fails some
            # other way (a bug, a disk error) is not a bad instruction file, and
            # swallowing it into the last-good path would report a broken system
            # as a stale one — the wrong of the two, and unrecoverable because
            # nothing counts it.
            self._on_failure(exc)
            return
        files: tuple[tuple[str, str], ...] | None
        try:
            files = texts_under(self._dir, self._glob)
        except (OSError, UnicodeDecodeError):
            # The scan succeeded on these files a moment ago, so this is the
            # disk moving under us (a pull mid-read), not a bad file: serve the
            # good scan, count nothing, and leave the fingerprint unset so the
            # next read rescans and reads the files again.
            files = None
        self._value = value
        self._loaded = True
        self._generation += 1
        self._fingerprint = fingerprint if files is not None else None
        self._loaded_record = Loaded(
            generation=self._generation, loaded_at=time.time(), files=files,
        )

    def _on_failure(self, exc: Exception) -> None:
        if not self._loaded:
            raise exc if isinstance(exc, self._error) else self._error(str(exc))
        self.failures += 1
        self._metrics.inc(self._failure_counter)
        # Advance the fingerprint so a broken file is not re-parsed on every read.
        with contextlib.suppress(OSError):
            self._fingerprint = self.fingerprint()
        self._log.error(
            "%s_reload_failed: %s (keeping the last good %s)",
            self._subject, exc, self._subject,
        )

"""Record what this process loaded, so "in force at T" is answerable.

At start the process
records its deploy (the baked manifest: version, commit, build time), the
three config files as it read them, and the doctrine and Dreamer corpus it
loaded; after that `watch` records every hot reload of either corpus. The
rows are what `nora snapshot --at T` materialises (`nora.infra.sql.snapshot_at`).

A reload is recorded with the time the watcher LOADED it, not the time this
loop noticed: `DirectoryWatcher.loaded()` rescans when the directory moved
and reports the scan's own time, so the row names the moment the new text
began being served (or, if this poll is what triggered the rescan, the
moment of this poll, which is the same thing).

Never load-bearing: a failed write is logged and counted
(`nora_revision_record_failures_total{kind}`) and the process runs on. What
is lost is that load's row, and a snapshot at a T it would have covered
reports the corpus as the previous load, or unavailable.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

from nora.infra.sql.history import HISTORY_TABLES
from nora.infra.sql.revisions import SqlRevisionStore
from nora.infra.version import DEFAULT_MANIFEST_PATH
from nora.instructions import Loaded
from nora.obs.metrics import Metrics

log = logging.getLogger("nora.app.revisions")

__all__ = ["RevisionRecorder", "read_manifest"]

#: How often `watch` looks for a hot reload. A reload is dated by the
#: watcher's own scan time, so this bounds how late a row is WRITTEN, not how
#: wrong its time is.
WATCH_INTERVAL_SECONDS = 30.0

#: `measure_history` runs on the first tick and then every this many: hourly
#: at the 30-second tick. It scans all of `row_history`, so not every tick.
MEASURE_EVERY_TICKS = 120

#: The `kind` label of `nora_revision_record_failures_total`, derived here
#: and declared from here (`nora.obs.metric_specs` help text names them).
RecordKind = Literal["deploy", "config", "doctrine", "dreamer", "history_size"]


class _Corpus(Protocol):
    def loaded(self) -> Loaded: ...


class _Digested(Protocol):
    @property
    def sha256(self) -> str: ...


class _Doctrine(_Corpus, Protocol):
    def get(self, *, applies_to: str = "tag") -> _Digested: ...


def read_manifest(path: Path) -> dict[str, str | None]:
    """The baked `version.json`'s running version, commit and build time,
    with `manifest_status` saying whether it could be read. An unstamped
    build (every ordinary deploy) has none: `absent`, never a guessed sha."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"manifest_status": "absent", "version": None, "git_sha": None, "built_at": None}
    except (OSError, ValueError):
        return {"manifest_status": "unreadable", "version": None, "git_sha": None, "built_at": None}
    if not isinstance(raw, dict):
        return {"manifest_status": "unreadable", "version": None, "git_sha": None, "built_at": None}

    def text(key: str) -> str | None:
        value = raw.get(key)
        return str(value) if isinstance(value, str) and value else None

    return {
        "manifest_status": "ok", "version": text("version"),
        "git_sha": text("commit"), "built_at": text("built_at"),
    }


class RevisionRecorder:
    """Writes one process's loads through `SqlRevisionStore`.

    Every write that fails is RETRIED on each `watch` tick until it lands:
    the deploy and config rows describe this process's start, which does not
    change, so a database that was briefly down at start costs a late row,
    never a missing one. The config files are read ONCE, at `record_start`
    (they are loaded once at start and an edit without a restart changes
    nothing that runs), and a retry writes what was read then.
    """

    def __init__(
        self, *, store: SqlRevisionStore, doctrine: _Doctrine, dreamer: _Corpus,
        config_files: Sequence[tuple[str, Path]], metrics: Metrics,
        manifest_path: Path = DEFAULT_MANIFEST_PATH, hostname: str | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self._store = store
        self._doctrine = doctrine
        self._dreamer = dreamer
        self._config_files = tuple(config_files)
        self._metrics = metrics
        self._manifest_path = manifest_path
        self._hostname = hostname if hostname is not None else socket.gethostname()
        self._environ = os.environ if environ is None else environ
        self._recorded: dict[str, int] = {}
        self._started_at: datetime | None = None
        self._deploy_pending = False
        #: config file name -> (path, content as read at start), until written
        self._config_pending: dict[str, tuple[Path, str]] = {}
        self._ticks = 0

    async def record_start(self, *, started_at: datetime) -> None:
        """The deploy, each config file, and both corpora as loaded now."""
        self._started_at = started_at
        self._deploy_pending = True
        for name, path in self._config_files:
            try:
                self._config_pending[name] = (path, path.read_text(encoding="utf-8"))
            except OSError:  # an unreadable file at start: nothing to record, said once
                self._failed("config")
        await self.poll()  # writes the deploy and config rows, then the corpora

    async def poll(self) -> None:
        """Retry anything from start not yet written, then record either
        corpus if its load moved since the last record."""
        await self._flush_start()
        await self._record_corpus("doctrine", self._doctrine)
        await self._record_corpus("dreamer", self._dreamer)

    async def watch(self, *, interval_seconds: float = WATCH_INTERVAL_SECONDS) -> None:
        while True:
            await asyncio.sleep(interval_seconds)
            await self.poll()
            self._ticks += 1
            if self._ticks % MEASURE_EVERY_TICKS == 1:
                await self.measure_history()

    async def measure_history(self) -> None:
        """`nora_row_history_rows` / `_bytes` per table: the instrument
        for history growth on the hot tables (DEC-232). One grouped scan of
        `row_history`, hourly."""
        try:
            sizes = await self._store.history_sizes()
        except Exception:  # noqa: BLE001 — never load-bearing: counted, logged, the process runs on
            self._failed("history_size")
            return
        for table in HISTORY_TABLES:
            rows, size = sizes.get(table, (0, 0))
            self._metrics.gauge("nora_row_history_rows", rows, table=table)
            self._metrics.gauge("nora_row_history_bytes", size, table=table)

    def _deploy_identity(self) -> dict[str, str | None]:
        """The manifest when the image was stamped; otherwise the commit and
        build time every build bakes in (`NORA_GIT_SHA`, `NORA_BUILT_AT`,
        Dockerfile). Empty stays None: a row with no identifier is reported
        unavailable by `snapshot_at.InForce.missing`, never guessed."""
        manifest = read_manifest(self._manifest_path)
        return {
            "manifest_status": manifest["manifest_status"],
            "version": manifest["version"],
            "git_sha": manifest["git_sha"] or self._environ.get("NORA_GIT_SHA") or None,
            "built_at": manifest["built_at"] or self._environ.get("NORA_BUILT_AT") or None,
            "image_id": self._environ.get("NORA_IMAGE_ID") or None,
        }

    async def _flush_start(self) -> None:
        if self._started_at is None:
            return
        if self._deploy_pending:
            try:
                ident = self._deploy_identity()
                await self._store.record_deploy(
                    started_at=self._started_at,
                    manifest_status=str(ident["manifest_status"]),
                    version=ident["version"], git_sha=ident["git_sha"],
                    built_at=ident["built_at"], image_id=ident["image_id"],
                    hostname=self._hostname,
                )
                self._deploy_pending = False
            except Exception:  # noqa: BLE001 — never load-bearing: counted, logged, retried next tick
                self._failed("deploy")
        for name, (path, content) in list(self._config_pending.items()):
            try:
                await self._store.record_config(
                    file=name, path=str(path), content=content,
                    loaded_at=self._started_at, hostname=self._hostname,
                )
                del self._config_pending[name]
            except Exception:  # noqa: BLE001 — never load-bearing: counted, logged, retried next tick
                self._failed("config")

    async def _record_corpus(
        self, corpus: Literal["doctrine", "dreamer"], source: _Corpus,
    ) -> None:
        try:
            loaded = source.loaded()
            if self._recorded.get(corpus) == loaded.generation:
                return
            if loaded.files is None:
                # The scan was good but a file could not be read beside it (a
                # pull landing mid-read): the watcher rescans on the next
                # read, and that load is the one recorded.
                return
            assembled = None
            if corpus == "doctrine":
                # The tag audience's digest: what `/health` and every tag
                # trace report as `doctrine_sha256`.
                assembled = self._doctrine.get(applies_to="tag").sha256
                if source.loaded().generation != loaded.generation:
                    return  # reloaded between the two reads; the next poll records it
            await self._store.record_instructions(
                corpus=corpus, files=dict(loaded.files),
                loaded_at=datetime.fromtimestamp(loaded.loaded_at, tz=UTC),
                assembled_sha256=assembled, hostname=self._hostname,
            )
            self._recorded[corpus] = loaded.generation
        except Exception:  # noqa: BLE001 — never load-bearing: counted, logged, retried next tick
            self._failed(corpus)

    def _failed(self, kind: RecordKind) -> None:
        log.exception("revision_record_failed kind=%s", kind)
        self._metrics.inc("nora_revision_record_failures_total", kind=kind)

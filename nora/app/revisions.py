"""Record what this process loaded, so "in force at T" is answerable.

design/cognition-loop/70-migration.md §5, step 1b. At start the process
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
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

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
    """Writes one process's loads through `SqlRevisionStore`."""

    def __init__(
        self, *, store: SqlRevisionStore, doctrine: _Doctrine, dreamer: _Corpus,
        config_files: Sequence[tuple[str, Path]], metrics: Metrics,
        manifest_path: Path = DEFAULT_MANIFEST_PATH, hostname: str | None = None,
        image_id: str | None = None,
    ) -> None:
        self._store = store
        self._doctrine = doctrine
        self._dreamer = dreamer
        self._config_files = tuple(config_files)
        self._metrics = metrics
        self._manifest_path = manifest_path
        self._hostname = hostname if hostname is not None else socket.gethostname()
        if image_id is None:
            image_id = os.environ.get("NORA_IMAGE_ID") or None
        self._image_id = image_id
        self._recorded: dict[str, int] = {}

    async def record_start(self, *, started_at: datetime) -> None:
        """The deploy, each config file, and both corpora as loaded now."""
        try:
            manifest = read_manifest(self._manifest_path)
            await self._store.record_deploy(
                started_at=started_at, manifest_status=str(manifest["manifest_status"]),
                version=manifest["version"], git_sha=manifest["git_sha"],
                built_at=manifest["built_at"], image_id=self._image_id,
                hostname=self._hostname,
            )
        except Exception:  # noqa: BLE001 — never load-bearing: counted, logged, the process runs on
            self._failed("deploy")
        for name, path in self._config_files:
            try:
                await self._store.record_config(
                    file=name, path=str(path), content=path.read_text(encoding="utf-8"),
                    loaded_at=started_at, hostname=self._hostname,
                )
            except Exception:  # noqa: BLE001 — never load-bearing: counted, logged, the process runs on
                self._failed("config")
        await self.poll()

    async def poll(self) -> None:
        """Record either corpus if its load moved since the last record."""
        await self._record_corpus("doctrine", self._doctrine)
        await self._record_corpus("dreamer", self._dreamer)

    async def watch(self, *, interval_seconds: float = WATCH_INTERVAL_SECONDS) -> None:
        while True:
            await asyncio.sleep(interval_seconds)
            await self.poll()

    async def _record_corpus(self, corpus: str, source: _Corpus) -> None:
        try:
            loaded = source.loaded()
            if self._recorded.get(corpus) == loaded.generation:
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
        except Exception:  # noqa: BLE001 — never load-bearing: counted, logged, the process runs on
            self._failed(corpus)

    def _failed(self, kind: str) -> None:
        log.exception("revision_record_failed kind=%s", kind)
        self._metrics.inc("nora_revision_record_failures_total", kind=kind)

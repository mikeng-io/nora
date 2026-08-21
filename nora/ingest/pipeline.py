"""`IngestPipeline` — runs the ambient stages in order (spec §4.3).

A readable table of contents, not a decision-maker: every branch belongs to
the stage that owns it (`nora.ingest.stages.*`). This module only sequences
the four calls and assembles the result — nothing here inspects message
content, catches an exception, or decides anything.

`run` takes an already-normalized `message`, not a raw event: parsing (the
former stage 1) moved onto the adapter itself
(`nora.platform.signal.adapter.SignalAdapter.listen`), which drops anything
that parses to `None` — a DM, a sync message, a self-send, an empty body —
before this pipeline ever sees it. `PlatformAdapter` deliberately has no
`parse()` (`nora.domain.ports`'s module docstring), so `IngestPipeline` is not
the place that could do this parsing generically even if it wanted to. See
Trap B in `docs/superpowers/plans/2026-08-05-nora-phase4-platforms.md`.

`message=None` in the returned `IngestResult` means either that the
conversation is not allowlisted (the scope stage refuses BEFORE the audit, so
a denied room is never recorded — see that module for why the room and the
sender are enforced in different places) or that the audit write failed (the
audit stage drops rather than re-raises). A message
that made it past that is always paired with a `decision`, even for an
unmapped sender (D6) or a policy error (the gate stage degrades closed rather
than propagating).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from nora.domain.models import NormalizedMessage
from nora.domain.ports import (
    BlobStore,
    EngageDecision,
    EngagePolicy,
    MemoryStore,
    RecordStore,
    Transcriber,
)
from nora.ingest.stickers import StickerReader
from nora.persons import Persons
from nora.platform.registry import MediaSourceRegistry
from nora.policy import Policy

from .stages import audit as audit_stage
from .stages import gate as gate_stage
from .stages import identity as identity_stage
from .stages import media as media_stage
from .stages import memory_feed as memory_feed_stage
from .stages import scope as scope_stage

log = logging.getLogger("nora.ingest.pipeline")


@dataclass(frozen=True)
class IngestResult:
    message: NormalizedMessage | None
    decision: EngageDecision | None


class IngestPipeline:
    def __init__(
        self,
        *,
        audit: RecordStore,
        memory: MemoryStore,
        persons: Persons,
        policy: EngagePolicy,
        allowlist: Policy,
        metrics: Any,
        spawn: Callable[[Coroutine[Any, Any, None]], None],
        media_sources: MediaSourceRegistry | None = None,
        blobs: BlobStore | None = None,
        stickers: StickerReader | None = None,
        transcriber: Transcriber | None = None,
        language: str = "",
        max_description_chars: int = 200,
    ) -> None:
        """`media_sources`/`blobs` default to None — media custody OFF — and
        that default is a wiring state, not a design position (DEC-43).

        Both are optional so this constructor stayed compatible with a
        composition root being edited in another worktree on the same day.
        With either absent, `media` counts every attachment it sees as
        `not_configured` and records the message exactly as before; nothing is
        silently skipped. Supplying both is what turns custody on — the
        registry is what then picks the RIGHT fetcher per message, by
        platform — and it is the one line the composition root owes this
        feature.

        `stickers` is `sticker_descriptions`'s wiring state, and it is a
        SEPARATE switch from custody: `None` is the deployment default and
        behaves exactly like custody-without-interpretation (a handle, no
        description), never like a failure. Only stickers are ever read this
        way — see `nora/ingest/stages/media.py`'s docstring for why.

        `transcriber` is `[speech.stt]`'s wiring state, the same shape as
        `stickers`: `None` (deployment default) records a voice note with a
        bare handle, exactly as before speech existed. When supplied, voice
        notes are transcribed inline during custody and their transcript rides
        the body beside the handle. `language` is the BCP-47 hint forwarded to
        the transcriber.
        """
        self._audit = audit
        self._memory = memory
        self._persons = persons
        self._policy = policy
        self._allowlist = allowlist
        self._metrics = metrics
        self._spawn = spawn
        self._media_sources = media_sources
        self._blobs = blobs
        self._stickers = stickers
        self._transcriber = transcriber
        self._language = language
        self._max_description_chars = max_description_chars

    async def run(self, *, message: NormalizedMessage) -> IngestResult:
        """`message` is already normalized — the caller (the adapter's `listen()`
        loop, via `nora.app.dispatch.Dispatcher.on_message`) never hands this a
        `None`. The `nora_messages_total` increment replaces what the deleted
        parse stage used to do right after a successful `adapter.parse()`."""
        m = self._metrics
        m.inc("nora_messages_total", platform=message.platform)

        # The ROOM gate, before anything is written: a conversation the
        # operator has not allowlisted is not recorded at all.
        if not await scope_stage.run(
            message=message, policy=self._allowlist, metrics=m
        ):
            return IngestResult(message=None, decision=None)

        # Identity first, so the resolved person_id lands in the audit INSERT
        # instead of a second write. `Persons.resolve` is two dict lookups on a
        # startup-built frozen mapping — no I/O, so nothing fallible moved
        # ahead of the record.
        person = await identity_stage.run(
            message=message, persons=self._persons, metrics=m
        )
        # Custody BEFORE the record, not after it (A9, DEC-43). This is the one
        # stage that returns a new message: the body gains the durable handle
        # for every attachment whose bytes are now Nora's, so the row and the
        # blob it names are committed by the same INSERT. See that module for
        # why it is inline rather than spawned like `memory_feed`.
        message = await media_stage.run(
            message=message, sources=self._media_sources, blobs=self._blobs, metrics=m,
            stickers=self._stickers, transcriber=self._transcriber,
            language=self._language, max_description_chars=self._max_description_chars,
        )
        recorded = await audit_stage.run(
            message=message, audit=self._audit, metrics=m, person=person
        )
        if not recorded:
            return IngestResult(message=None, decision=None)
        await memory_feed_stage.run(
            message=message, person=person, memory=self._memory, metrics=m, spawn=self._spawn,
        )

        decision = await gate_stage.run(message=message, policy=self._policy, metrics=m)

        # The SENDER gate. Only consulted once something would actually be
        # answered, so an unrecognised sender in an allowed room is still
        # audited — which is exactly what `nora persons pending` reads.
        if decision.engage and not self._allowlist.is_allowed(
            platform=message.platform,
            conversation_id=message.conversation_id,
            person_id=person.person_id if person else None,
        ):
            m.inc("nora_denied_senders_total", platform=message.platform)
            log.warning(
                "tagged by a sender who is not allowed in this conversation "
                "(platform=%s person=%s) — recorded, not answered",
                message.platform,
                person.person_id if person else "<unmapped>",
            )
            decision = EngageDecision(engage=False, reason="sender_not_allowed")

        return IngestResult(message=message, decision=decision)

"""The Dreamer, driven as `dream_loop` drives it.

`nora/app/runtime.py`'s `_run_adaptor` reads a batch with `record.since`, joins
media at `path="dreamer"`, and calls `DreamerRuntime.run`. This does the same
three things in the same order, against the same objects on the same graph, so
a pass measured here is the pass that runs on a fire.

Mirroring rather than importing that function because it is wrapped in the
loop's scheduling, watermark and failure-backoff bookkeeping — a pass driven
through the whole loop would be measuring the clock. What IS mirrored is
everything between the batch and the outcome, which is the part a case is about.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any

from nora.domain.media_join import MediaJoin
from nora.domain.models import TranscriptRow
from nora.infra.sql import create_sql_engine
from nora.infra.sql.notes import SqlNoteStore
from nora.persons import Persons

from .obtain import Subject


@dataclass(frozen=True, slots=True)
class Pass:
    """What one dream pass left behind.

    `status` is the RUNTIME's answer — `ok`, `nothing_found`, `unavailable` —
    and `unavailable` is the fail-closed one: the adaptor could not run. It is
    reported separately from `changed` because a pass that could not run and a
    pass that found nothing are different facts, and `nothing_found` is a
    legitimate verdict a corpus expects.
    """

    status: str
    stop_reason: str | None
    #: The notebook diff, as `("create", title)` / `("update", title)`. What a
    #: case grades on — not `status`, which says only whether the loop completed.
    changed: tuple[tuple[str, str], ...]

    #: What the pass WROTE — `DreamOutcome.written`.
    written: int = 0

    #: Model samples that came back readable, and samples that did not.
    #:
    #: `nora_dream_sample_total{status="ok"|"unavailable"}` — production's own
    #: per-sample counters. They exist here because `status` cannot separate the
    #: two things a caller must never confuse: a pass that ASKED the model and
    #: could not reach it, and a pass that never needed to ask because nothing
    #: was due. Both end as `nothing_found` with an empty diff.
    #:
    #: Without them, a pass with no provider reachable would return
    #: `nothing_found` and NO error, and a case expecting silence would score a
    #: PASS: ERROR-as-PASS.
    samples_ok: int = 0
    samples_failed: int = 0



def with_roster_senders(
    batch: Sequence[TranscriptRow], *, persons: Persons, platform: str,
) -> list[TranscriptRow]:
    """The batch with each member's row carrying a sender id the roster
    resolves, as production's rows do.

    The sandbox seeds known humans with loaded roster accounts. This
    projection also supports generated batches with minted sender ids and a
    copied `person_id`. Production's rows carry the member's real account
    id, and a scope-keyed adaptor names
    its batch through `Persons.name_transcript`, which keys on
    `(platform, sender_id)`, never on `person_id`. Against minted ids that
    projection resolves nobody, so an eval run would render the self-set
    profile name on BOTH arms and measure the old input. This puts back, per
    row, one id the roster maps the row's own `person_id` to — the same
    `platform_ids` pick `nora.subject.knowledge._sender_for` makes for a
    line a case delivers — so the adaptor's projection sees what it sees
    live. In memory only: no adaptor renders a sender id (R-18), and nothing
    is written back to the sandbox.

    Replaced too: a minted id that happens to resolve to SOMEONE ELSE (the
    sandbox's fixed `+852111` for a sender named like its default), so a row
    is never named as another person.

    Left alone: Nora's rows, a row with no `person_id` (unresolved at ingest;
    production resolves at render time against today's roster, so such a row
    may read differently live), a row whose id already resolves to its own
    person, and a person with no id on this platform.
    """
    named: list[TranscriptRow] = []
    for row in batch:
        resolved = persons.resolve(platform, row.sender_id)
        if (
            not row.is_nora and row.person_id
            and (resolved is None or resolved.person_id != row.person_id)
        ):
            ids = persons.platform_ids(platform, row.person_id)
            if ids:
                row = replace(row, sender_id=sorted(ids)[0])
        named.append(row)
    return named


def _samples(subject: Subject, status: str) -> int:
    """`nora_dream_sample_total{status=...}`, summed over adaptors."""
    return int(sum(
        v for key, v in subject.deps.metrics.snapshot().items()
        if key and key[0] == "nora_dream_sample_total"
        and ("status", status) in (key[1] or ())
    ))


def adaptor_names(subject: Subject) -> tuple[str, ...]:
    return tuple(a.name for a in subject.deps.dream_adaptors)


async def _notebook(subject: Subject, workspace: str) -> dict[str, tuple[str, str]]:
    """`note id -> (title, head_revision_id)` for one workspace's standing notes.

    Head revision and not just presence: an UPDATE creates no note, it moves a
    note's head, so a snapshot recording only presence could not tell an update
    from nothing happening at all.

    Keyed by ID and not by TITLE: `NOTE` has no unique constraint on
    `(workspace, title)`, so a case can seed several notes with one title, and a
    title-keyed dict collapses them. A pass that FILED A DUPLICATE instead of
    revising the tracked note would then show up as an `("update", title)`,
    reported as success when filing a duplicate is the failure to catch.
    """
    engine = create_sql_engine(subject.database_url)
    try:
        notes = await SqlNoteStore(engine).standing(unit=workspace)
        return {n.id: (n.title, n.head_revision_id) for n in notes}
    finally:
        await engine.dispose()


#: What an adaptor with NO schedule entry is handed. Production's own
#: fallback for the same case (`nora/app/runtime.py`'s `batch_limit`
#: parameter), and it is reached only by an adaptor the deployment does not
#: schedule — which is itself worth noticing rather than papering over.
_FALLBACK_BATCH = 64

def batch_after(batch: Sequence[Any], after_ms: int | None) -> list[Any]:
    """The rows a production fire whose watermark stood at `after_ms` would
    have read: those strictly after it. `None` keeps the whole window. An
    eval case states `after_ms` (`metadata.batch_after_ms`) when the newest
    `limit` rows at its anchor are wider than the batch production actually
    judged."""
    if after_ms is None:
        return list(batch)
    return [row for row in batch if row.ts > after_ms]


async def dream(
    subject: Subject,
    *,
    adaptor: str,
    room: tuple[str, str] | None = None,
    limit: int | None = None,
    after_ms: int | None = None,
    entity_key: str | None = None,
    after_id: int = 0,
) -> Pass:
    """One adaptor, one room, one pass. Raises `LookupError` for an unknown
    adaptor rather than running nothing and reporting `nothing_found`."""
    runtime = subject.deps.dreamer_runtime
    if runtime is None:
        raise LookupError("no dreamer runtime on the graph — [dreamer] enabled is false")
    chosen = next((a for a in subject.deps.dream_adaptors if a.name == adaptor), None)
    if chosen is None:
        raise LookupError(
            f"no adaptor {adaptor!r} on the graph; built: {adaptor_names(subject)}"
        )

    # THE KEY DEPENDS ON THE ADAPTOR, and the loop resolves it the same way
    # (`nora/app/runtime.py`, `_keys_for`): a `key_kind == "scope"` adaptor
    # is keyed by the ROOM, a `key_kind == "unit"` one by the UNIT.
    #
    # Keying everything by the room would be silently wrong for a unit-keyed
    # adaptor: it reads notes by unit, so a conversation id finds nothing and
    # the pass returns `nothing_found` with an empty diff, which every case
    # expecting `none` would score as a PASS.
    # The ROOM'S unit (`Subject.unit`, resolved from its policy entry) — not
    # `honcho.workspace_id`, which is Honcho's per-deployment workspace and in
    # production is not the unit of any room.
    workspace = subject.unit
    platform, conversation_id = room or subject.room
    key = workspace if chosen.key_kind == "unit" else conversation_id
    if chosen.key_kind == "entity":
        if entity_key is None or entity_key not in tuple(chosen.keys()):
            raise LookupError("entity adaptor requires a known entity_key")
        key = entity_key
    before = await _notebook(subject, workspace)
    ok_before = _samples(subject, "ok")
    failed_before = _samples(subject, "unavailable")

    # The production order, verbatim: read the batch, join media, run.
    # The BATCH is always the room's, whatever the key is: a workspace-keyed
    # adaptor's real input is a TABLE, and the loop hands it an empty batch
    # (`_dream_adaptor_once`: "an adaptor with `uses_watermark = False` gets NO
    # watermark ... and runs on every tick with an empty batch").
    # THE MOMENT'S batch, not the room's oldest. Production reads
    # `since(after_id=<watermark>, limit=batch)` — the unread backlog past the
    # cursor. A sandbox seeded with a room's whole history has no cursor, and
    # `after_id=0` would read the OLDEST rows. The cursor a real deployment
    # would hold at the moment is "everything but the newest `limit` rows has
    # been read", so the backlog is exactly those.
    # THE ADAPTOR'S OWN batch size, asked of the runtime rather than assumed.
    # A hardcoded default here would be a number production does not use.
    # `limit` stays a parameter for a caller that is deliberately probing a
    # smaller window, and `None` means "whatever this adaptor is given".
    if limit is None:
        limit = runtime.batch_limit_for(chosen.name) or _FALLBACK_BATCH
    batch: list = []
    if chosen.key_kind == "entity":
        batch, _cursor = await chosen.evidence.newest(
            key=key, after=after_id, limit=limit,
        )
        batch = list(batch)
    elif chosen.uses_watermark:
        newest = await subject.deps.audit.recent(
            conversation_id=conversation_id, limit=limit,
        )
        batch = await subject.deps.audit.since(
            conversation_id=conversation_id,
            after_id=(newest[0].id - 1) if newest else 0,
            limit=limit,
        )
        batch = batch_after(batch, after_ms)
        batch = with_roster_senders(batch, persons=subject.deps.persons, platform=platform)
    # Joined ONCE here rather than inside the adaptor, which is where
    # `_run_adaptor` does it: every scope-keyed adaptor reads this one batch.
    # Without it an image-only message reaches the Dreamer as an empty body.
    #
    # `path="dreamer_eval"` and not `"dreamer"`: one label per reader is the
    # rule `tests/architecture/test_media_join_reaches_every_transcript.py`
    # holds, so that two readers cannot land in one histogram. Nothing is
    # split by the difference — a `Subject` carries its OWN `Metrics()`, built
    # per case and scraped by nobody — and the distinct label says something
    # true: this read was made by the eval seam.
    join: MediaJoin = subject.deps.media_join
    # Entity evidence is already RoomMessage/Episode/Mention, as in the live
    # entity driver; it is not a room TranscriptRow window.
    if chosen.key_kind != "entity":
        batch = list(await join.enrich(batch, path="dreamer_eval")) if batch else []

    outcome = await runtime.run(adaptor=chosen, key=key, batch=batch)

    after = await _notebook(subject, workspace)
    changed = [("create", title) for nid, (title, _h) in after.items()
               if nid not in before]
    changed += [
        ("update", title) for nid, (title, head) in after.items()
        if nid in before and before[nid][1] != head
    ]
    return Pass(
        status=outcome.status,
        stop_reason=outcome.stop_reason,
        changed=tuple(sorted(changed)),
        written=outcome.written,
        samples_ok=_samples(subject, "ok") - ok_before,
        samples_failed=_samples(subject, "unavailable") - failed_before,
    )

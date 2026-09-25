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

import json
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any

import sqlalchemy as sa

from nora.domain.blocks import speaker_name
from nora.domain.media_join import MediaJoin
from nora.domain.models import TranscriptRow
from nora.domain.note_body import cited_ids_in
from nora.infra.sql import create_sql_engine
from nora.infra.sql.notes import SqlNoteStore
from nora.infra.sql.schema import NOTE_CANDIDATE, REMINDERS
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
    #: The notebook diff, as `("create", title)` / `("update", title)`. This is
    #: what the note-candidates corpus grades on — not `status`, which says only
    #: whether the loop completed.
    changed: tuple[tuple[str, str], ...]

    #: What the pass WROTE — `DreamOutcome.written`. For the proactive adaptor
    #: that is reminder rows, which is its whole output: it does not speak and
    #: it does not touch the notebook, so `changed` is empty for it by
    #: construction and grading it on `changed` would grade every case the same.
    written: int = 0

    #: Model samples that came back readable, and samples that did not.
    #:
    #: `nora_dream_sample_total{status="ok"|"unavailable"}` — production's own
    #: per-sample counters. They exist here because `status` cannot separate the
    #: two things a caller must never confuse: a pass that ASKED the model and
    #: could not reach it, and a pass that never needed to ask because nothing
    #: was due. Both end as `nothing_found` with an empty diff.
    #:
    #: Measured, not assumed: with no provider reachable, four proactive cases
    #: returned `nothing_found` and NO error, and the one expecting silence
    #: scored a PASS. That is ERROR-as-PASS inside the layer built to end it.
    samples_ok: int = 0
    samples_failed: int = 0

    #: Every candidate the pass STAGED, as
    #: `(title, anchor_date, message_ids, status)`.
    #:
    #: The note-candidates corpus asserts `must_cite` against a staged
    #: candidate's `message_ids` and `anchor_date` against its anchor — neither
    #: of which the notebook diff carries, so eight of its cases were asserting
    #: more than anything checked.
    staged: tuple[tuple[str, int, tuple[int, ...], str], ...] = ()

    #: Every staged candidate's BODY, as `(status, body)`, in `staged`'s order.
    #:
    #: A separate field rather than a fifth element of `staged`, whose 4-tuple
    #: shape other readers unpack. The body is what a later reader (a turn,
    #: the curator, the proactive pass) gets instead of the room's messages,
    #: so it is the output a change to the body's demonstration is measured
    #: on: the corpus's `body_names`/`body_mentions` checks and the per-draw
    #: body lengths both read it. `status == "created"` is a body that reached
    #: the notebook, as a new note or an appended revision.
    bodies: tuple[tuple[str, str], ...] = ()

    #: Each batch row's id and the name the model was SHOWN for its sender
    #: (`from=` on the rendered line, `speaker_name(person_name, sender_name)`).
    #: What `body_names` resolves a cited row to, so a case can ask «does the
    #: body name who said this» without writing anybody's name into a fixture.
    speakers: tuple[tuple[int, str], ...] = ()

    #: What the corpus's `body_schema` reading holds a written body against
    #: (design 42 §4.5, §10.1), beside `speakers`, which are the rendered
    #: rows: the roster as `(roster name, person_id)` — every person the
    #: deployment's identity knows, a superset of the room's —, the name
    #: Nora goes by, and the ids the heads this pass REVISED cited before it
    #: (`carried`: read with `nora.domain.note_body.cited_ids_in`, so a
    #: legacy head carries none, and a create carries none at all).
    roster: tuple[tuple[str, str], ...] = ()
    nora_names: tuple[str, ...] = ()
    carried: tuple[int, ...] = ()

    #: Every note the pass created or revised, as `(kind, the note's anchor
    #: date, the ids its NEW head body cites)`, in `changed`'s order. What a
    #: case graded on WHICH DAY a note landed on reads (the note-candidates
    #: corpus's `date_split`): `staged` carries the candidate's own anchor,
    #: which on an update is not the anchor of the note it revised.
    changed_dated: tuple[tuple[str, int | None, tuple[int, ...]], ...] = ()

    #: Why the pass declined BEFORE any model call, from
    #: `nora_proactive_pass_total{reason}` — production's own counter.
    #:
    #: The proactive pass has a deterministic screen ahead of the model
    #: (`nora/language/proactive.py:548`, issue #136), and its reasons are not
    #: the same kind of thing: `unchanged` and `held_quiet` are VERDICTS — the
    #: workspace has not moved, or it is quiet and nothing is owed — while
    #: `no_home_room` is WIRING. All three end as `nothing_found` with an empty
    #: diff, so without this a misconfigured policy scores as "correctly found
    #: nothing" on every case expecting none.
    declined: str = ""

    #: This pass's own NEW reminder rows, as `(note_id, due_at_ms)` — a
    #: before/after diff over `reminders`, the same shape `changed` is over
    #: the notebook. `note_id` is `None` for an UNLINKED row: `remind` lets
    #: `note` be omitted (`nora/agent/tools.py` ~:8898, ~:8959 — an unlinked
    #: proactive row's payload carries only `intent`, no `note_id`/
    #: `note_title` at all), so a row that restates the exact matter under
    #: test WITHOUT naming it is still one of these, not invisible (issue
    #: #158 review round 2 — a first cut here skipped unlinked rows
    #: entirely, which let a second, unlinked reminder about the same thing
    #: pass a note-scoped double-booking guard). `written` alone cannot tell
    #: "wrote nothing about the note under test" from "wrote nothing about a
    #: DIFFERENT note also due this pass" — `evals.backbone` reduces this
    #: (plus each row's own `due_at_ms`) to what a `proactive_decide` case
    #: actually needs graded (issue #158 rework, both rounds).
    written_reminders: tuple[tuple[str | None, int], ...] = ()


def with_roster_senders(
    batch: Sequence[TranscriptRow], *, persons: Persons, platform: str,
) -> list[TranscriptRow]:
    """The batch with each member's row carrying a sender id the roster
    resolves, as production's rows do (design 42 S1a).

    The sandbox MINTS sender ids (`nora.subject.sandbox`, «Sender ids are
    MINTED, never copied») and copies the real `person_id`. Production's
    rows carry the member's real account id, and note-candidates now names
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


def shown_speakers(
    batch: Sequence[TranscriptRow], *, persons: Persons, platform: str,
) -> tuple[tuple[int, str], ...]:
    """Each row's id and the name note-candidates SHOWED for its sender.

    The adaptor names its batch through the roster (design 42 S1a), so the
    `from=` it rendered is `name_transcript`'s projection of the batch it was
    handed, which on this path already carries the roster's sender ids
    (`with_roster_senders`). Without the projection here, `body_names` and
    `body_schema` would compare the model's roster names against profile
    names it was never shown.
    """
    return tuple(
        (int(r.id), speaker_name(r.person_name, r.sender_name))
        for r in persons.name_transcript(batch, platform=platform)
    )


def _declined(subject: Subject) -> str:
    """The reason label `nora_proactive_pass_total` moved to, if it moved."""
    for key, value in subject.deps.metrics.snapshot().items():
        if key and key[0] == "nora_proactive_pass_total" and value:
            return dict(key[1] or ()).get("reason", "")
    return ""


def _samples(subject: Subject, status: str) -> int:
    """`nora_dream_sample_total{status=...}`, summed over adaptors."""
    return int(sum(
        v for key, v in subject.deps.metrics.snapshot().items()
        if key and key[0] == "nora_dream_sample_total"
        and ("status", status) in (key[1] or ())
    ))


def adaptor_names(subject: Subject) -> tuple[str, ...]:
    return tuple(a.name for a in subject.deps.dream_adaptors)


async def _staged(
    subject: Subject, workspace: str,
) -> tuple[
    tuple[tuple[str, int, tuple[int, ...], str], ...],
    tuple[tuple[str, str], ...],
]:
    """The pass's staged create-candidates, read off the table it writes.

    Queried directly because `SqlNoteStore` offers a write
    (`record_create_candidate`) and a status update, and no read — the
    production readers are the adaptor's own SQL. What a case needs is the
    `message_ids` it cited, which is the only place a citation exists, and the
    body it proposed, as `(status, body)` in the same order.
    """
    engine = create_sql_engine(subject.database_url)
    try:
        stmt = sa.select(
            NOTE_CANDIDATE.c.title, NOTE_CANDIDATE.c.anchor_date,
            NOTE_CANDIDATE.c.message_ids, NOTE_CANDIDATE.c.status,
            NOTE_CANDIDATE.c.body,
        ).where(NOTE_CANDIDATE.c.unit == workspace)
        async with engine.begin() as conn:
            rows = (await conn.execute(stmt)).all()
        staged = tuple(
            (str(r.title), int(r.anchor_date or 0),
             tuple(int(i) for i in (r.message_ids or ())), str(r.status))
            for r in rows
        )
        bodies = tuple((str(r.status), str(r.body or "")) for r in rows)
        return staged, bodies
    finally:
        await engine.dispose()


async def _notebook(subject: Subject, workspace: str) -> dict[str, tuple[str, str]]:
    """`note id -> (title, head_revision_id)` for one workspace's standing notes.

    Head revision and not just presence: an UPDATE creates no note, it moves a
    note's head, so a snapshot recording only presence could not tell an update
    from nothing happening at all.

    Keyed by ID and not by TITLE, which is how it was and was wrong: `NOTE` has
    no unique constraint on `(workspace, title)`, three note-candidates cases
    seed a note called "Dinner 19 Sept" into one workspace, and a title-keyed
    dict collapses them. A pass that FILED A DUPLICATE instead of revising the
    tracked note then showed up as `("update", "Dinner 19 Sept")` — reported as
    the success, when filing a duplicate is precisely the failure
    `p5_already_tracked_gains_a_decision` exists to catch.
    """
    engine = create_sql_engine(subject.database_url)
    try:
        notes = await SqlNoteStore(engine).standing(unit=workspace)
        return {n.id: (n.title, n.head_revision_id) for n in notes}
    finally:
        await engine.dispose()


async def _dated(
    subject: Subject, workspace: str, changed: Sequence[tuple[str, str]],
) -> tuple[tuple[str, int | None, tuple[int, ...]], ...]:
    """`(kind, anchor_date, head citations)` per changed note id, read after
    the pass; see `Pass.changed_dated`."""
    engine = create_sql_engine(subject.database_url)
    try:
        store = SqlNoteStore(engine)
        out: list[tuple[str, int | None, tuple[int, ...]]] = []
        for kind, note_id in changed:
            got = await store.read(unit=workspace, note_id=note_id)
            if got is not None:
                note, head = got
                out.append((kind, note.anchor_date, tuple(sorted(cited_ids_in(head.body)))))
        return tuple(out)
    finally:
        await engine.dispose()


async def _head_citations(subject: Subject, workspace: str) -> dict[str, frozenset[int]]:
    """`note id -> the ids its HEAD body cites`, for every standing note,
    read before the pass. Only the heads the pass then REVISES become
    `Pass.carried` (design 42 §4.5: «the ids the head being revised already
    cites»); a create carries none."""
    engine = create_sql_engine(subject.database_url)
    try:
        store = SqlNoteStore(engine)
        cited: dict[str, frozenset[int]] = {}
        for note in await store.standing(unit=workspace):
            read = await store.read(unit=workspace, note_id=note.id)
            cited[note.id] = cited_ids_in(read[1].body) if read is not None else frozenset()
        return cited
    finally:
        await engine.dispose()


async def _reminder_rows(subject: Subject) -> dict[Any, tuple[str | None, int]]:
    """`reminder id -> (note id, due_at in epoch ms)`, for every one of
    NORA's OWN reminder rows in the sandbox's own database.

    `note id` is `None` for an UNLINKED row: `remind` lets `note` be omitted
    (`nora/agent/tools.py` ~:8898, ~:8959), and an unlinked proactive row's
    payload carries only `intent` — no `note_id`, no `note_title`. `None` is
    recorded rather than the row being left out, so a before/after diff
    (`dream`, below) can tell "this pass wrote an unlinked row" from "this
    pass wrote nothing" — the gap a first cut here had (issue #158 review
    round 2): a row without a note dropped out silently, so a SECOND,
    unlinked reminder about the same matter passed a note-scoped
    double-booking guard clean.

    The sandbox database is isolated PER CASE (`nora.subject.sandbox`), so
    every row here is either seeded or this pass's own — a before/after diff
    by id (`dream`'s own use, below) is `_notebook`'s pattern applied to
    `reminders` instead of the notebook.
    """
    engine = create_sql_engine(subject.database_url)
    try:
        stmt = sa.select(
            REMINDERS.c.id, REMINDERS.c.requester_person_id,
            REMINDERS.c.payload, REMINDERS.c.due_at,
        )
        async with engine.begin() as conn:
            rows = (await conn.execute(stmt)).all()
        result: dict[Any, tuple[str | None, int]] = {}
        for r in rows:
            if r.requester_person_id != "nora":
                continue
            payload = r.payload or {}
            if isinstance(payload, str):
                payload = json.loads(payload)
            note_id = payload.get("note_id") if isinstance(payload, dict) else None
            due_ms = int(r.due_at.timestamp() * 1000)
            result[r.id] = (str(note_id) if note_id else None, due_ms)
        return result
    finally:
        await engine.dispose()


#: What an adaptor with NO schedule entry is handed. Production's own
#: fallback for the same case (`nora/app/runtime.py`'s `batch_limit`
#: parameter), and it is reached only by an adaptor the deployment does not
#: schedule — which is itself worth noticing rather than papering over.
_FALLBACK_BATCH = 64

async def dream(
    subject: Subject,
    *,
    adaptor: str,
    room: tuple[str, str] | None = None,
    limit: int | None = None,
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
    # (`nora/app/runtime.py:976`, `_keys_for`): a `key_kind == "scope"` adaptor
    # is keyed by the ROOM, a `key_kind == "unit"` one by the UNIT.
    #
    # Keying everything by the room was silently wrong for the proactive
    # adaptor: it reads `notes.standing(unit=key)` and its home-room check
    # walks that unit's rooms, so a conversation id found no home room and
    # the pass returned before rendering anything. It answered `nothing_found`
    # with an empty diff — which every case expecting `none` scored as a PASS
    # until the decline reason was carried out.
    # The ROOM'S unit (`Subject.unit`, resolved from its policy entry) — not
    # `memory.workspace_id`, which is Honcho's per-deployment workspace and in
    # production is not the unit of any room.
    workspace = subject.unit
    platform, conversation_id = room or subject.room
    key = workspace if chosen.key_kind == "unit" else conversation_id
    before = await _notebook(subject, workspace)
    head_citations = await _head_citations(subject, workspace)
    reminders_before = await _reminder_rows(subject)
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
    # `after_id=0` read the OLDEST rows: on a room with 1,500 rows at the
    # anchor, a batch from the room's first week. The cursor a real deployment
    # would hold at the moment is "everything but the newest `limit` rows has
    # been read", so the backlog is exactly those — found by the corpus
    # conversion, whose every anchor assumed the newest rows.
    # THE ADAPTOR'S OWN batch size, asked of the runtime rather than assumed.
    # A hardcoded default here is a number production does not use: it hands
    # note-candidates 80 and culture 300 (`composition.py:3177`, `:3247`).
    # `limit` stays a parameter for a caller that is deliberately probing a
    # smaller window, and `None` means "whatever this adaptor is given".
    if limit is None:
        limit = runtime.batch_limit_for(chosen.name) or _FALLBACK_BATCH
    batch: list = []
    if chosen.uses_watermark:
        newest = await subject.deps.audit.recent(
            conversation_id=conversation_id, limit=limit,
        )
        batch = await subject.deps.audit.since(
            conversation_id=conversation_id,
            after_id=(newest[0].id - 1) if newest else 0,
            limit=limit,
        )
        batch = with_roster_senders(batch, persons=subject.deps.persons, platform=platform)
    # Joined ONCE here rather than inside the adaptor, which is where
    # `_run_adaptor` does it and for the reason issue #116 gives: every
    # scope-keyed adaptor reads this one batch. Without it an image-only message
    # reaches the Dreamer as an empty body.
    #
    # `path="dreamer_eval"` and not `"dreamer"`: one label per reader is the
    # rule `tests/architecture/test_media_join_reaches_every_transcript.py`
    # holds, so that two readers cannot land in one histogram. Reusing the
    # loop's label was tried and the control refused it, correctly. Nothing is
    # split by the difference — a `Subject` carries its OWN `Metrics()`, built
    # per case and scraped by nobody — and the distinct label says something
    # true: this read was made by the eval seam.
    join: MediaJoin = subject.deps.media_join
    batch = list(await join.enrich(batch, path="dreamer_eval")) if batch else []

    outcome = await runtime.run(adaptor=chosen, key=key, batch=batch)

    after = await _notebook(subject, workspace)
    staged, bodies = await _staged(subject, workspace)
    changed = [("create", title) for nid, (title, _h) in after.items()
               if nid not in before]
    changed += [
        ("update", title) for nid, (title, head) in after.items()
        if nid in before and before[nid][1] != head
    ]
    changed_dated = await _dated(subject, workspace, [
        ("create" if nid not in before else "update", nid)
        for nid, (_title, head) in after.items()
        if nid not in before or before[nid][1] != head
    ])
    reminders_after = await _reminder_rows(subject)
    written_reminders = tuple(
        v for rid, v in reminders_after.items() if rid not in reminders_before
    )
    return Pass(
        status=outcome.status,
        stop_reason=outcome.stop_reason,
        changed=tuple(sorted(changed)),
        written=outcome.written,
        samples_ok=_samples(subject, "ok") - ok_before,
        samples_failed=_samples(subject, "unavailable") - failed_before,
        declined=_declined(subject),
        staged=staged,
        written_reminders=written_reminders,
        bodies=bodies,
        speakers=shown_speakers(batch, persons=subject.deps.persons, platform=platform),
        roster=tuple(
            (ref.name, person_id)
            for person_id, ref in sorted(subject.deps.persons.by_person.items())
        ),
        nora_names=(subject.cfg.doctrine.display_name,),
        changed_dated=changed_dated,
        carried=tuple(sorted({
            i for nid, (_title, head) in after.items()
            if nid in before and before[nid][1] != head
            for i in head_citations.get(nid, frozenset())
        })),
    )

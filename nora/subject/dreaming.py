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

from dataclasses import dataclass

import sqlalchemy as sa

from nora.domain.media_join import MediaJoin
from nora.infra.sql import create_sql_engine
from nora.infra.sql.notes import SqlNoteStore
from nora.infra.sql.schema import NOTE_CANDIDATE

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
) -> tuple[tuple[str, int, tuple[int, ...], str], ...]:
    """The pass's staged create-candidates, read off the table it writes.

    Queried directly because `SqlNoteStore` offers a write
    (`record_create_candidate`) and a status update, and no read — the
    production readers are the adaptor's own SQL. What a case needs is the
    `message_ids` it cited, which is the only place a citation exists.
    """
    engine = create_sql_engine(subject.database_url)
    try:
        stmt = sa.select(
            NOTE_CANDIDATE.c.title, NOTE_CANDIDATE.c.anchor_date,
            NOTE_CANDIDATE.c.message_ids, NOTE_CANDIDATE.c.status,
        ).where(NOTE_CANDIDATE.c.workspace == workspace)
        async with engine.begin() as conn:
            rows = (await conn.execute(stmt)).all()
        return tuple(
            (str(r.title), int(r.anchor_date or 0),
             tuple(int(i) for i in (r.message_ids or ())), str(r.status))
            for r in rows
        )
    finally:
        await engine.dispose()


async def _notebook(subject: Subject, workspace: str) -> dict[str, str]:
    """`title -> head_revision_id` for the standing notes of one workspace.

    Head revision and not just presence: an UPDATE creates no note, it moves a
    note's head, so a snapshot that recorded only titles could not tell an
    update from nothing happening at all.
    """
    engine = create_sql_engine(subject.database_url)
    try:
        notes = await SqlNoteStore(engine).standing(workspace=workspace)
        return {n.title: n.head_revision_id for n in notes}
    finally:
        await engine.dispose()


async def dream(
    subject: Subject,
    *,
    adaptor: str,
    room: tuple[str, str] | None = None,
    limit: int = 64,
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
    # (`nora/app/runtime.py:965`, `_keys_for`): a `key_kind == "scope"` adaptor
    # is keyed by the ROOM, a `key_kind == "workspace"` one by the WORKSPACE.
    #
    # Keying everything by the room was silently wrong for the proactive
    # adaptor: it reads `notes.standing(workspace=key)` and its home-room check
    # walks that workspace's rooms, so a conversation id found no home room and
    # the pass returned before rendering anything. It answered `nothing_found`
    # with an empty diff — which every case expecting `none` scored as a PASS
    # until the decline reason was carried out.
    workspace = subject.cfg.memory.workspace_id
    _, conversation_id = room or subject.room
    key = workspace if chosen.key_kind == "workspace" else conversation_id
    before = await _notebook(subject, workspace)
    ok_before = _samples(subject, "ok")
    failed_before = _samples(subject, "unavailable")

    # The production order, verbatim: read the batch, join media, run.
    # The BATCH is always the room's, whatever the key is: a workspace-keyed
    # adaptor's real input is a TABLE, and the loop hands it an empty batch
    # (`_dream_adaptor_once`: "an adaptor with `uses_watermark = False` gets NO
    # watermark ... and runs on every tick with an empty batch").
    batch = (
        []
        if not chosen.uses_watermark
        else await subject.deps.audit.since(
            conversation_id=conversation_id, after_id=0, limit=limit,
        )
    )
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
    staged = await _staged(subject, workspace)
    changed = [("create", t) for t in after if t not in before]
    changed += [
        ("update", t) for t, head in after.items()
        if t in before and before[t] != head
    ]
    return Pass(
        status=outcome.status,
        stop_reason=outcome.stop_reason,
        changed=tuple(sorted(changed)),
        written=outcome.written,
        samples_ok=_samples(subject, "ok") - ok_before,
        samples_failed=_samples(subject, "unavailable") - failed_before,
        declined=_declined(subject),
        staged=staged,
    )

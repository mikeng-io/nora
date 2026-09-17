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

from nora.domain.media_join import MediaJoin
from nora.infra.sql import create_sql_engine
from nora.infra.sql.notes import SqlNoteStore

from .knowledge import DEFAULT_ROOM
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


def _samples(subject: Subject, status: str) -> int:
    """`nora_dream_sample_total{status=...}`, summed over adaptors."""
    return int(sum(
        v for key, v in subject.deps.metrics.snapshot().items()
        if key and key[0] == "nora_dream_sample_total"
        and ("status", status) in (key[1] or ())
    ))


def adaptor_names(subject: Subject) -> tuple[str, ...]:
    return tuple(a.name for a in subject.deps.dream_adaptors)


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
    room: tuple[str, str] = DEFAULT_ROOM,
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

    _, key = room
    workspace = subject.cfg.memory.workspace_id
    before = await _notebook(subject, workspace)
    ok_before = _samples(subject, "ok")
    failed_before = _samples(subject, "unavailable")

    # The production order, verbatim: read the batch, join media, run.
    batch = await subject.deps.audit.since(
        conversation_id=key, after_id=0, limit=limit,
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
    batch = list(await join.enrich(batch, path="dreamer_eval"))

    outcome = await runtime.run(adaptor=chosen, key=key, batch=batch)

    after = await _notebook(subject, workspace)
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
    )

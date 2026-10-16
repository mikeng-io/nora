"""The entry-point port: where a case's input enters Nora.

This is a PORT rather than a taxonomy; two implementations run through it:

    gate           `deps.relevance_gate.decide`   — a real component, no turn
    participation  `deps.fire_path.speak`         — a whole turn, no gate

Two entry points chosen for being maximally unalike. They share a lifecycle
(obtain → know → deliver → read) and one `Outcome`, so the port holds.

Both are fields on `Dependencies` (`relevance_gate`, `fire_path`), like every
other entry point (`dispatcher`, `engagement`, `participation_trigger`,
`dreamer_runtime`), so a case drives the deployed object rather than a second
assembly of it.

The gate is NOT run on the participation path, deliberately: *"a suite that had
to get past a live gate call first would be measuring the gate's verdict and the
turn's behaviour in one number"*. `speak` is called directly, exactly as the trigger calls it
once the gate has admitted.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal
from zoneinfo import ZoneInfo

from nora.app.fire import SENT, SILENT, participation_occasion
from nora.domain.standing import WorkingSetItem
from nora.subject import (
    Subject,
    act,
    arrive,
    deliver,
    dream,
    heard,
    last_row_id,
    owe,
    settle,
    silent_failures,
    watch,
)
from nora.subject.standing_fold import fold, read_fold_turn

from .domain import Case, Outcome, WorkingSetFinding
from .fold_evidence import render_evidence, render_shown


async def gate(subject: Subject, case: Case, *, now_ms: int) -> Outcome:
    """`RelevanceGate.decide` on the window the case laid down.

    There is no `input` to deliver: the gate's input IS the window, which
    `know()` has already written into the same `RecordStore` the deployed gate
    reads. That is the whole of the setup, and it is why this suite is the
    cleanest instrument in the repo.
    """
    g = subject.deps.relevance_gate
    if g is None:
        return Outcome(
            admitted=None,
            error="no gate on the graph — [relevance_gate] enabled is false",
        )
    platform, conversation_id = subject.room
    verdict = await g.decide(platform=platform, conversation_id=conversation_id)
    # `source` decides whether this is a READING or an ERROR. The gate never
    # raises: a timeout, a provider refusal and an unparseable body all come
    # back as `no_go`, which is the same token a correct refusal produces.
    # Without this branch every safety case would hold and every capability
    # read "dead" while no call succeeded (ERROR scored as PASS).
    if verdict.source == "fail_closed":
        return Outcome(
            admitted=None,
            source=verdict.source,
            error=f"gate failed closed: {verdict.error_kind}",
        )
    return Outcome(admitted=verdict.admits, source=verdict.source)


async def participation(subject: Subject, case: Case, *, now_ms: int) -> Outcome:
    """One participation turn, graded on what she WROTE for the room.

    The observable is the audit row, not the wire. `nora/agent/guards.py:528`
    appends her reply RECORD-FIRST — before any send, with
    `delivery_status=PENDING` — and a send that raises is caught. So with no
    platform daemon in front of a case the row is still exactly what production
    would have written, and `delivery` reports what the transport then did with
    it. Carried, never collapsed: *she said nothing* and *the wire refused what
    she said* are different facts, and a suite that maps the second onto the
    first grades a healthy Nora as silent. A well-formed Signal send lands in
    the sandbox (`nora.subject.sandbox_signal`), so `delivery` is `sent` as in
    production, and `failed` means a real refusal above the wire.
    """
    path = subject.deps.fire_path
    if path is None:
        return Outcome(admitted=None, error="no fire path on the graph")
    platform, conversation_id = subject.room

    # The judged line: UNTAGGED by construction, which is what makes it a
    # participation candidate rather than a tag. Stamped at `now_ms`, which
    # `know()` laid the window down to end one minute before, so it is strictly
    # the newest row `recent`'s `(ts, id)` sort sees.
    await arrive(subject, case.input, now_ms=now_ms, person=case.person)
    watermark = await last_row_id(subject)

    token = await path.speak(
        participation_occasion(platform=platform, conversation_id=conversation_id)
    )

    hers = await heard(subject, after_id=watermark)
    # THE SAME FAIL-CLOSED RULE THE GATE HAS.
    #
    # `speak` answers one of five tokens, and only `sent` and `silent` are
    # decisions. The three `failed_*` tokens are a turn that never happened —
    # the model was unreachable, the call timed out, a screen refused — and
    # every one of them leaves NO ROW, which reads as an empty `said`, which
    # grades as SILENCE.
    #
    # So without this branch an unreachable provider makes every `expect:
    # silence` case pass. That is ERROR scored as PASS, on the half of the
    # corpus the knowledge-cut twins live in: the twin that must fall silent
    # would "hold" while nothing ran at all.
    if token not in (SENT, SILENT):
        return Outcome(
            admitted=None,
            source="fail_closed",
            error=f"the turn did not run: {token}",
            outcome_token=token,
        )
    return Outcome(
        admitted=None,
        said=tuple(body for body, _ in hers),
        outcome_token=token,
        delivery=",".join(sorted({status for _, status in hers})),
    )


def silently_failed(counted: float, *, said: tuple[str, ...]) -> bool:
    """Did the turn fail silently — as opposed to replying, or declining?

    `nora_silent_failures_total` answers *"she did not speak and it was not a
    choice"*, so it only says that when she did not speak. A turn that DID
    reply and then had a SECOND delivery refused by a guard moves the counter
    too, and reading that as a failed turn makes a real reply an ERROR — the
    opposite mistake to the one the counter is here for, and just as wrong.

    A function rather than an inline `and` so the rule can be pinned without a
    subject: both directions matter and only one of them is obvious.
    """
    return bool(counted) and not said


async def tag(subject: Subject, case: Case, *, now_ms: int) -> Outcome:
    """`Dispatcher.on_message` — the deployment's own inbound path.

    This is the entry point a platform adapter's `listen()` loop calls, with an
    already-parsed message. Nothing stands between it and the turn: ingest
    decides admission, and an admitted message becomes an `EngagementRequest`
    the background tasks run.

    **It replaces a reconstruction.** Eight corpora — the six adversarial files
    plus `trigger`/`no_trigger` — were driven by `evals/run.py`'s
    `_invoke_agent`, a bare Strands `Agent` whose own docstring says what it is:

      > **NOT NORA.** [...] What this therefore does NOT have — every one of
      > these is part of Nora: `build_prefill` and every `ContextBlock`, the
      > framing every block carries in its own opening tag [...] `<standing>` /
      > `<notes>` / `<self_card>` / `<peer_card>`, the real tool registry,
      > `ReplyGuard` / `LeakGuard` / `Deliverer`.

    Every one of those is present here, because none of them is passed in.

    The turn is SPAWNED, not awaited — a listen loop must not block on one
    room — so `settle()` waits for what this path fired. A turn that does not
    settle is an ERROR, never a silence: see `participation` above for why that
    distinction is the whole discipline.
    """
    # The case's input VERBATIM. Nothing is prepended and no trigger is
    # inserted: whether the message engages her is exactly the deployment's
    # decision, made by `nora/tag.py` inside ingest, and it is the whole subject
    # of two of the corpora that land here. `no_trigger.yaml`'s twelve cases are
    # near-misses of a specific string — a helpfully-added trigger would turn
    # every one of them into a hit and the suite would report a perfect score
    # for a rule it had stopped testing.
    body = case.input
    # BEFORE delivering, and nothing is written here: ingest's audit stage
    # appends the inbound row itself, so pre-writing it would put the message in
    # the transcript twice.
    watermark = await last_row_id(subject)
    failures_before = silent_failures(subject)
    # BEFORE the line arrives: what this case fired is every task spawned from
    # here on, never what the graph already carried (the compute poller, a
    # pre-flight still reading).
    spawned = watch(subject)
    await deliver(subject, body, now_ms=now_ms, person=case.person, mention=case.mention)
    if not await settle(subject, spawned=spawned):
        return Outcome(
            admitted=None,
            source="fail_closed",
            error="the turn never settled within the timeout",
        )

    # THE FAIL-CLOSED RULE, and this path has no other signal for it.
    #
    # `Dispatcher.on_message` returns `None` and hands the turn to a background
    # task, so unlike the gate (`source`) and the turn (`speak`'s token) there
    # is nothing here to read. With no provider reachable it would return
    # `error=None, admitted=False, said=()` — a verdict from a run that reached
    # no model. Every corpus that lands here expects a REPLY, so that would
    # fail (or, with an expectation flipped, pass) every case for a reason no
    # number shows.
    #
    # `nora_silent_failures_total` is production's own counter for "she did not
    # speak and it was not a choice". Reading the deployment's instrumentation
    # rather than inventing a signal is the point: the thing that tells an
    # operator the turn broke is the thing that tells the suite.
    failed = silent_failures(subject) - failures_before
    hers = await heard(subject, after_id=watermark)
    if silently_failed(failed, said=tuple(b for b, _ in hers)):
        return Outcome(
            admitted=None,
            source="fail_closed",
            error=f"the turn failed silently ({failed:g} counted)",
        )
    return Outcome(
        admitted=bool(hers),
        said=tuple(b for b, _ in hers),
        delivery=",".join(sorted({st for _, st in hers})),
    )


async def _cognition_probe(subject: Subject, case: Case, *, now_ms: int) -> Outcome:
    """One Cognition session over the case's agenda-item sentence, graded on
    the tools it CALLED (`ToolRouteGrader`, from the tool spans).

    ERROR, never a pass, when the subject built no session runner: a
    deployment whose `[cognition]` is off has no session to measure, and a
    case that reached no model has measured nothing whatever it
    prints (`nora.app.cognition_wiring`: off builds nothing)."""
    runner = getattr(subject.deps, "cognition_runner", None)
    if runner is None:
        return Outcome(
            admitted=None, source="fail_closed",
            error="the subject built no Cognition session runner "
                  "([cognition] is off in the eval config)",
        )
    try:
        await runner.probe(unit=subject.unit, summary=case.input, now_ms=now_ms)
    except Exception as exc:  # noqa: BLE001 -- a session that raised measured nothing
        return Outcome(
            admitted=None, source="fail_closed", error=f"{type(exc).__name__}: {exc}",
        )
    return Outcome(admitted=None)


async def dreamer(subject: Subject, case: Case, *, now_ms: int) -> Outcome:
    """One dream pass, graded on the NOTEBOOK.

    The batch is what `know()` wrote — the Dreamer reads the room's rows, so a
    case's transcript IS its input here, exactly as it is for the gate. There
    is nothing to deliver.

    `case.category` names the adaptor, because which adaptor runs is a property
    of the CASE and not of the runner: adaptors are passes over one runtime, and branching on
    them in the runner would grow a driver per adaptor inside one file.
    """
    # `build_dependencies` takes a `clock` and `SubjectSpec.now` sets it, so a
    # case that states the time is judged AT that time.
    # AN INPUT THE GRAPH CANNOT HONOUR IS AN ERROR.
    #
    # `nora.subject.Knowledge` is single-room — one transcript, one standing,
    # one `DEFAULT_ROOM`. A case whose metadata carries `row` states an input
    # that cannot be seeded, so it is refused rather than run: with that input
    # unseeded the pass finds nothing due, writes nothing, and returns
    # `nothing_found` with no error, so every case expecting `none` PASSES
    # having measured an empty workspace. `rooms` and `standing` are seedable
    # (`knows`).
    unseeded = [k for k in ("row",) if case.metadata.get(k)]
    if unseeded:
        return Outcome(
            admitted=None,
            source="fail_closed",
            error=(
                f"the case states {unseeded} and `Knowledge` is single-room; "
                "multi-room seeding does not exist yet"
            ),
        )
    adaptor = case.category or "culture"
    if adaptor == "cognition":
        return await _cognition_probe(subject, case, now_ms=now_ms)
    try:
        after = case.metadata.get("batch_after_ms")
        result = await dream(
            subject, adaptor=adaptor,
            after_ms=int(after) if after is not None else None,
        )
    except LookupError as exc:
        return Outcome(admitted=None, source="fail_closed", error=str(exc))

    # THE FAIL-CLOSED RULE, and `status` alone cannot carry it.
    #
    # `unavailable` is one half: the adaptor could not run, and an un-run pass
    # produces an empty notebook diff byte-identically to a pass that correctly
    # found nothing.
    #
    # The other half is `nothing_found` after a model call that failed: with no
    # provider reachable a pass comes back `nothing_found` with NO error, and a
    # case expecting silence would PASS.
    #
    # `samples_failed` is production's own per-sample counter
    # (`nora_dream_sample_total{status="unavailable"}`), which is the only
    # thing that separates "asked the model and could not reach it" from "never
    # needed to ask, because nothing was due". The second is a real verdict a
    # corpus expects; the first is not a verdict at all.
    # `samples_failed and not samples_ok`, NOT `or samples_failed`: the
    # runtime's own rule is that a pass is `unavailable` only when NONE was
    # readable, because one good read is a read. Erroring on one bad sample of
    # three would make this layer stricter than the thing it measures.
    if result.status == "unavailable" or (
        result.samples_failed and not result.samples_ok
    ):
        return Outcome(
            admitted=None,
            source="fail_closed",
            error=(
                f"the pass did not complete: status={result.status}, "
                f"{result.samples_failed} sample(s) unreachable, "
                f"stop_reason={result.stop_reason}"
            ),
            outcome_token=result.status,
        )
    return Outcome(
        admitted=None,
        changed=result.changed,
        written=result.written,
        outcome_token=result.status,
    )


async def proactive_act(subject: Subject, case: Case, *, now_ms: int) -> Outcome:
    """The ACT turn — `FirePath.run` on a due `kind="proactive"` row.

    NOT a dream pass, and that distinction is the corpus's own: the DECIDE pass
    writes a reminder row, and this is what happens when that row comes due. It
    is a fire turn, which is why `FirePath.run` is the driver.

    The screen runs first, as the heartbeat runs it. A row it refuses is NOT a
    failure: the room lost its `proactive` trust, the note was retired, or the
    note moved on the tag path since the decision was linked. That is the
    system working, and it is reported as its own fact so a corpus can tell it
    from "she got the floor and declined it".
    """
    row = case.metadata.get("row") or {}
    if not row.get("note"):
        return Outcome(
            admitted=None, source="fail_closed",
            error="an act case must state the row it fires on (`row.note`)",
        )
    watermark = await last_row_id(subject)
    try:
        await owe(
            subject,
            note_title=str(row["note"]),
            intent=str(row.get("intent", "")),
            decided_hours_ago=float(row.get("decided_hours_ago", 6)),
            note_moved=bool(case.metadata.get("note_moved")),
        )
        result = await act(subject, after_id=watermark)
    except LookupError as exc:
        return Outcome(admitted=None, source="fail_closed", error=str(exc))

    if result.screened is not None:
        # A REFUSAL — and the two kinds must not collapse.
        #
        # `cancelled_*` is the system working: the note was retired, or the tag
        # path overtook the decision since the row was linked. That is a real
        # verdict and `a02_note_moved_on_the_tag_path` is a case about exactly
        # it, so it grades as silence.
        #
        # `failed_*` is WIRING: the room is not proactive-trusted, no note
        # store, a workspace the note cannot be reached from. Those are ERROR.
        # They produce an identical empty `said`, so without this split every
        # `silence` case passes on a misconfigured policy.
        if result.screened.startswith("failed_"):
            return Outcome(
                admitted=None, source="fail_closed",
                error=f"the row was refused for a wiring reason: {result.screened}",
                outcome_token=f"screened:{result.screened}",
            )
        return Outcome(
            admitted=False, said=(), outcome_token=f"screened:{result.screened}",
        )
    # THE SAME FAIL-CLOSED RULE the participation turn has, and for the same
    # reason: `failed_*` is a turn that never happened and leaves no row, which
    # reads as silence.
    if result.token not in (SENT, SILENT):
        return Outcome(
            admitted=None, source="fail_closed",
            error=f"the act turn did not run: {result.token}",
            outcome_token=result.token,
        )
    return Outcome(
        admitted=True, said=result.said,
        outcome_token=result.token, delivery=result.delivery,
    )


def _outcome_working_set(items: tuple[WorkingSetItem, ...]) -> tuple[WorkingSetFinding, ...]:
    """`Folded.working_set` (`nora.domain.standing.WorkingSetItem`, `nora`-side)
    into `Outcome.working_set` (`WorkingSetFinding`, domain-side) — the one
    place this boundary is crossed, so `standing()` itself stays a single
    `Outcome(...)` call. `found_at` is dropped here: `Outcome`'s
    own docstring says why.
    """
    return tuple(
        WorkingSetFinding(what=item.what, source=item.source, bears_on=item.bears_on)
        for item in items
    )


async def standing(subject: Subject, case: Case, *, now_ms: int) -> Outcome:
    """The standing FOLD on one real tag turn, graded on the position it wrote.

    The case names a turn
    (`metadata.tag_id`) and the instant it finished (`snapshot.before_ms`, the
    turn's own `tag_trace.created_at`). The sandbox already holds the room up
    to that instant; this reads the turn's record and the position as it stood
    BEFORE the turn out of the source, and runs the deployed pipeline's own
    fold on them (`nora.subject.standing_fold`).

    Every way this can end without a written position is an ERROR, never a
    verdict: a fold that timed out, answered prose instead of JSON, or was
    refused for overflow has said nothing about whether a thread was carried —
    and "nothing was written, so nothing stale was re-affirmed" would pass every
    `stale` case on a dead provider.

    **A turn with no prior is refused, not folded against a blank.** The
    standing chain (migration 0059) does not backfill, so «no revision at or
    before this instant» reads the same for a room that had never folded and for
    one whose position simply predates the chain. Folding the second against «no position yet»
    would grade a fold on an input it never had. Such a moment is parked in the
    corpus, or declared as the counterfactual above; one reaching here
    undeclared is a mistake in the corpus, and says so.

    `metadata.print_input` renders the fold's whole input and stops — no model
    is reached — and returns it as `shown`. Always an ERROR: a run that called
    no model has measured nothing, whatever it prints.
    """
    tag_id = str(case.metadata.get("tag_id") or "")
    moment = case.moment
    if not tag_id:
        return Outcome(
            admitted=None, source="fail_closed",
            error="a standing case names the turn it folds (`metadata.tag_id`)",
        )
    if moment is None:
        return Outcome(
            admitted=None, source="fail_closed",
            error="the fold reads its turn from the source and the runner handed no moment",
        )
    # THE ONE TIER A STANDING CASE MAY VARY: its prior, declared ABSENT, for a
    # turn older than the standing chain. Named in the case, checked here
    # against the source — never inferred from a read that came back empty.
    varies = (case.metadata.get("counterfactual") or {}).get("varies")
    if varies not in (None, "prior"):
        return Outcome(
            admitted=None, source="fail_closed",
            error=f"a standing case may vary only its prior, not {varies!r}",
        )
    prior_absent = varies == "prior"
    try:
        turn = await read_fold_turn(
            subject, source_url=moment.source_url, tag_id=tag_id,
            prior_absent=prior_absent,
        )
    except (LookupError, ValueError) as exc:
        return Outcome(admitted=None, source="fail_closed", error=str(exc))
    stated = int((case.snapshot or {}).get("before_ms") or 0)
    if prior_absent:
        # No revision exists to date the fold, so the room is cut where her
        # reply to the turn landed (`nora.subject.standing_fold`).
        if turn.room_at != stated:
            return Outcome(
                admitted=None, source="fail_closed",
                error=(
                    f"the case's before_ms ({stated}) is not the instant her reply "
                    f"to tag {tag_id!r} landed ({turn.room_at}); a counterfactual "
                    "case is anchored there and nowhere else"
                ),
            )
    # THE FOLD'S INSTANT, not the turn's: production's fold runs after the
    # turn finishes and reads the room then, and on a `recovered` turn her
    # reply is written in between. The anchor is the first revision the room
    # got after the turn; a case anchored anywhere else seeds a different room
    # than the fold read.
    elif turn.folded_at is None:
        return Outcome(
            admitted=None, source="fail_closed",
            error=(
                f"production wrote no standing revision after tag {tag_id!r}, "
                "so the instant its fold read the room at is unrecorded"
            ),
        )
    elif turn.folded_at != stated:
        return Outcome(
            admitted=None, source="fail_closed",
            error=(
                f"the case's before_ms ({stated}) is not the instant production "
                f"folded tag {tag_id!r} at ({turn.folded_at}); the sandbox would "
                "hold a different room than the fold read"
            ),
        )
    if turn.prior is None and not prior_absent:
        return Outcome(
            admitted=None, source="fail_closed",
            error=(
                "no standing revision stood before this turn — the chain "
                "(migration 0059) holds none, so the prior the fold read is "
                "unknowable here; a moment like this is parked, never folded "
                "against a blank"
            ),
        )
    dry = bool(case.metadata.get("print_input"))
    try:
        folded = await fold(subject, turn, rows=moment.rows, call_model=not dry)
    except (LookupError, ValueError, RuntimeError) as exc:
        return Outcome(admitted=None, source="fail_closed", error=str(exc))
    shown = render_shown(folded.calls)
    if dry:
        return Outcome(
            admitted=None, source="fail_closed", shown=shown,
            outcome_token=folded.outcome,
            error="print-input: the fold's input was rendered and no model was called",
        )
    if folded.outcome != "written" or folded.position is None:
        return Outcome(
            admitted=None, source="fail_closed", shown=shown,
            outcome_token=folded.outcome,
            error=f"the fold wrote no position: {folded.outcome}",
        )
    return Outcome(
        admitted=None,
        position=folded.position,
        working_set=_outcome_working_set(folded.working_set),
        evidence=render_evidence(
            turn, display_name=subject.cfg.doctrine.display_name,
            # The deployment's own zone (the one its recurrences keep), stated
            # rather than the machine's: a relative clause is placed on a
            # calendar by the judge, and the room lives in this zone.
            tz=ZoneInfo(subject.cfg.reminders.timezone),
        ),
        shown=shown,
        outcome_token=folded.outcome,
    )


@dataclass(frozen=True, slots=True)
class EntryPoint:
    """One port implementation, plus the configuration it needs to EXIST.

    `config` is here and not left to each caller because leaving it to each
    caller is the mechanism this whole rebuild is about:
    `config.example.toml` ships `[participation] enabled = false` and
    `[relevance_gate] enabled = false`, so `deps.relevance_gate` and
    `deps.fire_path` are `None` unless the entry point turns them on.

    The committed template is not the deployed configuration. That is correct
    for a template — a subsystem ships off — and it means an entry point has to
    say what it needs, once, here.
    """

    drive: Callable[..., Awaitable[Any]]

    #: Does a line ARRIVE after the window this entry point is given?
    #:
    #: Declared here rather than branched on in the runner: the entry point is
    #: a selection on a case. The gate is handed a closed
    #: window and nothing follows it; the other three are handed a window and
    #: then a line.
    delivers_input: bool = False

    #: Dotted config paths, applied UNDER a case's own overrides so a case can
    #: still change anything.
    config: dict[str, Any] = field(default_factory=dict)

    #: Does driving this entry point make her SPEAK INTO the room — a real
    #: `FirePath` turn?
    #:
    #: It decides which rooms a case may anchor in, so it is data here rather
    #: than a list in a test. `FirePath._turn` resolves the room's own adapter
    #: (`_adapter_for`) before it says anything, because speaking into a room
    #: on a platform she is not on is worse than staying quiet — so a turn in a
    #: room whose adapter the sandbox disables cannot run. Discord's is
    #: disabled there (a declared divergence: its adapter authenticates for
    #: real at startup), which makes a Discord-anchored turn case unrunnable
    #: by construction.
    #:
    #: A `participation` or `proactive_act` case anchored in a Discord room
    #: comes back `failed_error` with a `KeyError` — an ERROR, never a verdict.
    #: `gate` and `dreamer` are unaffected: they read and write, and never speak.
    drives_a_turn: bool = False

    #: What a judge grades, when a judge is configured: `"reply"` — what she
    #: said, against the reply rubric — or `"position"` — the standing position
    #: a fold wrote, against the case's own stated property. Data here, not a
    #: branch on the entry's name, for the reason `drives_a_turn` is.
    judged_on: Literal["reply", "position"] = "reply"


#: Both entry points turn on the SAME pair, and that is not duplication: the
#: quiescence trigger and the room policy belong to the gate, so
#: `[participation]` and `[relevance_gate]` are one subsystem with two
#: switches. `nora/config.py`'s `_the_gate_needs_something_behind_it` refuses a
#: gate with no turn behind it, which is the same fact as a config validator.
_PARTICIPATION_SUBSYSTEM = {
    "participation.enabled": True,
    "relevance_gate.enabled": True,
    # THE TIERS THE TURN READS, and they ship OFF.
    #
    # `config.example.toml` has `[standing] enabled = false` and `[notes]
    # enabled = false`, and composition gates the readers on them: no note
    # store, no standing reader, on the fire path AND on the gate.
    #
    # A participation case needs both on regardless of config. Without it every `-known` case in
    # `participation.yaml` is byte-identical to its `-unknown` twin: `know()`
    # writes the note and the standing, and the turn cannot read either. The
    # pair would report "the knowledge cut works" having cut nothing, which is
    # the footgun `seed_from_dict`'s own docstring names, arriving through
    # config instead of through a dropped key.
    #
    # It also decides one gate case: `real-commitment-in-standing` is the
    # positive half of a pair whose negative is a forged commitment claimed in
    # chat. With no standing reader the `go` case IS the `no_go` shape.
    "standing.enabled": True,
    "notes.enabled": True,
}

#: The port's implementations, by the name a case selects. A new suite over an
#: existing entry point adds NOTHING here; a test pins that.
ENTRY_POINTS = {
    "gate": EntryPoint(drive=gate, config=_PARTICIPATION_SUBSYSTEM),
    "participation": EntryPoint(
        drive=participation, config=_PARTICIPATION_SUBSYSTEM, delivers_input=True,
        drives_a_turn=True,
    ),
    # The tag path needs NOTHING turned on: `[engagement.trigger] enabled` ships
    # true, which is the deployment's own default and the reason this is the
    # cheapest of the three to drive.
    "tag": EntryPoint(drive=tag, delivers_input=True, drives_a_turn=True),
    # `[dreamer] enabled` ships false. The RUNTIME only: which adaptor is built is
    # the corpus's business (the culture adaptor needs `[culture] enabled`). Putting an
    # adaptor's flag here would make one corpus's requirement look like the
    # entry point's.
    "dreamer": EntryPoint(drive=dreamer, config={"dreamer.enabled": True}),
    # The act turn needs the FIRE PATH, which `[participation]` also builds,
    # and the note tier its row points at. NOT `[heartbeat]`: composition
    # spawns `heartbeat.run()` as an endless task, so a subject with it on
    # would tick underneath the case and `settle()` would never return.
    "proactive_act": EntryPoint(
        drive=proactive_act,
        drives_a_turn=True,
        config={
            "participation.enabled": True,
            "relevance_gate.enabled": True,
            "notes.enabled": True,
            "standing.enabled": True,
            "dreamer.enabled": True,
        },
    ),
    # The fold is the TAG PATH's post-turn stage, and composition builds its
    # wiring only under `[standing] enabled`. No turn is driven — the turn
    # already happened and is read from its record — so it may anchor in any
    # room, Discord's included: nothing is spoken.
    "standing": EntryPoint(
        drive=standing, config={"standing.enabled": True}, judged_on="position",
    ),
}

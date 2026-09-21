"""The entry-point port: where a case's input enters Nora.

`01-boundaries` §3.1 makes this a PORT rather than a taxonomy, and §7 names the
reading that would refute it. This module is that reading, run:

    gate           `deps.relevance_gate.decide`   — a real component, no turn
    participation  `deps.fire_path.speak`         — a whole turn, no gate

Two entry points chosen for being maximally unalike. They share a lifecycle
(obtain → know → deliver → read) and one `Outcome`, so the port holds. What it
COST to make them share is worth stating, because it is the finding:

**Neither was reachable.** `RelevanceGate` lived only at
`ParticipationTrigger._gate` and `FirePath` only at `._speak`. Every other
entry point — `dispatcher`, `engagement`, `participation_trigger`,
`dreamer_runtime` — is already a field on `Dependencies`; these two were not,
and building a second one was easier than reaching the deployed one. That is a
fair part of the mechanism behind seven assemblies, and it is now closed by two
fields that add no object and change no behaviour.

The gate is NOT run on the participation path, deliberately — the decision this
rebuild keeps rather than re-litigates: *"a suite that had to get past a live
gate call first would be measuring the gate's verdict and the turn's behaviour
in one number"*. `speak` is called directly, exactly as the trigger calls it
once the gate has admitted.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from nora.app.fire import SENT, SILENT, participation_occasion
from nora.subject import (
    Subject,
    act,
    arrive,
    deliver,
    dream,
    heard,
    last_row_id,
    owe,
    pending,
    settle,
    silent_failures,
)

from .domain import Case, Outcome


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
    # back as `no_go`, which is the same token a correct refusal produces. The
    # first run of the old gate suite reported PASS with every safety case held
    # and every capability "dead" while not one call had succeeded — that is
    # the ERROR-as-PASS substitution, reproduced inside the instrument built to
    # measure it, and this branch is what refuses it.
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
    first grades a healthy Nora as silent.
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
    # THE SAME FAIL-CLOSED RULE THE GATE HAS, and this path needs it more.
    #
    # `speak` answers one of five tokens, and only `sent` and `silent` are
    # decisions. The three `failed_*` tokens are a turn that never happened —
    # the model was unreachable, the call timed out, a screen refused — and
    # every one of them leaves NO ROW, which reads as an empty `said`, which
    # grades as SILENCE.
    #
    # So without this branch an unreachable provider makes every `expect:
    # silence` case pass. That is ERROR scored as PASS, on precisely the half
    # of the corpus the knowledge-cut twins live in: the twin that must fall
    # silent would "hold" while nothing ran at all. It is the same substitution
    # the old gate suite made on its first run — every safety case held, every
    # capability dead, not one call successful — one entry point over.
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
    # BEFORE the line arrives: composition spawns endless loops into the same
    # set (the compute poller, the version announcer), so what this case
    # fired is the DELTA over what the graph already carried.
    baseline = pending(subject)
    await deliver(subject, body, now_ms=now_ms, person=case.person, mention=case.mention)
    if not await settle(subject, baseline=baseline):
        return Outcome(
            admitted=None,
            source="fail_closed",
            error="the turn never settled within the timeout",
        )

    # THE FAIL-CLOSED RULE, and this path had no other signal for it.
    #
    # `Dispatcher.on_message` returns `None` and hands the turn to a background
    # task, so unlike the gate (`source`) and the turn (`speak`'s token) there
    # is nothing here to read. Measured: with no provider reachable this entry
    # point returned `error=None, admitted=False, said=()` — a verdict, from a
    # run that reached no model. Every corpus that lands here expects a REPLY,
    # so that is not a near miss: it is every case failing for a reason no
    # number would have shown, or — one flipped expectation away — every case
    # passing.
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


async def dreamer(subject: Subject, case: Case, *, now_ms: int) -> Outcome:
    """One dream pass, graded on the NOTEBOOK.

    The batch is what `know()` wrote — the Dreamer reads the room's rows, so a
    case's transcript IS its input here, exactly as it is for the gate. There
    is nothing to deliver.

    `case.category` names the adaptor, because which adaptor runs is a property
    of the CASE and not of the runner: `note-candidates` and the proactive
    adaptor are two passes over one runtime, and branching on them in the
    runner would be the eight drivers growing back inside one file.
    """
    # `now:` used to be refused here. It no longer is: `build_dependencies`
    # takes a `clock` and `SubjectSpec.now` sets it, so a case that states the
    # time is judged AT that time. The refusal stood for as long as the graph
    # could not be told, which was the honest answer then and is the wrong one
    # now — see `01-boundaries` §3.
    # AN INPUT THE GRAPH CANNOT HONOUR IS AN ERROR — the same rule the Honcho
    # conclusions follow, and the clock used to.
    #
    # The proactive corpora are MULTI-ROOM: a case names two rooms and keys its
    # standing per room, because the decide pass judges a workspace and picks
    # which of its rooms to stand in. `nora.subject.Knowledge` is single-room —
    # one transcript, one standing, one `DEFAULT_ROOM`.
    #
    # Refused rather than run, and this one had to be caught rather than
    # noticed: with the rooms unseeded the pass finds nothing due, writes
    # nothing, and returns `nothing_found` with no error — so every case
    # expecting `none` PASSES, having measured an empty workspace. That is the
    # shape this whole layer exists to refuse, and it survived one round of
    # fixing here because the clock was the louder problem.
    # `rooms` and `standing` are SEEDABLE now (GOAL step 1) and have moved to
    # `knows`. What is still not is `rows` — the reminder rows a decide pass is
    # asserted to WRITE, which is an expectation rather than an input, and is
    # checked against `Outcome.written` instead.
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
    adaptor = case.category or "note-candidates"
    try:
        result = await dream(subject, adaptor=adaptor)
    except LookupError as exc:
        return Outcome(admitted=None, source="fail_closed", error=str(exc))

    # THE FAIL-CLOSED RULE, and `status` alone cannot carry it.
    #
    # `unavailable` is the obvious half: the adaptor could not run, and an
    # un-run pass produces an empty notebook diff byte-identically to a pass
    # that correctly found nothing.
    #
    # The half that was MISSING is `nothing_found` after a model call that
    # failed. Measured with no provider reachable: four proactive cases came
    # back `nothing_found` with NO error, and the one expecting silence scored
    # a PASS — ERROR-as-PASS inside the layer built to end it, found by
    # checking rather than by reading.
    #
    # `samples_failed` is production's own per-sample counter
    # (`nora_dream_sample_total{status="unavailable"}`), which is the only
    # thing that separates "asked the model and could not reach it" from "never
    # needed to ask, because nothing was due". The second is a real verdict a
    # corpus expects; the first is not a verdict at all.
    # WIRING, not a verdict. `no_home_room` means the workspace named no home
    # room, so the pass returned before rendering anything — indistinguishable
    # from "the workspace had not moved" in `status`, and every case expecting
    # `none` would pass on a misconfigured policy.
    if result.declined == "no_home_room":
        return Outcome(
            admitted=None, source="fail_closed",
            error="the workspace names no home room, so the pass never ran",
            outcome_token=result.status,
        )
    # `samples_failed and not samples_ok`, NOT `or samples_failed`: the
    # runtime's own rule is that a pass is `unavailable` only when NONE was
    # readable, because one good read is a read (`dreamer.py:1470`). Erroring on
    # one bad sample of three made this layer stricter than the thing it
    # measures, which is its own kind of wrong number.
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
        staged=result.staged,
        # `declined` rides into the token so a `none` verdict says WHY it was
        # none — `unchanged` and `held_quiet` are different facts about the
        # workspace and a report that showed neither would be unreadable.
        outcome_token=(
            f"{result.status}:{result.declined}" if result.declined
            else result.status
        ),
    )


async def proactive_act(subject: Subject, case: Case, *, now_ms: int) -> Outcome:
    """The ACT turn — `FirePath.run` on a due `kind="proactive"` row.

    NOT a dream pass, and that distinction is the corpus's own: the DECIDE pass
    writes a reminder row, and this is what happens when that row comes due. It
    is a fire turn, which is why the old driver's `FirePath(21)` lived in
    `run_proactive_act_case` — the seventh assembly, and the one no parity test
    covered.

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
        # `silence` case passes on a misconfigured policy — which is how this
        # was found: three cases in one database, two of them screening an
        # earlier case's row.
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


@dataclass(frozen=True, slots=True)
class EntryPoint:
    """One port implementation, plus the configuration it needs to EXIST.

    `config` is here and not left to each caller because leaving it to each
    caller is the mechanism this whole rebuild is about. It was found the hard
    way: the first version of `tests/integration/test_two_entry_points_one_
    runner.py` went green while `deps.relevance_gate` and `deps.fire_path` were
    both `None` — `config.example.toml` ships `[participation] enabled = false`
    and `[relevance_gate] enabled = false`, so NEITHER entry point ran and the
    test passed on the error it was written to catch.

    The committed template is not the deployed configuration. That is correct
    for a template — a subsystem ships off — and it means an entry point has to
    say what it needs, once, here.
    """

    drive: Callable[..., Awaitable[Any]]

    #: Does a line ARRIVE after the window this entry point is given?
    #:
    #: Declared here rather than branched on in the runner, for §3.1's reason:
    #: the entry point is a selection on a case. The gate is handed a closed
    #: window and nothing follows it; the other three are handed a window and
    #: then a line.
    delivers_input: bool = False

    #: Dotted config paths, applied UNDER a case's own overrides so a case can
    #: still change anything.
    config: dict[str, Any] = field(default_factory=dict)


#: Both entry points turn on the SAME pair, and that is not duplication: since
#: design 36 the quiescence trigger and the room policy belong to the gate, so
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
    # The old harness forced both on for a participation case regardless of
    # config — `evals/harness.py:553`, literally `or participation` — and no
    # corpus ever said so. Without it every `-known` case in
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
#: existing entry point adds NOTHING here — that is `01-boundaries` §5(3), and
#: it is the property that decays first, so it is pinned by a test.
ENTRY_POINTS = {
    "gate": EntryPoint(drive=gate, config=_PARTICIPATION_SUBSYSTEM),
    "participation": EntryPoint(
        drive=participation, config=_PARTICIPATION_SUBSYSTEM, delivers_input=True,
    ),
    # The tag path needs NOTHING turned on: `[engagement.trigger] enabled` ships
    # true, which is the deployment's own default and the reason this is the
    # cheapest of the three to drive.
    "tag": EntryPoint(drive=tag, delivers_input=True),
    # `[dreamer] enabled` ships false, and `[notes]` decides whether the
    # note-candidates adaptor is built at all — two switches for two risks, and
    # a corpus that needs the pass needs both on.
    # The RUNTIME only. Which adaptor is built is the corpus's business and
    # they differ: note-candidates needs `[notes] candidates_enabled`, the
    # proactive adaptor has no flag at all and is wired by `policy.toml` naming
    # a home room. Putting either here would make one corpus's requirement look
    # like the entry point's.
    "dreamer": EntryPoint(drive=dreamer, config={"dreamer.enabled": True}),
    # The act turn needs the FIRE PATH, which `[participation]` also builds,
    # and the note tier its row points at. NOT `[heartbeat]`: composition
    # spawns `heartbeat.run()` as an endless task, so a subject with it on
    # would tick underneath the case and `settle()` would never return.
    "proactive_act": EntryPoint(
        drive=proactive_act,
        config={
            "participation.enabled": True,
            "relevance_gate.enabled": True,
            "notes.enabled": True,
            "standing.enabled": True,
            "dreamer.enabled": True,
        },
    ),
}

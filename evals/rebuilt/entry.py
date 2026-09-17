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
    DEFAULT_ROOM,
    Subject,
    arrive,
    deliver,
    dream,
    heard,
    last_row_id,
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
    platform, conversation_id = DEFAULT_ROOM
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
    platform, conversation_id = DEFAULT_ROOM

    # The judged line: UNTAGGED by construction, which is what makes it a
    # participation candidate rather than a tag. Stamped at `now_ms`, which
    # `know()` laid the window down to end one minute before, so it is strictly
    # the newest row `recent`'s `(ts, id)` sort sees.
    await arrive(subject, case.input, now_ms=now_ms)
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
    await deliver(subject, body, now_ms=now_ms)
    if not await settle(subject):
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
    if failed:
        return Outcome(
            admitted=None,
            source="fail_closed",
            error=f"the turn failed silently ({failed:g} counted)",
        )

    hers = await heard(subject, after_id=watermark)
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
    adaptor = case.category or "note-candidates"
    try:
        result = await dream(subject, adaptor=adaptor)
    except LookupError as exc:
        return Outcome(admitted=None, source="fail_closed", error=str(exc))

    # `unavailable` is the fail-closed one — the adaptor could not run. It must
    # not grade as `none`, which is a legitimate verdict a corpus expects and
    # which an un-run pass produces byte-identically: an empty notebook diff.
    if result.status == "unavailable":
        return Outcome(
            admitted=None,
            source="fail_closed",
            error=f"the pass was unavailable: {result.stop_reason}",
            outcome_token=result.status,
        )
    return Outcome(
        admitted=None,
        changed=result.changed,
        outcome_token=result.status,
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
}

#: The port's implementations, by the name a case selects. A new suite over an
#: existing entry point adds NOTHING here — that is `01-boundaries` §5(3), and
#: it is the property that decays first, so it is pinned by a test.
ENTRY_POINTS = {
    "gate": EntryPoint(drive=gate, config=_PARTICIPATION_SUBSYSTEM),
    "participation": EntryPoint(drive=participation, config=_PARTICIPATION_SUBSYSTEM),
    # The tag path needs NOTHING turned on: `[engagement.trigger] enabled` ships
    # true, which is the deployment's own default and the reason this is the
    # cheapest of the three to drive.
    "tag": EntryPoint(drive=tag),
    # `[dreamer] enabled` ships false, and `[notes]` decides whether the
    # note-candidates adaptor is built at all — two switches for two risks, and
    # a corpus that needs the pass needs both on.
    # The RUNTIME only. Which adaptor is built is the corpus's business and
    # they differ: note-candidates needs `[notes] candidates_enabled`, the
    # proactive adaptor has no flag at all and is wired by `policy.toml` naming
    # a home room. Putting either here would make one corpus's requirement look
    # like the entry point's.
    "dreamer": EntryPoint(drive=dreamer, config={"dreamer.enabled": True}),
}

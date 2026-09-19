"""The SDK-native spine — layers 3 and 4, and nothing else.

`design/evaluation/GOAL-2-backbone.md`. The basis: the evaluation suite IS
`strands-agents-evals`. Layer 1 (corpus, harness, report, caching) and layer 2
(content grading) are the SDK's and are not written here. This module holds
only what no SDK can supply:

- **layer 3, the subject** — `nora_task`, which obtains the deployed Nora,
  isolates the case, drives one entry point, and *never raises*;
- **layer 4, the absence** — `AbsenceGrader`, because 75 of 150 cases expect
  her to say nothing and every built-in evaluator judges `actual_output`.

**THE ERROR CHANNEL, and why it is where it is.** MEASURED 2026-09-18 against
the installed 1.0.3: `Experiment._run_task_async` reads exactly five keys off a
task's dict — `output`, `trajectory`, `interactions`, `environment_state`,
`input`. A `metadata` key is **silently dropped** (the `metadata` on the
`EvaluationData` is the CASE's, never the task's), so the first draft of this
probe put the failure reason there and watched it vanish. `environment_state`
is the surviving channel and is what `NORA_*` below rides in.

**Why the task must never raise.** `Experiment._worker` wraps the task in
`except Exception` and writes `test_pass=False, score=0` for **every evaluator
in the list, calling none of them**. An exception therefore arrives at the
report dressed as a considered FAIL, which is rule 3 (*ERROR is not FAIL and is
never PASS*) broken by the library rather than by us. So every failure is
caught here, scrubbed, and returned as data.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from opentelemetry import trace as _trace_api
from opentelemetry.sdk.trace import TracerProvider as _SDKTracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from strands_evals import Case, TracedHandler, eval_task
from strands_evals.evaluators.evaluator import Evaluator
from strands_evals.types.evaluation import (
    EnvironmentState,
    EvaluationData,
    EvaluationOutput,
)

from nora.subject import SubjectSpec, know, obtain

#: Names carried on `environment_state`. A reader must key on these, never on
#: `test_pass`, which is a `bool` and cannot hold the third answer.
NORA_ERROR = "nora.error"
NORA_TOKEN = "nora.outcome_token"  # noqa: S105 — an outcome token, not a credential
NORA_SAID = "nora.said_count"
NORA_DELIVERY = "nora.delivery"
#: `Outcome.admitted` — `bool` for the `gate` entry point, `None` for every
#: other one (`evals/rebuilt/domain.py`'s own docstring: "both answer *was she
#: admitted* and *what did the room hear*, and the gate simply never reaches
#: the second"). Carried separately from `NORA_SAID` because `gate()` never
#: writes room speech at all — `AbsenceGrader` read only `NORA_SAID` before
#: this existed, so it graded EVERY `gate` case as silence, verdict ignored
#: (`09-the-backbone-measured.md` §1.1, found while re-running Step 0 against
#: `relevance_gate`).
NORA_ADMITTED = "nora.admitted"
#: `nora.agent.prefill.build_prefill`'s own `nora.prefill.blocks_empty`
#: attribute (a comma-joined list of block names whose body was empty),
#: carried through unmapped. `StrandsInMemorySessionMapper` (what
#: `TracedHandler` uses) drops any span whose `gen_ai.operation.name` is not
#: `chat`/`execute_tool`/`invoke_agent` — a Nora plumbing span like
#: `agent.prefill` never survives into a `Session`, mapped or not. So S4
#: ("the blocks she needs were rendered non-empty",
#: `10-the-scenario-map.md` §3) reads the raw span directly via
#: `_prefill_capture()` rather than through the SDK's trajectory machinery —
#: the property in question is what `build_prefill` assembled, not anything
#: shaped like a model turn. Empty string for every entry point that never
#: calls `build_prefill` (`gate`, `dreamer`).
NORA_BLOCKS_EMPTY = "nora.blocks_empty"
#: `Outcome.written`/`len(.changed)`/`len(.staged)` off the FIRST drive —
#: never surfaced to a grader before this (S6, `10-the-scenario-map.md` §3).
#: `dream()`'s own `written`/`changed` are already PER-CALL DELTAS (it
#: diffs the notebook `before`/`after` inside one call), so a second call on
#: the SAME subject naturally reports "what run 2 added", not a cumulative
#: count. `staged` is the one exception — an ABSOLUTE snapshot of every
#: `NOTE_CANDIDATE` row for the workspace, not a diff — so idempotence on it
#: needs both runs' counts compared, not run 2's alone.
NORA_WRITTEN = "nora.written"
NORA_CHANGED_COUNT = "nora.changed_count"
NORA_STAGED_COUNT = "nora.staged_count"
#: The REPEAT run's equivalents (`Spec.repeat > 1`), `None` when there was no
#: repeat run.
NORA_WRITTEN_2 = "nora.written_2"
NORA_CHANGED_COUNT_2 = "nora.changed_count_2"
NORA_STAGED_COUNT_2 = "nora.staged_count_2"
#: The repeat run's OWN `Outcome.error` — found missing by review, 2026-09-19.
#: Entry points return `Outcome(error=...)` rather than raising
#: (`evals/rebuilt/entry.py`'s fail-closed branches), so a repeat run that
#: failed closed comes back with `written=0, changed=(), staged=()` and no
#: exception at all — indistinguishable from "ran cleanly and added
#: nothing" unless this is carried and checked. `evals/rebuilt/domain.py`'s
#: own `Outcome.error` docstring: "An Outcome with an error is never
#: graded" — `IdempotenceGrader` was violating that for the repeat half.
NORA_ERROR_2 = "nora.error_2"

#: The three verdicts, carried in `EvaluationOutput.label`.
#:
#: `test_pass` is a `bool`, so ERROR has nowhere else to go. Every aggregation
#: MUST read `label` and MUST NOT read `test_pass` alone: an ERROR sets
#: `test_pass=False` only because the field is not optional, and counting it as
#: a failure is the substitution this whole bundle exists to refuse.
PASS, FAIL, ERROR = "PASS", "FAIL", "ERROR"


def state_of(data: EvaluationData, name: str) -> Any:
    """One named value off `actual_environment_state`, or `None`."""
    for entry in data.actual_environment_state or ():
        if entry.name == name:
            return entry.state
    return None


def scrub(text: str, secret: str) -> str:
    """Take the DSN out of an exception string.

    The SDK writes `reason: f"An error occurred: {e}"` verbatim into the report
    and into anything `to_file()` writes, with no hook between the raise and
    the row. An asyncpg or migration failure carries the password, so this runs
    before any text leaves the task.
    """
    return text.replace(secret, "<dsn>") if secret else text


_prefill_exporter: InMemorySpanExporter | None = None


def _prefill_capture() -> InMemorySpanExporter:
    """The in-memory exporter `_drive()` reads `agent.prefill` off.

    Independent of `strands_evals.TracedHandler`/`StrandsEvalsTelemetry` on
    purpose: those exist to produce a `Session`, and a Nora plumbing span
    (`nora.obs.tracing.span("agent.prefill", ...)`) is dropped by
    `StrandsInMemorySessionMapper` before a `Session` ever sees it (it isn't
    `chat`/`execute_tool`/`invoke_agent`). This reads the RAW span instead.

    OTel's global `TracerProvider` may be set only once per process
    (`set_tracer_provider` no-ops with a warning on a second real one) — but
    this DOES coexist with `TracedHandler` in the same process, in either
    construction order: whichever runs first keeps the real provider,
    `add_span_processor` is additive rather than replacing, and
    `StrandsEvalsTelemetry.__init__` falls back to
    `trace_api.get_tracer_provider()` when its own `set_tracer_provider`
    call is refused — so both exporters end up attached to the ONE shared
    provider and both receive every span. Verified empirically both ways
    (`_prefill_capture()` first and `TracedHandler()` first); an earlier
    version of this docstring claimed the opposite without checking.
    """
    global _prefill_exporter
    if _prefill_exporter is None:
        _prefill_exporter = InMemorySpanExporter()
        provider = _trace_api.get_tracer_provider()
        if not isinstance(provider, _SDKTracerProvider):
            provider = _SDKTracerProvider()
            _trace_api.set_tracer_provider(provider)
        provider.add_span_processor(SimpleSpanProcessor(_prefill_exporter))
    return _prefill_exporter


def _blocks_empty(exporter: InMemorySpanExporter) -> str | None:
    """`nora.prefill.blocks_empty` off the `agent.prefill` span, or `None` if
    no such span was captured at all.

    **`None` and `""` are different facts, and collapsing them was a bug**
    (found by review, 2026-09-19): `""` means "a span was captured and no
    block was empty" — a real PASS-worthy fact. No span at all means
    `build_prefill` never ran, OR ran but the span was lost (provider
    ordering, a sampler, the entry point never calls it at all —
    `gate`/`dreamer`) — an ungradable fact. `ContextGrader` reading both as
    "nothing was empty" turned every one of those failure modes into a
    silent PASS, exactly the ERROR-as-PASS substitution this whole bundle
    exists to refuse.
    """
    for finished in exporter.get_finished_spans():
        if finished.name == "agent.prefill":
            return str((finished.attributes or {}).get("nora.prefill.blocks_empty", ""))
    return None


@dataclass(frozen=True)
class Spec:
    """What the spine needs that the SDK's `Case` does not carry."""

    database_url: str
    entry: str = "participation"
    #: `[platform, room_id]`. A room per case, because the gate reads the
    #: newest 40 rows and the Dreamer the oldest 64 — cases sharing a database
    #: and a room are graded on each other's messages.
    room: tuple[str, str] | None = None
    workspace: str = "nora"
    overrides: dict[str, Any] | None = None
    #: When the case happens. Empty means the wall clock.
    now: str = ""
    #: S6 idempotence (`10-the-scenario-map.md` §3): drive the SAME subject
    #: through the SAME case `repeat` times, at the SAME `now_ms`, and carry
    #: the LAST run's `written`/`changed`/`staged` separately from the
    #: first's — no re-`obtain()` between runs, unlike every other case,
    #: because idempotence needs run 2 to see what run 1 actually left.
    repeat: int = 1


async def _drive(case: Case, spec: Spec) -> dict[str, Any]:
    """Obtain, isolate, seed, drive, tear down. Raises; `nora_task` catches."""
    from evals.rebuilt.entry import ENTRY_POINTS
    from evals.rebuilt.runner import _knowledge, room_for, workspace_for

    # NOTE, and it is a finding rather than an import: `room_for`,
    # `workspace_for` and `_knowledge` are LAYER 3 — case isolation and
    # seeding, which no SDK supplies — but they live in `runner.py`, which is
    # layer 1 and is to be deleted. They have to move before that happens, and
    # this import is the marker.
    port = ENTRY_POINTS[spec.entry]
    if spec.repeat > 1 and port.delivers_input:
        # Found by review, 2026-09-19: `port.delivers_input` means a line
        # ARRIVES after the window (`entry.py`'s own `EntryPoint.
        # delivers_input` docstring) — driving the SAME case a second time
        # delivers a SECOND copy of that line, so run 2 is not "the same
        # pass again", it is a different pass with an extra message in it.
        # Loud rather than silent: idempotence is only meaningful for entry
        # points that read state without re-delivering input (`dreamer`).
        raise ValueError(
            f"Spec.repeat={spec.repeat} would re-deliver the case's message "
            f"for entry={spec.entry!r} (delivers_input=True) — idempotence "
            "only makes sense for an entry point that does not re-deliver "
            "input on each drive, like `dreamer`"
        )
    room = spec.room or room_for(case.name or "unnamed")
    workspace = workspace_for(case.name or "unnamed", spec.workspace)
    workdir = Path(tempfile.mkdtemp(prefix="nora-case-"))
    subject = None
    try:
        subject = await obtain(
            SubjectSpec(
                database_url=spec.database_url,
                workdir=workdir,
                room=room,
                overrides={
                    **port.config,
                    "memory.workspace_id": workspace,
                    **(spec.overrides or {}),
                },
                **({"now": datetime.fromisoformat(spec.now)} if spec.now else {}),
            )
        )
        seeded = await know(
            subject, _knowledge({}), leave_room_for_arrival=port.delivers_input,
        )
        exporter = _prefill_capture()
        exporter.clear()
        nora_case = _as_nora_case(case, spec)
        outcome = await port.drive(subject, nora_case, now_ms=seeded.now_ms)
        blocks_empty = _blocks_empty(exporter)

        repeat_state = []
        if spec.repeat > 1:
            # SAME subject, SAME case, SAME now_ms — no re-`obtain()`.
            # Idempotence needs run 2 to see exactly what run 1 left.
            # `spec.repeat > 1` guarantees the loop runs at least once, so
            # this initial value is never read — kept only so a static
            # checker does not have to prove that.
            repeat_outcome = outcome
            for _ in range(spec.repeat - 1):
                repeat_outcome = await port.drive(subject, nora_case, now_ms=seeded.now_ms)
            repeat_state = [
                EnvironmentState(name=NORA_ERROR_2, state=repeat_outcome.error),
                EnvironmentState(name=NORA_WRITTEN_2, state=repeat_outcome.written),
                EnvironmentState(name=NORA_CHANGED_COUNT_2, state=len(repeat_outcome.changed)),
                EnvironmentState(name=NORA_STAGED_COUNT_2, state=len(repeat_outcome.staged)),
            ]

        return {
            "output": "\n".join(outcome.said),
            "environment_state": [
                EnvironmentState(name=NORA_ERROR, state=outcome.error),
                EnvironmentState(name=NORA_TOKEN, state=outcome.outcome_token),
                EnvironmentState(name=NORA_SAID, state=len(outcome.said)),
                EnvironmentState(name=NORA_DELIVERY, state=outcome.delivery),
                EnvironmentState(name=NORA_ADMITTED, state=outcome.admitted),
                EnvironmentState(name=NORA_BLOCKS_EMPTY, state=blocks_empty),
                EnvironmentState(name=NORA_WRITTEN, state=outcome.written),
                EnvironmentState(name=NORA_CHANGED_COUNT, state=len(outcome.changed)),
                EnvironmentState(name=NORA_STAGED_COUNT, state=len(outcome.staged)),
                *repeat_state,
            ],
        }
    finally:
        # 150 cases at five samples is 750 connection pools. `EvalTaskHandler`'s
        # own hooks are SYNC and `_close()` is not, so teardown cannot live
        # there and lives here.
        if subject is not None:
            from evals.rebuilt.runner import _close

            await _close(subject)
        shutil.rmtree(workdir, ignore_errors=True)


def _as_nora_case(case: Case, spec: Spec) -> Any:
    from evals.rebuilt.domain import Case as NoraCase

    md = case.metadata or {}
    return NoraCase(
        name=case.name or "unnamed",
        entry=spec.entry,                         # type: ignore[arg-type]
        expect=md.get("expect", "either"),
        input=str(case.input),
        knows=md.get("knows", {}),
    )


def nora_task(spec: Spec):
    """A `Callable[[Case], dict]` for `Experiment.run_evaluations_async`.

    **Sync on purpose.** `@eval_task`'s wrapper does not await, and every entry
    point is `async def`, so an un-wrapped coroutine would be graded as the
    string `'<coroutine object ...>'` and scored. Two escapes exist: pass a
    bare `async def` (which the SDK does await) and lose `TracedHandler` with
    it, or `asyncio.run` inside a sync function. This takes the second, because
    `TracedHandler` is the only thing that produces a `Session`, and a
    `Session` is what all 13 level-bearing evaluators read.

    The cost is stated rather than hidden: this forces `max_workers=1`. That is
    not a concession — `TracedHandler` shares one span exporter and is
    documented as serial-only, so the constraint arrives twice and the second
    one is not ours to relax.
    """

    def task(case: Case) -> dict[str, Any]:
        try:
            return asyncio.run(_drive(case, spec))
        except Exception as exc:                      # noqa: BLE001 — see module docstring
            return {
                "output": "",
                "environment_state": [
                    EnvironmentState(
                        name=NORA_ERROR,
                        state=scrub(f"{type(exc).__name__}: {exc}", spec.database_url),
                    ),
                    EnvironmentState(name=NORA_TOKEN, state=""),
                    EnvironmentState(name=NORA_SAID, state=0),
                    EnvironmentState(name=NORA_DELIVERY, state=""),
                    EnvironmentState(name=NORA_ADMITTED, state=None),
                    EnvironmentState(name=NORA_BLOCKS_EMPTY, state=None),
                    EnvironmentState(name=NORA_WRITTEN, state=None),
                    EnvironmentState(name=NORA_CHANGED_COUNT, state=None),
                    EnvironmentState(name=NORA_STAGED_COUNT, state=None),
                    EnvironmentState(name=NORA_WRITTEN_2, state=None),
                    EnvironmentState(name=NORA_CHANGED_COUNT_2, state=None),
                    EnvironmentState(name=NORA_STAGED_COUNT_2, state=None),
                ],
            }

    return task


def traced_nora_task(spec: Spec) -> tuple[Any, TracedHandler]:
    """S2 — *tool routing* (`10-the-scenario-map.md` §3), and it needs no new
    grader at all. `ToolCalled`/`ToolSelectionAccuracyEvaluator`/
    `ToolParameterAccuracyEvaluator`/`TrajectoryEvaluator` all read
    `actual_trajectory`, and `09-the-backbone-measured.md` §1 already
    measured that a real `participation` case's `Session` carries a genuine
    `ToolExecutionSpan` — so the only thing missing was a `Session` at all,
    which `nora_task(spec)` alone never produces.

    `eval_task(TracedHandler())(nora_task(spec))` is the whole of it:
    `TracedHandler.after()` does `processed.setdefault("trajectory",
    session)`, and `nora_task`'s dict never sets that key, so this ADDS a
    trajectory onto the exact same `output`/`environment_state` shape every
    other grader in this module already reads — S2 and S7 (`AbsenceGrader`)
    can grade the SAME case.

    Returns `(task, handler)` — the caller must hold `handler` for the run's
    lifetime (one shared exporter) and pass `max_workers=1`, the same
    constraint `nora_task`'s own docstring already states for the reason
    `TracedHandler` itself gives (serial-only). Coexists with
    `_prefill_capture()` in the same process (verified both construction
    orders — see its docstring); does NOT coexist with a SECOND
    `TracedHandler` instance run concurrently in the same process.
    """
    handler = TracedHandler()
    return eval_task(handler)(nora_task(spec)), handler


def render_transcript_at(case: Case) -> dict[str, Any]:
    """S5 — *a dated question is answered against the case's clock*
    (`10-the-scenario-map.md` §3). Pure, no `Subject`, no database, no
    network: `RelevanceGate` has no clock seam at all (`SubjectSpec.now` has
    zero effect on what it renders — a genuine gap, not routed around here),
    and `participation`/`tag` DO (`FirePath` calls `self._clock()` and
    threads it into `build_prefill(now=...)`), but a full driven case pays
    for a `Subject`/database to exercise a computation that is a pure
    function of rows and a clock: `nora.domain.blocks.render_messages`, the
    same one `_transcript_block` calls, and `nora.domain.prompt.age`
    underneath it.

    `case.metadata`: `now` (an ISO timestamp) and `rows`, a list of
    `{body, name, minutes_ago}`. Returns the rendered `<m ...>` elements as
    `output`, for a grader to search (`SubstringGrader`, `case.
    expected_output` naming the exact `age="..."` attribute expected).

    **Never raises** (found missing by review, 2026-09-19) — this module's
    own docstring states the rule and every other task follows it, but this
    one did not: a malformed case (`now` missing, an unparseable timestamp, a
    row missing `name`) raised straight out, and `Experiment._worker`'s own
    `except Exception` (see module docstring) turns that into
    `test_pass=False, score=0` for EVERY evaluator **without writing a row to
    `detailed_results` at all** — worse than the ERROR-as-FAIL substitution
    the module docstring warns about, because the case does not even survive
    into `read()`'s count. It disappears from the denominator rather than
    landing in it.
    """
    from nora.domain.blocks import render_messages
    from nora.domain.models import DeliveryStatus, TranscriptRow

    try:
        md = case.metadata or {}
        now = datetime.fromisoformat(md["now"])
        rows = tuple(
            TranscriptRow(
                id=i, sender_id="+000", sender_name=r["name"],
                ts=int((now - timedelta(minutes=r["minutes_ago"])).timestamp() * 1000),
                body=r["body"], is_nora=False, delivery_status=DeliveryStatus.SENT,
                raw_json=None, person_name=r["name"],
            )
            for i, r in enumerate(md.get("rows", ()), 1)
        )
        rendered = render_messages(
            rows, speaker=lambda row: row.sender_name or "?",
            body=lambda row: row.body, timestamp=lambda row: row.ts, now=now,
        )
        return {"output": rendered}
    except Exception as exc:                          # noqa: BLE001 — see module docstring
        return {
            "output": "",
            "environment_state": [
                EnvironmentState(name=NORA_ERROR, state=f"{type(exc).__name__}: {exc}"),
            ],
        }


class SubstringGrader(Evaluator):
    """A per-case `Contains`. `strands_evals.evaluators.deterministic.
    Contains` takes one fixed `value` at construction, so one instance
    cannot check a different expected substring for each case in a suite —
    which S5 needs, since every case expects a different rendered age.
    `Case.expected_output` carries it instead.
    """

    def evaluate(self, data: EvaluationData) -> list[EvaluationOutput]:
        error = state_of(data, NORA_ERROR)
        if error:
            return [EvaluationOutput(
                score=0.0, test_pass=False, reason=f"the case did not run: {error}",
                label=ERROR,
            )]
        expected = str(data.expected_output or "")
        actual = str(data.actual_output or "")
        if not expected:
            return [EvaluationOutput(
                score=0.0, test_pass=False, reason="case names no expected_output to find",
                label=ERROR,
            )]
        found = expected in actual
        return [EvaluationOutput(
            score=1.0 if found else 0.0, test_pass=found,
            reason=f"{expected!r} {'found' if found else 'NOT found'} in the rendered transcript",
            label=PASS if found else FAIL,
        )]

    async def evaluate_async(self, data: EvaluationData) -> list[EvaluationOutput]:
        return self.evaluate(data)


class IdempotenceGrader(Evaluator):
    """S6 — *a second identical pass writes nothing new*
    (`10-the-scenario-map.md` §3). Only meaningful with `Spec.repeat > 1` —
    a case run without a repeat carries no `_2` signals at all and is
    ungradable here, the same rule every other grader in this module follows
    for a signal it cannot find.

    `written`/`changed` are already per-call deltas (`dream()` diffs its own
    notebook `before`/`after`), so the repeat run's own values ARE the
    answer for those two. `staged` is an absolute snapshot, not a diff, so
    it needs both runs' counts compared.

    **Scoped to notes/staging, not room speech** (review, 2026-09-19): this
    reads `written`/`changed`/`staged` only. `dreamer()`
    (`evals/rebuilt/entry.py`) never populates `Outcome.said` at all — the
    decide pass writes notes and reminders, not room messages — so there is
    no `said` signal here to check, and "writes nothing new" means exactly
    the notes/staging surface, not a broader claim about speech.
    """

    def evaluate(self, data: EvaluationData) -> list[EvaluationOutput]:
        error = state_of(data, NORA_ERROR)
        if error:
            return [self._out(ERROR, 0.0, f"the case did not run: {error}")]

        written_2 = state_of(data, NORA_WRITTEN_2)
        if written_2 is None:
            return [self._out(ERROR, 0.0, "no repeat run — Spec.repeat must be > 1")]

        # Found by review, 2026-09-19: the repeat run's OWN error was never
        # checked. Entry points fail closed by returning `Outcome(error=...)`
        # rather than raising, so a repeat run that broke comes back with
        # written=0/changed=()/staged=() and no exception — indistinguishable
        # from a clean, idempotent no-op unless this is checked separately
        # from the first run's `NORA_ERROR`.
        error_2 = state_of(data, NORA_ERROR_2)
        if error_2:
            return [self._out(ERROR, 0.0, f"the repeat run did not run cleanly: {error_2}")]

        changed_2 = state_of(data, NORA_CHANGED_COUNT_2) or 0
        staged_1 = state_of(data, NORA_STAGED_COUNT) or 0
        staged_2 = state_of(data, NORA_STAGED_COUNT_2) or 0

        problems = []
        if written_2:
            problems.append(f"wrote {written_2} row(s) on the repeat")
        if changed_2:
            problems.append(f"changed {changed_2} note(s) on the repeat")
        if staged_2 != staged_1:
            problems.append(f"staged count moved {staged_1} -> {staged_2}")

        if problems:
            return [self._out(FAIL, 0.0, "; ".join(problems))]
        return [self._out(PASS, 1.0, "the repeat pass added nothing")]

    async def evaluate_async(self, data: EvaluationData) -> list[EvaluationOutput]:
        return self.evaluate(data)

    @staticmethod
    def _out(verdict: str, score: float, reason: str) -> EvaluationOutput:
        return EvaluationOutput(
            score=score, test_pass=(verdict == PASS), reason=reason, label=verdict,
        )


class AbsenceGrader(Evaluator):
    """Layer 4 — *correctly said nothing* vs *nothing happened because it broke*.

    The one grader no SDK supplies. Every built-in judges `actual_output`, and
    `Equals(value="")` is not an instrument here because **a crash produces the
    same empty string**.

    `04-the-convention` §5.1 measured why the signal has to come from the task:
    with no provider reachable, three of four entry points return a VERDICT
    rather than an error — the gate fails closed to `no_go`, a failed turn
    writes no row and reads as silence, `Dispatcher.on_message` returns `None`.
    There is no shared fail-closed signal because the DEPLOYMENT does not offer
    one, so each entry point carries its own and this reads it off
    `environment_state`.

    `expect` comes from `Case.metadata["expect"]`, which the SDK does carry.
    """

    def evaluate(self, data: EvaluationData) -> list[EvaluationOutput]:
        error = state_of(data, NORA_ERROR)
        expect = (data.metadata or {}).get("expect", "either")
        said = state_of(data, NORA_SAID) or 0
        admitted = state_of(data, NORA_ADMITTED)

        if error:
            return [self._out(ERROR, 0.0, f"the case did not run: {error}")]

        # The `gate` entry point never writes room speech at all — `admitted`
        # IS its verdict, and `said` is permanently `0` for it. Every other
        # entry point leaves `admitted` at `None` (never ran a gate), so
        # `said` is still the right signal there. Reading `said` alone for a
        # gate case graded every one of them as silence, verdict ignored.
        spoke = bool(admitted) if admitted is not None else bool(said)
        detail = f"admitted={admitted}" if admitted is not None else f"{said} row(s)"
        if expect in ("silence", "no_reply", "none", "no_go"):
            return [
                self._out(PASS, 1.0, "said nothing, as the case expects")
                if not spoke
                else self._out(FAIL, 0.0, f"expected silence and got {detail}")
            ]
        if expect in ("speak", "reply", "go"):
            return [
                self._out(PASS, 1.0, f"spoke, as the case expects ({detail})")
                if spoke
                else self._out(FAIL, 0.0, "expected a reply and said nothing")
            ]
        # `either` says both ANSWERS are acceptable; it does not say that not
        # answering is. Something must have happened, or it is ungradable —
        # EXCEPT for a gate, where `admitted=False` already IS an answer.
        #
        # Found by review, 2026-09-19, one branch over from the fix
        # `747348bd` made: for a gate case (`admitted is not None`), both
        # `True` and `False` are the gate having run and decided — there is
        # no third "nothing happened" reading distinct from those two,
        # because a gate that truly failed to run reports through
        # `NORA_ERROR`, already checked above. Reading `spoke=False` here as
        # "ungradable" scored a correct, successful refusal as ERROR.
        if admitted is not None:
            return [self._out(PASS, 1.0, f"the gate decided ({detail})")]
        return [
            self._out(PASS, 1.0, f"something happened ({detail})")
            if spoke
            else self._out(ERROR, 0.0, "nothing ran and nothing was said")
        ]

    async def evaluate_async(self, data: EvaluationData) -> list[EvaluationOutput]:
        return self.evaluate(data)

    @staticmethod
    def _out(verdict: str, score: float, reason: str) -> EvaluationOutput:
        # `test_pass` is set for the field's sake only. Read `label`.
        return EvaluationOutput(
            score=score, test_pass=(verdict == PASS), reason=reason, label=verdict,
        )


class ContextGrader(Evaluator):
    """S4 — *the blocks she needs were rendered non-empty*
    (`10-the-scenario-map.md` §3). Deterministic: no model call, reads only
    what `build_prefill` assembled, off `NORA_BLOCKS_EMPTY`.

    `Case.metadata["must_render"]` names the block(s) THIS case seeded data
    for and therefore expects non-empty — `_BLOCKS`' own names in
    `nora/agent/prefill.py`: `self_card`, `peer_card`, `peer_history`,
    `people_in_window`, `notes`, `loop_decisions`, `standing`,
    `group_transcript`. A case naming none is ungradable by this grader, not
    a silent pass — the same rule `AbsenceGrader` follows for an `expect` it
    does not recognise.
    """

    def evaluate(self, data: EvaluationData) -> list[EvaluationOutput]:
        error = state_of(data, NORA_ERROR)
        if error:
            return [self._out(ERROR, 0.0, f"the case did not run: {error}")]

        must_render = tuple((data.metadata or {}).get("must_render", ()))
        if not must_render:
            return [self._out(ERROR, 0.0, "case names no `must_render` block to check")]

        empty_raw = state_of(data, NORA_BLOCKS_EMPTY)
        if empty_raw is None:
            return [self._out(
                ERROR, 0.0,
                "no agent.prefill span was captured — the entry point may never "
                "call build_prefill (gate, dreamer), or the capture itself failed",
            )]
        empty = {name for name in str(empty_raw).split(",") if name}
        missing = [name for name in must_render if name in empty]
        if missing:
            return [self._out(
                FAIL, 0.0, f"expected non-empty and got empty: {', '.join(missing)}",
            )]
        return [self._out(PASS, 1.0, f"all of {must_render} rendered non-empty")]

    async def evaluate_async(self, data: EvaluationData) -> list[EvaluationOutput]:
        return self.evaluate(data)

    @staticmethod
    def _out(verdict: str, score: float, reason: str) -> EvaluationOutput:
        return EvaluationOutput(
            score=score, test_pass=(verdict == PASS), reason=reason, label=verdict,
        )


@dataclass(frozen=True)
class Reading:
    """What a report says once ERROR is taken out of the denominator."""

    passed: int
    failed: int
    errored: int

    @property
    def gradable(self) -> int:
        return self.passed + self.failed

    def __str__(self) -> str:
        return (
            f"{self.passed}/{self.gradable} graded, {self.errored} ERROR"
            if self.gradable
            else f"0/0 graded, {self.errored} ERROR — nothing was measured"
        )


def read(report: Any) -> Reading:
    """The gate's own reader. **Keys on `label`, never on `test_pass`.**

    MEASURED 2026-09-18: `label` survives in exactly one place —
    `EvaluationReport.detailed_results`, a `list[list[EvaluationOutput]]`. It is
    NOT on `report.cases` (those rows are the `EvaluationData`), and it is not
    reachable from `report.scores`, `report.test_passes` or
    `report.overall_score`. So the two fields a reader would reach for first are
    precisely the two that cannot tell a broken instrument from a bad answer,
    and `overall_score` averages both kinds of zero in one denominator.

    That is why this function exists rather than a one-line property read.
    """
    counts = {PASS: 0, FAIL: 0, ERROR: 0}
    for outputs in report.detailed_results:
        for out in outputs:
            label = getattr(out, "label", None)
            counts[label if label in counts else ERROR] += 1
    return Reading(passed=counts[PASS], failed=counts[FAIL], errored=counts[ERROR])

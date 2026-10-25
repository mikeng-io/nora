"""The SDK-native spine — layers 3 and 4, and nothing else.

The basis: the evaluation suite IS
`strands-agents-evals`. Layer 1 (corpus, harness, report, caching) and layer 2
(content grading) are the SDK's and are not written here. This module holds
only what no SDK can supply:

- **layer 3, the subject** — `nora_task`, which obtains the deployed Nora,
  isolates the case, drives one entry point, and *never raises*;
- **layer 4, the absence** — `AbsenceGrader`, because many cases expect
  her to say nothing and every built-in evaluator judges `actual_output`.

**THE ERROR CHANNEL, and why it is where it is.** In the installed SDK
(1.0.3) `Experiment._run_task_async` reads exactly five keys off a task's dict
— `output`, `trajectory`, `interactions`, `environment_state`, `input`. A
`metadata` key is **silently dropped** (the `metadata` on the `EvaluationData`
is the CASE's, never the task's), so a failure reason cannot ride there.
`environment_state` is the surviving channel and is what `NORA_*` below rides
in.

**Why the task must never raise.** `Experiment._worker` wraps the task in
`except Exception` and writes `test_pass=False, score=0` for **every evaluator
in the list, calling none of them**. An exception therefore arrives at the
report dressed as a considered FAIL, which is rule 3 (*ERROR is not FAIL and is
never PASS*) broken by the library rather than by us. So every failure is
caught here, scrubbed, and returned as data.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
import os
import re
import shutil
import tempfile
import tomllib
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
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

from evals import pseudonyms
from evals.recall import recall_facts
from evals.situation import (
    LIVE_HANDLE_STANDINS,
    deployment_handles,
    deployment_networks,
    facts,
    shown_where,
    varied_transport,
)
from evals.subject_config import FULL_RESTORE_OVERRIDES, NO_GEOCODER_ENV
from evals.terminal import safe_print as print
from nora.domain.block_types import BLOCK_TYPES
from nora.domain.note_body import (
    ATTRIBUTED,
    SECTIONS,
    NoteBody,
    NoteBodyError,
    Shown,
    ShownMessage,
    parse_note_body,
)
from nora.subject import (
    DrawWorkspace,
    IndexCache,
    SubjectSpec,
    draw_workspace,
    index_sandbox,
    new_run_id,
    obtain,
    restored_room,
    sandbox_as_of,
    sandbox_database,
    sandbox_from_snapshot,
    seed_documents,
    seed_lessons,
)
from nora.subject.obtain import RoomNotInPolicyError
from nora.subject.sandbox_roster import recorded_rosters
from nora.subject.transport import held_states, hold_transports

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
#: writes room speech at all — `AbsenceGrader` reading only `NORA_SAID` would
#: grade EVERY `gate` case as silence, verdict ignored.
NORA_ADMITTED = "nora.admitted"
#: `nora.agent.prefill.build_prefill`'s own `nora.prefill.blocks_empty`
#: attribute (a comma-joined list of block names whose body was empty),
#: carried through unmapped. `StrandsInMemorySessionMapper` (what
#: `TracedHandler` uses) drops any span whose `gen_ai.operation.name` is not
#: `chat`/`execute_tool`/`invoke_agent` — a Nora plumbing span like
#: `agent.prefill` never survives into a `Session`, mapped or not. So the
#: "blocks she needs were rendered non-empty" check reads the raw span directly via
#: `_prefill_capture()` rather than through the SDK's trajectory machinery —
#: the property in question is what `build_prefill` assembled, not anything
#: shaped like a model turn. Empty string for every entry point that never
#: calls `build_prefill` (`gate`, `dreamer`).
NORA_BLOCKS_EMPTY = "nora.blocks_empty"
#: What the SUBJECT's turn spent, read off the model-call spans this process
#: already captures for `agent.prefill` — NOT from an external collector.
#:
#: Eval runs force `[obs.tracing]` off, deliberately: a run's spans would land
#: in the deployment's own collector beside real ones. The cost of that is that
#: a truncated call is invisible, so the only way to ask "cut off at what?"
#: would be to change a ceiling and re-run. Reading the numbers HERE
#: needs no second Langfuse project, no credential, and cannot pollute
#: anything: `_prefill_capture()` already attaches an in-memory exporter to
#: this process's own provider, and Strands puts `gen_ai.usage.*` and
#: `finish_reason` on every model-call span.
#:
#: These describe the FIRST drive and exclude the judge, which is eval-layer
#: cost rather than the subject's.
NORA_CALLS = "nora.model_calls"
NORA_INPUT_TOKENS = "nora.input_tokens"
NORA_OUTPUT_TOKENS = "nora.output_tokens"
NORA_CACHED_TOKENS = "nora.cached_input_tokens"
#: Every distinct `finish_reason` the turn's calls reported, comma-joined.
#: `max_tokens` here is the truncation, named rather than inferred.
NORA_STOPS = "nora.stop_reasons"
#: Every tool the turn EXECUTED, in the order it ran — read off Strands'
#: `execute_tool` spans (`gen_ai.tool.name`), which is what the SDK itself
#: records, not what the reply claims. S2 routing (`ToolRouteGrader`) reads
#: this. A tuple, never `None` on a turn that ran: an empty tuple is a turn
#: that called nothing, which is a routing answer too.
NORA_TOOLS = "nora.tools_called"
#: What each executed tool RETURNED, in order, as `(name, text)`: read off the
#: `gen_ai.choice` event Strands puts on every `execute_tool` span. The
#: reporting criterion (`tool_status_reported`) grades the reply against these,
#: so the judge sees the result she saw, not a description of it.
NORA_TOOL_RESULTS = "nora.tool_results"
#: What each executed tool was CALLED WITH: `(name, input)` per call, in the
#: order they ran, the input as Strands serialised it onto the same
#: `execute_tool` span. `ToolRouteGrader` reads it for a case that grades an
#: argument (`metadata.args_include` / `args_exclude` / `args_date`), because
#: routing alone cannot tell «drew herself with her reference» from «drew an
#: empty room»: both are one `generate_image` call.
NORA_TOOL_INPUTS = "nora.tool_inputs"
#: The clock line the turn was shown (`nora.agent.prefill._now_line`), read
#: back off the first model call. A case that grades a date she had to work
#: out («this Saturday») is graded against the date SHE was given, not the
#: grader's own clock, which may be a different day by the time it runs.
NORA_SHOWN_NOW = "nora.shown_now"
#: What the model was TOLD about where she is on this draw, read back off the
#: first model call's own messages (`evals.situation.shown_where`):
#: `<presence>…</presence>`, `_where_line`'s sentence on a build without it,
#: or `None`. Carried on every draw so a before/after measurement proves its
#: arms differ from the run itself, not from a reading of the code.
NORA_SHOWN_WHERE = "nora.shown_where"
#: The transport tier as the sandbox HELD it (`nora.subject.transport`),
#: `{network: state}`, or `None` on a build with no `<presence>` reader —
#: where there is nothing to hold and the prompt carries no transport state.
NORA_TRANSPORT = "nora.transport_held"
#: `Outcome.written`/`len(.changed)`/`len(.staged)` off the FIRST drive —
#: the idempotence grader's input.
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
#: The repeat run's OWN `Outcome.error`.
#: Entry points return `Outcome(error=...)` rather than raising
#: (`evals/rebuilt/entry.py`'s fail-closed branches), so a repeat run that
#: failed closed comes back with `written=0, changed=(), staged=()` and no
#: exception at all — indistinguishable from "ran cleanly and added
#: nothing" unless this is carried and checked: "An Outcome with an error is
#: never graded" (`evals/rebuilt/domain.py`) holds for the repeat half too.
NORA_ERROR_2 = "nora.error_2"
#: `Outcome.changed`/`.judged`/`.checks_failed` — the three signals
#: `Outcome.meets()` needs beyond `admitted`/`said`/`written` to grade
#: `create`/`update`/`either` and a judge's verdict. `MeetsGrader`
#: reconstructs a real `Outcome` from these and delegates to the SAME
#: `.meets(expect)`, rather than reimplementing its eleven-vocabulary logic.
NORA_CHANGED = "nora.changed"
NORA_JUDGED = "nora.judged"
NORA_CHECKS_FAILED = "nora.checks_failed"
#: The character length of every body a dream pass WROTE (a staged candidate
#: whose status is `created`: a new note or an appended revision), one per
#: body, in no particular order. Printed on the grader's reason so `--reasons`
#: carries it per draw:
#: the body is what every later reader gets instead of the room's messages,
#: and a change to how the objective demonstrates one is measured on it.
NORA_BODY_CHARS = "nora.body_chars"
#: Per written body, in `NORA_BODY_CHARS`' order: `ok` when it parses under
#: the note-body schema (`nora.domain.note_body.parse_note_body`) against the
#: draw's shown set, else the rule it broke. A READING printed on the grader's
#: reason as `body_schema=[...]`, not a verdict: bodies are prose until the
#: objective carries the schema, and a verdict here would fail every create
#: case. A case opts into it as a check with `body_schema: true`.
NORA_BODY_SCHEMA = "nora.body_schema"
#: The rule of each create the pass REFUSED for its body before staging
#: printed as `body_refused=[...]`. A refused body is never
#: written, so `body_schema` cannot see it: conformance is `ok` over written
#: plus refused, and this is the second half.
NORA_BODY_REFUSED = "nora.body_refused"
#: The standing corpus.
#: `NORA_POSITION` is the position a fold wrote (`Outcome.position`), what
#: `MeetsGrader` needs for `expect: fold`. `NORA_SHOWN` is what the subject's
#: model call was given, verbatim — `--print-input` prints it, so two arms are
#: diffed before a model is paid for. `NORA_JUDGED_WHY` is the judge's own
#: rationale, so a FAIL says why without another run.
NORA_POSITION = "nora.position"
#: The `working_set` the SAME fold wrote beside `position` — `Outcome.working_set`, a
#: `WorkingSetFinding` tuple. Carried as its own state, never folded into `NORA_POSITION`'s
#: string: the judge and `--positions` both have to show a reader which of the two things they
#: are reading, and a single string cannot label its own halves.
NORA_WORKING_SET = "nora.working_set"
NORA_SHOWN = "nora.shown"
NORA_JUDGED_WHY = "nora.judged_why"
#: The Honcho workspace this draw's subject read and wrote: a workspace of
#: the draw's own, created empty and deleted after. Carried so a
#: reading names the memory it was taken against.
NORA_MEMORY_WORKSPACE = "nora.memory_workspace"

#: This process's run, the middle of every draw workspace's name
#: (`<deployment>-eval-<RUN_ID>-<case>-<nonce>`). Two arms run as two
#: processes and so never share one.
RUN_ID = new_run_id()

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


async def _close(subject: Any) -> None:
    """Best-effort teardown. Never raises: a case's RESULT must not depend on
    whether its cleanup worked, and a teardown error is not a reading."""
    for shut in (
        subject.deps.background.cancel_all(),
        subject.deps.audit.aclose(),
        subject.deps.client.close(),
    ):
        with contextlib.suppress(Exception):
            await shut


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
    provider and both receive every span. This holds in either
    construction order (`_prefill_capture()` first or `TracedHandler()` first).
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

    **`None` and `""` are different facts, and must not be collapsed**:
    `""` means "a span was captured and no
    block was empty" — a real PASS-worthy fact. No span at all means
    `build_prefill` never ran, OR ran but the span was lost (provider
    ordering, a sampler, the entry point never calls it at all —
    `gate`/`dreamer`) — an ungradable fact. `ContextGrader` reading both as
    "nothing was empty" would turn every one of those failure modes into a
    silent PASS, exactly the ERROR-as-PASS substitution this whole bundle
    exists to refuse.
    """
    for finished in exporter.get_finished_spans():
        if finished.name == "agent.prefill":
            return str((finished.attributes or {}).get("nora.prefill.blocks_empty", ""))
    return None


#: What every case driven in this process has spent, appended by `_drive`.
#:
#: A module-level accumulator and not a field on the report, because the SDK's
#: report does NOT carry `environment_state`: `Experiment` builds each row from
#: `result["case"]` alone, so the graders see the spend and the report never
#: does.
#:
#: Safe as process state for the reason `nora_task`'s own docstring already
#: gives: `TracedHandler` forces `max_workers=1`, so cases are driven one at a
#: time, in this process.
_SPEND: list[dict[str, Any]] = []

#: Every draw's Honcho workspace in this process, with what its create and
#: delete cost. Process state for `_SPEND`'s reason.
_DRAWS: list[DrawWorkspace] = []

#: The graph surface's built sandbox indexes, for this run's later draws
#: (`nora.subject.sandbox_index.IndexCache`).
_INDEX_CACHE = IndexCache()


def drain_draws() -> list[DrawWorkspace]:
    """The draw workspaces since the last drain, and reset."""
    out = list(_DRAWS)
    _DRAWS.clear()
    return out


def drain_spend() -> dict[str, Any]:
    """Totals for the cases driven since the last drain, and reset.

    Drained per corpus rather than accumulated for the run, so a corpus's
    line describes that corpus.
    """
    calls = sum(int(x[NORA_CALLS]) for x in _SPEND)
    inp = sum(int(x[NORA_INPUT_TOKENS]) for x in _SPEND)
    out = sum(int(x[NORA_OUTPUT_TOKENS]) for x in _SPEND)
    cached = sum(int(x[NORA_CACHED_TOKENS]) for x in _SPEND)
    stops: list[str] = []
    for x in _SPEND:
        for reason in str(x[NORA_STOPS] or "").split(","):
            if reason and reason not in stops:
                stops.append(reason)
    turn = {
        key: sum(int(x.get(f"turn.{name}") or 0) for x in _SPEND)
        for key, name in (("calls", NORA_CALLS), ("input", NORA_INPUT_TOKENS),
                          ("cached", NORA_CACHED_TOKENS), ("output", NORA_OUTPUT_TOKENS))
    }
    turn["fresh"] = turn["input"] - turn["cached"]
    turn["turns"] = sum(1 for x in _SPEND if f"turn.{NORA_CALLS}" in x)
    unknown_usage = sum(int(x[NORA_CALLS]) for x in _SPEND if x.get("usage_known") is False)
    _SPEND.clear()
    return {
        "turn": turn,
        "calls": calls, "input": inp, "output": out, "cached": cached,
        "fresh": inp - cached, "stops": stops,
        # NAMED, not inferred: a `max_tokens` finish reason is the truncation.
        "truncated": [r for r in stops if "max_token" in r or "length" in r],
        **({"unknown_usage_calls": unknown_usage} if unknown_usage else {}),
    }


def _under_agent(spans: Sequence[Any], agent: str) -> list[Any]:
    """Every span in `spans` reachable, through `parent`, from an
    `invoke_agent` span named `agent` — THIS TURN's own calls, without a
    background pass the same drive spawns under a DIFFERENT agent (the
    standing fold's) or under NO agent at all: the relevance gate's own
    model call (`nora.ingest.relevance_gate.RelevanceGate`, "never
    constructs an agent" by its own docstring) goes through
    `nora.agent.telemetry.start_raw_model_span` — the same `chat` span shape
    a real turn's call has, `gen_ai.operation.name == "chat"` included — but
    with no `invoke_agent` ancestor to match, so it is never "under" any
    named agent and this walk excludes it by construction rather than by
    guessing at a span name.

    Shared by `_model_calls` (spend, per-turn) and `_shown_transcript`
    (the rendered window, per-turn) — both need "this turn's own
    calls, not the drive's other passes or its other model users."
    """
    by_id = {span.context.span_id: span for span in spans}
    roots = {
        span.context.span_id for span in spans
        if (span.attributes or {}).get("gen_ai.operation.name") == "invoke_agent"
        and (span.attributes or {}).get("gen_ai.agent.name") == agent
    }

    def _under(span: Any) -> bool:
        parent = span.parent
        while parent is not None:
            if parent.span_id in roots:
                return True
            up = by_id.get(parent.span_id)
            parent = up.parent if up is not None else None
        return False

    return [span for span in spans if _under(span)]


def _model_calls(exporter: InMemorySpanExporter, *, agent: str | None = None) -> dict[str, Any]:
    """What the model calls captured so far spent, and how they stopped.

    **One span per model call: `gen_ai.operation.name == "chat"`.** Counting
    every span carrying `gen_ai.usage.output_tokens` would be wrong: the
    enclosing `invoke_agent` span carries the AGENT's accumulated usage too, so
    every call would be counted twice and every agent's tokens once more on
    top. Matching the operation name is Strands' own convention
    (`_get_common_attributes`), not a span name.

    `agent`, when given, keeps only the calls made UNDER that agent's
    `invoke_agent` span (`_under_agent`) — the subject's own turn, without the
    background passes the same drive spawns. A per-TURN comparison is
    meaningless with them mixed in.
    """
    spans = exporter.get_finished_spans()
    if agent is not None:
        spans = _under_agent(spans, agent)
    calls = inp = out = cached = 0
    stops: list[str] = []
    for span in spans:
        attrs = span.attributes or {}
        if attrs.get("gen_ai.operation.name") != "chat":
            continue
        calls += 1
        inp += int(attrs.get("gen_ai.usage.input_tokens") or 0)
        out += int(attrs.get("gen_ai.usage.output_tokens") or 0)
        cached += int(attrs.get("gen_ai.usage.cache_read_input_tokens") or 0)
        reason = attrs.get("finish_reason")
        if reason and str(reason) not in stops:
            stops.append(str(reason))
    return {
        NORA_CALLS: calls, NORA_INPUT_TOKENS: inp, NORA_OUTPUT_TOKENS: out,
        NORA_CACHED_TOKENS: cached, NORA_STOPS: ",".join(stops),
    }


def _tools_called(exporter: InMemorySpanExporter) -> tuple[str, ...]:
    """The tools this turn executed, in start order (see `NORA_TOOLS`)."""
    spans = [
        span for span in exporter.get_finished_spans()
        if "gen_ai.tool.name" in (span.attributes or {})
    ]
    spans.sort(key=lambda span: span.start_time or 0)
    return tuple(str(span.attributes["gen_ai.tool.name"]) for span in spans)


def _tool_results(exporter: InMemorySpanExporter) -> tuple[tuple[str, str], ...]:
    """`(tool name, result text)` for each executed tool, in start order (see
    `NORA_TOOL_RESULTS`). A span whose result event is missing contributes
    `(name, "")`: an absent result is recorded as absent, never skipped."""
    spans = [
        span for span in exporter.get_finished_spans()
        if "gen_ai.tool.name" in (span.attributes or {})
    ]
    spans.sort(key=lambda span: span.start_time or 0)
    out = []
    for span in spans:
        text = ""
        for event in getattr(span, "events", ()) or ():
            attrs = event.attributes or {}
            if "message" in attrs:
                text = str(attrs["message"])
            elif "gen_ai.output.messages" in attrs:
                text = str(attrs["gen_ai.output.messages"])
        out.append((str(span.attributes["gen_ai.tool.name"]), text))
    return tuple(out)


#: Blocks `_shown_blocks` captures that `_judge_source` does NOT add as
#: recall context: `group_transcript` is already the source's first part
#: (`_shown_transcript`), and `tool_catalogue` describes her tools rather than
#: holding any record of the group, so neither can support a recalled claim.
_NOT_RECALL_CONTEXT = frozenset({"group_transcript", "tool_catalogue"})


def _judge_source(transcript: str | None, results: tuple[tuple[str, str], ...],
                  criteria: tuple[str, ...],
                  blocks: tuple[tuple[str, str], ...] = ()) -> str | None:
    """The source data a judge grades against — `transcript` is what
    `_shown_transcript` read back off the turn's own call, i.e. the window
    her turn actually rendered, never the whole seeded room. The tool
    results too when the case grades REPORTING (`tool_status_reported`),
    since that criterion is about what the results were worth.

    `blocks` (`_shown_blocks`) are the OTHER context blocks the same model
    call carried — notes, events, standing, culture, character… — and are
    read ONLY for `recall_grounded`: a recall grounded in a note she was shown
    is not a fabrication, and a judge that never saw the note would grade it as
    one. Every other criterion's source is byte-for-byte what it is without
    `blocks` (`test_the_judge_source_is_unchanged_for_every_other_criterion`).
    Never the doctrine: that is the system prompt, not a block, and the judge is
    handed it separately."""
    parts = [transcript] if transcript else []
    if "recall_grounded" in criteria:
        shown = [text for name, text in blocks if name not in _NOT_RECALL_CONTEXT]
        if shown:
            parts.append(
                "The other context blocks her turn was shown, exactly as rendered "
                "(each tag names its block):\n" + "\n\n".join(shown)
            )
    # `recall_grounded` too: «I couldn't check» is graded against what her
    # tools actually returned, so the judge has to see an `unavailable`.
    if (
        "tool_status_reported" in criteria or "lesson_applied" in criteria
        or "recall_grounded" in criteria
    ) and results:
        lines = [f"- {name}: {text}" for name, text in results
                 if name not in ("send_reply", "load_tools")]
        if lines:
            parts.append("Tool results this turn:\n" + "\n".join(lines))
    return "\n\n".join(parts) or None


#: Where Strands puts a tool call's INPUT (`Tracer.start_tool_call_span`): the legacy
#: convention emits a `gen_ai.tool.message` event whose `content` is the serialised input; the
#: latest one records `gen_ai.input.messages` as an event or a span attribute. The call's
#: OUTPUT goes under other names, so a result's `note` is never read as something she passed.
_TOOL_INPUT_EVENT = "gen_ai.tool.message"
_INPUT_MESSAGES = "gen_ai.input.messages"


def _tool_inputs(exporter: InMemorySpanExporter) -> tuple[tuple[str, str], ...]:
    """`(tool, input)` per executed tool, in start order (see `NORA_TOOL_INPUTS`).
    An input Strands did not record is an empty string, which a grader must
    read as «not captured», never as «passed nothing»."""
    spans = [
        span for span in exporter.get_finished_spans()
        if "gen_ai.tool.name" in (span.attributes or {})
    ]
    spans.sort(key=lambda span: span.start_time or 0)
    out: list[tuple[str, str]] = []
    for span in spans:
        parts: list[str] = []
        raw = (span.attributes or {}).get(_INPUT_MESSAGES)
        if isinstance(raw, str):
            parts.append(raw)
        for event in span.events or ():
            attrs = event.attributes or {}
            if event.name == _TOOL_INPUT_EVENT and isinstance(attrs.get("content"), str):
                parts.append(str(attrs["content"]))
            if isinstance(attrs.get(_INPUT_MESSAGES), str):
                parts.append(str(attrs[_INPUT_MESSAGES]))
        out.append((str(span.attributes["gen_ai.tool.name"]), "\n".join(parts)))
    return tuple(out)


_CLOCK_LINE = re.compile(
    r"Right now it is (?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday) "
    r"\d{1,2} [A-Z][a-z]+ \d{4}, \d{2}:\d{2}[^\n]*?\.(?=\s|\\n|$)"
)


def _shown_now(exporter: InMemorySpanExporter) -> str | None:
    """The clock line the turn's first model call carried, or None when no
    call was captured or none carried one (see `NORA_SHOWN_NOW`)."""
    chats = [
        span for span in exporter.get_finished_spans()
        if (span.attributes or {}).get("gen_ai.operation.name") == "chat"
    ]
    for span in sorted(chats, key=lambda s: s.start_time or 0):
        values = [*(span.attributes or {}).values()]
        for event in span.events or ():
            values.extend((event.attributes or {}).values())
        for raw in values:
            if isinstance(raw, str):
                found = _CLOCK_LINE.search(raw)
                if found:
                    return found.group(0)
    return None


#: `<group_transcript window="N" description="…">…rows…</group_transcript>`
#: (`nora.domain.blocks.ContextBlock.render`, `nora.agent.prefill.
#: _transcript_block`) — the body is what `_transcript_block` names group 1.
#: A body row is XML-escaped (R-13/R-13b), so a real `</group_transcript>`
#: cannot appear inside one and end the match early; `re.DOTALL` lets `.`
#: cross the row boundaries a multi-row window always has.
#:
#: Runs on the DECODED text (`_texts_in`), never on the raw span attribute — Strands writes
#: that as `json.dumps(..., ensure_ascii=False)` (`strands.telemetry.tracer.serialize`), so a
#: real body's newline is the two characters `\` `n` there, not the one this pattern's
#: `\n?`/`re.DOTALL` assume. Matching the raw JSON text would find `</group_transcript>` a line
#: early and leave a literal `\n` at each end of the captured group — the exact shape
#: `_shown_now`'s own clock-line pattern already hedges for with `(?=\s|\\n|$)`, which this
#: module's regexes are not otherwise immune to just because they look like plain text.
_GROUP_TRANSCRIPT = re.compile(
    r'<group_transcript\b[^>]*>\n?(.*?)\n?</group_transcript>', re.DOTALL,
)


def _strings_in(obj: Any) -> list[str]:
    """Every string leaf in a JSON-decoded value, depth first — a content
    block's `text`, a GenAI-latest message's `parts[].content`, or anything
    else a future Strands convention nests a string under. Not modelled by
    name on purpose: `_texts_in`'s docstring has the reason."""
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [s for v in obj.values() for s in _strings_in(v)]
    if isinstance(obj, list):
        return [s for v in obj for s in _strings_in(v)]
    return []


def _texts_in(raw: str) -> list[str]:
    """The real, unescaped text a captured span attribute VALUE carries.

    Strands serialises a message's content (or, under the latest GenAI
    convention, a whole `input_messages` list) through `json.dumps(...,
    ensure_ascii=False)` before writing it onto the span
    (`strands.telemetry.tracer.serialize`/`_add_event_messages`) — so the
    attribute VALUE is a JSON STRING, and a body's newline, quote or CJK
    character is exactly what `json.loads` turns back into the one the model
    was actually sent. This decodes it and walks every string leaf
    (`_strings_in`) rather than modelling the legacy `[{"text": "…"}]` and
    latest `[{"role", "parts": [{"type": "text", "content": "…"}]}]` shapes
    by name, because the ONE property both share — the block's true text is
    a string somewhere in the decoded tree — is all a caller here needs, and
    modelling the shape is how a convention change breaks this silently.

    `raw` that is not JSON — `gen_ai.operation.name`'s own value, a `finish_
    reason`, or a plain-string test fixture — is
    returned as itself: the search this feeds already tolerates a value with
    nothing to find, and a caller that built a fixture with a bare Python
    string (real newlines, real quotes) still gets searched correctly.
    """
    try:
        decoded = json.loads(raw)
    except (json.JSONDecodeError, ValueError, RecursionError):
        return [raw]
    return _strings_in(decoded)


def _shown_transcript(
    exporter: InMemorySpanExporter, *, agent: str | None = None,
) -> str | None:
    """The `<group_transcript>` block's body exactly as the turn's own model
    call carried it — the window HER TURN rendered, quote-chase growth
    included, read back off the call rather than re-derived from
    `context_window` (`sandbox_from_snapshot`'s `Sandboxed.transcript` is every
    row up to the instant, unbounded, so a judge could be handed a line she was
    never shown).

    **`agent` scopes this to the TURN's own calls (`_under_agent`), and that
    is load-bearing, not a nicety.** The relevance gate renders its OWN
    `<group_transcript>` too (`nora.ingest.relevance_gate.RelevanceGate`,
    on a raw model call — see `_under_agent`'s docstring), and `[participation]`
    and `[relevance_gate]` are one subsystem (`ENTRY_POINTS["participation"]`'s
    own comment): a `participation` drive can carry the gate's admission call
    BEFORE the turn's own reply call, both as `chat`-operation spans. Scanning
    every captured span with no filter, earliest first, would hand the judge
    the GATE's window — read at a different bound, answering a different
    question — instead of the turn's. Caller passes the subject's own agent
    name (`subject.cfg.agent.name`, the same value `_model_calls(exporter,
    agent=...)` already scopes spend to); `None` scans unfiltered, which is
    only correct for an entry point with no gate ahead of it or a test fixture
    with no `invoke_agent` span at all.

    Same scan `_shown_now` runs for the clock line otherwise: every string on
    every captured `chat`-operation span (decoded through `_texts_in`),
    attributes then events, earliest span first. The block is
    assembled once per turn and rides on every model call the turn makes, so
    the first match, within the scoped spans, is the turn's own render.

    **Byte-identical to the block `ContextBlock.render()` produced**, proven
    (not merely claimed) by `tests/unit/test_tool_route_grader.py::
    test_shown_transcript_decodes_the_real_strands_serialized_span`, which
    round-trips a realistic multi-row window — multi-line body, a quote,
    CJK, an attacker row whose body is a literal `</group_transcript>` —
    through the real renderer, the real `strands.telemetry.tracer.serialize`,
    and this function, and asserts equality.

    `None` when no call was captured, or the scoped calls carried no such
    block — a room whose window rendered with an empty body and was dropped
    before it reached the model (`nora.domain.blocks.assemble` drops an empty
    block entirely), or the span capture itself breaking. `_drive` treats
    `None` here as an ERROR draw when the case was going to be judged
    (`_judge_source`'s call site) rather than a silent blind grade — see that
    call site's own comment for why `source=None` is not, on its own,
    fail-closed (DEC-75).
    """
    spans = exporter.get_finished_spans()
    if agent is not None:
        spans = _under_agent(spans, agent)
    chats = [
        span for span in spans
        if (span.attributes or {}).get("gen_ai.operation.name") == "chat"
    ]
    for span in sorted(chats, key=lambda s: s.start_time or 0):
        values = [*(span.attributes or {}).values()]
        for event in span.events or ():
            values.extend((event.attributes or {}).values())
        for raw in values:
            if not isinstance(raw, str):
                continue
            for text in _texts_in(raw):
                found = _GROUP_TRANSCRIPT.search(text)
                if found:
                    return found.group(1)
    return None


#: A top-level context block as `ContextBlock.render()` writes it: an opening
#: tag named for a registered block type, the body on its own lines, the
#: matching close. Names come from `BLOCK_TYPES` (every block the prefill can
#: serve), longest first so `person` never wins over `person_account`. Bodies
#: are XML-escaped (R-13), so a member's text cannot close one early, and an
#: element nested inside a block's body starts AFTER that block's own opening
#: tag, so `finditer` consumes the outer block first and never yields the
#: nested one on its own.
_CONTEXT_BLOCK = re.compile(
    r"<(" + "|".join(sorted(map(re.escape, BLOCK_TYPES), key=len, reverse=True))
    + r")\b[^>]*(?<!/)>\n.*?\n</\1>",
    re.DOTALL,
)


def _shown_blocks(
    exporter: InMemorySpanExporter, *, agent: str | None = None,
) -> tuple[tuple[str, str], ...] | None:
    """`(block name, block as rendered)` for every context block in the message
    that carried the turn's `<group_transcript>` — `build_prefill`'s volatile
    message, the clock line plus `assemble(declared)` — read off the turn's own
    first model call exactly as `_shown_transcript` reads the window, and
    scoped by `agent` for the same reason (the relevance gate renders a
    `<group_transcript>` of its own).

    Anchored on the transcript block rather than on any message with a tag in
    it: the tag message (`<tag_message>`, `<replying_to>`) is the judge's
    `asked`, and the system prompt is the doctrine, which the judge gets on its
    own. `None` when no call carried the window — the caller checks the names
    against `_blocks_rendered` too, so a block the prefill built and this
    parse missed is an ERROR rather than a judge grading without it.
    """
    spans = exporter.get_finished_spans()
    if agent is not None:
        spans = _under_agent(spans, agent)
    chats = [
        span for span in spans
        if (span.attributes or {}).get("gen_ai.operation.name") == "chat"
    ]
    for span in sorted(chats, key=lambda s: s.start_time or 0):
        values = [*(span.attributes or {}).values()]
        for event in span.events or ():
            values.extend((event.attributes or {}).values())
        for raw in values:
            if not isinstance(raw, str):
                continue
            for text in _texts_in(raw):
                if _GROUP_TRANSCRIPT.search(text):
                    return tuple(
                        (found.group(1), found.group(0))
                        for found in _CONTEXT_BLOCK.finditer(text)
                    )
    return None


def _blocks_rendered(exporter: InMemorySpanExporter) -> tuple[str, ...] | None:
    """`nora.prefill.blocks_rendered` off the first `agent.prefill` span — the
    blocks `build_prefill` itself says it served with a body — or `None` when
    no such span was captured (`_blocks_empty` has why `None` is its own fact)."""
    for finished in exporter.get_finished_spans():
        if finished.name == "agent.prefill":
            raw = str((finished.attributes or {}).get("nora.prefill.blocks_rendered", ""))
            return tuple(name for name in raw.split(",") if name)
    return None


def _recall_blocks_problem(
    blocks: tuple[tuple[str, str], ...] | None, rendered: tuple[str, ...] | None,
) -> str | None:
    """Why a `recall_grounded` draw cannot be judged on what she was shown, or
    `None` when it can. Fail closed: a judge that misses a block she read
    grades a grounded recall as fabricated, which is the defect this exists
    to end — so an uncaptured or incomplete set is an ERROR, never a blind
    grade."""
    if blocks is None:
        return ("no context blocks were captured off the turn's own model call; "
                "a recall_grounded draw is not judged without them")
    if rendered is None:
        return ("no agent.prefill span was captured, so the blocks she was shown "
                "cannot be checked complete; a recall_grounded draw is not judged")
    missing = sorted(set(rendered) - {name for name, _ in blocks})
    if missing:
        return (f"the prefill rendered {missing} but the turn's model call was not "
                "read back with them; a recall_grounded draw is not judged without them")
    return None


_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def date_shown(clock_line: str) -> datetime | None:
    """The date a clock line states, or None if it states none."""
    found = re.search(r"(\d{1,2} [A-Z][a-z]+ \d{4})", clock_line)
    if found is None:
        return None
    try:
        return datetime.strptime(found.group(1), "%d %B %Y")
    except ValueError:
        return None


def this_weekday(today: datetime, weekday: str) -> datetime:
    """«This Saturday» from `today`: the next one, or today if today is it."""
    ahead = (_WEEKDAYS.index(weekday.lower()) - today.weekday()) % 7
    return today + timedelta(days=ahead)


def states_the_date(text: str, day: datetime) -> bool:
    """Whether `text` writes `day` out absolutely, in any of the usual forms:
    «26 September», «September 26», «Sep 26», an ISO date, 9月26日, 26/9."""
    month, mon, d, m = day.strftime("%B"), day.strftime("%b"), day.day, day.month
    # «Sept» as well as «Sep»: the abbreviation, then any letters.
    name = rf"(?:{month}|{mon}[a-z]*)"
    forms = (
        rf"(?<!\d){d}(?:st|nd|rd|th)?\s+(?:of\s+)?{name}\b",
        rf"\b{name}\.?\s+{d}(?!\d)",
        re.escape(day.strftime("%Y-%m-%d")),
        rf"(?<!\d){m}\s*月\s*{d}\s*日",
        rf"(?<!\d){d}/{m}(?!\d)",
    )
    return any(re.search(form, text, re.IGNORECASE) for form in forms)


@dataclass(frozen=True)
class Spec:
    """What the spine needs that the SDK's `Case` does not carry."""

    #: The deployment's CLONE — `nora_eval` on the deploy host — read and
    #: never written. Every case composes against a sandbox database of its
    #: own cloned beside it (`nora.subject.sandbox_database`), and the
    #: moment it names is read out of this one. NOT the deployment's live
    #: database: `python -m evals` refuses a name that is not a clone's.
    source_url: str
    entry: str = "participation"
    overrides: dict[str, Any] | None = None
    #: Idempotence: drive the SAME subject
    #: through the SAME case `repeat` times, at the SAME `now_ms`, and carry
    #: the LAST run's `written`/`changed`/`staged` separately from the
    #: first's — no re-`obtain()` between runs, unlike every other case,
    #: because idempotence needs run 2 to see what run 1 actually left.
    repeat: int = 1
    #: Judging: "did she reply" is structural;
    #: "was the reply right" needs a model that did not produce it —
    #: `evals.rebuilt.grading.Judge`, built ONCE per experiment batch (its
    #: own docstring), not per case. `None` skips judging. Typed `Any` to
    #: avoid importing `evals.rebuilt.grading` (and its `openai` dependency)
    #: at module load for every caller that never judges.
    judge: Any = None
    #: The roster and the room set every subject composes with — the
    #: DEPLOYMENT'S OWN `identity.toml` and `policy.toml`, verbatim, read
    #: from `$NORA_DEPLOYMENT_DIR` when `None`. A test that reconstructs a
    #: room of its own states its own pair; an eval run never does.
    identity: str | None = None
    policy: str | None = None
    #: An already-migrated blank database beside the source to clone each
    #: sandbox from (`sandbox_database`'s own). `None` builds one per source.
    template: str | None = None
    #: The DEPLOYMENT's `config.toml` text — read for the networks it runs and
    #: the handles it declares, which is what the sandbox's transport tier is
    #: held to and what a presence judge grades against. `None` reads it from
    #: `$NORA_DEPLOYMENT_DIR` beside the policy and roster. NOT the subject's
    #: config: the sandbox turns Discord and WhatsApp off, the deployment
    #: does not.
    deployment_config: str | None = None
    #: How the sandbox is seeded. `False` (every corpus but the ones
    #: `evals.scenarios.FULL_RESTORE_CORPORA` names) is the snapshot path:
    #: `sandbox_database`, then `sandbox_from_snapshot`'s four tiers written
    #: through the stores. `True` is the FULL RESTORE: `sandbox_as_of` copies
    #: every restorable store as of the instant, ids and all (messages with
    #: their `media_json`, `media_objects`, events, memos, presence, the
    #: Rollup), and the subject is composed against that database with
    #: `evals.subject_config.FULL_RESTORE_OVERRIDES` on. Per corpus, never
    #: per case: a corpus is a suite about one assembly.
    full_restore: bool = False
    #: The memory surface this run's cases were selected for (DEC-316,
    #: `evals.scenarios.MEMORY_SURFACES`): `graph` or `honcho`, or None for a
    #: caller that selected none. A subject composed on the OTHER surface is
    #: refused per case, never graded: the case filter and the config would
    #: be measuring two different Noras.
    memory_surface: str | None = None


def refuse_a_memory_surface_mismatch(selected: str | None, enabled: bool) -> None:
    """Raise when the cases were selected for one memory surface and the
    subject composed the other (DEC-316): the case filter and the config
    would be measuring two different Noras, and a case graded so is an
    ERROR, never a reading. `selected` None: the caller selected none."""
    if selected is not None and enabled != (selected == "graph"):
        raise ValueError(
            f"the cases were selected for the {selected} memory surface, and the "
            f"subject composed [memory_index] enabled = {enabled}: the case filter "
            "and the config disagree (DEC-316). Resolve the arm from the "
            "generated config, or pass --memory-index."
        )


#: Where the deployment's policy and roster live. Copied verbatim into each
#: case's own temp workdir, NEVER into the repo — `policy.toml` and
#: `identity.toml` are gitignored precisely because they are the PII (real
#: names, phone numbers, real room ids), and that does not change because an
#: eval wants them.
#:
#: Read from the deployment rather than authored: an authored roster drifts
#: from the real one, and faking the NAMES while the bodies keep them is worse
#: than either — message bodies name roster members outright, so a faked
#: roster makes the room talk about people who are not in it.
_DEPLOYMENT_DIR = Path(
    os.environ.get("NORA_DEPLOYMENT_DIR")
    or str(Path.home() / "workspace" / "nora")
)


@lru_cache(maxsize=1)
def _deployment_policy() -> str:
    return _read_deployment("policy.toml")


@lru_cache(maxsize=1)
def _deployment_identity() -> str:
    return _read_deployment("identity.toml")


@lru_cache(maxsize=1)
def _deployment_config() -> str:
    return _read_deployment("config.toml")


def _hold_the_transport_tier(subject: Any, spec: Spec, md: Mapping[str, Any]) -> Any:
    """Hold `<presence>`'s live inputs at what production renders
    (`nora.subject.transport`). Only when the
    graph HAS a reader: a build without `<presence>` has nothing to hold,
    and reading the deployment's config for it would be a failure about
    nothing."""
    if getattr(subject.deps, "self_presence", None) is None:
        return None
    if spec.deployment_config is not None:
        networks = deployment_networks(tomllib.loads(spec.deployment_config))
    elif spec.policy is not None:
        # A caller that states its OWN room set (a test reconstructing a room
        # of its own) composed its own world: the networks it runs are the
        # ones its subject enabled. Reading the deployment's config here would
        # fail every integration test off the deploy host.
        networks = tuple(sorted(
            name for name in ("signal", "discord", "whatsapp", "instagram")
            if getattr(getattr(subject.cfg.platforms, name, None), "enabled", False)
        ))
    else:
        networks = deployment_networks(tomllib.loads(_deployment_config()))
    return hold_transports(
        subject,
        networks=networks,
        varied=varied_transport(md),
        live_handles=pseudonyms.resolve(dict(LIVE_HANDLE_STANDINS)),
    )


def varied_lessons(md: Mapping[str, Any]) -> tuple[str, ...]:
    """The lessons a `lessons` counterfactual attaches (`evals.scenarios` refuses
    `metadata.lessons` anywhere else), or none."""
    if (md.get("counterfactual") or {}).get("varies") != "lessons":
        return ()
    return tuple(str(x) for x in md.get("lessons") or ())


def _lesson_facts(md: Mapping[str, Any], case: Any) -> str | None:
    """The judge's ground truth for a `lesson_applied` case: the lessons she held
    and the one behaviour this reply is graded on (the case's
    `expected_output`). From the CASE, so it is the same in both arms."""
    if "lesson_applied" not in tuple(md.get("criteria") or ()):
        return None
    held = "\n".join(f"- {x}" for x in varied_lessons(md))
    behaviour = str(getattr(case, "expected_output", "") or "").strip()
    return (
        "Lessons she held in this group (practice she drew from how its people "
        f"responded to her before; she may or may not have been shown them):\n{held}"
        f"\n\nThe one behaviour this reply is graded on:\n{behaviour}"
    )


def _presence_facts(
    spec: Spec, md: Mapping[str, Any], *, room: tuple[str, str],
    shown: str | None, tools: tuple[str, ...],
) -> str | None:
    """The judge's ground truth for a `presence` case, or `None` for every
    other case. Built from the DEPLOYMENT's files and the case, never from the
    subject — so it is the same facts whichever arm ran."""
    if "presence" not in tuple(md.get("criteria") or ()):
        return None
    config = tomllib.loads(
        spec.deployment_config if spec.deployment_config is not None
        else _deployment_config()
    )
    return facts(
        policy_toml=spec.policy if spec.policy is not None else _deployment_policy(),
        room=room,
        handles=deployment_handles(config),
        states=held_states(deployment_networks(config), varied_transport(md)),
        shown=shown,
        tools=tools,
    )


def _read_deployment(name: str) -> str:
    """Fail-closed: a case without the deployment's own files would silently
    fall back to an invented room and a one-person roster, which is the
    divergence this whole layer exists to close."""
    path = _DEPLOYMENT_DIR / name
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(
            f"a case needs the deployment's {name} and {path} is unreadable "
            f"({exc}). It reconstructs a REAL room, so it needs that room's "
            "own policy entry and the roster its speakers resolve through. "
            "Run this where the deployment lives, or point "
            "$NORA_DEPLOYMENT_DIR at it "
            "(design/evaluation/11-the-sandbox-rebuild.md §1.2)."
        ) from exc


def substitute_trigger(text: str, trigger: str) -> str:
    """Render `{trigger}` / `{trigger_split}` against the CONFIGURED trigger.

    Dropping it would break cases silently: `near_miss.yaml`'s cases assert that
    trigger-SHAPED text does not fire, and a literal `{trigger}` does not fire
    either — so they would PASS while asserting nothing.

    The corpus holds RULES about `nora.tag.is_tagged`'s boundaries, not
    literals. Rendered at DRIVE time against the composed subject's own
    `engagement.trigger.text`, which is the deployment's — a corpus cannot
    state a trigger of its own.

    `{trigger_split}` is the same trigger with a space after its first
    character — how "broken by whitespace" is expressed for an arbitrary
    trigger.
    """
    split = f"{trigger[:1]} {trigger[1:]}" if len(trigger) > 1 else trigger
    return text.replace("{trigger_split}", split).replace("{trigger}", trigger)


#: The keys a case's `snapshot:` states, and nothing else: the room and the
#: instant. Its culture key and unit are RESOLVED from the deployment's own
#: policy entry at `obtain()` (`Subject.culture_key`/`.unit`), so a case
#: cannot state them wrongly.
SNAPSHOT_KEYS = frozenset({"platform", "conversation_id", "before_ms"})


#: A staged candidate whose body reached the notebook (`nora.domain.notes.
#: CANDIDATE_CREATED`: a new note or a revision appended to one).
_WRITTEN = "created"


def _written_bodies(outcome: Any) -> list[str]:
    return [b for status, b in getattr(outcome, "bodies", ()) if status == _WRITTEN]


def _shown_set(outcome: Any) -> Shown:
    """What the draw's model was shown, as `parse_note_body` takes it: the
    batch rows by the name each was rendered under, the roster, Nora's name,
    and the ids the notebook already cited."""
    return Shown(
        rendered=tuple(
            ShownMessage(id=int(i), sender=str(name))
            for i, name in getattr(outcome, "speakers", ())
        ),
        carried=frozenset(getattr(outcome, "carried", ())),
        roster=dict(getattr(outcome, "roster", ())),
        nora_names=frozenset(getattr(outcome, "nora_names", ())),
    )


def _parsed_bodies(
    outcome: Any, *, require_open: bool,
) -> list[tuple[str, NoteBody | None]]:
    """Each WRITTEN body as `(rule, parsed)`: `("ok", body)` when it holds to
    the schema, `(<the rule it broke>, None)` when it does not."""
    shown = _shown_set(outcome)
    parsed: list[tuple[str, NoteBody | None]] = []
    for body in _written_bodies(outcome):
        try:
            parsed.append(("ok", parse_note_body(body, shown=shown, require_open=require_open)))
        except NoteBodyError as refused:
            parsed.append((refused.rule, None))
    return parsed


def _check_the_sections(
    sections: Mapping[str, Any], outcome: Any, rows: Mapping[Any, int], *, require_open: bool,
) -> list[str]:
    """`body_sections` — `body_names`/`body_mentions`, PLACED. A mapping of heading to items.
    An item that is a row LABEL (an int) names that row's sender, by the name the model was
    shown: under `Owed` they must own a line, under `Decided`/`Proposed`/`Ruled out` a line
    must name them, under `Open` a line must cite the row. Any other item is a fact (a string,
    or a list of alternatives) some line of that section must contain, case-insensitively.
    Only bodies that parse count: a fact in the wrong section, or in prose, is not placed."""
    parsed = [b for rule, b in _parsed_bodies(outcome, require_open=require_open) if b]
    if not parsed:
        return ["body_sections: no written body holds to the schema"]
    speakers = dict(getattr(outcome, "speakers", ()))
    failed: list[str] = []
    for heading, items in sections.items():
        if heading not in SECTIONS:
            # A misspelt heading would find no lines and fail every draw for
            # a reason about the fixture, not the body; say so instead.
            failed.append(f"body_sections: {heading!r} is not a heading of the schema")
            continue
        lines = [
            ln for b in parsed if (s := b.section(heading)) is not None for ln in s.lines
        ]
        for item in items or ():
            if isinstance(item, int):
                row_id = rows.get(item)
                name = speakers.get(row_id) if row_id is not None else None
                if name is None:
                    failed.append(f"body_sections: row {item} was not in the batch")
                    continue
                if heading == "Owed":
                    met = any(ln.owner == name for ln in lines)
                elif heading in ATTRIBUTED:
                    met = any(name in ln.names for ln in lines)
                else:
                    met = any(row_id in ln.ids for ln in lines)
                if not met:
                    failed.append(
                        f"body_sections: ## {heading} has no line for the sender of row {item}"
                    )
                continue
            alternatives = [item] if isinstance(item, str) else list(item)
            text = "\n".join(ln.text for ln in lines).casefold()
            if not any(str(a).casefold() in text for a in alternatives):
                failed.append(f"body_sections: ## {heading} carries none of {alternatives}")
    return failed


def _names(name: str, text: str) -> bool:
    """Whether casefolded `text` names `name` — see `_check_the_body`."""
    for part in (name, *(w for w in name.split() if len(w) >= 2)):
        folded = part.casefold()
        if folded.isascii():
            if re.search(rf"(?<!\w){re.escape(folded)}(?!\w)", text):
                return True
        elif folded in text:
            return True
    return False


def _check_the_body(
    meta: Mapping[str, Any], outcome: Any, rows: Mapping[Any, int],
    *, require_open: bool = False,
) -> list[str]:
    """`body_names` and `body_mentions` — whether a WRITTEN body carries the
    thread, checked as named facts rather than as a length.

    `body_names` lists row LABELS (real row ids, as `must_cite` does); each
    row's sender, by the name the model was SHOWN on that line, must be named
    in a written body. The name is resolved at grade time from the batch, so a
    fixture names a row and never a person. A shown name of several words
    («given family») is met by any one word of two or more characters, since
    a body that says «Ada» of «Ada Lovelace» has named her. A Latin-script
    word must stand as a WORD (a two-letter given name is inside half the
    words of an English sentence); a word in another script, where words are
    not separated, is matched as written.

    `body_mentions` lists facts; each is a string or a list of alternatives
    (one fact in the several ways a body may write it, e.g. a weekday in two
    languages), met when any alternative is a case-insensitive substring of a
    written body.

    No written body at all fails both: there is nothing to carry the thread.

    `body_schema: true` fails a draw whose written body breaks the note-body schema,
    naming the rule; `body_sections` places facts (`_check_the_sections`).
    `require_open` is the create case's rule that `## Open` holds a line.
    """
    names = meta.get("body_names") or ()
    mentions = meta.get("body_mentions") or ()
    sections = meta.get("body_sections") or {}
    schema = bool(meta.get("body_schema"))
    if not names and not mentions and not sections and not schema:
        return []
    bodies = _written_bodies(outcome)
    if not bodies:
        return ["body_names/body_mentions: no body was written"]
    text = "\n".join(bodies).casefold()
    failed: list[str] = []
    if schema:
        failed += [
            f"body_schema: {rule}"
            for rule, _b in _parsed_bodies(outcome, require_open=require_open)
            if rule != "ok"
        ]
    if sections:
        failed += _check_the_sections(sections, outcome, rows, require_open=require_open)
    shown = dict(getattr(outcome, "speakers", ()))
    for label in names:
        row_id = rows.get(label)
        name = shown.get(row_id) if row_id is not None else None
        if name is None:
            failed.append(f"body_names: row {label} was not in the batch")
            continue
        if not _names(name, text):
            failed.append(f"body_names: the sender of row {label} is not named")
    for fact in mentions:
        alternatives = [fact] if isinstance(fact, str) else list(fact)
        if not any(str(a).casefold() in text for a in alternatives):
            failed.append(f"body_mentions: none of {alternatives}")
    return failed


def _updates_named(
    named: str | Sequence[str] | None, updates: Sequence[str],
) -> list[str]:
    """`updates_named`: the revision landed on the note the
    case names and on no other. Three failures, each its own words, so a
    tally separates «wrong note» from «no update»: a similarly titled sibling
    revised INSTEAD, nothing revised, or the named note revised ALONG WITH another.

    `named` may be a LIST of equally correct notes: a unit holding several
    open restatements of one arrangement has no one right target, and grading
    one of them would make the case a coin. Revising exactly one of the list
    passes."""
    if not named:
        return []
    if not isinstance(named, str):
        accepted = {str(t) for t in named}
        hit = [t for t in updates if t in accepted]
        rest = [t for t in updates if t not in accepted]
        if not hit and rest:
            return [f"updates_named: wrong note: revised {rest}, not one of {sorted(accepted)}"]
        if not hit:
            return [f"updates_named: no update: none of {sorted(accepted)} was revised"]
        if rest or len(hit) > 1:
            return [f"updates_named: also revised {rest or hit[1:]} beside {hit[0]!r}"]
        return []
    others = [t for t in updates if t != named]
    hits = len(updates) - len(others)
    if hits == 0 and others:
        return [f"updates_named: wrong note: revised {others}, not {named!r}"]
    if hits == 0:
        return [f"updates_named: no update: {named!r} was not revised"]
    if others or hits > 1:
        return [f"updates_named: also revised {others or [named]} beside {named!r}"]
    return []


def _date_split(spec: Any, outcome: Any, rows: Mapping[Any, int]) -> list[str]:
    """`date_split` — a batch that mixes two dates (a booking on one day and a
    dinner on another, merged into one note under the wrong date). A mapping
    of:

    - `anchor`: the day the plan is on. PASS needs a note dated it that this
      pass CREATED, or one it REVISED (the unit may already hold it; either
      is a correct answer, which `expect: create` could not say and
      `either` could not grade);
    - `not_anchor`: the other day in the batch;
    - `rows`: the row LABELS (real ids) that state the plan. FAIL on any note
      dated `not_anchor` that this pass created or revised carrying one of
      them, cited in its head body or in a staged candidate's
      `message_ids`, which is the misdating itself.
    """
    if not spec:
        return []
    want, wrong = int(spec["anchor"]), int(spec["not_anchor"])
    plan = {rows[label] for label in spec.get("rows") or () if label in rows}
    if len(plan) != len(spec.get("rows") or ()):
        return [f"date_split names a row the snapshot did not seed: {spec.get('rows')}"]
    dated = list(getattr(outcome, "changed_dated", ()))
    failed: list[str] = []
    if not any(anchor == want for _kind, anchor, _ids in dated):
        failed.append(f"date_split: nothing dated {want} was created or revised")
    carried = {
        i for _kind, anchor, ids in dated if anchor == wrong for i in ids
    } | {
        i for _t, anchor, message_ids, status in outcome.staged
        if anchor == wrong and status == "created" for i in message_ids
    }
    if plan & carried:
        failed.append(
            f"date_split: a note dated {wrong} carries the {want} plan's rows "
            f"{sorted(plan & carried)}"
        )
    return failed


def _check_the_corpus_assertions(
    case: Any, outcome: Any, rows: Mapping[Any, int],
) -> Any:
    """`must_cite`, `anchor_date`, `creates`, `updates` — the four the
    note-candidates corpus states — and `body_names`/`body_mentions`
    (`_check_the_body`).

    `rows` translates a case's own message LABEL — the REAL row id in the
    source, under `snapshot:` — into the id the sandbox assigned. That
    translation is the whole reason `Message.label` and `Written.rows` exist.

    Skipped entirely for an outcome that already carries an error — an un-run
    case must not acquire a second reason to fail.
    """
    meta = case.metadata
    if outcome.error is not None or not meta:
        return outcome
    failed: list[str] = list(outcome.checks_failed)

    creates = [t for kind, t in outcome.changed if kind == "create"]
    updates = [t for kind, t in outcome.changed if kind == "update"]
    if meta.get("creates") is not None and len(creates) != int(meta["creates"]):
        failed.append(f"creates: expected {meta['creates']}, got {len(creates)}")
    failed += _updates_named(meta.get("updates"), updates)
    if case.expect == "create" and updates:
        failed.append(f"create case also revised {updates}")

    if meta.get("anchor_date") is not None and case.expect in ("create", "update"):
        want = int(meta["anchor_date"])
        got = [a for _t, a, _m, _s in outcome.staged]
        if want not in got:
            failed.append(f"anchor_date: expected {want}, staged {got}")
    said = " ".join(outcome.said)
    for key, negate in (("must_cite", False), ("must_not_cite", True)):
        wanted = meta.get(key)
        if not wanted:
            continue
        if all(isinstance(w, int) for w in wanted):
            cited: set[int] = set()
            for _t, _a, message_ids, _s in outcome.staged:
                cited |= set(message_ids)
            ids = {rows.get(label) for label in wanted}
            if None in ids:
                failed.append(
                    f"{key} names a row the snapshot did not seed: {wanted}"
                )
                continue
            hit = {i for i in ids if i is not None} & cited
            missing = {i for i in ids if i is not None} - cited
            if negate and hit:
                failed.append(f"{key}: cited {sorted(hit)}")
            elif not negate and missing:
                failed.append(f"{key}: {sorted(missing)} not among {sorted(cited)}")
        else:
            folded = said.casefold()
            present = [w for w in wanted if str(w).casefold() in folded]
            absent = [w for w in wanted if str(w).casefold() not in folded]
            if negate and present:
                failed.append(f"{key}: the reply contains {present}")
            # A text `must_cite` grades the MESSAGE, so it binds only when
            # there was one or the case requires one. On `either` (and
            # `silence`) staying quiet is a legal answer, and the corpus
            # header says what is graded there is «the message, if there was
            # one»; checking an empty reply for a word would fail every correct
            # silence.
            elif not negate and absent and (said or case.expect not in ("either", "silence")):
                failed.append(f"{key}: the reply does not contain {absent}")
    failed += _check_the_body(meta, outcome, rows, require_open=case.expect == "create")
    failed += _date_split(meta.get("date_split"), outcome, rows)

    return dataclasses.replace(outcome, checks_failed=tuple(failed))


@contextlib.asynccontextmanager
async def _sandbox_for(spec: Spec, now: datetime) -> AsyncIterator[str]:
    """The case's own database: empty and migrated (`sandbox_database`, the
    snapshot path seeds it after `obtain()`), or already holding every
    restorable store as of `now` (`sandbox_as_of`, `Spec.full_restore`).
    Both are cloned from the same blank template and dropped on exit."""
    if spec.full_restore:
        async with sandbox_as_of(spec.source_url, now, template=spec.template) as (url, _):
            yield url
    else:
        async with sandbox_database(spec.source_url, template=spec.template) as url:
            yield url


async def _drive(case: Case, spec: Spec) -> dict[str, Any]:
    """One DRAW, in a Honcho workspace of its own. Raises; `nora_task` catches.

    The sandbox database isolates what she knows in Postgres; Honcho is an
    external service and needs its own isolation. A shared
    `<deployment>-eval` workspace would be fed by every draw and read back by
    the tag path, so draw 3 would read draws 1 and 2 as the tagger card.

    The workspace is the OUTERMOST scope: created before the subject is
    composed, and deleted only after `_drive_draw` has returned, by which
    point its own `finally` has closed the subject and cancelled its
    background work. A feed still running would otherwise write into a
    workspace being deleted.
    """
    from evals.rebuilt.entry import ENTRY_POINTS
    port = ENTRY_POINTS[spec.entry]
    if port.frozen_input:
        outcome = await port.drive(spec.judge, case, now_ms=0)
        return {
            "output": "",
            "environment_state": [
                EnvironmentState(name=NORA_ERROR, state=outcome.error),
                EnvironmentState(name=NORA_ADMITTED, state=None),
                EnvironmentState(name=NORA_SAID, state=0),
                EnvironmentState(name=NORA_WRITTEN, state=0),
                EnvironmentState(name=NORA_CHANGED_COUNT, state=0),
                EnvironmentState(name=NORA_STAGED_COUNT, state=0),
                EnvironmentState(name=NORA_JUDGED, state=outcome.judged),
                EnvironmentState(name=NORA_CHECKS_FAILED, state=outcome.checks_failed),
                EnvironmentState(name=NORA_POSITION, state=outcome.position),
                EnvironmentState(name=NORA_SHOWN, state=outcome.shown),
            ],
        }
    async with draw_workspace(case.name or "unnamed", run_id=RUN_ID) as memory:
        # Held BEFORE the drive, so a draw that raises is still counted; its
        # delete time is filled in when the context exits.
        _DRAWS.append(memory)
        result = await _drive_draw(case, spec, memory_workspace=memory.id)
    result["environment_state"].append(
        EnvironmentState(name=NORA_MEMORY_WORKSPACE, state=memory.id),
    )
    return result


async def _drive_draw(case: Case, spec: Spec, *, memory_workspace: str) -> dict[str, Any]:
    """Obtain, sandbox, seed, drive, tear down. Raises; `nora_task` catches.

    ONE PATH. Every case names a real room at a real instant (`snapshot:`),
    composes the deployment's own Nora against a sandbox database of its own,
    is seeded with what that room held then, and is driven at that instant.
    There is no authored branch: a case with no anchor is UNANCHORED and is an
    ERROR here — never a skip, never a case quietly run in an empty room.
    """
    from evals.rebuilt.domain import Moment, Outcome
    from evals.rebuilt.entry import ENTRY_POINTS

    port = ENTRY_POINTS[spec.entry]
    if spec.repeat > 1 and port.delivers_input:
        # `port.delivers_input` means a line ARRIVES after the window —
        # driving the SAME case a second time delivers a SECOND copy of that
        # line, so run 2 is not "the same pass again", it is a different pass
        # with an extra message in it. Idempotence is only meaningful for
        # entry points that read state without re-delivering input.
        raise ValueError(
            f"Spec.repeat={spec.repeat} would re-deliver the case's message "
            f"for entry={spec.entry!r} (delivers_input=True) — idempotence "
            "only makes sense for an entry point that does not re-deliver "
            "input on each drive, like `dreamer`"
        )
    md = case.metadata or {}
    if md.get("parked"):
        raise ValueError(
            f"case {case.name!r} is parked ({md['parked'].get('reason')}) and "
            "must not reach the task; `load_corpus` keeps parked cases out of "
            "the active set"
        )
    shot = dict(md.get("snapshot") or {})
    if not shot:
        raise ValueError(
            f"case {case.name!r} is UNANCHORED: it names no `snapshot:` "
            "(a real room and a real instant) and is not parked. A case with "
            "no anchor cannot run — there is no room to reconstruct — and is "
            "an ERROR rather than a skip, so the report shows the work "
            "(11-the-sandbox-rebuild.md §7.1)."
        )
    unknown = set(shot) - SNAPSHOT_KEYS
    if unknown:
        raise ValueError(
            f"case {case.name!r}'s `snapshot:` states {sorted(unknown)}; "
            f"only {sorted(SNAPSHOT_KEYS)} are a case's to state — the "
            "culture key and unit are the deployment's policy entry's, and "
            "a window or limit is production's config's (R7)"
        )
    room = (str(shot["platform"]), str(shot["conversation_id"]))
    before_ms = int(shot["before_ms"])
    clock_ms = before_ms
    if port.synthetic_sources:
        private = md.get("profile_input") or {}
        if not md.get("private_profile_file") or not isinstance(private.get("clock_ms"), int):
            raise ValueError("profile writer requires private synthetic source file and clock")
        clock_ms = private["clock_ms"]
        if clock_ms < before_ms:
            raise ValueError("synthetic writer clock cannot precede the restored anchor")
    now = datetime.fromtimestamp(clock_ms / 1000, tz=UTC)
    if spec.full_restore and os.environ.get(NO_GEOCODER_ENV):
        # The geocoder credentials are pointed at this name BECAUSE nothing
        # sets it; set, the full-restore subject would build a billed client.
        raise ValueError(
            f"${NO_GEOCODER_ENV} is set, and a full-restore subject reads its "
            "geocoder credential from it (`FULL_RESTORE_OVERRIDES`); unset it"
        )
    workdir = Path(tempfile.mkdtemp(prefix="nora-case-"))
    subject = None
    try:
        async with _sandbox_for(spec, now) as database_url:
            # A full restore holds the real speakers, so each room's members
            # are read from them while the graph composes; without it every
            # lineage tool answers `unavailable` (`nora.subject.sandbox_roster`).
            with (
                recorded_rosters(database_url=database_url, now=now)
                if spec.full_restore else contextlib.nullcontext()
            ):
                subject = await obtain(
                    SubjectSpec(
                        database_url=database_url,
                        workdir=workdir,
                        room=room,
                        # LAST, so nothing a spec states can redirect it: THIS
                        # DRAW'S Honcho workspace, created empty.
                        overrides={
                            **port.config,
                            # A full restore turns the presence READER back on
                            # (`FULL_RESTORE_OVERRIDES` says why nothing polls).
                            # Before the spec's own, so a caller can still state
                            # otherwise; empty on every other corpus.
                            **(FULL_RESTORE_OVERRIDES if spec.full_restore else {}),
                            **(spec.overrides or {}),
                            "memory.workspace_id": memory_workspace,
                        },
                        # The deployment's OWN roster and room set, verbatim.
                        # `_DEFAULT_IDENTITY` knows ONE person; a real room has
                        # many, and a snapshot copies each row's real `person_id`
                        # — so most speakers would resolve to "the
                        # identity does not know them" and render nameless,
                        # while message BODIES name roster members
                        # outright. A real policy entry also carries
                        # `description` (which REACHES THE MODEL), the
                        # `relevance_gate`/`home_room`/`voice` opt-ins and the
                        # room's tool grant; a minimal invented one measures a
                        # different room.
                        identity=(
                            spec.identity if spec.identity is not None
                            else _deployment_identity()
                        ),
                        policy=spec.policy if spec.policy is not None else _deployment_policy(),
                        # FROZEN AT THE MOMENT. The seeded rows keep their real
                        # timestamps, so the clock she reads them against must be
                        # the instant they were written up to.
                        now=now,
                    )
                )
            # SPEAKING NEEDS AN ADAPTER, and the sandbox disables Discord's
            # (a declared divergence — it authenticates for real at startup).
            # `FirePath._turn` resolves the room's own adapter before it says
            # anything, so a turn case anchored where no adapter is wired dies
            # deep in `fire.py` with a `KeyError`. Refused here, by name,
            # against the COMPOSED config rather than a list kept in step by
            # hand.
            if port.drives_a_turn:
                platform = room[0]
                enabled = getattr(
                    getattr(subject.cfg.platforms, platform, None), "enabled", False,
                )
                if not enabled:
                    raise ValueError(
                        f"case {case.name!r} drives a turn (entry={spec.entry!r}) "
                        f"in a {platform} room, and the sandbox composes no "
                        f"{platform} adapter — `FirePath._turn` resolves the "
                        "room's adapter before speaking, so the turn cannot "
                        "run. Anchor a turn case where an adapter is wired "
                        "(design/evaluation/11-the-sandbox-rebuild.md §1.2's "
                        "declared Discord divergence)."
                    )
            if spec.memory_surface is not None:
                refuse_a_memory_surface_mismatch(
                    spec.memory_surface, subject.cfg.memory_index.enabled,
                )
            transport = _hold_the_transport_tier(subject, spec, md)
            if spec.full_restore:
                # Already seeded, every store, before `obtain()`: this reads
                # back what the drive needs and VERIFIES the row map is the
                # identity (`restored_room`'s own docstring).
                seeded = await restored_room(
                    subject, source_url=spec.source_url, before_ms=before_ms,
                )
            else:
                seeded = await sandbox_from_snapshot(
                    subject,
                    source_url=spec.source_url,
                    before_ms=before_ms,
                    leave_room_for_arrival=port.delivers_input,
                    **({"stimulus_clock_ms": clock_ms} if port.synthetic_sources else {}),
                )
            # A case about a document she already made
            # has to make it, the production source holding none. Refused on a
            # subject with `[documents]` off, never read as a model failure.
            await seed_documents(subject, case.name, md.get("documents") or ())
            # The lessons a `lessons` counterfactual
            # attaches, written into the subject's unit. Seeded in EVERY arm; only
            # `[lessons] render` decides whether the turn reads them.
            await seed_lessons(subject, case.name, varied_lessons(md), now_ms=seeded.now_ms)
            if spec.memory_surface == "graph":
                # LAST, after every seed: the sandbox's memory index, projected
                # by production's rebuild and holding the source's vectors
                # where the input is identical, or the draw is refused
                # (`nora.subject.sandbox_index`). One line per sandbox.
                # Built once per key and applied to the next draw seeded alike
                # (`IndexCache`): the key declares what decided the rows, and
                # `index_sandbox` adds the digest of what seeding projected.
                built = await index_sandbox(
                    subject.database_url, source_url=spec.source_url,
                    model_id=subject.cfg.memory_index.embedder_model,
                    cache=_INDEX_CACHE,
                    key=(
                        spec.full_restore, room, before_ms, port.delivers_input,
                        json.dumps(md.get("documents") or [], sort_keys=True),
                        tuple(varied_lessons(md)),
                    ),
                )
                print(f"  {case.name}: {built.line()}", flush=True)
            exporter = _prefill_capture()
            exporter.clear()
            nora_case = _as_nora_case(case, spec, trigger=subject.cfg.engagement.trigger.text)
            # The moment, RESOLVED: where it was read from and how the
            # sandbox renumbered it — for an entry point whose input is a
            # record of the moment rather than the room it happened in.
            nora_case = dataclasses.replace(
                nora_case, moment=Moment(source_url=spec.source_url, rows=seeded.rows),
            )
            from evals.rebuilt.profile_identity import REPRESENTATION_PROPERTIES
            from nora.subject.context_filter import case_scope

            if (port.judged_on == "identity" and nora_case.name in REPRESENTATION_PROPERTIES
                    and spec.judge is None and not md.get("print_input")):
                outcome = Outcome(admitted=None, error="representation requires a semantic judge")
            else:
                with case_scope(case.name):
                    outcome = await port.drive(subject, nora_case, now_ms=clock_ms)
            blocks_empty = _blocks_empty(exporter)
            # BEFORE the judge: these describe what the SUBJECT spent, and a
            # judge's draws are the eval layer's own cost.
            spend = _model_calls(exporter)
            # A subject that names no agent (a test fake) reports no turn
            # subset rather than a guessed one.
            agent_name = getattr(getattr(subject.cfg, "agent", None), "name", None)
            if agent_name:
                turn = _model_calls(exporter, agent=agent_name)
                spend.update({f"turn.{k}": v for k, v in turn.items()})
            tools_called = _tools_called(exporter)
            tool_results = _tool_results(exporter)
            tool_inputs = _tool_inputs(exporter)
            shown_now = _shown_now(exporter)
            # `agent=agent_name`: the turn's own render, never the relevance
            # gate's (`_shown_transcript`'s own docstring).
            shown_transcript = _shown_transcript(exporter, agent=agent_name or None)
            shown_blocks = _shown_blocks(exporter, agent=agent_name or None)
            blocks_rendered = _blocks_rendered(exporter)
            shown = shown_where(exporter.get_finished_spans())
            _SPEND.append(spend)
            if port.synthetic_sources and outcome.position is not None:
                receipt = json.loads(outcome.position)
                receipt.update(
                    run_id=RUN_ID, case=case.name,
                    trace_ids=sorted({format(s.context.trace_id, "032x")
                                      for s in exporter.get_finished_spans() if s.context}),
                    model_calls=spend,
                )
                outcome = dataclasses.replace(
                    outcome, position=json.dumps(receipt, ensure_ascii=False, sort_keys=True),
                )
            outcome = _check_the_corpus_assertions(nora_case, outcome, seeded.rows)

            judged_why = ""
            # The standing fold: the judged thing is the POSITION a fold wrote, against the
            # case's one stated property, read over the turn's RECORD — the
            # same evidence on both arms of a comparison. Selected by the
            # port's declared `judged_on`, not by its name.
            if (port.judged_on == "identity" and outcome.judged == "REVIEW"
                    and outcome.error is None):
                if spec.judge is None:
                    outcome = dataclasses.replace(
                        outcome, error="representation requires a semantic judge",
                    )
                else:
                    judgement = await spec.judge.grade_identity(
                        heads=outcome.position, evidence=outcome.evidence,
                    )
                    if (draw := getattr(judgement, "receipt", None)) is not None:
                        receipt = json.loads(outcome.position)
                        receipt["judge"] = draw
                        outcome = dataclasses.replace(
                            outcome,
                            position=json.dumps(receipt, ensure_ascii=False, sort_keys=True),
                        )
                    judged_why = judgement.why
                    outcome = dataclasses.replace(
                        outcome, judged=judgement.verdict,
                        error=judgement.error or (
                            "representation judge returned no rating"
                            if judgement.verdict == "REVIEW" else None),
                    )
            elif (
                spec.judge is not None and port.judged_on == "position"
                and outcome.position is not None and outcome.error is None
            ):
                from evals.rebuilt.fold_evidence import render_working_set

                judgement = await spec.judge.grade_position(
                    position=outcome.position, evidence=outcome.evidence,
                    kind=str(md.get("kind") or ""), prop=str(case.expected_output or ""),
                    working_set=render_working_set(outcome.working_set),
                )
                judged_why = judgement.why
                outcome = dataclasses.replace(
                    outcome, judged=judgement.verdict, error=judgement.error,
                )
            # Judged HERE, while the
            # subject is alive, because the judge needs the doctrine she
            # actually ran ON, not a copy read off disk.
            elif spec.judge is not None and outcome.said and outcome.error is None:
                if shown_transcript is None:
                    # NOT fail-closed to fall through to `source=None`: `Judge.
                    # grade` still answers with no source ("meaningful with or
                    # without source data") — it grades BLIND, which its own
                    # call comment already names (DEC-75) and which the judge
                    # cannot itself tell apart from a case that carries no
                    # source BY DESIGN (an injection case, say). A turn that
                    # SPOKE always ran `build_prefill`, which renders
                    # `<group_transcript>` unconditionally, so a miss here,
                    # scoped to this turn's own agent, is the CAPTURE breaking
                    # — an ERROR about the instrument, never a verdict about
                    # her, and it must cost a judged draw rather than spend one
                    # on nothing.
                    outcome = dataclasses.replace(
                        outcome,
                        error=(
                            "no <group_transcript> was captured off the turn's "
                            "own model call; the judge's source cannot be bound "
                            "to the window she was shown, so this draw is not "
                            "judged (#459)"
                        ),
                    )
                elif "recall_grounded" in tuple(md.get("criteria") or ()) and (
                    blocks_problem := _recall_blocks_problem(shown_blocks, blocks_rendered)
                ):
                    outcome = dataclasses.replace(outcome, error=blocks_problem)
                else:
                    from evals.rebuilt.grading import criteria_for

                    criteria = tuple(md.get("criteria") or ())
                    judge_facts = _presence_facts(
                        spec, md, room=room, shown=shown, tools=tools_called,
                    ) or _lesson_facts(md, case)
                    if judge_facts is None:
                        # Read while the sandbox still exists: the records
                        # the case names, as they stood at the instant.
                        judge_facts = await recall_facts(
                            subject, md, now_ms=seeded.now_ms,
                        )
                    judgement = await spec.judge.grade(
                        said="\n".join(outcome.said),
                        asked=nora_case.input,
                        doctrine=subject.deps.doctrine.doctrine_text,
                        # The SOURCE the reply had to ground against — the
                        # window her turn actually RENDERED (`shown_transcript`),
                        # not `seeded.transcript` (every row
                        # `sandbox_from_snapshot` read back, unbounded — the
                        # room up to the instant, not what she was shown), and
                        # for a reporting case the tool results too.
                        # `recall_grounded` reads every other block she
                        # was shown too (`_judge_source`'s docstring).
                        source=_judge_source(
                            shown_transcript, tool_results, criteria, shown_blocks or (),
                        ),
                        extra=criteria_for(criteria),
                        facts=judge_facts,
                    )
                    outcome = dataclasses.replace(
                        outcome, judged=judgement.verdict, error=judgement.error,
                    )
                    judged_why = str(getattr(judgement, "why", "") or "")

            repeat_state = []
            if spec.repeat > 1:
                # SAME subject, SAME case, SAME now_ms — no re-`obtain()`.
                repeat_outcome = outcome
                for _ in range(spec.repeat - 1):
                    repeat_outcome = await port.drive(subject, nora_case, now_ms=clock_ms)
                repeat_state = [
                    EnvironmentState(name=NORA_ERROR_2, state=repeat_outcome.error),
                    EnvironmentState(name=NORA_WRITTEN_2, state=repeat_outcome.written),
                    EnvironmentState(name=NORA_CHANGED_COUNT_2, state=len(repeat_outcome.changed)),
                    EnvironmentState(name=NORA_STAGED_COUNT_2, state=len(repeat_outcome.staged)),
                ]

            if port.synthetic_sources and outcome.position is not None:
                # Retain normal tool decisions for readback after the sandbox
                # closes. Append AFTER judging so diagnostics cannot cue its verdict.
                receipt = json.loads(outcome.position)
                receipt.update(tool_inputs=tool_inputs, tool_results=tool_results)
                outcome = dataclasses.replace(
                    outcome, position=json.dumps(receipt, ensure_ascii=False, sort_keys=True),
                )

            # Torn down INSIDE the sandbox's lifetime: the database is dropped
            # on exit and a pool still open on it would make the DROP hang.
            await _close(subject)
            subject = None
            return {
                "output": "\n".join(outcome.said),
                "environment_state": [
                    EnvironmentState(name=NORA_ERROR, state=outcome.error),
                    EnvironmentState(name=NORA_TOKEN, state=outcome.outcome_token),
                    EnvironmentState(name=NORA_SAID, state=len(outcome.said)),
                    EnvironmentState(name=NORA_DELIVERY, state=outcome.delivery),
                    EnvironmentState(name=NORA_ADMITTED, state=outcome.admitted),
                    EnvironmentState(name=NORA_BLOCKS_EMPTY, state=blocks_empty),
                    *(EnvironmentState(name=k, state=v) for k, v in spend.items()),
                    EnvironmentState(name=NORA_WRITTEN, state=outcome.written),
                    EnvironmentState(name=NORA_CHANGED_COUNT, state=len(outcome.changed)),
                    EnvironmentState(name=NORA_STAGED_COUNT, state=len(outcome.staged)),
                    EnvironmentState(name=NORA_CHANGED, state=outcome.changed),
                    EnvironmentState(name=NORA_JUDGED, state=outcome.judged),
                    EnvironmentState(name=NORA_CHECKS_FAILED, state=outcome.checks_failed),
                    EnvironmentState(
                        name=NORA_BODY_CHARS,
                        state=tuple(len(b) for b in _written_bodies(outcome)),
                    ),
                    EnvironmentState(
                        name=NORA_BODY_SCHEMA,
                        state=tuple(
                            rule for rule, _b in _parsed_bodies(
                                outcome,
                                require_open=(case.metadata or {}).get("expect") == "create",
                            )
                        ),
                    ),
                    EnvironmentState(
                        name=NORA_BODY_REFUSED,
                        state=tuple(getattr(outcome, "body_refused", ())),
                    ),
                    EnvironmentState(name=NORA_TOOLS, state=tools_called),
                    EnvironmentState(name=NORA_TOOL_RESULTS, state=tool_results),
                    EnvironmentState(name=NORA_TOOL_INPUTS, state=tool_inputs),
                    EnvironmentState(name=NORA_SHOWN_NOW, state=shown_now),
                    EnvironmentState(name=NORA_POSITION, state=outcome.position),
                    EnvironmentState(name=NORA_WORKING_SET, state=outcome.working_set),
                    EnvironmentState(name=NORA_SHOWN, state=outcome.shown),
                    EnvironmentState(name=NORA_JUDGED_WHY, state=judged_why),
                    EnvironmentState(name=NORA_SHOWN_WHERE, state=shown),
                    EnvironmentState(name=NORA_TRANSPORT, state=transport),
                    *repeat_state,
                ],
            }
    except RoomNotInPolicyError as exc:
        # A pseudonymised anchor with no map in effect is refused, and the
        # message says what would resolve it.
        if pseudonyms.active():
            # With a map in effect the room in the message is a REAL id: the
            # log is read and quoted, so the room is withheld here.
            raise RoomNotInPolicyError(
                "the case's room is not in the deployment's policy even through the "
                "pseudonym map (room withheld); add its pseudonym to the host map"
            ) from None
        raise RoomNotInPolicyError(f"{exc}; {pseudonyms.HINT}") from exc
    finally:
        # Hundreds of cases at several samples is as many connection pools. `EvalTaskHandler`'s
        # own hooks are SYNC and `_close()` is not, so teardown cannot live
        # there and lives here.
        if subject is not None:
            await _close(subject)
        shutil.rmtree(workdir, ignore_errors=True)


def _as_nora_case(case: Case, spec: Spec, *, trigger: str) -> Any:
    from evals.rebuilt.domain import Case as NoraCase

    md = case.metadata or {}
    return NoraCase(
        name=case.name or "unnamed",
        entry=spec.entry,                         # type: ignore[arg-type]
        expect=md.get("expect", "either"),
        input=substitute_trigger(str(case.input), trigger),
        snapshot=dict(md.get("snapshot") or {}),
        person=str(md.get("person") or pseudonyms.resolve("miles_novak")),
        mention=bool(md.get("mention", False)),
        # `evals/rebuilt/entry.py`'s `dreamer` reads `case.category or
        # "note-candidates"` to pick WHICH adaptor a `dreamer` pass runs (only
        # `culture` is left; the default names a deleted adaptor, DEC-277).
        category=str(md.get("category") or ""),
        # `NoraCase.metadata`'s own docstring is "whatever else the corpus
        # recorded, carried verbatim" — `proactive_act` reads its `row` off
        # it and the note-candidates checks read `must_cite`/`anchor_date`.
        metadata=md,
    )


def nora_task(spec: Spec):
    """A `Callable[[Case], dict]` for `Experiment.run_evaluations_async`.

    **Sync on purpose.** `@eval_task`'s wrapper does not await, and every entry
    point is `async def`, so an un-wrapped coroutine would be graded as the
    string `'<coroutine object ...>'` and scored. Two escapes exist: pass a
    bare `async def` (which the SDK does await) and lose `TracedHandler` with
    it, or `asyncio.run` inside a sync function. This takes the second, because
    `TracedHandler` is the only thing that produces a `Session`, and a
    `Session` is what the level-bearing evaluators read.

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
                        state=scrub(f"{type(exc).__name__}: {exc}", spec.source_url),
                    ),
                    EnvironmentState(name=NORA_TOKEN, state=""),
                    EnvironmentState(name=NORA_SAID, state=0),
                    EnvironmentState(name=NORA_DELIVERY, state=""),
                    EnvironmentState(name=NORA_ADMITTED, state=None),
                    EnvironmentState(name=NORA_BLOCKS_EMPTY, state=None),
                    EnvironmentState(name=NORA_CALLS, state=None),
                    EnvironmentState(name=NORA_INPUT_TOKENS, state=None),
                    EnvironmentState(name=NORA_OUTPUT_TOKENS, state=None),
                    EnvironmentState(name=NORA_CACHED_TOKENS, state=None),
                    EnvironmentState(name=NORA_STOPS, state=None),
                    EnvironmentState(name=NORA_WRITTEN, state=None),
                    EnvironmentState(name=NORA_CHANGED_COUNT, state=None),
                    EnvironmentState(name=NORA_STAGED_COUNT, state=None),
                    EnvironmentState(name=NORA_WRITTEN_2, state=None),
                    EnvironmentState(name=NORA_CHANGED_COUNT_2, state=None),
                    EnvironmentState(name=NORA_STAGED_COUNT_2, state=None),
                    EnvironmentState(name=NORA_CHANGED, state=()),
                    EnvironmentState(name=NORA_JUDGED, state=""),
                    EnvironmentState(name=NORA_CHECKS_FAILED, state=()),
                    EnvironmentState(name=NORA_TOOLS, state=None),
                    EnvironmentState(name=NORA_TOOL_RESULTS, state=None),
                    EnvironmentState(name=NORA_TOOL_INPUTS, state=None),
                    EnvironmentState(name=NORA_SHOWN_NOW, state=None),
                    EnvironmentState(name=NORA_POSITION, state=None),
                    EnvironmentState(name=NORA_WORKING_SET, state=()),
                    EnvironmentState(name=NORA_SHOWN, state=""),
                    EnvironmentState(name=NORA_JUDGED_WHY, state=""),
                    EnvironmentState(name=NORA_SHOWN_WHERE, state=None),
                    EnvironmentState(name=NORA_TRANSPORT, state=None),
                ],
            }

    return task


def traced_nora_task(spec: Spec) -> tuple[Any, TracedHandler]:
    """Tool routing, which needs no new grader at all. `ToolCalled`/
    `ToolSelectionAccuracyEvaluator`/`ToolParameterAccuracyEvaluator`/
    `TrajectoryEvaluator` all read `actual_trajectory`, and a real
    `participation` case's `Session` carries a genuine `ToolExecutionSpan` — so
    the only thing needed is a `Session` at all, which `nora_task(spec)` alone
    never produces.

    `eval_task(TracedHandler())(nora_task(spec))` is the whole of it:
    `TracedHandler.after()` does `processed.setdefault("trajectory",
    session)`, and `nora_task`'s dict never sets that key, so this ADDS a
    trajectory onto the exact same `output`/`environment_state` shape every
    other grader in this module already reads — routing and `AbsenceGrader`
    can grade the SAME case.

    Returns `(task, handler)` — the caller must hold `handler` for the run's
    lifetime (one shared exporter) and pass `max_workers=1`, the same
    constraint `nora_task`'s own docstring already states for the reason
    `TracedHandler` itself gives (serial-only). Coexists with
    `_prefill_capture()` in the same process (either construction order —
    see its docstring); does NOT coexist with a SECOND
    `TracedHandler` instance run concurrently in the same process.
    """
    handler = TracedHandler()
    return eval_task(handler)(nora_task(spec)), handler


def render_transcript_at(case: Case) -> dict[str, Any]:
    """A dated question is answered against the case's clock. Pure, no
    `Subject`, no database, no network: `RelevanceGate` has no clock seam at
    all (`SubjectSpec.now` has no effect on what it renders), and
    `participation`/`tag` do (`FirePath` calls `self._clock()` and
    threads it into `build_prefill(now=...)`), but a full driven case pays
    for a `Subject`/database to exercise a computation that is a pure
    function of rows and a clock: `nora.domain.blocks.render_messages`, the
    same one `_transcript_block` calls, and `nora.domain.prompt.age`
    underneath it.

    `case.metadata`: `now` (an ISO timestamp) and `rows`, a list of
    `{body, name, minutes_ago}`. Returns the rendered `<m ...>` elements as
    `output`, for a grader to search (`SubstringGrader`, `case.
    expected_output` naming the exact `age="..."` attribute expected).

    **Never raises**, per this module's rule: a malformed case (`now`
    missing, an unparseable timestamp, a row missing `name`) that raised
    straight out would hit `Experiment._worker`'s own `except Exception` (see
    module docstring), which turns it into `test_pass=False, score=0` for EVERY
    evaluator **without writing a row to `detailed_results` at all** — worse
    than the ERROR-as-FAIL substitution the module docstring warns about,
    because the case would not even survive into `read()`'s count. It would
    disappear from the denominator rather than landing in it.
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
    which the dated-question cases need, since every case expects a different rendered age.
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
    """*A second identical pass writes nothing new.* Only meaningful with `Spec.repeat > 1` —
    a case run without a repeat carries no `_2` signals at all and is
    ungradable here, the same rule every other grader in this module follows
    for a signal it cannot find.

    `written`/`changed` are already per-call deltas (`dream()` diffs its own
    notebook `before`/`after`), so the repeat run's own values ARE the
    answer for those two. `staged` is an absolute snapshot, not a diff, so
    it needs both runs' counts compared.

    **Scoped to notes/staging, not room speech**: this
    reads `written`/`changed`/`staged` only. `dreamer()`
    (`evals/rebuilt/entry.py`) never populates `Outcome.said` at all — it
    writes notes and reminders, not room messages — so there is
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

        # The repeat run's OWN error is checked too. Entry points fail closed by
        # returning `Outcome(error=...)` rather than raising, so a repeat run
        # that broke comes back with
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

    The signal has to come from the task: with no provider reachable, three of
    four entry points return a VERDICT
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
        # gate case would grade every one of them as silence, verdict ignored.
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
        # For a gate case (`admitted is not None`), both
        # `True` and `False` are the gate having run and decided — there is
        # no third "nothing happened" reading distinct from those two,
        # because a gate that truly failed to run reports through
        # `NORA_ERROR`, already checked above. Reading `spoke=False` here as
        # "ungradable" would score a correct, successful refusal as ERROR.
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


class MeetsGrader(Evaluator):
    """The corpus port — delegates to
    `evals.rebuilt.domain.Outcome.meets()` rather than reimplementing its
    eleven-vocabulary logic (`go`/`no_go`/`speak`/`silence`/`reply`/
    `no_reply`/`create`/`update`/`none`/`either`/`remind`) a second time.

    `AbsenceGrader` covers a SUBSET of this (silence/speak/go/no_go) for the
    hand-written cases. `MeetsGrader` is for the PORTED corpora, several of
    which need `create`/`update`/`remind`/`either`/a judge's verdict, which
    `AbsenceGrader` was never built to read.

    Reconstructs a real `Outcome` from `environment_state` rather than
    re-deriving `.meets()`'s branches, so a future change to that method's
    logic (or its own eleven-case test suite) covers this grader for free.
    """

    def evaluate(self, data: EvaluationData) -> list[EvaluationOutput]:
        from evals.rebuilt.domain import Outcome

        error = state_of(data, NORA_ERROR)
        expect = (data.metadata or {}).get("expect", "either")
        # `.meets()` only ever reads `bool(self.said)` — never an element's
        # content or the exact count (that is `NORA_SAID`'s own job) — so a
        # single placeholder row stands in for "she said something."
        said = ("said",) if (state_of(data, NORA_SAID) or 0) else ()
        outcome = Outcome(
            admitted=state_of(data, NORA_ADMITTED),
            said=said,
            changed=tuple(state_of(data, NORA_CHANGED) or ()),
            written=state_of(data, NORA_WRITTEN) or 0,
            checks_failed=tuple(state_of(data, NORA_CHECKS_FAILED) or ()),
            judged=state_of(data, NORA_JUDGED) or "",
            position=state_of(data, NORA_POSITION),
            error=error or None,
        )
        verdict = outcome.meets(expect)
        why = state_of(data, NORA_JUDGED_WHY) or ""
        why = f" — judge: {why}" if why else ""
        # The written bodies' lengths ride on the reason, so `--reasons`
        # prints them per draw (`NORA_BODY_CHARS`); only a pass that wrote one.
        chars = state_of(data, NORA_BODY_CHARS)
        if chars:
            why += f" — body_chars={list(chars)}"
        schema = state_of(data, NORA_BODY_SCHEMA)
        if schema:
            why += f" — body_schema={list(schema)}"
        refused = state_of(data, NORA_BODY_REFUSED)
        if refused:
            why += f" — body_refused={list(refused)}"
        failed = outcome.checks_failed
        if failed:
            why += f" — checks: {list(failed)}"
        if verdict is None:
            reason = (
                f"the case did not run: {error}" if error
                else f"ungradable: judged={outcome.judged!r}, expect={expect!r}"
            )
            return [self._out(ERROR, 0.0, reason + why)]
        return [
            self._out(PASS, 1.0, f"met expect={expect!r}{why}")
            if verdict
            else self._out(FAIL, 0.0, f"did not meet expect={expect!r}{why}")
        ]

    async def evaluate_async(self, data: EvaluationData) -> list[EvaluationOutput]:
        return self.evaluate(data)

    @staticmethod
    def _out(verdict: str, score: float, reason: str) -> EvaluationOutput:
        return EvaluationOutput(
            score=score, test_pass=(verdict == PASS), reason=reason, label=verdict,
        )


#: How much of a call's input an argument verdict quotes, PASS or FAIL. A
#: failure that names what was expected and not what was PASSED cannot be
#: read: «no call wrote out Saturday 26 September» with none of the prompts
#: shown says nothing about what the call carried. A PASS
#: is quoted too, because a pass on an exclusion («no «mascot»») says nothing
#: about whether the case can separate two arms until the prompt is read. 900
#: characters, because a poster prompt states its date near the end.
_SEEN_CHARS = 900


def _seen(texts: list[str]) -> str:
    """What the call actually carried, quoted for a FAIL reason."""
    if not texts:
        return "(no call)"
    return " | ".join(f"«{t[:_SEEN_CHARS]}»" for t in texts)


class ToolRouteGrader(Evaluator):
    """Did the turn reach for the RIGHT tool?

    `metadata.tools` names the acceptable tools — any one of them executing
    is a PASS, because «what is the weather» may honestly be answered by
    `weather` and a case should not pretend there is one right verb when
    there are two. `metadata.not_tools` names the confusable sibling that
    must NOT run — the half of a confusable pair that is the actual error
    (`where_been` for a «where is she NOW» question). Both are read from what
    EXECUTED (`NORA_TOOLS`), never from what the reply says.

    `metadata.required_tools` optionally names tools that must ALL execute,
    in addition to the ANY choice in `metadata.tools`.

    `load_tools` is plumbing and is never the answer to anything: it is
    dropped before either check, so a deferred turn and a resident one are
    graded on the same question.

    `metadata.before` is an ORDER criterion: `{instagram_read: [media_fetch]}`
    says `media_fetch` must not run before the first `instagram_read`, and
    may run after it. Some errors are an order, not a choice: calling `media_fetch` on a
    shared reel's cover, reading one frame and stopping, is a defect that a case
    whose `media_fetch` finds nothing then points her at the link, so without
    the order it grades PASS. `NORA_TOOLS` is in
    start order (`_tools_called`), which is what makes this readable at all. A
    `before` that is not a mapping of tool to a list of tools is an ERROR: a
    criterion the grader cannot read is not one it applied.

    ERROR, never FAIL, when the case did not run or recorded no tools state:
    a broken instrument is not a wrong answer.

    **What the call carried**, read from `NORA_TOOL_INPUTS` and
    checked only once routing has passed, each keyed by tool name:

    - `args_include: {tool: [text, ...]}`: some call of `tool` carries every
      text (case-insensitive), e.g. her canonical picture's sha on «draw
      yourself».
    - `args_exclude: {tool: [text, ...]}`: no call of `tool` carries any.
    - `args_date: {tool: <weekday>}`: some call of `tool` writes out the
      date of «this <weekday>», resolved against the clock line she was
      SHOWN (`NORA_SHOWN_NOW`).

    Inputs not captured, or no clock line for `args_date`, is ERROR: the
    instrument could not see, which is not the same as she did not pass it.
    """

    _PLUMBING = frozenset({"load_tools"})

    def _args(self, data: EvaluationData, md: Mapping[str, Any]) -> EvaluationOutput | None:
        """The argument checks, or None when the case states none."""
        include = md.get("args_include") or {}
        exclude = md.get("args_exclude") or {}
        dated = md.get("args_date") or {}
        if not (include or exclude or dated):
            return None
        inputs = state_of(data, NORA_TOOL_INPUTS)
        if inputs is None:
            return self._out(ERROR, 0.0, "no tool inputs recorded")
        calls: dict[str, list[str]] = {}
        for name, text in inputs:
            calls.setdefault(str(name), []).append(str(text).lower())
        for tool in {*include, *exclude, *dated}:
            if tool in calls and not any(calls[tool]):
                return self._out(ERROR, 0.0, f"{tool} ran but its input was not captured")
        for tool, needles in exclude.items():
            for text in calls.get(tool, ()):
                hit = [n for n in needles if str(n).lower() in text]
                if hit:
                    return self._out(
                        FAIL, 0.0, f"{tool} was called with {hit}: {_seen([text])}")
        for tool, needles in include.items():
            if not any(all(str(n).lower() in text for n in needles)
                       for text in calls.get(tool, ())):
                return self._out(
                    FAIL, 0.0,
                    f"no {tool} call carried all of {list(needles)}: {_seen(calls.get(tool, []))}",
                )
        if dated:
            clock = state_of(data, NORA_SHOWN_NOW)
            today = date_shown(clock) if clock else None
            if today is None:
                return self._out(ERROR, 0.0, "no clock line was shown, so no date to expect")
            for tool, weekday in dated.items():
                day = this_weekday(today, str(weekday))
                if not any(states_the_date(text, day) for text in calls.get(tool, ())):
                    return self._out(
                        FAIL, 0.0,
                        f"no {tool} call wrote out {day:%A %-d %B %Y} (from {clock!r}): "
                        f"{_seen(calls.get(tool, []))}",
                    )
        carried = [t for tool in {*include, *exclude, *dated} for t in calls.get(tool, [])]
        return self._out(PASS, 1.0, f"the call carried what the case asks: {_seen(carried)}")

    def evaluate(self, data: EvaluationData) -> list[EvaluationOutput]:
        error = state_of(data, NORA_ERROR)
        called = state_of(data, NORA_TOOLS)
        md = data.metadata or {}
        want = frozenset(md.get("tools") or ())
        avoid = frozenset(md.get("not_tools") or ())
        required = frozenset(md.get("required_tools") or ())
        before = md.get("before") or {}
        if error:
            return [self._out(ERROR, 0.0, f"the case did not run: {error}")]
        if called is None:
            return [self._out(ERROR, 0.0, "no tools state recorded")]
        if not want:
            return [self._out(ERROR, 0.0, "case names no metadata.tools to expect")]
        if not isinstance(before, dict) or not all(
            isinstance(v, (list, tuple)) for v in before.values()
        ):
            return [self._out(
                ERROR, 0.0, f"metadata.before must map a tool to a list, got {before!r}",
            )]
        ran = [name for name in called if name not in self._PLUMBING]
        wrong = sorted(avoid & set(ran))
        if wrong:
            return [self._out(FAIL, 0.0, f"called the sibling {wrong}; ran {ran}")]
        missing = sorted(required - set(ran))
        if missing:
            return [self._out(FAIL, 0.0, f"missing required tools {missing}; ran {ran}")]
        for first, not_before in before.items():
            if first not in ran:
                continue
            early = sorted(set(ran[:ran.index(first)]) & set(not_before))
            if early:
                return [self._out(
                    FAIL, 0.0, f"called {early} before {first}; ran {ran}",
                )]
        if want & set(ran):
            carried = self._args(data, md)
            if carried is not None and carried.label != PASS:
                return [carried]
            routed = f"called {sorted(want & set(ran))}; ran {ran}"
            if carried is not None:
                routed = f"{routed}; {carried.reason}"
            return [self._out(PASS, 1.0, routed)]
        return [self._out(FAIL, 0.0, f"expected one of {sorted(want)}; ran {ran}")]

    async def evaluate_async(self, data: EvaluationData) -> list[EvaluationOutput]:
        return self.evaluate(data)

    @staticmethod
    def _out(verdict: str, score: float, reason: str) -> EvaluationOutput:
        return EvaluationOutput(
            score=score, test_pass=(verdict == PASS), reason=reason, label=verdict,
        )


class ContextGrader(Evaluator):
    """*The blocks she needs were rendered non-empty.* Deterministic: no model call, reads only
    what `build_prefill` assembled, off `NORA_BLOCKS_EMPTY`.

    `Case.metadata["must_render"]` names the block(s) THIS case seeded data
    for and therefore expects non-empty — `_BLOCKS`' own names in
    `nora/agent/prefill.py`: `self`, `peer_card`, `peer_history`,
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

    `label` survives in exactly one place —
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


#: A fourth answer, alongside `PASS`/`FAIL`/`ERROR` — see `sample_read()`.
UNSTABLE = "UNSTABLE"


def sampled(base: Case, n: int) -> list[Case]:
    """`n` independently-named copies of `base`, for a case whose signal is
    stochastic rather than reading it once.

    A model-dependent case can swap verdicts across runs at `sample: 1`. The
    obvious fix (run the same `Case` N times) does not work as written: `Experiment`
    has no repeat parameter, and `EvaluationDataStore`/the SDK's own caching
    key on `case_name`, so N draws under `base.name` unchanged would
    silently collide rather than produce N rows. Each draw needs a name of
    its own; `sample_read()` undoes the naming to read them back as one.
    """
    return [
        base.model_copy(update={"name": f"{base.name}#{i}", "session_id": f"{base.name}#{i}"})
        for i in range(1, n + 1)
    ]


def base_name(name: str) -> str:
    """The name `sampled()` drew FROM, undoing its `#i` suffix. A name that
    was never run through `sampled()` returns unchanged."""
    head, sep, tail = name.rpartition("#")
    return head if sep and tail.isdigit() else name


@dataclass(frozen=True)
class SampleReading:
    """One base case's verdict across its `sampled()` draws."""

    verdict: str  # PASS | FAIL | ERROR | UNSTABLE
    passed: int
    failed: int
    errored: int


def sample_read(report: Any) -> dict[str, SampleReading]:
    """Group a report's rows by the base name `sampled()` drew them from,
    and read each group's stability rather than picking a winner.

    `report.cases` (a `list[dict]`) and
    `report.detailed_results` (a `list[list[EvaluationOutput]]`) are
    aligned 1:1 — one entry per (case, evaluator) PAIR, not one entry per
    case, which is easy to misread from `detailed_results`'s own docstring
    ("Attributes: cases: ... Each record carries an `evaluator` key").
    `read()`'s flat tally never needs this distinction; grouping draws back
    into their base case does.

    **`UNSTABLE` is a fourth answer, deliberately not a majority vote.** A
    case only reads `PASS` or `FAIL` here when every draw agreed. Collapsing
    disagreement into whichever verdict happened to win would hide exactly
    the instability, which should be surfaced, not smoothed over.
    `ERROR` is the ONE exception: if EVERY draw for a base case reads ERROR,
    the group is `ERROR`, matching rule 3 (ERROR is not FAIL and is never
    PASS) rather than reading a case that never once ran as merely
    "unstable."
    """
    groups: dict[str, list[str]] = {}
    for case_row, outputs in zip(report.cases, report.detailed_results, strict=True):
        base = base_name(case_row["name"])
        for out in outputs:
            label = getattr(out, "label", None)
            groups.setdefault(base, []).append(label if label in (PASS, FAIL, ERROR) else ERROR)

    readings: dict[str, SampleReading] = {}
    for base, labels in groups.items():
        distinct = set(labels)
        verdict = labels[0] if len(distinct) == 1 else UNSTABLE
        readings[base] = SampleReading(
            verdict=verdict,
            passed=labels.count(PASS),
            failed=labels.count(FAIL),
            errored=labels.count(ERROR),
        )
    return readings

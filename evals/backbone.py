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
import contextlib
import dataclasses
import os
import shutil
import tempfile
from collections.abc import Mapping
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

from nora.subject import (
    SubjectSpec,
    obtain,
    sandbox_database,
    sandbox_from_snapshot,
)

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
#: What the SUBJECT's turn spent, read off the model-call spans this process
#: already captures for `agent.prefill` — NOT from an external collector.
#:
#: Eval runs force `[obs.tracing]` off, deliberately: a run's spans would land
#: in the deployment's own collector beside real ones. The cost of that was
#: measured on 2026-09-22 — a note-candidates case failed with a truncation
#: and the call that truncated was invisible, so the only way to ask "cut off
#: at what?" was to change a ceiling and re-run. Reading the numbers HERE
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
#: `Outcome.changed`/`.judged`/`.checks_failed` — the three signals
#: `Outcome.meets()` needs beyond `admitted`/`said`/`written` to grade
#: `create`/`update`/`either` and a judge's verdict. Added for the corpus
#: port (`10-the-scenario-map.md`, 2026-09-20): `MeetsGrader` reconstructs a
#: real `Outcome` from these and delegates to the SAME, already-correct
#: `.meets(expect)` the old harness graded 150 cases with, rather than
#: reimplementing its eleven-vocabulary logic a second time.
NORA_CHANGED = "nora.changed"
NORA_JUDGED = "nora.judged"
NORA_CHECKS_FAILED = "nora.checks_failed"

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
    whether its cleanup worked, and a teardown error is not a reading.

    **Moved here from `evals/rebuilt/runner.py`, 2026-09-20**, same reason
    the seeding helpers once here.
    """
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


#: What every case driven in this process has spent, appended by `_drive`.
#:
#: A module-level accumulator and not a field on the report, because the SDK's
#: report does NOT carry `environment_state`: `Experiment` builds each row from
#: `result["case"]` alone (`experiment.py:672`), so the graders see the spend
#: and the report never does. Measured by printing nothing on the first try.
#:
#: Safe as process state for the reason `nora_task`'s own docstring already
#: gives: `TracedHandler` forces `max_workers=1`, so cases are driven one at a
#: time, in this process.
_SPEND: list[dict[str, Any]] = []


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
    _SPEND.clear()
    return {
        "calls": calls, "input": inp, "output": out, "cached": cached,
        "fresh": inp - cached, "stops": stops,
        # NAMED, not inferred. A `max_tokens` finish reason is the truncation
        # that cost two ceiling changes and two re-runs to identify on
        # 2026-09-22, and it was in this attribute the whole time.
        "truncated": [r for r in stops if "max_token" in r or "length" in r],
    }


def _model_calls(exporter: InMemorySpanExporter) -> dict[str, Any]:
    """What the model calls captured so far spent, and how they stopped.

    A span counts as a model call when it carries `gen_ai.usage.output_tokens`
    — the attribute Strands sets on exactly those (`telemetry/tracer.py`), so
    no span name has to be matched and a rename cannot silently empty this.
    """
    calls = inp = out = cached = 0
    stops: list[str] = []
    for span in exporter.get_finished_spans():
        attrs = span.attributes or {}
        if "gen_ai.usage.output_tokens" not in attrs:
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
    #: S6 idempotence (`10-the-scenario-map.md` §3): drive the SAME subject
    #: through the SAME case `repeat` times, at the SAME `now_ms`, and carry
    #: the LAST run's `written`/`changed`/`staged` separately from the
    #: first's — no re-`obtain()` between runs, unlike every other case,
    #: because idempotence needs run 2 to see what run 1 actually left.
    repeat: int = 1
    #: S3/S9 (`10-the-scenario-map.md` §3): "did she reply" is structural;
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


#: Where the deployment's policy and roster live. Copied verbatim into each
#: case's own temp workdir, NEVER into the repo — `policy.toml` and
#: `identity.toml` are gitignored precisely because they are the PII (real
#: names, phone numbers, real room ids), and that does not change because an
#: eval wants them.
#:
#: Read from the deployment rather than authored, on the operator's call
#: (2026-09-21): an authored roster drifts from the real one, and faking the
#: NAMES while the bodies keep them is worse than either — 647 of 4294 message
#: bodies (15%, measured) name a roster member outright, so a faked roster
#: makes the room talk about people who are not in it.
_DEPLOYMENT_DIR = Path(
    os.environ.get("NORA_DEPLOYMENT_DIR", "/home/norty/workspace/nora")
)


@lru_cache(maxsize=1)
def _deployment_policy() -> str:
    return _read_deployment("policy.toml")


@lru_cache(maxsize=1)
def _deployment_identity() -> str:
    return _read_deployment("identity.toml")


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

    Carried across from `evals/run.py:510` because dropping it broke ten cases
    silently and in both directions: `near_miss.yaml`'s four cases assert that
    trigger-SHAPED text does not fire, and a literal `{trigger}` does not fire
    either — so they PASSED while asserting nothing.

    The corpus holds RULES about `nora.tag.is_tagged`'s boundaries, not
    literals. Rendered at DRIVE time against the composed subject's own
    `engagement.trigger.text`, which is the deployment's — a corpus can no
    longer state a trigger of its own.

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


def _check_the_corpus_assertions(
    case: Any, outcome: Any, rows: Mapping[Any, int],
) -> Any:
    """`must_cite`, `anchor_date`, `creates`, `updates` — the four the
    note-candidates corpus states.

    `rows` translates a case's own message LABEL — the REAL row id in the
    source, under `snapshot:` — into the id the sandbox assigned. That
    translation is the whole reason `Message.label` and `Written.rows` exist.

    Skipped entirely for an outcome that already carries an error — an un-run
    case must not acquire a second reason to fail.
    """
    meta = case.metadata
    if outcome.error is not None or not meta:
        return outcome
    failed: list[str] = []

    creates = [t for kind, t in outcome.changed if kind == "create"]
    updates = [t for kind, t in outcome.changed if kind == "update"]
    if meta.get("creates") is not None and len(creates) != int(meta["creates"]):
        failed.append(f"creates: expected {meta['creates']}, got {len(creates)}")
    if meta.get("updates") and meta["updates"] not in updates:
        failed.append(f"updates: {meta['updates']!r} was not revised; got {updates}")
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
            elif not negate and absent:
                failed.append(f"{key}: the reply does not contain {absent}")

    return dataclasses.replace(outcome, checks_failed=tuple(failed))


async def _drive(case: Case, spec: Spec) -> dict[str, Any]:
    """Obtain, sandbox, seed, drive, tear down. Raises; `nora_task` catches.

    ONE PATH. Every case names a real room at a real instant (`snapshot:`),
    composes the deployment's own Nora against a sandbox database of its own,
    is seeded with what that room held then, and is driven at that instant.
    There is no authored branch: a case with no anchor is UNANCHORED and is an
    ERROR here — never a skip, never a case quietly run in an empty room
    (`11-the-sandbox-rebuild.md` §7.1).
    """
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
    now = datetime.fromtimestamp(before_ms / 1000, tz=UTC)
    workdir = Path(tempfile.mkdtemp(prefix="nora-case-"))
    subject = None
    try:
        async with sandbox_database(spec.source_url, template=spec.template) as database_url:
            subject = await obtain(
                SubjectSpec(
                    database_url=database_url,
                    workdir=workdir,
                    room=room,
                    overrides={**port.config, **(spec.overrides or {})},
                    # The deployment's OWN roster and room set, verbatim.
                    # `_DEFAULT_IDENTITY` knows ONE person; a real room has
                    # ten, and a snapshot copies each row's real `person_id`
                    # — so nine speakers in ten would resolve to "the
                    # identity does not know them" and render nameless,
                    # while 15% of message BODIES name a roster member
                    # outright. A real policy entry also carries
                    # `description` (which REACHES THE MODEL), the
                    # `relevance_gate`/`home_room`/`voice` opt-ins and the
                    # room's tool grant; a minimal invented one measures a
                    # different room.
                    identity=spec.identity if spec.identity is not None else _deployment_identity(),
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
            seeded = await sandbox_from_snapshot(
                subject,
                source_url=spec.source_url,
                before_ms=before_ms,
                leave_room_for_arrival=port.delivers_input,
            )
            exporter = _prefill_capture()
            exporter.clear()
            nora_case = _as_nora_case(case, spec, trigger=subject.cfg.engagement.trigger.text)
            outcome = await port.drive(subject, nora_case, now_ms=seeded.now_ms)
            blocks_empty = _blocks_empty(exporter)
            # BEFORE the judge: these describe what the SUBJECT spent, and a
            # judge's draws are the eval layer's own cost.
            spend = _model_calls(exporter)
            _SPEND.append(spend)
            outcome = _check_the_corpus_assertions(nora_case, outcome, seeded.rows)

            # S3/S9 (`10-the-scenario-map.md` §3) — judged HERE, while the
            # subject is alive, because the judge needs the doctrine she
            # actually ran ON, not a copy read off disk.
            if spec.judge is not None and outcome.said and outcome.error is None:
                from evals.rebuilt.grading import criteria_for

                judgement = await spec.judge.grade(
                    said="\n".join(outcome.said),
                    asked=nora_case.input,
                    doctrine=subject.deps.doctrine.doctrine_text,
                    # The SOURCE the reply had to ground against — the window
                    # she was seeded with. Without it the judge grades
                    # grounding BLIND (DEC-75).
                    source="\n".join(seeded.transcript) or None,
                    extra=criteria_for(tuple(md.get("criteria") or ())),
                )
                outcome = dataclasses.replace(
                    outcome, judged=judgement.verdict, error=judgement.error,
                )

            repeat_state = []
            if spec.repeat > 1:
                # SAME subject, SAME case, SAME now_ms — no re-`obtain()`.
                repeat_outcome = outcome
                for _ in range(spec.repeat - 1):
                    repeat_outcome = await port.drive(subject, nora_case, now_ms=seeded.now_ms)
                repeat_state = [
                    EnvironmentState(name=NORA_ERROR_2, state=repeat_outcome.error),
                    EnvironmentState(name=NORA_WRITTEN_2, state=repeat_outcome.written),
                    EnvironmentState(name=NORA_CHANGED_COUNT_2, state=len(repeat_outcome.changed)),
                    EnvironmentState(name=NORA_STAGED_COUNT_2, state=len(repeat_outcome.staged)),
                ]

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
                    *repeat_state,
                ],
            }
    finally:
        # 150 cases at five samples is 750 connection pools. `EvalTaskHandler`'s
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
        person=str(md.get("person") or "miles_novak"),
        mention=bool(md.get("mention", False)),
        # `evals/rebuilt/entry.py`'s `dreamer` reads `case.category or
        # "note-candidates"` to pick WHICH adaptor a `dreamer` pass runs.
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


class MeetsGrader(Evaluator):
    """The corpus port (`10-the-scenario-map.md`, 2026-09-20) — delegates to
    `evals.rebuilt.domain.Outcome.meets()` rather than reimplementing its
    eleven-vocabulary logic (`go`/`no_go`/`speak`/`silence`/`reply`/
    `no_reply`/`create`/`update`/`none`/`either`/`remind`) a second time.

    `AbsenceGrader` covers a SUBSET of this (silence/speak/go/no_go) for the
    hand-written S4-S7 cases already shipped this session and is left as is —
    changing an already-verified grader's internals for cases that do not
    need the extra vocabulary is not this port's job. `MeetsGrader` is for
    the PORTED corpora, several of which need `create`/`update`/`remind`/
    `either`/a judge's verdict, which `AbsenceGrader` was never built to
    read.

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
            error=error or None,
        )
        verdict = outcome.meets(expect)
        if verdict is None:
            reason = (
                f"the case did not run: {error}" if error
                else f"ungradable: judged={outcome.judged!r}, expect={expect!r}"
            )
            return [self._out(ERROR, 0.0, reason)]
        return [
            self._out(PASS, 1.0, f"met expect={expect!r}")
            if verdict
            else self._out(FAIL, 0.0, f"did not meet expect={expect!r}")
        ]

    async def evaluate_async(self, data: EvaluationData) -> list[EvaluationOutput]:
        return self.evaluate(data)

    @staticmethod
    def _out(verdict: str, score: float, reason: str) -> EvaluationOutput:
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


#: A fourth answer, alongside `PASS`/`FAIL`/`ERROR` — see `sample_read()`.
UNSTABLE = "UNSTABLE"


def sampled(base: Case, n: int) -> list[Case]:
    """`n` independently-named copies of `base`, for a case whose signal is
    stochastic rather than reading it once.

    `09-the-backbone-measured.md` §3.1 measured two model-dependent cases
    SWAP verdicts across two runs at `sample: 1` — "the only stable verdict
    was the ERROR." `08-the-sdk-surface.md` §4.4 found why the obvious fix
    (run the same `Case` N times) does not work as written: `Experiment`
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

    **MEASURED, not assumed**: `report.cases` (a `list[dict]`) and
    `report.detailed_results` (a `list[list[EvaluationOutput]]`) are
    aligned 1:1 — one entry per (case, evaluator) PAIR, not one entry per
    case, which is easy to misread from `detailed_results`'s own docstring
    ("Attributes: cases: ... Each record carries an `evaluator` key") on a
    first pass. `read()`'s flat tally never needed this distinction;
    grouping draws back into their base case does, so it is written down
    here rather than assumed a second time.

    **`UNSTABLE` is a fourth answer, deliberately not a majority vote.** A
    case only reads `PASS` or `FAIL` here when every draw agreed. Collapsing
    disagreement into whichever verdict happened to win would hide exactly
    the instability `09-the-backbone-measured.md` §3.1 measured — the
    finding this bundle's own discipline says to surface, not smooth over.
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

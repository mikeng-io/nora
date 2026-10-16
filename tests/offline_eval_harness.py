"""Eval harness: drives a real pipeline graph (real guards, fake db/memory/adapter)
against a scripted turn sequence — or against Nora's REAL model when `live=True`
— and reports what happened as a `Trajectory`, for the deterministic evaluators
in `evals.evaluators` to grade.

Two drive modes, mutually exclusive on `run_case`:

- **Scripted (`live=False`, default)** — `script` is the scripted model turn
  sequence, driven through `FakeAgentRunner`: offline, no network. `script=None`
  degrades to a zero-turn silent run.
- **Live (`live=True`)** — Nora's actual model through the actual Strands loop
  (`tests.offline_eval_support.build_live_runner` + `nora.app.agent_runner.
  LiveAgentRunner`, which wraps `nora.agent.factory.create_agent`/`invoke` — the
  same code path production runs), reaching the network. Nothing in `evals/`
  drives `run_case`; `python -m evals <corpus>` runs through `evals.backbone`.

  The graph around the model stays fakes (db/memory/adapter) so a live run
  measures the MODEL's behaviour without touching a real platform or store;
  `--skills` and `--seed-production` (see `run_case`) close specific
  production-shape gaps on demand, still with zero writes to the real stores.

Design note (this file's `run_case`/`Case` are design guidance, not pinned by
`tests/test_evals_gate.py` — that test only exercises `Trajectory` + the evaluators):
`run_case` builds its own pipeline graph from `tests.offline_pipeline.build_offline_pipeline`
plus `nora.testing`'s fakes, rather than reusing `tests/integration/harness.py`.
The live-model branch
is the `live=True` path in `run_case` below (see `nora/app/agent_runner.py`);
per-dataset (`--suite trigger/no_trigger/all`) live wiring is not done and still
runs scripted.

`FakeRecordStore`/`FakeMemoryStore` stand in for a real audit store: nothing
here is pytest-gated (see the module note above), and the evaluators only ever
read the returned `Trajectory` — not persisted rows — so an in-memory double is
equivalent for this harness's purpose and avoids the tmp-dir/migration
machinery `tests/integration/harness.py` needs for its own, broader,
fault-injection-capable test suite.

It runs a case through the real pipelines with a SCRIPTED model and no network,
which `nora.subject.obtain` cannot do: it always composes the real graph against
a real database and a real provider. R-97's only named test needs that.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import tempfile
import tomllib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import yaml

from nora.testing import (
    FakeAdapter,
    FakeAgentRunner,
    FakeMemoryStore,
    FakeRecordStore,
    FakeRosterSource,
    InMemoryNoteStore,
    InMemoryStandingStore,
    ScriptedTurn,
    group_event,
)
from tests.offline_eval_support import (
    TOOL_NAMES,
    DoctrineStore,
    Metrics,
    ProductionSeed,
    build_eval_tool_registry,
    build_live_participation_runner,
    build_live_runner,
    load_eval_config,
    load_persons,
    seed_from_dict,
)
from tests.offline_pipeline import build_offline_pipeline

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCTRINE_DIR = REPO_ROOT / "nora" / "doctrine"

# The one sender `group_event`'s default produces, mapped so the post-reply Honcho
# feed (which requires a resolved `ctx.tagger`, `nora/engagement/stages/feedback.py`)
# actually fires — matches `tests/integration/harness.py`'s own default identity.
_IDENTITY_TOML = (
    '[persons.miles_novak]\nname = "Miles"\n'
    '[[persons.miles_novak.signal]]\nphone = "+852111"\n'
)

def _configured_trigger() -> str:
    """`[engagement.trigger] text`, read straight out of the config FILE.

    `tomllib`, not `load_eval_config`: the full loader refuses to build without
    the provider API key, so a harness that went through it would raise at
    IMPORT time in the offline gate — and a fallback swallowing that would put
    the hand-copied literal back, just spelled differently.

    Deployment config first, template second: the trigger the running system
    answers to is the one a near-miss corpus has to miss.
    """
    for name in ("config.toml", "config.example.toml"):
        path = REPO_ROOT / name
        if not path.exists():
            continue
        conf = tomllib.loads(path.read_text(encoding="utf-8"))
        text = (conf.get("engagement", {}).get("trigger", {}) or {}).get("text")
        if text:
            return str(text)
    raise RuntimeError("no config declares [engagement.trigger] text")


# DERIVED from the config, not transcribed from it: a hand-copied value drifts
# from the trigger the deployment runs, and `evals/corpora/no_trigger.yaml` is
# a corpus of near-misses, meaningful only against that trigger.
TRIGGER_TEXT = _configured_trigger()

# The harness drives `group_event(body=...)` with its DEFAULTS — conversation "g1",
# sender "+852111" — and `_IDENTITY_TOML` maps that sender to person "miles_novak",
# the same production person id `--seed-production` reads Honcho under. The
# live-mode roster below names that one member so the room resolves to {miles_novak},
# a verified subset of the eval circle (`tests.offline_eval_support`), which is what lets
# `saved_places`/`where_is` disclose instead of degrading (F4). If `group_event`'s
# defaults change, these must change with them.
_EVAL_GROUP_ID = "g1"
_EVAL_MEMBER_ID = "+852111"


@dataclass
class Trajectory:
    tool_calls: list[str] = field(default_factory=list)
    sent: list[str] = field(default_factory=list)
    reply: str | None = None
    honcho_feeds: int = 0
    prefill_sufficient: bool = True
    # HOW the agent loop ended, read back off `nora_llm_stop_reason_total`
    # (None when no stop reason was recorded).
    #
    # Recorded because without it a silent turn cannot be attributed:
    # `end_turn` with no reply and `limit_output_tokens` are completely
    # different failures. A null result from an instrument that cannot measure
    # the thing is not a null result (CLAUDE.md).
    stop_reason: str | None = None
    #: WHY the room heard nothing, read off `nora_silent_failures_total{reason}`.
    #:
    #: Same argument as `stop_reason` above, one level in: the suite grades on
    #: `sent`, so a turn R-8's caps cut off before it could decide anything and
    #: a turn that decided to say nothing are the same row, and they are
    #: opposite findings.
    #:
    #: READ WHAT THIS CAN SEE. On the PARTICIPATION path it carries budget cuts
    #: ONLY. `nora/app/fire.py` routes a completed participation turn that never
    #: called `send_reply` to `nora_participation_silence_total` — the declined
    #: floor is the healthy default there, deliberately not a failure — so an
    #: empty dict here means "no cap bound", never "she chose the silence".
    #: `participation_silence` below is the one that says she reached the
    #: decision at all.
    silent_failures: dict[str, int] = field(default_factory=dict)
    #: Times this turn recorded `nora_participation_silence_total`: the model
    #: was offered the floor, completed its loop, and declined.
    #:
    #: It CANNOT separate "nothing needed saying" from "she wrote the answer as
    #: prose and never routed it" — production collapses both into this one
    #: series by construction (`fire.py`, the `participation and stop_reason
    #: not in BUDGET_STOP_REASONS` branch). That is a finding about the
    #: occasion, not a gap in the harness, and it is the case for a terminal
    #: `decline` verb: today a declined floor is counted, but the reason for it
    #: is not observable anywhere.
    participation_silence: int = 0
    #: The prose she wrote and never routed through `send_reply`, when the turn
    #: declined the floor holding one.
    #:
    #: THE number that partitions a missed speak. Production collapses "nothing
    #: needed saying" and "she wrote the answer and it never left the process"
    #: into one counter, and the two have opposite fixes: the first is a
    #: doctrine calibration, the second is a broken output contract. Empty
    #: string when she genuinely produced nothing.
    unrouted_prose: str = ""
    #: The run's OWN `Metrics()` instance (R-99), for a case whose evidence is a
    #: fine-grained counter no other `Trajectory` field carries —
    #: `nora_guard_vetoes_total{rule="R-99"}` fired, or
    #: `nora_tool_calls_total{tool=...}` did NOT (the tool body never ran).
    #: `None` is not a real state this harness produces — `run_case` always
    #: sets it — but the type stays optional so a case assembled by hand
    #: elsewhere (`test_the_deterministic_evaluators.py`) is not forced to
    #: invent one it does not have.
    metrics: Metrics | None = None


@dataclass
class Case:
    name: str
    input: str
    expected_output: str | None = None
    trigger: bool = True
    prefill_sufficient: bool = True
    # The participation suite:
    # `expect` is the verdict the determination must reach — "speak" or
    # "silence" — graded on `Trajectory.sent` (see `evals.evaluators.
    # ParticipationVerdict` for why `sent`, not `tool_calls`). `seed` is the
    # per-case seed MAPPING (transcript + standing/notes), built into a
    # `ProductionSeed` by `tests.offline_eval_support.seed_from_dict` inside
    # `run_case` — everything in it is keyed to the eval unit there, so
    # a case author cannot accidentally seed rows the eval room never reads.
    expect: str | None = None
    seed: dict | None = None
    #: How many times to run this case. `1` is one draw of a stochastic
    #: process, and a suite of one-draw cases reports a number that is not
    #: reproducible: a case can speak on one draw and stay silent on the next
    #: while the aggregate looks stable only because the other cases are
    #: saturated at silence.
    sample: int = 1

    @classmethod
    def from_dict(cls, raw: dict) -> Case:
        meta = raw.get("metadata") or {}
        expect = raw.get("expect")
        return cls(
            name=raw["name"],
            input=raw["input"],
            expected_output=raw.get("expected_output"),
            # Participation cases default to NO trigger prefix: the incoming
            # message is untagged by construction (that is what makes it a
            # participation candidate at all).
            trigger=bool(meta.get("trigger", expect is None)),
            prefill_sufficient=bool(meta.get("prefill_sufficient", True)),
            expect=expect,
            seed=raw.get("seed"),
            sample=int(raw.get("sample", 1)),
        )


def load_cases(path: Path | str) -> list[Case]:
    """`yaml.safe_load` a case file into `Case`s. An empty/missing file yields []."""
    raw = yaml.safe_load(Path(path).read_text()) or []
    return [Case.from_dict(item) for item in raw]


async def _drain(background) -> None:
    """Await every background task spawned so far — ambient memory-feeds,
    spawned tags, AND `EngagementPipeline`'s post-reply exchange feed all land
    in the same shared `BackgroundTasks` (`EngagementPipeline` takes an injected
    `spawn`; `tests.offline_pipeline.build_offline_pipeline` wires both
    pipelines' spawns to this one `background.spawn`) — including ones spawned
    by tasks that are themselves still in flight.

    Mirrors `tests/integration/harness.py::Harness.drain` exactly (same
    `asyncio.sleep(0)`-after-`gather` shape, for the same reason: a task's own
    coroutine can be reported `done()` before its `add_done_callback` discard
    runs, since done-callbacks are delivered via `call_soon` one loop tick
    later). Deliberately NOT `BackgroundTasks.cancel_all()` — that cancels
    outstanding tasks rather than letting them finish, which would lose the
    post-reply Honcho feed `Trajectory.honcho_feeds` depends on.
    """
    while background._tasks:
        pending = list(background._tasks)
        await asyncio.gather(*pending, return_exceptions=True)
        await asyncio.sleep(0)


# The counters that carry a per-tool `tool` label, so a live call's tool NAME can
# be read back. `nora.agent.tools` does NOT record every tool through one series:
# native tools use `nora_tool_calls_total{tool,status}` (`_record`), but the
# room/memory tools — group_members, lookup_person, about, ask_memory,
# search_memory — use `nora_room_lookups_total{tool,status}` (`_record_room`), a
# SEPARATE series (`tests/test_tools.py::test_the_new_counter_never_double_counts_a
# _room_scoped_tool` pins the split, asserting `nora_tool_calls_total == 0` and
# `nora_room_lookups_total == 3` for a room tool). Reading only the first would be
# BLIND to the whole MEMORY cluster of `SEED_INTENT_GRAPH`, silently false-PASSing
# a `NoSiblingLeakage` leak into `about`/`ask_memory`. `media_fetch` records
# `nora_media_fetch_total` which carries NO `tool` label and so cannot be
# reconstructed by name — it is deliberately not an intent-graph node, and
# `evals.evaluators.UNMEASURABLE_LIVE` + its guard test fail closed if one is added.
_TOOL_CALL_METRICS = ("nora_tool_calls_total", "nora_room_lookups_total")


def _stop_reason_from_metrics(metrics: Metrics) -> str | None:
    """How the agent loop ended, off `nora_llm_stop_reason_total{reason}`.

    One turn per `run_case`, so exactly one series is expected to have been
    incremented; if several are (a retry, a future multi-turn case), the one
    with the highest count wins and ties break alphabetically so the value is
    deterministic rather than dict-order-dependent. None when nothing recorded
    — a turn that faulted before the model was invoked has no stop reason, and
    inventing `"unknown"` would make that indistinguishable from a real one.
    """
    counts: dict[str, float] = {}
    for (name, labels), value in metrics.snapshot().items():
        if name == "nora_llm_stop_reason_total" and value:
            reason = dict(labels).get("reason", "")
            if reason:
                counts[reason] = counts.get(reason, 0.0) + value
    if not counts:
        return None
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def _silent_failures_from_metrics(metrics: Metrics) -> dict[str, int]:
    """Why the room heard nothing, off `nora_silent_failures_total{reason}`.

    Empty when nothing was recorded. See `Trajectory.silent_failures` for what
    that empty dict does and does not mean on the participation path.
    """
    out: dict[str, int] = {}
    for (name, labels), value in metrics.snapshot().items():
        if name == "nora_silent_failures_total" and value:
            reason = dict(labels).get("reason", "")
            if reason:
                out[reason] = out.get(reason, 0) + int(value)
    return out


class _UnroutedProseCapture(logging.Handler):
    """Capture the prose a declined participation turn was holding.

    A log record rather than a constructor seam on `FirePath`, deliberately:
    the alternative that already carries this text is the shadow mirror, and
    `run_case` forces shadow OFF because turning it on holds every send at the
    Deliverer and fails every speak case vacuously. A handler observes the same
    turn without being inside it.
    """

    #: The prefix of the DEBUG record in `nora/app/fire.py`. The text is read
    #: off `record.args`, so the log formatting never has to be reversed.
    PREFIX = "unrouted participation prose"

    #: The logger carrying the record. Raised to DEBUG for the duration and put
    #: back on exit, so the capture works whatever the ambient logging config is
    #: and a case that raises cannot leave the next one's logging turned up.
    LOGGER = "nora.app.fire"

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.prose: list[str] = []
        self._log = logging.getLogger(self.LOGGER)
        self._prior_level = self._log.level

    def __enter__(self) -> _UnroutedProseCapture:
        self._prior_level = self._log.level
        self._log.setLevel(logging.DEBUG)
        self._log.addHandler(self)
        return self

    def __exit__(self, *_exc: object) -> None:
        self._log.removeHandler(self)
        self._log.setLevel(self._prior_level)

    def emit(self, record: logging.LogRecord) -> None:
        if record.getMessage().startswith(self.PREFIX) and record.args:
            self.prose.append(str(record.args[-1]))


def _participation_silence_from_metrics(metrics: Metrics) -> int:
    """Times the model was offered the floor and declined it, off
    `nora_participation_silence_total`. See `Trajectory.participation_silence`
    for what this can and cannot distinguish."""
    return int(sum(
        value for (name, _labels), value in metrics.snapshot().items()
        if name == "nora_participation_silence_total"
    ))


def _tool_calls_from_metrics(metrics: Metrics) -> list[str]:
    """Reconstruct which tools the LIVE model actually called, from the
    per-tool call counters (`_TOOL_CALL_METRICS`).

    In scripted mode the harness knows the tool sequence up front — it is what
    the script tries to call. A live run has no script, so the tool names are
    read back off the counters that record real invocations. This is a MULTISET,
    not an ordered trace: Prometheus counters carry a value per label set, not an
    event log, so a tool called twice appears as two list entries but the
    interleaving with other tools is lost. That is enough for the tool-CHOICE
    evaluators D2 grades ("did she reach for weather or where_is?"), which is the
    "same same but different" signal; a strict path-order evaluator would need a
    separate ordered trace and is out of scope here.

    `status` ("ok"/"refused"/"unavailable") is summed over on purpose: a call the
    tool BODY saw is a routing choice whether or not it succeeded — a `place_info`
    that refused a person name still reached for place_info. But this counts only
    calls that reached the tool body: a call `ReplyGuard` VETOES first (R-7 budget,
    an unknown/hallucinated name) records `nora_guard_vetoes_total{rule}` — NO
    `tool` label — and is invisible here. So the reconstructed count is a LOWER
    BOUND on attempts, capped by the ReplyGuard budgets: `ConvergesInBudget`
    therefore cannot see the worst flail (a tool hammered past its budget), and a
    journey budget ≥ a tool's guard budget can never fail on that tool. Good enough
    for tool-CHOICE grading, not for a true attempt count.
    """
    calls: list[str] = []
    for (name, labels), value in metrics.snapshot().items():
        if name not in _TOOL_CALL_METRICS:
            continue
        tool = dict(labels).get("tool")
        if tool is None:
            continue
        calls.extend([tool] * int(value))
    return calls


def _build_body(case: Case) -> str:
    """Prefix `case.input` with the trigger text for trigger cases (unless the author
    already embedded it plausibly in the message), pass no-trigger cases through as-is.
    """
    if not case.trigger:
        return case.input
    if TRIGGER_TEXT.lower() in case.input.lower():
        return case.input
    return f"{TRIGGER_TEXT} {case.input}"


class _MutableClock:
    """The participation trigger's clock: the harness advances it past
    `settle_seconds` between noting the message and closing the window, so a
    case runs in milliseconds rather than wall-clock seconds. Monotonic-shaped
    (`float` seconds), matching `ParticipationTrigger`'s `clock` type."""

    def __init__(self, start: float = 1_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


async def run_case(
    case: Case, *, script: list[ScriptedTurn] | None = None, live: bool = False,
    effort: str | None = None, seed: ProductionSeed | None = None,
    skills_dir: str | Path | None = None,
) -> Trajectory:
    """Run one eval `Case` through a real pipeline graph and return the resulting
    `Trajectory`.

    Two mutually exclusive drive modes:

    - **Scripted (default, `live=False`)** — `script` is the scripted model turn
      sequence (offline, no network — see module docstring). The tool names it
      *attempts* become `Trajectory.tool_calls`: in scripted mode we already know
      exactly which tools the script tries to call, whether or not `ReplyGuard`
      ends up vetoing any given call, and that's the simplest, most direct signal
      of "what did the model try to do" for the evaluators to grade.

    - **Live (`live=True`)** — drives Nora's REAL model
      (`tests.offline_eval_support.build_live_runner`, `cfg.llm`) through the same
      real pipeline, reaching the network. `script` is ignored. There is no
      scripted tool list to read, so `Trajectory.tool_calls` is reconstructed
      after the run from the `nora_tool_calls_total` metric the tools emit (see
      `_tool_calls_from_metrics` for the multiset-not-trace caveat). A live run
      needs the LLM key env set; `build_live_runner` raises otherwise.

      Live-only knobs:
      - `effort` overrides `cfg.llm.reasoning_effort` for THIS run (compare
        effort levels without rewriting config.toml; `build_live_runner`).
      - `seed` injects a read-only `ProductionSeed` snapshot into the fake
        stores, so the model sees a production-shaped prompt (real transcript
        rows, real Honcho cards/conclusions) while all writes stay fake.
      - `skills_dir` turns the DEC-50 on-demand skill layer ON, mirroring
        production's `cfg.doctrine.dir.parent / "skills"`. None (default)
        leaves it inert, so plain gate runs are unaffected.
    """
    turns = script or []
    tool_calls = [name for turn in turns for name, _ in turn.tool_calls]
    # A participation case: the incoming message is UNTAGGED, noted
    # by the real `RelevanceGatePolicy`/`ParticipationTrigger`, and exactly one
    # determination runs through the real `FirePath.speak` once the harness
    # clock settles the window. The verdict is read off `Trajectory.sent`.
    participation = case.expect is not None
    # ONE read of the wall clock for the whole case, the same
    # "interpret once" discipline `build_prefill` uses for its own clock: the
    # seeded transcript's base AND the judged line's own timestamp below come
    # from this SAME `now_ms`, so the ordering between them is by
    # CONSTRUCTION rather than by two reads of the clock happening to agree.
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    if case.seed is not None:
        # Per-case seed — built nora-side so every tier row lands in
        # the eval unit (the footgun: unit-keyed rows seeded anywhere
        # else silently never render).
        seed = seed_from_dict(case.seed, now_ms=now_ms)

    cfg = load_eval_config((REPO_ROOT / "config.toml", REPO_ROOT / "config.example.toml"))
    # The eval measures the DETERMINATION (does the model decide speak/silence
    # correctly), never the shadow gate. A deployment config.toml with
    # `[participation] shadow = true` (the true-shadow review surface) would
    # otherwise hold every send at the Deliverer and make all speak cases
    # fail vacuously — the suite would be grading the gate, not the model.
    # Forced off here, unconditionally: shadow is a production rollout
    # surface, and the eval's job is the model's grounded-value decision.
    cfg = cfg.model_copy(update={
        "participation": cfg.participation.model_copy(update={"shadow": False}),
    })
    metrics = Metrics()
    doctrine_store = DoctrineStore(
        DOCTRINE_DIR, strict=cfg.doctrine.strict, reload=cfg.doctrine.reload,
        canary=cfg.doctrine.canary, display_name=cfg.doctrine.display_name,
        trigger=cfg.engagement.trigger.text, language_hint=cfg.doctrine.language_hint,
        tool_names=TOOL_NAMES, metrics=metrics,
    )

    with (
        tempfile.TemporaryDirectory(prefix="nora-eval-") as tmp,
        _UnroutedProseCapture() as prose_capture,
    ):
        identity_path = Path(tmp) / "identity.toml"
        identity_path.write_text(_IDENTITY_TOML)
        persons = load_persons(identity_path)

        audit = FakeRecordStore()
        memory = FakeMemoryStore()
        if seed is not None:
            # Production-shape start state: real transcript rows + real Honcho
            # read surfaces, so the model's prompt LOOKS like production's
            # without any of this run's writes leaving the fakes. Rows are
            # seeded through the store's append-shaped in-memory list; they
            # must be transcript rows already remapped onto the eval room
            # (`_EVAL_GROUP_ID`), which `build_production_seed` does.
            for row in seed.recents:
                audit.rows.append({
                    "id": row.id, "platform": "signal",
                    "conversation_id": _EVAL_GROUP_ID,
                    "sender_id": row.sender_id, "sender_name": row.sender_name,
                    # The seed rows carry production's RESOLVED person id (the
                    # roster's canonical id, e.g. "miles") — never derived from
                    # sender_id, which is the raw platform id ("+852…", Discord
                    # snowflake) and would mis-resolve every seeded speaker.
                    "person_id": row.person_id,
                    "ts": row.ts, "body": row.body, "is_nora": row.is_nora,
                    "delivery_status": row.delivery_status,
                    "quoted_ts": row.quoted_ts, "raw_json": row.raw_json,
                    "media_json": row.media_json,
                })
            memory = FakeMemoryStore(cards=dict(seed.cards),
                                 conclusions=dict(seed.conclusions))
        # The note/standing tiers, gated the same way `nora.app.
        # composition.build_dependencies` gates them — `cfg` here is the same
        # shipped `config.toml`/`config.example.toml` production reads, so a
        # tier off in the deployment stays `None` in the eval too rather than
        # rendering a block production never would. In-memory doubles, never
        # the real SQL stores: an eval run writes nothing real.
        #
        # EXCEPT the participation suite, which wires both
        # UNCONDITIONALLY: the grounded-value gate is meaningless with
        # the tiers off, and the knowledge-cut twins must differ ONLY in seed
        # content — a twin with the tier absent entirely would be a different
        # prompt SHAPE, not a cut fact. An enabled-but-empty tier still renders
        # its extent line («she holds N, shown none»), which is the honest
        # shape a knowledge-cut case is graded against.
        notes_store = InMemoryNoteStore() if (cfg.notes.enabled or participation) else None
        standing_store = (
            InMemoryStandingStore() if (cfg.standing.enabled or participation) else None
        )
        if seed is not None:
            if notes_store is not None:
                for note, revision in seed.notes:
                    notes_store.notes[note.id] = note
                    notes_store.revisions[revision.id] = revision
            if standing_store is not None and seed.standing is not None:
                # Remapped onto the eval room's own key — `seed.standing`
                # carries whatever room production last folded, which is never
                # the eval room's own conversation id.
                await standing_store.write(standing=dataclasses.replace(
                    seed.standing, platform="signal",
                    conversation_id=_EVAL_GROUP_ID,
                ))
        adapter = FakeAdapter()
        # Live mode swaps the scripted double for Nora's real, networked model;
        # everything else in the graph (db, memory, adapter) stays a fake, so a
        # live run measures the MODEL's behaviour against real doctrine+tools
        # without touching a real platform or store.
        runner = (
            build_live_runner(cfg, effort=effort, skills_dir=skills_dir)
            if live else FakeAgentRunner(turns)
        )
        # The participation occasion's own runner — `tool_choice="auto"` in
        # live mode (`build_live_participation_runner`, the mirror of
        # production's composition), because the determination must be able to
        # end in silence and the tag runner's `required` pin makes that
        # inexpressible. Scripted mode replays the same `script` through a
        # second fake, so the offline path exercises the REAL trigger and
        # FirePath with only the model scripted.
        participation_runner = None
        if participation:
            participation_runner = (
                build_live_participation_runner(cfg, effort=effort, skills_dir=skills_dir)
                if live else FakeAgentRunner(turns)
            )
        participation_clock = _MutableClock() if participation else None
        # Live mode wires the canned backends so the routing gate's siblings
        # answer `ok` and a leak is a real choice, not a fallback artifact. Scripted
        # mode keeps the bare registry — its tool_calls come from the script.
        # `notes=` is what lets `note_read`/`note_search` answer. Without it
        # the note tools are registered against a `None` store and say
        # `unavailable`, so a seeded note reaches the prompt only as a
        # title-and-description index line and knowledge-cut twins cannot flip.
        tool_registry = (
            build_eval_tool_registry(notes=notes_store) if live else None
        )
        # Live mode also names the room so `saved_places`/`where_is` clear the R-31
        # subset gate (the room {miles} ⊆ the eval circle {miles}); scripted mode
        # leaves the room unavailable.
        roster = (
            FakeRosterSource(groups={_EVAL_GROUP_ID: (frozenset({_EVAL_MEMBER_ID}),)})
            if live else None
        )
        try:
            dispatcher, _engagement, background, trigger = build_offline_pipeline(
                cfg, adapter=adapter, audit=audit, memory=memory, persons=persons,
                doctrine_store=doctrine_store, runner=runner, metrics=metrics,
                tool_registry=tool_registry, roster_source=roster,
                notes=notes_store, standing=standing_store,
                participation_runner=participation_runner,
                participation_clock=participation_clock,
                participation_opt_in=participation,
            )
            body = _build_body(case)
            # Mirrors tests/integration/harness.py::_HandlerView.on_event: parse the
            # raw signal-cli-shaped event the same way SignalAdapter.listen() would
            # internally, then dispatch only if it parsed to something.
            #
            # `ts=` is EXPLICIT for a participation case — never
            # `group_event`'s bare `ts=1` default. Stamped at the SAME `now_ms`
            # `seed_from_dict` above ended its own window one step before, so
            # the untagged line under judgement lands strictly after every
            # seeded row: the newest thing `FakeRecordStore.recent`'s
            # `(ts, id)` sort sees, rendered LAST, never sorted first above a
            # window it is 56 years older than. A non-participation case
            # (`tool_calls`/tag-suite driven) keeps the bare default: nothing
            # here renders an age for those, and `ts=1` is what the rest of
            # `tests/` and `evals/` already assume of `group_event`.
            message = await adapter.parse(
                group_event(body=body, ts=now_ms if participation else 1)
            )
            if message is not None:
                await dispatcher.on_message(message)
            if participation and trigger is not None:
                # The quiescence window: noting the message opened it;
                # advance past the settle window and close it, which runs the
                # ONE determination for the whole window through FirePath.speak.
                assert participation_clock is not None
                participation_clock.now += cfg.participation.settle_seconds + 1.0
                await trigger.close_due_windows()
            await _drain(background)
        finally:
            await audit.aclose()

        # HER feeds, counted by PEER rather than by a text prefix: Honcho takes
        # one-speaker-per-peer streams (DEC-36), so no `"[Nora]"` prefix exists
        # anywhere in `nora/` and a prefix count would be structurally 0.
        # Vacuously true while nothing delivers on this path.
        feeds = len([f for f in memory.fed if f[0] == cfg.memory.ai_peer])
        sent = list(adapter.texts)
        # Live mode has no script to read tool intent from — reconstruct it from
        # what the tools actually recorded this run (see the helper's caveat).
        # Read UNCONDITIONALLY. `nora_llm_stop_reason_total` is incremented by
        # the production stages for a scripted agent exactly as for a live one,
        # so gating this read on `live` would make None mean two different things —
        # "the model was never invoked" on the live path, and "scripted" on the
        # other. `ParticipationVerdict` treats a None as an ERROR rather than a
        # correct silence, and that is only sound if None has one meaning.
        # (`tool_calls` stays live-only: a scripted case carries its tool intent
        # in the script itself.)
        stop_reason = _stop_reason_from_metrics(metrics)
        silent_failures = _silent_failures_from_metrics(metrics)
        participation_silence = _participation_silence_from_metrics(metrics)
        # WHAT ACTUALLY RAN, on both paths. Falling back to the names in the
        # SCRIPT would make every offline evaluator grade the script rather
        # than the run: `NoSendWithoutTrigger` would assert «send_reply called
        # 0 times» about a list the harness had been handed, and mutating
        # `nora.tag.is_tagged` to return True for every message would leave
        # `--suite no_trigger` green.
        #
        # A scripted turn drives the REAL tool registry, so the counters move
        # for it too — the same thing that made `stop_reason` readable offline.
        tool_calls = _tool_calls_from_metrics(metrics)
        return Trajectory(
            tool_calls=tool_calls,
            sent=sent,
            reply=sent[-1] if sent else None,
            honcho_feeds=feeds,
            prefill_sufficient=case.prefill_sufficient,
            stop_reason=stop_reason,
            silent_failures=silent_failures,
            participation_silence=participation_silence,
            unrouted_prose=(prose_capture.prose[-1] if prose_capture.prose else ""),
            metrics=metrics,
        )

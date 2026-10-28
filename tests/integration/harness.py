"""Integration harness: real pipelines, real db, real guards — fake model, fake senders.

Shared wiring for every scenario. `build_handler` is the single entry point: it
assembles a real `IngestPipeline` + `EngagementPipeline`, glued by a real
`Dispatcher` (`nora.app.dispatch`), against a real (throwaway) Postgres
database and the real, already-validated doctrine files, with only the model
and the outbound sender faked. The wiring below is the harness-side mirror of
`nora.app.composition.build_dependencies`, using `nora.testing`/local fakes
wherever `build_dependencies` would use a real collaborator.

`ScriptedTurn`, `FakeAgentRunner`, and `group_event` live in `nora.testing`,
one definition shared with the offline eval harness. The
ingest+engagement+dispatcher wiring tail lives in
`tests.offline_pipeline.build_offline_pipeline`, which `run_case` in
`tests/offline_eval_harness.py` calls instead of duplicating this module's
pipeline assembly.
"""

from __future__ import annotations

import asyncio
import dataclasses
import os
import re
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from nora.app.dispatch import Dispatcher
from nora.app.participation import ParticipationTrigger
from nora.app.runtime import BackgroundTasks
from nora.config import Config
from nora.doctrine.loader import DoctrineStore
from nora.domain.models import DeliveryStatus
from nora.domain.ports import RecordStore, RosterSource
from nora.engagement.pipeline import EngagementPipeline
from nora.engagement.slots import ConversationSlots
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.tag_traces import SqlTagTraceStore
from nora.ingest.policy import RelevanceGatePolicy
from nora.obs.metrics import Metrics
from nora.persons import load_persons
from nora.policy import load_policy
from nora.testing import FakeAgentRunner, FakeMemoryStore, ScriptedTurn
from tests.builders import load_example_config, override_config
from tests.fakes import SinkSender
from tests.offline_pipeline import build_offline_pipeline

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCTRINE_DIR = REPO_ROOT / "nora" / "doctrine"

OWN_ACCOUNT = "+85290000100"

_DEFAULT_IDENTITY_TOML = (
    "[persons.miles]\n"
    "name = \"Miles\"\n"
    "[[persons.miles.signal]]\n"
    "phone = \"+852111\"\n"
)


class _StoreProxy:
    """Delegates everything to a real store; subclasses break exactly one method.

    The store is an injected object, so a fault is injected by subclassing
    rather than by patching a module attribute.
    """

    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)


class _FailingAppendStore(_StoreProxy):
    # Keep this signature in lockstep with `RecordStore.append`. It forwards
    # every argument, so a parameter added to the port and not added here is
    # not an inert omission: the ambient append raises TypeError, ingest drops
    # the message, and the tag path this fault exists to exercise never runs.
    async def append(
        self,
        *,
        message,
        is_nora=False,
        delivery_status=DeliveryStatus.PENDING,
        person_id=None,
    ):
        if is_nora:
            raise RuntimeError("disk full")
        return await self._inner.append(
            message=message,
            is_nora=is_nora,
            delivery_status=delivery_status,
            person_id=person_id,
        )


class _FailingRecentStore(_StoreProxy):
    async def recent(self, *, conversation_id, limit):
        raise RuntimeError("db down")


class _HarnessAdapter(SinkSender):
    """SinkSender + a configurable own-account, scoped to this integration harness only."""

    def __init__(self, *, succeed: bool = True, account: str = OWN_ACCOUNT) -> None:
        super().__init__(succeed=succeed)
        self._account = account

    async def parse(self, raw_event: dict):
        from nora.platform.signal.parser import parse_event

        return parse_event(raw_event, self._account)


class _HandlerView:
    """Thin test-side view combining `Dispatcher.on_message` with direct access
    to the conversation slots, for the R-9/R-10 tests that read
    `h.handler.slots`.

    `on_event` keeps a raw-dict-in shape deliberately: every test in this suite
    calls `h.handler.on_event(group_event(...))`, and this method is what stands
    in for the SSE loop that, in production, lives inside `SignalAdapter.listen` —
    parsing the raw event and dropping it silently if it parses to `None`, then
    calling `Dispatcher.on_message` with the result, exactly like the adapter's
    internal `_on_event` does. `IngestPipeline` accepts no raw dicts, so this
    parse-then-dispatch step cannot move into `Dispatcher` itself.

    This is a proxy, not a retention: `slots` is a `@property` returning the
    SAME `ConversationSlots` the pipeline was built with and releases into on
    every exit path (`nora/engagement/pipeline.py`'s `finally`) — nothing is
    copied or accumulated here, so this is a live view into already-bounded
    state.
    """

    def __init__(
        self, *, dispatcher: Dispatcher, engagement: EngagementPipeline, adapter: Any
    ) -> None:
        self._dispatcher = dispatcher
        self._engagement = engagement
        self._adapter = adapter

    async def on_event(self, raw_event: dict) -> None:
        message = await self._adapter.parse(raw_event)
        if message is not None:
            await self._dispatcher.on_message(message)

    @property
    def slots(self) -> ConversationSlots:
        return self._engagement._slots


@dataclasses.dataclass
class Harness:
    handler: _HandlerView
    sink: SinkSender
    honcho: FakeMemoryStore
    metrics: Metrics
    own_account: str
    doctrine_store: DoctrineStore
    # Port, not the concrete SqlRecordStore: under db_append_fails/context_read_fails
    # this is a _StoreProxy subclass instead (see the two subclasses above).
    store: RecordStore
    # The reasoning-continuity retention store — sharing `store`'s own engine
    # (see its construction in `build_handler`), so `store.aclose()` tears it
    # down too. Exposed for the tests that assert a driven tag left a
    # retrievable `TagTrace` row.
    tag_trace_store: SqlTagTraceStore
    # Cumulative call log fed by every `FakeAgentRunner` this harness has ever used
    # (including ones `set_script` swaps in) — see `FakeAgentRunner`'s docstring for
    # why this outlives any single runner instance.
    _calls: list
    _engagement: EngagementPipeline
    _background: BackgroundTasks
    _tmp_path: Path
    _identity_toml: str
    _register: Callable[[Harness], None] | None = None
    # The participation occasion's quiescence trigger, None unless
    # `participation_script` was given. Tests drive `close_due_windows()`
    # directly against the injected clock — never a sleep.
    participation_trigger: ParticipationTrigger | None = None

    async def aclose(self) -> None:
        await self.store.aclose()

    @property
    def agents_constructed(self) -> int:
        return len(self._calls)

    @property
    def last_prefill(self):
        return self._calls[-1]["pre_fill"] if self._calls else None

    @property
    def last_limits(self):
        return self._calls[-1]["limits"] if self._calls else None

    @property
    def prefills(self) -> list:
        return [c["pre_fill"] for c in self._calls]

    @property
    def guards(self) -> list:
        return [c["guard"] for c in self._calls]

    async def drain(self) -> None:
        """Await every background task spawned so far — ambient memory-feeds,
        spawned tags, AND `EngagementPipeline`'s post-reply exchange feed all
        land in the same shared `BackgroundTasks`: `EngagementPipeline` takes an
        injected `spawn` instead of rolling its own private task set, so
        `tests.offline_pipeline.build_offline_pipeline` wires both pipelines'
        spawns to the one `background.spawn` (see `nora/engagement/pipeline.py`'s
        `spawn` constructor parameter) — including ones spawned by tasks that are
        themselves still in flight.

        Deliberately loops with `asyncio.sleep(0)` after each `gather`, not just
        `while pending: await gather(*pending)`: a task's own coroutine can finish
        (and be reported `done()`) before its `add_done_callback` discard runs —
        done-callbacks are always delivered via `call_soon`, one loop tick later,
        even for a callback registered on an already-done future. `sleep(0)`
        unconditionally yields once per iteration, which lets that discard land.
        """
        while self._background._tasks:
            pending = list(self._background._tasks)
            await asyncio.gather(*pending, return_exceptions=True)
            await asyncio.sleep(0)

    def set_script(self, script) -> None:
        """Swap the active model runner in place, for a second tag in the same
        Harness (the model is stateless across invocations).

        Private-attribute reassignment on `EngagementPipeline` — a test-harness-only
        seam, not a production hook.
        """
        new_runner = script if isinstance(script, FakeAgentRunner) \
            else FakeAgentRunner(script, on_call=self._calls.append)
        self._engagement._runner = new_runner

    async def simulate_restart(self, *, script=None, **kwargs) -> Harness:
        """Build a brand new Harness (fresh pipelines, fresh debounce/in-flight state)
        that reuses the same DATABASE_URL and the same Honcho double, so the record
        store and Honcho continuity are real — a "restart" only ever loses in-process state.

        Closes this Harness's store engine first, mirroring a real process exit —
        the new Harness opens its own engine against the same database. Passes this
        Harness's own `_register` callback through, so the replacement is owned by
        whatever fixture (`harness`/`harness_factory`) built the parent, and a chain
        of restarts stays fully covered by that fixture's teardown rather than
        needing a second `aclose()` bolted on somewhere.
        """
        await self.store.aclose()
        return await build_handler(
            self._tmp_path, script=script, honcho=self.honcho,
            identity_toml=self._identity_toml, own_account=self.own_account,
            register=self._register, **kwargs,
        )


def _build_config(
    tmp_path: Path, *, persons_path: Path, policy_path: Path, doctrine_dir: Path,
    invocation_timeout: float | None = None,
    debounce: float | None = None,
) -> Config:
    base = load_example_config(tmp_path)

    changes: dict[str, object] = {
        "store.data_dir": tmp_path,
        "doctrine.dir": doctrine_dir,
        # The Dreamer's corpus is validated at startup, so it has to point at a
        # real tree; the integration harness has no reason to fork it.
        "dreamer.dir": REPO_ROOT / "nora" / "dreamer",
        "identity_file": persons_path,
        "policy_file": policy_path,
    }
    if invocation_timeout is not None:
        changes["llm.timeout_seconds"] = invocation_timeout
    if debounce is not None:
        changes["engagement.trigger.min_interval_seconds"] = debounce
    return override_config(base, changes)


async def build_handler(
    tmp_path: Path,
    *,
    script=None,
    honcho: FakeMemoryStore | None = None,
    sink: SinkSender | None = None,
    identity_toml: str | None = None,
    own_account: str = OWN_ACCOUNT,
    raises: Exception | None = None,
    delay: float = 0.0,
    invocation_timeout: float | None = None,
    debounce: float | None = None,
    fault: str | None = None,
    register: Callable[[Harness], None] | None = None,
    roster: RosterSource | None = None,
    tool_registry=None,
    participation_script=None,
    participation_rooms: tuple[str, ...] = ("g1",),
    participation_clock: Callable[[], float] | None = None,
) -> Harness:
    """Assemble real pipelines wired to a tmp db + a tmp COPY of the real doctrine dir +
    fakes.

    `register`, if given, is called with the finished `Harness` before it's returned,
    and is threaded onto the `Harness` itself so `simulate_restart` can call it again
    for the replacement. The caller (a `harness`/`harness_factory` fixture) passes its
    own bookkeeping list's `.append` here so every `Harness` reachable from a test —
    including one built by a chain of restarts — gets torn down at fixture teardown.

    `script` is either a `FakeAgentRunner` instance, a list of `ScriptedTurn`s
    (wrapped into a `FakeAgentRunner` for you), or `None` (wrapped into an empty,
    zero-turn `FakeAgentRunner`). `EngagementPipeline` always gets a non-`None`
    `runner` from this factory, so `nora.app.agent_runner.LiveAgentRunner`'s real,
    live-network branch is unreachable from this test suite by construction (a
    dedicated `@pytest.mark.network` smoke test covers that branch).

    `tool_registry` is threaded straight to `build_offline_pipeline`, which
    otherwise builds a bare `ToolRegistry()` (every tool inert/`unavailable`).
    A scenario that needs a tool to answer `ok` — the version tool needs a
    `VersionManifest` pointed at a real stamp — passes one wired with the
    collaborator it cares about, and everything else stays inert.

    `fault` injects exactly one dependency failure (R-5's silent-failure
    matrix) into an otherwise-normal build; `fault=None` (the default) leaves every
    other caller's behavior unchanged.

    `participation_script`, when given, wires the participation occasion the way
    the composition root does: the rooms named in `participation_rooms` get
    `relevance_gate = true` in policy.toml, a REAL `FirePath` is built with the
    scripted model as its `participation_runner`, and a `ParticipationTrigger`
    over the engagement pipeline's own slots is handed to the dispatcher — so an
    untagged message in an opted-in room is NOTED, and nothing runs until the
    test closes the window itself with
    `harness.participation_trigger.close_due_windows()` (the clock is
    `participation_clock`, injected for the same reason `Debounce`'s is).
    """
    identity_toml = identity_toml or _DEFAULT_IDENTITY_TOML
    persons_path = tmp_path / "identity.toml"
    persons_path.write_text(identity_toml)

    # Never point config at the real, shared nora/doctrine/*.md files: doctrine_reload_fails
    # writes invalid content into a doctrine file, and that must never touch the repo's
    # production content. The copy is byte-identical, so this has no behavior difference
    # for every other test. dirs_exist_ok=True makes a second build_handler call on the
    # same tmp_path (simulate_restart) a harmless idempotent re-copy.
    doctrine_copy_dir = tmp_path / "doctrine"
    shutil.copytree(DOCTRINE_DIR, doctrine_copy_dir, dirs_exist_ok=True)

    # The integration harness drives synthetic conversation ids, so its policy
    # admits any room; the room gate has its own unit tests. `persons = "*"`
    # still refuses an UNMAPPED sender, which is the behaviour these tests care
    # about preserving. Each room has its own unit to keep these scenarios'
    # shared state isolated.
    policy_path = tmp_path / "policy.toml"
    policy_path.write_text(
        'default = "deny"\n'
        + "".join(
            f'[[conversations]]\nplatform = "signal"\nid = "{cid}"\n'
            f'label = "{cid}"\npersons = "*"\nunit = "room-{cid}"\n'
            + (
                # The participation occasion's room opt-in: only present
                # when the scenario wired a participation runner at all.
                "relevance_gate = true\n"
                if participation_script is not None and cid in participation_rooms
                else ""
            )
            for cid in ("g1", "g2", "a", "A", "b", "B")
        )
    )

    cfg = _build_config(
        tmp_path, persons_path=persons_path, policy_path=policy_path,
        doctrine_dir=doctrine_copy_dir,
        invocation_timeout=invocation_timeout, debounce=debounce,
    )

    database_url = os.environ[cfg.store.database_url_env]
    run_migrations(database_url)
    # ONE engine shared by `store` and `tag_trace_store`, mirroring
    # `nora.app.composition.build_dependencies`'s own "one engine, one pool"
    # convention — `store.aclose()` below disposes it for both.
    engine = create_sql_engine(database_url)
    store = SqlRecordStore(engine)
    tag_trace_store = SqlTagTraceStore(engine)
    persons = load_persons(persons_path)
    metrics = Metrics()
    doctrine_store = DoctrineStore(
        cfg.doctrine.dir,
        strict=cfg.doctrine.strict,
        reload=cfg.doctrine.reload,
        canary=cfg.doctrine.canary,
        display_name=cfg.doctrine.display_name,
        trigger=cfg.engagement.trigger.text,
        metrics=metrics,
    )

    honcho_double = honcho if honcho is not None else FakeMemoryStore()
    adapter = sink if sink is not None else _HarnessAdapter(account=own_account)
    calls: list = []

    if script is None:
        # Never leave the runner unset: an empty FakeAgentRunner keeps EngagementPipeline's
        # `runner` non-None so `LiveAgentRunner`'s real, live-network branch is unreachable
        # from this suite by construction (a dedicated @pytest.mark.network test
        # covers that branch).
        runner = FakeAgentRunner([], delay=delay, raises=raises, on_call=calls.append)
    elif isinstance(script, FakeAgentRunner):
        runner = script
    else:
        runner = FakeAgentRunner(script, delay=delay, raises=raises, on_call=calls.append)

    # Fault dispatch: applied AFTER the normal build above (so most faults mutate an
    # already-built object) and BEFORE wiring the pipelines (so a cfg mutation like the
    # timeout override below is picked up).
    if fault == "llm_error":
        runner = FakeAgentRunner(
            script if not isinstance(script, FakeAgentRunner) else [],
            raises=RuntimeError("omniroute down"), on_call=calls.append,
        )
    elif fault == "llm_timeout":
        runner = FakeAgentRunner(
            script if not isinstance(script, FakeAgentRunner) else [],
            delay=5.0, on_call=calls.append,
        )
        cfg = override_config(cfg, {"llm.timeout_seconds": 0.2})
    elif fault == "empty_reply":
        # bypass_guard=True: ReplyGuard's own R-4 pre-tool veto would otherwise catch a
        # whitespace-only text before Deliverer ever sees it. This exercises Deliverer's
        # own (defense-in-depth) empty-text refusal directly.
        runner = FakeAgentRunner([ScriptedTurn(tool_calls=[("send_reply", {"text": "   "})],
            final_text="   ", bypass_guard=True)], on_call=calls.append)
    elif fault == "no_send_call":
        # `final_text=""` — a turn that produces NOTHING, which is what "no
        # script" should mean. Prose without a `send_reply` call is recovered and
        # delivered (DEC-93), so any placeholder text here would be sent to the group.
        runner = FakeAgentRunner([ScriptedTurn(final_text="")], on_call=calls.append)
    elif fault == "leak_guard_trips":
        # A >=40-char verbatim span of nora/doctrine/core/constraints.md's body, read live so
        # this can't drift from the real file. LeakGuard normalizes whitespace before
        # matching, so we do too, to be sure the span really lands inside its haystack.
        raw_body = (DOCTRINE_DIR / "core" / "constraints.md").read_text().split("---", 2)[-1]
        leak_span = re.sub(r"\s+", " ", raw_body).strip()[:60]
        runner = FakeAgentRunner([ScriptedTurn(tool_calls=[("send_reply", {"text": leak_span})],
            final_text=leak_span)], on_call=calls.append)
    elif fault == "honcho_down":
        honcho_double.fail = True
    elif fault == "send_fails":
        adapter._succeed = False   # SinkSender's existing succeed flag, flipped post-construction
    elif fault == "db_append_fails":
        # Both the ambient path and Deliverer go through the same injected `store` —
        # failing every write would break ambient ingest too, not just the tag path this
        # fault means to exercise. `_FailingAppendStore` fails only is_nora=True appends
        # (Deliverer's), so ambient writes still succeed.
        store = _FailingAppendStore(store)
    elif fault == "context_read_fails":
        store = _FailingRecentStore(store)
    elif fault == "doctrine_reload_fails":
        doctrine_store.get(applies_to="tag")     # warm the last-good cache before corrupting
        (doctrine_copy_dir / "core" / "constraints.md").write_text("not valid doctrine content\n")

    # The participation occasion's runner: built BEFORE the pipeline so it can
    # be threaded into `build_offline_pipeline`, which then builds the real
    # FirePath + quiescence trigger itself — the one definition of that wiring,
    # shared with `evals/` (which may not import `nora.app.fire` to build its
    # own).
    participation_runner = None
    if participation_script is not None:
        participation_runner = (
            participation_script
            if isinstance(participation_script, FakeAgentRunner)
            else FakeAgentRunner(participation_script, on_call=calls.append)
        )
    dispatcher, engagement, background, participation_trigger = build_offline_pipeline(
        cfg, adapter=adapter, audit=store, memory=honcho_double, persons=persons,
        doctrine_store=doctrine_store, runner=runner, metrics=metrics,
        # None by default, which is what a scenario with no roster needs:
        # `Room` reports an absent source as UNAVAILABLE, never as an empty room,
        # so the room-scoped tools degrade honestly. A scenario that needs Nora
        # to TAG somebody has to say who is actually in the group — and the
        # roster gate means a scenario that forgets gets no mention at all,
        # which is the fail-closed behaviour rather than a broken test.
        roster_source=roster,
        tool_registry=tool_registry,
        tag_trace_store=tag_trace_store,
        extra_engage_policies=(
            (RelevanceGatePolicy(load_policy(policy_path)),)
            if participation_script is not None else ()
        ),
        participation_runner=participation_runner,
        participation_clock=participation_clock,
    )
    handler = _HandlerView(dispatcher=dispatcher, engagement=engagement, adapter=adapter)

    harness = Harness(
        handler=handler,
        sink=adapter,
        honcho=honcho_double,
        metrics=metrics,
        own_account=getattr(adapter, "_account", own_account),
        doctrine_store=doctrine_store,
        store=store,
        tag_trace_store=tag_trace_store,
        _calls=calls,
        _engagement=engagement,
        _background=background,
        _tmp_path=tmp_path,
        _identity_toml=identity_toml,
        _register=register,
        participation_trigger=participation_trigger,
    )
    if register is not None:
        register(harness)
    return harness

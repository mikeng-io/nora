"""Integration harness: real pipelines, real db, real guards — fake model, fake senders.

Shared wiring for every M5 scenario. `build_handler` is the single entry point: it
assembles a real `IngestPipeline` + `EngagementPipeline`, glued by a real `Dispatcher`
(`nora.app.dispatch`), against a real (tmp) sqlite db and the real, already-validated
persona files, with only the model and the outbound sender faked. This is Task 8's
cutover from the deleted `Handler`: the wiring below is the harness-side mirror of
`nora.app.composition.build_dependencies`, using `nora.testing`/local fakes wherever
`build_dependencies` would use a real collaborator.

`ScriptedTurn`, `FakeAgentRunner`, and `group_event` moved to `nora.testing` in Task
10 (Phase 3): `evals/harness.py` needed the same scripted-model double and event
builder without importing `tests/`, so there is one definition, imported here and
there. The ingest+engagement+dispatcher wiring tail itself moved to
`nora.app.offline.build_offline_pipeline` for the same reason — `evals/` may import
`nora.app` and `nora.testing` only, so that function is what it calls instead of
duplicating this module's pipeline assembly.
"""

from __future__ import annotations

import asyncio
import dataclasses
import re
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from nora.app.dispatch import Dispatcher
from nora.app.offline import build_offline_pipeline
from nora.app.runtime import BackgroundTasks
from nora.config import Config
from nora.domain.models import DeliveryStatus
from nora.domain.ports import AuditStore
from nora.engagement.pipeline import EngagementPipeline
from nora.infra.sql.audit import SqlAuditStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.obs.metrics import Metrics
from nora.persona.loader import PersonaStore
from nora.persons import load_persons
from nora.testing import FakeAgentRunner, ScriptedTurn
from tests.builders import load_example_config, override_config
from tests.fakes import FakeHoncho, SinkSender

REPO_ROOT = Path(__file__).resolve().parents[2]
PERSONA_DIR = REPO_ROOT / "nora" / "persona"

OWN_ACCOUNT = "+85290000100"

_DEFAULT_IDENTITY_TOML = (
    "[persons.miles]\n"
    "name = \"Miles\"\n"
    "[[persons.miles.signal]]\n"
    "phone = \"+852111\"\n"
)


class _StoreProxy:
    """Delegates everything to a real store; subclasses break exactly one method.

    This replaces `monkeypatch.setattr(db, "append", ...)`, which stopped
    working when the store became an injected object instead of a module.
    """

    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)


class _FailingAppendStore(_StoreProxy):
    # Keep this signature in lockstep with `AuditStore.append`. It forwards
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
    to `EngagementPipeline`'s in-flight set, for the R-9/R-10 tests written
    against `h.handler._in_flight` (the deleted `Handler`'s surface).

    `on_event` keeps its pre-phase-4 name and raw-dict-in shape deliberately:
    every test in this suite already calls `h.handler.on_event(group_event(...))`,
    and this method is what stands in for the SSE loop that, in production, now
    lives inside `SignalAdapter.listen` — parsing the raw event and dropping it
    silently if it parses to `None`, then calling `Dispatcher.on_message` with
    the result, exactly like the adapter's internal `_on_event` does (Trap B,
    docs/superpowers/plans/2026-08-05-nora-phase4-platforms.md). `IngestPipeline`
    no longer accepts raw dicts at all, so this parse-then-dispatch step cannot
    move into `Dispatcher` itself.

    This is a proxy, not a retention: `_in_flight` is a `@property` reading the
    SAME set `EngagementPipeline` already owns and clears on every exit path
    (`nora/engagement/pipeline.py`'s `finally`) — nothing is copied or accumulated
    here. That distinction matters: `Handler._guards`/`_prefills` were lists that
    grew forever; this is a live view into already-bounded state.
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
    def _in_flight(self):
        return self._engagement._in_flight


@dataclasses.dataclass
class Harness:
    handler: _HandlerView
    sink: SinkSender
    honcho: FakeHoncho
    metrics: Metrics
    own_account: str
    persona_store: PersonaStore
    # Port, not the concrete SqlAuditStore: under db_append_fails/context_read_fails
    # this is a _StoreProxy subclass instead (see the two subclasses above).
    store: AuditStore
    # Cumulative call log fed by every `FakeAgentRunner` this harness has ever used
    # (including ones `set_script` swaps in) — see `FakeAgentRunner`'s docstring for
    # why this outlives any single runner instance.
    _calls: list
    _engagement: EngagementPipeline
    _background: BackgroundTasks
    _tmp_path: Path
    _identity_toml: str
    _register: Callable[[Harness], None] | None = None

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
        land in the same shared `BackgroundTasks` now (Important 2's fix:
        `EngagementPipeline` takes an injected `spawn` instead of rolling its
        own private task set, so `nora.app.offline.build_offline_pipeline`
        wires both pipelines' spawns to the one `background.spawn` — see
        `nora/engagement/pipeline.py`'s `spawn` constructor parameter) —
        including ones spawned by tasks that are themselves still in flight.

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
        Harness (statelessness scenario 9: the model is stateless across invocations).

        Private-attribute reassignment on `EngagementPipeline`, mirroring what the
        pre-phase-3 harness did to `Handler._invoke_fn` — a test-harness-only seam,
        not a production hook.
        """
        new_runner = script if isinstance(script, FakeAgentRunner) \
            else FakeAgentRunner(script, on_call=self._calls.append)
        self._engagement._runner = new_runner

    async def simulate_restart(self, *, script=None, **kwargs) -> Harness:
        """Build a brand new Harness (fresh pipelines, fresh debounce/in-flight state)
        that reuses the same db_path and the same Honcho double, so SQLite and Honcho
        continuity are real — a "restart" only ever loses in-process state.

        Closes this Harness's store engine first, mirroring a real process exit —
        the new Harness opens its own engine against the same db_path. Passes this
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
    tmp_path: Path, *, persons_path: Path, policy_path: Path, persona_dir: Path,
    invocation_timeout: float | None = None,
    debounce: float | None = None,
) -> Config:
    base = load_example_config(tmp_path)

    changes: dict[str, object] = {
        "store.db_path": tmp_path / "messages.db",
        "persona.dir": persona_dir,
        "identity_file": persons_path,
        "policy_file": policy_path,
    }
    if invocation_timeout is not None:
        changes["llm.tiers.flash.timeout_seconds"] = invocation_timeout
    if debounce is not None:
        changes["engagement.trigger.min_interval_seconds"] = debounce
    return override_config(base, changes)


async def build_handler(
    tmp_path: Path,
    *,
    script=None,
    honcho: FakeHoncho | None = None,
    sink: SinkSender | None = None,
    identity_toml: str | None = None,
    own_account: str = OWN_ACCOUNT,
    raises: Exception | None = None,
    delay: float = 0.0,
    invocation_timeout: float | None = None,
    debounce: float | None = None,
    fault: str | None = None,
    register: Callable[[Harness], None] | None = None,
) -> Harness:
    """Assemble real pipelines wired to a tmp db + a tmp COPY of the real persona dir +
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
    live-network branch is unreachable from this test suite by construction (that
    is Task 4's dedicated `@pytest.mark.network` smoke test's job).

    `fault` injects exactly one dependency failure (spec B.3/R-5's silent-failure
    matrix) into an otherwise-normal build; `fault=None` (the default) leaves every
    other caller's behavior unchanged.
    """
    identity_toml = identity_toml or _DEFAULT_IDENTITY_TOML
    persons_path = tmp_path / "identity.toml"
    persons_path.write_text(identity_toml)

    # Never point config at the real, shared nora/persona/*.md files: persona_reload_fails
    # writes invalid content into a persona file, and that must never touch the repo's
    # production content. The copy is byte-identical, so this has no behavior difference
    # for every other test. dirs_exist_ok=True makes a second build_handler call on the
    # same tmp_path (simulate_restart) a harmless idempotent re-copy.
    persona_copy_dir = tmp_path / "persona"
    shutil.copytree(PERSONA_DIR, persona_copy_dir, dirs_exist_ok=True)

    # The integration harness drives synthetic conversation ids, so its policy
    # admits any room; the room gate has its own unit tests. `persons = "*"`
    # still refuses an UNMAPPED sender, which is the behaviour these tests care
    # about preserving.
    policy_path = tmp_path / "policy.toml"
    policy_path.write_text(
        'default = "deny"\n'
        + "".join(
            f'[[conversations]]\nplatform = "signal"\nid = "{cid}"\n'
            'persons = "*"\n'
            for cid in ("g1", "g2", "a", "A", "b", "B")
        )
    )

    cfg = _build_config(
        tmp_path, persons_path=persons_path, policy_path=policy_path,
        persona_dir=persona_copy_dir,
        invocation_timeout=invocation_timeout, debounce=debounce,
    )

    run_migrations(cfg.store.db_path)
    store = SqlAuditStore(create_sql_engine(cfg.store.db_path))
    persons = load_persons(persons_path)
    metrics = Metrics()
    persona_store = PersonaStore(
        cfg.persona.dir,
        strict=cfg.persona.strict,
        reload=cfg.persona.reload,
        canary=cfg.persona.canary,
        display_name=cfg.persona.display_name,
        trigger=cfg.engagement.trigger.text,
        metrics=metrics,
    )

    honcho_double = honcho if honcho is not None else FakeHoncho()
    adapter = sink if sink is not None else _HarnessAdapter(account=own_account)
    calls: list = []

    if script is None:
        # Never leave the runner unset: an empty FakeAgentRunner keeps EngagementPipeline's
        # `runner` non-None so `LiveAgentRunner`'s real, live-network branch is unreachable
        # from this suite by construction (Task 4's dedicated @pytest.mark.network test
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
        cfg = override_config(cfg, {"llm.tiers.flash.timeout_seconds": 0.2})
    elif fault == "empty_reply":
        # bypass_guard=True: ReplyGuard's own R-4 pre-tool veto would otherwise catch a
        # whitespace-only text before Deliverer ever sees it. This exercises Deliverer's
        # own (defense-in-depth) empty-text refusal directly.
        runner = FakeAgentRunner([ScriptedTurn(tool_calls=[("send_reply", {
            "conversation_id": "g1", "conversation_type": "group", "text": "   "})],
            final_text="   ", bypass_guard=True)], on_call=calls.append)
    elif fault == "no_send_call":
        runner = FakeAgentRunner([ScriptedTurn(final_text="silent")], on_call=calls.append)
    elif fault == "leak_guard_trips":
        # A >=40-char verbatim span of nora/persona/guardrails.md's body, read live so
        # this can't drift from the real file. LeakGuard normalizes whitespace before
        # matching, so we do too, to be sure the span really lands inside its haystack.
        raw_body = (PERSONA_DIR / "guardrails.md").read_text().split("---", 2)[-1]
        leak_span = re.sub(r"\s+", " ", raw_body).strip()[:60]
        runner = FakeAgentRunner([ScriptedTurn(tool_calls=[("send_reply", {
            "conversation_id": "g1", "conversation_type": "group", "text": leak_span})],
            final_text=leak_span)], on_call=calls.append)
    elif fault == "honcho_down":
        honcho_double.fail = True
    elif fault == "send_fails":
        adapter._succeed = False   # SinkSender's existing succeed flag, flipped post-construction
    elif fault == "db_append_fails":
        # Both the ambient path and Deliverer now go through the same injected `store` —
        # failing every write would break ambient ingest too, not just the tag path this
        # fault means to exercise. `_FailingAppendStore` fails only is_nora=True appends
        # (Deliverer's), so ambient writes still succeed.
        store = _FailingAppendStore(store)
    elif fault == "context_read_fails":
        store = _FailingRecentStore(store)
    elif fault == "persona_reload_fails":
        persona_store.get(applies_to="tag")     # warm the last-good cache before corrupting
        (persona_copy_dir / "guardrails.md").write_text("not valid persona content\n")

    dispatcher, engagement, background = build_offline_pipeline(
        cfg, adapter=adapter, audit=store, memory=honcho_double, persons=persons,
        persona_store=persona_store, runner=runner, metrics=metrics,
    )
    handler = _HandlerView(dispatcher=dispatcher, engagement=engagement, adapter=adapter)

    harness = Harness(
        handler=handler,
        sink=adapter,
        honcho=honcho_double,
        metrics=metrics,
        own_account=getattr(adapter, "_account", own_account),
        persona_store=persona_store,
        store=store,
        _calls=calls,
        _engagement=engagement,
        _background=background,
        _tmp_path=tmp_path,
        _identity_toml=identity_toml,
        _register=register,
    )
    if register is not None:
        register(harness)
    return harness

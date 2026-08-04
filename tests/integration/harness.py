"""Integration harness: real handler, real db, real guards — fake LLM, fake senders.

Shared wiring for every M5 scenario (Tasks 31-35). `build_handler` is the single entry
point: it assembles a real `Handler` against a real (tmp) sqlite db and the real,
already-validated persona files, with only the LLM and the outbound sender faked.
"""

from __future__ import annotations

import asyncio
import dataclasses
import re
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from nora.config import Config
from nora.handler import Handler
from nora.infra.sql.audit import SqlAuditStore
from nora.infra.sql.engine import create_sql_engine
from nora.obs.metrics import Metrics
from nora.persona.loader import PersonaStore
from nora.persons import load_persons
from nora.store import db
from tests.builders import load_example_config, override_config
from tests.fakes import FakeHoncho, SinkSender

REPO_ROOT = Path(__file__).resolve().parents[2]
PERSONA_DIR = REPO_ROOT / "nora" / "persona"

OWN_ACCOUNT = "+85290000100"

_DEFAULT_IDENTITY_TOML = (
    "[persons.miles]\n"
    "name = \"Miles\"\n"
    "[persons.miles.platforms]\n"
    "signal = \"+852111\"\n"
)


def group_event(*, body: str, sender: str = "+852111", conv: str = "g1",
                name: str = "Miles", ts: int = 1) -> dict:
    return {"method": "receive", "params": {"envelope": {
        "source": sender, "sourceName": name, "timestamp": ts,
        "dataMessage": {"message": body, "groupInfo": {"groupId": conv}, "timestamp": ts}}}}


@dataclasses.dataclass
class ScriptedTurn:
    """One model turn: tool calls to perform, then the final text.

    `bypass_guard` is an escape hatch for tests that specifically exercise a lower
    layer's own defenses (e.g. Deliverer's own empty-text/leak checks) independent of
    ReplyGuard's pre-tool veto — everywhere else it stays False and behavior is
    unchanged.
    """
    tool_calls: list[tuple[str, dict]] = dataclasses.field(default_factory=list)
    final_text: str = ""
    bypass_guard: bool = False


class FakeLLM:
    """Replays a scripted turn sequence against the real tools and the real guards.

    This stands in for factory.invoke — the agent loop itself is Strands' concern and
    is covered by M0's contract test; what M5 must prove is *our* orchestration.
    """

    def __init__(self, turns: list[ScriptedTurn], *, delay: float = 0.0,
                 raises: Exception | None = None) -> None:
        self.turns = turns
        self.delay = delay
        self.raises = raises
        self.invocations = 0

    async def __call__(self, tools_by_name: dict, guard, tag_message: str) -> str:
        self.invocations += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.raises:
            raise self.raises
        text = ""
        for turn in self.turns:
            for name, args in turn.tool_calls:
                if not turn.bypass_guard:
                    veto = guard.evaluate(name, args)
                    if veto:
                        continue                   # the model sees the correction and moves on
                await _invoke_tool(tools_by_name[name], **args)
                guard.record(name)
            text = turn.final_text or text
        return text


async def _invoke_tool(tool, **kwargs):
    """Call a Strands @tool-decorated function the way a scripted turn exercises it."""
    result = tool(**kwargs)
    if asyncio.iscoroutine(result):
        result = await result
    return result


class _HarnessAdapter(SinkSender):
    """SinkSender + a configurable own-account, scoped to this integration harness only."""

    def __init__(self, *, succeed: bool = True, account: str = OWN_ACCOUNT) -> None:
        super().__init__(succeed=succeed)
        self._account = account

    async def parse(self, raw_event: dict):
        from nora.platform.signal.parser import parse_event

        return parse_event(raw_event, self._account)


class _Counter:
    """A plain int can't be rebound by a closure — this gives create_agent_fn somewhere
    to mutate that the harness can read back.
    """

    def __init__(self) -> None:
        self.value = 0

    def increment(self) -> None:
        self.value += 1


@dataclasses.dataclass
class Harness:
    handler: Handler
    db_path: Path
    sink: SinkSender
    honcho: FakeHoncho
    metrics: Metrics
    own_account: str
    persona_store: PersonaStore
    store: SqlAuditStore
    _agents: _Counter
    _tmp_path: Path
    _identity_toml: str
    _register: Callable[[Harness], None] | None = None

    async def aclose(self) -> None:
        await self.store.aclose()

    @property
    def agents_constructed(self) -> int:
        return self._agents.value

    @property
    def last_prefill(self):
        return self.handler._last_prefill

    @property
    def last_limits(self):
        return self.handler._last_limits

    @property
    def prefills(self) -> list:
        return self.handler._prefills

    @property
    def guards(self) -> list:
        return self.handler._guards

    async def drain(self) -> None:
        """Await every background task `Handler._spawn` has launched, including ones
        spawned by tasks that are themselves still in flight (e.g. `_feed_ambient`).
        """
        while self.handler._bg:
            await asyncio.gather(*list(self.handler._bg), return_exceptions=True)

    def set_script(self, script) -> None:
        """Swap the handler's invoke function in place, for a second tag in the same
        Harness (statelessness scenario 9: the model is stateless across invocations).
        """
        self.handler._invoke_fn = script if isinstance(script, FakeLLM) else FakeLLM(script)

    async def simulate_restart(self, *, script=None, **kwargs) -> Harness:
        """Build a brand new Harness (fresh Handler, fresh debounce/in-flight state)
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
    tmp_path: Path, *, persons_path: Path, persona_dir: Path,
    invocation_timeout: float | None = None,
    debounce: float | None = None,
) -> Config:
    base = load_example_config(tmp_path)

    changes: dict[str, object] = {
        "store.db_path": tmp_path / "messages.db",
        "persona.dir": persona_dir,
        "identity_file": persons_path,
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
    monkeypatch: pytest.MonkeyPatch | None = None,
    register: Callable[[Harness], None] | None = None,
) -> Harness:
    """Assemble a real `Handler` wired to a tmp db + a tmp COPY of the real persona dir +
    fakes.

    `register`, if given, is called with the finished `Harness` before it's returned,
    and is threaded onto the `Harness` itself so `simulate_restart` can call it again
    for the replacement. The caller (a `harness`/`harness_factory` fixture) passes its
    own bookkeeping list's `.append` here so every `Harness` reachable from a test —
    including one built by a chain of restarts — gets torn down at fixture teardown.

    `script` is either a `FakeLLM` instance, a list of `ScriptedTurn`s (wrapped into a
    `FakeLLM` for you), or `None` (wrapped into an empty, zero-turn `FakeLLM`). `Handler`
    always gets a non-`None` `invoke_fn` from this factory, so the real, live-network
    `factory.*` branch of `Handler._invoke` is unreachable from this test suite by
    construction — no test here should ever exercise it (that is Task 4's dedicated
    `@pytest.mark.network` smoke test's job).

    `fault` injects exactly one dependency failure (Task 33's silent-failure matrix, spec
    B.3/R-5) into an otherwise-normal build; `fault=None` (the default) leaves every other
    caller's behavior byte-for-byte unchanged from Task 31/32.
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

    cfg = _build_config(
        tmp_path, persons_path=persons_path, persona_dir=persona_copy_dir,
        invocation_timeout=invocation_timeout, debounce=debounce,
    )

    await db.init(cfg.store.db_path)
    store = SqlAuditStore(create_sql_engine(cfg.store.db_path))
    persons = load_persons(persons_path)
    persona_store = PersonaStore(
        cfg.persona.dir,
        strict=cfg.persona.strict,
        reload=cfg.persona.reload,
        canary=cfg.persona.canary,
        display_name=cfg.persona.display_name,
        trigger=cfg.engagement.trigger.text,
    )

    honcho_double = honcho if honcho is not None else FakeHoncho()
    adapter = sink if sink is not None else _HarnessAdapter(account=own_account)
    metrics = Metrics()
    counter = _Counter()

    if script is None:
        # Never leave invoke_fn unset: an empty FakeLLM keeps Handler._invoke_fn non-None so
        # the real factory.*/live-network branch of _invoke is unreachable from this suite by
        # construction (Task 4's dedicated @pytest.mark.network test covers that branch).
        invoke_fn = FakeLLM([], delay=delay, raises=raises)
    elif isinstance(script, FakeLLM):
        invoke_fn = script
    else:
        invoke_fn = FakeLLM(script, delay=delay, raises=raises)

    # Fault dispatch: applied AFTER the normal build above (so most faults mutate an
    # already-built object) and BEFORE constructing Handler (so a cfg mutation like the
    # timeout override below is picked up).
    if fault == "llm_error":
        invoke_fn = FakeLLM(script if not isinstance(script, FakeLLM) else [],
                            raises=RuntimeError("omniroute down"))
    elif fault == "llm_timeout":
        invoke_fn = FakeLLM(script if not isinstance(script, FakeLLM) else [], delay=5.0)
        cfg = override_config(cfg, {"llm.tiers.flash.timeout_seconds": 0.2})
    elif fault == "empty_reply":
        # bypass_guard=True: ReplyGuard's own R-4 pre-tool veto would otherwise catch a
        # whitespace-only text before Deliverer ever sees it. This exercises Deliverer's
        # own (defense-in-depth) empty-text refusal directly.
        invoke_fn = FakeLLM([ScriptedTurn(tool_calls=[("send_reply", {
            "conversation_id": "g1", "conversation_type": "group", "text": "   "})],
            final_text="   ", bypass_guard=True)])
    elif fault == "no_send_call":
        invoke_fn = FakeLLM([ScriptedTurn(final_text="silent")])
    elif fault == "leak_guard_trips":
        # A >=40-char verbatim span of nora/persona/guardrails.md's body, read live so
        # this can't drift from the real file. LeakGuard normalizes whitespace before
        # matching, so we do too, to be sure the span really lands inside its haystack.
        raw_body = (PERSONA_DIR / "guardrails.md").read_text().split("---", 2)[-1]
        leak_span = re.sub(r"\s+", " ", raw_body).strip()[:60]
        invoke_fn = FakeLLM([ScriptedTurn(tool_calls=[("send_reply", {
            "conversation_id": "g1", "conversation_type": "group", "text": leak_span})],
            final_text=leak_span)])
    elif fault == "honcho_down":
        honcho_double.fail = True
    elif fault == "send_fails":
        adapter._succeed = False   # SinkSender's existing succeed flag, flipped post-construction
    elif fault == "db_append_fails" and monkeypatch is not None:
        # Deliverer's audit write goes through `store`, not the `db` module — the
        # ambient path is the only caller left on `db.append`, and it never carries
        # is_nora=True, so failing every write on `store` reaches only Deliverer.
        async def _failing_append(*a, **kw):
            raise RuntimeError("disk full")

        monkeypatch.setattr(store, "append", _failing_append)
    elif fault == "context_read_fails" and monkeypatch is not None:
        async def _failing_recent(*a, **kw):
            raise RuntimeError("db down")

        monkeypatch.setattr(db, "recent", _failing_recent)
    elif fault == "persona_reload_fails":
        persona_store.get(applies_to="tag")     # warm the last-good cache before corrupting
        (persona_copy_dir / "guardrails.md").write_text("not valid persona content\n")

    handler = Handler(
        cfg=cfg,
        adapter=adapter,
        persons=persons,
        honcho=honcho_double,
        persona_store=persona_store,
        store=store,
        metrics=metrics,
        invoke_fn=invoke_fn,
        create_agent_fn=counter.increment,
    )

    harness = Harness(
        handler=handler,
        db_path=cfg.store.db_path,
        sink=adapter,
        honcho=honcho_double,
        metrics=metrics,
        own_account=getattr(adapter, "_account", own_account),
        persona_store=persona_store,
        store=store,
        _agents=counter,
        _tmp_path=tmp_path,
        _identity_toml=identity_toml,
        _register=register,
    )
    if register is not None:
        register(harness)
    return harness

"""Integration harness: real handler, real db, real guards — fake LLM, fake senders.

Shared wiring for every M5 scenario (Tasks 31-35). `build_handler` is the single entry
point: it assembles a real `Handler` against a real (tmp) sqlite db and the real,
already-validated persona files, with only the LLM and the outbound sender faked.
"""

from __future__ import annotations

import asyncio
import dataclasses
import shutil
from pathlib import Path

from nora.config import Config, load as load_config
from nora.handler import Handler
from nora.obs.metrics import Metrics
from nora.persona.loader import PersonaStore
from nora.persons import load_persons
from nora.store import db
from tests.fakes import FakeHoncho, SinkSender

REPO_ROOT = Path(__file__).resolve().parents[2]
PERSONA_DIR = REPO_ROOT / "nora" / "persona"

OWN_ACCOUNT = "+85290000100"

_DEFAULT_PERSONS_YAML = (
    "persons:\n"
    "  miles:\n"
    "    name: Miles\n"
    "    platforms:\n"
    "      signal: \"+852111\"\n"
)


def group_event(*, body: str, sender: str = "+852111", conv: str = "g1",
                name: str = "Miles", ts: int = 1) -> dict:
    return {"method": "receive", "params": {"envelope": {
        "source": sender, "sourceName": name, "timestamp": ts,
        "dataMessage": {"message": body, "groupInfo": {"groupId": conv}, "timestamp": ts}}}}


@dataclasses.dataclass
class ScriptedTurn:
    """One model turn: tool calls to perform, then the final text."""
    tool_calls: list[tuple[str, dict]] = dataclasses.field(default_factory=list)
    final_text: str = ""


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
                veto = guard.evaluate(name, args)
                if veto:
                    continue                       # the model sees the correction and moves on
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
    _agents: _Counter

    @property
    def agents_constructed(self) -> int:
        return self._agents.value

    async def drain(self) -> None:
        """Await every background task `Handler._spawn` has launched, including ones
        spawned by tasks that are themselves still in flight (e.g. `_feed_ambient`).
        """
        while self.handler._bg:
            await asyncio.gather(*list(self.handler._bg), return_exceptions=True)


def _build_config(tmp_path: Path, *, persons_path: Path) -> Config:
    cfg_path = tmp_path / "config.yaml"
    shutil.copy(REPO_ROOT / "config.example.yaml", cfg_path)
    base = load_config(cfg_path)
    return dataclasses.replace(
        base,
        buffer=dataclasses.replace(base.buffer, db_path=tmp_path / "messages.db"),
        persona=dataclasses.replace(base.persona, dir=PERSONA_DIR),
        persons_file=persons_path,
    )


async def build_handler(
    tmp_path: Path,
    *,
    script=None,
    honcho: FakeHoncho | None = None,
    sink: SinkSender | None = None,
    persons_yaml: str | None = None,
    own_account: str = OWN_ACCOUNT,
) -> Harness:
    """Assemble a real `Handler` wired to a tmp db + the real persona dir + fakes.

    `script` is either a `FakeLLM` instance, a list of `ScriptedTurn`s (wrapped into a
    `FakeLLM` for you), or `None` — the ambient path (this task) never invokes it.
    """
    persons_path = tmp_path / "persons.yaml"
    persons_path.write_text(persons_yaml or _DEFAULT_PERSONS_YAML)
    cfg = _build_config(tmp_path, persons_path=persons_path)

    await db.init(cfg.buffer.db_path)
    persons = load_persons(persons_path)
    persona_store = PersonaStore(
        cfg.persona.dir,
        strict=cfg.persona.strict,
        reload=cfg.persona.reload,
        canary=cfg.persona.canary,
        display_name=cfg.persona.display_name,
        trigger=cfg.trigger.text,
    )

    honcho_double = honcho if honcho is not None else FakeHoncho()
    adapter = sink if sink is not None else _HarnessAdapter(account=own_account)
    metrics = Metrics()
    counter = _Counter()

    if script is None:
        invoke_fn = None
    elif isinstance(script, FakeLLM):
        invoke_fn = script
    else:
        invoke_fn = FakeLLM(script)

    handler = Handler(
        cfg=cfg,
        adapter=adapter,
        persons=persons,
        honcho=honcho_double,
        persona_store=persona_store,
        metrics=metrics,
        invoke_fn=invoke_fn,
        create_agent_fn=counter.increment,
    )

    return Harness(
        handler=handler,
        db_path=cfg.buffer.db_path,
        sink=adapter,
        honcho=honcho_double,
        metrics=metrics,
        own_account=getattr(adapter, "_account", own_account),
        _agents=counter,
    )

"""Proves `build_offline_pipeline`'s remaining tier collaborators
(`notes`/`standing`, threaded through `nora.app.assembler.build_assembler` — see
`tests/architecture/test_assembler_parity.py` for the structural half of this
guarantee) actually reach the model-visible prompt, not just that they are
accepted as parameters and silently dropped somewhere on the way to
`nora.agent.prefill.build_prefill` (via the `pre_fill` `invoke.run` already
hands the runner).

Drives one real message through a real `Dispatcher` built by
`build_offline_pipeline`, exactly as `evals/harness.py::run_case` does, with
`nora.testing`'s in-memory doubles seeded ahead of time. The `TagContext` the
pipeline actually assembled is recovered off `FakeAgentRunner`'s `on_call`
log — the same `ctx=` keyword `nora.engagement.stages.invoke.run` passes to
every `AgentRunner.invoke` in production — so this checks the SAME object a
live turn would render from, not a hand-built stand-in.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from nora.doctrine.loader import DoctrineStore
from nora.domain.notes import Note, NoteRevision
from nora.domain.standing import Standing
from nora.obs.metrics import Metrics
from nora.persons import load_persons
from nora.testing import (
    FakeAdapter,
    FakeAgentRunner,
    FakeMemoryStore,
    FakeRecordStore,
    InMemoryNoteStore,
    InMemoryStandingStore,
    ScriptedTurn,
    group_event,
)
from tests.offline_eval_support import TOOL_NAMES
from tests.offline_pipeline import build_offline_pipeline

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCTRINE_DIR = REPO_ROOT / "nora" / "doctrine"

_IDENTITY_TOML = (
    '[persons.miles_novak]\nname = "Miles"\n'
    '[[persons.miles_novak.signal]]\nphone = "+852111"\n'
)


def _flatten(prefill: list[dict]) -> str:
    return "\n".join(
        block["text"] for message in prefill for block in message["content"]
    )



@pytest.fixture(autouse=True)
def _omniroute_key(monkeypatch):
    """Loading a config is fail-closed on the LLM key — the same refusal that
    protects production (`nora/config.py`: "refusing to start").

    Tests take the `example_config` fixture (R-B), which sets the placeholder itself, so they do not
    depend on the developer's shell exporting the real key; this autouse fixture covers the tests
    that build the pipeline without it.

    Nothing here reaches a provider — the model is scripted — so a placeholder
    is the honest value.
    """
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")

async def test_seeded_notes_and_standing_render_in_the_assembled_prompt(
    tmp_path, example_config,
):
    cfg = example_config
    identity_path = tmp_path / "identity.toml"
    identity_path.write_text(_IDENTITY_TOML)
    persons = load_persons(identity_path)
    metrics = Metrics()
    doctrine_store = DoctrineStore(
        DOCTRINE_DIR, strict=cfg.doctrine.strict, reload=cfg.doctrine.reload,
        canary=cfg.doctrine.canary, display_name=cfg.doctrine.display_name,
        trigger=cfg.engagement.trigger.text, language_hint=cfg.doctrine.language_hint,
        tool_names=TOOL_NAMES, metrics=metrics,
    )

    notes_store = InMemoryNoteStore()
    notes_store.notes["11111111-0000-4000-8000-000000000001"] = Note(
        id="11111111-0000-4000-8000-000000000001", unit="nora",
        title="a tracked uncertainty", description="whether the venue is booked",
        uncertainty="not confirmed", closing_condition="someone confirms the booking",
        established_in="g1", created_at=1,
        head_revision_id="22222222-0000-4000-8000-000000000002",
        anchor_date=20260101,
    )
    notes_store.revisions["22222222-0000-4000-8000-000000000002"] = NoteRevision(
        id="22222222-0000-4000-8000-000000000002",
        note_id="11111111-0000-4000-8000-000000000001",
        body="the venue booking is still unconfirmed", parent_revision_id=None,
        written_at=1, established_by="tag-seed",
    )

    standing_store = InMemoryStandingStore()
    await standing_store.write(standing=Standing(
        platform="signal", conversation_id="g1",
        body="the group is planning a weekend trip", fold_count=1, updated_at=1,
    ))

    audit = FakeRecordStore()
    memory = FakeMemoryStore()
    adapter = FakeAdapter()
    calls: list[dict] = []
    runner = FakeAgentRunner(
        [ScriptedTurn(final_text="ok")], on_call=calls.append,
    )

    try:
        dispatcher, _, background, _trigger = build_offline_pipeline(
            cfg, adapter=adapter, audit=audit, memory=memory, persons=persons,
            doctrine_store=doctrine_store, runner=runner, metrics=metrics,
            notes=notes_store, standing=standing_store,
        )
        message = await adapter.parse(group_event(body="nora.000 hi"))
        assert message is not None
        await dispatcher.on_message(message)
        # Mirrors `evals.harness._drain`: a task's own coroutine can report
        # `done()` before its `add_done_callback` discard runs, so drain until
        # the set is genuinely empty rather than gathering once.
        while background._tasks:
            await asyncio.gather(*list(background._tasks), return_exceptions=True)
            await asyncio.sleep(0)
    finally:
        await audit.aclose()

    assert calls, "the scripted runner was never invoked — no turn ran"
    # The pipeline passes the assembled `pre_fill` to `runner.invoke`
    # (`nora/engagement/stages/invoke.py`), so `on_call` captured exactly what
    # the model sees — asserting on that instead of re-deriving through
    # `build_prefill` here rules out drift between the two call sites.
    assert "pre_fill" in calls[0], "invoke.run did not hand the runner its pre_fill"
    rendered = _flatten(calls[0]["pre_fill"])

    # Each tag carries `untrusted="true" note="..."` attributes (every
    # injected block's convention), so the check is for the OPEN TAG's
    # prefix, not a bare `<standing>`.
    assert "<standing " in rendered
    assert "the group is planning a weekend trip" in rendered
    assert "<notes " in rendered
    # The notes block is the INDEX — title + description, never the revision
    # body (`nora.language.note_render.render_notes`'s own docstring: "title
    # and description only", by design).
    assert "a tracked uncertainty" in rendered
    assert "whether the venue is booked" in rendered

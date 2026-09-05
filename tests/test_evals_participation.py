"""The participation eval suite's harness pieces (design/dreamer-agent-loop/35
§9) — the case schema's `expect`/`seed` fields, the nora-side seed builder's
workspace keying, the `sent`-based verdict, and the offline drive path that
lets a case assert speak/silence through the REAL ParticipationTrigger and
FirePath with only the model scripted.

The 15-case corpus itself (`evals/cases/participation/participation.yaml`) is
graded LIVE by `make eval-participation`; what these tests pin offline is that
the harness CAN drive the path and that the seeds reach the determination's
prompt.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from evals.evaluators import ParticipationVerdict
from evals.harness import Case, Trajectory, run_case
from nora.app.eval_support import TOOL_NAMES, load_eval_config, seed_from_dict
from nora.app.offline import build_offline_pipeline
from nora.doctrine.loader import DoctrineStore
from nora.obs.metrics import Metrics
from nora.persons import load_persons
from nora.testing import (
    FakeAdapter,
    FakeAgentRunner,
    FakeMemoryStore,
    FakeRecordStore,
    InMemoryFactStore,
    InMemoryNoteStore,
    InMemoryStandingStore,
    ScriptedTurn,
    group_event,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCTRINE_DIR = REPO_ROOT / "nora" / "doctrine"

_IDENTITY_TOML = (
    '[persons.miles_novak]\nname = "Miles"\n'
    '[[persons.miles_novak.signal]]\nphone = "+852111"\n'
)

_FACT_SEED = {
    "transcript": ["does anyone know when AWS Community Day 2026 is?"],
    "facts": [
        {
            "subject": "AWS Community Day",
            "predicate": "is scheduled for",
            "object": "October 26, 2026 at IVE (Tsing Yi)",
            "asserter": "miles_novak",
        },
    ],
}


def _flatten(prefill: list[dict]) -> str:
    return "\n".join(
        block["text"] for message in prefill for block in message["content"]
    )


# ── Case schema (§9 gap 2) ──────────────────────────────────────────────────


def test_case_from_dict_reads_expect_and_seed():
    case = Case.from_dict({
        "name": "fact-lookup-known",
        "input": "does anyone know when AWS Community Day 2026 is?",
        "expect": "speak",
        "seed": _FACT_SEED,
    })
    assert case.expect == "speak"
    assert case.seed == _FACT_SEED


def test_a_participation_case_defaults_to_no_trigger():
    """The incoming message of a participation case is UNTAGGED by construction
    — that is what makes it a participation candidate at all. An explicit
    `metadata.trigger` still wins."""
    case = Case.from_dict({"name": "c", "input": "hi", "expect": "silence"})
    assert case.trigger is False
    tagged = Case.from_dict(
        {"name": "c", "input": "hi", "expect": "speak",
         "metadata": {"trigger": True}})
    assert tagged.trigger is True
    # A tag-suite case (no `expect`) is untouched: trigger still defaults True.
    tag = Case.from_dict({"name": "c", "input": "hi"})
    assert tag.trigger is True
    assert tag.expect is None
    assert tag.seed is None


# ── The seed builder's workspace keying (§9 gap 2's footgun) ────────────────


def test_seed_from_dict_keys_every_tier_to_the_eval_workspace():
    """Notes/facts keyed outside workspace "nora" silently never render in the
    eval room — the twin would then "pass" for the wrong reason. Every seeded
    row must carry the eval workspace."""
    seed = seed_from_dict({
        "transcript": ["hi", {"name": "Grant", "body": "yo"}],
        "standing": "the group is mid-planning",
        "notes": [{"title": "t", "description": "d", "body": "b"}],
        "facts": [{"subject": "s", "predicate": "p", "object": "o",
                   "asserter": "miles_novak"}],
    })
    assert len(seed.recents) == 2
    assert seed.standing is not None
    assert all(note.workspace == "nora" for note, _ in seed.notes)
    assert all(fact.workspace == "nora" for fact in seed.facts)
    assert len(seed.asserters) == 1


def test_seed_from_dict_is_deterministic():
    a = seed_from_dict(_FACT_SEED)
    b = seed_from_dict(_FACT_SEED)
    assert [f.id for f in a.facts] == [f.id for f in b.facts]
    assert [n.id for n, _ in a.notes] == [n.id for n, _ in b.notes]


# ── The verdict (§9 gap 3): `sent`, the room-visible truth ──────────────────


def test_participation_verdict_speak():
    verdict = ParticipationVerdict("speak")
    assert verdict.evaluate(Trajectory(sent=["it's Oct 26"])).passed
    assert not verdict.evaluate(Trajectory()).passed


def test_participation_verdict_silence():
    verdict = ParticipationVerdict("silence")
    assert verdict.evaluate(Trajectory()).passed
    assert not verdict.evaluate(Trajectory(sent=["oops"])).passed


def test_participation_verdict_fails_closed_on_a_bad_expect():
    with pytest.raises(ValueError, match="speak"):
        ParticipationVerdict("maybe")


# ── The corpus itself (design §9) ───────────────────────────────────────────

CORPUS = (
    Path(__file__).resolve().parent.parent
    / "evals" / "cases" / "participation" / "participation.yaml"
)

EXPECTED_VERDICTS = {
    "fact-lookup-known": "speak", "fact-lookup-unknown": "silence",
    "stalled-question-known": "speak", "stalled-question-unknown": "silence",
    "commitment-followup-known": "speak",
    "commitment-followup-unknown": "silence",
    "correction-known": "speak", "correction-unknown": "silence",
    "planning-context-known": "speak", "planning-context-unknown": "silence",
    "status-update": "silence", "private-exchange": "silence",
    "acknowledgment": "silence", "inside-joke": "silence",
    "other-service-command": "silence",
}


def test_the_corpus_is_the_15_cases_with_their_verdicts():
    """§9's suite, pinned: 5 relevance-Yes, their 5 knowledge-cut twins, 5
    pure-No. Every case carries its seed (the twins' seeds differ from their
    speak twin ONLY by the cut tier) and a verdict `ParticipationVerdict`
    accepts."""
    from evals.harness import load_cases

    cases = load_cases(CORPUS)
    assert {c.name: c.expect for c in cases} == EXPECTED_VERDICTS
    for case in cases:
        assert case.seed is not None, f"{case.name} has no seed"
        assert case.trigger is False, f"{case.name} must arrive untagged"
        ParticipationVerdict(case.expect)  # raises on a bad expect
    # The twin structure: each -unknown case's transcript+input equals its
    # -known twin's — ONLY the seed's tier content differs.
    by_name = {c.name: c for c in cases}
    for stem in ("fact-lookup", "stalled-question", "commitment-followup",
                 "correction", "planning-context"):
        known, unknown = by_name[f"{stem}-known"], by_name[f"{stem}-unknown"]
        assert known.input == unknown.input
        assert (known.seed or {}).get("transcript") == (unknown.seed or {}).get("transcript")


# ── The offline drive path (§9 gap 1) ───────────────────────────────────────


async def test_run_case_drives_a_participation_case_to_speak():
    """A real ParticipationTrigger + real FirePath + scripted model that calls
    `send_reply` — the room must hear it (the verdict signal is `sent`)."""
    case = Case.from_dict({
        "name": "fact-lookup-known", "input": "so when is it?",
        "expect": "speak", "seed": _FACT_SEED,
    })
    trajectory = await run_case(
        case,
        script=[ScriptedTurn(
            tool_calls=[("send_reply", {"text": "Oct 26 at IVE"})],
            final_text="Oct 26 at IVE",
        )],
    )
    assert trajectory.sent == ["Oct 26 at IVE"]
    assert ParticipationVerdict("speak").evaluate(trajectory).passed


async def test_run_case_drives_a_participation_case_to_silence():
    """The same drive path, model declines the floor (prose, no send_reply):
    the room hears nothing."""
    case = Case.from_dict({
        "name": "fact-lookup-unknown", "input": "so when is it?",
        "expect": "silence", "seed": {"transcript": ["so when is it?"]},
    })
    trajectory = await run_case(
        case, script=[ScriptedTurn(final_text="I don't have that")],
    )
    assert trajectory.sent == []
    assert ParticipationVerdict("silence").evaluate(trajectory).passed


async def _drive_participation(tmp_path: Path, seed: dict | None,
                               script: list[ScriptedTurn]):
    """Build the offline pipeline with the participation occasion wired the way
    `run_case` wires it, drive one untagged message through, settle the window,
    and return (adapter, metrics, participation-runner calls)."""
    cfg = load_eval_config((REPO_ROOT / "config.toml",
                            REPO_ROOT / "config.example.toml"))
    metrics = Metrics()
    doctrine_store = DoctrineStore(
        DOCTRINE_DIR, strict=cfg.doctrine.strict, reload=cfg.doctrine.reload,
        canary=cfg.doctrine.canary, display_name=cfg.doctrine.display_name,
        trigger=cfg.engagement.trigger.text,
        language_hint=cfg.doctrine.language_hint,
        tool_names=TOOL_NAMES, metrics=metrics,
    )
    notes_store = InMemoryNoteStore()
    facts_store = InMemoryFactStore()
    standing_store = InMemoryStandingStore()
    audit = FakeRecordStore()
    if seed is not None:
        built = seed_from_dict(seed)
        # The transcript half of the seed: the fact tier's room slice matches
        # subject mentions against these rows + the incoming message, exactly
        # as `run_case` seeds them (remapped onto the eval room).
        for row in built.recents:
            audit.rows.append({
                "id": row.id, "platform": "signal", "conversation_id": "g1",
                "sender_id": row.sender_id, "sender_name": row.sender_name,
                "person_id": row.person_id, "ts": row.ts, "body": row.body,
                "is_nora": row.is_nora, "delivery_status": row.delivery_status,
                "quoted_ts": row.quoted_ts, "raw_json": row.raw_json,
                "media_json": row.media_json,
            })
        for note, revision in built.notes:
            notes_store.notes[note.id] = note
            notes_store.revisions[revision.id] = revision
        for fact in built.facts:
            facts_store.seed_fact(fact)
        if built.asserters:
            facts_store.seed_asserters(*built.asserters)

    identity_path = tmp_path / "identity.toml"
    identity_path.write_text(_IDENTITY_TOML)

    clock = [1_000_000.0]
    adapter = FakeAdapter()
    calls: list[dict] = []
    participation_runner = FakeAgentRunner(script, on_call=calls.append)
    try:
        dispatcher, _, background, trigger = build_offline_pipeline(
            cfg, adapter=adapter, audit=audit, memory=FakeMemoryStore(),
            persons=load_persons(identity_path), doctrine_store=doctrine_store,
            runner=FakeAgentRunner([ScriptedTurn(final_text="tag")]),
            metrics=metrics, notes=notes_store, facts=facts_store,
            standing=standing_store,
            participation_runner=participation_runner,
            participation_clock=lambda: clock[0],
            participation_opt_in=True,
        )
        message = await adapter.parse(group_event(body="so when is it?"))
        assert message is not None
        await dispatcher.on_message(message)
        assert trigger is not None
        clock[0] += cfg.participation.settle_seconds + 1.0
        await trigger.close_due_windows()
        while background._tasks:
            await asyncio.gather(*list(background._tasks), return_exceptions=True)
            await asyncio.sleep(0)
    finally:
        await audit.aclose()
    return adapter, metrics, calls


async def test_the_seeded_fact_reaches_the_participation_prompt(tmp_path):
    """The knowledge-cut mechanism, pinned: the same incoming message with the
    fact seeded renders `<facts>` in the determination's prompt; without the
    seed it does not. This is what makes a twin's silence meaningful — the
    twin's prompt lacks the answer, not the whole tier."""
    adapter, metrics, calls = await _drive_participation(
        tmp_path,
        _FACT_SEED,
        [ScriptedTurn(tool_calls=[("send_reply", {"text": "Oct 26 at IVE"})],
                      final_text="Oct 26 at IVE")],
    )
    assert calls, "the participation runner was never invoked"
    rendered = _flatten(calls[0]["pre_fill"])
    assert "October 26, 2026 at IVE (Tsing Yi)" in rendered
    assert adapter.texts == ["Oct 26 at IVE"]
    # The mirror of the gate's live accounting: a live participation send,
    # counted as such.
    assert metrics.get("nora_participation_spoke_total", platform="signal") == 1

    adapter2, metrics2, calls2 = await _drive_participation(
        tmp_path,
        {"transcript": ["does anyone know when AWS Community Day 2026 is?"]},
        [ScriptedTurn(final_text="I don't have that")],
    )
    assert calls2
    assert "October 26, 2026 at IVE (Tsing Yi)" not in _flatten(calls2[0]["pre_fill"])
    assert adapter2.texts == []
    assert metrics2.get("nora_participation_silence_total", platform="signal") == 1
    assert metrics2.get("nora_participation_spoke_total") == 0

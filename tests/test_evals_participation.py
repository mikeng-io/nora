"""The participation eval suite's harness pieces (design/dreamer-agent-loop/35
§9) — the case schema's `expect`/`seed` fields, the nora-side seed builder's
workspace keying, the `sent`-based verdict, and the offline drive path that
lets a case assert speak/silence through the REAL ParticipationTrigger and
FirePath with only the model scripted.

The 11-case corpus itself (`evals/cases/participation/participation.yaml`) is
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
from nora.app.eval_support import TOOL_NAMES, seed_from_dict
from nora.app.offline import build_offline_pipeline
from nora.doctrine.loader import DoctrineStore
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
from tests.builders import load_example_config

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCTRINE_DIR = REPO_ROOT / "nora" / "doctrine"

_IDENTITY_TOML = (
    '[persons.miles_novak]\nname = "Miles"\n'
    '[[persons.miles_novak.signal]]\nphone = "+852111"\n'
)

#: The knowledge-cut shape, on the NOTE tier. It was a `facts:` seed until the
#: knowledge tier was deleted; the property under test is the seed reaching the
#: determination's prompt, which is the tier-neutral half.
_GROUNDED_SEED = {
    "transcript": ["does anyone know when AWS Community Day 2026 is?"],
    "notes": [
        {
            "title": "AWS Community Day date",
            "description": "when AWS Community Day 2026 lands",
            "body": "October 26, 2026 at IVE (Tsing Yi), per the organisers.",
        },
    ],
}



@pytest.fixture(autouse=True)
def _omniroute_key(monkeypatch):
    """`load_eval_config` loads the REAL config, and that load is fail-closed on
    the LLM key — the same refusal that protects production
    (`nora/config.py`: "refusing to start").

    Every other test that loads the real config sets a placeholder for it
    (`tests/test_production_seed.py`, `tests/test_config.py`); the tests in this
    file did not, so they passed only on a machine that happened to export the
    real key and were RED in any fresh worktree. That is a test asserting the
    developer's shell, not the code.

    Nothing here reaches a provider — the model is scripted — so a placeholder
    is the honest value.
    """
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")

def _flatten(prefill: list[dict]) -> str:
    return "\n".join(
        block["text"] for message in prefill for block in message["content"]
    )


# ── Case schema (§9 gap 2) ──────────────────────────────────────────────────


def test_case_from_dict_reads_expect_and_seed():
    case = Case.from_dict({
        "name": "stalled-question-known",
        "input": "does anyone know when AWS Community Day 2026 is?",
        "expect": "speak",
        "seed": _GROUNDED_SEED,
    })
    assert case.expect == "speak"
    assert case.seed == _GROUNDED_SEED


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
    """A note keyed outside workspace "nora" silently never renders in the
    eval room — the twin would then "pass" for the wrong reason. Every seeded
    row must carry the eval workspace."""
    seed = seed_from_dict({
        "transcript": ["hi", {"name": "Grant", "body": "yo"}],
        "standing": "the group is mid-planning",
        "notes": [{"title": "t", "description": "d", "body": "b"}],
    })
    assert len(seed.recents) == 2
    assert seed.standing is not None
    assert all(note.workspace == "nora" for note, _ in seed.notes)


def test_seed_from_dict_is_deterministic():
    a = seed_from_dict(_GROUNDED_SEED)
    b = seed_from_dict(_GROUNDED_SEED)
    assert [n.id for n, _ in a.notes] == [n.id for n, _ in b.notes]


# ── The verdict (§9 gap 3): `sent`, the room-visible truth ──────────────────


def test_participation_verdict_speak():
    verdict = ParticipationVerdict("speak")
    assert verdict.evaluate(Trajectory(sent=["it's Oct 26"])).passed
    assert not verdict.evaluate(Trajectory()).passed


def test_participation_verdict_silence():
    verdict = ParticipationVerdict("silence")
    assert verdict.evaluate(Trajectory(stop_reason="end_turn")).passed
    assert not verdict.evaluate(Trajectory(sent=["oops"], stop_reason="end_turn")).passed


def test_a_turn_that_never_reached_the_model_is_an_error_not_a_silence():
    """The whole suite's floor. Eight of eleven cases expect silence, so a
    provider outage scores near-perfect unless a dead call is separated from a
    decision: pointing the base url at a closed port reproduced the suite's
    usual summary line byte for byte (audit, 2026-09-08). `stop_reason is
    None` is the only signal that says the model was never invoked."""
    result = ParticipationVerdict("silence").evaluate(Trajectory())
    assert not result.passed
    assert "ERROR" in result.reason


def test_participation_verdict_fails_closed_on_a_bad_expect():
    with pytest.raises(ValueError, match="speak"):
        ParticipationVerdict("maybe")


# ── The corpus itself (design §9) ───────────────────────────────────────────

CORPUS = (
    Path(__file__).resolve().parent.parent
    / "evals" / "cases" / "participation" / "participation.yaml"
)

EXPECTED_VERDICTS = {
    "stalled-question-known": "speak", "stalled-question-unknown": "silence",
    "commitment-followup-known": "speak",
    "commitment-followup-unknown": "silence",
    "planning-context-known": "speak", "planning-context-unknown": "silence",
    "status-update": "silence", "private-exchange": "silence",
    "acknowledgment": "silence", "inside-joke": "silence",
    "other-service-command": "silence",
    # Issue #210 — the two HONCHO-cut pairs. Every pair above cuts a tier the
    # participation turn already read; these cut her memory, which that turn
    # read nothing of at all until #210.
    "admin-joke-known": "speak",
    "admin-joke-unknown": "silence",
    "asked-before-known": "speak",
    "asked-before-unknown": "silence",
}

#: The twin stems, and WHICH seed key each one's cut lives in. A pair whose cut
#: key is not actually different between its halves is a pair that cannot flip,
#: and a case that cannot flip measures nothing — the exact reason the
#: `fact-lookup` and `correction` pairs were deleted with the knowledge tier
#: rather than rewritten onto another tier's seed.
_TWINS = {
    "stalled-question": "notes",
    "commitment-followup": "standing",
    "planning-context": "notes",
    "admin-joke": "conclusions",
    "asked-before": "conclusions",
}


def test_the_corpus_is_the_15_cases_with_their_verdicts():
    """§9's suite, pinned: 5 relevance-Yes, their 5 knowledge-cut twins, 5
    pure-No. Every case carries its seed (the twins' seeds differ from their
    speak twin ONLY by the cut tier) and a verdict `ParticipationVerdict`
    accepts.

    It was 15, then 11, and is 15 again. The `fact-lookup` and `correction`
    pairs were grounded in the KNOWLEDGE TIER and went with it — a pair with no
    grounding to cut cannot flip. The two that replace them are grounded in
    Honcho (issue #210), which is the tier the participation turn could not
    read at all until this branch."""
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
    for stem, cut in _TWINS.items():
        known, unknown = by_name[f"{stem}-known"], by_name[f"{stem}-unknown"]
        assert known.input == unknown.input
        assert (known.seed or {}).get("transcript") == (unknown.seed or {}).get("transcript")
        # The cut is REAL and it is the named key: the -known half holds
        # something under it and the -unknown half holds nothing. Asserted
        # because «only the seed differs» above is satisfied by two seeds that
        # differ in a key nothing reads.
        assert (known.seed or {}).get(cut), f"{stem}-known seeds no {cut}"
        assert not (unknown.seed or {}).get(cut), f"{stem}-unknown still seeds {cut}"


# ── The offline drive path (§9 gap 1) ───────────────────────────────────────


async def test_run_case_drives_a_participation_case_to_speak():
    """A real ParticipationTrigger + real FirePath + scripted model that calls
    `send_reply` — the room must hear it (the verdict signal is `sent`)."""
    case = Case.from_dict({
        "name": "stalled-question-known", "input": "so when is it?",
        "expect": "speak", "seed": _GROUNDED_SEED,
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
        "name": "stalled-question-unknown", "input": "so when is it?",
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
    cfg = load_example_config(tmp_path)
    metrics = Metrics()
    doctrine_store = DoctrineStore(
        DOCTRINE_DIR, strict=cfg.doctrine.strict, reload=cfg.doctrine.reload,
        canary=cfg.doctrine.canary, display_name=cfg.doctrine.display_name,
        trigger=cfg.engagement.trigger.text,
        language_hint=cfg.doctrine.language_hint,
        tool_names=TOOL_NAMES, metrics=metrics,
    )
    notes_store = InMemoryNoteStore()
    standing_store = InMemoryStandingStore()
    audit = FakeRecordStore()
    if seed is not None:
        built = seed_from_dict(seed)
        # The transcript half of the seed, seeded exactly as `run_case` does
        # (remapped onto the eval room).
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

    # The Honcho half of the seed (issue #210), injected exactly as `run_case`
    # does: `FakeMemoryStore(conclusions=...)`, keyed by the OBSERVED peer.
    memory = FakeMemoryStore(
        conclusions=dict(seed_from_dict(seed).conclusions) if seed is not None else {},
    )

    identity_path = tmp_path / "identity.toml"
    identity_path.write_text(_IDENTITY_TOML)

    clock = [1_000_000.0]
    adapter = FakeAdapter()
    calls: list[dict] = []
    participation_runner = FakeAgentRunner(script, on_call=calls.append)
    try:
        dispatcher, _, background, trigger = build_offline_pipeline(
            cfg, adapter=adapter, audit=audit, memory=memory,
            persons=load_persons(identity_path), doctrine_store=doctrine_store,
            runner=FakeAgentRunner([ScriptedTurn(final_text="tag")]),
            metrics=metrics, notes=notes_store, standing=standing_store,
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


async def test_the_seeded_grounding_reaches_the_participation_prompt(tmp_path):
    """The knowledge-cut mechanism, pinned: the same incoming message with the
    note seeded renders it in the determination's prompt; without the seed it
    does not. This is what makes a twin's silence meaningful — the twin's
    prompt lacks the answer, not the whole tier."""
    adapter, metrics, calls = await _drive_participation(
        tmp_path,
        _GROUNDED_SEED,
        [ScriptedTurn(tool_calls=[("send_reply", {"text": "Oct 26 at IVE"})],
                      final_text="Oct 26 at IVE")],
    )
    assert calls, "the participation runner was never invoked"
    rendered = _flatten(calls[0]["pre_fill"])
    assert "AWS Community Day date" in rendered
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
    assert "AWS Community Day date" not in _flatten(calls2[0]["pre_fill"])
    assert adapter2.texts == []
    assert metrics2.get("nora_participation_silence_total", platform="signal") == 1
    assert metrics2.get("nora_participation_spoke_total") == 0


async def test_the_seeded_conclusions_reach_the_participation_prompt(tmp_path):
    """Issue #210's own knowledge-cut mechanism, and the reason it needed
    building: the three pairs above cut a NOTE or a POSITION, both of which the
    participation turn already rendered. A memory-cut pair could not have
    flipped on master at all — `participation_occasion` builds `person_id=None`,
    so `peer_card` was `()` and `self_card`/`<peer_history>` were assembled only
    by the TAG path's assembler. A twin that fails for that reason is measuring
    the wiring, not the model.

    So both halves are pinned here: seeded, the conclusions render under the
    two blocks that carry them; cut, the SAME message renders neither.
    """
    seeded = {
        "transcript": ["哈哈 我哋個管理員好忙"],
        "conclusions": {
            "nora": ["the room jokingly calls nora the channel's 管理員"],
            "miles_novak": ["miles_novak calls nora 管理員 as a running joke"],
        },
    }
    _adapter, _metrics, calls = await _drive_participation(
        tmp_path, seeded,
        [ScriptedTurn(tool_calls=[("send_reply", {"text": "得，我睇下"})],
                      final_text="得，我睇下")],
    )
    assert calls, "the participation runner was never invoked"
    rendered = _flatten(calls[0]["pre_fill"])

    assert "<self_card" in rendered
    assert "the room jokingly calls nora the channel's 管理員" in rendered
    assert "<people_in_window" in rendered
    assert '<person name="Miles">' in rendered
    assert "miles_novak calls nora 管理員 as a running joke" in rendered
    # The hedge travels with the content, or the content is a directive (A2).
    assert "Every line is undated and none of it is current" in rendered

    _a2, _m2, calls2 = await _drive_participation(
        tmp_path,
        {"transcript": ["哈哈 我哋個管理員好忙"]},
        [ScriptedTurn(final_text="")],
    )
    assert calls2
    cut = _flatten(calls2[0]["pre_fill"])
    assert "管理員 as a running joke" not in cut
    assert "people_in_window" not in cut
    assert "<self_card" not in cut


# ── What the harness can see about a silence (2026-09-08) ───────────────────


async def test_a_declined_floor_is_recorded_and_is_not_a_silent_failure():
    """The positive control for `Trajectory.participation_silence`.

    Reading a zero off an instrument that cannot move is not a null result
    (CLAUDE.md), and the first version of this read the wrong series: it
    watched `nora_silent_failures_total`, which `nora/app/fire.py` never
    increments for a completed participation turn — a declined floor is the
    healthy default there and goes to `nora_participation_silence_total`.
    """
    case = Case.from_dict({
        "name": "declined", "input": "so when is it?",
        "expect": "silence", "seed": {"transcript": ["so when is it?"]},
    })
    trajectory = await run_case(
        case, script=[ScriptedTurn(final_text="I don't have that")],
    )
    assert trajectory.sent == []
    assert trajectory.participation_silence == 1, "the declined floor must be counted"
    assert trajectory.silent_failures == {}, "a declined floor is not a malfunction"


async def test_prose_she_never_routed_is_separable_from_having_nothing_to_say():
    """THE partition. Two opposite findings used to be the same row.

    "She wrote the answer and it never left the process" is a broken output
    contract; "she had nothing to say" is a doctrine calibration. They have
    opposite fixes, and on 2026-09-08 she declined 13 of 15 runs on cases that
    expected her to speak with no way to say which had happened.

    Note what stays collapsed: every counter still reads the same for both,
    because production files both under `nora_participation_silence_total` by
    design. The harness separates them; production does not.
    """
    case = Case.from_dict({
        "name": "declined", "input": "so when is it?",
        "expect": "silence", "seed": {"transcript": ["so when is it?"]},
    })
    wrote_prose = await run_case(
        case, script=[ScriptedTurn(final_text="It's Oct 26 at IVE.")],
    )
    said_nothing = await run_case(case, script=[ScriptedTurn(final_text="")])

    assert wrote_prose.unrouted_prose == "It's Oct 26 at IVE."
    assert said_nothing.unrouted_prose == ""
    # ... and the counters STILL cannot tell them apart. That is the finding.
    assert (wrote_prose.sent, wrote_prose.participation_silence,
            wrote_prose.silent_failures, wrote_prose.stop_reason) == (
        said_nothing.sent, said_nothing.participation_silence,
        said_nothing.silent_failures, said_nothing.stop_reason,
    )

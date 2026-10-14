"""The full-restore mode, the `recall_grounded` criterion and its facts, with
no database: which seams `_drive()` reaches in each mode, that every other
corpus keeps today's path, the load-time evidence check, and the facts'
wording (`evals.recall.render` is pure).

The database half (the restore itself, the identity row map, the as-of
evidence read) is `tests/integration/test_full_restore_recall.py`.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from strands_evals import Case

from evals import backbone
from evals.backbone import Spec
from evals.rebuilt.domain import Outcome
from evals.rebuilt.entry import ENTRY_POINTS, EntryPoint
from evals.recall import CRITERION, evidence_problems, render
from evals.subject_config import (
    ALLOWED_TO_DIFFER,
    FULL_RESTORE_OVERRIDES,
    adopted,
)
from nora.subject import DrawWorkspace, Sandboxed
from nora.subject.evidence import (
    Evidence,
    HeldMedia,
    HeldMessage,
    HeldNote,
    HeldOuting,
    HeldRevision,
)

_SHOT = {"platform": "signal", "conversation_id": "the-real-group", "before_ms": 1_752_000_000_000}
_NOW = datetime.fromtimestamp(_SHOT["before_ms"] / 1000, tz=UTC)
_SOURCE = "postgresql://u:p@h/src_eval"


class _Closable:
    async def cancel_all(self) -> None: ...
    async def aclose(self) -> None: ...
    async def close(self) -> None: ...


class _Subject:
    deps = SimpleNamespace(
        background=_Closable(), audit=_Closable(), client=_Closable(),
        doctrine=SimpleNamespace(doctrine_text="the doctrine"),
    )
    room = ("signal", "the-real-group")
    cfg = SimpleNamespace(engagement=SimpleNamespace(trigger=SimpleNamespace(text="nora")))


@contextlib.asynccontextmanager
async def _no_honcho_workspace(case: str, *, run_id: str):
    """The draw's Honcho workspace with no Honcho, as `test_eval_backbone.py` has it."""
    yield DrawWorkspace(id=f"nora-eval-{run_id}-{case.replace('#', '-')}-fake", create_ms=0.0)


@pytest.fixture
def seams(monkeypatch: Any) -> dict[str, list[Any]]:
    """Every seam either mode can reach, recording what reached it."""
    calls: dict[str, list[Any]] = {
        "sandbox_database": [], "sandbox_as_of": [], "sandbox_from_snapshot": [],
        "restored_room": [], "obtain": [],
    }

    @contextlib.asynccontextmanager
    async def sandbox_database(source_url: str, *, template: str | None = None):
        calls["sandbox_database"].append((source_url, template))
        yield f"{source_url}_sb"

    @contextlib.asynccontextmanager
    async def sandbox_as_of(source_url: str, at: datetime, *, template: str | None = None):
        calls["sandbox_as_of"].append((source_url, at, template))
        yield f"{source_url}_asof", SimpleNamespace(copied={})

    async def sandbox_from_snapshot(_subject: object, **kwargs: Any) -> Sandboxed:
        calls["sandbox_from_snapshot"].append(kwargs)
        return Sandboxed(now_ms=int(kwargs["before_ms"]), rows={}, transcript=())

    async def restored_room(_subject: object, **kwargs: Any) -> Sandboxed:
        calls["restored_room"].append(kwargs)
        return Sandboxed(now_ms=int(kwargs["before_ms"]), rows={}, transcript=())

    async def obtain(spec: Any) -> _Subject:
        calls["obtain"].append(spec)
        return _Subject()

    async def drive(_subject: object, _case: object, *, now_ms: int) -> Outcome:
        return Outcome(admitted=None)

    monkeypatch.setattr(backbone, "draw_workspace", _no_honcho_workspace)
    for name, fake in (
        ("sandbox_database", sandbox_database), ("sandbox_as_of", sandbox_as_of),
        ("sandbox_from_snapshot", sandbox_from_snapshot), ("restored_room", restored_room),
        ("obtain", obtain),
    ):
        monkeypatch.setattr(backbone, name, fake)
    monkeypatch.setitem(
        ENTRY_POINTS, "tag",
        EntryPoint(drive=drive, delivers_input=True, config={"port.key": 1}),
    )
    return calls


def _spec(**kwargs: Any) -> Spec:
    return Spec(
        source_url=_SOURCE, entry="tag", identity="[persons.miles_novak]\n",
        policy='default = "deny"\n', template="the_blank", **kwargs,
    )


def _case(name: str) -> Case:
    return Case(name=name, input="x", metadata={"snapshot": dict(_SHOT)})


async def test_every_other_corpus_keeps_the_snapshot_path_unchanged(seams) -> None:
    """`full_restore` defaults off, and off is TODAY'S path: the empty sandbox,
    the four-tier snapshot seeding with the entry point's window rule, and the
    subject's overrides exactly the port's, the spec's and the draw's
    workspace, in that order, with nothing of the restore's among them."""
    await backbone._drive(_case("snapshot"), _spec(overrides={"spec.key": 2}))

    assert seams["sandbox_database"] == [(_SOURCE, "the_blank")]
    assert seams["sandbox_as_of"] == [] and seams["restored_room"] == []
    [seeded] = seams["sandbox_from_snapshot"]
    assert seeded == {
        "source_url": _SOURCE, "before_ms": _SHOT["before_ms"], "leave_room_for_arrival": True,
    }
    [spec] = seams["obtain"]
    assert spec.database_url == f"{_SOURCE}_sb"
    assert list(spec.overrides) == ["port.key", "spec.key", "memory.workspace_id"]
    assert not set(spec.overrides) & set(FULL_RESTORE_OVERRIDES)


async def test_a_full_restore_corpus_seeds_every_store_as_of_the_instant(seams) -> None:
    """`full_restore` restores AT the case's own instant, composes the subject
    against that database with the presence reader on, and reads the room back
    through `restored_room` instead of seeding it."""
    await backbone._drive(_case("restore"), _spec(full_restore=True))

    assert seams["sandbox_as_of"] == [(_SOURCE, _NOW, "the_blank")]
    assert seams["sandbox_database"] == [] and seams["sandbox_from_snapshot"] == []
    assert seams["restored_room"] == [{"source_url": _SOURCE, "before_ms": _SHOT["before_ms"]}]
    [spec] = seams["obtain"]
    assert spec.database_url == f"{_SOURCE}_asof"
    assert spec.now == _NOW
    assert spec.overrides["presence.enabled"] is True
    assert spec.overrides["memory.workspace_id"].startswith(f"nora-eval-{backbone.RUN_ID}-restore-")


async def test_a_spec_override_still_wins_over_the_restore_override(seams) -> None:
    await backbone._drive(
        _case("restore-off"), _spec(full_restore=True, overrides={"presence.enabled": False}),
    )
    [spec] = seams["obtain"]
    assert spec.overrides["presence.enabled"] is False


def test_the_restore_override_is_declared_and_the_generated_config_keeps_it_off() -> None:
    """The reader is turned on per subject, never in the generated config: the
    file every corpus composes from still forces presence off."""
    assert FULL_RESTORE_OVERRIDES == {"presence.enabled": True}
    assert set(FULL_RESTORE_OVERRIDES) <= set(ALLOWED_TO_DIFFER)
    assert adopted("[presence]\nenabled = true\n")["presence.enabled"] is False


def test_the_registry_names_the_recall_corpus_and_experiment_for_threads_it(monkeypatch) -> None:
    from evals import scenarios

    assert frozenset({"conversational_recall"}) == scenarios.FULL_RESTORE_CORPORA
    assert set(scenarios.CORPUS_ENTRY) >= scenarios.FULL_RESTORE_CORPORA
    assert "conversational_recall" in scenarios.JUDGED_CORPORA
    captured: list[Spec] = []
    monkeypatch.setattr(scenarios, "nora_task", lambda spec: captured.append(spec))
    scenarios.experiment_for("conversational_recall", source_url=_SOURCE)
    scenarios.experiment_for("presence", source_url=_SOURCE)
    assert [s.full_restore for s in captured] == [True, False]


def test_the_criterion_is_registered_and_selectable() -> None:
    from evals.rebuilt.grading import criteria_for
    from evals.rebuilt.rubric import OPTIONAL_CRITERIA, RECALL_GROUNDED

    assert OPTIONAL_CRITERIA[CRITERION] is RECALL_GROUNDED
    assert criteria_for((CRITERION,)) == (RECALL_GROUNDED,)
    # Both failures it exists to separate are in its words.
    text = RECALL_GROUNDED.description
    assert "could not check" in text and "scores HIGH" in text and "scores LOW" in text


def test_the_judge_source_carries_tool_results_for_a_recall_case() -> None:
    results = (("media_fetch", '{"status": "unavailable"}'), ("send_reply", "sent"))
    with_results = backbone._judge_source("<m>hi</m>", results, (CRITERION,))
    assert with_results is not None and "media_fetch: " in with_results
    assert "send_reply" not in with_results
    assert backbone._judge_source("<m>hi</m>", results, ("presence",)) == "<m>hi</m>"


_SHA = "a" * 64
_NOTE = "0a1b2c3d"


@pytest.mark.parametrize(("metadata", "fragment"), [
    ({"criteria": [CRITERION]}, "needs `metadata.evidence`"),
    ({"evidence": {"messages": [1]}}, "read only by"),
    ({"criteria": [CRITERION], "evidence": {"rooms": [1]}}, "not a key"),
    ({"criteria": [CRITERION], "evidence": {"media": ["abc123"]}}, "evidence.media"),
    ({"criteria": [CRITERION], "evidence": {"messages": ["9397"]}}, "evidence.messages"),
    ({"criteria": [CRITERION], "evidence": {"notes": ["not-hex!"]}}, "evidence.notes"),
    ({"criteria": [CRITERION], "evidence": {"notes": ["0a1b2c3d0"]}}, "evidence.notes"),
    ({"criteria": [CRITERION], "evidence": {"rollup_window_days": 0}}, "rollup_window_days"),
    ({"criteria": [CRITERION], "evidence": {"rollup_outings": []}}, "rollup_outings"),
])
def test_evidence_that_cannot_be_graded_is_refused_at_load(metadata, fragment) -> None:
    problems = evidence_problems(metadata)
    assert any(fragment in p for p in problems), problems


def test_typed_evidence_is_accepted_and_other_cases_are_untouched() -> None:
    assert evidence_problems({"criteria": [CRITERION], "evidence": {
        "messages": [9397], "media": [_SHA], "notes": [_NOTE], "events": [_NOTE],
        "rollup_outings": [33], "rollup_window_days": 7,
    }}) == []
    assert evidence_problems({"criteria": ["presence"]}) == []


def test_a_corpus_case_with_bad_evidence_does_not_load(monkeypatch, tmp_path) -> None:
    from evals import scenarios

    (tmp_path / "presence.yaml").write_text(
        "cases:\n- name: x\n  entry: tag\n  expect: reply\n"
        "  metadata:\n    criteria: [recall_grounded]\n", encoding="utf-8",
    )
    monkeypatch.setattr(scenarios, "_CORPORA", tmp_path)
    with pytest.raises(ValueError, match=r"needs `metadata\.evidence`"):
        scenarios.load_all("presence")


def test_the_facts_name_people_and_times_and_mark_the_current_revision() -> None:
    at = datetime(2026, 9, 30, 13, 0, tzinfo=UTC)
    at_ms = int(at.timestamp() * 1000)
    held = Evidence(
        at=at,
        messages=(HeldMessage(
            id=9397, ts=at_ms - 3 * 60_000, is_nora=False, person_id="miles_novak",
            sender_name="self-set", body="this one?", media=((_SHA, "image/jpeg"),),
        ),),
        media=(
            HeldMedia(sha256=_SHA, media_type="image/jpeg", title="a bike",
                      description="a red bicycle", content="", withheld=False),
            HeldMedia(sha256="b" * 64, media_type="image/png", title="", description="",
                      content="", withheld=True),
        ),
        notes=(HeldNote(
            id=_NOTE, title="Friday ride", anchor_date=20261002, retired=False,
            revisions=(
                HeldRevision(written_at=at_ms - 86_400_000, body="seven", current=False),
                HeldRevision(written_at=at_ms - 600_000, body="eight", current=True),
            ),
        ),),
        outings=(HeldOuting(id=33, valid_from_ms=at_ms - 2 * 86_400_000,
                            valid_to_ms=at_ms - 2 * 86_400_000 + 3_600_000,
                            persons=("miles_novak", "stranger"), place_label=None),),
        window_days=7, window_outings=(),
    )
    text = render(held, names={"miles_novak": "Miles"}, display_name="Nora",
                  tz=ZoneInfo("Asia/Hong_Kong"))
    assert "2026-09-30 21:00 (Wed)" in text, "the instant in the deployment's own timezone"
    assert "message 9397, 2026-09-30 20:57 (Wed), 3 min before she was asked, from Miles:" in text
    assert "[image aaaaaa]" in text and "a red bicycle" in text
    assert "withheld" in text and "b" * 64 not in text
    assert "(superseded) written" in text and "(current) written" in text
    assert text.index("seven") < text.index("eight")
    assert "dated 2026-10-02" in text
    assert "Miles, someone outside this group's roster, at a place with no label" in text
    assert "- none: no outing was recorded in that window." in text
    assert "miles_novak" not in text and "self-set" not in text

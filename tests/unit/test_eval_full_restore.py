"""The full-restore mode, the `recall_grounded` criterion and its facts, with
no database: which seams `_drive()` reaches in each mode, that every other
corpus keeps the snapshot path, the load-time evidence check, and the facts'
wording (`evals.recall.render` is pure).

The database half (the restore itself, the identity row map, the as-of
evidence read) is `tests/integration/test_full_restore_recall.py`.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from functools import partial
from types import SimpleNamespace
from typing import Any
from uuid import UUID
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
    NO_GEOCODER_ENV,
    SKIP_PREFIX,
    adopted,
)
from nora.subject import DrawWorkspace, Sandboxed, SubjectSpec
from nora.subject.evidence import (
    Evidence,
    HeldEvent,
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
    """`full_restore` defaults off, and off is the snapshot path: the empty sandbox,
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
    assert list(spec.overrides) == ["port.key", "spec.key", "honcho.workspace_id"]
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
    assert spec.overrides["honcho.workspace_id"].startswith(f"nora-eval-{backbone.RUN_ID}-restore-")


async def test_a_spec_override_still_wins_over_the_restore_override(seams) -> None:
    await backbone._drive(
        _case("restore-off"), _spec(full_restore=True, overrides={"presence.enabled": False}),
    )
    [spec] = seams["obtain"]
    assert spec.overrides["presence.enabled"] is False


def test_the_restore_override_is_declared_and_the_generated_config_keeps_it_off() -> None:
    """The reader is turned on per subject, never in the generated config: the
    file every corpus composes from still forces presence off."""
    assert FULL_RESTORE_OVERRIDES["presence.enabled"] is True
    assert "presence.enabled" in ALLOWED_TO_DIFFER
    assert all(k.startswith(SKIP_PREFIX) or k in ALLOWED_TO_DIFFER for k in FULL_RESTORE_OVERRIDES)
    assert adopted("[presence]\nenabled = true\n")["presence.enabled"] is False


def _built(tmp_path: Any, overrides: dict[str, object] | None) -> Any:
    from nora.subject.obtain import build_config

    return build_config(SubjectSpec(
        database_url="postgresql://unused/x", workdir=tmp_path, overrides=overrides,
    ))


#: Every geocoder credential, set as a deploy host's environment would hold it.
_KEYED = {"GOOGLE_MAPS_API_KEY": "a-real-looking-key", "HKGOV_MAPS_TOKEN": "a-token"}


def test_a_full_restore_subject_builds_no_geocoder_even_with_the_keys_set(
    tmp_path, monkeypatch,
) -> None:
    """The reader hands `reverse_geocode` coordinates, and the geocoder is built
    from the environment whatever the flag says, so the full-restore subject
    points both credentials at a name nothing sets. The control: the same
    environment composes a billed client for a snapshot subject, so the test
    can see one."""
    from nora.app.composition import reverse_geocoder

    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    default = _built(tmp_path / "default", None)
    assert reverse_geocoder(default, _KEYED, cache_store=None) is not None
    restored = _built(tmp_path / "restore", dict(FULL_RESTORE_OVERRIDES))
    assert restored.presence.enabled is True
    assert reverse_geocoder(restored, _KEYED, cache_store=None) is None
    hkgov = _built(tmp_path / "hkgov", {
        **FULL_RESTORE_OVERRIDES, "presence.geocode_provider": "hkgov",
        "presence.hkgov_geocode_url": "https://example.invalid/reverseGeocode",
    })
    assert reverse_geocoder(hkgov, _KEYED, cache_store=None) is None


@pytest.mark.parametrize("enabled", [False, True])
def test_both_composed_geocoders_screen_the_areas_they_keep(tmp_path, monkeypatch, enabled) -> None:
    """Both backends get the real area screen, bound to the reversible flag.
    Clean areas survive in both modes; credentials are always withheld."""
    from nora.app.composition import reverse_geocoder
    from nora.app.gatherings import screened_area
    from nora.domain.ports import PlaceArea

    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    overrides = {"content_checks.enabled": True} if enabled else {}
    google = reverse_geocoder(_built(tmp_path / "google", overrides), _KEYED, cache_store=None)
    hkgov = reverse_geocoder(_built(tmp_path / "hkgov", {
        **overrides,
        "presence.geocode_provider": "hkgov",
        "presence.hkgov_geocode_url": "https://example.invalid/reverseGeocode",
    }), _KEYED, cache_store=None)
    for geo in (google, hkgov):
        assert geo is not None
        screen = geo._area_screen  # type: ignore[attr-defined]
        assert isinstance(screen, partial) and screen.func is screened_area
        assert screen.keywords == {"content_checks": enabled}
        clean = PlaceArea(district="Example District", city="Example City")
        assert screen(clean) == clean
        assert screen(None) is None
        for name in (
            f"+1 {202} {555} {101:04d}", str(UUID(int=7)),
            ", ".join(f"{n + 0.125:.3f}" for n in (1, 2)),
        ):
            area = PlaceArea(district=name, city="Example City")
            expected = PlaceArea(district=None, city="Example City") if enabled else area
            assert screen(area) == expected
        assert screen(PlaceArea(district="sk-" + "synthetic" * 4)) is None


async def test_a_full_restore_refuses_while_the_no_geocoder_name_is_set(seams, monkeypatch) -> None:
    monkeypatch.setenv(NO_GEOCODER_ENV, "someone-set-it")
    with pytest.raises(ValueError, match=NO_GEOCODER_ENV):
        await backbone._drive_draw(_case("set"), _spec(full_restore=True), memory_workspace="w")
    assert seams["obtain"] == []


def test_the_corpus_is_judge_only_and_a_run_without_a_judge_refuses(monkeypatch, capsys) -> None:
    """`expect: reply` is met by ANY reply, so without the judge every recall
    case would pass on having spoken."""
    from evals import __main__ as cli
    from evals.scenarios import JUDGE_REQUIRED

    assert "conversational_recall" in JUDGE_REQUIRED
    monkeypatch.setattr("sys.argv", ["evals", "conversational_recall"])
    assert cli.main() == 2
    assert "refusing to run: ['conversational_recall']" in capsys.readouterr().err


class _Judge:
    def __init__(self) -> None:
        self.graded: list[dict[str, Any]] = []

    async def grade(self, **kwargs: Any) -> Any:
        self.graded.append(kwargs)
        return SimpleNamespace(verdict="PASS", error=None, why="ok")


@pytest.fixture
def judged(seams, monkeypatch) -> dict[str, list[Any]]:
    """The seams, with a turn that SPOKE and a captured window, so the drive
    reaches its judge branch; the facts builders are recorded."""
    calls: dict[str, list[Any]] = {"recall_facts": [], "presence_facts": []}

    async def drive(_subject: object, _case: object, *, now_ms: int) -> Outcome:
        _render_her_turn()
        return Outcome(admitted=True, said=("she said",))

    async def recall_facts(_subject: object, md: Any, *, now_ms: int) -> str:
        calls["recall_facts"].append(md)
        return "RECALL FACTS"

    def presence_facts(_spec: Any, md: Any, **_kw: Any) -> str | None:
        calls["presence_facts"].append(md)
        return "PRESENCE FACTS" if "presence" in (md.get("criteria") or ()) else None

    monkeypatch.setitem(
        ENTRY_POINTS, "tag", EntryPoint(drive=drive, delivers_input=True, config={}),
    )
    monkeypatch.setattr(backbone, "_shown_transcript", lambda *_a, **_k: "<m>the window</m>")
    monkeypatch.setattr(backbone, "recall_facts", recall_facts)
    monkeypatch.setattr(backbone, "_presence_facts", presence_facts)
    return calls


#: A block her turn was shown that names a fact the evidence does not: a trip a
#: note held open at the instant.
_NOTE_BLOCK = (
    '<notes description="what the group has planned">\n'
    "[7] (2d ago) the trip on the 17th is on\n"
    "</notes>"
)
_WINDOW = (
    '<group_transcript window="1" description="the room">\n'
    '<m n="1" age="just now" from="Miles">where have we been lately?</m>\n'
    "</group_transcript>"
)


def _render_her_turn(
    blocks: str = _NOTE_BLOCK, rendered: str = "notes,group_transcript",
) -> None:
    """What `build_prefill` leaves on the spans: its `agent.prefill` span
    naming the blocks it served, and the turn's first `chat` call carrying the
    volatile message (clock line, the blocks, the window). A second message on
    the same call carries a block-shaped string with NO window, the way the
    system prompt or the tag message would: it must never reach the judge."""
    from nora.obs.tracing import annotate, span

    with span("agent.prefill"):
        annotate(**{"nora.prefill.blocks_rendered": rendered})
    with span("chat", **{"gen_ai.operation.name": "chat"}):
        annotate(**{
            "gen_ai.system_instructions": (
                '<notes description="x">\nTHE DOCTRINE, NOT A BLOCK\n</notes>'
            ),
            "gen_ai.input.messages": (
                "Right now it is Saturday 11 October 2026, 20:00 HKT.\n"
                f"{blocks}\n\n{_WINDOW}"
            ),
        })


def _judged_case(name: str, criteria: list[str], **md: Any) -> Case:
    return Case(name=name, input="x", metadata={
        "snapshot": dict(_SHOT), "criteria": criteria, **md,
    })


async def test_the_judge_is_handed_the_recall_evidence(judged) -> None:
    """Pins `_drive_draw`'s wiring: delete the `recall_facts` call and the
    judge grades a recall case on no facts."""
    judge = _Judge()
    case = _judged_case("recall", [CRITERION], evidence={"messages": [1]})
    await backbone._drive(case, _spec(full_restore=True, judge=judge))
    [graded] = judge.graded
    assert graded["facts"] == "RECALL FACTS"
    assert judged["recall_facts"] == [case.metadata]


async def test_a_presence_case_keeps_its_own_facts_and_never_reads_recall_evidence(judged) -> None:
    """The other half of the claim that the judge branch is unchanged for
    every other corpus: `recall_facts` runs only when no other builder
    applied, so a presence case is graded on the presence facts."""
    judge = _Judge()
    await backbone._drive(_judged_case("presence", ["presence"]), _spec(judge=judge))
    [graded] = judge.graded
    assert graded["facts"] == "PRESENCE FACTS"
    assert judged["recall_facts"] == []


@pytest.mark.parametrize("other", ["presence", "lesson_applied"])
def test_recall_combined_with_another_facts_criterion_is_refused(other) -> None:
    problems = evidence_problems({
        "criteria": [CRITERION, other], "evidence": {"messages": [1]},
    })
    assert any("cannot be combined" in p and other in p for p in problems), problems


def test_the_registry_names_the_recall_corpus_and_experiment_for_threads_it(monkeypatch) -> None:
    from evals import scenarios

    assert frozenset({
        "conversational_recall", "cognition_session", "standing_unit",
        "tool_selection", "tool_selection_cognition",
    }) == scenarios.FULL_RESTORE_CORPORA
    assert set(scenarios.CORPUS_ENTRY) >= scenarios.FULL_RESTORE_CORPORA
    assert "conversational_recall" in scenarios.JUDGED_CORPORA
    captured: list[Spec] = []
    monkeypatch.setattr(scenarios, "nora_task", lambda spec: captured.append(spec))
    scenarios.experiment_for("conversational_recall", source_url=_SOURCE)
    scenarios.experiment_for("standing_unit", source_url=_SOURCE)
    scenarios.experiment_for("presence", source_url=_SOURCE)
    assert [s.full_restore for s in captured] == [True, True, False]


def test_the_criterion_is_registered_and_selectable() -> None:
    from evals.rebuilt.grading import criteria_for
    from evals.rebuilt.rubric import OPTIONAL_CRITERIA, RECALL_GROUNDED

    assert OPTIONAL_CRITERIA[CRITERION] is RECALL_GROUNDED
    assert criteria_for((CRITERION,)) == (RECALL_GROUNDED,)
    # Both failures it exists to separate are in its words.
    text = RECALL_GROUNDED.description
    assert "could not check" in text and "scores HIGH" in text and "scores LOW" in text


@pytest.mark.parametrize("criterion", [CRITERION, "social_suitability"])
def test_the_judge_source_carries_tool_results_for_a_recall_case(criterion) -> None:
    results = (("media_fetch", '{"status": "unavailable"}'), ("send_reply", "sent"))
    with_results = backbone._judge_source("<m>hi</m>", results, (criterion,))
    assert with_results is not None and "media_fetch: " in with_results
    assert "send_reply" not in with_results
    assert backbone._judge_source("<m>hi</m>", results, ("presence",)) == "<m>hi</m>"


@pytest.mark.parametrize("criterion", [CRITERION, "social_suitability"])
async def test_a_recall_judge_is_shown_every_block_her_turn_was_shown(judged, criterion) -> None:
    """A note open in the `<notes>` block she was shown is evidence: a judge
    sourced from only the window and her tool results calls «the trip on the 17th
    is still there» fabricated (`j4_where_have_we_been_together_lately`).
    Reverting `_judge_source`'s `recall_grounded` branch (or `_drive`'s
    `shown_blocks` argument) drops the note from the source and this fails."""
    judge = _Judge()
    await backbone._drive(
        _judged_case("recall", [criterion], evidence={"messages": [1]}),
        _spec(full_restore=True, judge=judge),
    )
    [graded] = judge.graded
    source = graded["source"]
    assert _NOTE_BLOCK in source, "a block she was shown never reached the recall judge"
    assert source.startswith("<m>the window</m>")
    assert source.count("<group_transcript") == 0, "the window is the source's head, once"
    assert "THE DOCTRINE, NOT A BLOCK" not in source, "only the message carrying her window"


_RESULTS = (("media_fetch", '{"status": "unavailable"}'), ("send_reply", "sent"))
_BLOCKS = (("notes", _NOTE_BLOCK), ("group_transcript", _WINDOW))


@pytest.mark.parametrize("criteria", [
    (), ("presence",), ("tool_status_reported",), ("lesson_applied",),
    ("personality", "source_scope", "being_contradicted", "appearance"),
])
def test_the_judge_source_is_unchanged_for_every_other_criterion(criteria) -> None:
    """Byte for byte the window-and-tools source: only `recall_grounded` reads
    `blocks`."""
    reporting = {"tool_status_reported", "lesson_applied"} & set(criteria)
    expected = "<m>hi</m>" + (
        '\n\nTool results this turn:\n- media_fetch: {"status": "unavailable"}'
        if reporting else ""
    )
    assert backbone._judge_source("<m>hi</m>", _RESULTS, criteria, _BLOCKS) == expected
    assert backbone._judge_source("<m>hi</m>", _RESULTS, criteria) == expected


async def test_a_presence_case_is_judged_on_exactly_the_window(judged) -> None:
    """The drive-level half: the same spans carry a `<notes>` block, and a
    presence case's source is still the window alone."""
    judge = _Judge()
    await backbone._drive(_judged_case("presence", ["presence"]), _spec(judge=judge))
    [graded] = judge.graded
    assert graded["source"] == "<m>the window</m>"


@pytest.mark.parametrize(("blocks", "rendered", "fragment"), [
    (None, "", "no context blocks were captured"),
    (_NOTE_BLOCK, "notes,events,group_transcript", "['events']"),
])
@pytest.mark.parametrize("criterion", [CRITERION, "social_suitability"])
async def test_a_recall_draw_whose_blocks_are_not_read_back_is_an_error(
    judged, monkeypatch, blocks, rendered, fragment, criterion,
) -> None:
    """Fail closed: no blocks captured off her call, or fewer than the prefill
    says it rendered, is an ERROR draw, and the judge is never called."""
    from strands_evals.types.evaluation import EvaluationData

    from evals.backbone import NORA_ERROR, NORA_JUDGED, state_of

    async def drive(_subject: object, _case: object, *, now_ms: int) -> Outcome:
        if blocks is not None:
            _render_her_turn(blocks, rendered)
        return Outcome(admitted=True, said=("she said",))

    monkeypatch.setitem(
        ENTRY_POINTS, "tag", EntryPoint(drive=drive, delivers_input=True, config={}),
    )
    judge = _Judge()
    result = await backbone._drive(
        _judged_case("recall", [criterion], evidence={"messages": [1]}),
        _spec(full_restore=True, judge=judge),
    )
    assert judge.graded == [], "a recall draw graded blind"
    data = EvaluationData(input="x", actual_environment_state=result["environment_state"])
    assert state_of(data, NORA_JUDGED) == ""
    error = state_of(data, NORA_ERROR)
    assert error and fragment in error and "recall_grounded" in error


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


def test_an_event_is_rendered_in_the_deployments_timezone_with_its_distance() -> None:
    at = datetime(2026, 9, 30, 13, 0, tzinfo=UTC)
    at_ms = int(at.timestamp() * 1000)
    held = Evidence(at=at, events=(HeldEvent(
        id=_NOTE, title="dinner", when_ms=at_ms + 2 * 86_400_000,
        end_ms=at_ms + 2 * 86_400_000 + 7_200_000, venue=None, status="confirmed",
        people=("miles_novak",),
    ),))
    text = render(held, names={"miles_novak": "Miles"}, display_name="Nora",
                  tz=ZoneInfo("Asia/Hong_Kong"))
    assert (
        "when 2026-10-02 21:00 (Fri), 2 days 0 h after she was asked "
        "to 2026-10-02 23:00 (Fri), 2 days 2 h after she was asked"
    ) in text
    assert "with Miles" in text and "no venue set" in text


def test_a_self_closed_block_never_swallows_the_next_one() -> None:
    """A `when_empty="self_close"` block (`<memos .../>`) must not match as an
    opening tag and swallow through the next same-name block's close."""
    from evals.backbone import _CONTEXT_BLOCK

    text = '<memos kind="x"/>\n\n<notes kind="y">\nbody &lt;b&gt;x&lt;/b&gt;\n</notes>'
    names = [m.group(1) for m in _CONTEXT_BLOCK.finditer(text)]
    assert names == ["notes"]
    doubled = '<memos a="1"/>\n\n<memos a="2">\nreal\n</memos>'
    [only] = list(_CONTEXT_BLOCK.finditer(doubled))
    assert only.group(0).startswith('<memos a="2">')


@pytest.mark.parametrize("corpus", ["tool_selection", "tool_selection_cognition"])
async def test_tool_routing_uses_the_full_record_restore(corpus, seams, monkeypatch):
    from evals import scenarios
    captured=[]
    monkeypatch.setattr(scenarios, "nora_task", lambda spec: captured.append(spec))
    scenarios.experiment_for(corpus, source_url=_SOURCE)
    [selected] = captured
    assert selected.full_restore, "routing must seed the actual stores its tools read"
    await backbone._drive(_case("tool-record"), _spec(full_restore=selected.full_restore))
    assert seams["sandbox_as_of"] == [(_SOURCE, _NOW, "the_blank")]
    assert seams["sandbox_from_snapshot"] == []
    assert seams["restored_room"] == [{"source_url":_SOURCE,"before_ms":_SHOT["before_ms"]}]


@pytest.mark.parametrize("latest", [False, True])
@pytest.mark.parametrize("status", ["ok", "held_for_review", "unavailable", "malformed"])
async def test_reaction_receipts_reach_suitability_without_phantom_text(
    judged, monkeypatch, latest, status,
) -> None:
    """Real SDK tool spans: confirmed reaction is judged; held/unknown is not speech."""
    import json

    from strands.telemetry.tracer import get_tracer
    from strands_evals.types.evaluation import EvaluationData

    from evals.backbone import NORA_REACTIONS, NORA_SAID, AbsenceGrader, MeetsGrader, state_of

    async def drive(_subject, _case, *, now_ms):
        _render_her_turn()
        tracer = get_tracer()
        monkeypatch.setattr(tracer, "use_latest_genai_conventions", latest)
        tool = {"name": "reaction_send", "toolUseId": "reaction-1",
                "input": {"emoji": "😂", "reply_to": 1}}
        span = tracer.start_tool_call_span(tool)
        text = "bad-json" if status == "malformed" else json.dumps({"status": status})
        tracer.end_tool_call_span(span, {"toolUseId": "reaction-1", "status": "success",
                                          "content": [{"text": text}]})
        return Outcome(admitted=False)

    monkeypatch.setitem(ENTRY_POINTS, "tag", EntryPoint(drive=drive, delivers_input=True))
    judge = _Judge()
    result = await backbone._drive(
        _judged_case("reaction", ["social_suitability"]),
        _spec(full_restore=True, judge=judge),
    )
    data = EvaluationData(input="x", metadata={"expect": "reply"},
                          actual_environment_state=result["environment_state"])
    assert state_of(data, NORA_SAID) == 0
    assert bool(state_of(data, NORA_REACTIONS)) == (status == "ok")
    [graded] = MeetsGrader().evaluate(data)
    assert graded.label == ("PASS" if status == "ok" else "FAIL")
    [absence] = AbsenceGrader().evaluate(data)
    assert absence.label == ("PASS" if status == "ok" else "FAIL")
    silent_data = EvaluationData(
        input="x", metadata={"expect": "silence"},
        actual_environment_state=result["environment_state"],
    )
    [silence] = AbsenceGrader().evaluate(silent_data)
    assert silence.label == ("FAIL" if status == "ok" else "PASS")
    if status == "ok":
        [call] = judge.graded
        assert "Confirmed reaction_send" in call["said"]
        assert "😂" in call["said"] and '"reply_to": 1' in call["said"]
        assert result["output"] == call["said"]
    else:
        assert judge.graded == []
        assert result["output"] == ""


@pytest.mark.parametrize('state', ['unavailable', 'held'])
def test_circle_reaction_requires_actual_execution_and_forbids_hold_bypass(state):
    import json

    from nora.subject.circle import CircleOverlay
    overlay = CircleOverlay(reaction=state)
    result = 'held_for_review' if state == 'held' else 'unavailable'
    receipt = (('reaction_send', json.dumps([{'text': json.dumps({'status': result})}])),)
    assert backbone._circle_execution_problem(overlay, receipt, (), ('reaction_send',)) is None
    assert backbone._circle_execution_problem(overlay, (), (), ())
    assert backbone._circle_execution_problem(overlay,
        (('reaction_send', '[{"text":"{\"status\":\"ok\"}"}]'),), (), ('reaction_send',))
    if state == 'held':
        assert backbone._circle_execution_problem(overlay, receipt, (),
            ('reaction_send', 'send_reply'))


async def test_circle_reaction_outage_is_the_real_signal_result_and_validates_requests():
    import aiohttp

    from nora.domain.models import ConversationTarget
    from nora.platform.signal.react import SignalReactor
    from nora.subject.circle import CircleOverlay, reaction_wire
    async with reaction_wire(CircleOverlay(reaction='unavailable')) as url:
        result = await SignalReactor(url).react(ConversationTarget('signal', 'generated', 'group'),
            '👍', to_author='generated-author', to_timestamp=42)
        assert result.supported and not result.ok
        async with (
            aiohttp.ClientSession() as client,
            client.post(url + '/api/v1/rpc', json={'method': 'send'}) as response,
        ):
            assert response.status == 400


@pytest.mark.parametrize('unavailable', [False, True])
@pytest.mark.parametrize('query', [
    'spicy', '我對 generated spicy noodle dish 嘅 preference 係點？',
])
async def test_circle_memory_uses_admitted_correction_and_scoped_read(unavailable, query):
    from nora.agent.memory_search import MemorySearch
    from nora.domain.lineage import LiveRoom, RenderRoom, RoomFacts
    from nora.domain.memory_graph import Walk
    from nora.obs.metrics import Metrics
    from nora.subject.circle import CircleMemory
    memory = CircleMemory(unavailable=unavailable)
    subject = SimpleNamespace(unit='generated-unit', room=('signal', 'generated-room'))
    await memory.seed(subject, person='generated-person', now_ms=1_790_000_000_000)
    assert memory.graph.relations['circle-preference-0'].status == 'superseded'
    assert memory.graph.relations['circle-preference-1'].status == 'current'
    room = RoomFacts('signal', frozenset({'generated-room'}), 'generated-unit',
                     frozenset({'generated-person'}))
    target = RenderRoom(room=room, rooms=(room,), circles=(),
                        live=LiveRoom(person_ids=frozenset({'generated-person'})))
    service = MemorySearch(memory.index, Metrics(), graph=memory.graph)
    result = await service.read(query=query, target=target, now_ms=1_790_000_000_000,
                                social_off=())
    assert result['status'] == ('unavailable' if unavailable else 'partial'), result
    assert memory.index.calls
    if unavailable:
        with pytest.raises(TimeoutError):
            await memory.index.index_state()
        down = await memory.graph.walk((), Walk('generated-unit', frozenset()))
        assert down.status == 'unavailable'
    if not unavailable:
        assert 'no longer eat' in result['preview'], result
        assert 'Generated speaker' in result['preview']
        from nora.agent.prefill import _recalled_block
        from nora.domain.blocks import assemble
        block = _recalled_block(SimpleNamespace(recalled=(result,), recalled_partial=True))
        rendered = assemble([block])
        assert '<recalled' in rendered and 'no longer eat' in rendered
        assert 'partial' in rendered

        refused = await memory.graph.walk((('person', 'generated-unit:person:generated-person'),),
            Walk('another-unit', frozenset({'generated-person'})))
        assert not refused.nodes
        hidden = await memory.graph.walk((('person', 'generated-unit:person:generated-person'),),
            Walk('generated-unit', frozenset()))
        assert not hidden.nodes


async def test_circle_gif_absence_reaches_existing_registry_boundary():
    from nora.agent.registry import ToolRegistry
    from nora.subject.circle import CircleOverlay, external_boundaries
    with external_boundaries(CircleOverlay(gif='unavailable')):
        registry = ToolRegistry(giphy=object())
        assert registry._giphy is None
    assert ToolRegistry(giphy='positive')._giphy == 'positive'
    from nora.obs.metrics import Metrics
    from nora.testing import FakeRecordStore
    specs = registry.specs(store=FakeRecordStore(), conversation_id='generated-room',
        conversation_type='group', persons=None, honcho=None,
        deliverer=SimpleNamespace(), metrics=Metrics(), room=None)
    tools = {spec.name: spec for spec in specs}
    result = await tools['search_gifs'].call(query='laughing noodles')
    assert result == {'status': 'unavailable', 'note': 'Reply in words instead.'}
    assert (await tools['send_gif'].call(candidate_id='invented'))['status'] == 'unavailable'



def test_circle_public_capture_requires_actual_public_chat_input():
    import json

    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer('generated')
    rendered = '<social_post>generated public question</social_post>'
    assert backbone._public_input(exporter, rendered) is None
    with tracer.start_as_current_span('model') as span:
        span.set_attribute('gen_ai.operation.name', 'chat')
        span.set_attribute('gen_ai.input.messages', json.dumps([
            {'role': 'user', 'content': rendered}]))
    assert backbone._public_input(exporter, rendered) == rendered
    assert backbone._public_input(exporter, '<social_post>other input</social_post>') is None


@pytest.mark.parametrize('state', ['running', 'checked_complete'])
async def test_circle_job_lifecycle_is_read_by_real_context_and_desk(monkeypatch, state):
    from nora.agent.subagent_desk import SubagentDesk
    from nora.doctrine.loader import Doctrine
    from nora.engagement.subagents_context import SubagentsContextReader
    from nora.language.subagents_render import render_subagents
    from nora.obs.metrics import Metrics
    from nora.subject import circle
    from nora.subject.recording_subagent import RecordingSubagentBackend
    from nora.testing import FakeAdapter, FakeComputeJobStore, FakeDocumentStore, FakeRecordStore
    store, docs, audit, adapter = (FakeComputeJobStore(), FakeDocumentStore(),
                                    FakeRecordStore(), FakeAdapter())
    class Engine:
        async def dispose(self): pass
    monkeypatch.setattr(circle, 'create_sql_engine', lambda _url: Engine())
    monkeypatch.setattr(circle, 'SqlComputeJobStore', lambda _engine: store)
    now = datetime.fromtimestamp(1_790_000_000, UTC)
    backend = RecordingSubagentBackend()
    metrics = Metrics()
    subject = SimpleNamespace(database_url='unused', room=('signal', 'generated-room'),
        deps=SimpleNamespace(subagent_backend=backend, documents=docs, audit=audit,
            registry=SimpleNamespace(adapters={'signal': adapter}), metrics=metrics,
            doctrine=Doctrine(sections=(), text='', doctrine_text='', sha256='generated',
                              runtime_context='', loaded_at=0)),
        cfg=SimpleNamespace(documents=SimpleNamespace(enabled=True),
                            doctrine=SimpleNamespace(canary='GENERATED-CANARY')))
    job_id = await circle.seed_job(subject, circle.CircleOverlay(job=state),
        person='generated-person', now_ms=1_790_000_000_000)
    reader = SubagentsContextReader(jobs=store, clock=lambda: now, metrics=metrics)
    reader.attach(backend)
    view = await reader.read(platform='signal', conversation_id='generated-room',
                              person_id='generated-person')
    assert view is not None and len(view.lines) == 1
    rendered = render_subagents(view).body
    assert 'Generated comparison' in rendered
    overlay = circle.CircleOverlay(job=state)
    job = await store.get(UUID(job_id))
    binding = {'job': job, 'room': subject.room}
    assert backbone._circle_execution_problem(
        overlay, (), (('subagents', rendered),), (), **binding,
    ) is None
    assert backbone._circle_execution_problem(overlay, (), (), ())
    assert backbone._circle_execution_problem(
        overlay, (), (('subagents', rendered),), ('subagent_create',), **binding,
    )
    assert view.lines[0].state == ('running' if state == 'running' else 'done')
    assert view.lines[0].delivered == (state == 'checked_complete')
    if state == 'checked_complete':
        assert '12 units' in rendered and '9 units' in rendered
        assert len(adapter.sent) == 1
        assert len(docs._rooms) == 1
        assert len(audit.rows) == 1
        # FirePath legitimately omits the pending-only subagents block. Capture the
        # real generated document as document_read returns it, with no new job/read.
        import dataclasses
        import json

        doc = await docs.get(document_id=UUID(job.result_document_ids[0]))
        capture = (('document_read', json.dumps([{'text': json.dumps({
            'status': 'ok', 'document_id': str(doc.summary.id), 'title': doc.summary.title,
            'spec': doc.spec,
        })}])),)
        assert backbone._circle_execution_problem(overlay, capture, (), (), **binding) is None
        from html import escape
        rendered_doc = (('documents', escape(
            str(doc.summary.id) + '\n' + doc.text)),)
        assert backbone._circle_execution_problem(
            overlay, (), rendered_doc, (), **binding) is None
        assert backbone._circle_execution_problem(overlay, (), (), (), **binding)
        assert backbone._circle_execution_problem(
            overlay, capture, (), ('subagent_create',), **binding)
        for wrong in (
            dataclasses.replace(job, result_document_ids=('wrong-document',)),
            dataclasses.replace(job, result_report='Wrong generated result'),
            dataclasses.replace(job, conversation_id='another-unit'),
            dataclasses.replace(job, state=circle.ComputeJobState.PENDING),
            dataclasses.replace(job, completed_at=None),
        ):
            assert backbone._circle_execution_problem(
                overlay, capture, (), (), job=wrong, room=subject.room)
        missing_result = (('document_read', json.dumps([{'text': json.dumps({
            'status': 'ok', 'document_id': str(doc.summary.id), 'title': doc.summary.title,
        })}])),)
        assert backbone._circle_execution_problem(overlay, missing_result, (), (), **binding)
    else:
        assert not adapter.sent and not audit.rows and not docs._rooms
    assert await reader.read(platform='signal', conversation_id='another-room',
                               person_id='generated-person') is None
    # The desk's bound read refuses a handle from another room.
    desk = SubagentDesk(jobs=store, clock=lambda: now)
    desk.attach(backend)
    result = await desk.read(platform='signal', conversation_id='generated-room',
                             job_id=job_id, person_id='generated-person')
    assert result is not None and result.state == view.lines[0].state
    assert await desk.read(platform='signal', conversation_id='another-room',
                            job_id=job_id, person_id='generated-person') is None


@pytest.mark.parametrize('different_route', [False, True])
async def test_circle_public_adapter_uses_real_occasion_and_narrow_grant(
    monkeypatch, tmp_path, different_route,
):
    from pathlib import Path
    from unittest.mock import AsyncMock

    from nora.agent import factory
    from nora.agent.registry import ToolRegistry
    from nora.app.summons import PUBLIC_GRANT
    from nora.doctrine.loader import DoctrineStore
    from nora.domain.summons import SummonsContext, ThreadRow
    from nora.obs.metrics import Metrics
    from nora.persons import load_persons
    from nora.subject import public_summons
    from nora.testing import FakeAgentRunner, ScriptedTurn
    identity = tmp_path / 'generated-identity.toml'
    identity.write_text('[persons.generated]\nname="Generated"\n')
    persons = load_persons(identity)
    metrics = Metrics()
    doctrine = DoctrineStore(Path(__file__).resolve().parents[2] / 'nora/doctrine',  # noqa: ASYNC240
        canary='GENERATED-CANARY', strict=True,
        tool_names=ToolRegistry().tool_names(), metrics=metrics)
    calls = []
    runner = FakeAgentRunner([ScriptedTurn(tool_calls=[
        ('send_reply', {'text': 'I cannot tell from this public post.'})])], on_call=calls.append)
    built = []
    def build(**kwargs):
        built.append(kwargs)
        return runner
    monkeypatch.setattr(public_summons, 'LiveAgentRunner', build)
    client = SimpleNamespace(close=AsyncMock())
    created = []
    def create_client(**kwargs):
        created.append(kwargs)
        return client
    monkeypatch.setattr(factory, 'create_client', create_client)
    def endpoint(purpose, model=''):
        return ('https://public.invalid' if different_route and purpose == 'summons'
                else 'https://tag.invalid', '', 'public-model' if purpose == 'summons' else 'tag')

    subject = SimpleNamespace(cfg=SimpleNamespace(endpoint=endpoint,
        agent=SimpleNamespace(name='generated'),
        doctrine=SimpleNamespace(canary='GENERATED-CANARY'),
        llm=SimpleNamespace(timeout_seconds=5), content_checks=SimpleNamespace(enabled=False)),
        deps=SimpleNamespace(persons=persons, metrics=metrics, doctrine_store=doctrine,
            engagement=SimpleNamespace(
                _limits={'turns':8, 'total_tokens':64000, 'output_tokens':8000},
                _runner=SimpleNamespace(_models=factory.ModelSource(client=None,
                    spec=factory.ModelSpec(model='unused', max_tokens=100, temperature=0)),
                    _retry=None))))
    now = datetime(2026, 10, 1, tzinfo=UTC)
    ctx = SummonsContext('generated', now, 'What is my preference?', 'generated', now,
        'generated', 'Public food post', None, (), 0,
        (ThreadRow('generated', 'What is my preference?', now, True),), 0)
    said, failure = await public_summons.turn(subject, ctx)
    assert failure is None and said == ('I cannot tell from this public post.',)
    assert runner.invocations == 1
    assert '<social_post' in calls[0]['tag_message']
    assert not calls[0]['pre_fill']
    assert {tool.tool_name for tool in calls[0]['tools']} <= PUBLIC_GRANT
    assert built[0]['skills_dir'] is None
    assert built[0]['models']._spec.tool_choice == 'auto'
    assert built[0]['models']._spec.model == 'public-model'
    assert bool(created) == different_route
    assert client.close.await_count == int(different_route)
    assert not {'memory_search', 'memory_read', 'subagent_read', 'send_gif'} & PUBLIC_GRANT


async def test_circle_declared_entry_is_used_by_the_actual_drive(seams, monkeypatch):
    calls = []
    async def drive(subject, case, *, now_ms):
        calls.append((case.entry, now_ms))
        return Outcome(admitted=None)
    monkeypatch.setitem(ENTRY_POINTS, 'public_summons', EntryPoint(drive=drive))
    case = _case('generated-public-entry')
    case.metadata['entry'] = 'public_summons'
    await backbone._drive(case, _spec())
    assert calls == [('public_summons', _SHOT['before_ms'])]
    assert seams['sandbox_from_snapshot'][0]['leave_room_for_arrival'] is False


async def test_circle_judge_receives_requirement_separately_from_actual_evidence(
    judged, monkeypatch,
):
    import json
    case = _judged_case('generated-gif-unavailable', ['social_suitability'])
    case.metadata['circle_overlay'] = {'gif': 'unavailable'}
    case.expected_output = 'No invented sent GIF; words remain possible.'
    raw = json.dumps([{'text': json.dumps({'status': 'unavailable',
                                          'note': 'Reply in words instead.'})}])
    monkeypatch.setattr(backbone, '_tool_results', lambda _exporter: (('search_gifs', raw),))
    monkeypatch.setattr(backbone, '_tool_inputs', lambda _exporter: (('search_gifs', '{}'),))
    monkeypatch.setattr(backbone, '_tools_called', lambda _exporter: ('search_gifs',))
    judge = _Judge()
    await backbone._drive(case, _spec(judge=judge))
    [graded] = judge.graded
    criterion = next(c for c in graded['extra'] if c.name == 'social_suitability')
    assert 'Case acceptance requirement (not evidence)' in criterion.description
    assert case.expected_output in criterion.description
    assert case.expected_output not in graded['source']
    assert 'Reply in words instead.' in graded['source'] and 'unavailable' in graded['source']


def test_circle_memory_capture_measures_status_not_words():
    from nora.subject.circle import CircleOverlay
    unavailable = CircleOverlay(memory='unavailable')
    partial = CircleOverlay(memory='partial')
    wrong = (('recalled', '<recalled>{"text":"unavailable partial"}</recalled>'),)
    assert backbone._circle_execution_problem(unavailable, (), wrong, ())
    assert backbone._circle_execution_problem(partial, (), wrong, ())
    assert backbone._circle_execution_problem(unavailable, (), (), ())
    real = (('recalled', '<recalled>{"status":"unavailable"}</recalled>'),)
    assert backbone._circle_execution_problem(unavailable, (), real, ()) is None
    real_partial = (('recalled', '<recalled partial="yes">{"text":"generated"}</recalled>'),)
    assert backbone._circle_execution_problem(partial, (), real_partial, ()) is None


@pytest.mark.parametrize("reason", [None, "llm_error", "invocation_timeout"])
async def test_public_silence_requires_a_healthy_invocation(monkeypatch, reason):
    from evals.rebuilt.domain import Case as NoraCase
    from evals.rebuilt.entry import public_summons
    from nora.obs.metrics import Metrics
    from nora.subject import public_summons as public_adapter

    metrics = Metrics()
    calls = []

    async def turn(subject, context):
        calls.append(context.question)
        if reason:
            subject.deps.metrics.inc(
                "nora_silent_failures_total", reason=reason,
                platform="instagram", audience="summons",
            )
        # The production resolved-turn API can return this on first-call errors.
        return (), None

    monkeypatch.setattr(public_adapter, "turn", turn)
    subject = SimpleNamespace(deps=SimpleNamespace(metrics=metrics))
    case = NoraCase(name="generated-public-silence", entry="public_summons",
                    expect="silence", input="Generated private-preference question")
    outcome = await public_summons(subject, case, now_ms=1_790_000_000_000)
    assert calls == [case.input]
    assert "<social_post" in outcome.shown
    assert bool(outcome.error) == bool(reason)
    assert outcome.meets("silence") is (None if reason else True)


def test_judge_export_retains_raw_votes_and_errors_privately(monkeypatch, tmp_path):
    import json

    from evals.rebuilt.grading import Judgement

    votes = ('[{"criterion":"safety","score":0.6,"confidence":0.4}]', 'malformed vote')
    reading = Judgement('safety', 'REVIEW', votes, 'TimeoutError')
    monkeypatch.delenv(backbone.EXCHANGES_ENV, raising=False)
    backbone._dump_judgement(reading, 'synthetic')
    assert not list(tmp_path.iterdir())
    folder = tmp_path / 'private-exchanges'
    monkeypatch.setenv(backbone.EXCHANGES_ENV, str(folder))
    backbone._dump_judgement(reading, 'synthetic')
    path, = folder.glob('synthetic-*-judge.json')
    assert json.loads(path.read_text()) == {
        'criterion': 'safety', 'verdict': 'REVIEW', 'samples': list(votes),
        'error': 'TimeoutError',
    }
    assert path.stat().st_mode & 0o777 == 0o600
    assert folder.stat().st_mode & 0o777 == 0o700

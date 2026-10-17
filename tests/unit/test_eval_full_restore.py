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


def test_both_composed_geocoders_screen_the_areas_they_keep(tmp_path, monkeypatch) -> None:
    """`reverse_geocoder` hands each backend the R-28 area screen venue areas
    pass (`screened_area`); without it a backend keeps no areas at all."""
    from nora.app.composition import reverse_geocoder
    from nora.app.gatherings import screened_area

    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    google = reverse_geocoder(_built(tmp_path / "google", None), _KEYED, cache_store=None)
    hkgov = reverse_geocoder(_built(tmp_path / "hkgov", {
        "presence.geocode_provider": "hkgov",
        "presence.hkgov_geocode_url": "https://example.invalid/reverseGeocode",
    }), _KEYED, cache_store=None)
    for geo in (google, hkgov):
        assert geo is not None and geo._area_screen is screened_area  # type: ignore[attr-defined]


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


async def test_a_recall_judge_is_shown_every_block_her_turn_was_shown(judged) -> None:
    """A note open in the `<notes>` block she was shown is evidence: a judge
    sourced from only the window and her tool results calls «the trip on the 17th
    is still there» fabricated (`j4_where_have_we_been_together_lately`).
    Reverting `_judge_source`'s `recall_grounded` branch (or `_drive`'s
    `shown_blocks` argument) drops the note from the source and this fails."""
    judge = _Judge()
    await backbone._drive(
        _judged_case("recall", [CRITERION], evidence={"messages": [1]}),
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
async def test_a_recall_draw_whose_blocks_are_not_read_back_is_an_error(
    judged, monkeypatch, blocks, rendered, fragment,
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
        _judged_case("recall", [CRITERION], evidence={"messages": [1]}),
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

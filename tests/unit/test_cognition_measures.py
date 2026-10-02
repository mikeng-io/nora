"""The Cognition session as a MEASURED thing (an audit of the live shadow data found
it could not yet produce evidence the step-4 cut-over could be judged on).

Every test drives the REAL runner through its REAL tool closures with a scripted
agent, the way `test_cognition_session_verbs_c.py` does (whose rig it reuses).
What a dry-run verb says must be what the live one would say, so each test names a
refusal the live path makes and shows the shadow makes it too; and every refusal,
every close, every self-wake and every token spent is COUNTED, so the shadow week
reads from metrics and not from a model's account of itself.

What each test catches if its fix is reverted is named in
`scripts/mutations/cognition-measures.json`.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from nora.domain.cognition_remind import PendingMirroredRemind
from nora.domain.cognition_session import Room
from nora.domain.gatherings import FieldMeta, GatheringStatus
from nora.domain.ports import NewCognitionRemindMirror, NoteHead
from nora.obs.metrics import Metrics
from nora.testing.cognition_dry_run_store import InMemoryCognitionDryRunStore
from tests.unit.test_cognition_session_events_d import _event
from tests.unit.test_cognition_session_verbs_c import (
    GOOD_BODY,
    NOTE,
    NOW,
    _reasons,
    _session,
    iso,
)

HEAD = "rev-2"
_A, _B = Room("signal", "room-a"), Room("signal", "room-b")


def _series(metrics: Metrics, name: str) -> list[dict[str, str]]:
    """The label sets of every series recorded under `name`."""
    return [dict(labels) for (series, labels) in metrics.snapshot() if series == name]


def _heads(**table: NoteHead | None) -> Any:
    async def head_of(unit: str, note_id: str) -> NoteHead | None:
        return table.get(note_id)
    return head_of


def _kw(**over: Any) -> dict[str, Any]:
    return over


def _no_digits(text: str) -> bool:
    return not re.search(r"\d", text)


# ---------------------------------------------------------------------------
# note_update: the live store's refusals, made without a write
# ---------------------------------------------------------------------------

async def _update(head: NoteHead | None, parent: str = HEAD, **kw: Any):
    answers: list[Any] = []

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        answers.append(await by["note_update"](
            note_id="n1", body=GOOD_BODY, parent_revision_id=parent,
        ))

    store, _ = await _session(behave, runner_kwargs=_kw(note_head=_heads(n1=head), **kw))
    return answers[0], store


async def test_a_note_update_against_the_head_it_read_would_commit():
    answer, store = await _update(NoteHead(head_revision_id=HEAD))
    assert answer == {"status": "ok"}
    assert [w.outcome for _, w, _ in store.would_writes] == ["would_commit"]


async def test_a_note_update_against_a_stale_head_is_refused_and_recorded_refused():
    # The tool text says «A stale one is refused»; the dry run recorded it
    # `would_commit` and overstated what the live path would write.
    answer, store = await _update(NoteHead(head_revision_id="rev-9"))
    assert answer["status"] == "refused" and answer["reason"] == "stale_base"
    assert _reasons(store) == ["stale_base"]
    assert [w.outcome for _, w, _ in store.would_writes] == ["refused"]


async def test_a_note_update_of_a_note_the_unit_does_not_hold_is_refused_not_found():
    answer, store = await _update(None)
    assert (answer["status"], answer["reason"]) == ("refused", "not_found")
    assert _reasons(store) == ["not_found"]


async def test_a_note_update_of_a_retired_note_is_refused_note_retired():
    answer, _ = await _update(NoteHead(head_revision_id=HEAD, retired=True))
    assert (answer["status"], answer["reason"]) == ("refused", "note_retired")


async def test_a_refused_note_update_does_not_back_a_written_close():
    closed: list[Any] = []

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        await by["note_update"](note_id="n1", body=GOOD_BODY, parent_revision_id="rev-9")
        closed.append(await by["close"](item_index=0, reason="written"))

    await _session(behave, runner_kwargs=_kw(note_head=_heads(n1=NoteHead(HEAD))))
    assert closed[0]["reason"] == "no_write_for_item"


async def test_a_head_that_cannot_be_read_is_unavailable_never_a_would_commit():
    async def broken(unit: str, note_id: str) -> NoteHead | None:
        raise RuntimeError("the note store is down")

    answers: list[Any] = []

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        answers.append(await by["note_update"](
            note_id="n1", body=GOOD_BODY, parent_revision_id=HEAD,
        ))

    store, _ = await _session(behave, runner_kwargs=_kw(note_head=broken))
    assert answers[0]["status"] == "unavailable" and answers[0]["reason"] == "note_head"
    assert store.would_writes == []


# ---------------------------------------------------------------------------
# the event verbs make the live store's existence and staleness checks too
# ---------------------------------------------------------------------------

async def test_event_revise_and_set_status_refuse_a_stale_base_and_an_unknown_event():
    event = _event(GatheringStatus.PROPOSED)
    answers: list[Any] = []

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        common = {"reasoning": "told", "confidence": "high", "cited_ids": ["101"]}
        answers.append(await by["event_revise"](
            event_id=str(event.id), venue_text="place two", base_revision_id="not-the-head",
            **common,
        ))
        answers.append(await by["event_revise"](
            event_id="no-such-event", venue_text="place two", **common,
        ))
        answers.append(await by["event_set_status"](
            event_id=str(event.id), status="confirmed", base_revision_id="not-the-head",
            **common,
        ))
        answers.append(await by["event_revise"](
            event_id=str(event.id), venue_text="place two",
            base_revision_id=str(event.head_revision_id), **common,
        ))

    store, _ = await _session(behave, events=(event,))
    assert [a.get("reason") for a in answers] == ["stale_base", "not_found", "stale_base", None]
    assert [w.outcome for _, w, _ in store.would_writes] == [
        "refused", "refused", "refused", "would_commit",
    ]


# ---------------------------------------------------------------------------
# remind: the code-side act guards of 30 §3, refused with a named reason
# ---------------------------------------------------------------------------

async def _remind(*, intent: str = "tell", rows: tuple[str, ...] = ("note:1",), **session: Any):
    answers: list[Any] = []

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        answers.append(await by["remind"](
            intent=intent, subject_rows=list(rows), at=iso(NOW + 3_600_000),
        ))

    store, _ = await _session(behave, **session)
    return answers[0], store


async def test_a_remind_to_a_room_that_is_not_proactive_is_refused_and_recorded_refused():
    answer, store = await _remind(runner_kwargs=_kw(room_proactive=lambda p, c: False))
    assert (answer["status"], answer["reason"]) == ("refused", "room_not_proactive")
    assert [m.outcome for _, m, _ in store.remind_mirrors] == ["refused"]


async def test_a_remind_to_a_proactive_room_is_recorded():
    seen: list[tuple[str, str]] = []

    def proactive(platform: str, conversation_id: str) -> bool:
        seen.append((platform, conversation_id))
        return True

    answer, store = await _remind(runner_kwargs=_kw(room_proactive=proactive))
    assert answer["status"] == "ok" and seen == [("signal", "room-a")]
    assert [m.outcome for _, m, _ in store.remind_mirrors] == ["recorded"]


async def test_a_tell_resting_on_an_abduced_row_is_refused_and_an_ask_is_recorded():
    abduced = _event(
        GatheringStatus.PROPOSED, reasoning="told",
        field_meta=(FieldMeta(field="place", reasoning="abduced"),),
    )
    told = _event(GatheringStatus.PROPOSED, reasoning="told")
    refused, _ = await _remind(intent="tell", rows=(f"event:{abduced.id}",), events=(abduced,))
    assert (refused["status"], refused["reason"]) == ("refused", "abduced_must_ask")
    asked, _ = await _remind(intent="ask", rows=(f"event:{abduced.id}",), events=(abduced,))
    assert asked["status"] == "ok"
    plain, _ = await _remind(intent="tell", rows=(f"event:{told.id}",), events=(told,))
    assert plain["status"] == "ok"
    other, _ = await _remind(intent="tell", rows=("note:1",), events=(abduced,))
    assert other["status"] == "ok"       # the abduced event is not one of the cited rows


async def test_a_remind_past_the_rooms_daily_cap_is_refused_and_one_a_day_later_is_not():
    store = InMemoryCognitionDryRunStore()
    for n, age_ms in enumerate((1_000, 2_000)):
        await store.record_remind_mirror(
            mirror=NewCognitionRemindMirror(
                session_id=None, unit="u", verb="remind", reminder_ref=f"r{n}",
                platform="signal", conversation_id="room-a", intent="tell",
                subject_rows=(f"note:{n}",), at_ms=NOW + 10_000, why=None, outcome="recorded",
            ),
            recorded_at_ms=NOW - age_ms,
        )
    answer, _ = await _remind(
        store=store, runner_kwargs=_kw(max_reminds_per_room_per_day=2),
    )
    assert (answer["status"], answer["reason"]) == ("refused", "room_daily_cap")
    assert _no_digits(answer["detail"])
    roomy, _ = await _remind(store=store, runner_kwargs=_kw(max_reminds_per_room_per_day=3))
    assert roomy["status"] == "ok"
    # Another room's reminders and ones older than a day are not this room's day.
    old = InMemoryCognitionDryRunStore()
    await old.record_remind_mirror(
        mirror=NewCognitionRemindMirror(
            session_id=None, unit="u", verb="remind", reminder_ref="old", platform="signal",
            conversation_id="room-a", intent="tell", subject_rows=("note:9",), at_ms=NOW,
            why=None, outcome="recorded",
        ),
        recorded_at_ms=NOW - 25 * 3_600_000,
    )
    fresh, _ = await _remind(store=old, runner_kwargs=_kw(max_reminds_per_room_per_day=1))
    assert fresh["status"] == "ok"


async def test_a_remind_past_the_pending_ceiling_is_refused_and_says_no_number():
    waiting = tuple(
        PendingMirroredRemind(
            reminder_ref=f"r{n}", platform="signal", conversation_id="room-b",
            subject_rows=(f"note:{n}",), created_by_session_id=None,
        )
        for n in range(3)
    )
    answer, _ = await _remind(
        pending_reminds=waiting, runner_kwargs=_kw(max_pending_reminds=3),
    )
    assert (answer["status"], answer["reason"]) == ("refused", "pending_ceiling")
    assert _no_digits(answer["detail"])
    under, _ = await _remind(pending_reminds=waiting, runner_kwargs=_kw(max_pending_reminds=4))
    assert under["status"] == "ok"


# ---------------------------------------------------------------------------
# the metrics: counted where the session records its outcomes
# ---------------------------------------------------------------------------

async def test_every_refusal_is_counted_by_verb_and_rule_whatever_path_refused_it():
    metrics = Metrics()

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        await by["note_update"](note_id="n1", body=GOOD_BODY, parent_revision_id="rev-9")
        await by["note_create"](**NOTE, body="## Decided\n- x\n## Open\n- y\n")
        await by["close"](item_index=7, reason="no_change")                     # an early return
        await by["close"](item_index=0, reason="written")                       # no write for it
        await by["wake_at"](subject_rows=[], at=iso(NOW + 3_600_000), why="x")
        await by["remind"](intent="tell", subject_rows=[], at=iso(NOW + 3_600_000))
        await by["remind_cancel"](reminder_id="nope", why="x")
        await by["event_revise"](
            event_id="nope", reasoning="told", confidence="high", cited_ids=["101"],
            venue_text="v",
        )

    await _session(
        behave, metrics=metrics, runner_kwargs=_kw(note_head=_heads(n1=NoteHead(HEAD))),
    )

    def n(verb: str, rule: str) -> float:
        return metrics.get("nora_cognition_refused_total", verb=verb, rule=rule)

    assert n("note_update", "stale_base") == 1
    assert n("close", "no_such_item") == 1
    assert n("close", "no_write_for_item") == 1
    assert n("wake_at", "no_subject") == 1
    assert n("remind", "no_citations") == 1
    assert n("remind_cancel", "not_found") == 1
    assert n("event_revise", "not_found") == 1
    assert sum(
        metrics.get("nora_cognition_refused_total", verb=v, rule=r)
        for v, r in (
            ("note_update", "stale_base"), ("close", "no_such_item"),
            ("close", "no_write_for_item"), ("wake_at", "no_subject"),
            ("remind", "no_citations"), ("remind_cancel", "not_found"),
            ("event_revise", "not_found"),
        )
    ) == 7
    # the note_create refusal is the body's own rule, counted under whatever it is
    note_create = [
        rule for rule in ("uncited", "missing_section", "uncited_line")
        if n("note_create", rule)
    ]
    assert len(note_create) == 1


async def test_an_unreadable_head_is_counted_as_unavailable_not_as_a_refusal_of_the_work():
    metrics = Metrics()

    async def broken(unit: str, note_id: str) -> NoteHead | None:
        raise RuntimeError("down")

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        await by["note_update"](note_id="n1", body=GOOD_BODY, parent_revision_id=HEAD)

    await _session(behave, metrics=metrics, runner_kwargs=_kw(note_head=broken))
    assert metrics.get("nora_cognition_refused_total", verb="note_update",
                       rule="unavailable_note_head") == 1
    assert metrics.get("nora_cognition_refused_total", verb="note_update",
                       rule="note_head") == 0


async def test_items_are_counted_by_kind_and_how_they_closed_and_code_closes_are_apart():
    metrics = Metrics()

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        await by["close"](item_index=0, reason="no_change")
        # item 1 is never closed by the model: code closes it `waiting`

    await _session(behave, metrics=metrics, rooms=(_A, _B))
    assert metrics.get("nora_cognition_item_total", kind="message", outcome="no_change") == 1
    assert metrics.get("nora_cognition_item_total", kind="message", outcome="unclosed") == 1
    assert metrics.get("nora_cognition_item_total", kind="message", outcome="waiting") == 0


async def test_tokens_are_counted_by_what_the_agenda_was_made_of_never_by_unit_or_room():
    metrics = Metrics()

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        await by["close"](item_index=0, reason="no_change")

    await _session(behave, metrics=metrics, tokens=1234)
    assert metrics.get("nora_cognition_unit_tokens_total", unit_kind="message") == 1234
    for labels in _series(metrics, "nora_cognition_unit_tokens_total"):
        assert set(labels) == {"unit_kind"} and labels["unit_kind"] in {
            "message", "time", "rollup", "self", "social", "play", "mixed",
        }


async def test_a_run_that_could_not_read_its_usage_adds_no_tokens_rather_than_a_zero():
    metrics = Metrics()

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        await by["close"](item_index=0, reason="no_change")

    await _session(behave, metrics=metrics, tokens=None)
    assert _series(metrics, "nora_cognition_unit_tokens_total") == []


async def test_self_wakes_are_counted_by_outcome():
    metrics = Metrics()
    made: list[Any] = []

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        made.append(await by["wake_at"](
            subject_rows=["event:1"], at=iso(NOW + 3_600_000), why="look",
        ))
        await by["wake_at"](subject_rows=[], at=iso(NOW + 3_600_000), why="refused")
        await by["wake_move"](wake_id=made[0]["wake_id"], at=iso(NOW + 7_200_000), why="m")
        await by["wake_cancel"](wake_id="nope", why="refused")

    await _session(behave, metrics=metrics)
    out = {o: metrics.get("nora_self_wake_total", outcome=o)
           for o in ("created", "moved", "cancelled", "refused", "fired")}
    # the pending seam of this rig reads nothing, so the move finds no wake: refused
    assert out == {"created": 1, "moved": 0, "cancelled": 0, "refused": 3, "fired": 0}


# ---------------------------------------------------------------------------
# the production tool results keep the shape the live verbs would have
# ---------------------------------------------------------------------------

async def test_a_write_that_lands_answers_exactly_status_ok_as_a_live_verb_would():
    answers: list[Any] = []

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        answers.append(await by["standing_revise"](where_it_stands="dinner is on"))
        answers.append(await by["note_update"](
            note_id="n1", body=GOOD_BODY, parent_revision_id=HEAD,
        ))

    await _session(behave, runner_kwargs=_kw(note_head=_heads(n1=NoteHead(HEAD))))
    assert answers == [{"status": "ok"}, {"status": "ok"}]


@pytest.mark.parametrize("rule", ["room_not_proactive", "abduced_must_ask", "room_daily_cap"])
def test_no_remind_refusal_detail_carries_a_number_or_a_mode_word(rule):
    from nora.domain.cognition_remind import RemindContext, check_remind
    contexts = {
        "room_not_proactive": RemindContext(room_proactive=False),
        "abduced_must_ask": RemindContext(abduced_subject=True),
        "room_daily_cap": RemindContext(acts_in_room_today=9, max_acts_per_room_per_day=9),
    }
    refusal = check_remind(
        subject_rows=["note:1"], intent="tell", platform="signal", conversation_id="c",
        pending=(), context=contexts[rule],
    )
    assert refusal is not None and refusal.reason == rule
    assert _no_digits(refusal.detail)


def test_the_composition_requires_and_passes_every_check_the_runner_would_otherwise_skip():
    """An unwired check is skipped by the RUNNER (only a test wants that); the
    composition must therefore be unable to build one without them."""
    import inspect

    from nora.app import composition
    from nora.app.cognition_wiring import build_cognition_runner

    params = inspect.signature(build_cognition_runner).parameters
    for name in ("note_head", "room_proactive"):
        assert params[name].default is inspect.Parameter.empty, name
    wiring = inspect.getsource(build_cognition_runner)
    for name in (
        "note_head=note_head", "room_proactive=room_proactive",
        "max_reminds_per_room_per_day=cfg.cognition.max_reminds_per_room_per_day",
        "max_pending_reminds=cfg.cognition.max_pending_reminds",
    ):
        assert name in wiring, name
    call = inspect.getsource(composition).split("cognition_runner = build_cognition_runner(")[1]
    call = call.split("homes=homes")[0]
    assert "note_head=_note_head" in call and "room_proactive=" in call


def test_every_metric_a_cognition_module_cites_exists_with_the_labels_it_cites():
    """`nora/domain/cognition_pace.py` cited `nora_self_wake_total{transition="fired"}`,
    a series nothing declared (the design named it; the build never shipped it). A name
    and a label a comment cites are a claim about the registry, so they are checked
    against it."""
    from pathlib import Path

    from nora.obs.metric_specs import _COUNTERS, _GAUGES

    declared = {s.name: set(s.labelnames) for s in (*_COUNTERS, *_GAUGES)}
    root = Path(__file__).resolve().parents[2] / "nora"
    cited = re.compile(r"\b(nora_[a-z_]+_total|nora_cognition_session_units)(?:\{([^}]*)\})?")
    seen = 0
    for path in sorted((*root.glob("*/cognition_*.py"), root / "domain" / "stimuli.py")):
        for name, labels in cited.findall(path.read_text()):
            seen += 1
            assert name in declared, f"{path.name} cites {name}, which no spec declares"
            for label in re.findall(r"([a-z_]+)\s*=", labels):
                assert label in declared[name], f"{path.name}: {name} has no label {label!r}"
            for label in (x.strip() for x in labels.split(",") if x.strip() and "=" not in x):
                assert label in declared[name], f"{path.name}: {name} has no label {label!r}"
    assert seen > 10                                  # it read the citations, not nothing

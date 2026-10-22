"""The Persona write's rules (`nora.domain.persona.plan_write`), one test per
rule of design/unified-memory/45 §2 and §3: each test fails if its rule is
taken out. The store's transaction and CHECKs are `tests/infra/test_persona_store.py`.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

import pytest

from nora.domain.persona import (
    PERSONA_HKT,
    TRAIT_DECAY_MS,
    Account,
    AddOp,
    KeepOp,
    PersonaCitable,
    PersonaLine,
    PersonaOp,
    PersonaPlan,
    RetireOp,
    ReviseOp,
    Said,
    handles_for,
    parse_ops,
    plan_write,
    rollup_citation,
)

UNIT = "u1"
SUBJECT = "p-kit"
OTHER = "p-sam"
ROOM = "room-a"
ELSEWHERE = "room-b"
DAY = 24 * 3600 * 1000


def _ms(y: int, m: int, d: int, h: int = 12) -> int:
    return int(datetime(y, m, d, h, tzinfo=PERSONA_HKT).timestamp() * 1000)


NOW = _ms(2026, 10, 17)
HUMAN = Account(key=f"{UNIT}#character:{SUBJECT}", entity_kind="human", unit=UNIT,
                subject=SUBJECT)
ROOM_ACCOUNT = Account(key=ROOM, entity_kind="room", unit=UNIT)
TIE = rollup_citation("tie", f"{UNIT}|{OTHER},{SUBJECT}")

#: 101..103 by the subject on three different days; 201 by someone else;
#: 301..303 by the subject on ONE Hong Kong day (two of them on different UTC
#: days: 00:30 and 23:30 HKT); 401 said in a room the subject is not in.
CITABLE = PersonaCitable(
    messages={
        101: Said(_ms(2026, 10, 1), SUBJECT, ROOM),
        102: Said(_ms(2026, 10, 2), SUBJECT, ROOM),
        103: Said(_ms(2026, 10, 3), SUBJECT, ROOM),
        201: Said(_ms(2026, 10, 4), OTHER, ROOM),
        301: Said(_ms(2026, 10, 5, 0) + 30 * 60_000, SUBJECT, ROOM),
        302: Said(_ms(2026, 10, 5, 12), SUBJECT, ROOM),
        303: Said(_ms(2026, 10, 5, 23) + 30 * 60_000, SUBJECT, ROOM),
        401: Said(_ms(2026, 10, 6), SUBJECT, ELSEWHERE),
    },
    episodes={900: Said(_ms(2026, 10, 7), None, ROOM)},
    rollup_keys=frozenset({TIE}),
    listed_rooms=frozenset({ROOM}),
)


def _plan(ops: list[PersonaOp], heads: tuple[PersonaLine, ...] = (), *, account=HUMAN,
          citable=CITABLE, decay=False, screen=lambda _t: None) -> PersonaPlan:
    return plan_write(account=account, heads=heads, ops=ops, citable=citable, now_ms=NOW,
                      written_by_session="test", screen=screen, apply_decay=decay)


def _reasons(plan: PersonaPlan) -> list[str]:
    return [r.reason for r in plan.refusals]


def _line(n: int, *, part="character", kind="trait", mode="induced", state="current",
          told_by=None, written_at=None, last=None, window=None) -> PersonaLine:
    abduced = mode == "abduced"
    return PersonaLine(
        id=f"00000000-0000-0000-0000-{n:012d}", persona_key=HUMAN.key, part=part, kind=kind,
        text=f"line {n}", mode=mode, citations=("101",), state=state, unit=UNIT,
        written_at=written_at if written_at is not None else NOW - n, written_by_session="s",
        told_by=told_by, prediction="p" if abduced else None,
        prediction_observable="o" if abduced else None,
        prediction_window_end=window if abduced else None,
        verdict="open" if abduced else None, last_supported_at=last,
    )


def _told(text="vegetarian", *, by=SUBJECT, cites=("101",), kind="attribute", **kw) -> AddOp:
    return AddOp(part="identity", kind=kind, text=text, mode="told", citations=cites,
                 told_by=by, fact_key={"attribute": "occupation"}.get(kind, kind),
                 fact_value=text, **kw)


def _induced(cites=("101", "102", "103"), **kw) -> AddOp:
    return AddOp(part="character", kind="trait", text="organises plans", mode="induced",
                 citations=cites, **kw)


# --- part, kind and mode -------------------------------------------------------


def test_a_told_line_is_identity_only() -> None:
    op = AddOp(part="character", kind="trait", text="x", mode="told", citations=("101",),
               told_by=SUBJECT)
    assert _reasons(_plan([op])) == ["mode_part"]


def test_an_induced_line_is_character_only() -> None:
    op = AddOp(part="identity", kind="attribute", text="x", mode="induced",
               citations=("101", "102", "103"), fact_key="occupation", fact_value="x")
    assert _reasons(_plan([op])) == ["mode_part"]


def test_an_abduced_line_is_a_hypothesis_only() -> None:
    op = AddOp(part="character", kind="trait", text="x", mode="abduced", citations=("101",),
               prediction="p", prediction_observable="o", prediction_window_end=NOW + DAY)
    assert _reasons(_plan([op])) == ["mode_part"]


def test_a_kind_belongs_to_its_part() -> None:
    op = AddOp(part="character", kind="attribute", text="x", mode="induced",
               citations=("101", "102", "103"))
    assert _reasons(_plan([op])) == ["part_kind"]


def test_a_collective_holds_no_actor_kind_and_an_actor_no_collective_kind() -> None:
    room_op = AddOp(part="identity", kind="attribute", text="x", mode="deduced",
                    citations=("101", "102"), fact_key="occupation", fact_value="x")
    assert _reasons(_plan([room_op], account=ROOM_ACCOUNT,
                          citable=PersonaCitable(messages=CITABLE.messages))) == ["part_kind"]
    assert _reasons(_plan([_told(kind="purpose")])) == ["part_kind"]


# --- the text -------------------------------------------------------------------


def test_a_line_says_something_in_at_most_300_characters() -> None:
    assert _reasons(_plan([_told(text="  ")])) == ["empty"]
    assert _reasons(_plan([_told(text="x" * 301)])) == ["too_long"]
    assert not _plan([_told(text="x" * 300)]).refused


def test_message_numbers_stay_out_of_the_text() -> None:
    assert _reasons(_plan([_told(text="said so in 101")])) == ["ids_in_text"]


def test_the_egress_screen_refuses_with_its_own_reason() -> None:
    plan = _plan([_told(text="lives at 22.3,114.1")],
                 screen=lambda t: "coordinate" if "22.3" in t else None)
    assert _reasons(plan) == ["coordinate"]


# --- citations ------------------------------------------------------------------


def test_a_line_cites_what_it_rests_on() -> None:
    assert _reasons(_plan([_told(cites=())])) == ["uncited"]
    op = AddOp(part="character", kind="trait", text="x", mode="deduced", citations=())
    assert _reasons(_plan([op])) == ["uncited"]


def test_a_citation_must_be_something_the_writer_was_shown() -> None:
    assert _reasons(_plan([_told(cites=("999",))])) == ["unresolved_citation"]


def test_a_line_is_never_cited_it_is_drawn_on() -> None:
    held = (_line(1),)
    assert _reasons(_plan([_told(cites=("P1",))], held)) == ["line_in_citations"]
    assert _reasons(_plan([_told(cites=(held[0].id,))], held)) == ["line_in_citations"]


def test_evidence_from_a_room_the_subject_is_not_listed_in_is_refused() -> None:
    assert _reasons(_plan([_told(cites=("401",))])) == ["unlisted_room"]


# --- told -----------------------------------------------------------------------


def test_a_told_line_records_who_told_it() -> None:
    plan = _plan([_told()])
    assert not plan.refused
    assert plan.inserts[0].told_by == SUBJECT and plan.inserts[0].mode == "told"
    assert _reasons(_plan([_told(by=None)])) == ["told_by_missing"]


def test_told_by_is_who_said_the_cited_message() -> None:
    assert _reasons(_plan([_told(by=OTHER, cites=("101",))])) == ["told_by_not_speaker"]
    assert not _plan([_told(by=OTHER, cites=("201",))]).refused


def test_a_told_line_cites_only_messages() -> None:
    assert _reasons(_plan([_told(cites=("101", TIE))])) == ["told_needs_message"]
    assert _reasons(_plan([_told(cites=("response:900",))])) == ["told_needs_message"]


def test_an_instruction_is_told_by_its_subject_only() -> None:
    assert not _plan([_told(kind="instruction")]).refused
    plan = _plan([_told(kind="instruction", by=OTHER, cites=("201",))])
    assert _reasons(plan) == ["instruction_not_subject"]


# --- deduced --------------------------------------------------------------------


def _deduced(cites=("101",), draws=()) -> AddOp:
    return AddOp(part="character", kind="trait", text="x", mode="deduced",
                 citations=cites, draws_on=draws)


def test_a_deduction_needs_two_premises() -> None:
    assert _reasons(_plan([_deduced()])) == ["deduced_premises"]
    assert _reasons(_plan([_deduced(cites=("101", "102"))])) == ["induced_instances"]
    assert not _plan([_deduced(cites=("101", "102", "103"))]).refused
    assert _reasons(_plan([_deduced(draws=("P1",))], (_line(1),))) == ["induced_instances"]


def test_a_hypothesis_is_never_a_premise() -> None:
    held = (_line(1, part="hypothesis", kind="explanation", mode="abduced",
                  window=NOW + DAY),)
    assert _reasons(_plan([_deduced(draws=("P1",))], held)) == ["premise_abduced"]


def test_every_premise_line_is_current() -> None:
    held = (_line(1, state="disputed"),)
    assert _reasons(_plan([_deduced(draws=("P1",))], held)) == ["premise_not_current"]


# --- induced --------------------------------------------------------------------


def test_an_induction_needs_three_instances() -> None:
    assert _reasons(_plan([_induced(cites=("101", "102"))])) == ["induced_instances"]


def test_an_induction_spans_two_hong_kong_days() -> None:
    # 301 and 303 fall on two UTC days but one Hong Kong day.
    assert _reasons(_plan([_induced(cites=("301", "302", "303"))])) == ["induced_one_day"]
    assert not _plan([_induced(cites=("301", "302", "101"))]).refused


def test_one_rollup_aggregate_key_needs_dated_occasion_roots() -> None:
    assert _reasons(_plan([_induced(cites=(TIE,))])) == ["support_unavailable"]


# --- abduced --------------------------------------------------------------------


def _abduced(draws=("P1",), **kw) -> AddOp:
    fields = {"prediction": "picks short games on weekdays",
              "prediction_observable": "a short game chosen on a weekday evening",
              "prediction_window_end": NOW + 30 * DAY} | kw
    return AddOp(part="hypothesis", kind="explanation", text="weekday evenings are short",
                 mode="abduced", citations=("101",), draws_on=draws,
                 content={"proposed_cause": "limited evenings", "alternative": "prefers brevity"},
                 **fields)


def test_a_hypothesis_carries_prediction_observable_and_an_open_window() -> None:
    held = (_line(1),)
    assert not _plan([_abduced()], held).refused
    assert _reasons(_plan([_abduced(prediction=None)], held)) == ["abduced_prediction"]
    assert _reasons(_plan([_abduced(prediction_observable=None)], held)) == [
        "abduced_observable"]
    assert _reasons(_plan([_abduced(prediction_window_end=None)], held)) == [
        "abduced_window"]
    assert _reasons(_plan([_abduced(prediction_window_end=NOW)], held)) == [
        "abduced_window_closed"]


def test_a_hypothesis_explains_a_current_induced_trait() -> None:
    told = (_line(1, part="identity", kind="attribute", mode="told", told_by=SUBJECT),)
    assert _reasons(_plan([_abduced()], told)) == ["abduced_explains_nothing"]
    assert _reasons(_plan([_abduced(draws=())], (_line(1),))) == ["abduced_explains_nothing"]


def test_a_new_hypothesis_starts_open() -> None:
    plan = _plan([_abduced()], (_line(1),))
    assert plan.inserts[0].verdict == "open"


# --- precedence -----------------------------------------------------------------


def test_told_by_the_subject_outranks_an_induced_trait_and_both_stay() -> None:
    held = (_line(1),)  # induced
    op = AddOp(part="character", kind="trait", text="x", mode="deduced",
               citations=("101", "102", "103"), conflicts_with="P1")
    plan = _plan([op], held)
    assert not plan.refused
    lowered = [x for x in plan.inserts if x.supersedes == held[0].id]
    assert [x.state for x in lowered] == ["outranked"]
    assert [x.state for x in plan.inserts if x.supersedes is None] == ["current"]


def test_a_lower_rank_line_is_written_outranked() -> None:
    held = (_line(1, part="identity", kind="attribute", mode="told", told_by=SUBJECT),)
    op = AddOp(part="identity", kind="attribute", text="x", mode="deduced",
               citations=("101", "102"), conflicts_with="P1", fact_key="occupation", fact_value="x")
    plan = _plan([op], held)
    assert [x.state for x in plan.inserts] == ["outranked"]


def test_told_by_the_subject_outranks_told_by_someone_else() -> None:
    held = (_line(1, part="identity", kind="attribute", mode="told", told_by=OTHER),)
    plan = _plan([_told(conflicts_with="P1")], held)
    assert {x.state for x in plan.inserts} == {"outranked", "current"}
    assert next(x for x in plan.inserts if x.supersedes).state == "outranked"


def test_a_conflict_on_the_same_footing_is_refused() -> None:
    held = (_line(1, part="identity", kind="attribute", mode="told", told_by=SUBJECT),)
    assert _reasons(_plan([_told(conflicts_with="P1")], held)) == ["same_rank"]


def test_a_hypothesis_never_conflicts() -> None:
    held = (_line(1), _line(2, part="hypothesis", kind="explanation", mode="abduced",
                            window=NOW + DAY))
    assert _reasons(_plan([_told(conflicts_with="P2")], held)) == ["conflict_hypothesis"]


def test_rewording_an_outranked_line_does_not_win_it_back() -> None:
    held = (_line(1, state="outranked"),)
    plan = _plan([ReviseOp(line="P1", citations=("101", "102", "103"), text="y")], held)
    assert [x.state for x in plan.inserts] == ["outranked"]


# --- caps and decay -------------------------------------------------------------


def test_a_tenth_trait_is_over_the_cap_unless_one_is_retired() -> None:
    held = tuple(_line(n) for n in range(1, 10))
    assert _reasons(_plan([_induced()], held)) == ["over_cap"]
    assert not _plan([_induced(), RetireOp(line="P1", why="stale")], held).refused


def test_retiring_a_hypothesis_early_frees_no_slot() -> None:
    held = (_line(9), *(
        _line(n, part="hypothesis", kind="explanation", mode="abduced", window=NOW + DAY)
        for n in range(1, 4)))
    handle_of_first_hypothesis = "P2"
    plan = _plan([RetireOp(line=handle_of_first_hypothesis, why="going badly"),
                  _abduced(draws=("P1",))], held)
    assert _reasons(plan) == ["over_cap"]


def test_a_trait_unsupported_for_sixty_days_is_retired_at_the_session() -> None:
    stale = _line(1, last=NOW - TRAIT_DECAY_MS - 1)
    plan = _plan([_told()], (stale,), decay=True)
    assert [x.state for x in plan.inserts if x.supersedes == stale.id] == ["retired"]
    kept = _plan([KeepOp(line="P1", citations=("103",))], (stale,), decay=True)
    assert [x.state for x in kept.inserts] == ["current"]
    assert not _plan([_told()], (stale,), decay=False).inserts[1:]


# --- the call -------------------------------------------------------------------


def test_one_refused_op_refuses_the_whole_call() -> None:
    plan = _plan([_told(), _told(cites=("999",))])
    assert plan.refused and not plan.inserts
    assert [r.op_index for r in plan.refusals] == [1]


def test_a_line_is_acted_on_once_per_call() -> None:
    held = (_line(1),)
    plan = _plan([RetireOp(line="P1", why="a"), RetireOp(line="P1", why="b")], held)
    assert _reasons(plan) == ["line_twice"]


def test_handles_name_held_lines_only() -> None:
    held = (_line(1), _line(2, state="retired"), _line(3, state="hidden"))
    assert list(handles_for(held).values()) == [held[0].id]


@pytest.mark.parametrize("raw", [[], "x", [{"op": "nope"}], [{"op": "add"}]])
def test_a_malformed_call_is_refused(raw) -> None:
    _ops, refusals = parse_ops(raw)
    assert refusals and refusals[0].reason == "malformed"


# --- review round: rules that hold across ops in one call -------------------------


def test_an_instruction_is_never_deduced() -> None:
    op = AddOp(part="identity", kind="instruction", text="x", mode="deduced",
               citations=("101", "102"), fact_key="instruction", fact_value="x")
    assert _reasons(_plan([op])) == ["instruction_not_subject"]
    told = (_line(1, part="identity", kind="instruction", mode="told", told_by=SUBJECT),)
    revise = ReviseOp(line="P1", citations=("101", "102"), mode="deduced",
                      fact_key="instruction", fact_value="x")
    assert _reasons(_plan([revise], told)) == ["instruction_not_subject"]


def test_a_premise_retired_earlier_in_the_call_is_not_current() -> None:
    plan = _plan([RetireOp(line="P1", why="gone"), _deduced(draws=("P1",))], (_line(1),))
    assert _reasons(plan) == ["premise_not_current"]


def test_a_hypothesis_cannot_explain_a_trait_that_decays_in_the_same_call() -> None:
    stale = (_line(1, last=NOW - TRAIT_DECAY_MS - 1),)
    assert _reasons(_plan([_abduced()], stale, decay=True)) == ["premise_not_current"]
    assert not _plan([_abduced()], stale, decay=False).refused


def test_re_citing_old_evidence_does_not_stop_a_trait_decaying() -> None:
    stale = _line(1, last=NOW - TRAIT_DECAY_MS - 1)
    old = replace(stale, citations=("101",))
    plan = _plan([KeepOp(line="P1", citations=("101",))], (old,), decay=True)
    assert [x.state for x in plan.inserts] == ["retired"]


def test_a_revision_is_stored_stripped() -> None:
    plan = _plan([ReviseOp(line="P1", citations=("101", "102", "103"),
                           text="y" * 290 + " " * 40)], (_line(1),))
    assert plan.inserts[0].text == "y" * 290


def test_a_revision_keeps_premises_revised_since_through_their_chain() -> None:
    premise = _line(1)
    newer = replace(premise, id="00000000-0000-0000-0000-000000000099",
                    supersedes=premise.id)
    deduced = replace(_line(2, mode="deduced"), draws_on=(premise.id,),
                      citations=("101",))
    plan = plan_write(
        account=HUMAN, heads=(newer, deduced),
        ops=[ReviseOp(line=next(h for h, i in handles_for((newer, deduced)).items()
                                if i == deduced.id), citations=("101", "102", "103"))],
        citable=CITABLE, now_ms=NOW, written_by_session="t", screen=lambda _t: None,
        apply_decay=False, aliases={premise.id: newer.id},
    )
    assert not plan.refused and plan.inserts[0].draws_on == (newer.id,)


def test_fields_that_belong_to_another_mode_are_refused() -> None:
    told_by = AddOp(part="character", kind="trait", text="x", mode="induced",
                    citations=("101", "102", "103"), told_by=SUBJECT)
    assert _reasons(_plan([told_by])) == ["told_by_unexpected"]
    assert _reasons(_plan([_told(prediction_window_end=0)])) == ["prediction_unexpected"]
    drawn = AddOp(part="character", kind="trait", text="x", mode="induced",
                  citations=("101", "102", "103"), draws_on=("P1",))
    assert _reasons(_plan([drawn], (_line(1),))) == ["draws_on_unexpected"]


def test_a_retirement_says_why_and_names_a_shown_line() -> None:
    assert _reasons(_plan([RetireOp(line="P1", why=" ")], (_line(1),))) == ["retire_why"]
    assert _reasons(_plan([RetireOp(line="P7", why="x")], (_line(1),))) == ["unknown_line"]


# --- social items -----------------------------------------------------------------


def _item(n: str) -> str:
    return f"social:instagram:{n}"


#: The subject's items 7001, 7002 and story frame 7003_8004 on two Hong Kong
#: days; 7101..7103 on ONE Hong Kong day (two UTC days); 7201 by someone else.
SOCIAL = replace(CITABLE, social={
    "instagram:7001": Said(_ms(2026, 10, 8), SUBJECT, None),
    "instagram:7002": Said(_ms(2026, 10, 9), SUBJECT, None),
    "instagram:7003_8004": Said(_ms(2026, 10, 8, 18), SUBJECT, None),
    "instagram:7101": Said(_ms(2026, 10, 11, 0) + 30 * 60_000, SUBJECT, None),
    "instagram:7102": Said(_ms(2026, 10, 11, 12), SUBJECT, None),
    "instagram:7103": Said(_ms(2026, 10, 11, 23) + 30 * 60_000, SUBJECT, None),
    "instagram:7201": Said(_ms(2026, 10, 9), OTHER, None),
})


def test_social_items_are_induced_instances_counted_on_hong_kong_days() -> None:
    two_days = (_item("7001"), _item("7002"), _item("7003_8004"))
    plan = _plan([_induced(cites=two_days)], citable=SOCIAL)
    assert not plan.refused
    assert plan.inserts[0].last_supported_at == _ms(2026, 10, 9)
    one_day = (_item("7101"), _item("7102"), _item("7103"))
    assert _reasons(_plan([_induced(cites=one_day)], citable=SOCIAL)) == ["induced_one_day"]


def test_a_social_item_is_a_deduced_premise() -> None:
    assert not _plan([_deduced(cites=("101", "102", _item("7001")))], citable=SOCIAL).refused
    assert _reasons(_plan([_deduced(cites=(_item("7001"),))], citable=SOCIAL)) == [
        "deduced_premises"]


def test_a_social_item_not_handed_is_refused() -> None:
    for cite in (_item("9999"), "social:threads:7001", _item("7001")):
        citable = CITABLE if cite == _item("7001") else SOCIAL
        plan = _plan([_deduced(cites=("101", cite))], citable=citable)
        assert _reasons(plan) == ["unresolved_citation"], cite


def test_a_social_item_is_evidence_about_its_author_only() -> None:
    plan = _plan([_deduced(cites=("101", _item("7201")))], citable=SOCIAL)
    assert _reasons(plan) == ["social_not_author"]
    room_trait = AddOp(part="character", kind="trait", text="x", mode="induced",
                       citations=(_item("7001"), _item("7002"), _item("7003_8004")))
    assert _reasons(_plan([room_trait], account=ROOM_ACCOUNT, citable=replace(
        SOCIAL, listed_rooms=None))) == ["social_not_author"]


def test_a_social_item_counts_only_where_its_author_is_listed_in_the_unit() -> None:
    op = _deduced(cites=("101", _item("7001")))
    for listed in (frozenset(), None):
        citable = replace(SOCIAL, listed_rooms=listed)
        assert _reasons(_plan([op], citable=citable))[-1:] == ["unlisted_room"], listed


def test_a_told_line_never_cites_a_social_item() -> None:
    assert _reasons(_plan([_told(cites=(_item("7001"),))], citable=SOCIAL)) == [
        "told_needs_message"]
    assert _reasons(_plan([_told(cites=("101", _item("7001")))], citable=SOCIAL)) == [
        "told_needs_message"]


def test_social_item_numbers_stay_out_of_the_text() -> None:
    for text in ("posted 7001", "her story 8004"):
        assert _reasons(_plan([_told(text=text)], citable=SOCIAL)) == ["ids_in_text"], text


def test_keeping_a_trait_on_a_social_item_supports_it_as_of_when_it_was_taken() -> None:
    held = (_line(1, last=_ms(2026, 10, 1)),)
    plan = _plan([KeepOp(line="P1", citations=(_item("7002"),))], held, citable=SOCIAL)
    assert not plan.refused
    assert plan.inserts[0].last_supported_at == _ms(2026, 10, 9)
    assert plan.inserts[0].support_count == 2


def test_keeping_a_told_line_never_adds_a_social_citation() -> None:
    held = (_line(1, part="identity", kind="attribute", mode="told", told_by=SUBJECT),)
    assert not _plan([KeepOp(line="P1", citations=("102",))], held, citable=SOCIAL).refused
    plan = _plan([KeepOp(line="P1", citations=(_item("7001"),))], held, citable=SOCIAL)
    assert _reasons(plan) == ["told_needs_message"]


@pytest.mark.parametrize("text", [None, "birthday stated in spring"])
def test_same_typed_birthday_revision_retains_assertion_and_grounding(text):
    # t1: "My birthday is May 6". t2: "Happy birthday!" to somebody else.
    # Said intentionally carries provenance only: the planner cannot judge meaning.
    [original] = _plan([AddOp(
        part="identity", kind="attribute", mode="told", text="birthday May 6",
        citations=("101",), told_by=SUBJECT,
        fact_key="birthday", fact_value={"month": 5, "day": 6},
    )]).inserts
    result = _plan([ReviseOp(
        line="P1", text=text, citations=("102",),
        fact_key="birthday", fact_value={"month": 5, "day": 6},
    )], (original,))
    assert not result.refusals
    [reworded] = result.inserts
    assert reworded.supersedes == original.id
    assert reworded.citations == ("101", "102")
    assert reworded.as_of == original.as_of == CITABLE.messages[101].ts
    assert reworded.support_count == original.support_count == 1
    assert reworded.last_supported_at == original.last_supported_at
    # A real correction at t2 still replaces the value and assertion time.
    changed = _plan([ReviseOp(
        line="P1", citations=("102",), fact_key="birthday",
        fact_value={"month": 5, "day": 7},
    )], (original,))
    assert not changed.refusals
    [corrected] = changed.inserts
    assert corrected.fact_value == {"month": 5, "day": 7}
    assert corrected.as_of == CITABLE.messages[102].ts
    assert corrected.citations == ("102",)


def test_explicit_height_roundtrips_through_real_planner_and_shared_formatter():
    from nora.domain.persona import identity_text

    op = replace(_told("I am 178 cm tall"), fact_key="attribute",
                 fact_value={"name": "height", "value": 178, "unit": "cm"})
    plan = _plan([op])
    assert not plan.refusals, plan.refusals
    [line] = plan.inserts
    assert line.fact_value == op.fact_value
    assert line.as_of == CITABLE.messages[101].ts
    assert identity_text(line).startswith("height: 178 cm (asserted ")


@pytest.mark.parametrize("value", [
    None, {}, {"name": "height", "value": None},
    {"name": "height", "value": {"amount": 178}},
    {"name": "height", "value": [{"amount": 178}]},
    {"name": "height", "value": float("nan")},
    {"name": "height", "value": float("inf")},
    {"name": "height", "value": ""}, {"name": "height", "value": []},
    {"name": "height", "value": [""]},
    {"name": "height", "value": 178, "unit": ""},
    {"name": "height", "value": 178, "extra": "x"},
    {"name": "", "value": 178},
    {"name": "height" * 100, "value": 178},
])
def test_invalid_fallback_shapes_fail_closed(value):
    plan = _plan([replace(_told(), fact_key="attribute", fact_value=value)])
    assert _reasons(plan) == ["invalid_fact"]


@pytest.mark.parametrize("name", ["birthday", " Gender ", "sex", "residence",
                                  "food-preferences", "dietary restrictions", "attribute"])
def test_fallback_cannot_alias_dedicated_contract(name):
    plan = _plan([replace(_told(), fact_key="attribute",
                         fact_value={"name": name, "value": "claim"})])
    assert _reasons(plan) == ["invalid_fact"]


def test_fallback_is_human_told_only_and_retains_normal_source_checks():
    op = replace(_told(), fact_key="attribute",
                 fact_value={"name": "height", "value": 178, "unit": "cm"})
    assert _reasons(_plan([replace(op, mode="deduced", told_by=None,
                                  citations=("101", "102"))])) == ["invalid_fact"]
    assert _reasons(_plan([op], account=replace(HUMAN, entity_kind="self"))) == ["invalid_fact"]
    assert _reasons(_plan([op], account=ROOM_ACCOUNT)) == ["part_kind"]
    assert _reasons(_plan([replace(op, told_by=OTHER)])) == ["told_by_not_speaker"]
    assert _reasons(_plan([replace(op, citations=("401",))])) == ["unlisted_room"]
    third_party = _plan([replace(op, told_by=OTHER, citations=("201",))])
    assert not third_party.refusals
    assert third_party.inserts[0].told_by == OTHER


@pytest.mark.parametrize(("before", "after", "same"), [
    (178, 178.0, True), (178.0, 178, True),
    (0, -0.0, True), (-0.0, 0, True),
    (True, 1, False), (1, True, False), (178, 179, False),
])
def test_fallback_revision_numeric_equality_preserves_provenance(before, after, same):
    initial = _plan([replace(_told("measurement stated"), fact_key="attribute",
                            fact_value={"name": "measurement", "value": before})])
    assert not initial.refusals
    [original] = initial.inserts
    result = _plan([ReviseOp(
        line="P1", text="measurement reworded", citations=("102",),
        fact_key="attribute", fact_value={"name": "measurement", "value": after},
    )], (original,))
    assert not result.refusals
    [revised] = result.inserts
    assert revised.supersedes == original.id
    assert revised.fact_value == {"name": "measurement", "value": after}
    if same:
        assert revised.as_of == original.as_of == CITABLE.messages[101].ts
        assert revised.citations == ("101", "102")
        assert revised.support_count == original.support_count == 1
        assert revised.last_supported_at == original.last_supported_at
    else:
        assert revised.as_of == CITABLE.messages[102].ts != original.as_of
        assert revised.citations == ("102",)


@pytest.mark.parametrize("name", ["age", "height"])
@pytest.mark.parametrize("body,unit", [
    (34, None), (34, ""), (34, " "), (34, True),
    (True, "years"), ("34", "years"), (["34"], "years"),
    (float("inf"), "years"), (float("nan"), "years"),
])
def test_quantity_attributes_require_numeric_value_and_source_unit(name, body, unit):
    value = {"name": name, "value": body}
    if unit is not None:
        value["unit"] = unit
    plan = _plan([replace(_told(), fact_key="attribute", fact_value=value)])
    assert _reasons(plan) == ["invalid_fact"]
    assert "age and height require a numeric value and source unit" in plan.refusals[0].detail
    assert not plan.inserts


@pytest.mark.parametrize("value", [
    {"name": "age", "value": 34, "unit": "years"},
    {"name": "height", "value": 178.5, "unit": "cm"},
    {"name": "shoe_size", "value": 42},
])
def test_quantity_contract_preserves_assertion_without_computing_age_or_birthday(value):
    from nora.domain.persona import identity_text

    plan = _plan([replace(_told(), fact_key="attribute", fact_value=value)])
    assert not plan.refusals
    [line] = plan.inserts
    assert line.fact_key == "attribute" and line.fact_value == value
    assert line.as_of == CITABLE.messages[101].ts
    assert line.valid_from is None and line.valid_until is None
    rendered = identity_text(line)
    assert "asserted 2026-10-01" in rendered
    assert "birthday" not in rendered and "current age" not in rendered


def test_rollup_rebuild_and_alias_with_same_roots_cannot_add_support_or_refresh_age():
    other = rollup_citation('daybook', UNIT + '|' + SUBJECT)
    citable = replace(CITABLE, rollup_keys=frozenset({TIE, other}),
        rollup_roots={TIE: ('101', '102', '103'), other: ('101', '102', '103')})
    first = _plan([AddOp(part='character', kind='trait', text='a repeated pattern',
        mode='induced', citations=(TIE,))], citable=citable)
    assert not first.refused
    line = first.inserts[0]
    assert line.support_count == 3 and line.last_supported_at == CITABLE.messages[103].ts
    assert line.as_of == CITABLE.messages[103].ts
    second = _plan([KeepOp(line='P1', citations=(other, '101'))], heads=(line,), citable=citable)
    assert not second.refused
    assert second.inserts[0].support_count == 3
    assert second.inserts[0].last_supported_at == line.last_supported_at
    # A genuinely new root can support the same aggregate, once.
    newer = replace(citable, rollup_roots={TIE: ('101', '102', '103', '201')})
    added = _plan([KeepOp(line='P1', citations=(TIE,))], heads=(line,), citable=newer)
    assert added.inserts[0].support_count == 4
    assert added.inserts[0].last_supported_at == CITABLE.messages[201].ts


def test_same_root_through_rollup_is_not_two_deduction_premises_and_reading_is_not_told():
    citable = replace(CITABLE, rollup_roots={TIE: ('101',)},
                      interpretation_only=frozenset({101}))
    deduced = _plan([_deduced(cites=('101', TIE))], citable=citable)
    assert _reasons(deduced) == ['deduced_premises']
    told = _plan([_told(cites=('101',))], citable=citable)
    assert _reasons(told) == ['told_needs_message']
    # A genuine raw statement remains eligible.
    assert not _plan([_told(cites=('101',))]).refused

"""Profile store, render, and routing contracts independent of the session verbs."""
from dataclasses import replace
from datetime import UTC, datetime

from nora.app.composition_helpers import _cognition_writes_profile
from nora.domain.profile import Account, AddOp, ProfileCitable, RetireOp, ReviseOp, Said
from nora.language.profile_render import ProfileView, render_profile
from nora.testing.profile import InMemoryProfileStore

UNIT = "u1"
PERSON = "p1"
ROOM = "room1"
ACCOUNT = Account(f"{UNIT}#character:{PERSON}", "human", UNIT, PERSON)
AS_OF = datetime(2025, 1, 5, tzinfo=UTC)
NOW = int(AS_OF.timestamp() * 1000)
CITATIONS = ("11", "12", "13")
CITABLE = ProfileCitable(
    messages={
        n: Said(int(datetime(2025, 1, day, tzinfo=UTC).timestamp() * 1000), PERSON, ROOM)
        for n, day in zip((11, 12, 13), (1, 2, 3), strict=True)
    },
    listed_rooms=frozenset({ROOM}),
)
RELATION = "relation:rel-1"


def _habit(statement: str, action: str) -> dict[str, str]:
    return {"statement": statement, "context": "weeknights", "action": action,
            "rhythm": "daily", "setting": "in the neighbourhood"}


DEDUCED = AddOp(part="traits", kind="habit", mode="deduced", text="plans reliably",
                content=_habit("plans reliably", "plans outings"),
                citations=CITATIONS, draws_on=(RELATION,))
INDUCED = AddOp(part="traits", kind="habit", mode="induced", text="walks in the evening",
                content=_habit("walks in the evening", "evening walks"), citations=CITATIONS)


async def _write(store, *ops, relations=()):
    return await store.profile_write(
        account=ACCOUNT, ops=ops, citable=replace(CITABLE, relations=frozenset(relations)),
        now_ms=NOW, written_by_session="generated", apply_decay=False,
    )


async def _render(store):
    heads = await store.account_heads(key=ACCOUNT.key)
    return render_profile(
        ProfileView(entity="person", of=PERSON, lines=heads),
        name_of=lambda pid: pid, now=AS_OF,
    )


async def test_write_then_later_turn_renders():
    store = InMemoryProfileStore()
    refused = await _write(store, DEDUCED)
    assert [refusal.reason for refusal in refused.refusals] == ["unknown_premise"]

    written = await _write(store, DEDUCED, relations=("rel-1",))
    assert written.status == "ok"
    [head] = await store.account_heads(key=ACCOUNT.key)
    assert head.draws_on == (RELATION,)
    block = await _render(store)
    assert block is not None and "plans reliably" in block.body.text


async def test_correction_and_retirement_change_the_later_read():
    store = InMemoryProfileStore()
    assert (await _write(store, INDUCED)).status == "ok"
    revise = ReviseOp(line="P1", text="walks at night",
                      content=_habit("walks at night", "night walks"), citations=CITATIONS)
    assert (await _write(store, revise)).status == "ok"
    block = await _render(store)
    assert block is not None and "walks at night" in block.body.text
    assert "walks in the evening" not in block.body.text

    assert (await _write(store, RetireOp(line="P1", why="no longer true"))).status == "ok"
    [head] = await store.account_heads(key=ACCOUNT.key)
    assert head.state == "retired"
    block = await _render(store)
    assert block is None or "walks at night" not in block.body.text


async def test_abduced_guess_rests_on_a_current_induced_trait():
    store = InMemoryProfileStore()
    assert (await _write(store, INDUCED)).status == "ok"
    guess = AddOp(
        part="guesses", kind="explanation", mode="abduced", text="walks after the gym",
        content={"proposed_cause": "late gym visits", "alternative": "unknown"},
        draws_on=("P1",), citations=("13",), prediction="keeps evening gym sessions",
        prediction_observable="late-evening gym visits",
        prediction_window_end=int(datetime(2025, 2, 1, tzinfo=UTC).timestamp() * 1000),
    )
    assert (await _write(store, guess)).status == "ok"
    heads = await store.account_heads(key=ACCOUNT.key)
    [trait] = [head for head in heads if head.part == "traits"]
    [line] = [head for head in heads if head.part == "guesses"]
    assert line.verdict == "open" and line.draws_on == (trait.id,)


async def test_a_carried_relation_premise_survives_a_later_session():
    store = InMemoryProfileStore()
    assert (await _write(store, DEDUCED, relations=("rel-1",))).status == "ok"
    revise = ReviseOp(line="P1", text="plans early",
                      content=_habit("plans early", "plans outings"), citations=CITATIONS)
    assert (await _write(store, revise)).status == "ok"
    [head] = await store.account_heads(key=ACCOUNT.key)
    assert head.content["statement"] == "plans early" and head.draws_on == (RELATION,)


async def test_explicit_relation_premises_replace_instead_of_carrying():
    store = InMemoryProfileStore()
    assert (await _write(store, DEDUCED, relations=("rel-1",))).status == "ok"
    revise = ReviseOp(line="P1", text="plans early", draws_on=(),
                      content=_habit("plans early", "plans outings"), citations=CITATIONS)
    assert (await _write(store, revise)).status == "ok"
    [head] = await store.account_heads(key=ACCOUNT.key)
    assert head.content["statement"] == "plans early" and head.draws_on == ()


def test_profile_writer_ownership_matrix() -> None:
    """The Cognition session owns the Profile accounts exactly where it is wired:
    the Profile switch and graph comprehension both on."""
    coverage = {"cognition_enabled": True, "units": frozenset({UNIT}),
                "home_units": frozenset({UNIT})}
    assert _cognition_writes_profile(profile_index=True, graph_comprehend=True, **coverage)
    for profile_index, comprehend in ((False, True), (True, False), (False, False)):
        assert not _cognition_writes_profile(
            profile_index=profile_index, graph_comprehend=comprehend, **coverage)


def test_cognition_does_not_own_profile_without_cognition_or_complete_unit_coverage() -> None:
    flags = {"profile_index": True, "graph_comprehend": True,
             "cognition_enabled": True, "units": frozenset({"one", "two"}),
             "home_units": frozenset({"one", "two"})}
    assert _cognition_writes_profile(**flags)
    assert not _cognition_writes_profile(**{**flags, "cognition_enabled": False})
    assert not _cognition_writes_profile(**{**flags, "home_units": frozenset({"one"})})
    assert not _cognition_writes_profile(**{**flags, "units": frozenset()})


async def test_no_change_keeps_the_real_store_revision():
    store = InMemoryProfileStore()
    result = await _write(store)
    assert result.status == "ok" and result.revision == 0
    assert (await store.account_state(key=ACCOUNT.key))[0] == 0

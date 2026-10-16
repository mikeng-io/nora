"""The Cognition session's memo verbs (DEC-306):
`memo_write`, `memo_revise`, `memo_withdraw`. Each holds the body to the memo
schema with the note body's line grammar, screens everything but the `## Kept`
lines (D5, DEC-230), commits through the memo store stamped `cognition:<unit>`, and
answers a refusal to the model. Over in-memory stores with a scripted agent
(`tests/unit/cognition_rig.py`); names are invented and no message is quoted."""

from __future__ import annotations

from typing import Any

from nora.domain.cognition_ground import Grounding, RecordItem
from nora.testing.cognition_grounding import StaticGrounder
from tests.unit.cognition_rig import GOOD_BODY, NOTE, NOW, RECORDS, A, Stores, session

PROVENANCE = "cognition:u"
KEPT = "## Kept\n- the wifi password is hunter-two [101]\n"
#: Synthetic, key-shaped and phone-shaped values: what D5 lets a Kept line hold.
KEY = "sk-abcdefghijklmnopqrstuvwx"
PHONE = "+85290000101"

#: Nora's own send in the item's window (D1): a memo may cite it, a note may not.
OWN = RecordItem(
    ref="messages:9", kind="message", message_id=103, room=A, author="Nora",
    at_ms=NOW, text="the code is on the router", own=True,
)
WITH_OWN = {"grounder": StaticGrounder(Grounding(records=dict(RECORDS), own_sends={0: (OWN,)}))}


async def _seed(stores: Stores, title: str = "wifi", unit: str = "u") -> Any:
    return await stores.memos.create(
        unit=unit, platform="signal", conversation_id="room-a", title=title, body=KEPT,
        citations=("101",), mode="told", established_by="cognition:u", model=None,
        now_ms=NOW - 1000,
    )


async def _one(call: str, runner_kwargs: Any = None, stores: Stores | None = None,
               **arguments: Any) -> tuple[Stores, dict[str, Any]]:
    answers: list[Any] = []

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        answers.append(await by[call](**arguments))

    stores, _ = await session(behave, stores=stores, runner_kwargs=runner_kwargs)
    return stores, answers[0]


async def test_memo_write_keeps_a_memo_with_cognition_provenance_and_backs_a_written_close():
    answers: list[Any] = []

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        answers.append(await by["memo_write"](title="wifi", body=KEPT))
        answers.append(await by["close"](item_index=0, reason="written"))

    stores, outcome = await session(behave)
    assert answers[0]["status"] == "ok" and answers[1]["status"] == "ok"
    (memo,) = await stores.memos.kept(unit="u")
    assert memo.id == answers[0]["memo_id"] and memo.title == "wifi"
    assert memo.head.established_by == PROVENANCE and memo.head.citations == ("101",)
    assert memo.head.mode == "told"
    assert (memo.platform, memo.conversation_id) == ("signal", "room-a")
    assert [c.reason for c in outcome.closed] == ["written"]


async def test_a_kept_line_holds_a_key_and_a_phone_verbatim():
    """D5: the room said it, so the memo keeps it as said."""
    body = f"## Kept\n- the router key is {KEY} [101]\n- the landlord is {PHONE} [102]\n"
    stores, answer = await _one("memo_write", title="router", body=body)
    assert answer["status"] == "ok"
    (memo,) = await stores.memos.kept(unit="u")
    assert KEY in memo.head.body and PHONE in memo.head.body


async def test_the_screen_still_reads_the_title_and_the_conditions():
    """DEC-230's falsifier: the exemption reaches the `## Kept` lines and nothing
    else, so the same value in a title or a condition is refused."""
    _, in_title = await _one("memo_write", title=f"call {PHONE}", body=KEPT)
    _, in_conditions = await _one(
        "memo_write", title="wifi", body=KEPT + f"## Conditions\n- ask {PHONE} first [101]\n",
    )
    _, a_key_in_conditions = await _one(
        "memo_write", title="wifi", body=KEPT + f"## Conditions\n- unless {KEY} [101]\n",
    )
    for answer in (in_title, in_conditions, a_key_in_conditions):
        assert answer["status"] == "refused" and answer["reason"] == "identifier"
    stores = Stores()
    memo = await _seed(stores)
    _, revised = await _one(
        "memo_revise", stores=stores, memo_id=memo.id, base_revision_id=memo.head.id,
        body=KEPT, title=f"wifi {PHONE}",
    )
    assert revised["reason"] == "identifier"
    assert (await stores.memos.get(unit="u", memo_id=memo.id)).head.seq == 1


async def test_a_model_written_superseded_section_is_refused():
    _, answer = await _one(
        "memo_write", title="wifi", body=KEPT + "## Superseded\n- old password [101]\n",
    )
    assert answer["status"] == "refused" and answer["reason"] == "code_written"


async def test_a_memo_with_nothing_kept_or_citing_an_unshown_message_is_refused():
    _, empty = await _one("memo_write", title="wifi", body="## Conditions\n- weekdays [101]\n")
    _, unshown = await _one("memo_write", title="wifi", body=KEPT.replace("[101]", "[999]"))
    _, uncited = await _one("memo_write", title="wifi", body="## Kept\n- the password\n")
    assert (empty["reason"], unshown["reason"], uncited["reason"]) == (
        "missing_section", "unknown_id", "uncited")


async def test_a_memo_may_cite_noras_own_send_and_a_note_still_may_not():
    """«記低» keeps what she found, as she found it; a note stays the group's."""
    body = "## Kept\n- the code is on the router [101, 103]\n"
    stores, memo = await _one("memo_write", runner_kwargs=WITH_OWN, title="code", body=body)
    assert memo["status"] == "ok"
    (kept,) = await stores.memos.kept(unit="u")
    assert kept.head.citations == ("101", "103")
    _, note = await _one(
        "note_create", runner_kwargs=WITH_OWN,
        body=GOOD_BODY.replace("which place [101]", "which place [103]"), **NOTE,
    )
    assert note["status"] == "refused" and note["reason"] == "cites_nora"


async def test_a_second_memo_under_a_kept_title_is_refused_naming_the_first():
    stores = Stores()
    memo = await _seed(stores)
    _, answer = await _one("memo_write", stores=stores, title="WiFi", body=KEPT)
    assert answer["reason"] == "duplicate" and memo.id in answer["detail"]
    assert len(await stores.memos.kept(unit="u")) == 1


async def test_memo_revise_appends_and_a_stale_base_is_refused():
    stores = Stores()
    memo = await _seed(stores)
    answers: list[Any] = []
    body = KEPT + "## Conditions\n- only on weekdays [102]\n"

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        for _ in range(2):
            answers.append(await by["memo_revise"](
                memo_id=memo.id, base_revision_id=memo.head.id, body=body,
            ))

    await session(behave, stores=stores)
    assert answers[0]["status"] == "ok"
    assert answers[1]["status"] == "refused" and answers[1]["reason"] == "stale_base"
    head = (await stores.memos.get(unit="u", memo_id=memo.id)).head
    assert head.seq == 2 and head.body.endswith("[102]")
    assert head.established_by == PROVENANCE and head.citations == ("101", "102")


async def test_memo_withdraw_needs_a_cited_message_and_leaves_the_memo_unrevisable():
    stores = Stores()
    memo = await _seed(stores)
    answers: list[Any] = []

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        answers.append(await by["memo_withdraw"](
            memo_id=memo.id, base_revision_id=memo.head.id, cited_ids=[],
        ))
        answers.append(await by["memo_withdraw"](
            memo_id=memo.id, base_revision_id=memo.head.id, cited_ids=["102"],
        ))
        answers.append(await by["memo_revise"](
            memo_id=memo.id, base_revision_id=memo.head.id, body=KEPT,
        ))

    await session(behave, stores=stores)
    assert [a["status"] for a in answers] == ["refused", "ok", "refused"]
    assert answers[0]["reason"] == "uncited" and answers[2]["reason"] == "withdrawn"
    assert await stores.memos.kept(unit="u") == ()
    head = (await stores.memos.get(unit="u", memo_id=memo.id)).head
    assert head.state == "withdrawn" and head.citations == ("102",)


async def test_another_units_memo_cannot_be_revised_or_withdrawn():
    stores = Stores()
    other = await _seed(stores, unit="elsewhere")
    _, revised = await _one(
        "memo_revise", stores=stores, memo_id=other.id, base_revision_id=other.head.id,
        body=KEPT,
    )
    _, withdrawn = await _one(
        "memo_withdraw", stores=stores, memo_id=other.id, base_revision_id=other.head.id,
        cited_ids=["101"],
    )
    assert revised["reason"] == withdrawn["reason"] == "not_found"
    assert len(await stores.memos.revisions(unit="elsewhere", memo_id=other.id)) == 1


async def test_without_a_memo_store_every_memo_verb_is_unavailable():
    _, answer = await _one(
        "memo_write", runner_kwargs={"memos": None}, title="wifi", body=KEPT,
    )
    assert answer["status"] == "unavailable"


async def test_a_title_carrying_the_kept_value_is_refused_and_a_title_naming_the_thing_is_not():
    """`<memos>` shows titles on every turn, so a title names
    the thing and never the value a Kept line keeps."""
    body = "## Kept\n- the wifi password is hunter2pass [101]\n- the door code is 4721# [102]\n"
    for title in ("wifi: hunter2pass", "door code 4721#"):
        _, answer = await _one("memo_write", title=title, body=body)
        assert answer["status"] == "refused" and answer["reason"] == "title_carries_value"
    for title in ("Wifi password", "Door code"):
        _, answer = await _one("memo_write", title=title, body=body)
        assert answer["status"] == "ok", (title, answer)
    _, deposit = await _one(
        "memo_write", title="Deposit", body="## Kept\n- the deposit is $500, split four [101]\n",
    )
    assert deposit["status"] == "ok"

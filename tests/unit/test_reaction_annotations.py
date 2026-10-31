"""Recorded reaction state on a target, visible everywhere the target is.

First platform-observations slice: the `message_reaction` rows a platform
already records fold once (`fold_reactions`, the definition `record_query`
established) and the result annotates the target in the prompt transcript,
in `load_transcript`/`room_read`/`record_query` output, and in the Cognition
agenda — with the same counts, the newest recorded SOURCE event's time and
the `recorded` coverage. A reaction is never a fake utterance, and unknown
or unread state is never rendered as a complete empty set or a zero.
"""

from __future__ import annotations

from datetime import UTC, datetime

from nora.agent.prefill import render_transcript
from nora.agent.tools import _transcript_messages
from nora.app.cognition_grounding import _record_item
from nora.domain.cognition_ground import RecordItem
from nora.domain.cognition_session import Room
from nora.domain.models import (
    DeliveryStatus,
    EditTarget,
    NormalizedMessage,
    ReactionRow,
    RowReactions,
    Touch,
    TranscriptRow,
    fold_reactions,
)
from nora.domain.record_query import RecordHit, RecordRefHit
from nora.domain.stimuli import Stimulus
from nora.language.cognition_render import _message_row
from nora.testing import FakeRecordStore

NOW = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
NOW_MS = int(NOW.timestamp() * 1000)


def _row(id: int = 1, ts: int = NOW_MS - 9 * 60_000,
         reactions: RowReactions | None = None) -> TranscriptRow:
    return TranscriptRow(
        id=id, sender_id="+852111", sender_name="Miles", ts=ts,
        body="dinner at 8?", is_nora=False,
        delivery_status=DeliveryStatus.SENT, raw_json=None,
        reactions=reactions,
    )


def _touch(*, emoji: str, ts: int, removed: bool = False,
           sender: str = "+852222") -> NormalizedMessage:
    return NormalizedMessage(
        platform="signal", conversation_id="g1", conversation_type="group",
        sender_id=sender, sender_name="Kim", timestamp=ts, body=None, raw={},
        touch=Touch(
            kind="reaction", emoji=emoji, removed=removed,
            target=EditTarget(sent_ts=1),
        ),
    )


# ---- the one fold -----------------------------------------------------------

def test_add_remove_add_is_one_standing_reaction():
    rows = [
        ReactionRow(id=1, message_id=1, sender_id="a", emoji="👍", removed=False, ts=10),
        ReactionRow(id=2, message_id=1, sender_id="a", emoji="👍", removed=True, ts=20),
        ReactionRow(id=3, message_id=1, sender_id="a", emoji="👍", removed=False, ts=30),
    ]
    state = fold_reactions(rows)[1]
    assert state.counts == (("👍", 1),)
    assert state.recorded == 3
    # The newest SOURCE event time, not the read's as-of.
    assert state.observed_at == 30


def test_every_recorded_reaction_taken_back_is_removed_not_zero():
    rows = [
        ReactionRow(id=1, message_id=1, sender_id="a", emoji="👍", removed=False, ts=10),
        ReactionRow(id=2, message_id=1, sender_id="a", emoji="👍", removed=True, ts=20),
    ]
    state = fold_reactions(rows)[1]
    assert state.counts == ()
    assert state.recorded == 2
    assert state.label == "removed"


def test_a_message_with_no_rows_is_absent_from_the_fold():
    state = fold_reactions(
        [ReactionRow(id=1, message_id=1, sender_id="a", emoji="👍", removed=False, ts=10)]
    )
    assert 99 not in state
    assert RowReactions().label is None          # nothing recorded renders nothing


def test_the_fold_is_scoped_per_target_and_actor():
    rows = [
        ReactionRow(id=1, message_id=1, sender_id="a", emoji="👍", removed=False, ts=10),
        ReactionRow(id=2, message_id=2, sender_id="a", emoji="👍", removed=False, ts=10),
        ReactionRow(id=3, message_id=1, sender_id="b", emoji="👍", removed=False, ts=10),
        ReactionRow(id=4, message_id=1, sender_id="a", emoji="❤️", removed=False, ts=10),
    ]
    out = fold_reactions(rows)
    # Per (actor, emoji): message 1 has a+👍, b+👍, a+❤️ -> 👍x2 ❤️x1
    assert out[1].counts == (("❤️", 1), ("👍", 2))
    assert out[2].counts == (("👍", 1),)


# ---- through the store ------------------------------------------------------

async def test_recent_rows_carry_their_reaction_state():
    store = FakeRecordStore()
    mid = await store.append(message=NormalizedMessage(
        platform="signal", conversation_id="g1", conversation_type="group",
        sender_id="+852111", sender_name="Miles", timestamp=1, body="hi", raw={},
    ))
    await store.append_reaction(message_id=mid, message=_touch(emoji="👍", ts=5), person_id="p2")
    await store.append_reaction(
        message_id=mid, message=_touch(emoji="👍", ts=9, sender="+852333"), person_id="p3",
    )
    rows = await store.recent(conversation_id="g1", limit=10)
    assert rows[0].reactions is not None
    assert rows[0].reactions.counts == (("👍", 2),)
    assert rows[0].reactions.observed_at == 9
    assert rows[0].reactions.recorded == 2


async def test_another_conversations_reaction_never_lands_on_the_target():
    store = FakeRecordStore()
    await store.append(message=NormalizedMessage(
        platform="signal", conversation_id="g1", conversation_type="group",
        sender_id="+852111", sender_name="Miles", timestamp=1, body="hi", raw={},
    ))
    other = await store.append(message=NormalizedMessage(
        platform="signal", conversation_id="g2", conversation_type="group",
        sender_id="+852111", sender_name="Miles", timestamp=2, body="hi", raw={},
    ))
    await store.append_reaction(message_id=other, message=_touch(emoji="👍", ts=5), person_id="p2")
    rows = await store.recent(conversation_id="g1", limit=10)
    assert rows[0].reactions is None


# ---- prompt render ----------------------------------------------------------

def test_the_prompt_annotates_counts_provenance_and_coverage():
    out = render_transcript(
        [_row(reactions=RowReactions(
            counts=(("👍", 2), ("❤️", 1)), observed_at=NOW_MS - 20 * 60_000,
            recorded=3,
        ))],
        display_name="Nora", now=NOW,
    )
    assert 'reactions="👍x2 ❤️x1"' in out
    assert 'reactions_observed="recorded 20m ago"' in out


def test_an_unreacted_or_unread_row_carries_no_annotation():
    out = render_transcript([_row()], display_name="Nora", now=NOW)
    assert "reactions" not in out


def test_reactions_never_become_a_fake_utterance():
    out = render_transcript(
        [_row(reactions=RowReactions(counts=(("👍", 1),), observed_at=NOW_MS, recorded=1))],
        display_name="Nora", now=NOW,
    )
    assert out.count("<m ") == 1


# ---- tool render, agreeing with the prompt ----------------------------------

def test_the_tool_result_carries_the_same_state_additively():
    row = _row(reactions=RowReactions(
        counts=(("👍", 2),), observed_at=NOW_MS - 20 * 60_000, recorded=2,
    ))
    items, _ = _transcript_messages([row], now=NOW)
    assert items[0]["reactions"] == {"👍": 2}
    assert items[0]["reactions_source"] == "recorded"
    assert "reactions_at" in items[0]
    # The prompt's label and the tool's dict are the SAME fold's counts.
    prompt = render_transcript([row], display_name="Nora", now=NOW)
    assert 'reactions="👍x2"' in prompt


def test_all_removed_reads_removed_not_an_empty_set():
    row = _row(reactions=RowReactions(
        counts=(), observed_at=NOW_MS - 60_000, recorded=2,
    ))
    items, _ = _transcript_messages([row], now=NOW)
    assert "reactions" not in items[0]
    assert items[0]["reactions_removed"] is True


def test_an_unread_row_emits_no_reaction_keys():
    items, _ = _transcript_messages([_row()], now=NOW)
    assert not any(k.startswith("reactions") for k in items[0])


# ---- Cognition: stimulus change vs current target state ---------------------

def test_a_reaction_item_keeps_its_own_event_and_the_targets_state():
    stimulus = Stimulus(
        id=1, unit="u1", ref="message_reaction:7", kind="message",
        item="reaction", platform="signal", conversation_id="g1",
        person_id="p2", at_ms=NOW_MS - 60_000,
        recorded_at_ms=NOW_MS - 60_000,
    )
    target = _row(reactions=RowReactions(
        counts=(("👍", 3),), observed_at=NOW_MS - 60_000, recorded=4,
    ))
    found = RecordRefHit(
        "message_reaction", 7,
        RecordHit(platform="signal", conversation_id="g1", row=target),
        emoji="👍",
    )
    item = _record_item(
        stimulus, ("message_reaction", 7), found, body="dinner at 8?",
        author="Miles",
    )
    # The stimulus's own change/time is retained…
    assert item.kind == "reaction" and item.emoji == "👍"
    assert item.at_ms == NOW_MS - 60_000
    # …beside the target's CURRENT recorded state, same fold.
    assert item.reactions is not None
    assert item.reactions.counts == (("👍", 3),)


def test_the_agenda_renders_change_and_current_state_distinctly():
    item = RecordItem(
        ref="message_reaction:7", kind="reaction", message_id=42,
        room=Room("signal", "g1"), author="Miles",
        at_ms=NOW_MS - 60_000, text="dinner at 8?", emoji="👍",
        reactions=RowReactions(
            counts=(("👍", 3),), observed_at=NOW_MS - 60_000, recorded=4,
        ),
    )
    row = _message_row(item, (), now_ms=NOW_MS, tz=UTC, message_chars=200)
    out = row.show(None)
    assert 'reaction="👍"' in out               # the stimulus's change
    assert 'reactions="👍x3"' in out            # the target's current state
    assert 'reactions_observed="recorded ' in out


def test_an_item_without_recorded_reactions_renders_no_claim():
    item = RecordItem(
        ref="messages:1", kind="message", message_id=1,
        room=Room("signal", "g1"), author="Miles",
        at_ms=NOW_MS, text="hi",
    )
    out = _message_row(item, (), now_ms=NOW_MS, tz=UTC, message_chars=200).show(None)
    assert "reactions" not in out

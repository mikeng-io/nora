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

import json
from datetime import UTC, datetime
from types import SimpleNamespace

from nora.agent.prefill import render_transcript
from nora.agent.toolkit.transcript import _transcript_messages
from nora.domain.block_types import BLOCK_TYPES
from nora.domain.models import (
    REACTION_PAIRS_MAX,
    DeliveryStatus,
    EditTarget,
    NormalizedMessage,
    ReactionRow,
    RowReactions,
    Touch,
    TranscriptRow,
    display_reactions,
    emoji_display,
    fold_reactions,
    states_from_standing,
)
from nora.infra.sql.audit import reaction_states
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
    items, _ = _transcript_messages([row], now=NOW, display_name="Nora")
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
    items, _ = _transcript_messages([row], now=NOW, display_name="Nora")
    assert "reactions" not in items[0]
    assert items[0]["reactions_removed"] is True


def test_an_unread_row_emits_no_reaction_keys():
    items, _ = _transcript_messages([_row()], now=NOW, display_name="Nora")
    assert not any(k.startswith("reactions") for k in items[0])


# ---- Cognition: stimulus change vs current target state ---------------------







# ---- the read's bounds: SQL reduction and the partial marker --------------

class _Rows:
    """A `conn.execute` answer: `.all()` is all the read asks of it."""

    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _Conn:
    """The `conn` `reaction_states` reads through: answers each execute in
    order and records the compiled SQL, so the read's SHAPE is checkable
    with no Postgres at all."""

    def __init__(self, answers):
        self._answers = list(answers)
        self.seen: list[str] = []

    async def execute(self, stmt):
        from sqlalchemy.dialects import postgresql

        self.seen.append(str(stmt.compile(dialect=postgresql.dialect())))
        return _Rows(self._answers.pop(0))


async def test_the_reaction_read_reduces_in_sql_and_bounds_what_it_transfers(
    monkeypatch,
):
    """The database folds transition history to standing tuples (DISTINCT ON
    by arrival id), the metadata is grouped beside the targets, and the
    standing read is capped: a target the cap reached reads `partial` with
    its REAL recorded count, its counts withheld — never a folded prefix."""
    import nora.infra.sql.audit as audit

    monkeypatch.setattr(audit, "REACTION_STATES_MAX", 3)
    conn = _Conn([[
        SimpleNamespace(tag="m", mid=1, a=4, b=30, c=3, d=0),
        SimpleNamespace(tag="s", mid=1, a=1, b=30, c=100, d=0, e="a", f="👍"),
        SimpleNamespace(tag="s", mid=1, a=2, b=30, c=100, d=0, e="b", f="👍"),
        SimpleNamespace(tag="s", mid=1, a=3, b=30, c=100, d=0, e="c", f="❤️"),
        # The meta branch preserves a target beyond the standing cap.
        SimpleNamespace(tag="m", mid=2, a=2, b=20, c=1, d=0),
    ]])
    states = await reaction_states(conn, [1, 2])
    assert states[1].counts == (("❤️", 1), ("👍", 2))
    assert states[1].recorded == 4 and states[1].observed_at == 30
    assert states[1].coverage == "recorded"
    assert states[2].coverage == "partial"
    assert states[2].counts == ()
    assert states[2].recorded == 2 and states[2].observed_at == 20
    assert len(conn.seen) == 1
    assert "count" in conn.seen[0] and "max" in conn.seen[0]
    assert "DISTINCT ON" in conn.seen[0] and "LIMIT" in conn.seen[0]
    assert "LEFT OUTER JOIN" in conn.seen[0]


async def test_a_target_with_no_rows_stays_absent():
    conn = _Conn([[]])
    assert await reaction_states(conn, [1, 2]) == {}
    conn2 = _Conn([])
    assert await reaction_states(conn2, []) == {}
    assert conn2.seen == []


def test_states_from_standing_never_folds_a_truncated_set():
    """The pure assembly, no SQL: `partial` keeps metadata, withholds counts;
    the latest-only rows still fold add/remove/add to one standing."""
    states = states_from_standing(
        [(1, "👍", False), (1, "❤️", False), (2, "👍", True)],
        recorded={1: 10, 2: 4}, observed={1: 99, 2: 50}, partial={1},
    )
    assert states[1].coverage == "partial" and states[1].counts == ()
    assert states[1].recorded == 10 and states[1].observed_at == 99
    assert states[2].coverage == "recorded" and states[2].counts == ()
    assert states[2].label == "removed"


# ---- the emoji a model may see --------------------------------------------

def test_a_custom_emoji_projects_to_its_scoped_name_never_its_id():
    assert emoji_display("<:wave:1234567890>") == ":wave:"
    assert emoji_display("<a:party:42>") == ":party:"
    assert emoji_display("👍") == "👍"
    # A long or markup-shaped payload never reaches the model raw: a string
    # cut could render a DIFFERENT glyph, so it is a placeholder instead.
    assert emoji_display("x" * 40) == "[emoji]"
    assert emoji_display('"><script>') == "[emoji]"
    assert emoji_display("<:bad name:99>") == "[emoji]"


def test_display_counts_merge_custom_handles_a_model_cannot_tell_apart():
    assert display_reactions(
        (("<:party:1>", 1), ("<a:party:2>", 2), ("👍", 3))
    ) == {":party:": 3, "👍": 3}


def test_the_label_and_prompt_never_carry_a_platform_emoji_id():
    state = RowReactions(
        counts=(("<:wave:1234567890>", 2),), observed_at=NOW_MS, recorded=2,
    )
    assert state.label == ":wave:x2"
    prompt = render_transcript([_row(reactions=state)], display_name="Nora", now=NOW)
    assert ":wave:x2" in prompt and "1234567890" not in prompt


def test_the_tool_dict_carries_display_handles_and_a_bounded_pair_count():
    row = _row(reactions=RowReactions(
        counts=(("<:wave:1234567890>", 2), ("<a:wave:999>", 1)),
        observed_at=NOW_MS, recorded=3,
    ))
    items, _ = _transcript_messages([row], now=NOW, display_name="Nora")
    assert items[0]["reactions"] == {":wave:": 3}
    assert "1234567890" not in json.dumps(items[0])
    flood = _row(reactions=RowReactions(
        counts=tuple((f"e{i}", 1) for i in range(30)),
        observed_at=NOW_MS, recorded=30,
    ))
    items, _ = _transcript_messages([flood], now=NOW, display_name="Nora")
    assert len(items[0]["reactions"]) == REACTION_PAIRS_MAX
    assert items[0]["reactions_more"] == 30 - REACTION_PAIRS_MAX


# ---- the budget: annotation bounded beside the words -----------------------







# ---- partial coverage and the removal bit ---------------------------------

def test_a_partial_state_is_marked_never_read_as_complete():
    state = RowReactions(
        counts=(), observed_at=NOW_MS - 60_000, recorded=9, coverage="partial",
    )
    assert state.label == "partial"
    prompt = render_transcript([_row(reactions=state)], display_name="Nora", now=NOW)
    assert 'reactions="partial"' in prompt
    assert 'reactions_observed="partial ' in prompt
    items, _ = _transcript_messages([_row(reactions=state)], now=NOW, display_name="Nora")
    assert items[0]["reactions_partial"] is True
    assert "reactions" not in items[0]
    assert items[0]["reactions_source"] == "partial"




# ---- what the model is told -----------------------------------------------

def test_the_reaction_fields_are_glossed_where_the_model_reads_them():
    """The coverage explanation lives in the block's field glosses, not in a
    docstring: recorded changes include captured clears/snapshots, upstream
    completeness stays unknown, and partial state is explicit."""
    for name in ("transcript", "agenda"):
        glosses = dict(BLOCK_TYPES[name].annotations)
        assert "reactions" in glosses and "reactions_observed" in glosses
        assert "recorded" in glosses["reactions"]
        assert "upstream" in glosses["reactions"]
        assert "partial" in glosses["reactions_observed"]
    assert "reaction_removed" in dict(BLOCK_TYPES["agenda"].annotations)


async def test_the_tool_result_note_explains_the_counts_are_recorded_only(tmp_path):
    """The coverage reading rides the RESULT, paid only when the keys
    appear: `load_transcript`'s real output, built by `build_tools`, carries
    the note beside the annotation — and no note on a reaction-free read."""
    from nora.agent.room import Room as AgentRoom
    from nora.agent.tools import build_tools
    from nora.infra.transit import TransitClient
    from nora.obs.metrics import Metrics
    from nora.persons import load_persons
    from tests.fakes import SinkSender

    (tmp_path / "identity.toml").write_text("")
    persons = load_persons(tmp_path / "identity.toml")
    store = FakeRecordStore()
    mid = await store.append(message=NormalizedMessage(
        platform="signal", conversation_id="g1", conversation_type="group",
        sender_id="+852111", sender_name="Miles", timestamp=1, body="hi", raw={},
    ))
    await store.append_reaction(
        message_id=mid, message=_touch(emoji="👍", ts=5), person_id="p2",
    )
    tools = {
        t.tool_name: t for t in build_tools(
            store=store, conversation_id="g1", conversation_type="group",
            persons=persons, deliverer=SinkSender(),
            metrics=Metrics(),
            room=AgentRoom(
                platform="signal", conversation_id="g1",
                roster_source=None, persons=persons,
            ),
            transit_source=TransitClient(
                kmb_base_url="http://127.0.0.1:1",
                mtr_schedule_url="http://127.0.0.1:1/s",
                mtr_routes_url="http://127.0.0.1:1/r.csv",
                timeout_seconds=0.5,
            ),
            clock=lambda: NOW,
        )
    }
    events = [e async for e in tools["load_transcript"].stream(
        {"toolUseId": "t1", "name": "load_transcript", "input": {}},
        {"agent": None},
    )]
    answer = json.loads(events[-1].tool_result["content"][0]["text"])
    assert answer["messages"][0]["reactions"] == {"👍": 1}
    assert "recorded changes" in answer["note"]
    assert "captured clears and snapshots" in answer["note"]
    assert "not the complete upstream set" in answer["note"]

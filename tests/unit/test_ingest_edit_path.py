"""The edit path through `IngestPipeline`.

Every test here drives the REAL pipeline over `FakeRecordStore`, which the
contract suite binds to `SqlRecordStore`, so what is asserted is the ingest
behaviour and not a stand-in for it. The outcome VOCABULARY is pinned in
`test_ingest_pipeline.py`; this file pins what each outcome MEANS.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from nora.domain.models import EditTarget, NormalizedMessage, PersonRef, Quote
from nora.domain.ports import EngageDecision, MemoryHealth
from nora.ingest.pipeline import IngestPipeline
from nora.ingest.policy import CompositePolicy, MentionPolicy, TriggerTextPolicy
from nora.obs.metrics import Metrics
from nora.persons import Persons
from nora.policy import permissive_policy
from nora.testing import FakeRecordStore

NORA = "+852999"
TRIGGER = "nora.000"
#: The Signal address of the message `_msg` sends by default: its sent ts.
ORIGINAL = EditTarget(sent_ts=1)


class RecordingMemory:
    """Records what Honcho would be fed, text and metadata included."""

    def __init__(self) -> None:
        self.fed: list[dict[str, Any]] = []

    async def ensure_workspace(self) -> None:
        return None

    async def ensure_peer(self, *, peer_id: str, **_route: str | None) -> None:
        return None

    async def feed(self, *, peer_id: str, text: str, conversation_id: str = "",
                   metadata: dict | None = None) -> bool:
        self.fed.append({"peer_id": peer_id, "text": text, "metadata": metadata})
        return True

    async def peer_card(self, *, peer_id: str, **_route: str | None) -> list[str]:
        return []

    async def health(self) -> MemoryHealth:
        return MemoryHealth(reachable=True)

    async def aclose(self) -> None:
        return None


def _persons() -> Persons:
    """Miles has TWO accounts on one platform — the WhatsApp phone/LID shape."""
    return Persons(
        by_person={"miles": PersonRef(person_id="miles", name="Miles", honcho_peer_id="miles")},
        index={("signal", "+852111"): "miles", ("signal", "lid:111"): "miles"},
    )


def _msg(body: str, *, ts: int = 1, sender: str = "+852111",
         mentions: frozenset[str] = frozenset(), message_id: str = "") -> NormalizedMessage:
    return NormalizedMessage(
        platform="signal", conversation_id="g1", conversation_type="group",
        sender_id=sender, sender_name="Miles", timestamp=ts, body=body, raw={"v": body},
        mentions=mentions, message_id=message_id,
    )


def _edit(body: str, *, sender: str = "+852111", ts: int = 5,
          mentions: frozenset[str] = frozenset(),
          target: EditTarget = ORIGINAL) -> NormalizedMessage:
    return dataclasses.replace(
        _msg(body, ts=ts, sender=sender, mentions=mentions), edit_of=target,
    )


def _pipeline(store: FakeRecordStore, *, memory: RecordingMemory | None = None,
              policy: Any = None, metrics: Metrics | None = None,
              spawned: list | None = None, **kwargs: Any) -> IngestPipeline:
    def _spawn(coro):
        # Kept for `_drain` when a test asks to observe the feed; closed
        # otherwise, as the sibling suite's `lambda c: c.close()` does.
        if spawned is not None:
            spawned.append(coro)
        else:
            coro.close()

    return IngestPipeline(
        audit=store,
        memory=memory or RecordingMemory(),
        persons=_persons(),
        policy=policy or CompositePolicy([
            MentionPolicy(frozenset({NORA})), TriggerTextPolicy(TRIGGER),
        ]),
        allowlist=permissive_policy(),
        metrics=metrics or Metrics(),
        spawn=_spawn,
        **kwargs,
    )


async def _drain(spawned: list) -> None:
    for coro in spawned:
        await coro
    spawned.clear()


# ── where it lands ───────────────────────────────────────────────────────────


async def test_an_edit_is_recorded_beside_its_original_not_as_a_new_message():
    store = FakeRecordStore()
    await store.append(message=_msg("dinner at 7"), person_id="miles")
    result = await _pipeline(store).run(message=_edit("dinner at 8"))

    assert len(store.rows) == 1, "an edit is not a second message"
    assert [e["body"] for e in store.edits] == ["dinner at 8"]
    rows = await store.recent(conversation_id="g1", limit=10)
    assert [r.body for r in rows] == ["dinner at 8"]
    assert result.decision is not None and not result.decision.engage


async def test_an_unchanged_edit_opens_no_turn_and_feeds_nothing_but_keeps_the_chain():
    """Same text and mentions (a style-only Signal edit): no correction for
    Honcho, no turn — but the row IS written when it is newer, because the
    sender's next edit names THIS edit's timestamp, and leaving it unrecorded
    would make every later edit an orphan."""
    store = FakeRecordStore()
    memory = RecordingMemory()
    spawned: list = []
    metrics = Metrics()
    await store.append(
        message=_msg("@nora hi", mentions=frozenset({NORA})), person_id="miles",
    )
    pipe = _pipeline(store, memory=memory, spawned=spawned, metrics=metrics)
    style_only = await pipe.run(message=_edit("@nora hi", ts=5, mentions=frozenset({NORA})))
    await _drain(spawned)
    assert style_only.decision is None
    assert memory.fed == []
    assert [e["ts"] for e in store.edits] == [5]
    assert metrics.get("nora_ingest_outcomes_total", outcome="edit_unchanged") == 1

    # ...so the NEXT edit, naming that revision, still finds the message.
    await pipe.run(message=_edit(
        "@nora hi there", ts=9, mentions=frozenset({NORA}), target=EditTarget(sent_ts=5),
    ))
    assert [e["body"] for e in store.edits] == ["@nora hi", "@nora hi there"]


async def test_an_exact_redelivery_writes_nothing():
    """The same time and the same text as the version on record — a bridge
    reconnect, a Discord pin of an edited message."""
    store = FakeRecordStore()
    metrics = Metrics()
    await store.append(message=_msg("dinner at 7"), person_id="miles")
    pipe = _pipeline(store, metrics=metrics)
    await pipe.run(message=_edit("dinner at 8", ts=5))
    await pipe.run(message=_edit("dinner at 8", ts=5))
    assert [e["body"] for e in store.edits] == ["dinner at 8"]
    assert metrics.get("nora_ingest_outcomes_total", outcome="edit_stale") == 1


async def test_a_mention_only_change_is_a_change():
    """In a Signal body every mention is one placeholder character, so
    «@Alice hi» → «@Nora hi» is identical as TEXT. It is still an edit — and
    here one that newly addresses her."""
    store = FakeRecordStore()
    await store.append(
        message=_msg("\ufffc hi", mentions=frozenset({"+852000"})), person_id="miles",
    )
    result = await _pipeline(store).run(
        message=_edit("\ufffc hi", mentions=frozenset({NORA})),
    )
    assert [e["body"] for e in store.edits] == ["\ufffc hi"]
    assert result.decision is not None and result.decision.engage


async def test_an_edit_whose_timestamp_is_on_record_is_a_redelivery():
    """On Signal an edit's timestamp IS its identity; on every network a
    repeated one is a redelivery. Recording it again would flip the fold back to
    older text."""
    store = FakeRecordStore()
    metrics = Metrics()
    await store.append(message=_msg("v0", message_id="m1"), person_id="miles")
    pipe = _pipeline(store, metrics=metrics)
    await pipe.run(message=_edit("v1", ts=5000, target=EditTarget(message_id="m1")))
    await pipe.run(message=_edit("v2", ts=6000, target=EditTarget(message_id="m1")))
    await pipe.run(message=_edit("v1", ts=5000, target=EditTarget(message_id="m1")))
    rows = await store.recent(conversation_id="g1", limit=5)
    assert rows[0].body == "v2"
    assert metrics.get("nora_ingest_outcomes_total", outcome="edit_stale") == 1


async def test_a_skewed_clock_edit_cannot_let_a_redelivery_bring_back_old_text():
    """Phone edits at 1000 (v1); a desktop 200 ms behind edits v1 at 800
    (v2, admitted by the waiver). Signal then redelivers the phone's edit.
    Judged against the version SHOWN (800) it would look newer and resurrect
    v1, fed to Honcho as a correction. Judged against the newest clock on
    record, and by its timestamp already being there, it is stale."""
    store = FakeRecordStore()
    memory = RecordingMemory()
    spawned: list = []
    await store.append(message=_msg("v0", ts=100), person_id="miles")
    pipe = _pipeline(store, memory=memory, spawned=spawned)
    await pipe.run(message=_edit("v1", ts=1000, target=EditTarget(sent_ts=100)))
    await pipe.run(message=_edit("v2", ts=800, target=EditTarget(sent_ts=1000)))
    await _drain(spawned)
    fed_before = len(memory.fed)
    await pipe.run(message=_edit("v1", ts=1000, target=EditTarget(sent_ts=100)))
    await _drain(spawned)
    rows = await store.recent(conversation_id="g1", limit=5)
    assert rows[0].body == "v2"
    assert len(memory.fed) == fed_before, "a redelivery must not be fed as a correction"


async def test_a_quote_on_another_platform_is_never_re_pointed():
    """Only Signal quotes a revision. Elsewhere a quote carries its parent's
    own timestamp, so a reply to a message Nora never recorded whose
    timestamp collides with some edit must keep its pointer."""
    store = FakeRecordStore()
    wa = {"platform": "whatsapp"}
    await store.append(
        message=dataclasses.replace(_msg("alice", ts=100, message_id="m1"), **wa),
        person_id="miles",
    )
    await _pipeline(store).run(message=dataclasses.replace(
        _edit("alice fixed", ts=7000, target=EditTarget(message_id="m1")), **wa,
    ))
    reply = dataclasses.replace(
        _msg("yes", ts=8000), quote=Quote(author_id="x", text="", quoted_ts=7000), **wa,
    )
    await _pipeline(store).run(message=reply)
    assert store.rows[-1]["quoted_ts"] == 7000


async def test_a_signal_edit_naming_the_latest_revision_wins_even_from_a_slow_clock():
    """Each Signal device stamps its own sends. An edit from a linked desktop
    whose clock is behind names the latest revision by ITS timestamp, so it
    is newer by definition — and it becomes the version shown."""
    store = FakeRecordStore()
    await store.append(message=_msg("v1"), person_id="miles")
    pipe = _pipeline(store)
    await pipe.run(message=_edit("v2", ts=100))
    await pipe.run(message=_edit("v3", ts=50, target=EditTarget(sent_ts=100)))
    rows = await store.recent(conversation_id="g1", limit=5)
    assert rows[0].body == "v3"


async def test_an_edit_with_no_time_is_stamped_on_arrival():
    """Zero would render no `edited` marker and lose to every other edit."""
    store = FakeRecordStore()
    await store.append(message=_msg("v1"), person_id="miles")
    await _pipeline(store).run(message=_edit("v2", ts=0))
    rows = await store.recent(conversation_id="g1", limit=5)
    assert rows[0].edit is not None and rows[0].edit.ts > 1_700_000_000_000


async def test_signal_finds_the_message_when_the_sender_id_form_changed():
    """signal-cli can name one sender by phone on the message and by ACI on
    the edit. The lookup prefers the same id but does not require it;
    authorship is then decided by person."""
    store = FakeRecordStore()
    await store.append(message=_msg("v1"), person_id="miles")
    await _pipeline(store).run(message=_edit("v2", sender="lid:111"))
    assert [e["body"] for e in store.edits] == ["v2"]


async def test_a_reply_quoting_an_edits_timestamp_points_at_the_message():
    """Signal quotes the REVISION the replier saw. The transcript keeps the
    edited message at its original ts, so the pointer is rewritten once, at
    ingest, and the reply resolves its parent."""
    store = FakeRecordStore()
    await store.append(message=_msg("dinner at 7", ts=1), person_id="miles")
    await _pipeline(store).run(message=_edit("dinner at 8", ts=5))
    reply = dataclasses.replace(
        _msg("ok", ts=9, sender="+852000"),
        quote=Quote(author_id="+852111", text="dinner at 8", quoted_ts=5),
    )
    await _pipeline(store).run(message=reply)
    assert store.rows[-1]["quoted_ts"] == 1


async def test_an_orphan_is_dropped_even_when_the_platform_names_its_sender():
    """An edit of something never recorded — pruned, from before Nora joined,
    or a Discord update racing its own message. Ingesting it would put an old
    message at the bottom of the transcript as if said now, and a tag in it
    would open a turn on something long settled."""
    store = FakeRecordStore()
    metrics = Metrics()
    result = await _pipeline(store, metrics=metrics).run(
        message=_edit(f"{TRIGGER} dinner at 8"),
    )
    assert store.rows == [] and store.edits == []
    assert result.message is None and result.decision is None
    assert metrics.get("nora_ingest_outcomes_total", outcome="edit_orphaned") == 1


async def test_an_unattributed_orphan_is_dropped():
    store = FakeRecordStore()
    metrics = Metrics()
    result = await _pipeline(store, metrics=metrics).run(message=_edit("x", sender=""))
    assert store.rows == [] and store.edits == []
    assert result.message is None
    assert metrics.get("nora_ingest_outcomes_total", outcome="edit_orphaned") == 1


async def test_a_redelivered_or_out_of_order_edit_is_stale():
    """The transcript already shows the later text; recording the older one
    would feed Honcho a stale version as the latest correction."""
    store = FakeRecordStore()
    memory = RecordingMemory()
    spawned: list = []
    metrics = Metrics()
    await store.append(message=_msg("dinner at 7"), person_id="miles")
    pipe = _pipeline(store, memory=memory, spawned=spawned, metrics=metrics)
    await pipe.run(message=_edit("dinner at 9", ts=9))
    await pipe.run(message=_edit("dinner at 8", ts=5))
    await _drain(spawned)
    assert [e["body"] for e in store.edits] == ["dinner at 9"]
    assert len(memory.fed) == 1
    assert metrics.get("nora_ingest_outcomes_total", outcome="edit_stale") == 1


# ── whose words ──────────────────────────────────────────────────────────────


async def test_an_edit_naming_no_sender_takes_the_originals():
    """Instagram. Identity — and so the Honcho peer — comes from the message
    the edit changes."""
    store = FakeRecordStore()
    memory = RecordingMemory()
    spawned: list = []
    await store.append(message=_msg("dinner at 7", message_id="mid.$m"), person_id="miles")
    await _pipeline(store, memory=memory, spawned=spawned).run(
        message=_edit("dinner at 8", sender="", target=EditTarget(message_id="mid.$m")),
    )
    await _drain(spawned)
    assert [e["body"] for e in store.edits] == ["dinner at 8"]
    assert [f["peer_id"] for f in memory.fed] == ["miles"]


async def test_somebody_elses_edit_by_handle_is_refused():
    """A handle names a message regardless of who sent it, so the author has
    to be checked: an edit is not a way to put words under someone else's
    row."""
    store = FakeRecordStore()
    metrics = Metrics()
    await store.append(message=_msg("dinner at 7", message_id="h1"), person_id="miles")
    await _pipeline(store, metrics=metrics).run(
        message=_edit("x", sender="+852000", target=EditTarget(message_id="h1")),
    )
    assert store.edits == []
    assert metrics.get("nora_ingest_outcomes_total", outcome="edit_refused") == 1


async def test_on_signal_somebody_elses_edit_cannot_reach_the_row_at_all():
    """Signal addresses a message by (author, sent-timestamp), so another
    sender's edit naming Miles's timestamp finds nothing of theirs — an orphan,
    dropped. Miles's row is untouched, which is the property the refusal is
    for."""
    store = FakeRecordStore()
    await store.append(message=_msg("dinner at 7"), person_id="miles")
    await _pipeline(store).run(message=_edit("x", sender="+852000"))
    assert store.edits == []
    assert [(r["sender_id"], r["body"]) for r in store.rows] == [("+852111", "dinner at 7")]


async def test_the_same_person_under_their_other_account_form_may_edit():
    """WhatsApp can deliver the original under a phone JID and the edit under
    a LID. Both map to one person, so it is their own message."""
    store = FakeRecordStore()
    await store.append(message=_msg("dinner at 7"), person_id="miles")
    # The Signal pair lookup is keyed on the sender, so address by handle here.
    store.rows[0]["platform_message_id"] = "h1"
    await _pipeline(store).run(
        message=_edit("dinner at 8", sender="lid:111", target=EditTarget(message_id="h1")),
    )
    assert [e["body"] for e in store.edits] == ["dinner at 8"]


async def test_an_edit_of_noras_own_message_is_refused():
    store = FakeRecordStore()
    metrics = Metrics()
    await store.append(message=_msg("hello", message_id="h1"), is_nora=True)
    await _pipeline(store, metrics=metrics).run(
        message=_edit("x", sender="", target=EditTarget(message_id="h1")),
    )
    assert store.edits == []
    assert metrics.get("nora_ingest_outcomes_total", outcome="edit_refused") == 1


# ── what Honcho hears ────────────────────────────────────────────────────────


async def test_honcho_is_fed_a_correction_naming_what_it_first_said():
    store = FakeRecordStore()
    memory = RecordingMemory()
    spawned: list = []
    await store.append(message=_msg("dinner at 7"), person_id="miles")
    await _pipeline(store, memory=memory, spawned=spawned).run(
        message=_edit("dinner at 8", ts=5),
    )
    await _drain(spawned)
    assert memory.fed == [{
        "peer_id": "miles",
        "text": "[edited an earlier message; it first said: «dinner at 7»] dinner at 8",
        "metadata": {"ts": 5, "edit_of_ts": 1},
    }]


# ── whether it opens a turn ──────────────────────────────────────────────────


async def test_an_edit_that_adds_a_mention_of_nora_opens_a_turn():
    store = FakeRecordStore()
    metrics = Metrics()
    await store.append(message=_msg("dinner?"), person_id="miles")
    result = await _pipeline(store, metrics=metrics).run(
        message=_edit("@nora dinner?", mentions=frozenset({NORA})),
    )
    assert result.decision is not None and result.decision.engage
    assert metrics.get("nora_ingest_outcomes_total", outcome="edit_engaged") == 1


async def test_an_edit_that_adds_the_trigger_text_opens_a_turn():
    store = FakeRecordStore()
    await store.append(message=_msg("dinner?"), person_id="miles")
    result = await _pipeline(store).run(message=_edit(f"{TRIGGER} dinner?"))
    assert result.decision is not None and result.decision.engage


async def test_fixing_a_typo_in_a_message_that_tagged_her_does_not_answer_again():
    """She has answered it or is answering it, so a typo fix does not open a turn."""
    store = FakeRecordStore()
    await store.append(
        message=_msg("@nora dinnr?", mentions=frozenset({NORA})), person_id="miles",
    )
    result = await _pipeline(store).run(
        message=_edit("@nora dinner?", mentions=frozenset({NORA})),
    )
    assert result.decision == EngageDecision(engage=False, reason="edit_already_addressed")
    assert [e["body"] for e in store.edits] == ["@nora dinner?"], "still recorded"


async def test_adding_a_mention_to_a_reply_she_already_answered_does_not_answer_again():
    """A reply to Nora was addressed to her in every version. The edit event
    may not repeat the quote (Instagram's never does), so the fact is read off
    the RECORDED message, not the edit."""
    store = FakeRecordStore()
    await store.append(message=_msg("hello", ts=1, sender=NORA), is_nora=True)
    await store.append(
        message=dataclasses.replace(
            _msg("what about dinner?", ts=2),
            quote=Quote(author_id=NORA, text="hello", quoted_ts=1),
        ),
        person_id="miles",
    )
    result = await _pipeline(store).run(message=_edit(
        "@nora what about dinner?", mentions=frozenset({NORA}),
        target=EditTarget(sent_ts=2),
    ))
    assert result.decision == EngageDecision(engage=False, reason="edit_already_addressed")


async def test_an_edit_that_cannot_see_mentions_records_unknown_and_is_judged_honestly():
    """Instagram's edit delta has no mentions. The gate borrows the original's
    to compare against, but the edit row records «unknown»: nobody observed
    what this version mentions. So the NEXT edit refuses rather than being
    judged against a guess (R-5)."""
    store = FakeRecordStore()
    await store.append(
        message=_msg("@nora dinnr?", message_id="m1", mentions=frozenset({NORA})),
        person_id="miles",
    )
    blind = EditTarget(message_id="m1", mentions_known=False)
    first = await _pipeline(store).run(message=_edit("@nora dinner?", sender="", target=blind))
    assert store.edits[0]["mentions_json"] is None
    second = await _pipeline(store).run(message=_edit(
        f"{TRIGGER} dinner??", sender="", ts=9, target=blind,
    ))
    assert first.decision is not None and not first.decision.engage
    assert second.decision == EngageDecision(engage=False, reason="edit_previous_unknown")


async def test_an_edit_that_cannot_see_mentions_of_an_unknown_original_records_unknown():
    store = FakeRecordStore()
    await store.append(message=_msg("dinner?", message_id="m1"), person_id="miles")
    store.rows[0]["mentions_json"] = None
    await _pipeline(store).run(message=_edit(
        "dinner at 8?", sender="", target=EditTarget(message_id="m1", mentions_known=False),
    ))
    assert store.edits[0]["mentions_json"] is None


async def test_the_comparison_is_against_the_version_the_edit_replaced():
    """v1 untagged, v2 adds the tag (a turn), v3 fixes a typo in v2 (no second
    turn). Against the FIRST version, v3 would look newly tagged too."""
    store = FakeRecordStore()
    await store.append(message=_msg("dinner?"), person_id="miles")
    second = await _pipeline(store).run(
        message=_edit("@nora dinnr?", ts=5, mentions=frozenset({NORA})),
    )
    third = await _pipeline(store).run(
        message=_edit("@nora dinner?", ts=9, mentions=frozenset({NORA})),
    )
    assert second.decision is not None and second.decision.engage
    assert third.decision == EngageDecision(engage=False, reason="edit_already_addressed")


async def test_an_edit_of_a_message_whose_mentions_are_unknown_does_not_open_a_turn():
    """A row stored before `mentions_json` existed. The second question cannot
    be asked honestly, and R-5 says silence."""
    store = FakeRecordStore()
    await store.append(message=_msg("dinner?"), person_id="miles")
    store.rows[0]["mentions_json"] = None
    result = await _pipeline(store).run(
        message=_edit("@nora dinner?", mentions=frozenset({NORA})),
    )
    assert result.decision == EngageDecision(engage=False, reason="edit_previous_unknown")


async def test_a_turn_an_edit_opens_is_answered_as_the_original_message():
    """The 👀 reaction and a threaded reply must point at the message the room
    sees — its timestamp and its handle — never at the edit."""
    store = FakeRecordStore()
    await store.append(message=_msg("dinner?", ts=1, message_id="orig"), person_id="miles")
    result = await _pipeline(store).run(message=dataclasses.replace(
        _edit("@nora dinner?", ts=5, mentions=frozenset({NORA}),
              target=EditTarget(message_id="orig")),
        message_id="the-edit-stanza",
    ))
    assert result.message is not None
    assert (result.message.timestamp, result.message.message_id) == (1, "orig")
    assert result.message.body == "@nora dinner?"


async def test_an_edit_is_never_a_relevance_gate_candidate():
    """It is not new room activity; the quiescence trigger counts messages."""
    import tempfile
    from pathlib import Path

    from nora.ingest.policy import RelevanceGatePolicy
    from nora.policy import load_policy

    p = Path(tempfile.mkdtemp()) / "policy.toml"
    p.write_text(
        'default = "deny"\n[[conversations]]\nplatform = "signal"\n'
        'id = "g1"\nlabel = "g1"\npersons = "*"\nrelevance_gate = true\n'
    )
    opted_in = RelevanceGatePolicy(load_policy(p))
    store = FakeRecordStore()
    plain = await _pipeline(store, policy=opted_in).run(message=_msg("dinner?"))
    assert plain.decision is not None and plain.decision.relevance_gate, \
        "positive control: a MESSAGE in this room is a candidate"
    result = await _pipeline(store, policy=opted_in).run(
        message=_edit("dinner at 8?"),
    )
    assert result.decision is not None
    assert not result.decision.engage and not result.decision.relevance_gate


async def test_an_edited_caption_keeps_the_attachment_handle_without_refetching():
    """No network lets an edit ADD media, so an edit with as many attachments
    as the original holds gets the original's handles back — nothing is
    downloaded. Re-running custody would re-fetch on every update, and a failed
    re-fetch would leave `[image]` where the record said `[image a3f2c1]`:
    compared as a change, and the handle would vanish."""
    from nora.domain.media import MediaBlob, MediaRef, label_for, render_placeholder

    ref = MediaRef(platform_id="att1", media_type="image/png")
    blob = MediaBlob(sha256="a3f2c1" + "0" * 58, media_type="image/png", size=10)
    held = f"look {render_placeholder(label_for(ref), blob.handle)}"
    store = FakeRecordStore()
    await store.append(
        message=dataclasses.replace(_msg(held), media=(ref,), media_blobs=(blob,)),
        person_id="miles",
    )
    edit = dataclasses.replace(
        _edit(f"look here {render_placeholder(label_for(ref))}"), media=(ref,),
    )
    # No media sources wired: custody, had it run, could only leave a bare
    # placeholder — so a handle in the fold proves it was carried, not fetched.
    await _pipeline(store).run(message=edit)
    rows = await store.recent(conversation_id="g1", limit=5)
    assert rows[0].body == f"look here {render_placeholder(label_for(ref), blob.handle)}"


async def test_handles_are_not_reused_when_the_original_did_not_hold_every_attachment():
    """`media_json` lists only held blobs — a failed download is a gap — so
    it is positional only when every placeholder in the original carried a
    handle. Otherwise a removed attachment could hand its neighbour's handle
    to the wrong placeholder; custody runs instead."""
    from nora.domain.media import MediaBlob, MediaRef, label_for, render_placeholder

    a = MediaRef(platform_id="a", media_type="image/png")
    b = MediaRef(platform_id="b", media_type="image/png")
    blob_b = MediaBlob(sha256="b" * 64, media_type="image/png", size=1)
    bare_a = render_placeholder(label_for(a))
    original_body = f"pics {bare_a} {render_placeholder(label_for(b), blob_b.handle)}"
    store = FakeRecordStore()
    await store.append(
        message=dataclasses.replace(_msg(original_body), media=(a, b), media_blobs=(blob_b,)),
        person_id="miles",
    )
    edit = dataclasses.replace(_edit(f"pics {render_placeholder(label_for(a))}"), media=(a,))
    await _pipeline(store).run(message=edit)
    rows = await store.recent(conversation_id="g1", limit=5)
    assert blob_b.handle not in (rows[0].body or ""), "b's handle given to a's placeholder"


async def test_a_redelivery_at_the_newest_timestamp_is_caught_by_its_timestamp():
    """Not older than the newest clock — it IS the newest clock — but already
    on record, so a redelivery: a different text at a timestamp Nora holds
    must not flip the fold."""
    store = FakeRecordStore()
    await store.append(message=_msg("v0", message_id="m1"), person_id="miles")
    pipe = _pipeline(store)
    await pipe.run(message=_edit("v1", ts=5000, target=EditTarget(message_id="m1")))
    await pipe.run(message=_edit("v2", ts=6000, target=EditTarget(message_id="m1")))
    await pipe.run(message=_edit("v1 again", ts=6000, target=EditTarget(message_id="m1")))
    rows = await store.recent(conversation_id="g1", limit=5)
    assert rows[0].body == "v2"


async def test_an_out_of_order_edit_is_judged_against_the_newest_clock_not_the_shown_one():
    """After a skewed desktop edit (800) of a phone edit (1000), an older
    edit at 900 that names the ORIGINAL is out of order: older than the
    newest clock on record, though newer than the version shown."""
    store = FakeRecordStore()
    await store.append(message=_msg("v0", ts=100), person_id="miles")
    pipe = _pipeline(store)
    await pipe.run(message=_edit("v1", ts=1000, target=EditTarget(sent_ts=100)))
    await pipe.run(message=_edit("v2", ts=800, target=EditTarget(sent_ts=1000)))
    await pipe.run(message=_edit("stale", ts=900, target=EditTarget(sent_ts=100)))
    rows = await store.recent(conversation_id="g1", limit=5)
    assert rows[0].body == "v2"

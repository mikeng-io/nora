"""The person Profile session's social input (design/unified-memory/45 §4):
the oldest `SOCIAL_ITEMS_MAX` items past the key's own social cursor, handed
once (the cursor moves on `settle`, never on a failed run), rendered as
`<social_items>` with a post's or reel's latest counts, and citable as
`social:<network>:<item id>` — evidence about its author alone. No database:
`_Reads` and `_PersonSocial` are this module's own private Protocols, faked
inline; the culture read is the contract-bound `InMemoryCultureStore`.
"""

from __future__ import annotations

from datetime import UTC, datetime

from nora.domain.models import PersonRef
from nora.domain.profile import Said
from nora.domain.social_stream import PersonSocialItem, RecordedCounts
from nora.language.character import SOCIAL_ITEMS_MAX, PersonKey
from nora.language.profile import ProfileEvidence, ProfilePersonAdaptor
from nora.testing import InMemoryCultureStore

T0 = 1_760_000_000_000  # ms
NOW = datetime.fromtimestamp((T0 + 3_600_000) / 1000, tz=UTC)

KEY = "character:unit:ada"
PK = PersonKey(unit="unit", rooms=(), home="room-a", listed=("room-a",),
               person=PersonRef(person_id="ada", name="Ada", honcho_peer_id="ada"))


def _item(item_id: str, *, us: int) -> PersonSocialItem:
    return PersonSocialItem(
        network="instagram", item_id=item_id, kind="post", collab=False,
        taken_at=datetime.fromtimestamp(us / 1_000_000, tz=UTC), origin="poll",
        caption="lunch", recorded_us=us,
    )


class _ReadsFake:
    def __init__(self) -> None:
        self.cursors: dict[tuple[str, str], tuple[int, int]] = {}

    async def history_state(self, *, key):
        return 0, 0

    async def person_count(self, *, rooms, person_id, after) -> int:
        return 0

    async def person_since(self, *, rooms, person_id, after, limit):
        return [], after

    async def mentions_since(self, *, rooms, person_id, after, limit):
        return [], after

    async def episodes(self, *, unit, after, limit, evidence_only=False):
        return [], after

    async def rows_by_id(self, *, ids):
        return []

    async def tie_totals(self, *, unit, person_id):
        return []

    async def social_cursor(self, *, adaptor, key):
        return self.cursors.get((adaptor, key), (0, 0))

    async def advance_social_cursor(self, *, adaptor, key, cursor, read_at):
        held, at = self.cursors.get((adaptor, key), (0, 0))
        self.cursors[(adaptor, key)] = (max(held, cursor), max(at, read_at))


class _ProfileFake:
    async def account_state(self, *, key):
        return 0, T0  # settled once: the Character head is not handed

    async def account_heads(self, *, key):
        return ()

    async def pending_told(self, *, key):
        return 0

    async def pending_disputes(self, *, key):
        return 0

    async def open_disputes(self, *, key, limit):
        return ()


class _SocialFake:
    def __init__(self, items: list[PersonSocialItem], *, polled: int = 0) -> None:
        self._items = items
        self._polled = polled
        self.counts_asked: list[tuple[str, str]] = []

    async def polled_count(self, *, persons, after_us) -> int:
        return self._polled

    async def now(self) -> datetime:
        return NOW

    async def person_items(self, *, persons, after_us, limit):
        return [i for i in self._items if i.recorded_us > after_us][:limit]

    async def current_counts(self, *, items):
        self.counts_asked = list(items)
        return {
            item: RecordedCounts(likes=12, comments=3, reposts=None,
                                 likes_hidden=False, read_at=NOW)
            for item in items
        }


def _evidence(store: _ReadsFake, social: _SocialFake | None) -> ProfileEvidence:
    return ProfileEvidence(store=store, profile=_ProfileFake(), keys={KEY: PK},
                           wake_batch=100, social=social)


async def test_polled_social_items_count_toward_the_wake_capped() -> None:
    evidence = _evidence(_ReadsFake(), _SocialFake([], polled=SOCIAL_ITEMS_MAX * 3))
    assert await evidence.count(key=KEY, after=0) == SOCIAL_ITEMS_MAX


async def test_items_are_handed_once_and_the_cursor_moves_on_settle() -> None:
    store = _ReadsFake()
    evidence = _evidence(store, _SocialFake([_item("a", us=10), _item("b", us=20)]))
    batch, _ = await evidence.newest(key=KEY, after=0, limit=50)
    assert [i.item_id for i in batch if isinstance(i, PersonSocialItem)] == ["a", "b"]
    evidence.handed(KEY, [i for i in batch if isinstance(i, PersonSocialItem)])
    await evidence.settle(key=KEY)
    assert store.cursors[("profile-person", KEY)][0] == 20
    # The next wake reads past them.
    batch, _ = await evidence.newest(key=KEY, after=0, limit=50)
    assert not [i for i in batch if isinstance(i, PersonSocialItem)]


async def test_a_failed_run_hands_the_same_items_again() -> None:
    store = _ReadsFake()
    evidence = _evidence(store, _SocialFake([_item("a", us=10)]))
    await evidence.newest(key=KEY, after=0, limit=50)
    evidence.forget(KEY)
    await evidence.settle(key=KEY)
    assert store.cursors == {}
    batch, _ = await evidence.newest(key=KEY, after=0, limit=50)
    assert [i.item_id for i in batch if isinstance(i, PersonSocialItem)] == ["a"]


async def test_inputs_renders_the_items_with_counts_and_makes_them_citable() -> None:
    item = _item("abc", us=10)
    social = _SocialFake([item])
    store = _ReadsFake()
    evidence = _evidence(store, social)
    adaptor = ProfilePersonAdaptor(
        profile=_ProfileFake(), culture=InMemoryCultureStore(), evidence=evidence,
        reads=store, keys={KEY: PK},
        name_of=lambda pid: "someone", clock_ms=lambda: T0 + 3_600_000,
        clock=lambda: NOW, social=social, screen=lambda _t: None,
    )
    batch, _ = await evidence.newest(key=KEY, after=0, limit=50)
    rendered = await adaptor.inputs(key=KEY, batch=batch)
    assert "<social_items " in rendered
    assert 'cite="social:instagram:abc"' in rendered
    assert 'likes="12"' in rendered and 'comments="3"' in rendered
    assert 'reposts="' not in rendered  # unknown, never 0
    assert social.counts_asked == [("instagram", "abc")]
    # The item the block carried is what the session may cite, on the item's
    # own day, about its author, in no room.
    run = adaptor._runs[KEY]
    assert run.citable.social == {
        "instagram:abc": Said(ts=int(item.taken_at.timestamp() * 1000),
                              person_id="ada", room=None, medium="social",
                              occasion="social:instagram:abc"),
    }
    # And only the handed items move the cursor.
    assert evidence._reads[KEY].reach == 10


async def test_chat_media_readings_reach_profile_without_becoming_sender_claims() -> None:
    from dataclasses import replace

    from nora.agent.egress import scan_identifiers
    from nora.domain.culture import Episode, RoomMessage
    from nora.domain.media import MediaObject
    from nora.domain.media_join import MediaJoin
    from nora.domain.models import DeliveryStatus, TranscriptRow
    from nora.language.profile import Mention
    from nora.obs.metrics import Metrics
    from nora.testing import FakeMediaObjectStore

    rows = []
    objects = []
    for i, (label, mime) in enumerate([
        ("image", "image/png"), ("video", "video/mp4"), ("sticker", "image/webp"),
        ("image", "image/png"), ("image", "image/png"),
    ], 1):
        sha = f"{i:06x}" + "0" * 58
        rows.append(RoomMessage(room="room-a", row=TranscriptRow(
            id=i, sender_id="sender", sender_name="Ada", person_id="ada", ts=T0,
            body=f"[{label} {sha[:6]}]", is_nora=False,
            delivery_status=DeliveryStatus.SENT, raw_json=None,
            media_json=f'[{{"sha256":"{sha}"}}]',
        )))
        if i != 4:
            objects.append(MediaObject(
                sha256=sha, media_type=mime, size=4, source="inbound",
                description=f"synthetic scene {i}" if i != 5 else "+85291234567",
            ))
    plain = replace(rows[0], row=replace(rows[0].row, id=6, body="hello", media_json=None))
    reads = _ReadsFake()
    adaptor = ProfilePersonAdaptor(
        profile=_ProfileFake(), culture=InMemoryCultureStore(),
        evidence=_evidence(reads, None), reads=reads, keys={KEY: PK},
        name_of=lambda _pid: "Ada", clock_ms=lambda: T0, clock=lambda: NOW,
        media_join=MediaJoin(store=FakeMediaObjectStore(objects),
                             screen=scan_identifiers, metrics=Metrics()),
    )
    rendered = await adaptor.inputs(key=KEY, batch=[
        rows[0], Mention(message=replace(rows[2], row=replace(
            rows[2].row, person_id="bo", sender_name="Bo"))), *rows[3:], plain,
        Episode(send_id=7, room="room-a", text="what happened?", outcome="responded",
                reactions=0, edits=0, replies=(rows[1].row,)),
    ])
    assert "synthetic scene 2" in rendered.split("<profile_episodes", 1)[1]
    assert "source_at=" in rendered.split("<profile_episodes", 1)[1]
    assert "synthetic scene 3" in rendered.split("<profile_mentions", 1)[1]
    for i in (1, 2, 3):
        assert f"synthetic scene {i}" in rendered
    assert '"raw_caption":' in rendered
    assert '"attribution": "shared_by_author"' in rendered
    assert '"state": "unavailable"' in rendered
    assert "no readable media evidence" in rendered
    assert "+85291234567" not in rendered
    assert ">hello<" in rendered
    assert set(adaptor._runs[KEY].citable.messages) == {1, 2, 3, 4, 5, 6}


async def test_message_transport_is_evidence_not_guessed_from_room_label():
    from dataclasses import replace
    from types import SimpleNamespace

    from nora.domain.culture import RoomMessage
    from nora.domain.models import DeliveryStatus, TranscriptRow

    room = SimpleNamespace(platform="signal", key="room-a", label="Common room")
    pk = replace(PK, rooms=(room,))
    reads = _ReadsFake()
    adaptor = ProfilePersonAdaptor(
        profile=_ProfileFake(), culture=InMemoryCultureStore(),
        evidence=_evidence(reads, None), reads=reads, keys={KEY: pk},
        name_of=lambda _pid: "Ada", clock_ms=lambda: T0, clock=lambda: NOW,
    )
    row = TranscriptRow(id=1, sender_id="s", sender_name="Ada", person_id="ada",
                        body="Hello", ts=T0, is_nora=False,
                        delivery_status=DeliveryStatus.SENT, raw_json=None)
    shown = await adaptor.inputs(key=KEY, batch=[RoomMessage(room="room-a", row=row)])
    assert 'platform="signal"' in shown


async def test_colliding_room_transports_are_omitted_in_both_orders():
    from dataclasses import replace
    from types import SimpleNamespace

    from nora.domain.culture import RoomMessage
    from nora.domain.models import DeliveryStatus, TranscriptRow

    for platforms in (("signal", "discord"), ("discord", "signal"), ("signal",)):
        pk = replace(
            PK, rooms=tuple(SimpleNamespace(platform=p, key="room-a", label=p) for p in platforms)
        )
        reads = _ReadsFake()
        adaptor = ProfilePersonAdaptor(
            profile=_ProfileFake(),
            culture=InMemoryCultureStore(),
            evidence=_evidence(reads, None),
            reads=reads,
            keys={KEY: pk},
            name_of=lambda _: "Ada",
            clock_ms=lambda: T0,
            clock=lambda: NOW,
        )
        row = TranscriptRow(
            id=1,
            sender_id="s",
            sender_name="Ada",
            person_id="ada",
            body="Hello",
            ts=T0,
            is_nora=False,
            delivery_status=DeliveryStatus.SENT,
            raw_json=None,
        )
        shown = await adaptor.inputs(key=KEY, batch=[RoomMessage(room="room-a", row=row)])
        line = next(line for line in shown.splitlines() if ">Hello</m>" in line)
        assert ('platform="' in line) is (len(platforms) == 1)

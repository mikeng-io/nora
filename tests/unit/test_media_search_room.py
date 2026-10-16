"""`media_search(scope="room")`: photos PEOPLE SENT, in the calling room only
(DEC-138).

The room authority is the message row, never the media
index's provenance, which names only the first room a byte string was seen in.
So a photo is found in a room when a message of THAT room carries its sha256,
whichever room the index row was born in, and is never found in a room whose
messages do not carry it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from nora.agent.room import Room
from nora.agent.tools import build_tool_specs
from nora.domain.media import MediaBlob, MediaObject
from nora.domain.models import NormalizedMessage
from nora.obs.metrics import Metrics
from nora.persons import load_persons
from nora.testing import FakeMediaObjectStore, FakeRecordStore, FakeRosterSource

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
NOW_MS = int(NOW.timestamp() * 1000)
DAY = 86_400_000

ROOM_A = "g-a"
ROOM_B = "g-b"
DINNER = "d1" * 32
PARK = "e2" * 32
OWN = "f3" * 32


@pytest.fixture
def persons(tmp_path):
    path = tmp_path / "identity.toml"
    path.write_text('[persons.p]\nname = "P"\n[[persons.p.signal]]\nphone = "+10000000001"\n')
    return load_persons(path)


@pytest.fixture
def world():
    return {"store": FakeRecordStore(), "media": FakeMediaObjectStore()}


def _tool(persons, world, room_id: str = ROOM_B, platform: str = "signal"):
    specs = build_tool_specs(
        store=world["store"], conversation_id=room_id, conversation_type="group",
        persons=persons, honcho=None, deliverer=None, metrics=Metrics(),
        room=Room(platform=platform, conversation_id=room_id,
                  roster_source=FakeRosterSource(groups={}), persons=persons),
        transit_source=None, media_store=world["media"], clock=lambda: NOW,
    )
    return next(s for s in specs if s.name == "media_search").call


async def _send(world, room: str, sha: str, *, days_ago: float = 1.0,
                media_type: str = "image/jpeg", role: str = "", is_nora: bool = False) -> None:
    await world["store"].append(
        message=NormalizedMessage(
            "signal", room, "group", "+10000000001", "Someone",
            NOW_MS - int(days_ago * DAY), "[image]", {},
            media_blobs=(MediaBlob(sha256=sha, media_type=media_type, size=4, role=role),),
        ),
        is_nora=is_nora,
    )


def _read(world, sha: str, *, title: str, description: str, born_in: str = ROOM_A,
          source: str = "inbound") -> None:
    world["media"].objs[sha] = MediaObject(
        sha256=sha, media_type="image/jpeg", size=4, source=source, title=title,
        description=description, describe_model="m",
        provenance={"platform": "signal", "conversation_id": born_in},
        created_at_ms=NOW_MS - DAY,
    )


async def _room(persons, world, **kwargs):
    return await _tool(persons, world)(scope="room", **kwargs)


async def test_a_photo_sent_in_room_a_is_never_found_from_room_b(persons, world):
    """The same bytes were first indexed in room A and sent only there."""
    _read(world, DINNER, title="a dinner table", description="noodles on a table")
    await _send(world, ROOM_A, DINNER)
    out = await _room(persons, world, query="dinner")
    assert out["status"] == "ok"
    assert out["results"] == []


async def test_a_photo_re_sent_into_room_b_is_found_in_room_b(persons, world):
    """First source wins in the index, so the row names room A. Room B's own
    message row is what puts it in room B."""
    _read(world, DINNER, title="a dinner table", description="noodles on a table",
          born_in=ROOM_A)
    await _send(world, ROOM_A, DINNER, days_ago=6)
    await _send(world, ROOM_B, DINNER, days_ago=2)
    out = await _room(persons, world, query="dinner")
    assert [r["handle"] for r in out["results"]] == [DINNER[:6]]
    assert out["results"][0]["description"] == "noodles on a table"
    assert out["results"][0]["sent"].startswith("2026-09-29")


async def test_days_excludes_a_photo_sent_before_the_window(persons, world):
    _read(world, DINNER, title="a dinner table", description="noodles")
    _read(world, PARK, title="a park", description="a dinner in a park")
    await _send(world, ROOM_B, DINNER, days_ago=10)
    await _send(world, ROOM_B, PARK, days_ago=2)
    week = await _room(persons, world, query="dinner", days=3)
    assert [r["handle"] for r in week["results"]] == [PARK[:6]]
    wide = await _room(persons, world, query="dinner", days=30)
    assert {r["handle"] for r in wide["results"]} == {DINNER[:6], PARK[:6]}


async def test_days_is_bounded_and_a_room_search_with_none_looks_back_90(persons, world):
    await _send(world, ROOM_B, DINNER, days_ago=80)
    await _send(world, ROOM_B, PARK, days_ago=100)
    default = await _room(persons, world)
    assert [r["handle"] for r in default["results"]] == [DINNER[:6]]
    clamped = await _room(persons, world, days=5000)
    assert clamped["days"] == 90
    assert [r["handle"] for r in clamped["results"]] == [DINNER[:6]]


async def test_an_unreadable_message_store_is_unavailable_never_no_photos(persons, world):
    world["store"].fail_on_recent = True
    out = await _room(persons, world, query="dinner")
    assert out["status"] == "unavailable"
    assert "results" not in out and "do not say there are none" in out["note"]


async def test_an_unreadable_media_index_is_unavailable_never_no_photos(persons, world):
    await _send(world, ROOM_B, DINNER)
    world["media"].fail = True
    out = await _room(persons, world, query="dinner")
    assert out["status"] == "unavailable" and "results" not in out


async def test_the_library_scope_reports_an_unreadable_index_as_unavailable(persons, world):
    """A database error from `search` is not swallowed as `()`: a down index must
    not read as an empty library."""
    world["media"].fail = True
    out = await _tool(persons, world)(query="cat")
    assert out["status"] == "unavailable"
    assert "results" not in out


async def test_her_own_generated_media_is_still_found_by_default(persons, world):
    _read(world, OWN, title="a birthday card", description="a birthday card for P",
          source="generated")
    _read(world, DINNER, title="a dinner table", description="a birthday dinner")
    await _send(world, ROOM_B, DINNER)
    out = await _tool(persons, world)(query="birthday")
    assert out["status"] == "ok"
    assert [r["handle"] for r in out["results"]] == [OWN[:6]]


async def test_the_library_scope_honours_days(persons, world):
    _read(world, OWN, title="old card", description="a card", source="generated")
    world["media"].objs[OWN] = MediaObject(
        sha256=OWN, media_type="image/png", size=4, source="generated",
        description="a card", provenance={}, created_at_ms=NOW_MS - 10 * DAY)
    out = await _tool(persons, world)(query="card", days=3)
    assert out["results"] == []
    out = await _tool(persons, world)(query="card", days=30)
    assert [r["handle"] for r in out["results"]] == [OWN[:6]]


async def test_only_pictures_and_clips_people_sent_are_returned(persons, world):
    await _send(world, ROOM_B, DINNER)
    await _send(world, ROOM_B, PARK, media_type="audio/aac")
    await _send(world, ROOM_B, OWN, role="cover")
    await _send(world, ROOM_B, "a9" * 32, is_nora=True)
    out = await _room(persons, world)
    assert [r["handle"] for r in out["results"]] == [DINNER[:6]]


async def test_a_result_names_no_sender_and_no_room(persons, world):
    _read(world, DINNER, title="a dinner table", description="noodles")
    await _send(world, ROOM_B, DINNER)
    out = await _room(persons, world, query="dinner")
    text = json.dumps(out)
    assert "Someone" not in text and "+1000" not in text
    assert ROOM_A not in text and ROOM_B not in text
    assert set(out["results"][0]) == {"handle", "kind", "sent", "title", "description"}


async def test_an_unread_photo_cannot_match_words_and_is_counted_not_hidden(persons, world):
    await _send(world, ROOM_B, DINNER)  # no media_objects row at all
    out = await _room(persons, world, query="dinner")
    assert out["results"] == []
    assert "1 photo(s)" in out["note"] and "not been read" in out["note"]
    listed = await _room(persons, world)
    assert listed["results"][0]["handle"] == DINNER[:6]
    assert listed["results"][0]["read"] is False


async def test_a_description_with_an_identifier_is_withheld(persons, world):
    _read(world, DINNER, title="a card", description="call 90000101 or +85290000101")
    await _send(world, ROOM_B, DINNER)
    out = await _room(persons, world)
    assert out["results"][0]["withheld"] is True
    assert "description" not in out["results"][0]


async def test_a_run_with_no_room_cannot_search_a_room(persons, world):
    """A Dreamer unit keyed by name has a key in the conversation slot and no
    platform. An empty answer there would be a false «no photos»."""
    await _send(world, "unit-key", DINNER)
    out = await _tool(persons, world, room_id="unit-key", platform="")(scope="room")
    assert out["status"] == "unavailable" and "results" not in out


# ---- videos: found by their stored reading ---------------------------------

CLIP = "c4" * 32


async def test_a_room_video_is_found_by_a_word_from_its_reading(persons, world):
    """A video's automatic reading is `content` (timecoded) beside an empty
    `description`; `video_readings` is a composite-keyed cache of deliberate
    reads that holds nothing the index lacks."""
    world["media"].objs[CLIP] = MediaObject(
        sha256=CLIP, media_type="video/mp4", size=4, source="inbound", title="a clip",
        description="", content="00:03 somebody blows out the candles on a birthday cake",
        describe_model="m", provenance={"conversation_id": ROOM_A}, created_at_ms=NOW_MS)
    await _send(world, ROOM_B, CLIP, media_type="video/mp4", days_ago=4)
    out = await _room(persons, world, query="candles")
    assert [(r["handle"], r["kind"]) for r in out["results"]] == [(CLIP[:6], "video")]
    assert "blows out the candles" in out["results"][0]["description"]


async def test_a_video_sent_in_another_room_is_not_found(persons, world):
    world["media"].objs[CLIP] = MediaObject(
        sha256=CLIP, media_type="video/mp4", size=4, source="inbound", title="a clip",
        description="", content="00:03 candles on a cake", provenance={}, created_at_ms=NOW_MS)
    await _send(world, ROOM_A, CLIP, media_type="video/mp4")
    assert (await _room(persons, world, query="candles"))["results"] == []


# ---- a code room search returned resolves in media_fetch -------------------

from nora.testing import FakeBlobStore  # noqa: E402
from tests.test_media_fetch import PNG_SHA, RED_PNG, CountingVision  # noqa: E402


def _fetch(persons, world, room_id: str = ROOM_B):
    blobs = FakeBlobStore()
    blobs.blobs[PNG_SHA] = RED_PNG
    specs = build_tool_specs(
        store=world["store"], conversation_id=room_id, conversation_type="group",
        persons=persons, honcho=None, deliverer=None, metrics=Metrics(),
        room=Room(platform="signal", conversation_id=room_id,
                  roster_source=FakeRosterSource(groups={}), persons=persons),
        transit_source=None, media_store=world["media"], clock=lambda: NOW,
        vision_source=CountingVision(), blob_store=blobs,
        describe_models={"image": "test/image-model"}, context_window=40,
    )
    return next(s for s in specs if s.name == "media_fetch").call


async def _bury(world, room: str, count: int = 400) -> None:
    """Text rows newer than the photo, so it is far outside `recent`'s window."""
    for i in range(count):
        await world["store"].append(message=NormalizedMessage(
            "signal", room, "group", "+10000000001", "Someone",
            NOW_MS - 1000 + i, f"chatter {i}", {}))


async def test_a_code_found_by_room_search_30_days_back_resolves_in_media_fetch(persons, world):
    _read(world, PNG_SHA, title="a dinner table", description="noodles")
    await _send(world, ROOM_B, PNG_SHA, days_ago=30, media_type="image/png")
    await _bury(world, ROOM_B)
    found = await _room(persons, world, query="dinner")
    code = found["results"][0]["handle"]
    out = await _fetch(persons, world)(code=code)
    assert out["status"] == "ok" and out["code"] == code


async def test_the_same_code_from_another_room_does_not_resolve(persons, world):
    await _send(world, ROOM_B, PNG_SHA, days_ago=30, media_type="image/png")
    out = await _fetch(persons, world, room_id=ROOM_A)(code=PNG_SHA[:6])
    assert out["status"] == "nothing_found"


async def test_a_handle_matching_two_old_photos_is_refused_with_the_candidates(persons, world):
    one, two = "ab12cd" + "0" * 58, "ab12cd" + "1" * 58
    await _send(world, ROOM_B, one, days_ago=20, media_type="image/png")
    await _send(world, ROOM_B, two, days_ago=25, media_type="image/png")
    await _bury(world, ROOM_B)
    out = await _fetch(persons, world)(code="ab12cd")
    assert out["status"] == "ambiguous" and out["could_be"] == ["ab12cd", "ab12cd"]


async def test_media_beyond_90_days_is_not_found(persons, world):
    await _send(world, ROOM_B, PNG_SHA, days_ago=100, media_type="image/png")
    await _send(world, ROOM_B, "12" * 32, days_ago=85, media_type="image/png")
    await _bury(world, ROOM_B)
    out = await _fetch(persons, world)(code=PNG_SHA[:6])
    assert out["status"] == "nothing_found"
    assert (await _fetch(persons, world)(code="121212"))["status"] != "nothing_found"


# ---- the identifier screen covers the whole reading ---------

PHONE = "+85290000101"


def _read_with(world, sha, **fields):
    base = {"title": "", "description": "", "content": ""}
    base.update(fields)
    world["media"].objs[sha] = MediaObject(
        sha256=sha, media_type="image/jpeg", size=4, source="inbound", describe_model="m",
        provenance={"conversation_id": ROOM_B}, created_at_ms=NOW_MS, **base)


async def test_a_title_holding_an_identifier_is_withheld_with_the_rest(persons, world):
    """The title is screened for identifiers too, not only shown beside `withheld: True`."""
    _read_with(world, DINNER, title=f"business card {PHONE}", description="a card on a table")
    await _send(world, ROOM_B, DINNER)
    out = await _room(persons, world)
    entry = out["results"][0]
    assert entry["withheld"] is True
    assert "title" not in entry and "description" not in entry
    assert PHONE not in json.dumps(out)


async def test_a_withheld_reading_cannot_be_probed_one_query_at_a_time(persons, world):
    """Matching on hidden text would let a model confirm its digits, so a withheld
    reading cannot be probed."""
    _read_with(world, DINNER, title="a card", content=f"call {PHONE} now")
    _read_with(world, PARK, title="a park", description="a dinner in a park")
    await _send(world, ROOM_B, DINNER)
    await _send(world, ROOM_B, PARK)
    for probe in (PHONE, "+8529775", "call"):
        out = await _room(persons, world, query=probe)
        assert [r["handle"] for r in out["results"]] != [DINNER[:6]], probe
        assert all(r["handle"] != DINNER[:6] for r in out["results"]), probe
    ok = await _room(persons, world, query="park")
    assert [r["handle"] for r in ok["results"]] == [PARK[:6]]


# ---- the row cap is a signal, not a silent drop -------------


async def test_a_code_behind_the_row_cap_is_unavailable_never_nothing_found(
    persons, world, monkeypatch,
):
    """A photo older than the row cap's reach is `unavailable`, not `nothing_found`."""
    monkeypatch.setattr("nora.domain.media.ROOM_MEDIA_ROWS", 3)
    await _send(world, ROOM_B, PNG_SHA, days_ago=60, media_type="image/png")
    for i in range(5):
        await _send(world, ROOM_B, f"{i + 1:02d}" * 32, days_ago=1 + i / 10)
    await _bury(world, ROOM_B)
    out = await _fetch(persons, world)(code=PNG_SHA[:6])
    assert out["status"] == "unavailable" and "not exist" in out["note"]


async def test_a_code_inside_the_row_cap_still_resolves_when_the_scan_truncated(
    persons, world, monkeypatch,
):
    monkeypatch.setattr("nora.domain.media.ROOM_MEDIA_ROWS", 3)
    _read(world, PNG_SHA, title="a dinner table", description="noodles")
    await _send(world, ROOM_B, PNG_SHA, days_ago=0.5, media_type="image/png")
    for i in range(5):
        await _send(world, ROOM_B, f"{i + 1:02d}" * 32, days_ago=2 + i)
    await _bury(world, ROOM_B)
    assert (await _fetch(persons, world)(code=PNG_SHA[:6]))["status"] == "ok"


async def test_room_search_says_when_older_media_was_not_searched(persons, world, monkeypatch):
    monkeypatch.setattr("nora.domain.media.ROOM_MEDIA_ROWS", 3)
    for i in range(5):
        await _send(world, ROOM_B, f"{i + 1:02d}" * 32, days_ago=1 + i)
    out = await _room(persons, world)
    assert out["truncated"] is True and len(out["results"]) == 3
    assert "NOT" in out["note"] and "did not reach" in out["note"]
    assert "truncated" not in await _room(persons, world, days=1)

"""Real SQL media-fetch cover custody and disclosure metadata."""

from __future__ import annotations

import re

import pytest

from nora.agent.toolkit.files import _COVER_NOTE
from nora.domain.media import MediaBlob
from nora.domain.models import NormalizedMessage
from nora.testing import FakeMediaObjectStore
from tests.test_media_fetch import (
    PNG_SHA,
    RED_PNG,
    _append_image,
    _call,
    _comprehended,
)
from tests.test_media_fetch import (
    wiring as wiring,
)


async def _append_shared_cover(store, *, sha256: str, ts: int = 100) -> None:
    """Append a cover-role blob with a generated share line that has no link."""
    blob = MediaBlob(
        sha256=sha256, media_type="image/png", size=len(RED_PNG), role="cover",
    )
    await store.append(message=NormalizedMessage(
        "signal", "g1", "group", "+852111", "Miles", ts,
        f"[shared story by @synth.example] [cover image {sha256[:6]}]",
        {}, media_blobs=(blob,),
    ))


async def _append_linked_cover(store, *, sha256: str, line: str, ts: int = 100) -> None:
    """Append a cover-role blob beside the item's generated share link."""
    blob = MediaBlob(
        sha256=sha256, media_type="image/png", size=len(RED_PNG), role="cover",
    )
    await store.append(message=NormalizedMessage(
        "signal", "g1", "group", "+852111", "Miles", ts,
        f"what is in this {line} [cover image {sha256[:6]}]",
        {}, media_blobs=(blob,),
    ))


_LEGACY_LINES = {
    "https://www.instagram.com/reel/SynthReel02/":
        "[shared reel by @synth.example https://www.instagram.com/reel/SynthReel02/]",
    "https://www.instagram.com/p/SynthPost02/":
        "[shared post by @synth.example https://www.instagram.com/p/SynthPost02/ — a caption]",
}


@pytest.mark.parametrize("link", sorted(_LEGACY_LINES))
async def test_a_stale_cover_beside_its_post_or_reel_link_is_refused_before_any_look(
    wiring, link,
):
    """A stored row with the item's link AND its cover. Serving the cover
    would answer one frame as the reel, so the code is refused before any
    reading, stored or fresh, and the note names the link to open instead."""
    await _append_linked_cover(wiring["store"], sha256=PNG_SHA, line=_LEGACY_LINES[link])
    # A stored reading exists too, so a refusal here is not the layers
    # having nothing to serve.
    index = FakeMediaObjectStore([_comprehended(PNG_SHA)])

    out = await _call(wiring["build"](media_store=index), code=PNG_SHA[:6])

    assert out == {
        "status": "nothing_found", "code": PNG_SHA[:6],
        "note": f"That code is only the cover picture of the item at {link}. "
                "If that is the item being asked about, open it with instagram_read.",
    }
    assert wiring["vision"].calls == 0
    assert not re.search("[\u2013\u2014]", out["note"]), "ADR 0015: no dash in a note"


async def test_a_story_cover_beside_its_link_is_still_served(wiring):
    """A story share renders its link AND its cover by design (a story link
    reads the author's current story, not the frame that was shared), so the
    guard must not refuse it: it is served with the cover note."""
    await _append_linked_cover(
        wiring["store"], sha256=PNG_SHA,
        line="[shared story by @synth.example "
             "https://www.instagram.com/stories/synth.example/3000000000000000001/]",
    )

    out = await _call(wiring["build"](), code=PNG_SHA[:6])

    assert out["status"] == "ok"
    assert wiring["vision"].calls == 1
    assert out["note"].endswith(_COVER_NOTE)


async def test_a_photo_beside_a_shared_link_is_not_a_stale_cover(wiring):
    """The guard reads the ROLE, not the line: a member's own photo sent in
    the same message as a shared post line is served as a photo."""
    blob = MediaBlob(sha256=PNG_SHA, media_type="image/png", size=len(RED_PNG))
    await wiring["store"].append(message=NormalizedMessage(
        "signal", "g1", "group", "+852111", "Miles", 100,
        "[shared reel by @synth.example https://www.instagram.com/reel/SynthReel02/]"
        f" [image {PNG_SHA[:6]}]",
        {}, media_blobs=(blob,),
    ))

    out = await _call(wiring["build"](), code=PNG_SHA[:6])

    assert out["status"] == "ok"
    assert _COVER_NOTE not in out["note"]


async def test_a_cover_served_from_a_fresh_look_says_it_is_only_the_cover(wiring):
    await _append_shared_cover(wiring["store"], sha256=PNG_SHA)

    out = await _call(wiring["build"](), code=PNG_SHA[:6])

    assert out["status"] == "ok"
    assert out["description"] == "A red one-pixel square."
    assert out["note"].endswith(_COVER_NOTE)
    assert "cover" in out["note"] and "if that line carries one" in out["note"]
    assert not re.search("[–—]", out["note"]), "ADR 0015: no dash in a note"


async def test_a_cover_served_from_the_stored_reading_says_so_too(wiring):
    """Layer 1: the same bytes were read once already (sent as a photo
    elsewhere, then shared). The stored reading is served without a look, and
    it is still only the cover of the thing this handle names here."""
    await _append_shared_cover(wiring["store"], sha256=PNG_SHA)
    index = FakeMediaObjectStore([_comprehended(PNG_SHA)])

    out = await _call(wiring["build"](media_store=index), code=PNG_SHA[:6])

    assert out["status"] == "ok"
    assert wiring["vision"].calls == 0
    assert out["note"].endswith(_COVER_NOTE)


async def test_a_cover_served_from_a_vision_cache_hit_says_so_too(wiring):
    """The content-hash cache, with no media index wired at all: the bytes
    were described once before (in any room), so no look is paid for, and it
    is still only the cover of what this handle names here."""
    await _append_shared_cover(wiring["store"], sha256=PNG_SHA)
    wiring["cache"].put(PNG_SHA, "A beach at dusk.")

    out = await _call(wiring["build"](media_store=None), code=PNG_SHA[:6])

    assert out["status"] == "ok"
    assert wiring["vision"].calls == 0
    assert out["description"] == "A beach at dusk."
    assert out["note"].endswith(_COVER_NOTE)


async def test_a_cover_asked_a_question_says_so_too(wiring):
    """Layer 2: a question looks again and writes nothing back, and the
    answer is still about the cover, not the reel."""
    await _append_shared_cover(wiring["store"], sha256=PNG_SHA)

    out = await _call(
        wiring["build"](), code=PNG_SHA[:6], question="what colour is the sky",
    )

    assert out["status"] == "ok"
    assert wiring["vision"].calls == 1
    assert out["note"].endswith(_COVER_NOTE)


async def test_a_withheld_cover_keeps_both_sentences(wiring):
    """An identifier in the cover's reading withholds the words; the cover
    sentence rides along, so the next step is still the link."""
    wiring["vision"].description = "A poster reading +85291234567"
    await _append_shared_cover(wiring["store"], sha256=PNG_SHA)

    out = await _call(wiring["build"](), code=PNG_SHA[:6])

    assert out["withheld"] is True
    assert out["note"].endswith(_COVER_NOTE)
    assert out["note"] != _COVER_NOTE


async def test_an_ordinary_photo_carries_no_cover_note(wiring):
    """The negative, on a row with no `role` at all."""
    await _append_image(wiring["store"], conversation_id="g1", sha256=PNG_SHA)

    out = await _call(wiring["build"](), code=PNG_SHA[:6])

    assert out["status"] == "ok"
    # Every description carries the how-to-report note; only
    # the cover clause is absent.
    assert _COVER_NOTE not in out["note"]


async def test_bytes_that_were_also_sent_as_a_photo_are_not_called_a_cover(wiring):
    """Every sighting must be a cover, not any: once the same bytes are
    somebody's own photo in view, the handle names that too, and «this is
    only the cover of a shared reel» would be a claim about the wrong one."""
    await _append_shared_cover(wiring["store"], sha256=PNG_SHA, ts=100)
    await _append_image(wiring["store"], conversation_id="g1", sha256=PNG_SHA, ts=200)

    out = await _call(wiring["build"](), code=PNG_SHA[:6])

    assert out["status"] == "ok"
    # Every description carries the how-to-report note; only
    # the cover clause is absent.
    assert _COVER_NOTE not in out["note"]


async def test_bytes_shared_as_a_cover_after_they_were_sent_as_a_photo_are_not_called_a_cover(
    wiring,
):
    """The other order of the test above. Sightings are read newest first, so
    here the COVER is seen first and the earlier photo has to take it back; the
    one above has the photo first and the cover has to be refused. A resolver
    that handled only one order passes the other one's test."""
    await _append_image(wiring["store"], conversation_id="g1", sha256=PNG_SHA, ts=100)
    await _append_shared_cover(wiring["store"], sha256=PNG_SHA, ts=200)

    out = await _call(wiring["build"](), code=PNG_SHA[:6])

    assert out["status"] == "ok"
    assert _COVER_NOTE not in out["note"]

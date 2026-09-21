"""`InstagramSocialClient`'s OWN surface — the part of it that is not the port
(design/instagram-adoption/10-discovery-retrieval-and-paging).

Its three states and its never-raises discipline are in
`tests/contract/test_social_provider_contract.py`, held against every other
implementation. What is here is what only this client can get wrong, and it is
all one rule seen from four sides: **a story result carries REFERENCES, never
content.** `social_fetch` finds out *that* something exists and where;
`media_fetch` finds out *what it is*. The moment a story result carries the
content inline it has absorbed `media_fetch`'s job, the model stops spending
deliberately, and "does Miles have a story up" costs the same as "what is in
it".
"""

from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from nora.infra.social import InstagramSocialClient
from nora.testing import FakeSocialSubjectIndex

_READ = {"platform": "instagram", "what": "story", "target": "miles", "limit": 5}
# Resolves `miles`, Tier 0 — every test below is about what comes BACK, so the
# resolve/authorize branches are left to the contract suite that owns them.
_SUBJECTS = {"miles": "221731317"}


@pytest.fixture
async def client():
    """`(set_envelope, client)` — the bridge answers whatever the test puts in
    the box, so each test states the WIRE it is defending against."""
    box: dict[str, object] = {"status": "ok", "items": []}

    async def story(request: web.Request) -> web.Response:
        box["_body"] = await request.json()
        return web.json_response({k: v for k, v in box.items() if not k.startswith("_")})

    app = web.Application()
    app.router.add_post("/social/story", story)
    server = TestServer(app)
    await server.start_server()
    root = str(server.make_url("")).rstrip("/")
    yield box, InstagramSocialClient(
        base_url=root,
        subjects=FakeSocialSubjectIndex(_SUBJECTS),
        timeout_seconds=5.0,
    )
    await server.close()


def _reel(**extra):
    item = {
        "id": "3323265599221534543",
        "kind": "video",
        "taken_at": 1789935632,
        "url": "https://scontent.cdninstagram.com/v/t50/x.mp4?oe=68D&oh=00_A",
    }
    item.update(extra)
    return item


async def test_an_item_carries_only_the_four_reference_fields(client):
    """The field whitelist, and the reason it is a whitelist rather than a
    denylist: it must hold against a field that does not exist yet. A bridge
    that grew a `caption`, an `ocr_text` or a transcript — all of them things
    instameow could plausibly surface — would leak comprehension into a result
    that is supposed to be a pointer, and nothing above this would notice
    because it would arrive as ordinary extra keys."""
    box, provider = client
    box["items"] = [_reel(caption="at the beach with tyler", ocr_text="BEACH DAY",
                          description="a man on a beach")]
    out = await provider.read(**_READ)
    assert out["status"] == "ok"
    assert set(out["items"][0]) == {"id", "kind", "taken_at", "url"}
    # And the content is not smuggled into the rendered text either.
    assert "beach" not in out["text"].lower()


async def test_the_item_url_is_rendered_as_a_media_fetch_marker(client):
    """`[video https://…]` is `media_fetch`'s own documented shape, which is
    what makes the second call a thing the model can actually make. A result
    the model cannot act on is a result that turns into a guess."""
    box, provider = client
    box["items"] = [_reel()]
    out = await provider.read(**_READ)
    assert out["text"].startswith("[video https://scontent.cdninstagram.com/")


async def test_limit_clamps_nora_side_because_the_reel_arrives_whole(client):
    """Stories are not paginated by the protocol — a reel arrives entire, so
    there is no cursor and nothing for a backend `limit` to page against. The
    cap therefore has to be applied HERE, and it is worth applying even though
    the bridge is passed the number: a fifteen-item story would otherwise
    arrive whole into an input that is re-sent on every subsequent model
    call."""
    box, provider = client
    box["items"] = [_reel(id=str(i)) for i in range(15)]
    out = await provider.read(**{**_READ, "limit": 2})
    assert len(out["items"]) == 2
    # The bridge is still TOLD the limit — the clamp is a second line of
    # defence, not a substitute for asking for less.
    assert box["_body"] == {"igid": "221731317", "limit": 2}


async def test_a_non_https_media_url_is_dropped(client):
    """The URL is handed to the model and from there to `media_fetch`, which
    fetches it over Nora's OWN network. A scheme this does not recognise is
    refused at the boundary rather than left for the next tool to defend
    against — and `file://` is the shape that makes the point."""
    box, provider = client
    box["items"] = [_reel(url="file:///etc/passwd"), _reel(id="good")]
    out = await provider.read(**_READ)
    assert [i["id"] for i in out["items"]] == ["good"]


async def test_an_unrecognised_kind_does_not_ride_through_verbatim(client):
    """`kind` is rendered into text the model reasons about, so a vocabulary
    that can grow silently is one nothing can be asserted against."""
    box, provider = client
    box["items"] = [_reel(kind="ephemeral_carousel_thing")]
    out = await provider.read(**_READ)
    assert out["items"][0]["kind"] == "media"


async def test_a_bogus_taken_at_is_absent_rather_than_guessed(client):
    """A story answer usually turns on WHEN («is this from today?»), and a
    model asked a question about time from a block with no time in it answers
    anyway — wrong, at a rate nothing reports. So the field is carried when it
    is real and simply absent when it is not; `True` quietly becoming a 1970
    timestamp is the wrong answer that reads as data."""
    box, provider = client
    box["items"] = [_reel(taken_at=True)]
    out = await provider.read(**_READ)
    assert "taken_at" not in out["items"][0]


async def test_expires_at_rides_the_result_when_the_bridge_names_one(client):
    """Instagram CDN URLs are signed and valid for a window, not
    indefinitely — so a "list now, look later" flow has to be SOON after, and
    a stored URL is a dead link."""
    box, provider = client
    box["items"] = [_reel()]
    box["expires_at"] = 1790022033
    out = await provider.read(**_READ)
    assert out["expires_at"] == 1790022033


async def test_a_row_this_backend_does_not_serve_is_unavailable_without_a_call(client):
    """It serves ONE row. Anything else arriving here is a routing bug, not a
    user error — and it must not be answered, because an Instagram profile
    read served from the story route would be the substitution the router
    exists to prevent, one layer further down."""
    box, provider = client
    out = await provider.read(
        platform="instagram", what="profile", target="miles", limit=5,
    )
    assert out["status"] == "unavailable"
    assert "_body" not in box   # positive control: nothing was sent

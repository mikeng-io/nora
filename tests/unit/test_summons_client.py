"""`InstagramSummonsClient` against a recording fake bridge
(`tests/unit/summons_bridge.py`, shaped like the Go handlers).

What is pinned: every write refusal is read by the envelope's `code`, never
by the status alone (409 is both `needs_human` and `already_replied`; 429 is
both `daily_cap` and `too_soon`); a body with no envelope still says what
happened; a row the bridge sends that is malformed is dropped rather than
guessed; and the client never raises.
"""

from __future__ import annotations

import pytest

from nora.infra.social.summons_client import InstagramSummonsClient
from tests.unit.summons_bridge import (
    SHORTCODE,
    SUMMONS_COMMENT,
    FakeSummonsBridge,
    comment_item,
    envelope,
    serve,
)


@pytest.fixture
async def world():
    fake = FakeSummonsBridge()
    async for url in serve(fake):
        yield fake, InstagramSummonsClient(base_url=url, timeout_seconds=5)


def test_the_client_has_no_mentions_read():
    """The notification feed never carried a comment mention of her: the read is
    gone, not merely unused."""
    assert not hasattr(InstagramSummonsClient, "mentions")


async def test_the_post_read_keeps_cdn_items_only(world):
    fake, client = world
    status, body = fake.post
    body = dict(body)
    body["items"] = [*body["items"], {"kind": "image", "url": "https://evil.test/x.jpg"}]
    fake.post = (status, body)
    got = await client.post(SHORTCODE)
    assert got.ok and got.value is not None
    assert len(got.value.items) == 3
    assert all("evil" not in item.url for item in got.value.items)
    assert got.value.items[1].duration_seconds == 12.5
    assert fake.calls == [("GET", f"/posts/{SHORTCODE}")]


async def test_a_post_with_no_usable_item_is_not_ok(world):
    fake, client = world
    fake.post = (200, {"shortcode": SHORTCODE, "items": []})
    got = await client.post(SHORTCODE)
    assert got.value is None and got.code == "bad_body"


async def test_a_shortcode_that_is_not_one_is_never_sent(world):
    fake, client = world
    got = await client.post("../accounts")
    assert got.code == "invalid_id"
    assert fake.calls == []


async def test_comments_are_read_and_a_malformed_row_is_dropped(world):
    fake, client = world
    fake.comments = (200, {"items": [
        comment_item("17900000000000010", "hi"),
        {"id": "x", "text": "no id"},
        comment_item("17900000000000011", "hello", at=None),
    ], "next_cursor": None, "skipped_count": 0})
    got = await client.comments(SHORTCODE)
    assert got.value is not None
    assert [c.id for c in got.value] == ["17900000000000010", "17900000000000011"]
    assert got.value[1].at is None


async def test_a_comment_keeps_its_author_igid_and_a_non_decimal_one_matches_nobody(world):
    """The igid is what the occasion matches the card's sender against; one
    that is not a decimal id is "" and so matches nobody."""
    fake, client = world
    fake.comments = (200, {"items": [
        comment_item("17900000000000010", "hi", igid="10000001"),
        comment_item("17900000000000011", "hi", igid="12_34"),
    ]})
    got = await client.comments(SHORTCODE)
    assert got.value is not None
    assert [c.author_igid for c in got.value] == ["10000001", ""]


async def test_a_reply_page_is_asked_for_with_reply_to(world):
    fake, client = world
    await client.comments(SHORTCODE, reply_to=SUMMONS_COMMENT)
    assert fake.calls == [("GET", f"/posts/{SHORTCODE}/comments?reply_to={SUMMONS_COMMENT}")]


async def test_the_write_posts_exactly_the_text_and_the_target(world):
    fake, client = world
    got = await client.post_comment(SHORTCODE, text="It's a grey heron.",
                                    reply_to_comment_id=SUMMONS_COMMENT)
    assert got.ok and got.value == "17900000000000999"
    assert fake.calls == [("POST", f"/posts/{SHORTCODE}/comments")]
    assert fake.posts() == [
        {"text": "It's a grey heron.", "reply_to_comment_id": SUMMONS_COMMENT},
    ]


@pytest.mark.parametrize("code", [
    "writes_disabled", "writes_blocked", "not_a_summons", "already_replied",
    "needs_human", "invalid_text", "daily_cap", "too_soon", "ledger_unavailable",
    "upstream_rejected", "internal",
])
async def test_every_write_refusal_is_read_by_its_code(world, code):
    """Two codes share 409 and two share 429: the status alone cannot say
    which, and a client that read it would retry a checkpoint or drop a
    duplicate. Every refusal comes back as its own code."""
    fake, client = world
    fake.write = envelope(code)
    got = await client.post_comment(SHORTCODE, text="x", reply_to_comment_id=SUMMONS_COMMENT)
    assert got.value is None and got.code == code
    assert got.status == envelope(code)[0]


async def test_a_failure_with_no_envelope_is_named_by_its_status(world):
    fake, client = world
    fake.write = (502, "<html>bad gateway</html>")
    got = await client.post_comment(SHORTCODE, text="x", reply_to_comment_id=SUMMONS_COMMENT)
    assert got.code == "http_502"


async def test_a_201_without_a_readable_id_is_still_published(world):
    fake, client = world
    fake.write = (201, {"id": "not-an-id"})
    got = await client.post_comment(SHORTCODE, text="x", reply_to_comment_id=SUMMONS_COMMENT)
    assert got.ok and got.value is None


async def test_an_unreachable_bridge_is_a_code_never_an_exception():
    client = InstagramSummonsClient(base_url="http://127.0.0.1:9", timeout_seconds=2)
    got = await client.post_comment(SHORTCODE, text="x", reply_to_comment_id=SUMMONS_COMMENT)
    assert got.value is None and got.code in {"unreachable", "timeout"}
    assert (await client.comments(SHORTCODE)).code in {"unreachable", "timeout"}


async def test_a_reply_target_that_is_not_a_comment_id_is_never_sent(world):
    fake, client = world
    got = await client.post_comment(SHORTCODE, text="x", reply_to_comment_id="1/../2")
    assert got.code == "invalid_id" and fake.calls == []

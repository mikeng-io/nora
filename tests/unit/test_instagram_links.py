"""The one Instagram link parser (`nora.domain.instagram_links`), read by the
adapter's transcript render and by `instagram_read`.

The parser's own behaviour through the transcript is pinned in
`tests/unit/test_instagram_parser.py`, and the tool's «a hostile link never
reaches the bridge» in `tests/unit/test_instagram_read_links.py`. What is here
is the module's own surface: which link becomes which `(kind, key)`, and the
rebuilt URL. Every handle and shortcode is synthetic.
"""

from __future__ import annotations

import pytest

from nora.domain.instagram_links import InstagramLink, is_handle, link_shape, parse_link

_PK = "3000000000000000001"


@pytest.mark.parametrize(
    ("url", "kind", "key", "clean"),
    [
        ("https://www.instagram.com/some.account/", "profile", "some.account",
         "https://www.instagram.com/some.account/"),
        ("https://instagram.com/some.account?igsh=c3ludGhldGlj", "profile", "some.account",
         "https://www.instagram.com/some.account/"),
        ("https://www.instagram.com/p/SynthPost01/?img_index=2", "post", "SynthPost01",
         "https://www.instagram.com/p/SynthPost01/"),
        ("https://www.instagram.com/reel/SynthReel01/", "reel", "SynthReel01",
         "https://www.instagram.com/reel/SynthReel01/"),
        ("http://www.instagram.com/reels/SynthReel01", "reel", "SynthReel01",
         "https://www.instagram.com/reel/SynthReel01/"),
        (f"https://www.instagram.com/stories/Some.Account/{_PK}?reel_owner_id=4242424242",
         "story", "Some.Account", f"https://www.instagram.com/stories/Some.Account/{_PK}/"),
        # A story-ring share names the account's whole current story.
        ("https://www.instagram.com/stories/some.account/?utm_source=ig_story_ring",
         "story", "some.account", "https://www.instagram.com/stories/some.account/"),
    ],
)
def test_each_supported_link_becomes_its_kind_and_key(url, kind, key, clean):
    link = parse_link(url)
    assert link is not None
    assert (link.kind, link.key) == (kind, key)
    assert link.url == clean
    assert "?" not in link.url and "#" not in link.url


def test_a_story_link_carries_its_pk():
    link = parse_link(f"https://www.instagram.com/stories/some.account/{_PK}/")
    assert link == InstagramLink("story", "some.account", _PK)


@pytest.mark.parametrize(
    "url",
    [
        "https://www.instagram.com/",
        "https://www.instagram.com/explore/",
        "https://www.instagram.com/explore/tags/synth/",
        "https://www.instagram.com/accounts/login/",
        "https://www.instagram.com/direct/inbox/",
        "https://www.instagram.com/p/",
        "https://www.instagram.com/p/bad.code/",
        # A saved highlight: `highlights` passes the username charset, and it
        # is not an account whose story this reads.
        "https://www.instagram.com/stories/highlights/17800000000000001/",
        "https://www.instagram.com/stories/HIGHLIGHTS/17800000000000001/",
        "https://www.instagram.com/stories/highlights/",
        "https://www.instagram.com/stories/4242424242/",
        "https://www.instagram.com/reel/bad%20code/",
        "https://www.instagram.com/4242424242/",
        "https://notinstagram.com/some.account/",
        "https://some.account.instagram.com/",
        None,
        "",
        42,
    ],
)
def test_anything_else_is_unsupported(url):
    assert parse_link(url) is None


def test_the_shape_is_a_label_and_never_a_licence():
    """The adapter needs the kind of a link it will not render, so the shape
    is answered on its own. `parse_link` is still `None` for the same url."""
    evil = "https://evil.example/reel/SynthReel01/"
    assert link_shape(evil) == "reel"
    assert parse_link(evil) is None
    assert link_shape("https://www.instagram.com/explore/") is None


@pytest.mark.parametrize(
    ("value", "ok"),
    [("some.account", True), ("a_1", True), ("4242424242", False), ("bad-name", False),
     ("", False), ("x" * 31, False), ("ｓynth", False)],
)
def test_a_handle_has_a_non_digit_and_only_its_charset(value, ok):
    assert is_handle(value) is ok


def test_a_story_ring_link_has_no_frame_id():
    link = parse_link("https://www.instagram.com/stories/some.account/")
    assert link == InstagramLink("story", "some.account", "")


def test_the_transcript_render_keeps_its_measured_shapes():
    """The adapter passes `story_ring=False`: a shared item's kind applies to the
    three-segment story form only, so a two-segment one keeps no label (the
    adapter renders it as `post`)."""
    ring = "https://www.instagram.com/stories/some.account/"
    assert link_shape(ring) == "story"
    assert link_shape(ring, story_ring=False) is None
    frame = f"https://www.instagram.com/stories/some.account/{_PK}/"
    assert link_shape(frame, story_ring=False) == "story"

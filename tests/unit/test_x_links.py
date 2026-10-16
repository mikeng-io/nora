"""`nora.domain.x_links`: which X links name a post or an account, and that
nothing else is read as one."""

from __future__ import annotations

import pytest

from nora.domain.x_links import XLink, is_handle, is_x_host, parse_link


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://x.com/nasa/status/1234567890", XLink("post", "1234567890")),
        ("https://twitter.com/nasa/status/1234567890", XLink("post", "1234567890")),
        ("https://www.x.com/nasa/status/1234567890/", XLink("post", "1234567890")),
        ("https://mobile.twitter.com/nasa/status/42", XLink("post", "42")),
        ("http://x.com/nasa/status/42", XLink("post", "42")),
        # Share junk is dropped, never read.
        ("https://x.com/nasa/status/42?s=20&t=abcDEF", XLink("post", "42")),
        ("https://x.com/nasa/status/42#reply", XLink("post", "42")),
        # A media sub-path still names the post.
        ("https://x.com/nasa/status/42/photo/1", XLink("post", "42")),
        # X's id-only forms.
        ("https://x.com/i/status/42", XLink("post", "42")),
        ("https://x.com/i/web/status/42", XLink("post", "42")),
        ("https://x.com/nasa", XLink("profile", "nasa")),
        ("https://x.com/nasa/", XLink("profile", "nasa")),
        ("https://twitter.com/Some_User_15ch", XLink("profile", "Some_User_15ch")),
    ],
)
def test_a_post_or_account_link_is_read(url, expected):
    assert parse_link(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        # Not X, or X spelled as a substring or a suffix.
        "https://x.com.evil.example/nasa/status/42",
        "https://evilx.com/nasa",
        "https://www.instagram.com/p/SynthPost01/",
        "https://nitter.net/nasa/status/42",
        # A malformed origin.
        "https://user:pass@x.com/nasa",
        "https://x.com:8443/nasa",
        "ftp://x.com/nasa",
        # X's own pages, which name no account or post.
        "https://x.com/home",
        "https://x.com/explore",
        "https://x.com/search?q=nasa",
        "https://x.com/i/flow/login",
        "https://x.com/hashtag/space",
        "https://x.com/settings/profile",
        "https://x.com/",
        # A segment outside its charset.
        "https://x.com/na.sa",
        "https://x.com/sixteen_chars_xx",
        "https://x.com/nasa/status/12ab",
        "https://x.com/nаsa",  # a Cyrillic a
        # An account sub-page is not the account.
        "https://x.com/nasa/likes",
        # Not a link at all.
        "",
        "   ",
        None,
        42,
    ],
)
def test_anything_else_is_refused(url):
    assert parse_link(url) is None


def test_the_handle_charset_is_x_s_own():
    assert is_handle("nasa")
    assert is_handle("a_b_1")
    assert not is_handle("@nasa")
    assert not is_handle("na.sa")
    assert not is_handle("x" * 16)
    assert not is_handle("")
    assert not is_handle(None)


def test_is_x_host_tells_an_x_page_from_another_network():
    assert is_x_host("https://x.com/home")
    assert not is_x_host("https://www.instagram.com/p/SynthPost01/")
    assert not is_x_host("not a url")

"""`OpenGraphPreviewSource` — the SSRF guard, the card, and the redirect rule.

**WHAT THIS FILE CAN AND CANNOT MEASURE, stated up front** (CLAUDE.md: never
read an instrument's output as evidence without first checking it can measure
the thing). The guard this module exists to test refuses every destination that
is not globally routable — and an `aiohttp_server` fixture binds `127.0.0.1`,
which is precisely such a destination. So an end-to-end "fetch a page and get a
card" test is not merely inconvenient here, it is asking the code under test to
do the one thing it is written to refuse.

The consequence is deliberate and the coverage is split to match:

* the GUARD is tested as a predicate, against literal addresses (`_safe`), so
  every refusal branch — scheme, loopback, RFC1918, link-local, IPv6 loopback —
  is asserted directly;
* the CARD is tested as a pure function over bytes (`card_from_html`), so every
  precedence, unescaping, clamping and titleless-page decision is asserted
  without a socket;
* the REDIRECT rule is tested as a pure function (`next_hop`);
* and ONE end-to-end test stands up a real loopback server serving a perfect
  OpenGraph page and asserts the source returns `None` anyway — which is the
  positive control for the guard being in the fetch path at all. Without it,
  every `None` in this file would be consistent with a source that never
  fetches anything.

**Still not covered end to end, and named rather than left implicit:** that the
byte ceiling truncates a real response, that a non-HTML `Content-Type` is
refused mid-flight, and that `og:image` is downloaded and base64'd. All three
are reachable only through a fetch, and a fetch is reachable only through the
guard. They are the operator's live-smoke items along with the wire names.
"""

from __future__ import annotations

import pytest
from aiohttp import web

from nora.infra.preview import (
    MAX_DESCRIPTION_CHARS,
    MAX_TITLE_CHARS,
    OpenGraphPreviewSource,
    _safe,
    card_from_html,
    next_hop,
)

URL = "https://links.example.net/abc"


def _card(body: str):
    return card_from_html(URL, body, image_src_out=[])


# ------------------------------------------------------------------ the guard

@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:9100/metrics",     # Nora's OWN metrics endpoint
        "http://localhost:18080/api/v1/rpc",  # signal-cli's JSON-RPC
        "http://10.0.0.5/",                  # RFC1918
        "http://192.168.1.1/",
        "http://172.16.0.1/",
        "http://169.254.169.254/latest/meta-data/",   # cloud metadata
        "http://[::1]/",                     # IPv6 loopback
        "http://0.0.0.0/",                   # the "all interfaces" form
    ],
)
async def test_a_destination_that_is_not_globally_routable_is_refused(url):
    """A Tavily fetch runs from ITS OWN servers, never Nora's (DEC-73, DEC-116),
    so it needs no such guard. This module IS handed a url, from model output,
    and fetches it over NORA'S OWN network — so the guard applies.

    Each of these is a real thing on Nora's network or her host, not a
    hypothetical: `127.0.0.1:9100` is her `/health`+`/metrics`, `:18080` is
    signal-cli, `169.254.169.254` is the cloud metadata endpoint.
    """
    assert await _safe(url) is False


@pytest.mark.parametrize("url", ["file:///etc/passwd", "data:text/html,<title>x",
                                 "gopher://example.com/", "ftp://example.com/"])
async def test_a_non_http_scheme_is_refused(url):
    assert await _safe(url) is False


async def test_a_public_literal_address_is_allowed():
    """The POSITIVE control for the predicate. Without it every assertion above
    is equally consistent with a `_safe` that returns False unconditionally —
    which would be a preview feature that silently never works, R-5's shape.

    A literal IP so `getaddrinfo` does no DNS and the test needs no network.
    """
    assert await _safe("http://8.8.8.8/") is True
    assert await _safe("https://1.1.1.1/x") is True


async def test_a_host_that_does_not_resolve_is_refused():
    assert await _safe("http://nx.invalid./") is False


# ------------------------------------------------------------------- the card

def test_open_graph_tags_win_and_become_the_card():
    card = _card(
        '<html><head><meta property="og:title" content="A shortened link">'
        '<meta property="og:description" content="What it points at">'
        "<title>fallback</title></head><body>ignored</body></html>"
    )
    assert card is not None
    assert card.title == "A shortened link"
    assert card.description == "What it points at"
    assert card.url == URL


def test_twitter_tags_are_the_second_choice():
    card = _card(
        '<html><head><meta name="twitter:title" content="Twitter title">'
        '<meta name="twitter:description" content="Twitter description">'
        "</head></html>"
    )
    assert card is not None
    assert card.title == "Twitter title"
    assert card.description == "Twitter description"


def test_a_plain_title_tag_is_the_last_resort():
    """An ordinary page with no social markup still previews. Refusing one
    would make the feature look broken on most of the web."""
    card = _card("<html><head><title>Just a page</title></head></html>")
    assert card is not None
    assert card.title == "Just a page"
    assert card.description == ""


def test_a_bare_meta_description_is_used_when_there_is_no_og_one():
    card = _card(
        '<html><head><title>T</title>'
        '<meta name="description" content="The plain one"></head></html>'
    )
    assert card is not None
    assert card.description == "The plain one"


def test_a_page_with_no_usable_title_yields_no_card_at_all():
    """`--preview-title` is documented MANDATORY. A titleless card is not a
    degraded card, it is an invalid one — so the message goes out plain rather
    than carrying a blank chip."""
    assert _card("<html><head><meta property='og:description' content='x'></head></html>") is None
    assert _card("<html><head><title>   </title></head></html>") is None
    assert _card("") is None


def test_entities_are_unescaped_exactly_once():
    """Once, not twice: `&amp;amp;` in a title is the R-13b double-escaping
    shape, and a raw `&amp;` shown to the group is the under-escaped one."""
    card = _card('<html><head><meta property="og:title" content="Tom &amp; Jerry"></head></html>')
    assert card is not None
    assert card.title == "Tom & Jerry"


def test_whitespace_in_a_title_is_collapsed():
    card = _card("<html><head><title>a\n   long\t title</title></head></html>")
    assert card is not None
    assert card.title == "a long title"


def test_the_strings_are_clamped():
    """These strings are chosen by whoever controls the page, and they land in
    an audit row and a log line as well as in the group."""
    card = _card(
        f'<html><head><meta property="og:title" content="{"T" * 900}">'
        f'<meta property="og:description" content="{"D" * 900}"></head></html>'
    )
    assert card is not None
    assert len(card.title) == MAX_TITLE_CHARS
    assert len(card.description) == MAX_DESCRIPTION_CHARS


def test_an_og_tag_in_the_body_is_ignored():
    """`</head>` ends collection, and `_done` is CHECKED rather than merely
    set. Otherwise a page could hide the title it hands Nora somewhere no
    browser and no other Signal client would look."""
    card = _card(
        "<html><head><title>Real title</title></head><body>"
        '<meta property="og:title" content="Smuggled"></body></html>'
    )
    assert card is not None
    assert card.title == "Real title"


def test_a_meta_tag_inside_a_script_string_is_not_read_as_markup():
    """The reason this is an HTML parser and not a regex over the document.

    A `re.findall` over `<meta ...>` would let a page choose Nora's card title
    from inside a JavaScript string literal; this assertion pins the
    HTML-parser behaviour.
    """
    card = _card(
        '<html><head><title>Real title</title>'
        '<script>var s = \'<meta property="og:title" content="Injected">\';</script>'
        "</head></html>"
    )
    assert card is not None
    assert card.title == "Real title"


def test_the_og_image_src_is_handed_back_out_of_band():
    """The src leaves through the out-parameter and never through a
    `LinkPreview` field, so a caller cannot mistake an unfetched src for
    something sendable."""
    seen: list[str] = []
    card = card_from_html(
        URL,
        '<html><head><title>T</title>'
        '<meta property="og:image" content="/img/card.png"></head></html>',
        image_src_out=seen,
    )
    assert card is not None
    assert card.image_data_uri is None
    assert seen == ["/img/card.png"]


# ---------------------------------------------------------------- redirects

@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_every_redirect_status_produces_a_next_hop(status):
    assert next_hop(status, "https://example.com/real", URL) == "https://example.com/real"


def test_a_relative_location_is_resolved_against_the_url_it_came_from():
    assert next_hop(302, "/real-page", "https://links.example.net/abc") == "https://links.example.net/real-page"


def test_a_non_redirect_status_is_not_a_hop():
    assert next_hop(200, "https://example.com/", URL) is None
    assert next_hop(404, None, URL) is None


def test_a_redirect_with_no_location_goes_nowhere():
    assert next_hop(302, None, URL) is None


# ------------------------------------------------------- the end-to-end control

async def test_the_guard_refuses_a_real_reachable_page(aiohttp_server):
    """THE POSITIVE CONTROL for the guard being in the fetch path.

    A real server, really listening, really serving a perfect OpenGraph
    document that `card_from_html` above proves would make a good card — and
    the source still returns `None`, because the server is on loopback. If the
    guard were removed this test would go red by SUCCEEDING, which is the only
    direction a security check can be usefully asserted in.
    """
    served = []

    async def handler(request: web.Request) -> web.Response:
        served.append(request.path)
        return web.Response(
            content_type="text/html",
            text='<html><head><meta property="og:title" content="A page">'
                 '<meta property="og:description" content="ok"></head></html>',
        )

    app = web.Application()
    app.router.add_get("/page", handler)
    server = await aiohttp_server(app)

    source = OpenGraphPreviewSource(timeout_seconds=2.0)
    assert await source.preview_for(url=str(server.make_url("/page"))) is None
    assert served == []          # ...and it never even connected


async def test_an_unreachable_host_is_a_missing_card_not_an_exception():
    """R-5. Whatever the network did, `preview_for` returns rather than raises
    — `Deliverer` is on the other side of this call with a human waiting."""
    source = OpenGraphPreviewSource(timeout_seconds=1.0)
    assert await source.preview_for(url="https://nx.invalid./page") is None


async def test_a_malformed_url_is_a_missing_card_not_an_exception():
    source = OpenGraphPreviewSource(timeout_seconds=1.0)
    assert await source.preview_for(url="https://") is None
    assert await source.preview_for(url="not a url at all") is None


# ── against a REAL server, not a fixture ─────────────────────────────────────


@pytest.mark.network
async def test_a_real_public_page_produces_a_real_card():
    """The whole path against the LIVE internet: DNS, the SSRF address check, a
    real redirect hop, real OpenGraph tags, a real image body.

    Every other test in this module stops at a seam — and it has to, because the
    guard refuses non-globally-routable hosts and `aiohttp_server` binds
    loopback, so an end-to-end test on a fixture asks the code to do the one
    thing it exists to refuse. That is a real limit, and it means the fixtures
    can only ever prove the REFUSALS. This is the test that proves the accept.

    The URL is a real public page that serves OpenGraph tags. Network-marked,
    so it is deselected by default and run deliberately:
    `uv run pytest -m network`.
    """
    source = OpenGraphPreviewSource(timeout_seconds=10.0, fetch_image=True)
    card = await source.preview_for(url="https://github.com/")

    assert card is not None, "the live page served no card"
    # The url is the one PASSED IN, never the post-redirect target — signal-cli
    # requires the body to carry the same url, and the body carries what she wrote.
    assert card.url == "https://github.com/"
    assert card.title                       # real og:title
    assert card.image_data_uri is None or card.image_data_uri.startswith("data:image/")


@pytest.mark.network
async def test_the_live_guard_still_refuses_a_private_host():
    """The same real source, pointed somewhere it must not go. Without this the
    test above would pass just as well against a source with no guard at all."""
    source = OpenGraphPreviewSource(timeout_seconds=5.0, fetch_image=False)
    assert await source.preview_for(url="http://169.254.169.254/latest/meta-data/") is None
    assert await source.preview_for(url="http://127.0.0.1:9100/metrics") is None

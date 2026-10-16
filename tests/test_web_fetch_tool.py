"""`web_fetch`'s own closure (DEC-116) — the egress screen, the unwired/raising
degradations, and that a refused/unwired call never reaches the provider.

Mirrors `tests/test_reminder_tools.py`'s lightweight `_tools` pattern: built
through `ToolRegistry.specs(...)`, no database, no real Tavily. `web_search`'s
provider-call path is covered by `tests/test_tavily.py`; this file is about the
closure IN `nora.agent.tools` that sits in front of it — the part
`tests/unit/test_tool_registry.py`/`tests/test_tools.py` (name, budget, tier)
and `tests/test_tavily.py` (provider behaviour) do not reach.
"""

from __future__ import annotations

from typing import Any

from nora.agent.registry import ToolRegistry
from nora.agent.room import Room
from nora.domain.models import PersonRef
from nora.obs.metrics import Metrics
from nora.testing import FakeMemoryStore
from tests.fakes import SinkSender

MILES = PersonRef(person_id="miles", name="Miles Novak", honcho_peer_id="miles")


class _FakeStore:
    async def recent(self, *, conversation_id: str, limit: int):
        return []


class _FakePersons:
    def __init__(self, *refs: PersonRef) -> None:
        self.by_person = {ref.person_id: ref for ref in refs}

    def resolve(self, platform: str, sender_id: str):
        return None


class _FakeSearchProvider:
    """`SearchProvider` double that records every `fetch` call it actually
    receives — the probe a screen that refuses BEFORE the call needs, the same
    shape `web_search`'s own `calls == []` comment describes."""

    def __init__(self, *, raises: bool = False,
                 search: dict[str, Any] | None = None,
                 fetch: dict[str, Any] | None = None) -> None:
        self.fetch_calls: list[str] = []
        self._raises = raises
        # A fixed payload per verb, for the note tests at the bottom: the
        # shapes the real providers return, which carry no `note` on `ok` or
        # `nothing_found`.
        self._search = search
        self._fetch = fetch

    async def search(self, *, query: str) -> dict[str, Any]:
        if self._search is not None:
            return dict(self._search)
        return {"status": "ok", "results": []}

    async def fetch(self, *, url: str) -> dict[str, Any]:
        self.fetch_calls.append(url)
        if self._raises:
            raise RuntimeError("boom")
        if self._fetch is not None:
            return dict(self._fetch)
        # `text`, NOT `content` — same Strands-envelope invariant as the real
        # providers (a dict carrying both `status` and `content` passes
        # through `_wrap_tool_result` unwrapped and reads as "empty").
        return {"status": "ok", "url": url, "text": "the page"}


def _room() -> Room:
    return Room(
        platform="signal", conversation_id="g1", roster_source=None,
        persons=_FakePersons(MILES), tagger=MILES,
    )


def _tools(*, search=None, metrics: Metrics | None = None, **turn) -> dict:
    registry = ToolRegistry(search=search)
    specs = registry.specs(
        store=_FakeStore(), conversation_id="g1", conversation_type="group",
        persons=_FakePersons(MILES), honcho=FakeMemoryStore(), deliverer=SinkSender(),
        metrics=metrics or Metrics(), room=_room(), **turn,
    )
    return {spec.name: spec.call for spec in specs}


async def test_unwired_answers_unavailable():
    tools = _tools(search=None)
    out = await tools["web_fetch"](url="https://example.com/a")
    assert out["status"] == "unavailable"


async def test_a_non_http_scheme_is_refused_before_the_provider_is_called():
    fake = _FakeSearchProvider()
    tools = _tools(search=fake)
    out = await tools["web_fetch"](url="file:///etc/passwd")
    assert out["status"] == "nothing_found"
    assert fake.fetch_calls == []


async def test_a_bare_string_with_no_scheme_is_refused():
    fake = _FakeSearchProvider()
    tools = _tools(search=fake)
    out = await tools["web_fetch"](url="example.com/a")
    assert out["status"] == "nothing_found"
    assert fake.fetch_calls == []


async def test_an_identifier_in_the_url_is_refused_before_the_provider_is_called():
    """The egress concern is not SSRF (Tavily connects, never Nora) — it is that
    the URL itself leaves to a THIRD PARTY, model-chosen, and can carry an
    identifier the same way a `web_search` query can."""
    fake = _FakeSearchProvider()
    tools = _tools(search=fake)
    out = await tools["web_fetch"](url="https://example.com/?n=+85212345678")
    assert out["status"] == "nothing_found"
    assert fake.fetch_calls == []


async def test_a_percent_encoded_identifier_in_the_url_is_also_refused():
    fake = _FakeSearchProvider()
    tools = _tools(search=fake)
    out = await tools["web_fetch"](url="https://example.com/?n=%2B85212345678")
    assert out["status"] == "nothing_found"
    assert fake.fetch_calls == []


async def test_a_coordinate_in_the_url_is_refused_before_the_provider_is_called():
    fake = _FakeSearchProvider()
    tools = _tools(search=fake)
    out = await tools["web_fetch"](url="https://example.com/?at=22.31,114.16")
    assert out["status"] == "nothing_found"
    assert fake.fetch_calls == []


async def test_an_ordinary_url_reaches_the_provider():
    fake = _FakeSearchProvider()
    tools = _tools(search=fake)
    out = await tools["web_fetch"](url="https://example.com/a")
    assert out["status"] == "ok"
    assert fake.fetch_calls == ["https://example.com/a"]


async def test_a_raising_provider_degrades_to_unavailable():
    fake = _FakeSearchProvider(raises=True)
    tools = _tools(search=fake)
    out = await tools["web_fetch"](url="https://example.com/a")
    assert out["status"] == "unavailable"


# ---- an Instagram page is instagram_read's ----------------------------------
# A shared post's page link must not be fetched here. The guard is the one
# `media_fetch` has, through the same helper. Every link below is synthetic.

_INSTAGRAM_PAGES = {
    "post": "https://www.instagram.com/p/SynthPost01/",
    "reel": "https://www.instagram.com/reel/SynthReel01/?igsh=c3ludGhldGlj",
    "reels": "https://www.instagram.com/reels/SynthReel01/",
    "story": "https://www.instagram.com/stories/some.account/3000000000000000001/",
    "story_ring": "https://www.instagram.com/stories/some.account/",
    "profile": "https://www.instagram.com/some.account/",
    "bare_host": "https://instagram.com/p/SynthPost01/",
    "http": "http://www.instagram.com/p/SynthPost01/",
    "no_scheme": "instagram.com/p/SynthPost01/",
    "no_scheme_www": "www.instagram.com/reel/SynthReel01/",
}


async def test_an_instagram_page_link_is_routed_to_instagram_read_and_never_fetched():
    """Each page kind, with and without a scheme: `nothing_found`, a note
    naming `instagram_read`, and no call to the provider at all. Positive
    control on the SAME fake: an ordinary page and an Instagram CDN file
    (not a page) still reach it."""
    fake = _FakeSearchProvider()
    tools = _tools(search=fake)
    for kind, url in _INSTAGRAM_PAGES.items():
        out = await tools["web_fetch"](url=url)
        assert out["status"] == "nothing_found", kind
        assert "instagram_read" in out["note"], kind
        assert url not in out["note"], kind
    assert fake.fetch_calls == []

    cdn = "https://scontent.cdninstagram.com/v/t51/synthetic.jpg"
    for url in ("https://example.com/a", cdn):
        assert (await tools["web_fetch"](url=url))["status"] == "ok", url
    assert fake.fetch_calls == ["https://example.com/a", cdn]


async def test_the_guard_holds_with_instagram_off_and_in_a_fire_turn():
    """This harness wires no social backend, so Instagram is OFF here, and
    the guard still answers: it does not depend on `instagram_read` being
    usable. In a fire turn (a reminder, a proactive turn) `instagram_read`
    refuses to open a post nobody asked about; without this guard,
    `web_fetch` would have been the way round that rule."""
    fake = _FakeSearchProvider()
    tools = _tools(search=fake, fire_turn=True)
    out = await tools["web_fetch"](url=_INSTAGRAM_PAGES["post"])

    assert out["status"] == "nothing_found"
    assert fake.fetch_calls == []
    assert (await tools["web_fetch"](url="https://example.com/a"))["status"] == "ok"


async def test_a_mobile_instagram_page_link_asks_for_the_www_link():
    """`m.instagram.com` is a host `instagram_read` refuses, so the note does
    not send her there: it asks for the www link. Nothing is fetched."""
    fake = _FakeSearchProvider()
    tools = _tools(search=fake)
    for url in ("https://m.instagram.com/reel/SynthReel01/", "m.instagram.com/p/SynthPost01/"):
        out = await tools["web_fetch"](url=url)
        assert out["status"] == "nothing_found", url
        assert "www.instagram.com" in out["note"], url
    assert fake.fetch_calls == []


# ---- an X post/profile or LinkedIn person/company link is x_read's/
# linkedin_read's. Every link below is synthetic.

_X_PAGES = {
    "post": "https://x.com/synth_user01/status/9000000000000000001",
    "profile": "https://twitter.com/synth_user02",
    "mobile_post": "https://mobile.x.com/synth_user03/status/9000000000000000003",
    "mobile_profile": "https://mobile.twitter.com/synth_user04",
    "www": "https://www.x.com/synth_user05",
    "no_scheme": "x.com/synth_user01/status/9000000000000000001",
    # An id-only post link (no username segment).
    "id_only_post": "https://x.com/i/web/status/9000000000000000006",
    # Anything after the post id is ignored (a photo index).
    "post_with_photo_segment": "https://x.com/synth_user01/status/9000000000000000001/photo/1",
    # Share junk in the query is dropped, never sent on.
    "post_with_query": "https://x.com/synth_user01/status/9000000000000000001?s=20",
    # `urlsplit().hostname` lowercases the host, so an upper-case paste reads
    # the same as the lower-case spelling.
    "uppercase_host": "https://X.COM/synth_user06",
}


async def test_an_x_link_is_routed_to_x_read_and_never_fetched():
    """Each X shape the parser accepts — a post, a profile, and every
    mobile/alternate host `x_links.X_HOSTS` already reads: `nothing_found`, a
    note naming `x_read`, and no call to the provider. Positive control on
    the SAME fake: X's shortener, X's own CDN, a reserved X page and a
    lookalike host still reach it."""
    fake = _FakeSearchProvider()
    tools = _tools(search=fake)
    for kind, url in _X_PAGES.items():
        out = await tools["web_fetch"](url=url)
        assert out["status"] == "nothing_found", kind
        assert "x_read" in out["note"], kind
        assert url not in out["note"], kind
    assert fake.fetch_calls == []

    still_fetched = (
        "https://t.co/synthShort1",  # a shortener, not X's own page
        "https://pbs.twimg.com/media/synthetic.jpg",  # X's own CDN file
        "https://x.com.evil.example/synth_user01",  # a lookalike host
        "https://x.com/explore",  # a reserved X page, not an account
        "https://example.com/a",
    )
    for url in still_fetched:
        assert (await tools["web_fetch"](url=url))["status"] == "ok", url
    assert fake.fetch_calls == list(still_fetched)


_LINKEDIN_PAGES = {
    "person": "https://www.linkedin.com/in/synth-person-01",
    "company": "https://linkedin.com/company/synth-company-01",
    "country_subdomain": "https://hk.linkedin.com/in/synth-person-02",
    "no_scheme": "linkedin.com/in/synth-person-01",
}


async def test_a_linkedin_person_or_company_link_is_routed_to_linkedin_read():
    fake = _FakeSearchProvider()
    tools = _tools(search=fake)
    for kind, url in _LINKEDIN_PAGES.items():
        out = await tools["web_fetch"](url=url)
        assert out["status"] == "nothing_found", kind
        assert "linkedin_read" in out["note"], kind
        assert url not in out["note"], kind
    assert fake.fetch_calls == []


async def test_a_mobile_linkedin_link_asks_for_the_www_link():
    """`m.linkedin.com` is a host the parser's own charset refuses (a country
    subdomain is exactly two letters, and `m` is one) — the same shape as
    Instagram's mobile host, so the note asks for the www link instead of
    naming `linkedin_read`. Nothing is fetched."""
    fake = _FakeSearchProvider()
    tools = _tools(search=fake)
    for url in (
        "https://m.linkedin.com/in/synth-person-03",
        "m.linkedin.com/company/synth-company-04",
    ):
        out = await tools["web_fetch"](url=url)
        assert out["status"] == "nothing_found", url
        assert "www.linkedin.com" in out["note"], url
    assert fake.fetch_calls == []


async def test_a_linkedin_post_and_other_unclaimed_linkedin_urls_still_fetch():
    """A LinkedIn post is never claimed: `linkedin_read` cannot read one from
    its link, and a public post page is something `web_fetch` may still be
    able to read. Same treatment as `lnkd.in`
    and LinkedIn's own CDN host."""
    fake = _FakeSearchProvider()
    tools = _tools(search=fake)
    still_fetched = (
        "https://www.linkedin.com/posts/synth-person-01_synthetic-activity-9000000000000000001/",
        "https://m.linkedin.com/posts/synth-person-01_synthetic-activity-9000000000000000002/",
        "https://lnkd.in/synthShort2",  # a shortener, not LinkedIn's own page
        "https://media.licdn.com/dms/image/synthetic.jpg",  # LinkedIn's own CDN file
        "https://example.com/a",
    )
    for url in still_fetched:
        assert (await tools["web_fetch"](url=url))["status"] == "ok", url
    assert fake.fetch_calls == list(still_fetched)


# ---- ADR 0015 §3: how to report a status rides in the result ----------------
# `web_search` shares this closure's harness (one `search_source`, both verbs),
# so its notes are pinned here beside `web_fetch`'s.

_DASHES = ("—", "–")


def _assert_noted(case: str, out: dict, status: str) -> None:
    assert out["status"] == status, (case, out)
    assert isinstance(out.get("note"), str) and out["note"].strip(), case
    assert not any(d in out["note"] for d in _DASHES), case


async def test_every_non_ok_web_status_carries_a_note():
    """Every non-ok path of both verbs, the provider's own included, so a path
    that loses its note (the reporting rule for that status) goes red."""
    empty = _tools(search=_FakeSearchProvider(
        search={"status": "nothing_found", "results": []}, fetch={"status": "nothing_found"},
    ))
    raising = _tools(search=_FakeSearchProvider(raises=True))
    unwired = _tools(search=None)
    from nora.infra.search._shared import UNAVAILABLE_FETCH, UNAVAILABLE_SEARCH
    down = _tools(search=_FakeSearchProvider(search=UNAVAILABLE_SEARCH, fetch=UNAVAILABLE_FETCH))
    search_cases = {
        "nothing_found/identifier": (empty, "call +85212345678"),
        "nothing_found/coordinate": (empty, "22.31,114.16"),
        "nothing_found/empty": (empty, "a real question"),
        "unavailable/unwired": (unwired, "a real question"),
        "unavailable/provider": (down, "a real question"),
    }
    for case, (tools, query) in search_cases.items():
        _assert_noted(f"web_search {case}", await tools["web_search"](query=query),
                      case.split("/")[0])
    fetch_cases = {
        "nothing_found/not_a_url": (empty, "example.com/a"),
        "nothing_found/instagram_page": (empty, "https://www.instagram.com/p/SynthPost01/"),
        "nothing_found/instagram_mobile_page": (empty, "https://m.instagram.com/p/SynthPost01/"),
        "nothing_found/x_page": (empty, "https://x.com/synth_user01/status/9000000000000000001"),
        "nothing_found/linkedin_page": (empty, "https://www.linkedin.com/in/synth-person-01"),
        "nothing_found/linkedin_mobile_page": (empty, "https://m.linkedin.com/in/synth-person-03"),
        "nothing_found/identifier": (empty, "https://example.com/?n=%2B85212345678"),
        "nothing_found/coordinate": (empty, "https://example.com/?at=22.31,114.16"),
        "nothing_found/dead": (empty, "https://example.com/a"),
        "unavailable/unwired": (unwired, "https://example.com/a"),
        "unavailable/raised": (raising, "https://example.com/a"),
        "unavailable/provider": (down, "https://example.com/a"),
    }
    for case, (tools, url) in fetch_cases.items():
        _assert_noted(f"web_fetch {case}", await tools["web_fetch"](url=url),
                      case.split("/")[0])


async def test_an_ok_web_note_comes_before_the_page_written_text():
    """The reading rules that left the descriptions (a stranger's words, give
    the link, say which page) ride with the `ok`, placed right after `status`
    so they are read before the text they are about."""
    tools = _tools(search=_FakeSearchProvider(
        search={"status": "ok", "results": [{"url": "https://a.example", "content": "x"}]},
        fetch={"status": "ok", "url": "https://a.example", "text": "the page"},
    ))
    found = await tools["web_search"](query="a real question")
    assert list(found)[:3] == ["status", "note", "results"]
    assert "`url`" in found["note"] and "stranger" in found["note"]
    page = await tools["web_fetch"](url="https://a.example")
    assert list(page)[:2] == ["status", "note"]
    assert page["text"] == "the page"
    assert "which page" in page["note"] and "stranger" in page["note"]
    # A page or snippet is partial: absence from it is not proof of absence.
    assert "not proof it doesn't exist" in found["note"]
    assert "not proof it doesn't exist" in page["note"]


async def test_a_file_link_handle_points_at_file_fetch_not_at_the_url():
    # The model is shown `[Google Doc link c41d09]`, never the URL; passing that
    # to web_fetch must not answer "ask them for the actual URL" and give up.
    # Brackets or not, the note names file_fetch and the code.
    fake = _FakeSearchProvider()
    tools = _tools(search=fake)
    for given in (
        "[Google Doc link c41d09]",
        "Google Sheet link C41D09",
        " [google slides link c41d09] ",
    ):
        out = await tools["web_fetch"](url=given)
        assert out["status"] == "nothing_found"
        assert "file_fetch" in out["note"] and "c41d09" in out["note"]
        # A shared picture or video is looked at, not read as text.
        assert "media_fetch" in out["note"]
        assert "actual URL" not in out["note"]
    assert fake.fetch_calls == []


async def test_a_bare_word_is_still_asked_for_its_url():
    out = await _tools(search=_FakeSearchProvider())["web_fetch"](url="c41d09")
    assert "actual URL" in out["note"]


async def test_a_link_built_around_a_handles_code_points_at_its_code():
    # Shown `[Google Drive link 491db6]`, she may send
    # `https://drive.google.com/file/d/491db6` or a folder URL to web_fetch;
    # neither may reach the provider, which would answer "couldn't read that
    # page".
    fake = _FakeSearchProvider()
    tools = _tools(search=fake)
    for given in ("https://drive.google.com/file/d/491db6",
                  "https://drive.google.com/drive/folders/491DB6"):
        out = await tools["web_fetch"](url=given)
        assert out["status"] == "nothing_found"
        assert "491db6" in out["note"] and "computer_import" in out["note"]
    assert fake.fetch_calls == []

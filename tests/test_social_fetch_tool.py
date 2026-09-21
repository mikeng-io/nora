"""`social_fetch`'s own closure (DEC-151) — the vocabulary validation, the
egress screen, the unwired/raising degradations, and that a refused/unwired
call never reaches the provider.

Mirrors `tests/test_web_fetch_tool.py`'s lightweight `_tools` pattern: built
through `ToolRegistry.specs(...)`, no database, no real bridge. The provider
itself is covered by `tests/test_social_bridge_client.py`; this file is about
the closure IN `nora.agent.tools` that sits in front of it — layer one of the
read-only boundary (the bridge's allowlist, layer two, is pinned by
`tests/unit/test_social_bridge_server.py`).
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


class _FakeSocialProvider:
    """`SocialProvider` double that records every `read` call it actually
    receives — the probe a screen that refuses BEFORE the call needs, the same
    shape `web_fetch`'s own `fetch_calls == []` tests use."""

    def __init__(self, *, raises: bool = False) -> None:
        self.read_calls: list[dict[str, Any]] = []
        self._raises = raises

    async def read(
        self, *, platform: str, what: str, target: str, limit: int,
    ) -> dict[str, Any]:
        self.read_calls.append(
            {"platform": platform, "what": what, "target": target, "limit": limit}
        )
        if self._raises:
            raise RuntimeError("boom")
        # `text`, NOT `content` — the Strands envelope invariant (a dict
        # carrying both `status` and `content` passes through unwrapped and
        # reads as "empty"; see nora/infra/social/bridge_client.py).
        # The live tag name (#314). A fake echoing a tag the real client no
        # longer emits is a fake that has stopped standing for it.
        return {"status": "ok", "text": "<social_post>the post</social_post>"}


def _room() -> Room:
    return Room(
        platform="signal", conversation_id="g1", roster_source=None,
        persons=_FakePersons(MILES), tagger=MILES,
    )


def _tools(*, social=None, metrics: Metrics | None = None) -> dict:
    registry = ToolRegistry(social=social)
    specs = registry.specs(
        store=_FakeStore(), conversation_id="g1", conversation_type="group",
        persons=_FakePersons(MILES), honcho=FakeMemoryStore(), deliverer=SinkSender(),
        metrics=metrics or Metrics(), room=_room(),
    )
    return {spec.name: spec.call for spec in specs}


async def test_unwired_answers_unavailable():
    tools = _tools(social=None)
    out = await tools["social_fetch"](platform="x", what="profile", target="nasa")
    assert out["status"] == "unavailable"


async def test_an_unknown_platform_is_nothing_found_and_not_echoed():
    fake = _FakeSocialProvider()
    tools = _tools(social=fake)
    out = await tools["social_fetch"](
        platform="tiktok", what="profile", target="nasa",
    )
    assert out["status"] == "nothing_found"
    assert "tiktok" not in str(out)
    assert fake.read_calls == []


async def test_an_unknown_what_is_nothing_found_and_not_echoed():
    fake = _FakeSocialProvider()
    tools = _tools(social=fake)
    out = await tools["social_fetch"](platform="x", what="like", target="nasa")
    assert out["status"] == "nothing_found"
    assert "like" not in str(out)
    assert fake.read_calls == []


async def test_instagram_post_is_unavailable_not_nothing_found():
    """The recorded limitation: OpenCLI 1.8.7 has no caption-by-URL command,
    so «I can't» is the honest answer — never «the post isn't there»."""
    fake = _FakeSocialProvider()
    tools = _tools(social=fake)
    out = await tools["social_fetch"](
        platform="instagram", what="post", target="https://www.instagram.com/p/abc/",
    )
    assert out["status"] == "unavailable"
    assert fake.read_calls == []


async def test_an_identifier_in_the_target_is_refused_before_the_provider_is_called():
    """`target` is an egress surface (design/14 §2.2), exactly like
    `web_search`'s query: the identifier is blocked BEFORE the call, and the
    string is not echoed back."""
    fake = _FakeSocialProvider()
    tools = _tools(social=fake)
    out = await tools["social_fetch"](
        platform="x", what="search", target="+85212345678",
    )
    assert out["status"] == "nothing_found"
    assert "+85212345678" not in str(out)
    assert fake.read_calls == []


async def test_a_coordinate_in_the_target_is_refused_before_the_provider_is_called():
    fake = _FakeSocialProvider()
    tools = _tools(social=fake)
    out = await tools["social_fetch"](
        platform="x", what="search", target="22.31,114.16",
    )
    assert out["status"] == "nothing_found"
    assert "22.31" not in str(out)
    assert fake.read_calls == []


async def test_a_bad_username_is_nothing_found():
    fake = _FakeSocialProvider()
    tools = _tools(social=fake)
    out = await tools["social_fetch"](
        platform="instagram", what="profile", target="not a username!",
    )
    assert out["status"] == "nothing_found"
    assert fake.read_calls == []


async def test_an_at_prefixed_username_is_refused():
    fake = _FakeSocialProvider()
    tools = _tools(social=fake)
    out = await tools["social_fetch"](platform="x", what="posts", target="@nasa")
    assert out["status"] == "nothing_found"
    assert fake.read_calls == []


async def test_an_x_post_url_is_parsed_to_its_id():
    fake = _FakeSocialProvider()
    tools = _tools(social=fake)
    out = await tools["social_fetch"](
        platform="x", what="post", target="https://x.com/nasa/status/1234567890",
    )
    assert out["status"] == "ok"
    assert fake.read_calls[0]["target"] == "1234567890"


async def test_a_twitter_com_post_url_is_parsed_too():
    fake = _FakeSocialProvider()
    tools = _tools(social=fake)
    out = await tools["social_fetch"](
        platform="x", what="post",
        target="https://twitter.com/nasa/status/1234567890",
    )
    assert out["status"] == "ok"
    assert fake.read_calls[0]["target"] == "1234567890"


async def test_a_post_url_with_query_junk_is_parsed_to_its_id():
    fake = _FakeSocialProvider()
    tools = _tools(social=fake)
    out = await tools["social_fetch"](
        platform="x", what="post",
        target="https://x.com/nasa/status/1234567890?s=20&t=abc",
    )
    assert out["status"] == "ok"
    assert fake.read_calls[0]["target"] == "1234567890"


async def test_a_bare_numeric_post_id_is_accepted():
    fake = _FakeSocialProvider()
    tools = _tools(social=fake)
    out = await tools["social_fetch"](platform="x", what="post", target="1234567890")
    assert out["status"] == "ok"
    assert fake.read_calls[0]["target"] == "1234567890"


async def test_a_non_post_url_is_nothing_found():
    fake = _FakeSocialProvider()
    tools = _tools(social=fake)
    out = await tools["social_fetch"](
        platform="x", what="post", target="https://example.com/not-a-tweet",
    )
    assert out["status"] == "nothing_found"
    assert fake.read_calls == []


async def test_an_empty_search_query_is_nothing_found():
    fake = _FakeSocialProvider()
    tools = _tools(social=fake)
    out = await tools["social_fetch"](platform="x", what="search", target="   ")
    assert out["status"] == "nothing_found"
    assert fake.read_calls == []


async def test_an_overlong_search_query_is_nothing_found():
    fake = _FakeSocialProvider()
    tools = _tools(social=fake)
    out = await tools["social_fetch"](
        platform="instagram", what="search", target="q" * 201,
    )
    assert out["status"] == "nothing_found"
    assert fake.read_calls == []


async def test_a_raising_provider_degrades_to_unavailable():
    fake = _FakeSocialProvider(raises=True)
    tools = _tools(social=fake)
    out = await tools["social_fetch"](platform="x", what="profile", target="nasa")
    assert out["status"] == "unavailable"


async def test_the_ok_path_passes_platform_what_target_limit_through():
    fake = _FakeSocialProvider()
    tools = _tools(social=fake)
    out = await tools["social_fetch"](
        platform="instagram", what="search", target="hk hiking", limit=7,
    )
    assert out["status"] == "ok"
    assert fake.read_calls == [
        {"platform": "instagram", "what": "search", "target": "hk hiking", "limit": 7}
    ]


async def test_limit_is_clamped_to_the_ceiling():
    fake = _FakeSocialProvider()
    tools = _tools(social=fake)
    await tools["social_fetch"](platform="x", what="posts", target="nasa", limit=100)
    assert fake.read_calls[0]["limit"] == 20


async def test_limit_is_clamped_to_the_floor():
    fake = _FakeSocialProvider()
    tools = _tools(social=fake)
    await tools["social_fetch"](platform="x", what="posts", target="nasa", limit=0)
    assert fake.read_calls[0]["limit"] == 1


# ------------------------------------------------------- the `story` surface
#
# Added 2026-09-22. The row shipped with ZERO tests through this closure:
# nothing exercised the `x story` note, the person-id shape check, the story
# branch reaching the provider, or what is counted. Every assertion below is
# about the TOOL layer — the backend's own three-plus-two states live in
# tests/contract/test_social_provider_contract.py and
# tests/unit/test_instagram_social_client.py.


async def test_a_story_on_x_is_unavailable_with_its_own_note():
    """X has no story concept at all, so «I can't» — never «there's nothing
    up», which would be a claim about an account on a platform that has no
    such thing. It gets its OWN sentence rather than the instagram/post one:
    "I can't read Instagram posts yet" is false about X and about stories.
    """
    fake = _FakeSocialProvider()
    tools = _tools(social=fake)
    out = await tools["social_fetch"](platform="x", what="story", target="miles")
    assert out["status"] == "unavailable"
    assert "Instagram" in out["note"] and "X doesn't have" in out["note"]
    assert fake.read_calls == []


async def test_a_story_target_that_is_not_a_person_id_is_ambiguous():
    """NOT `nothing_found`. This tool's own docstring defines that word as
    «the read ran and there was nothing», and nothing ran: no index was
    consulted, no bridge was called, no account was looked at. `ambiguous` is
    the declared word for exactly this — «I don't know which account that is,
    so I didn't look» — and the model says a different sentence for each.
    """
    fake = _FakeSocialProvider()
    tools = _tools(social=fake)
    out = await tools["social_fetch"](
        platform="instagram", what="story", target="not a person id!",
    )
    assert out["status"] == "ambiguous"
    assert fake.read_calls == []


async def test_a_person_id_too_long_for_the_shape_check_is_ambiguous_too():
    """`_SOCIAL_PERSON_ID_RE` caps at 64 characters and `persons_file` puts no
    length bound on a person id, so a LEGAL person can land here. That is the
    case that makes the word matter: «I don't know which account that is» is
    true of a 70-character person id; «there's nothing there» is not.
    """
    fake = _FakeSocialProvider()
    tools = _tools(social=fake)
    out = await tools["social_fetch"](
        platform="instagram", what="story", target="m" * 70,
    )
    assert out["status"] == "ambiguous"
    assert fake.read_calls == []


async def test_a_well_shaped_story_target_reaches_the_provider_trimmed():
    """The happy path through the branch — and that `target` arrives as the
    person id and nothing else. A bare igid typed into this slot also passes
    the charset (digits are person-id characters) and then fails to RESOLVE
    one layer down, which is the fail-closed direction: the model is told it
    doesn't know whose account that is, rather than being handed a read of
    whoever that number turns out to be."""
    fake = _FakeSocialProvider()
    tools = _tools(social=fake)
    out = await tools["social_fetch"](
        platform="instagram", what="story", target="  miles  ", limit=1,
    )
    assert out["status"] == "ok"
    assert fake.read_calls == [
        {"platform": "instagram", "what": "story", "target": "miles", "limit": 1}
    ]


async def test_the_story_row_is_counted_under_its_own_platform_and_what():
    """`_record` carries `platform`/`what` on the way out, and they are safe
    on the line only because both were validated against closed vocabularies
    first. A story read counted as anything else is a row that cannot be seen
    separately from the OpenCLI ones it does not share a backend with."""
    metrics = Metrics()
    fake = _FakeSocialProvider()
    tools = _tools(social=fake, metrics=metrics)
    await tools["social_fetch"](
        platform="instagram", what="story", target="miles",
    )
    assert metrics.get("nora_tool_calls_total", tool="social_fetch", status="ok") == 1
    # And the refusing branch is counted under the status it actually
    # returned — the whole point of changing it: a dashboard that reads this
    # series must not see "I didn't know whose account" as "I looked".
    await tools["social_fetch"](
        platform="instagram", what="story", target="not a person id!",
    )
    assert metrics.get(
        "nora_tool_calls_total", tool="social_fetch", status="ambiguous",
    ) == 1
    assert metrics.get(
        "nora_tool_calls_total", tool="social_fetch", status="nothing_found",
    ) == 0

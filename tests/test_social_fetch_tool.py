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

import pytest

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
        platform="x", what="profile", target="not a username!",
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
        platform="x", what="search", target="q" * 201,
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
        platform="x", what="search", target="hk hiking", limit=7,
    )
    assert out["status"] == "ok"
    assert fake.read_calls == [
        {"platform": "x", "what": "search", "target": "hk hiking", "limit": 7}
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


# ------------------------------------------- every Instagram read moved out
#
# 2026-09-24 (design/instagram-adoption/18): Instagram profile and story left
# this tool for `instagram_read` (P5), and Instagram account search left for
# `instagram_search` (P11). `social_fetch` is X only. Every Instagram request
# is answered by THIS tool, `unavailable` («not here»), names the two tools
# that read Instagram now, and never reaches the provider: a request that
# reached OpenCLI for Instagram would be a second tool reading the same thing
# over the backend `15` took Instagram away from.


async def test_a_story_is_redirected_to_instagram_read_and_never_sent():
    """On either platform. «There's nothing on his story» (`nothing_found`)
    would be a claim about an account nobody looked at; the honest answer is
    which tool reads stories now."""
    fake = _FakeSocialProvider()
    tools = _tools(social=fake)
    for platform in ("instagram", "x"):
        out = await tools["social_fetch"](platform=platform, what="story", target="miles")
        assert out["status"] == "unavailable"
        assert "instagram_read" in out["note"]
    assert fake.read_calls == []


@pytest.mark.parametrize(
    ("what", "target"),
    [
        ("search", "hk hiking"),
        ("profile", "example_friend"),
        ("posts", "example_friend"),
        ("post", "https://www.instagram.com/p/abc/"),
    ],
)
async def test_every_instagram_request_is_redirected_and_never_sent(what, target):
    """The P11 cutover, tool side. The note names BOTH Instagram tools, so a
    model that reached for the wrong one is told which is right."""
    fake = _FakeSocialProvider()
    tools = _tools(social=fake)
    out = await tools["social_fetch"](platform="instagram", what=what, target=target)
    assert out["status"] == "unavailable"
    assert "instagram_search" in out["note"]
    assert "instagram_read" in out["note"]
    assert fake.read_calls == []


async def test_an_instagram_search_is_redirected_while_an_x_search_is_sent():
    """The negative and its positive control on the SAME query: `instagram`
    is not sent, `x` is. Also case-insensitive on the platform name, so
    «Instagram» cannot slip past the redirect into `nothing_found`."""
    fake = _FakeSocialProvider()
    tools = _tools(social=fake)
    for platform in ("instagram", "Instagram"):
        out = await tools["social_fetch"](platform=platform, what="search", target="hk hiking")
        assert out["status"] == "unavailable"
        assert "instagram_search" in out["note"]
    assert fake.read_calls == []
    assert (await tools["social_fetch"](
        platform="x", what="search", target="hk hiking",
    ))["status"] == "ok"
    assert fake.read_calls == [
        {"platform": "x", "what": "search", "target": "hk hiking", "limit": 5}
    ]


async def test_a_moved_row_is_counted_as_unavailable_not_as_a_miss():
    """A dashboard reading this series must not see «wrong tool» as «I
    looked and found nothing»."""
    metrics = Metrics()
    tools = _tools(social=_FakeSocialProvider(), metrics=metrics)
    await tools["social_fetch"](platform="instagram", what="story", target="miles")
    await tools["social_fetch"](platform="instagram", what="profile", target="miles")
    await tools["social_fetch"](platform="instagram", what="search", target="miles")
    assert metrics.get(
        "nora_tool_calls_total", tool="social_fetch", status="unavailable",
    ) == 3
    assert metrics.get(
        "nora_tool_calls_total", tool="social_fetch", status="nothing_found",
    ) == 0



@pytest.mark.parametrize(
    "platform", ["instagram", "Instagram", " INSTAGRAM ", "ig", "IG", "insta", " Insta"],
)
async def test_every_instagram_name_is_redirected_to_both_instagram_tools(platform):
    """`platform` is normalized once (`.strip().lower()`), and every name a
    model reaches for when it means Instagram gets the redirect. Before the
    normalization, `ig` fell through to «I can only read X», which reads as
    «Instagram can't be read at all»."""
    fake = _FakeSocialProvider()
    tools = _tools(social=fake)
    out = await tools["social_fetch"](platform=platform, what="search", target="hk hiking")
    assert out["status"] == "unavailable"
    assert "instagram_read" in out["note"] and "instagram_search" in out["note"]
    assert fake.read_calls == []


@pytest.mark.parametrize("platform", ["x", "X", " x ", "twitter", "Twitter"])
async def test_every_x_name_is_read_as_x_and_sent_as_x(platform):
    """The positive half: X under any casing or padding is served, and the
    backend is handed the canonical `x`, never the model's spelling."""
    fake = _FakeSocialProvider()
    tools = _tools(social=fake)
    out = await tools["social_fetch"](platform=platform, what="profile", target="nasa")
    assert out["status"] == "ok"
    assert fake.read_calls == [
        {"platform": "x", "what": "profile", "target": "nasa", "limit": 5}
    ]


async def test_an_unknown_platform_names_both_instagram_tools():
    fake = _FakeSocialProvider()
    tools = _tools(social=fake)
    out = await tools["social_fetch"](platform="tiktok", what="profile", target="nasa")
    assert out["status"] == "nothing_found"
    assert "instagram_read" in out["note"] and "instagram_search" in out["note"]
    assert fake.read_calls == []

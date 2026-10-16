"""`x_read` and `x_search` (DEC-201): the closures in `nora.agent.tools`.

Layer one of the read-only boundary: which inputs each tool accepts, the
egress screen on exactly what leaves, the unwired and raising degradations,
and that a refused call never reaches the provider. The bridge's allowlist
(layer two) is `tests/unit/test_social_bridge_server.py`, and the three layers
against each other are `test_r86_social_reads_are_read_only_at_three_layers`.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest

from nora.agent.dreamer import UNIT_SAFE_TOOLS
from nora.agent.registry import ToolRegistry
from nora.agent.room import Room
from nora.agent.tools import NATIVE_TOOLS
from nora.domain.models import PersonRef
from nora.obs.metrics import Metrics
from nora.testing import FakeMemoryStore, FakeSocialProvider
from tests.fakes import SinkSender

MILES = PersonRef(person_id="miles", name="Miles Novak", honcho_peer_id="miles")
_OK = {"status": "ok", "text": "<social_post>the post</social_post>"}
_X_ROWS = frozenset({("x", "post"), ("x", "profile"), ("x", "posts"), ("x", "search")})


class _NoStore:
    async def recent(self, *, conversation_id: str, limit: int):
        return []


class _Persons:
    def __init__(self) -> None:
        self.by_person = {MILES.person_id: MILES}

    def resolve(self, platform: str, sender_id: str):
        return None


class _RaisingSocial:
    """Only fails: the fault injector for the never-raises belt."""

    async def read(self, *, platform: str, what: str, target: str, limit: int) -> dict[str, Any]:
        raise RuntimeError("boom")


def _specs(social) -> tuple:
    room = Room(
        platform="signal", conversation_id="g1", roster_source=None,
        persons=_Persons(), tagger=MILES,
    )
    return ToolRegistry(social=social).specs(
        store=_NoStore(), conversation_id="g1", conversation_type="group",
        persons=_Persons(), honcho=FakeMemoryStore(), deliverer=SinkSender(),
        metrics=Metrics(), room=room,
    )


def _tools(social=None) -> dict:
    return {s.name: s.call for s in _specs(social)}


@pytest.fixture
def social() -> FakeSocialProvider:
    return FakeSocialProvider(dict.fromkeys(_X_ROWS, _OK))


# ---- x_read: which read, from which input ---------------------------------


@pytest.mark.parametrize(
    ("kwargs", "sent"),
    [
        ({"link": "https://x.com/nasa/status/42?s=20"}, ("x", "post", "42")),
        ({"link": "twitter.com/nasa/status/42"}, ("x", "post", "42")),
        ({"link": "https://x.com/nasa"}, ("x", "profile", "nasa")),
        ({"link": "https://x.com/nasa", "what": "profile"}, ("x", "profile", "nasa")),
        ({"link": "https://x.com/nasa", "what": "posts"}, ("x", "posts", "nasa")),
        ({"handle": "nasa", "what": "profile"}, ("x", "profile", "nasa")),
        ({"handle": "@nasa", "what": "posts"}, ("x", "posts", "nasa")),
        ({"handle": "  nasa ", "what": "posts"}, ("x", "posts", "nasa")),
    ],
)
async def test_the_input_decides_the_read(social, kwargs, sent):
    out = await _tools(social)["x_read"](**kwargs)
    assert out["status"] == "ok"
    assert [c[:3] for c in social.calls] == [sent]


async def test_an_ok_carries_how_to_report_it(social):
    out = await _tools(social)["x_read"](handle="nasa", what="profile")
    assert out["text"] == _OK["text"]
    assert "strangers" in out["note"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"handle": "nasa", "link": "https://x.com/nasa", "what": "profile"},
        {"handle": "  ", "what": "profile"},
        # A handle needs `what`, and a post is read from its link only.
        {"handle": "nasa"},
        {"handle": "nasa", "what": "post"},
        {"handle": "nasa", "what": "story"},
        {"handle": "not a name!", "what": "profile"},
        {"handle": "na.sa", "what": "profile"},
        # A link that names nothing. A link to another network is not here: it is a registry
        # redirect (`nothing_found`), not a bad input — see
        # test_an_instagram_link_names_the_tool_that_reads_it.
        {"link": "https://x.com/explore"},
        {"link": "https://evil.example/nasa/status/42"},
        # `what` beside a link must name the same read.
        {"link": "https://x.com/nasa/status/42", "what": "posts"},
        {"link": "https://x.com/nasa/status/42", "what": "profile"},
        {"link": "https://x.com/nasa", "what": "story"},
    ],
)
async def test_a_bad_input_is_unavailable_and_nothing_is_sent(social, kwargs):
    out = await _tools(social)["x_read"](**kwargs)
    assert out["status"] == "unavailable"
    assert out["note"]
    assert social.calls == []


async def test_an_instagram_link_names_the_tool_that_reads_it(social):
    out = await _tools(social)["x_read"](link="https://www.instagram.com/p/SynthPost01/")
    assert out["status"] == "nothing_found"
    assert "instagram_read" in out["note"]
    assert social.calls == []


async def test_a_linkedin_link_names_the_tool_that_reads_it(social):
    """The registry lookup catches a LinkedIn link too, not only Instagram."""
    out = await _tools(social)["x_read"](link="https://www.linkedin.com/in/williamhgates/")
    assert out["status"] == "nothing_found"
    assert "linkedin_read" in out["note"]
    assert social.calls == []


async def test_no_input_is_echoed_back(social):
    out = await _tools(social)["x_read"](handle="secret_value_x", what="story")
    assert "secret_value_x" not in json.dumps(out)


async def test_the_query_junk_of_a_link_never_leaves(social):
    await _tools(social)["x_read"](link="https://x.com/nasa/status/42?t=secret&s=20")
    assert social.calls == [("x", "post", "42", 5)]


# ---- x_search ----------------------------------------------------------------


async def test_a_search_sends_the_trimmed_query(social):
    out = await _tools(social)["x_search"](query="  nasa launch  ", limit=3)
    assert out["status"] == "ok"
    assert social.calls == [("x", "search", "nasa launch", 3)]


@pytest.mark.parametrize("query", ["", "   ", "q" * 201, "--help", "-f json"])
async def test_an_empty_or_overlong_query_is_unavailable_and_not_sent(social, query):
    out = await _tools(social)["x_search"](query=query)
    assert out["status"] == "unavailable"
    assert social.calls == []


@pytest.mark.parametrize("query", ["+85212345678", "22.31,114.16", "%2B85212345678"])
async def test_an_identifier_or_coordinate_is_refused_and_not_sent(social, query):
    out = await _tools(social)["x_search"](query=query)
    assert out["status"] == "refused"
    assert social.calls == []


# ---- both: limits, degradation ----------------------------------------------


@pytest.mark.parametrize(("given", "sent"), [(0, 1), (-3, 1), (99, 20), ("x", 5)])
async def test_limit_is_clamped(social, given, sent):
    await _tools(social)["x_read"](handle="nasa", what="posts", limit=given)
    await _tools(social)["x_search"](query="nasa", limit=given)
    assert [c[3] for c in social.calls] == [sent, sent]


@pytest.mark.parametrize(
    ("tool", "kwargs"),
    [("x_read", {"handle": "nasa", "what": "profile"}), ("x_search", {"query": "nasa"})],
)
async def test_unwired_is_unavailable(tool, kwargs):
    out = await _tools(None)[tool](**kwargs)
    assert out["status"] == "unavailable"
    assert "X" in out["note"]


@pytest.mark.parametrize(
    ("tool", "kwargs"),
    [("x_read", {"handle": "nasa", "what": "profile"}), ("x_search", {"query": "nasa"})],
)
async def test_a_raising_provider_degrades_to_unavailable(tool, kwargs):
    out = await _tools(_RaisingSocial())[tool](**kwargs)
    assert out["status"] == "unavailable"


async def test_the_backend_s_own_miss_is_passed_through_with_its_note():
    social = FakeSocialProvider(serves=_X_ROWS)  # serves, nothing behind it
    out = await _tools(social)["x_read"](handle="nasa", what="profile")
    assert out["status"] == "nothing_found"


# ---- the tool rows and the spec Strands sends --------------------------------


@pytest.mark.parametrize("name", ["x_read", "x_search"])
def test_the_native_tool_row(name):
    row = NATIVE_TOOLS[name]
    assert row.read_only is True
    assert row.side_effect_state == "none"
    assert row.output_contract == "three_state"
    assert row.source_tier == "web"
    assert row.level == 3
    assert name in UNIT_SAFE_TOOLS


@pytest.mark.parametrize("name", ["x_read", "x_search"])
def test_the_description_meets_adr_0015(name):
    """A declared level, the text Strands sends within its ceiling, and no em
    or en dash or non-Latin character anywhere in the spec it sends."""
    import tiktoken

    from nora.agent.ports import adapt

    enc = tiktoken.get_encoding("cl100k_base")
    spec = next(s for s in _specs(None) if s.name == name)
    sent = adapt(spec).tool_spec
    assert len(enc.encode(sent["description"])) <= 320
    whole = json.dumps(sent, ensure_ascii=False)
    assert not re.search("[–—]", whole)
    assert not re.search("[^\x00-ɏ]", whole)


def test_x_read_s_schema_has_what_as_an_enum_and_nothing_required():
    from nora.agent.ports import adapt

    spec = next(s for s in _specs(None) if s.name == "x_read")
    schema = adapt(spec).tool_spec["inputSchema"]["json"]
    assert set(schema["properties"]) == {"what", "handle", "link", "url", "limit"}
    assert not schema.get("required")
    assert "posts" in json.dumps(schema["properties"]["what"])


def test_social_fetch_is_gone():
    assert "social_fetch" not in NATIVE_TOOLS
    assert "social_fetch" not in {s.name for s in _specs(None)}
    assert "social_fetch" not in UNIT_SAFE_TOOLS


# ---- `url` is `link` ----------


@pytest.mark.parametrize(
    ("kwargs", "sent"),
    [
        ({"url": "https://x.com/nasa/status/42?s=20"}, ("x", "post", "42")),
        ({"url": "https://x.com/nasa", "what": "posts"}, ("x", "posts", "nasa")),
        # Two spellings of one link agree: the parser judges them, not the text.
        ({"link": "twitter.com/nasa/status/42", "url": "https://x.com/nasa/status/42?s=20"},
         ("x", "post", "42")),
    ],
)
async def test_url_reads_as_link(social, kwargs, sent):
    out = await _tools(social)["x_read"](**kwargs)
    assert out["status"] == "ok"
    assert [c[:3] for c in social.calls] == [sent]


async def test_url_and_link_that_differ_read_nothing(social):
    out = await _tools(social)["x_read"](
        link="https://x.com/nasa/status/42", url="https://x.com/esa/status/7",
    )
    assert out["status"] == "unavailable"
    assert "as link" in out["note"]
    assert "nasa" not in out["note"] and "esa" not in out["note"]
    assert social.calls == []

"""`linkedin_read` and `linkedin_search` (DEC-201).

Layer one of the read-only boundary for LinkedIn: which inputs each tool
accepts, the asked-turn rule (neither runs in a fire turn or a Dreamer pass),
the egress screen on exactly what leaves, the `[social.linkedin]` switch, and
that a refused call never reaches the provider. The bridge's allowlist is
`tests/unit/test_social_bridge_server.py`; the three layers against each other
are `test_r86_social_reads_are_read_only_at_three_layers`.
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
_OK = {"status": "ok", "text": "<social_post>a profile</social_post>"}
_ROWS = frozenset({
    ("linkedin", "profile"), ("linkedin", "experience"), ("linkedin", "posts"),
    ("linkedin", "company"), ("linkedin", "search"),
})


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


def _specs(linkedin, **turn) -> tuple:
    room = Room(
        platform="signal", conversation_id="g1", roster_source=None,
        persons=_Persons(), tagger=MILES,
    )
    return ToolRegistry(social=linkedin, linkedin=linkedin).specs(
        store=_NoStore(), conversation_id="g1", conversation_type="group",
        persons=_Persons(), honcho=FakeMemoryStore(), deliverer=SinkSender(),
        metrics=Metrics(), room=room, **turn,
    )


def _tools(linkedin=None, **turn) -> dict:
    return {s.name: s.call for s in _specs(linkedin, **turn)}


@pytest.fixture
def social() -> FakeSocialProvider:
    return FakeSocialProvider(dict.fromkeys(_ROWS, _OK))


# ---- linkedin_read: which read, from which input ----------------------------


@pytest.mark.parametrize(
    ("kwargs", "sent"),
    [
        ({"link": "https://www.linkedin.com/in/williamhgates/?trk=x"},
         ("linkedin", "profile", "williamhgates")),
        ({"link": "linkedin.com/in/williamhgates"}, ("linkedin", "profile", "williamhgates")),
        ({"link": "https://www.linkedin.com/in/williamhgates/", "what": "experience"},
         ("linkedin", "experience", "williamhgates")),
        ({"link": "https://www.linkedin.com/in/williamhgates/", "what": "posts"},
         ("linkedin", "posts", "williamhgates")),
        ({"link": "https://www.linkedin.com/company/nvidia/"}, ("linkedin", "company", "nvidia")),
        ({"link": "https://www.linkedin.com/company/nvidia/", "what": "company"},
         ("linkedin", "company", "nvidia")),
        ({"handle": "williamhgates", "what": "profile"}, ("linkedin", "profile", "williamhgates")),
        ({"handle": " williamhgates ", "what": "posts"}, ("linkedin", "posts", "williamhgates")),
    ],
)
async def test_the_input_decides_the_read(social, kwargs, sent):
    out = await _tools(social)["linkedin_read"](**kwargs)
    assert out["status"] == "ok"
    assert [c[:3] for c in social.calls] == [sent]
    assert "account owner" in out["note"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"handle": "williamhgates", "link": "https://www.linkedin.com/in/williamhgates/"},
        # A handle needs `what`, and a company is read from its link only.
        {"handle": "williamhgates"},
        {"handle": "nvidia", "what": "company"},
        {"handle": "william gates", "what": "profile"},
        {"handle": "ab", "what": "profile"},
        {"handle": "williamhgates", "what": "story"},
        # `what` must fit the link.
        {"link": "https://www.linkedin.com/in/williamhgates/", "what": "company"},
        {"link": "https://www.linkedin.com/company/nvidia/", "what": "posts"},
        # Pages that name nothing. A link to another network is a registry
        # redirect (`nothing_found`), not a bad input — see
        # test_another_network_s_link_names_the_tool_that_reads_it.
        {"link": "https://www.linkedin.com/jobs/view/1/"},
        {"link": "https://evil.example/in/williamhgates/"},
        # One post cannot be read from its link.
        {"link": "https://www.linkedin.com/posts/williamhgates_ai-activity-1-a/"},
        # A leading dash would reach OpenCLI as a flag.
        {"link": "https://www.linkedin.com/company/-f/"},
        {"handle": "-f", "what": "profile"},
    ],
)
async def test_a_bad_input_is_unavailable_and_nothing_is_sent(social, kwargs):
    out = await _tools(social)["linkedin_read"](**kwargs)
    assert out["status"] == "unavailable"
    assert out["note"]
    assert social.calls == []


@pytest.mark.parametrize(
    ("link", "tool"),
    [("https://x.com/nasa", "x_read"),
     ("https://www.instagram.com/p/SynthPost01/", "instagram_read")],
)
async def test_another_network_s_link_names_the_tool_that_reads_it(social, link, tool):
    out = await _tools(social)["linkedin_read"](link=link)
    assert out["status"] == "nothing_found"
    assert tool in out["note"]
    assert social.calls == []


async def test_a_post_link_says_it_cannot_be_read_and_offers_the_author(social):
    out = await _tools(social)["linkedin_read"](
        link="https://www.linkedin.com/feed/update/urn:li:activity:123/",
    )
    assert "post" in out["note"] and "profile link" in out["note"]


async def test_no_input_is_echoed_back(social):
    out = await _tools(social)["linkedin_read"](handle="secret-value-x", what="story")
    assert "secret-value-x" not in json.dumps(out)


async def test_the_query_junk_of_a_link_never_leaves(social):
    await _tools(social)["linkedin_read"](
        link="https://www.linkedin.com/in/williamhgates/?trk=secret&x=1",
    )
    assert social.calls == [("linkedin", "profile", "williamhgates", 5)]


# ---- the asked-turn rule -----------------------------------------------------


@pytest.mark.parametrize("turn", [{"fire_turn": True}, {"dream_adaptor": "policy"}])
@pytest.mark.parametrize(
    ("tool", "kwargs"),
    [
        ("linkedin_read", {"handle": "williamhgates", "what": "profile"}),
        ("linkedin_read", {"link": "https://www.linkedin.com/company/nvidia/"}),
        ("linkedin_read", {"url": "https://www.linkedin.com/company/nvidia/"}),
        # Refused BEFORE the url alias is judged: two different links in an
        # unasked turn are `refused`, never the alias's `unavailable`.
        ("linkedin_read", {"link": "https://www.linkedin.com/in/williamhgates/",
                           "url": "https://www.linkedin.com/company/nvidia/"}),
        ("linkedin_search", {"query": "satya nadella"}),
    ],
)
async def test_a_turn_nobody_asked_for_is_refused_before_anything_is_sent(
    social, turn, tool, kwargs,
):
    """A LinkedIn read is a profile visit LinkedIn can show its owner, and a
    search spends a monthly limit: neither runs in a fire turn or a Dreamer
    pass."""
    out = await _tools(social, **turn)[tool](**kwargs)
    assert out["status"] == "refused"
    assert "asks" in out["note"]
    assert social.calls == []


def test_neither_linkedin_tool_is_unit_safe():
    assert "linkedin_read" not in UNIT_SAFE_TOOLS
    assert "linkedin_search" not in UNIT_SAFE_TOOLS


# ---- linkedin_search ---------------------------------------------------------


async def test_a_search_sends_the_trimmed_query(social):
    out = await _tools(social)["linkedin_search"](query="  satya nadella  ", limit=3)
    assert out["status"] == "ok"
    assert social.calls == [("linkedin", "search", "satya nadella", 3)]
    assert "impostors" in out["note"] and "never pick" in out["note"]


@pytest.mark.parametrize(("given", "sent"), [(0, 1), (99, 10), ("x", 5)])
async def test_search_limit_is_clamped_to_people_search_s_range(social, given, sent):
    await _tools(social)["linkedin_search"](query="someone", limit=given)
    assert social.calls[0][3] == sent


@pytest.mark.parametrize("query", ["", "  ", "q" * 201, "--limit", "-f json"])
async def test_an_empty_overlong_or_flag_shaped_query_is_unavailable_and_not_sent(social, query):
    out = await _tools(social)["linkedin_search"](query=query)
    assert out["status"] == "unavailable"
    assert social.calls == []


@pytest.mark.parametrize("query", ["+85212345678", "22.31,114.16", "%2B85212345678"])
async def test_an_identifier_or_coordinate_is_refused_and_not_sent(social, query):
    """A phone number in a PEOPLE search would be looked up against real
    people."""
    out = await _tools(social)["linkedin_search"](query=query)
    assert out["status"] == "refused"
    assert social.calls == []


# ---- the switch, and degradation ---------------------------------------------


@pytest.mark.parametrize(
    ("tool", "kwargs"),
    [("linkedin_read", {"handle": "williamhgates", "what": "profile"}),
     ("linkedin_search", {"query": "someone"})],
)
async def test_switched_off_is_not_set_up_and_sends_nothing(tool, kwargs):
    """`[social.linkedin] enabled` false: the composition hands the tools no
    source, while X keeps its own (`social=`)."""
    x_only = FakeSocialProvider(dict.fromkeys(_ROWS, _OK))
    room = Room(platform="signal", conversation_id="g1", roster_source=None,
                persons=_Persons(), tagger=MILES)
    specs = ToolRegistry(social=x_only, linkedin=None).specs(
        store=_NoStore(), conversation_id="g1", conversation_type="group",
        persons=_Persons(), honcho=FakeMemoryStore(), deliverer=SinkSender(),
        metrics=Metrics(), room=room,
    )
    out = await next(s.call for s in specs if s.name == tool)(**kwargs)
    assert out["status"] == "unavailable"
    assert "isn't set up" in out["note"]
    assert x_only.calls == []


@pytest.mark.parametrize(
    ("tool", "kwargs"),
    [("linkedin_read", {"handle": "williamhgates", "what": "profile"}),
     ("linkedin_search", {"query": "someone"})],
)
async def test_a_raising_provider_degrades_to_unavailable(tool, kwargs):
    out = await _tools(_RaisingSocial())[tool](**kwargs)
    assert out["status"] == "unavailable"


# ---- the tool rows and the spec Strands sends --------------------------------


@pytest.mark.parametrize(("name", "budget"), [("linkedin_read", 2), ("linkedin_search", 1)])
def test_the_native_tool_row(name, budget):
    row = NATIVE_TOOLS[name]
    assert row.budget == budget
    assert row.read_only is True
    assert row.side_effect_state == "none"
    assert row.cacheable is False
    assert row.output_contract == "three_state"
    assert row.level == 3


@pytest.mark.parametrize("name", ["linkedin_read", "linkedin_search"])
def test_the_description_meets_adr_0015(name):
    import tiktoken

    from nora.agent.ports import adapt

    enc = tiktoken.get_encoding("cl100k_base")
    spec = next(s for s in _specs(None) if s.name == name)
    sent = adapt(spec).tool_spec
    assert len(enc.encode(sent["description"])) <= 320
    whole = json.dumps(sent, ensure_ascii=False)
    assert not re.search("[–—]", whole)
    assert not re.search("[^\x00-ɏ]", whole)


# ---- `url` is `link` ----------


@pytest.mark.parametrize(
    ("kwargs", "sent"),
    [
        ({"url": "https://www.linkedin.com/in/williamhgates/?trk=x"},
         ("linkedin", "profile", "williamhgates")),
        ({"url": "https://www.linkedin.com/company/nvidia/"}, ("linkedin", "company", "nvidia")),
        ({"link": "linkedin.com/in/williamhgates",
          "url": "https://www.linkedin.com/in/williamhgates/?trk=x"},
         ("linkedin", "profile", "williamhgates")),
    ],
)
async def test_url_reads_as_link(social, kwargs, sent):
    out = await _tools(social)["linkedin_read"](**kwargs)
    assert out["status"] == "ok"
    assert [c[:3] for c in social.calls] == [sent]


async def test_url_and_link_that_differ_read_nothing(social):
    out = await _tools(social)["linkedin_read"](
        link="https://www.linkedin.com/in/williamhgates/",
        url="https://www.linkedin.com/company/nvidia/",
    )
    assert out["status"] == "unavailable"
    assert "as link" in out["note"]
    assert "williamhgates" not in out["note"] and "nvidia" not in out["note"]
    assert social.calls == []

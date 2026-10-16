"""No em or en dash in any `note` the three social tools can return (ADR 0015
§3).

A `note` is text the model reads at the moment it is choosing how to phrase
its answer, so every dash in one is a demonstration of the style her doctrine
asks her not to use. ADR 0015 already bans dashes from descriptions
(`test_the_description_meets_adr_0015`); this extends the ban to the result
text of `x_read`, `x_search`, `instagram_read` and `instagram_search`, including
the notes for a handle, a link and a post.

Two halves, because a note reaches the model from two kinds of place:

* every string literal in the three closures' own source (the inline
  `{"status": ..., "note": ...}` dicts), found by parsing the source rather
  than by exercising each branch, so a new branch is covered without a new
  test;
* every shared payload those closures or their backends return (the
  module-level constants in `nora.agent.tools`, `nora.infra.social.results`
  and the Instagram client), listed by name, so a constant that one of them
  starts returning has to be added here.
"""

from __future__ import annotations

import ast
import inspect
import re
import textwrap
import typing

import pytest

from nora.agent import tools as tools_module
from nora.agent.registry import ToolRegistry
from nora.agent.room import Room
from nora.infra.social import instagram_client, render, results
from nora.obs.metrics import Metrics
from nora.testing import FakeMemoryStore
from tests.fakes import SinkSender

_DASH = re.compile("[–—]")
_TOOLS = (
    "x_read", "x_search", "linkedin_read", "linkedin_search",
    "instagram_read", "instagram_search",
)


class _NoStore:
    async def recent(self, *, conversation_id: str, limit: int):
        return []


class _NoPersons:
    by_person: typing.ClassVar[dict] = {}

    def resolve(self, platform, sender_id):
        return None


def _calls() -> dict:
    room = Room(platform="signal", conversation_id="g1", roster_source=None, persons=_NoPersons())
    specs = ToolRegistry().specs(
        store=_NoStore(), conversation_id="g1", conversation_type="group",
        persons=_NoPersons(), honcho=FakeMemoryStore(), deliverer=SinkSender(),
        metrics=Metrics(), room=room,
    )
    return {s.name: s.call for s in specs if s.name in _TOOLS}


@pytest.mark.parametrize("tool", _TOOLS)
def test_no_string_in_the_closure_carries_a_dash(tool):
    call = _calls()[tool]
    lines, first = inspect.getsourcelines(call)
    tree = ast.parse(textwrap.dedent("".join(lines)))
    offenders = [
        (first + node.lineno - 1, node.value[:60])
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and _DASH.search(node.value)
    ]
    assert offenders == [], f"{tool}: a string it can return carries a dash: {offenders}"


def _shared_payloads() -> dict[str, object]:
    return {
        "tools._X_OFF": tools_module._X_OFF,
        "tools._X_NOTES[ok]": {"note": tools_module._X_NOTES["ok"]},
        "tools._LINKEDIN_OFF": tools_module._LINKEDIN_OFF,
        "tools._LINKEDIN_FAILED": tools_module._LINKEDIN_FAILED,
        "tools._LINKEDIN_UNASKED": tools_module._LINKEDIN_UNASKED,
        "tools._URL_AND_LINK_DIFFER": tools_module._URL_AND_LINK_DIFFER,
        "tools._LINKEDIN_READ_NOTES[ok]": {"note": tools_module._LINKEDIN_READ_NOTES["ok"]},
        "tools._LINKEDIN_SEARCH_NOTES[ok]": {"note": tools_module._LINKEDIN_SEARCH_NOTES["ok"]},
        "tools._INSTAGRAM_OFF": tools_module._INSTAGRAM_OFF,
        "tools._INSTAGRAM_FAILED": tools_module._INSTAGRAM_FAILED,
        "results.UNAVAILABLE": results.UNAVAILABLE,
        "results.NOTHING_FOUND": results.NOTHING_FOUND,
        "results.AMBIGUOUS": results.AMBIGUOUS,
        "results.NO_ACCOUNT": results.NO_ACCOUNT,
        "results.REFUSED": results.REFUSED,
        "results.NO_SUCH_HANDLE": results.NO_SUCH_HANDLE,
        "results.not_set_up(instagram)": results.not_set_up("instagram"),
        "results.not_set_up(x)": results.not_set_up("x"),
        "render.STORY_FRAMING": {"note": render.STORY_FRAMING},
        "render.STORY_READ_FRAMING": {"note": render.STORY_READ_FRAMING},
        "render.STORY_UNSEEN_FRAMING": {"note": render.STORY_UNSEEN_FRAMING},
        "render.SEARCH_FRAMING": {"note": render.SEARCH_FRAMING},
        "instagram_client.SEARCH_NOTHING_FOUND": instagram_client.SEARCH_NOTHING_FOUND,
        "render.POST_FRAMING": {"note": render.POST_FRAMING},
        "render.POST_READ_FRAMING": {"note": render.POST_READ_FRAMING},
        "render.POST_UNSEEN_FRAMING": {"note": render.POST_UNSEEN_FRAMING},
        "render.POST_NOT_READ_FRAMING": {"note": render.POST_NOT_READ_FRAMING},
        "render.POST_MIXED_FRAMING": {"note": render.POST_MIXED_FRAMING},
        "render.STORY_NOT_READ_FRAMING": {"note": render.STORY_NOT_READ_FRAMING},
        "render.STORY_MIXED_FRAMING": {"note": render.STORY_MIXED_FRAMING},
        "render._PAST_THE_CAP": {"note": render._PAST_THE_CAP},
        # What an item line says when it was not seen: inside the wrap, but
        # text the model reads all the same.
        **{f"render._NOT_SEEN[{k}]": {"note": v} for k, v in render._NOT_SEEN.items()},
        "instagram_client.POST_NOTHING_FOUND": instagram_client.POST_NOTHING_FOUND,
        "instagram_client.POST_UNSUPPORTED": instagram_client.POST_UNSUPPORTED,
        # `media_fetch`'s answer to an Instagram page link: it routes the
        # model to `instagram_read`, so it is a social note in all but tool.
        "tools._INSTAGRAM_PAGE_LINK": {"note": tools_module._INSTAGRAM_PAGE_LINK},
        "tools._INSTAGRAM_MOBILE_PAGE_LINK": {"note": tools_module._INSTAGRAM_MOBILE_PAGE_LINK},
        # `web_fetch`'s answer to the same links.
        "tools._WEB_FETCH_INSTAGRAM_PAGE": {"note": tools_module._WEB_FETCH_INSTAGRAM_PAGE},
        "tools._WEB_FETCH_INSTAGRAM_MOBILE_PAGE": {
            "note": tools_module._WEB_FETCH_INSTAGRAM_MOBILE_PAGE,
        },
    }


@pytest.mark.parametrize("name", sorted(_shared_payloads()))
def test_no_shared_payload_note_carries_a_dash(name):
    note = _shared_payloads()[name]["note"]  # type: ignore[index]
    assert not _DASH.search(note), f"{name}: {note!r}"


async def test_the_notes_actually_returned_on_the_refusal_paths_carry_no_dash():
    """The behavioural half, for the paths reachable with no backend: the
    source scan above says what COULD be returned, this reads what IS."""
    calls = _calls()
    outs = [
        await calls["x_read"](),
        await calls["x_read"](handle="nasa", link="https://x.com/nasa"),
        await calls["x_read"](handle="nasa"),
        await calls["x_read"](handle="not a name!", what="profile"),
        await calls["x_read"](link="https://www.instagram.com/p/SynthPost01/"),
        await calls["x_read"](link="https://evil.example/nasa"),
        await calls["x_read"](link="https://x.com/nasa/status/1", what="posts"),
        await calls["x_read"](handle="+85212345678", what="profile"),
        await calls["x_read"](handle="nasa", what="profile"),
        await calls["x_search"](query=""),
        await calls["x_search"](query="+85212345678"),
        await calls["x_search"](query="nasa launch"),
        await calls["linkedin_read"](),
        await calls["linkedin_read"](handle="williamhgates"),
        await calls["linkedin_read"](link="https://www.linkedin.com/posts/x_y-activity-1-a/"),
        await calls["linkedin_read"](link="https://x.com/nasa"),
        await calls["linkedin_read"](handle="williamhgates", what="profile"),
        await calls["linkedin_search"](query=""),
        await calls["linkedin_search"](query="+85212345678"),
        await calls["linkedin_search"](query="someone"),
        await calls["instagram_read"](what="posts", person="Quinn"),
        # The paths a handle or a link takes before any backend.
        await calls["instagram_read"](),
        await calls["instagram_read"](person="Quinn", handle="example_friend"),
        await calls["instagram_read"](handle="example_friend"),
        await calls["instagram_read"](handle="bad-handle", what="profile"),
        await calls["instagram_read"](handle="sk_live_abcdefghijklmnopqrstu", what="profile"),
        await calls["instagram_read"](handle="example_friend", what="profile"),
        await calls["instagram_read"](link="https://evil.example/p/SynthPost01/"),
        await calls["instagram_read"](
            link="https://www.instagram.com/p/SynthPost01/", what="profile",
        ),
        await calls["instagram_read"](link="https://www.instagram.com/p/SynthPost01/"),
        # The `url` alias: two different links.
        await calls["instagram_read"](
            link="https://www.instagram.com/p/SynthPost01/",
            url="https://www.instagram.com/p/SynthPost02/",
        ),
        await calls["instagram_search"](query=""),
        await calls["instagram_search"](query="+85212345678"),
        await calls["instagram_search"](query="example"),
    ]
    for out in outs:
        assert "note" in out, out
        assert not _DASH.search(out["note"]), out["note"]


async def test_a_refusal_in_a_turn_nobody_asked_for_carries_no_dash():
    """The asked-turn refusal for a story, a post and a reel."""
    room = Room(platform="signal", conversation_id="g1", roster_source=None, persons=_NoPersons())
    specs = ToolRegistry().specs(
        store=_NoStore(), conversation_id="g1", conversation_type="group",
        persons=_NoPersons(), honcho=FakeMemoryStore(), deliverer=SinkSender(),
        metrics=Metrics(), room=room, fire_turn=True,
    )
    read = next(s.call for s in specs if s.name == "instagram_read")
    for arg in (
        {"link": "https://www.instagram.com/reel/SynthReel01/"},
        {"handle": "example_friend", "what": "story"},
    ):
        out = await read(**arg)
        assert out["status"] == "refused"
        assert not _DASH.search(out["note"]), out["note"]

"""`instagram_read` by HANDLE and by LINK, end to end below the model.

The tool parses a pasted link with the one link parser
(`nora.domain.instagram_links`), the REAL router resolves a username through
the REAL client's exact lookup and decides reach through the REAL person index
and room policy, and the REAL client reads the bridge. Only the bridge is
fake: an aiohttp server that answers the shared contract fixtures the Go
handlers generate (`tests/fixtures/instagram_bridge/`), behaves like the real
`GET /accounts?username=` (exact, ASCII case-insensitive, 0 or 1 accounts),
and records EVERY request on a catch-all route, so «no story request reached
the bridge» is asserted on the bridge's own log, each beside a positive
control that shows the same world does reach it when it should.

Every name, handle, shortcode and id here is synthetic.
"""

from __future__ import annotations

import copy
import json
import re
import typing
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from nora.agent.registry import ToolRegistry
from nora.agent.room import Room
from nora.infra.social import (
    EXCEPTION_ROUTES,
    InstagramSocialClient,
    PersonsSubjectIndex,
    SocialRouter,
)
from nora.obs.metrics import Metrics
from nora.persons import load_persons
from nora.policy import Conversation, Policy
from nora.testing import FakeMemoryStore, FakeRosterSource
from tests.fakes import SinkSender

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "instagram_bridge"

_U_QUINN = "00000000-0000-4000-8000-000000000001"
_U_ROBIN = "00000000-0000-4000-8000-000000000002"
_U_RIVER = "00000000-0000-4000-8000-000000000004"
_U_ZED = "00000000-0000-4000-8000-000000000005"
_U_SOL = "00000000-0000-4000-8000-000000000006"

#: Every id in this world. None may appear in anything the model is handed
#: (R-18): igids and fbids are Instagram's, the person ids are Nora's.
_IDS = (
    "10000001", "100000000000001", "10000002", "100000000000002",
    "10000003", "100000000000003", "10000004", "100000000000004",
    "10000005", "100000000000005", "10000006", "100000000000006",
    "10000099", "100000000000099", "p_quinn", "p_robin", "p_river", "p_zed", "p_sol",
)

_IDENTITY = f'''
[persons.p_quinn]
name = "Quinn"
[[persons.p_quinn.signal]]
uuid = "{_U_QUINN}"
[[persons.p_quinn.instagram]]
username = "example_friend"
fbid = "100000000000001"
igid = "10000001"

[persons.p_robin]
name = "Robin"
[[persons.p_robin.signal]]
uuid = "{_U_ROBIN}"
[[persons.p_robin.instagram]]
label = "personal"
fbid = "100000000000002"
igid = "10000002"
[[persons.p_robin.instagram]]
label = "work"
fbid = "100000000000003"
igid = "10000003"

[persons.p_river]
name = "River"
[[persons.p_river.signal]]
uuid = "{_U_RIVER}"
[[persons.p_river.instagram]]
fbid = "100000000000004"
igid = "10000004"

[persons.p_zed]
name = "Zed"
[[persons.p_zed.signal]]
uuid = "{_U_ZED}"
[[persons.p_zed.instagram]]
fbid = "100000000000005"
igid = "10000005"

[persons.p_sol]
name = "Sol"
[[persons.p_sol.signal]]
uuid = "{_U_SOL}"
[[persons.p_sol.instagram]]
fbid = "100000000000006"
igid = "10000006"
'''

#: The accounts the fake bridge's username lookup holds. `some.stranger` is
#: in nobody's identity entry: a real account Nora knows nothing about.
_ACCOUNTS = {
    "example_friend": ("10000001", "100000000000001"),
    "robin.personal": ("10000002", "100000000000002"),
    "robin.work": ("10000003", "100000000000003"),
    "river.example": ("10000004", "100000000000004"),
    "zed.example": ("10000005", "100000000000005"),
    "sol.example": ("10000006", "100000000000006"),
    "some.stranger": ("10000099", "100000000000099"),
}

def _roster(*uuids: str) -> tuple[frozenset[str], ...]:
    return tuple(frozenset({u}) for u in uuids)


#: `g1`, where the questions are asked unless a test says otherwise: Quinn,
#: Robin and River are in the chat. Zed and Sol are not.
_ROSTER = {"g1": _roster(_U_QUINN, _U_ROBIN, _U_RIVER)}
#: Quinn and Robin are listed (Tier 0); River is in the chat and listed
#: nowhere.
_G1 = Conversation(
    platform="signal", ids=frozenset({"g1"}), persons=frozenset({"p_quinn", "p_robin"}),
)
#: Another room, listing Zed: Zed is Tier 0 THROUGH IT and is not on g1's
#: roster, the cross-room case.
_G2 = Conversation(platform="signal", ids=frozenset({"g2"}), persons=frozenset({"p_zed"}))
#: An open room (`persons = "*"`), which admits anyone and lists nobody. Sol
#: is a member of it and of nothing else, so it is the only room that could
#: make Sol Tier 0, and it must not.
_OPEN = Conversation(platform="signal", ids=frozenset({"open"}), persons=None)

_REEL = "https://www.instagram.com/reel/SynthReel01/"
_POST = "https://www.instagram.com/p/SynthPost01/"


def _fixture(name: str) -> dict:
    return json.loads((_FIXTURES / name).read_text())


def _story_link(username: str) -> str:
    return f"https://www.instagram.com/stories/{username}/3000000000000000001/"


class FakeBridge:
    """The instagram-bridge's read routes on a CATCH-ALL, so any request at
    all is recorded before it is answered. `calls` holds `(method, path with
    query)`."""

    _ACCOUNT = re.compile(r"/accounts/([0-9]+)")
    _STORY = re.compile(r"/accounts/([0-9]+)/story")
    _POSTS = re.compile(r"/posts/([A-Za-z0-9_-]+)")
    _USERNAME = re.compile(r"@?[A-Za-z0-9._]{1,30}")

    def __init__(self) -> None:
        self.timeline = _fixture("items_ok.json")
        self.account = _fixture("account_ok.json")
        self.story = _fixture("story_found.json")
        self.posts: dict[str, dict] = {
            "SynthReel01": _fixture("posts_carousel_ok.json"),
            "SynthPost01": _fixture("posts_carousel_ok.json"),
            "SynthGone01": _fixture("posts_not_found_404.json"),
            "SynthLongCode01": _fixture("posts_long_shortcode_501.json"),
            "SynthRefused1": _fixture("posts_upstream_rejected_502.json"),
        }
        #: When set, the username route answers these rows whatever it was
        #: asked: a bridge that broke its own exact-match contract.
        self.lookup_items: list[dict] | None = None
        self.calls: list[tuple[str, str]] = []

    @staticmethod
    def _answer(fx: dict) -> web.Response:
        return web.json_response(fx["body"], status=fx["status"])

    def _lookup(self, request: web.Request) -> web.Response:
        username = request.query.get("username", "")
        if not self._USERNAME.fullmatch(username):
            return self._answer(_fixture("accounts_by_username_bad_username_400.json"))
        body = copy.deepcopy(_fixture("accounts_by_username_found.json")["body"])
        if self.lookup_items is not None:
            body["items"] = self.lookup_items
            return web.json_response(body)
        wanted = username.removeprefix("@").lower()
        body["items"] = [
            {**body["items"][0], "username": name, "igid": igid, "fbid": fbid}
            for name, (igid, fbid) in _ACCOUNTS.items() if name.lower() == wanted
        ]
        return web.json_response(body)

    async def handle(self, request: web.Request) -> web.Response:
        self.calls.append((request.method, request.path_qs))
        if request.method != "GET":
            return web.Response(status=405, text="405 method not allowed")
        if request.path == "/accounts" and "username" in request.query:
            return self._lookup(request)
        if re.fullmatch(r"/accounts/[0-9]+/items", request.path):
            return self._answer(
                _fixture("items_last_page.json") if request.query.get("after") else self.timeline
            )
        if self._STORY.fullmatch(request.path):
            return self._answer(self.story)
        if self._ACCOUNT.fullmatch(request.path):
            return self._answer(self.account)
        if (m := self._POSTS.fullmatch(request.path)) and m.group(1) in self.posts:
            return self._answer(self.posts[m.group(1)])
        return web.Response(status=404, text="404 page not found")


@pytest.fixture
async def bridge():
    fake = FakeBridge()
    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", fake.handle)
    server = TestServer(app)
    await server.start_server()
    yield fake, str(server.make_url("")).rstrip("/")
    await server.close()


@pytest.fixture
def persons(tmp_path):
    path = tmp_path / "identity.toml"
    path.write_text(_IDENTITY)
    return load_persons(path)


class _NoStore:
    async def recent(self, *, conversation_id: str, limit: int):
        return []


@pytest.fixture
def world(bridge, persons):
    """`make(**turn) -> (fake_bridge, instagram_read)`: production router,
    person index, username lookup and client over one fake bridge, wired the
    way the composition root wires them. `as_spec=True` hands back the whole
    `ToolSpec` instead of its call, for a test that goes through Strands."""
    fake, root = bridge

    def make(*, room_id: str = "g1", roster: dict | None = None, tagger: str = "p_quinn",
             as_spec: bool = False, **turn):
        client = InstagramSocialClient(base_url=root, timeout_seconds=5.0)
        router = SocialRouter(
            default=None,
            exceptions=dict.fromkeys(EXCEPTION_ROUTES, client),
            subjects=PersonsSubjectIndex(persons, Policy(conversations=(_G1, _G2, _OPEN))),
            accounts=client,
        )
        room = Room(
            platform="signal", conversation_id=room_id,
            roster_source=FakeRosterSource(groups=roster or _ROSTER),
            persons=persons, tagger=persons.by_person[tagger],
        )
        specs = ToolRegistry(social=router).specs(
            store=_NoStore(), conversation_id=room_id, conversation_type="group",
            persons=persons, honcho=FakeMemoryStore(), deliverer=SinkSender(),
            metrics=Metrics(), room=room, **turn,
        )
        spec = next(s for s in specs if s.name == "instagram_read")
        return fake, (spec if as_spec else spec.call)

    return make


def _lookup(username: str) -> tuple[str, str]:
    return ("GET", f"/accounts?username={username}")


def _inside_the_wrap(text: str, needle: str) -> bool:
    opening = re.match(r"<([a-z_]+)\b[^>]*>", text)
    assert opening, text
    return needle in text[opening.end():text.rindex(f"</{opening.group(1)}>")]


def _no_ids(out: dict) -> None:
    wire = json.dumps(out, ensure_ascii=False)
    for identifier in _IDS:
        assert identifier not in wire, identifier


# ---- a profile, by handle or by link ------------------------------------------


@pytest.mark.parametrize(
    "arg",
    [
        {"handle": "example_friend", "what": "profile"},
        {"handle": "@Example_Friend", "what": "profile"},
        {"link": "https://www.instagram.com/example_friend/?igsh=c3ludGhldGlj"},
        {"link": "instagram.com/example_friend"},
        {"link": "https://www.instagram.com/example_friend/", "what": "profile"},
    ],
    ids=["handle", "handle_at_and_case", "link_with_query", "link_no_scheme", "link_with_what"],
)
async def test_a_profile_by_handle_or_link_is_looked_up_then_read(world, arg):
    """username → igid through the bridge's exact lookup, then the account
    route on THAT igid. The lookup is sent the handle alone: no `@`, no
    query, nothing else the person pasted."""
    fake, read = world()
    out = await read(**arg)

    assert out["status"] == "ok"
    assert "@example_friend" in out["text"]
    wanted = arg.get("handle", "example_friend").removeprefix("@")
    assert fake.calls == [_lookup(wanted), ("GET", "/accounts/10000001")]
    assert "person" not in out
    _no_ids(out)


async def test_a_handle_nobody_holds_is_nothing_found_saying_so(world):
    fake, read = world()
    out = await read(handle="no.such.account", what="profile")

    assert out["status"] == "nothing_found"
    assert "no account with that username" in out["note"]
    assert fake.calls == [_lookup("no.such.account")]


async def test_a_profile_is_read_for_anyone_including_a_stranger(world):
    """A profile has no reach check: a pasted link to a stranger's
    profile is read."""
    fake, read = world()
    out = await read(link="https://www.instagram.com/some.stranger/")

    assert out["status"] == "ok"
    assert fake.calls == [_lookup("some.stranger"), ("GET", "/accounts/10000099")]
    _no_ids(out)


# ---- a story, by handle or by link: Tier 0 on the igid it named ---------------


@pytest.mark.parametrize(
    "arg",
    [{"handle": "example_friend", "what": "story"}, {"link": _story_link("example_friend")}],
    ids=["handle", "link"],
)
async def test_a_listed_persons_story_by_handle_or_link_is_read(world, arg):
    fake, read = world()
    out = await read(**arg)

    assert out["status"] == "ok"
    assert out["source_kind"] == "instagram_story"
    assert fake.calls == [_lookup("example_friend"), ("GET", "/accounts/10000001/story")]
    _no_ids(out)


@pytest.mark.parametrize(
    "arg",
    [{"link": _story_link("some.stranger")}, {"handle": "some.stranger", "what": "story"}],
    ids=["link", "handle"],
)
async def test_a_story_of_someone_in_none_of_her_rooms_is_refused_and_never_requested(
    world, arg,
):
    """THE negative: the account maps to nobody, so it is in none of
    her rooms. `refused`, and the bridge's own log holds the lookup and NO
    story request. Positive control: the same stranger's profile IS read, so
    the missing story call is the reach check and not a lookup failure."""
    fake, read = world()
    out = await read(**arg)

    assert out["status"] == "refused"
    assert fake.calls == [_lookup("some.stranger")]
    assert not any(path.endswith("/story") for _m, path in fake.calls)
    _no_ids(out)

    assert (await read(handle="some.stranger", what="profile"))["status"] == "ok"
    assert fake.calls[-1] == ("GET", "/accounts/10000099")


async def test_a_story_link_to_someone_in_the_chat_but_listed_nowhere_is_refused(world):
    """River is in this group's roster and in `identity.toml`, and listed in
    no room's `persons`: not Tier 0."""
    fake, read = world()
    out = await read(link=_story_link("river.example"))

    assert out["status"] == "refused"
    assert fake.calls == [_lookup("river.example")]


async def test_a_story_link_to_one_of_two_accounts_reads_that_account(world):
    """The multi-account case: Robin has two accounts. A
    story link names ONE, and that one is read. The same friend by name
    stays `ambiguous` and nothing is sent: nothing in «Robin» says which."""
    fake, read = world()

    work = await read(link=_story_link("robin.work"))
    assert work["status"] == "ok"
    assert fake.calls == [_lookup("robin.work"), ("GET", "/accounts/10000003/story")]

    personal = await read(handle="robin.personal", what="story")
    assert personal["status"] == "ok"
    assert fake.calls[-1] == ("GET", "/accounts/10000002/story")

    calls_before = len(fake.calls)
    by_name = await read(person="Robin", what="story")
    assert by_name["status"] == "ambiguous"
    assert len(fake.calls) == calls_before
    for out in (work, personal, by_name):
        _no_ids(out)


# ---- one post or reel, by link -------------------------------------------------


async def test_a_carousel_of_three_returns_three_items_in_order(world):
    """The whole post: every item, in the bridge's order, with kind and url,
    and a video's duration. The author and the caption ride in the wrapped
    text, numbered in the same order as `items`. This world wires no media
    reading, so every item says it was not seen and the note says she
    could not see the post (the readings themselves are
    `tests/unit/test_instagram_read_readings.py`)."""
    fake, read = world()
    out = await read(link=_POST)

    body = _fixture("posts_carousel_ok.json")["body"]
    assert out["status"] == "ok"
    assert out["source_kind"] == "instagram_post"
    assert [i["url"] for i in out["items"]] == [i["url"] for i in body["items"]]
    assert [i["kind"] for i in out["items"]] == ["image", "video", "image"]
    assert out["items"][1]["duration_seconds"] == 12.5
    assert all(set(i) <= {"kind", "url", "duration_seconds"} for i in out["items"])
    assert out["author"] == "example_author"
    assert "couldn't see the post" in out["note"]
    assert _inside_the_wrap(out["text"], body["caption"])
    rest = {k: v for k, v in out.items() if k != "text"}
    assert body["caption"] not in json.dumps(rest)
    # The items render in the same order as `items`.
    positions = [out["text"].index(f"{n}. {i['kind']}") for n, i in enumerate(out["items"], 1)]
    assert positions == sorted(positions)
    assert fake.calls == [("GET", "/posts/SynthPost01")]
    _no_ids(out)


@pytest.mark.parametrize(
    "link",
    [_REEL, "https://www.instagram.com/reels/SynthReel01/", _REEL + "?igsh=c3ludGhldGlj#x"],
    ids=["reel", "reels", "query_and_fragment"],
)
async def test_a_reel_link_reads_the_post_route_with_the_shortcode_alone(world, link):
    fake, read = world()
    out = await read(link=link)

    assert out["status"] == "ok"
    assert fake.calls == [("GET", "/posts/SynthReel01")]


@pytest.mark.parametrize(
    ("code", "status"),
    [("SynthGone01", "nothing_found"), ("SynthLongCode01", "unavailable"),
     ("SynthRefused1", "unavailable")],
    ids=["404", "501", "502"],
)
async def test_the_post_routes_failures_map_to_their_states(world, code, status):
    fake, read = world()
    out = await read(link=f"https://www.instagram.com/p/{code}/")

    assert out["status"] == status
    assert "note" in out
    assert fake.calls == [("GET", f"/posts/{code}")]
    # The bridge's own message never reaches the model: the note is Nora's.
    message = fake.posts[code]["body"]["error"]["message"]
    assert message not in json.dumps(out)


@pytest.mark.parametrize("what", ["profile", "story", "posts"])
async def test_a_post_link_with_what_is_not_guessed_at(world, what):
    """A link whose kind contradicts `what` answers `unavailable`, and
    nothing is sent. The same link alone is read."""
    fake, read = world()
    out = await read(link=_REEL, what=what)

    assert out["status"] == "unavailable"
    assert fake.calls == []
    assert (await read(link=_REEL))["status"] == "ok"


async def test_a_story_link_asked_as_a_profile_is_not_guessed_at(world):
    fake, read = world()
    out = await read(link=_story_link("example_friend"), what="profile")

    assert out["status"] == "unavailable"
    assert fake.calls == []


# ---- a turn nobody asked for ----------------------------------------------------


_UNASKED = [{"fire_turn": True}, {"dream_adaptor": "proactive"}]


@pytest.mark.parametrize("turn", _UNASKED, ids=["fire_turn", "dreamer"])
@pytest.mark.parametrize(
    "arg",
    [
        {"link": _story_link("example_friend")},
        {"handle": "example_friend", "what": "story"},
        {"link": _POST},
        {"link": _REEL},
    ],
    ids=["story_link", "story_handle", "post", "reel"],
)
async def test_a_background_turn_is_refused_a_story_post_or_reel_before_any_call(
    world, turn, arg,
):
    """Nora reads a story, and a shared post or reel, only when a person asks.
    A fire turn or a Dreamer pass is `refused` before
    anything is looked up or sent. Positive control: the SAME turn reads a
    profile by link, which stays allowed everywhere."""
    fake, read = world(**turn)
    out = await read(**arg)

    assert out["status"] == "refused"
    assert fake.calls == []
    assert (await read(link="https://www.instagram.com/example_friend/"))["status"] == "ok"
    assert fake.calls == [_lookup("example_friend"), ("GET", "/accounts/10000001")]


# ---- what the tool refuses before anything ---------------------------------------


#: The hostile table (tests/unit/test_instagram_parser.py), as links
#: pasted into the tool, plus the suffix-host shapes. Not one may reach the
#: bridge.
_HOSTILE = [
    "https://evil.example/reel/SynthReel01/",
    "https://www.instagram.com/reel/bad%20code/",
    "https://www.instagram.com/reel/bad.code/",
    "https://www.instagram.com:8443/reel/SynthReel01/",
    "https://someone:secret@www.instagram.com/reel/SynthReel01/",
    "javascript://www.instagram.com/reel/SynthReel01/",
    "https://www.instagram.com/stories/example_friend/notdigits",
    "https://www.instagram.com/stories/bad/user/name/1",
    "https://www.instagram.com/explore/",
    "https://www.instagram.com/reel/SynthReel01/extra/",
    "/reel/SynthReel01/",
    "not a url at all",
    "https://www.instagram.com.evil.x/reel/SynthReel01/",
    "https://m.instagram.com/reel/SynthReel01/",
    "https://instagr.am/reel/SynthReel01/",
    "https://www.instagram.com./reel/SynthReel01/",
    "https://www.instagram.com/reel/SynthReel01;params/",
    "https://evil.example\\@www.instagram.com/reel/SynthReel01/",
    "https://www.instagram.com\\@evil.example/reel/SynthReel01/",
    "https://www.іnstagram.com/reel/SynthReel01/",
    "https://www.instagram.com/reel/Ｓｙｎｔｈ/",
    "https://www.instagram.com/stories/example_friend/３3000000000000000001",
    "https://www.instagram.com/ｒｅｅｌ/SynthReel01/",
    # Suffix and substring hosts: a host check by `endswith` or `in` passes
    # each of these.
    "https://notinstagram.com/p/SynthPost01/",
    "https://evil-instagram.com/example_friend/",
    "https://evil.example/www.instagram.com/p/SynthPost01/",
    "https://www.instagram.com/stories/10000001/3000000000000000001/",
    "https://www.instagram.com/10000001/",
    # A saved highlight is addressed by the highlight's id, not an account,
    # and `highlights` would otherwise read as a story by `@highlights`.
    "https://www.instagram.com/stories/highlights/17800000000000001/",
    "https://www.instagram.com/stories/highlights/",
    # Key-shaped usernames: valid handle charset, refused by the egress
    # screen a typed handle gets, before the username leaves.
    "https://www.instagram.com/sk_live_abcdefghijklmnopqrstu/",
    "https://www.instagram.com/stories/sk_live_abcdefghijklmnopqrstu/3000000000000000001/",
]


@pytest.mark.parametrize("link", _HOSTILE)
async def test_a_hostile_link_never_reaches_the_bridge(world, link):
    fake, read = world()
    out = await read(link=link)

    assert out["status"] in ("unavailable", "refused")
    assert fake.calls == []
    # The link is not echoed back.
    assert link not in json.dumps(out, ensure_ascii=False)


@pytest.mark.parametrize(
    "arg",
    [
        {"link": "https://www.instagram.com/sk_live_abcdefghijklmnopqrstu/"},
        {"link": "https://www.instagram.com/stories/sk_live_abcdefghijklmnopqrstu/3000000000000000001/"},
        {"link": "https://www.instagram.com/sk_live_abcdefghijklmnopqrstu/", "what": "story"},
        {"handle": "sk_live_abcdefghijklmnopqrstu", "what": "story"},
    ],
    ids=["profile_link", "story_link", "profile_link_as_story", "handle"],
)
async def test_a_key_shaped_username_is_refused_however_it_arrived(world, arg):
    """The egress screen runs on EVERY username about to leave, a link's as
    much as a typed handle's: a link is not a way around it."""
    fake, read = world()
    out = await read(**arg)

    assert out["status"] == "refused"
    assert "phone number or ID" in out["note"]
    assert fake.calls == []


@pytest.mark.parametrize(
    "arg",
    [{}, {"what": "profile"}, {"person": "Quinn", "handle": "example_friend", "what": "profile"},
     {"handle": "example_friend", "link": _REEL}, {"person": "  ", "what": "profile"},
     {"handle": "example_friend", "url": _REEL}, {"person": "Quinn", "url": _POST},
     {"url": "  "}],
    ids=["nothing", "what_only", "person_and_handle", "handle_and_link", "blank_person",
         "handle_and_url", "person_and_url", "blank_url"],
)
async def test_exactly_one_input_is_required(world, arg):
    """`url` is `link` under another name, so it counts as the link: a
    handle or a person beside it is still two inputs."""
    fake, read = world()
    out = await read(**arg)

    assert out["status"] == "unavailable"
    assert "exactly one" in out["note"]
    assert fake.calls == []


# ---- `url` is `link` ------------------------------------------------------------
#
# A model calling from the deferred catalogue, before the schema is loaded,
# passes `url=`. Without such a parameter Strands drops it and the call fails
# «exactly one».


@pytest.mark.parametrize(
    ("url", "calls"),
    [(_POST, [("GET", "/posts/SynthPost01")]),
     ("instagram.com/reel/SynthReel01/", [("GET", "/posts/SynthReel01")]),
     ("https://www.instagram.com/example_friend/",
      [_lookup("example_friend"), ("GET", "/accounts/10000001")])],
    ids=["post", "reel_no_scheme", "profile"],
)
async def test_url_is_read_exactly_as_link_would_be(world, url, calls):
    fake, read = world()
    out = await read(url=url)

    assert out["status"] == "ok"
    assert fake.calls == calls
    _no_ids(out)


async def test_url_survives_strands_argument_validation(world):
    """Through the adapter the agent loop runs, not the bare closure: Strands
    validates the model's input through a model built from the signature,
    which drops any key it does not declare. This is the path a live
    `url=` call takes, and the bridge is asked for the post."""
    from nora.agent.ports import adapt

    fake, spec = world(as_spec=True)
    tool = adapt(spec)
    events = [
        event async for event in tool.stream(
            {"toolUseId": "t1", "name": "instagram_read", "input": {"url": _POST}}, {},
        )
    ]

    assert events[-1].tool_result["status"] == "success"
    assert fake.calls == [("GET", "/posts/SynthPost01")]


async def test_url_and_link_that_differ_read_nothing(world):
    """Two different links: nothing says which was meant. Positive control:
    the same link given both ways is simply that link."""
    fake, read = world()
    out = await read(link=_POST, url=_REEL)

    assert out["status"] == "unavailable"
    assert "link" in out["note"] and "url" in out["note"]
    assert _POST not in out["note"] and _REEL not in out["note"]
    assert fake.calls == []

    same = await read(link=_POST, url=f"  {_POST} ")
    assert same["status"] == "ok"
    assert fake.calls == [("GET", "/posts/SynthPost01")]


@pytest.mark.parametrize(
    ("link", "url", "path"),
    [("instagram.com/p/SynthPost01/", _POST, "/posts/SynthPost01"),
     (_POST + "?igsh=c3ludGhldGlj", "www.instagram.com/p/SynthPost01", "/posts/SynthPost01"),
     ("https://www.instagram.com/reels/SynthReel01/", _REEL, "/posts/SynthReel01")],
    ids=["no_scheme", "query_and_no_slash", "reels_and_reel"],
)
async def test_url_and_link_spelling_the_same_link_agree(world, link, url, path):
    """Compared as the link parser reads them, not as typed: two spellings
    of one post are one link, and it is read."""
    fake, read = world()
    out = await read(link=link, url=url)

    assert out["status"] == "ok"
    assert fake.calls == [("GET", path)]


@pytest.mark.parametrize(
    "arg",
    [{"handle": "example_friend"}, {"handle": "example_friend", "what": "unsupported"}],
    ids=["no_what", "posts"],
)
async def test_a_handle_needs_a_read_it_can_do(world, arg):
    fake, read = world()
    out = await read(**arg)

    assert out["status"] == "unavailable"
    assert "posts" not in json.dumps(out)
    assert fake.calls == []


@pytest.mark.parametrize(
    "handle", ["10000001", "bad-handle", "two words", "a" * 31, "@"],
)
async def test_a_handle_that_is_not_one_is_never_sent(world, handle):
    """An all-digit handle is the shape of an igid, and it is not sent as a
    username either."""
    fake, read = world()
    out = await read(handle=handle, what="profile")

    assert out["status"] == "unavailable"
    assert fake.calls == []


async def test_a_handle_shaped_like_a_key_is_refused_by_the_egress_screen(world):
    fake, read = world()
    out = await read(handle="sk_live_abcdefghijklmnopqrstu", what="profile")

    assert out["status"] == "refused"
    assert fake.calls == []


# ---- the client does not re-pick -------------------------------------------------


async def test_the_exact_match_is_read_whatever_order_the_bridge_listed_it_in(world):
    """An exact match need not be the first result. The
    bridge filters, and the client does not trust that: a body listing a
    near match FIRST still reads the exact one."""
    fake, read = world()
    found = _fixture("accounts_by_username_found.json")["body"]["items"][0]
    fake.lookup_items = [
        {**found, "username": "example_friend2", "igid": "10000099"},
        {**found, "username": "Example_Friend", "igid": "10000001"},
    ]
    out = await read(handle="example_friend", what="profile")

    assert out["status"] == "ok"
    assert fake.calls[-1] == ("GET", "/accounts/10000001")


async def test_a_lookup_with_no_exact_match_is_unavailable_and_reads_nothing(world):
    """A body naming only someone else broke the bridge's contract: «I
    couldn't check», never that account, and never «no such account»."""
    fake, read = world()
    found = _fixture("accounts_by_username_found.json")["body"]["items"][0]
    fake.lookup_items = [{**found, "username": "example_friend2", "igid": "10000099"}]
    out = await read(handle="example_friend", what="profile")

    assert out["status"] == "unavailable"
    assert fake.calls == [_lookup("example_friend")]


async def test_every_request_is_a_get_on_an_account_or_post_resource(world):
    """Read-only at the client layer."""
    fake, read = world()
    for arg in (
        {"handle": "example_friend", "what": "profile"},
        {"link": _story_link("example_friend")},
        {"link": _POST},
    ):
        assert (await read(**arg))["status"] == "ok"
    assert len(fake.calls) == 5
    assert all(
        m == "GET" and (p.startswith("/accounts") or p.startswith("/posts/"))
        for m, p in fake.calls
    )


# ---- the surface -------------------------------------------------------------------


class _NoPersons:
    by_person: typing.ClassVar[dict] = {}

    def resolve(self, platform, sender_id):
        return None


def test_the_schema_takes_one_of_person_handle_or_link_and_an_optional_read():
    """`what` is an enum of the two reads a name or a handle can
    ask for, and nothing is required, because exactly one of three inputs is
    given and a link needs no `what`. No `limit` or `cursor`.
    `url` is `link` under the name a model reaches for first,
    declared because Strands drops an undeclared argument."""
    from nora.agent.ports import adapt

    room = Room(platform="signal", conversation_id="g1", roster_source=None, persons=_NoPersons())
    (spec,) = [
        s for s in ToolRegistry().specs(
            store=_NoStore(), conversation_id="g1", conversation_type="group",
            persons=_NoPersons(), honcho=FakeMemoryStore(), deliverer=SinkSender(),
            metrics=Metrics(), room=room,
        ) if s.name == "instagram_read"
    ]
    schema = adapt(spec).tool_spec["inputSchema"]["json"]
    assert set(schema["properties"]) == {
        "what", "person", "handle", "link", "url", "limit", "cursor",
    }
    assert schema["properties"]["url"]["description"] == "same as link."
    assert not schema.get("required")
    assert schema["properties"]["what"]["enum"] == ["profile", "story", "posts"]


# ---- a story by handle or link is scoped to THIS room -------------------------


@pytest.mark.parametrize(
    "arg",
    [{"handle": "zed.example", "what": "story"}, {"link": _story_link("zed.example")},
     {"link": "https://www.instagram.com/zed.example/", "what": "story"}],
    ids=["handle", "story_link", "profile_link_as_story"],
)
async def test_a_story_of_someone_listed_in_another_room_only_is_refused(world, arg):
    """The operator's conservative default: Zed is Tier 0 through g2 and is not
    on this room's roster. A story by handle or link reaches no further than
    a name would, so it is `refused` and no story request is sent. The answer
    is byte for byte a stranger's: a different one would tell this room that
    Zed is in one of her other rooms. Positive control: asked from a room
    where Zed IS on the roster, the same read is made."""
    fake, read = world()
    out = await read(**arg)
    stranger = await read(handle="some.stranger", what="story")

    assert out["status"] == "refused"
    assert out == stranger
    assert not any(path.endswith("/story") for _m, path in fake.calls)
    _no_ids(out)

    fake2, read2 = world(roster={"g1": _roster(_U_QUINN, _U_ZED)})
    assert (await read2(**arg))["status"] == "ok"
    assert fake2.calls[-1] == ("GET", "/accounts/10000005/story")


async def test_an_open_room_makes_nobody_tier_zero_for_a_handle_either(world):
    """Asked in the `*` room, about Sol, who is on its roster and listed in
    no room: the open room admits anyone and asserts nobody, so the story is
    `refused` and never requested. Positive control: Sol's profile is read
    from the same room."""
    fake, read = world(room_id="open", roster={"open": _roster(_U_QUINN, _U_SOL)})
    for arg in ({"handle": "sol.example", "what": "story"}, {"link": _story_link("sol.example")}):
        out = await read(**arg)
        assert out["status"] == "refused"
    assert not any(path.endswith("/story") for _m, path in fake.calls)

    assert (await read(handle="sol.example", what="profile"))["status"] == "ok"
    assert fake.calls[-1] == ("GET", "/accounts/10000006")


async def test_an_unreadable_roster_makes_a_handle_story_unavailable(persons, bridge):
    """No roster to scope the check to: «I couldn't check», nothing looked up.
    A profile by handle needs no roster."""
    fake, root = bridge
    client = InstagramSocialClient(base_url=root, timeout_seconds=5.0)
    router = SocialRouter(
        default=None,
        exceptions=dict.fromkeys(EXCEPTION_ROUTES, client),
        subjects=PersonsSubjectIndex(persons, Policy(conversations=(_G1,))),
        accounts=client,
    )
    room = Room(
        platform="signal", conversation_id="g1", roster_source=FakeRosterSource(fail=True),
        persons=persons, tagger=persons.by_person["p_quinn"],
    )
    specs = ToolRegistry(social=router).specs(
        store=_NoStore(), conversation_id="g1", conversation_type="group",
        persons=persons, honcho=FakeMemoryStore(), deliverer=SinkSender(),
        metrics=Metrics(), room=room,
    )
    read = next(s.call for s in specs if s.name == "instagram_read")

    assert (await read(handle="example_friend", what="story"))["status"] == "unavailable"
    assert fake.calls == []
    assert (await read(handle="example_friend", what="profile"))["status"] == "ok"


# ---- a profile link with `what="story"`, and a story-ring link -----------------


async def test_a_profile_link_asked_for_its_story_reads_that_accounts_story(world):
    """«what's on her story <profile link>»: the link names the account, `what`
    names the read. It is the handle path exactly, under every story check:
    the stranger's is refused and never requested."""
    fake, read = world()
    out = await read(link="https://www.instagram.com/example_friend/", what="story")

    assert out["status"] == "ok"
    assert out["source_kind"] == "instagram_story"
    assert fake.calls == [_lookup("example_friend"), ("GET", "/accounts/10000001/story")]

    refused = await read(link="https://www.instagram.com/some.stranger/", what="story")
    assert refused["status"] == "refused"
    assert fake.calls[-1] == _lookup("some.stranger")


@pytest.mark.parametrize(
    ("link", "what"),
    [
        ("https://www.instagram.com/example_friend/", "unsupported"),
        ("https://www.instagram.com/example_friend/", "reel"),
        # `post` IS the read a post link decides, and it is still not a value
        # `what` may take: the enum is enforced, not only compared.
        (_POST, "post"),
        (_REEL, "post"),
    ],
)
async def test_a_what_outside_the_enum_beside_a_link_is_unavailable(world, link, what):
    fake, read = world()
    out = await read(link=link, what=what)

    assert out["status"] == "unavailable"
    assert fake.calls == []


@pytest.mark.parametrize(
    ("username", "status", "igid"),
    [("example_friend", "ok", "10000001"), ("some.stranger", "refused", None)],
)
async def test_a_story_ring_link_reads_the_current_story_under_the_same_checks(
    world, username, status, igid,
):
    """`/stories/<user>/` with no frame id (a story-ring share) is that
    account's current story: read for a listed person in this room, refused
    and never requested for anyone else."""
    fake, read = world()
    out = await read(link=f"https://www.instagram.com/stories/{username}/")

    assert out["status"] == status
    story_calls = [path for _m, path in fake.calls if path.endswith("/story")]
    assert story_calls == ([f"/accounts/{igid}/story"] if igid else [])


async def test_post_lists_page_without_losing_items_or_exposing_upstream_cursors(world):
    fake, read = world()
    first = await read(what="posts", person="Quinn", limit=1)
    assert first["status"] == "ok"
    assert len(first["posts"]) == 1
    assert first["next_cursor"]
    assert "synthetic-cursor-1" not in json.dumps(first)
    _no_ids(first)
    calls = len(fake.calls)
    second = await read(what="posts", person="Quinn", limit=1, cursor=first["next_cursor"])
    assert second["status"] == "ok"
    assert second["posts"][0]["link"] != first["posts"][0]["link"]
    assert len(fake.calls) == calls, "the tail of a fetched page is retained, not skipped"
    third = await read(what="posts", person="Quinn", cursor=second["next_cursor"])
    assert third["status"] == "ok"
    assert third["next_cursor"] is None
    assert any("after=synthetic-cursor-1" in path for _, path in fake.calls)


async def test_post_list_cursors_cannot_cross_accounts_or_turns(world):
    fake, read = world()
    first = await read(what="posts", handle="example_friend", limit=1)
    before = len(fake.calls)
    refused = await read(what="posts", handle="some.stranger", cursor=first["next_cursor"])
    assert refused["status"] == "unavailable"
    assert len(fake.calls) == before
    _, other_turn = world()
    refused = await other_turn(what="posts", handle="example_friend", cursor=first["next_cursor"])
    assert refused["status"] == "unavailable"
    assert len(fake.calls) == before


async def test_post_lists_refuse_unasked_turns_and_do_not_hide_malformed_pages(world):
    fake, read = world(fire_turn=True)
    assert (await read(what="posts", handle="example_friend"))["status"] == "refused"
    assert fake.calls == []
    fake, read = world()
    fake.timeline = {"status": 200, "body": {"items": [{}], "next_cursor": None}}
    assert (await read(what="posts", person="Quinn"))["status"] == "unavailable"

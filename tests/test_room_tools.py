"""`group_members`, `lookup_person`, `about` — the three room-scoped tools.

The rules under test:

* **The model names WHAT, Nora resolves WHERE** (DEC-21/DEC-24). None of the
  three declares a conversation, a peer id or a phone number, so there is
  nothing for the model to invent and nothing it can address outside this room.
* **Scope is enforced at ASSEMBLY, not egress.** A person in `identity.toml`
  who is not in this conversation is indistinguishable from a person who does
  not exist. R-18 cannot help here — a roster leak carries only names.
* **A tool that cannot answer says so in its RETURN VALUE.** «asked and nothing
  known» and «could not ask» are different answers; conflating them would hide
  a dead `peer_card` endpoint.
"""

from __future__ import annotations

import json

import pytest

from nora.agent.egress import scan_identifiers
from nora.agent.room import Room
from nora.agent.tools import build_tools
from nora.infra.transit import TransitClient
from nora.obs.metrics import Metrics
from nora.persons import load_persons
from nora.testing import FakeMemoryStore, FakeRosterSource
from tests.fakes import SinkSender

GROUP = "Lf2L9zqeNUahRsOz6BA+sTB8aWfbuvqu9vOhpvmJi34="

NORA = frozenset({"+85290000100", "a1000000-0000-4000-8000-000000000000"})
MILES = frozenset({"+85290000101", "a1000001-0000-4000-8000-000000000001"})
MILES_WORK = frozenset({"+85290000103", "a1000002-0000-4000-8000-000000000002"})
TYLER = frozenset({"+85290000102"})
STRANGER = frozenset({"+85260000000"})

IDENTITY = """
[persons.miles_novak]
first_name = "Miles"
last_name  = "Novak"
aliases = ["俊爺", "Miles"]
[[persons.miles_novak.signal]]
label = "personal"
phone = "+85290000101"
uuid  = "a1000001-0000-4000-8000-000000000001"
[[persons.miles_novak.signal]]
label = "business"
phone = "+85290000103"
uuid  = "a1000002-0000-4000-8000-000000000002"
[[persons.miles_novak.discord]]
user_id = "d1-user"

[persons.tyler_cortez]
first_name = "Tyler"
last_name  = "Cortez"
aliases = ["阿豹", "Tyler"]
[[persons.tyler_cortez.signal]]
phone = "+85290000102"

# Never a member of any group under test. The whole point of this entry is
# that no tool here can reach her.
[persons.elsewhere_liu]
first_name = "Elsewhere"
last_name  = "Liu"
aliases = ["外人"]
[[persons.elsewhere_liu.signal]]
phone = "+85288888888"
"""


class _NullStore:
    async def recent(self, *, conversation_id: str, limit: int):
        return []



# `transit` is not what these tests are about — see `UNREACHABLE_TRANSIT` in
# tests/test_tools.py for why this is a real client aimed at a closed port
# rather than a stand-in object.
_UNREACHABLE_TRANSIT = TransitClient(
    kmb_base_url="http://127.0.0.1:1", mtr_schedule_url="http://127.0.0.1:1/s",
    mtr_routes_url="http://127.0.0.1:1/r.csv", timeout_seconds=0.5,
)

def _build(
    *,
    persons,
    members=(MILES, TYLER, NORA),
    roster_fails=False,
    honcho=None,
    tagger=None,
    question="邊個係阿豹",
    metrics=None,
    tagger_card_injected=False,
    platform="signal",
    group_id=GROUP,
):
    source = (
        FakeRosterSource(fail=True) if roster_fails
        else FakeRosterSource(groups={group_id: tuple(members)})
    )
    metrics = metrics or Metrics()
    room = Room(
        platform=platform, conversation_id=group_id, roster_source=source,
        persons=persons, tagger=tagger, self_ids=NORA,
        tagger_card_injected=tagger_card_injected,
    )
    tools = build_tools(
        store=_NullStore(), conversation_id=group_id, conversation_type="group",
        persons=persons, honcho=honcho or FakeMemoryStore(), deliverer=SinkSender(),
        metrics=metrics, room=room, transit_source=_UNREACHABLE_TRANSIT,
        question=question, context_window=40,
    )
    return {t.tool_name: t for t in tools}


@pytest.fixture
def persons(tmp_path):
    path = tmp_path / "identity.toml"
    path.write_text(IDENTITY)
    return load_persons(path)


async def _call(tool, **kwargs):
    return await tool._tool_func(**kwargs)


# ---- DEC-24: nothing in these schemas is Nora's to know ---------------------


def test_no_room_scoped_tool_asks_the_model_for_anything_nora_already_knows(persons):
    """DEC-24 asserted on the SCHEMA, which is what the model actually sees.

    `group_members` takes nothing at all — it is bound to the tag's
    conversation. `lookup_person` takes ONE argument, a name, because a name is
    the only thing in the exchange the model genuinely decides. A conversation,
    a peer id, a phone number would each be a value Nora already holds, so none
    is declared.
    """
    tools = _build(persons=persons)
    assert tools["group_members"].tool_spec["inputSchema"]["json"].get("properties") in (
        None, {},
    )
    assert set(
        tools["lookup_person"].tool_spec["inputSchema"]["json"]["properties"]
    ) == {"name"}


def test_no_room_scoped_tool_accepts_a_platform_identifier(persons):
    tools = _build(persons=persons)
    forbidden = {"conversation_id", "conversation_type", "peer_id", "sender_id",
                 "person_id", "phone", "uuid", "search_query"}
    for name in ("group_members", "lookup_person"):
        properties = set(
            tools[name].tool_spec["inputSchema"]["json"].get("properties") or {}
        )
        assert not properties & forbidden, f"{name} makes the model supply {properties}"


def test_every_room_scoped_tool_has_a_selection_policy_in_its_docstring(persons):
    """The docstring IS the schema (`FunctionToolMetadata.extract_metadata`),
    and WHEN TO REACH FOR A TOOL belongs there rather than in another prose
    catalogue in the prompt."""
    tools = _build(persons=persons)
    for name in ("group_members", "lookup_person"):
        doc = (tools[name].__doc__ or "").lower()
        assert len(doc) > 200, f"{name}'s docstring is too thin to steer a model"
        assert "call this" in doc or "do not call" in doc, name


# ---- group_members -----------------------------------------------------------


async def test_group_members_returns_this_rooms_people_by_name(persons):
    result = await _call(_build(persons=persons)["group_members"])
    assert result["status"] == "ok"
    assert result["members"] == ["Miles Novak", "Tyler Cortez"]


async def test_group_members_never_lists_someone_who_is_not_in_this_room(persons):
    """The roster leak, as a test. Asked who was in the group, Nora must not
    list people who were never members because the prompt was handed the
    whole address book. `elsewhere_liu` is in the same identity.toml."""
    result = await _call(_build(persons=persons)["group_members"])
    assert "Elsewhere Liu" not in result["members"]


async def test_group_members_counts_unmapped_members_without_naming_them(persons):
    result = await _call(_build(persons=persons, members=(MILES, STRANGER, NORA))["group_members"])
    assert result["members"] == ["Miles Novak"]
    assert result["members_i_have_no_name_for"] == 1


async def test_group_members_does_not_count_nora_as_a_faceless_stranger(persons):
    result = await _call(_build(persons=persons, members=(MILES, NORA))["group_members"])
    assert result["members_i_have_no_name_for"] == 0


async def test_group_members_degrades_honestly_when_the_daemon_is_unreachable(persons):
    """It must NEVER return an empty member list here: «nobody is in this
    group» is a confident lie, and the group would read it as one."""
    metrics = Metrics()
    tools = _build(persons=persons, roster_fails=True, metrics=metrics)
    result = await _call(tools["group_members"])
    assert result["status"] == "unavailable"
    assert "members" not in result
    assert metrics.get(
        "nora_room_lookups_total", tool="group_members", status="unavailable",
    ) == 1


async def test_group_members_is_counted_when_it_succeeds_too(persons):
    """R-17's lesson: a failure counter alone cannot tell "healthy" from "this
    code no longer runs"."""
    metrics = Metrics()
    await _call(_build(persons=persons, metrics=metrics)["group_members"])
    assert metrics.get("nora_room_lookups_total", tool="group_members", status="ok") == 1


async def test_group_members_labels_a_conversation_roster_as_the_conversation(persons):
    """Signal's roster IS the conversation's own member list, so `scope` says
    exactly that — the Discord caveat must not become a hedge applied to
    every platform."""
    result = await _call(_build(persons=persons)["group_members"])
    assert result["scope"] == "this conversation"


async def test_group_members_on_discord_labels_the_guild_scope_not_the_channel(persons):
    """`DiscordRoster` reads `GET /guilds/{id}/members` — the whole server's
    member list, which can exceed this channel's audience (Discord exposes no
    per-channel member read). The `ok` result names that actual scope rather
    than letting «in this conversation» claim a precision the source does not
    have; the names still list, since over-reporting is the safe direction."""
    cid = "channel-one"
    tools = _build(persons=persons, platform="discord", group_id=cid,
                   members=(frozenset({"d1-user"}),
                            frozenset({"unmapped-actor"})))
    result = await _call(tools["group_members"])
    assert result["status"] == "ok"
    # The mapped member resolves by name; the unmapped one is a COUNT, and
    # neither result nor scope ever carries the source id itself (R-18).
    assert result["members"] == ["Miles Novak"]
    assert result["members_i_have_no_name_for"] == 1
    assert "server" in result["scope"]
    assert "unmapped-actor" not in json.dumps(result)


async def test_group_members_on_discord_reports_unavailable_not_an_empty_server(persons):
    """A roster failure is `unavailable`, never an empty guild — and the scope
    label rides along on `ok` only, so a failed read asserts nothing."""
    result = await _call(_build(
        persons=persons, platform="discord", group_id="channel-one",
        roster_fails=True)["group_members"])
    assert result["status"] == "unavailable"
    assert "members" not in result and "scope" not in result


# ---- lookup_person -----------------------------------------------------------


async def test_an_alias_resolves_to_the_person_it_names(persons):
    """The operator's need, verbatim: "if somebody says 阿豹 she should know
    that is Tyler Cortez"."""
    result = await _call(_build(persons=persons)["lookup_person"], name="阿豹")
    assert result["status"] == "ok"
    assert result["name"] == "Tyler Cortez"


async def test_lookup_person_is_blind_to_someone_who_is_not_in_this_room(persons):
    """`elsewhere_liu` is in identity.toml — `persons.resolve` would find her
    from her phone number this instant — and she is not in this group."""
    result = await _call(_build(persons=persons)["lookup_person"], name="外人")
    assert result["status"] == "not_found"


async def test_not_being_here_is_indistinguishable_from_not_existing(persons):
    """Returning "exists but not here" would disclose membership — and the
    existence of people in Nora's other rooms — through a behaviour
    difference. That is the roster leak again, spelled slowly."""
    tools = _build(persons=persons)
    absent = await _call(tools["lookup_person"], name="外人")
    invented = await _call(tools["lookup_person"], name="Nobody At All")
    assert absent == invented | {"asked_about": "外人"}
    assert absent["status"] == invented["status"] == "not_found"


async def test_a_model_supplied_identifier_is_never_echoed_back(persons):
    """`asked_about` parrots the model's own word so a turn with two lookups can
    tell the answers apart — but the word is model-supplied, and R-18 fails
    closed on the WHOLE reply, so parroting a phone number would cost the
    entire message. Every other field these tools return is safe by
    construction; this one is made safe explicitly."""
    tools = _build(persons=persons)
    result = await _call(tools["lookup_person"], name="+85288888888")
    assert result["status"] == "not_found"
    assert "asked_about" not in result
    assert scan_identifiers(repr(result)) is None


_HANDLES = """
[[persons.tyler_cortez.instagram]]
username = "Harbour.Kite_88"
igid = "10000002"
[[persons.elsewhere_liu.instagram]]
username = "far_away_handle"
fbid = "100000000000003"
"""


@pytest.fixture
def handled(tmp_path):
    """IDENTITY plus Instagram usernames: Tyler's, with an `igid` and no `fbid`
    (the handle index must not depend on the fbid pairing), and one for
    `elsewhere_liu`, who is in no group under test. Usernames are invented."""
    path = tmp_path / "identity.toml"
    path.write_text(IDENTITY + _HANDLES)
    return load_persons(path)


@pytest.mark.parametrize("tag", ["@harbour.kite_88", "Harbour.Kite_88", " @HARBOUR.KITE_88 "])
async def test_an_instagram_username_resolves_to_the_member_it_was_recorded_for(handled, tag):
    """A tag of a known member's Instagram username resolves: the username is
    in identity.toml, matched case-insensitively with `@` stripped."""
    result = await _call(_build(persons=handled)["lookup_person"], name=tag)
    assert result["status"] == "ok"
    assert result["name"] == "Tyler Cortez"


async def test_an_instagram_username_of_someone_not_in_this_room_resolves_to_nobody(handled):
    """The room is the scope: a username recorded for a person who is not a
    member here reads exactly like a username nobody recorded."""
    tools = _build(persons=handled)
    absent = await _call(tools["lookup_person"], name="@far_away_handle")
    invented = await _call(tools["lookup_person"], name="@nobody_recorded_this")
    assert absent["status"] == invented["status"] == "not_found"


async def test_a_name_typed_as_a_tag_resolves_like_the_name(persons):
    result = await _call(_build(persons=persons)["lookup_person"], name="@阿豹")
    assert result["status"] == "ok" and result["name"] == "Tyler Cortez"


async def test_lookup_person_reports_an_ambiguous_nickname_rather_than_picking(persons, tmp_path):
    path = tmp_path / "twins.toml"
    path.write_text(
        '[persons.a]\nname = "Ah Ming"\naliases = ["阿星"]\n'
        '[[persons.a.signal]]\nphone = "+85211111111"\n'
        '[persons.b]\nname = "Ming Chai"\naliases = ["阿星"]\n'
        '[[persons.b.signal]]\nphone = "+85222222222"\n'
    )
    tools = _build(
        persons=load_persons(path),
        members=(frozenset({"+85211111111"}), frozenset({"+85222222222"})),
    )
    result = await _call(tools["lookup_person"], name="阿星")
    assert result["status"] == "ambiguous"
    assert sorted(result["could_be"]) == ["Ah Ming", "Ming Chai"]


async def test_lookup_person_says_unavailable_not_not_found_when_it_could_not_look(persons):
    """A miss while the roster is unreadable is «I could not check», never
    «nobody here is called that» — the second is a claim about a room Nora
    cannot see."""
    metrics = Metrics()
    tools = _build(persons=persons, roster_fails=True, metrics=metrics)
    result = await _call(tools["lookup_person"], name="阿豹")
    assert result["status"] == "unavailable"
    assert metrics.get(
        "nora_room_lookups_total", tool="lookup_person", status="unavailable",
    ) == 1


async def test_lookup_person_still_names_the_tagger_when_the_daemon_is_down(persons):
    """The narrowed scope is a strict SUBSET of the room — Nora received their
    message in it — so it can never resolve somebody outside this
    conversation, and it keeps her able to answer about the person in front of
    her while the roster is unreadable."""
    tools = _build(
        persons=persons, roster_fails=True, tagger=persons.by_person["miles_novak"],
    )
    assert (await _call(tools["lookup_person"], name="俊爺"))["name"] == "Miles Novak"
    assert (await _call(tools["lookup_person"], name="外人"))["status"] == "unavailable"


async def test_a_model_named_in_a_lookup_argument_is_still_echoed_back(persons):
    """`asked_about` exists to tell two lookups in one turn apart, so dropping
    it costs the model the disambiguation it is for. A model name in a
    model-supplied argument discloses nothing — the model wrote it, and if it
    reaches the reply `Deliverer` blocks it there. Only identifiers are
    dropped."""
    result = await _call(_build(persons=persons)["lookup_person"], name="Claude")
    assert result["status"] == "not_found"
    assert result["asked_about"] == "Claude"


async def test_no_room_scoped_tool_can_return_a_phone_number_or_an_aci(persons):
    """R-18 as a property of these tools rather than of the egress guard: what
    they hand the model must already be safe to say out loud, or the guard
    turns a leak into a silent turn."""
    tools = _build(persons=persons, members=(MILES, TYLER, STRANGER, NORA))
    answers = [
        await _call(tools["group_members"]),
        await _call(tools["lookup_person"], name="阿豹"),
        await _call(tools["lookup_person"], name="+85290000102"),
    ]
    for answer in answers:
        assert scan_identifiers(repr(answer)) is None, answer


async def test_a_phone_number_is_not_a_name_and_resolves_nobody(persons):
    """These tools speak names on both sides. A model that supplies a platform
    id — because it saw one somewhere, or invented one — gets nothing, so they
    cannot be used to confirm an id belongs to a member."""
    tools = _build(persons=persons)
    for value in ("+85290000102", "a1000001-0000-4000-8000-000000000001"):
        assert (await _call(tools["lookup_person"], name=value))["status"] == "not_found"


# ---- the memory channel carries the prefill's demotion ----------------------
#
# `prefill._transcript_block` puts a framing sentence above `transcript`
# and `untrusted="true"` on the tag. `load_transcript` hands the model the SAME
# text — verbatim group-authored bodies — through a channel that must carry the
# same demotion: `untrusted: True` plus the `framing` sentence, stamped by
# `_memory_result`.


async def test_load_transcript_frames_verbatim_group_words_as_untrusted_memory(persons):
    """The envelope is why this tool returns a dict at all: a bare `list` has
    nowhere to put the demotion, and the marker must not live only in a
    docstring the result itself then carries nothing of."""
    from nora.agent.toolkit.results import _MEMORY_FRAMING

    out = await _call(_build(persons=persons)["load_transcript"], limit=40)

    assert out["messages"] == []                     # `_NullStore` — shape, not content
    assert out["untrusted"] is True
    assert out["framing"] == _MEMORY_FRAMING


# ---- ADR 0015: how to report a status rides in the result -------------------


def _twins(tmp_path):
    path = tmp_path / "twins.toml"
    path.write_text(
        '[persons.a]\nname = "Ah Ming"\naliases = ["阿星"]\n'
        '[[persons.a.signal]]\nphone = "+85211111111"\n'
        '[persons.b]\nname = "Ming Chai"\naliases = ["阿星"]\n'
        '[[persons.b.signal]]\nphone = "+85222222222"\n'
    )
    return _build(
        persons=load_persons(path),
        members=(frozenset({"+85211111111"}), frozenset({"+85222222222"})),
    )


async def test_every_non_ok_room_tool_status_carries_a_note(persons, tmp_path):
    """The descriptions only NAME the statuses; how to report each one is the
    result's `note` (ADR 0015). Every non-ok path of the room tools is driven
    here, so one that loses its note, or grows a dash back, goes red."""
    twins = _twins(tmp_path)
    down = _build(persons=persons, roster_fails=True)
    cases = {
        "unavailable/group_members": (down["group_members"], {}),
        "unavailable/lookup_person": (down["lookup_person"], {"name": "阿豹"}),
        "not_found/lookup_person": (_build(persons=persons)["lookup_person"], {"name": "外人"}),
        "ambiguous/lookup_person": (twins["lookup_person"], {"name": "阿星"}),
    }
    for case, (tool, kwargs) in cases.items():
        out = await _call(tool, **kwargs)
        assert out["status"] == case.split("/")[0], (case, out)
        assert isinstance(out.get("note"), str) and out["note"].strip(), case
        assert "—" not in out["note"] and "–" not in out["note"], case

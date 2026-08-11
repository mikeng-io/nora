"""`group_members`, `lookup_person`, `about` — the three room-scoped tools.

The rules under test are design/12's, and each one was paid for:

* **The model names WHAT, Nora resolves WHERE** (DEC-21/DEC-24). None of the
  three declares a conversation, a peer id or a phone number, so there is
  nothing for the model to invent and nothing it can address outside this room.
* **Scope is enforced at ASSEMBLY, not egress** (design/12 §3). A person in
  `identity.toml` who is not in this conversation is indistinguishable from a
  person who does not exist. R-18 cannot help here — the roster that leaked in
  2026-08-06 carried only names.
* **A tool that cannot answer says so in its RETURN VALUE.** «asked and nothing
  known» and «could not ask» are different answers; conflating them is what hid
  a dead `peer_card` endpoint for the life of the deployment.
"""

from __future__ import annotations

import pytest

from nora.agent.egress import scan_identifiers
from nora.agent.room import Room
from nora.agent.tools import build_tools
from nora.infra.transit import TransitClient
from nora.obs.metrics import Metrics
from nora.persons import load_persons
from nora.testing import FakeRosterSource
from tests.fakes import FakeHoncho, SinkSender

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
):
    source = (
        FakeRosterSource(fail=True) if roster_fails
        else FakeRosterSource(groups={GROUP: tuple(members)})
    )
    metrics = metrics or Metrics()
    room = Room(
        platform="signal", conversation_id=GROUP, roster_source=source,
        persons=persons, tagger=tagger, self_ids=NORA,
    )
    tools = build_tools(
        store=_NullStore(), conversation_id=GROUP, conversation_type="group",
        persons=persons, honcho=honcho or FakeHoncho(), deliverer=SinkSender(),
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
    conversation. The other two take ONE argument, a name, because a name is
    the only thing in the exchange the model genuinely decides. A conversation,
    a peer id, a phone number or the search query would each be a value Nora
    already holds, and DEC-24 measured what declaring one costs: on the first
    real tag the model invented a group id three times and the turn died mute.
    """
    tools = _build(persons=persons)
    assert tools["group_members"].tool_spec["inputSchema"]["json"].get("properties") in (
        None, {},
    )
    assert set(
        tools["lookup_person"].tool_spec["inputSchema"]["json"]["properties"]
    ) == {"name"}
    assert set(tools["about"].tool_spec["inputSchema"]["json"]["properties"]) == {"person"}


def test_no_room_scoped_tool_accepts_a_platform_identifier(persons):
    tools = _build(persons=persons)
    forbidden = {"conversation_id", "conversation_type", "peer_id", "sender_id",
                 "person_id", "phone", "uuid", "search_query", "query"}
    for name in ("group_members", "lookup_person", "about"):
        properties = set(
            tools[name].tool_spec["inputSchema"]["json"].get("properties") or {}
        )
        assert not properties & forbidden, f"{name} makes the model supply {properties}"


def test_every_room_scoped_tool_has_a_selection_policy_in_its_docstring(persons):
    """The docstring IS the schema (`FunctionToolMetadata.extract_metadata`),
    and design/12 puts WHEN TO REACH FOR A TOOL there rather than in another
    prose catalogue in the prompt."""
    tools = _build(persons=persons)
    for name in ("group_members", "lookup_person", "about"):
        doc = (tools[name].__doc__ or "").lower()
        assert len(doc) > 200, f"{name}'s docstring is too thin to steer a model"
        assert "call this" in doc or "do not call" in doc, name


# ---- group_members -----------------------------------------------------------


async def test_group_members_returns_this_rooms_people_by_name(persons):
    result = await _call(_build(persons=persons)["group_members"])
    assert result["status"] == "ok"
    assert result["members"] == ["Miles Novak", "Tyler Cortez"]


async def test_group_members_never_lists_someone_who_is_not_in_this_room(persons):
    """The 2026-08-06 leak, as a test. Asked who was in the group, Nora listed
    five people who had never been members, because the prompt was handed the
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
    construction; this one had to be made safe."""
    tools = _build(persons=persons)
    result = await _call(tools["lookup_person"], name="+85288888888")
    assert result["status"] == "not_found"
    assert "asked_about" not in result
    assert scan_identifiers(repr(result)) is None


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
    her during the failure mode this deployment has actually had."""
    tools = _build(
        persons=persons, roster_fails=True, tagger=persons.by_person["miles_novak"],
    )
    assert (await _call(tools["lookup_person"], name="俊爺"))["name"] == "Miles Novak"
    assert (await _call(tools["lookup_person"], name="外人"))["status"] == "unavailable"


# ---- about ----------------------------------------------------------


async def test_recall_returns_what_is_remembered_about_someone_in_the_room(persons):
    honcho = FakeHoncho(conclusions={"miles_novak": ["Miles is from Ridgeline", "Miles drinks espresso"]})
    result = await _call(_build(persons=persons, honcho=honcho)["about"], person="俊爺")
    assert result["status"] == "ok"
    assert result["person"] == "Miles Novak"
    assert result["profile"] == ["Miles is from Ridgeline", "Miles drinks espresso"]


async def test_recall_asks_the_question_the_group_actually_asked(persons):
    """`search_query` comes from the tagging message so the conclusions are
    curated to it — and it is Nora's to supply, never a parameter the model
    has to invent (DEC-24)."""
    honcho = FakeHoncho(conclusions={"miles_novak": ["something"]})
    tools = _build(persons=persons, honcho=honcho, question="佢係咪識踢波")
    await _call(tools["about"], person="Miles")
    assert honcho.queries == [("nora", "miles_novak", "佢係咪識踢波")]


async def test_recall_is_blind_to_someone_who_is_not_in_this_room(persons):
    """Same scope as `lookup_person`, and it has to be: a memory lookup that
    could reach any peer in the workspace is an address-book dump with extra
    steps (design/11 §1.5)."""
    honcho = FakeHoncho(conclusions={"elsewhere_liu": ["a secret about her"]})
    result = await _call(_build(persons=persons, honcho=honcho)["about"], person="外人")
    assert result["status"] == "not_found"
    assert honcho.queries == []          # her peer is never even asked about


async def test_ask_memory_reasons_from_noras_model_of_the_person(persons):
    """`ask_memory` is the DIALECTIC verb: it reads `(observer=nora,
    observed=person)` through `chat`, carrying the tagging question and the
    model's chosen depth (design/24 §4). The answer is the reasoned string."""
    honcho = FakeHoncho(conclusions={"miles_novak": ["plays five-a-side on Sundays"]})
    tools = _build(persons=persons, honcho=honcho, question="佢鍾唔鍾意踢波")
    result = await _call(tools["ask_memory"], person="Miles", depth="high")
    assert honcho.chats == [("nora", "miles_novak", "佢鍾唔鍾意踢波", "high")]
    assert result["status"] == "ok"
    assert result["answer"] == "plays five-a-side on Sundays"


async def test_ask_memory_depth_falls_back_to_low_when_off_list(persons):
    """Depth is the model's per-question choice, but only from Honcho's ladder;
    anything off-list is the fast default, never passed through raw."""
    honcho = FakeHoncho(conclusions={"miles_novak": ["likes hiking"]})
    tools = _build(persons=persons, honcho=honcho)
    await _call(tools["ask_memory"], person="Miles", depth="turbo")
    assert honcho.chats[0][3] == "low"


async def test_ask_memory_is_blind_to_someone_not_in_this_room(persons):
    """Same roster gate as `about`: a reasoning call that could reach any peer
    in the workspace is a cross-room leak with extra steps. Never asked."""
    honcho = FakeHoncho(conclusions={"elsewhere_liu": ["a secret about her"]})
    result = await _call(_build(persons=persons, honcho=honcho)["ask_memory"], person="外人")
    assert result["status"] == "not_found"
    assert honcho.chats == []


async def test_ask_memory_withholds_a_reasoned_answer_that_names_an_identifier(persons):
    """R-18 fails closed on the whole reply, and this answer is ONE indivisible
    string — if it carries an identifier it is withheld whole, never handed to
    the model to echo (the `about` rationale, applied to a reasoned blob)."""
    honcho = FakeHoncho(conclusions={"miles_novak": ["reach him on +85298765432"]})
    tools = _build(persons=persons, honcho=honcho)
    result = await _call(tools["ask_memory"], person="Miles")
    assert result["status"] == "nothing_known"
    assert result["withheld"] == 1
    assert "98765432" not in str(result)


async def test_ask_memory_is_unavailable_when_honcho_cannot_answer(persons):
    """`chat` returns None for both unreachable and nothing-to-say; neither is a
    licence to claim ignorance of the PERSON — the tool says `unavailable`."""
    honcho = FakeHoncho(conclusions={})           # miles_novak seeds nothing → chat None
    tools = _build(persons=persons, honcho=honcho)
    result = await _call(tools["ask_memory"], person="Miles")
    assert result["status"] == "unavailable"
    assert result["person"] == "Miles Novak"


async def test_search_memory_surfaces_only_roster_speakers_and_screens_identifiers(persons):
    """design/24 §S.3: search_memory hands back RAW words, so it is the tightest-
    gated recall verb — only messages from people in THIS room (never Nora's own,
    never an off-room peer), and R-18 drops any line carrying an identifier."""
    honcho = FakeHoncho()
    honcho.messages = [
        ("miles_novak", "book 咗七點枱"),                 # a roster speaker → kept
        ("nora", "好呀"),                              # Nora's own reply → dropped
        ("miles_novak", "call 佢 +85298765432"),          # carries a phone → R-18 drops it
        ("elsewhere_liu", "a secret about her"),      # not in this room → dropped
    ]
    tools = _build(persons=persons, honcho=honcho)
    result = await _call(tools["search_memory"], query="枱")
    assert honcho.searches == ["枱"]
    assert result["status"] == "ok"
    assert result["results"] == [{"who": "Miles Novak", "said": "book 咗七點枱"}]


async def test_search_memory_nothing_found_when_no_roster_speaker_matches(persons):
    honcho = FakeHoncho()
    honcho.messages = [("nora", "只有我講嘢")]           # only Nora → filtered → empty
    metrics = Metrics()
    tools = _build(persons=persons, honcho=honcho, metrics=metrics)
    result = await _call(tools["search_memory"], query="x")
    assert result["status"] == "nothing_found"
    # A miss is recorded as a MISS. It was recorded as `ok` until 2026-08-11,
    # which made hit-rate unreadable — searching constantly and finding nothing
    # looked identical to searching constantly and succeeding.
    assert metrics.get("nora_room_lookups_total",
                       tool="search_memory", status="nothing_found") == 1


async def test_search_memory_reports_an_outage_as_unavailable_not_as_nothing_found(persons):
    """R-27 at the tool boundary, and the one that was WRONG in a way no test
    could see (external review, 2026-08-11).

    A Honcho outage used to arrive here as `[]` — because the adapter catches
    everything — and fell through to «I couldn't find anything like that in
    what's been said here», counted `ok`. An ERROR reading as a PASS, invisible
    because "nothing matched" is a true-sounding sentence for a search that
    never ran. An `except` in this function did not fix it either: the adapter
    never raises, so that handler was unreachable code that read as a fix.

    `MessageHits.reachable` is the real signal, and the fake carries it because
    the real client does."""
    honcho = FakeHoncho()
    honcho.messages = [("miles_novak", "book 咗七點枱")]   # would match, if it could run
    honcho.fail = True
    tools = _build(persons=persons, honcho=honcho)

    result = await _call(tools["search_memory"], query="枱")

    assert result["status"] == "unavailable"
    assert "couldn't look" in result["note"]
    assert "nothing" not in result["note"].lower().replace("do not say there was nothing", "")


async def test_search_memory_says_it_cannot_repeat_what_it_found_when_all_hits_are_screened(
    persons,
):
    """The other half of the same distinction. A result set emptied by the
    EGRESS filter is not «nobody said that» — the words exist and she may not
    repeat them, and saying nobody said them is false.

    DEC-95 made this likely rather than theoretical: the filter moved to
    `scan_reply`, which drops any line naming an LLM, and this is a group of
    software engineers who say «claude» constantly."""
    honcho = FakeHoncho()
    honcho.messages = [("miles_novak", "琴日我用 claude 寫個 script 好撚快")]
    metrics = Metrics()
    tools = _build(persons=persons, honcho=honcho, metrics=metrics)

    result = await _call(tools["search_memory"], query="script")

    # `withheld`, NOT `nothing_found`. The model keys on `status` and reads the
    # note second, so encoding this as «nothing was said» leaves the true answer
    # depending on how far it read (external review, 2026-08-11).
    assert result["status"] == "withheld"
    assert result["found_but_unquotable"] == 1
    assert "can't quote" in result["note"]

    # And the METRIC label moves with the payload. This half was decorative
    # until now: a verification pass reverted both `_record_room` statuses back
    # to "ok", left the returned payloads untouched, and 110 tests stayed green.
    # `nora_room_lookups_total` is the series DEC-30's climbing-withheld alert
    # reads, so a refactor collapsing it into `ok` would take the alert flat
    # with nothing noticing.
    assert metrics.get("nora_room_lookups_total",
                       tool="search_memory", status="withheld") == 1


async def test_search_memory_refuses_in_a_room_with_someone_it_cannot_place(persons):
    """A raw-message search must NOT surface the group's history into a room
    holding an outsider Nora can't place (R-31, fail-closed) — and it never even
    reaches Honcho."""
    honcho = FakeHoncho()
    honcho.messages = [("miles_novak", "something")]
    stranger = frozenset({"+85260000000"})              # not in identity.toml
    tools = _build(persons=persons, honcho=honcho, members=(MILES, TYLER, NORA, stranger))
    result = await _call(tools["search_memory"], query="x")
    assert result["status"] == "refused"
    assert honcho.searches == []                         # never queried


async def test_nothing_known_is_distinguishable_from_could_not_ask(persons):
    """THE distinction. Honcho answers 200-with-an-empty-string for a peer it
    has never heard of, so at the HTTP layer these two are the same event —
    and `peer_card` collapsing them is why a dead endpoint went unnoticed for
    the life of the deployment."""
    empty = Metrics()
    quiet = await _call(
        _build(persons=persons, honcho=FakeHoncho(), metrics=empty)["about"],
        person="Miles",
    )
    down = Metrics()
    broken = await _call(
        _build(persons=persons, honcho=FakeHoncho(fail=True), metrics=down)["about"],
        person="Miles",
    )
    assert quiet["status"] == "nothing_known"
    assert broken["status"] == "unavailable"
    assert quiet != broken
    assert empty.get("nora_recall_total", status="empty") == 1
    assert down.get("nora_recall_total", status="unavailable") == 1
    assert empty.get("nora_recall_total", status="unavailable") == 0


async def test_a_raising_memory_store_lands_on_unavailable_not_on_empty(persons):
    class Exploding(FakeHoncho):
        async def representation(self, **kw):
            raise RuntimeError("connection reset")

    tools = _build(persons=persons, honcho=Exploding())
    result = await _call(tools["about"], person="Miles")
    assert result["status"] == "unavailable"


async def test_recall_withholds_a_conclusion_that_carries_an_identifier(persons):
    """Honcho conclusions are distilled from what people actually said, so they
    can and do carry a phone number. R-18 fails closed on the WHOLE reply, so
    letting one through would cost the entire message rather than one fact —
    dropping it here is strictly better than blocking at egress."""
    honcho = FakeHoncho(conclusions={"miles_novak": [
        "Miles is from Ridgeline",
        "Miles's number is +85290000101",
        "Miles's ACI is a1000001-0000-4000-8000-000000000001",
    ]})
    result = await _call(_build(persons=persons, honcho=honcho)["about"], person="Miles")
    assert result["profile"] == ["Miles is from Ridgeline"]
    assert result["withheld"] == 2


async def test_recall_keeps_a_conclusion_that_merely_names_an_llm(persons):
    """The bug the split fixes. `about` filtered conclusions through the REPLY
    predicate, whose `_MODELNAME` pattern matches nine words — including
    `claude`, `gemini`, `haiku` and `opus`. In a group of software engineers
    that silently withheld a large share of everything Honcho had ever
    concluded, and the only trace was a `withheld` count.

    What the model she runs on is remains hers alone: that is a property of
    the REPLY (R-6/R-18, `Deliverer`), not of a durable memory about a person.
    A number in the same list is still dropped, because that one is unsafe
    everywhere."""
    honcho = FakeHoncho(conclusions={"miles_novak": [
        "Miles ships an app that calls Claude and Gemini",
        "Miles prefers haiku over sonnet for cheap classification",
        "Miles's number is +85290000101",
    ]})
    result = await _call(_build(persons=persons, honcho=honcho)["about"], person="Miles")
    assert result["status"] == "ok"
    assert result["profile"] == [
        "Miles ships an app that calls Claude and Gemini",
        "Miles prefers haiku over sonnet for cheap classification",
    ]
    assert result["withheld"] == 1


async def test_a_model_named_in_a_lookup_argument_is_still_echoed_back(persons):
    """`asked_about` exists to tell two lookups in one turn apart, so dropping
    it costs the model the disambiguation it was added for. A model name in a
    model-supplied argument discloses nothing — the model wrote it, and if it
    reaches the reply `Deliverer` blocks it there. Only identifiers are dropped
    (see the test above this one's sibling)."""
    result = await _call(_build(persons=persons)["lookup_person"], name="Claude")
    assert result["status"] == "not_found"
    assert result["asked_about"] == "Claude"


async def test_no_room_scoped_tool_can_return_a_phone_number_or_an_aci(persons):
    """R-18 as a property of these tools rather than of the egress guard: what
    they hand the model must already be safe to say out loud, or the guard
    turns a leak into a silent turn."""
    honcho = FakeHoncho(conclusions={"miles_novak": ["reachable on +85290000101"]})
    tools = _build(persons=persons, members=(MILES, TYLER, STRANGER, NORA), honcho=honcho)
    answers = [
        await _call(tools["group_members"]),
        await _call(tools["lookup_person"], name="阿豹"),
        await _call(tools["lookup_person"], name="+85290000102"),
        await _call(tools["about"], person="Miles"),
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
        assert (await _call(tools["about"], person=value))["status"] == "not_found"

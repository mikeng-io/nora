"""`<presence>` — where she is, generated every turn.

Each test names ONE network or ONE state rather than looping over the name
table: a test parametrized over the constant it pins cannot fail.
"""

from __future__ import annotations

import asyncio
import re
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

import nora.agent.prefill as prefill
from nora.agent.prefill import _BLOCKS, build_prefill
from nora.app.composition import _presence_reader
from nora.domain.models import NormalizedMessage, TagContext
from nora.domain.ports import AdapterHealth
from nora.domain.self_presence import Presence, PresenceRoom
from nora.engagement.self_presence import PresenceReader
from nora.language.presence_render import render_presence
from nora.platform.discord.adapter import DiscordAdapter
from nora.platform.instagram.self_id import derive_identity, read_username
from nora.platform.registry import AdapterRegistry
from nora.policy import Conversation, Policy, load_policy


def _ctx(platform: str = "signal", **kwargs) -> TagContext:
    msg = NormalizedMessage(platform, "g1", "group", "+852111", "Miles", 1, "hi", {})
    return TagContext("tag-1", msg, None, (), (), 0.0, **kwargs)


def _turn_text(ctx: TagContext) -> str:
    """The per-turn message, where anything that varies between turns lives."""
    return build_prefill(ctx, display_name="Nora", context_window=40)[1]["content"][0]["text"]


# ---- display names --------------------------------------------------


def test_whatsapp_is_named_as_the_room_calls_it_not_by_its_wire_key():
    text = _turn_text(_ctx(platform="whatsapp"))

    assert "WhatsApp" in text
    assert "whatsapp" not in text


def test_instagram_is_named_as_the_room_calls_it_not_by_its_wire_key():
    text = _turn_text(_ctx(platform="instagram"))

    assert "Instagram" in text
    assert "instagram" not in text


# ---- the reader, the renderer, and what they must never say ----------------

#: Two units. `friends` has three rooms; `ridgeline` is another group entirely,
#: and nothing about it — not its label, not its network — may reach a
#: friends turn (D1 = B).
_POLICY = '''
default = "deny"

[[conversations]]
platform = "signal"
id = "SIGNAL-FRIENDS-ID"
label = "Friends"
persons = "*"
unit = "friends"

[[conversations]]
platform = "discord"
id = "111/222"
label = "Zone 42"
persons = "*"
unit = "friends"

[[conversations]]
platform = "whatsapp"
id = "WA-FRIENDS-ID"
label = "Friends WA"
persons = "*"
unit = "friends"

[[conversations]]
platform = "instagram"
id = "IG-RIDGELINE-ID"
label = "Ridgeline Secret Club"
persons = "*"
unit = "ridgeline"
'''


class _Scripted:
    """Local scaffolding for the reader's one verb: a `health()` that answers
    what the test says, and counts how often it was asked."""

    def __init__(self, platform: str, *, connected=True, degraded=False, raises=False):
        self.platform = platform
        self.health_now = AdapterHealth(
            platform=platform, connected=connected, degraded=degraded,
            detail="instagram session is 'checkpointed'" if degraded else "",
        )
        self.raises = raises
        self.calls = 0

    async def health(self) -> AdapterHealth:
        self.calls += 1
        if self.raises:
            raise RuntimeError("bridge unreachable")
        return self.health_now


@pytest.fixture
def policy(tmp_path):
    path = tmp_path / "policy.toml"
    path.write_text(_POLICY)
    return load_policy(path)


def _adapters(**overrides):
    base = {
        "signal": _Scripted("signal"),
        "discord": _Scripted("discord"),
        "whatsapp": _Scripted("whatsapp"),
        "instagram": _Scripted("instagram"),
    }
    base.update(overrides)
    return base


async def _body(reader: PresenceReader, platform="signal", cid="SIGNAL-FRIENDS-ID") -> str:
    presence = await reader.read(platform=platform, conversation_id=cid)
    return render_presence(presence).body


async def test_this_units_rooms_are_named_by_network_and_label(policy):
    body = await _body(PresenceReader(policy=policy, adapters=_adapters()))

    assert body.startswith("You are in «Friends» on Signal.")
    assert (
        "This group also has rooms on Discord («Zone 42») and WhatsApp («Friends WA»)."
        in body
    )


async def test_another_units_room_is_never_named_only_said_to_exist(policy):
    """D1 = B. The ridgeline room's label and its network must not reach a
    friends turn; that other groups EXIST must."""
    body = await _body(PresenceReader(policy=policy, adapters=_adapters()))

    assert "Ridgeline" not in body
    assert "Instagram" not in body   # ridgeline is the only Instagram room
    assert "You are also in other groups, which you do not discuss here." in body


async def test_a_unit_alone_in_policy_does_not_claim_other_groups(tmp_path):
    path = tmp_path / "policy.toml"
    path.write_text(_POLICY.split('[[conversations]]\nplatform = "instagram"')[0])
    body = await _body(PresenceReader(policy=load_policy(path), adapters=_adapters()))

    assert "other groups" not in body


async def test_no_room_id_reaches_the_block():
    """R-18. The WhatsApp room here has NO label, which is the case that
    matters: `UnitRoom` falls back to `primary_id` for exactly that, and a
    reader copying it would put a room id in every turn.

    Built as a `Policy` directly rather than through `load_policy`: an
    allowed room with no label is a STARTUP error there, so a `policy.toml`
    cannot carry the case this test proves is handled — the render-time floor
    stays defense in depth regardless."""
    convs = (
        Conversation(
            platform="signal", ids=frozenset({"SIGNAL-FRIENDS-ID"}), persons=None,
            label="Friends", primary_id="SIGNAL-FRIENDS-ID", unit="friends",
        ),
        Conversation(
            platform="discord", ids=frozenset({"111/222"}), persons=None,
            label="Zone 42", primary_id="111/222", unit="friends",
        ),
        Conversation(
            platform="whatsapp", ids=frozenset({"WA-FRIENDS-ID"}), persons=None,
            primary_id="WA-FRIENDS-ID", unit="friends",
        ),
        Conversation(
            platform="instagram", ids=frozenset({"IG-RIDGELINE-ID"}), persons=None,
            label="Ridgeline Secret Club", primary_id="IG-RIDGELINE-ID", unit="ridgeline",
        ),
    )
    policy = Policy(
        conversations=convs, _by_id={(c.platform, c.primary_id): c for c in convs},
    )
    body = await _body(PresenceReader(policy=policy, adapters=_adapters()))

    assert "and WhatsApp." in body          # named by its network alone
    for rid in ("SIGNAL-FRIENDS-ID", "111/222", "WA-FRIENDS-ID", "IG-RIDGELINE-ID"):
        assert rid not in body


async def test_a_degraded_adapter_needs_a_person_even_while_connected(policy):
    """A checkpointed session can hold its socket open and refuse every send.
    `degraded` wins over `connected`, and `detail` (the operator's
    diagnostic) is never rendered."""
    reader = PresenceReader(policy=policy, adapters=_adapters(
        whatsapp=_Scripted("whatsapp", connected=True, degraded=True),
    ))
    body = await _body(reader)

    assert "WhatsApp is down and needs a person to fix it" in body
    assert "checkpointed" not in body


async def test_a_disconnected_adapter_is_reconnecting_and_the_rest_are_up(policy):
    reader = PresenceReader(policy=policy, adapters=_adapters(
        discord=_Scripted("discord", connected=False),
    ))
    body = await _body(reader)

    assert "Discord is reconnecting right now; the others are up." in body


async def test_a_health_read_that_raises_is_unchecked_never_up(policy):
    reader = PresenceReader(policy=policy, adapters=_adapters(
        discord=_Scripted("discord", raises=True),
    ))
    body = await _body(reader)

    assert "Discord's connection could not be checked" in body


async def test_a_network_with_no_adapter_is_not_running(policy):
    adapters = _adapters()
    del adapters["whatsapp"]
    body = await _body(PresenceReader(policy=policy, adapters=adapters))

    assert "WhatsApp is not running in this deployment" in body


async def test_health_is_read_on_every_turn_never_cached(policy):
    """A cached «up» is how she promises a send that then fails."""
    discord = _Scripted("discord")
    reader = PresenceReader(policy=policy, adapters=_adapters(discord=discord))

    first = await _body(reader)
    discord.health_now = AdapterHealth(platform="discord", connected=False)
    second = await _body(reader)

    assert discord.calls == 2
    assert "Discord is reconnecting" not in first
    assert "Discord is reconnecting" in second


async def test_declared_handles_render_grouped_by_handle(policy):
    reader = PresenceReader(policy=policy, adapters=_adapters(), handles={
        "signal": "@nora.000", "whatsapp": "@noraexample",
        "x": "noraexample", "threads": "@noraexample",
    })
    body = await _body(reader)

    assert (
        "You are @nora.000 on Signal, and @noraexample on WhatsApp, X and Threads."
        in body
    )


async def test_a_handle_neither_source_has_is_omitted_never_guessed(policy):
    reader = PresenceReader(policy=policy, adapters=_adapters(), handles={
        "signal": "@nora.000",
    })
    handle_line = (await _body(reader)).split("\n")[1]

    assert handle_line == "You are @nora.000 on Signal."


async def test_the_live_instagram_handle_beats_the_declared_one(policy):
    async def live():
        return "noraexample"

    reader = PresenceReader(
        policy=policy, adapters=_adapters(),
        handles={"instagram": "@not_____nora"},
        live_handles={"instagram": live},
    )
    body = await _body(reader)

    assert "@noraexample on Instagram" in body
    assert "not_____nora" not in body


async def test_a_failed_live_read_falls_back_to_the_declared_handle(policy):
    async def live():
        raise TimeoutError

    reader = PresenceReader(
        policy=policy, adapters=_adapters(),
        handles={"instagram": "@noraexample"}, live_handles={"instagram": live},
    )
    assert "@noraexample on Instagram" in await _body(reader)


async def test_a_live_read_with_nothing_declared_and_no_answer_is_omitted(policy):
    async def live():
        return None

    reader = PresenceReader(
        policy=policy, adapters=_adapters(), live_handles={"instagram": live},
    )
    assert "on Instagram" not in await _body(reader)


async def test_the_live_discord_handle_beats_the_declared_one(policy):
    """The same precedence Instagram's live read uses, wired through
    composition's `_presence_reader` — not just the
    reader taking whatever callable it is handed, but the ACTUAL DiscordAdapter
    registered under "discord"."""
    async def me(request):
        return web.json_response({"id": "9", "username": "nora", "discriminator": "1234"})

    app = web.Application()
    app.router.add_get("/users/@me", me)
    server = TestServer(app)
    await server.start_server()
    try:
        base = str(server.make_url("")).rstrip("/")
        discord = DiscordAdapter("tok", base_url=base)
        registry = AdapterRegistry(adapters={"discord": discord})
        cfg = SimpleNamespace(
            agent=SimpleNamespace(handles={"discord": "@old-name"}),
        )
        reader = _presence_reader(cfg, policy, registry)
        body = await _body(reader, platform="discord", cid="111/222")
    finally:
        await server.close()

    assert "@nora#1234 on Discord" in body
    assert "old-name" not in body


async def test_the_reader_never_raises_through_a_hanging_bridge(policy):
    async def hangs():
        await asyncio.sleep(10)

    reader = PresenceReader(
        policy=policy, adapters=_adapters(), live_handles={"instagram": hangs},
        timeout_seconds=0.05,
    )
    body = await _body(reader)
    assert body.startswith("You are in «Friends» on Signal.")


def test_the_block_is_situation_never_capability():
    """Telling a model what it CAN DO is not telling it what it is READING. No
    line of `<presence>` may say what she can do."""
    body = render_presence(Presence(
        platform="signal", label="Friends", here_state="up",
        unit_rooms=(PresenceRoom("discord", "Zone 42", "unknown"),),
        other_groups=True, handles=(("signal", "@nora.000"),),
    )).body

    assert not re.search(
        r"\b(can|able to|could)\b", body.replace("could not be checked", ""),
    )


def test_the_description_says_generated_and_to_trust_it_over_memory():
    rendered = render_presence(Presence.minimal("signal")).render()

    assert "Generated each turn" in rendered
    assert "trust it over any memory that disagrees" in rendered


def test_where_line_is_gone_and_presence_is_a_declared_row():
    assert not hasattr(prefill, "_where_line")
    assert ("presence", 5, "turn") in _BLOCKS


def test_presence_reaches_the_turn_through_assemble_not_a_bare_string():
    """A block with an opening tag, first after the clock — not a sentence
    concatenated in front of `assemble`."""
    text = _turn_text(_ctx(platform="discord", presence=Presence(
        platform="discord", label="Zone 42", here_state="up",
        unit_rooms=(), other_groups=False,
    )))
    after_clock = text.split("\n", 1)[1]

    assert after_clock.startswith("<presence ")
    assert "You are in «Zone 42» on Discord." in after_clock


def test_a_context_with_no_reader_still_says_which_network():
    """The fallback: no reader, still a
    situational model — and no claim about rooms or health nobody read."""
    text = _turn_text(_ctx(platform="discord"))
    block = text.split("<presence ")[1].split("</presence>")[0]

    assert "You are on Discord right now." in block
    assert "also has rooms" not in block
    assert "is up right now" not in block


async def test_the_instagram_username_is_read_but_never_becomes_an_identifier():
    """The handle is display text. `derive_identity` must not carry it, so it
    can never reach `self_aliases()` or match a sender."""
    async def health(_request):
        return web.json_response({
            "ok": True, "session": "authenticated", "connected": True,
            "self": {"fbid": "18247361241432477", "igid": "79597894900",
                     "username": "noraexample"},
        })

    app = web.Application()
    app.router.add_get("/health", health)
    server = TestServer(app)
    await server.start_server()
    try:
        base = str(server.make_url("")).rstrip("/")
        username = await read_username(base)
        _primary, identifiers = await derive_identity(base)
    finally:
        await server.close()

    assert username == "noraexample"
    assert all(i.value != "noraexample" for i in identifiers)


def test_a_tag_turn_pays_for_no_field_it_never_carries():
    """`this_room_is` is set only on the proactive pass. Declared as a type
    ANNOTATION it would render into `fields=` on every tag and fire turn,
    costing tokens each time; its meaning lives in the description instead,
    phrased to be true whether or not the attribute is there."""
    rendered = render_presence(Presence(platform="signal", label="Friends")).render()
    opening = rendered.split(">", 1)[0]

    assert "fields=" not in opening
    assert "A this_room_is, where there is one" in opening

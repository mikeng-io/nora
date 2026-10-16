"""The new outbound surfaces, driven by a REAL INBOUND MESSAGE — not by calling
`Deliverer.deliver`.

Everything DEC-82 (styles), DEC-86 (mentions) and DEC-89 (link carry) ships is
unit-tested at its own seam, but deleting the `LinkCarry.apply` call from
`Deliverer.deliver` would leave those green. That is DEC-42's "present, wired,
inert" failure: every piece correct, the wiring untested, and nothing that
fails when the feature stops happening.

So these tests start where a real turn starts — a signal-cli-shaped envelope
through `handler.on_event` — and end where a real turn ends, on what the adapter
was actually asked to SEND. Nothing in between is stubbed except the model,
which has to be scripted because the point is to pin the plumbing around it.

The assertions are on `sink.sent` / `sent_mentions` / `sent_styles`, i.e. the
wire, because that is the only place all three features are simultaneously
observable and the only place a mis-ordering between them shows up. `deliver()`
returning True proves nothing about whether the chip landed on the right span.
"""

from __future__ import annotations

from nora.domain.utf16 import utf16_len
from nora.testing import FakeRosterSource, ScriptedTurn, group_event

MILES = "+852111"
GRANT = "+852333"
GRANT_UUID = "a1000003-0000-4000-8000-000000000003"

IDENTITY = (
    '[persons.miles]\nname = "Miles"\n[[persons.miles.signal]]\nphone = "+852111"\n'
    '[persons.grant]\nname = "Grant"\naliases = ["Chi Ming"]\n'
    f'[[persons.grant.signal]]\nphone = "{GRANT}"\nuuid = "{GRANT_UUID}"\n'
)


def _says(text: str):
    return [ScriptedTurn(tool_calls=[("send_reply", {"text": text})], final_text=text)]


#: Who is actually in "g1". A mention is gated on the LIVE roster as well as on
#: identity.toml (DEC-86): the address book is per-deployment and possibly
#: stale, the roster is per-room and current, and tagging someone into a
#: conversation they are not in is the failure that gate exists to prevent. A
#: scenario that omits this gets no mention at all — fail-closed, not broken.
ROOM = FakeRosterSource(groups={"g1": (frozenset({MILES}), frozenset({GRANT, GRANT_UUID}))})


async def test_a_reply_written_with_emphasis_goes_out_styled_not_asterisked(
    harness_factory,
):
    """DEC-82 end to end. The model writes Markdown because models write
    Markdown; the group must see formatting, never `**`.

    CJK-adjacent on purpose: `\\w` is Unicode-aware, so 投/資 ARE word characters,
    and a naive conversion mangles exactly this shape into `*投資*方面` —
    half-stripped, asterisks still on screen. Cantonese has no spaces, so this
    is the COMMON case here, not an edge case."""
    h = await harness_factory(
        script=_says("**投資**方面我建議月供 ETF"), identity_toml=IDENTITY, roster=ROOM,
    )
    await h.handler.on_event(group_event(body="nora.000 點投資好?", sender=MILES, ts=1))
    await h.drain()

    _target, text = h.sink.sent[-1]
    assert text == "投資方面我建議月供 ETF"
    assert "*" not in text
    (style,) = h.sink.sent_styles[-1]
    assert (style.start, style.length) == (0, utf16_len("投資"))


async def test_a_reply_naming_someone_here_goes_out_as_a_real_mention(harness_factory):
    """DEC-86 + DEC-88 end to end: the model writes `@Grant` as prose — there
    is no mentions parameter on `send_reply`, deliberately, so a model can never
    hand over an ACI — and the composer turns it into a real range.

    She should be able to say she does not know and then ASK the person who
    does."""
    h = await harness_factory(
        script=_says("我唔知你幾高喎 @Grant 你幾高?"), identity_toml=IDENTITY, roster=ROOM,
    )
    await h.handler.on_event(
        group_event(body="nora.000 Grant 幾高?", sender=MILES, ts=1))
    await h.drain()

    _target, text = h.sink.sent[-1]
    (mention,) = h.sink.sent_mentions[-1]
    assert mention.start == utf16_len("我唔知你幾高喎 ")
    assert mention.length == utf16_len("@Grant")
    # The recipient rides in the PARALLEL field. An ACI in the text would have
    # made her mute (R-18), not leaked — but assert it directly anyway.
    assert GRANT_UUID not in text
    assert GRANT not in text


async def test_the_offsets_survive_an_emoji_before_the_mention(harness_factory):
    """The one arithmetic that cannot be eyeballed. Signal counts UTF-16 code
    units, and a non-BMP emoji is TWO of them while Python's `len` says one, so
    a naive implementation puts the chip one unit left of the name."""
    h = await harness_factory(
        script=_says("😅 @Grant 你幾高?"), identity_toml=IDENTITY, roster=ROOM,
    )
    await h.handler.on_event(group_event(body="nora.000 ?", sender=MILES, ts=1))
    await h.drain()

    (mention,) = h.sink.sent_mentions[-1]
    assert mention.start == 3            # 😅 is 2 units, then the space
    _target, text = h.sink.sent[-1]
    assert text[text.index("@"):].startswith("@Grant")


async def test_a_name_that_is_not_in_this_room_is_never_tagged(harness_factory):
    """The roster gate, from the inbound side. DEC-86 resolves through
    identity.toml AND the live group roster: identity.toml is the address book
    for every room, so resolving there alone would tag someone into a
    conversation they are not in."""
    h = await harness_factory(
        script=_says("@Nobody 你幾高?"), identity_toml=IDENTITY, roster=ROOM,
    )
    await h.handler.on_event(group_event(body="nora.000 ?", sender=MILES, ts=1))
    await h.drain()

    assert h.sink.sent_mentions[-1] == ()
    _target, text = h.sink.sent[-1]
    assert text == "@Nobody 你幾高?"      # sent plain, never refused


async def test_a_turn_can_send_more_than_one_message(harness_factory):
    """DEC-88's mechanism, end to end. `send_reply`'s budget is 3 and
    `tool_choice` relaxes to `auto` after the first send, so a turn CAN answer
    in parts — the doctrine decides whether it should."""
    h = await harness_factory(
        script=[ScriptedTurn(
            tool_calls=[("send_reply", {"text": "我唔知你幾高"}),
                        ("send_reply", {"text": "@Grant 你幾高?"})],
            final_text="ok",
        )],
        identity_toml=IDENTITY, roster=ROOM,
    )
    await h.handler.on_event(group_event(body="nora.000 ?", sender=MILES, ts=1))
    await h.drain()

    assert [t for _target, t in h.sink.sent] == ["我唔知你幾高", "@Grant 你幾高?"]
    assert h.sink.sent_mentions[0] == ()          # no name in the first
    assert len(h.sink.sent_mentions[1]) == 1      # tagged in the second

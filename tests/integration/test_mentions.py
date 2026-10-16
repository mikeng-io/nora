"""DEC-34: an @-mention reaches the model as a NAME, never as U+FFFC and never as a phone
number.

End-to-end, through the real ingest path, the real audit store and the real prefill: a
mentioning message is ingested ambiently, a tag follows, and the assertions are on the
prompt the model was actually handed. That matters here more than usual: the array
carrying the identity is read for the trigger check, so a layer that dropped it afterwards
would leave every function working and the model seeing a void.

Revert `MentionRenderer` (or the `rehydrate` call in `EngagementPipeline`) and every test
in this file goes red on a bare ￼.
"""

from __future__ import annotations

from nora.testing import ScriptedTurn, group_event

OWN = "+85290000100"          # the harness's own account; Nora's own mention
TYLER = "+852222"
TYLER_UUID = "a1000000-0000-4000-8000-000000000000"
PLACEHOLDER = "￼"

IDENTITY = (
    '[persons.miles]\nname = "Miles"\n[[persons.miles.signal]]\nphone = "+852111"\n'
    '[persons.tyler]\nname = "Tyler Cortez"\n[[persons.tyler.signal]]\n'
    f'phone = "{TYLER}"\nuuid = "{TYLER_UUID}"\n'
)


def _mention(who: str, *, start: int, uuid: str | None = None) -> dict:
    """One `mentions` entry, in signal-cli's real shape.

    `name` is the PHONE NUMBER on every row in `data/messages.db` — signal-cli
    does not put a display name there. It is included exactly so a renderer
    that reached for it would be caught putting a number in the prompt.
    """
    entry = {"name": who, "number": who, "start": start, "length": 1}
    if uuid:
        entry["uuid"] = uuid
    return entry


def _reply(text="ok"):
    return [ScriptedTurn(tool_calls=[("send_reply", {"text": text})], final_text=text)]


def _transcript(h) -> str:
    return h.last_prefill[1]["content"][0]["text"]


async def test_a_stored_mention_renders_the_persons_name(harness_factory):
    """THE PROBE: a placeholder, then a question about somebody. A void where the name goes
    (`￼ 邊個係阿豹妹`) must not reach her."""
    h = await harness_factory(script=_reply(), identity_toml=IDENTITY)
    await h.handler.on_event(group_event(
        body=f"{PLACEHOLDER} 邊個係阿豹妹", ts=1,
        mentions=[_mention(TYLER, start=0, uuid=TYLER_UUID)]))
    await h.handler.on_event(group_event(body="nora.000 ?", ts=2))
    await h.drain()

    transcript = _transcript(h)
    assert "@Tyler Cortez 邊個係阿豹妹" in transcript
    assert PLACEHOLDER not in transcript


async def test_a_message_that_was_only_a_mention_is_no_longer_empty(harness_factory):
    """A message whose entire content is one @-mention must not reach the model as a single
    invisible character and nothing else."""
    h = await harness_factory(script=_reply(), identity_toml=IDENTITY)
    await h.handler.on_event(group_event(
        body=PLACEHOLDER, ts=1, mentions=[_mention(TYLER, start=0)]))
    await h.handler.on_event(group_event(body="nora.000 ?", ts=2))
    await h.drain()

    assert "@Tyler Cortez" in _transcript(h)


async def test_a_mention_of_nora_herself_says_her_name(harness_factory):
    """「@nora 你好」 must not reach her as 「￼ 你好」 — the one message shape that is unambiguously
    addressed to her.

    Nora is deliberately absent from `identity.toml`, so this can only work through
    `PlatformIdentity.aliases`; drop `self_ids` and it renders `@someone`, which asserts a
    stranger was tagged.
    """
    h = await harness_factory(script=_reply(), identity_toml=IDENTITY)
    await h.handler.on_event(group_event(
        body=f"{PLACEHOLDER} 你好", ts=1, mentions=[_mention(OWN, start=0)]))
    await h.handler.on_event(group_event(body="nora.000 ?", ts=2))
    await h.drain()

    assert "@Nora 你好" in _transcript(h)


async def test_the_tagging_message_itself_is_rehydrated(harness_factory):
    """The tag message is a separate block from the transcript, built by
    `build_tag_message`, and it is where a mention is most likely to be."""
    h = await harness_factory(script=_reply(), identity_toml=IDENTITY)
    body = f"nora.000 {PLACEHOLDER} 幾點"
    await h.handler.on_event(group_event(
        body=body, ts=1, mentions=[_mention(TYLER, start=body.index(PLACEHOLDER))]))
    await h.drain()

    tag_message = h._calls[-1]["tag_message"]
    assert "@Tyler Cortez 幾點" in tag_message
    assert PLACEHOLDER not in tag_message


async def test_an_unmapped_mention_leaks_no_identifier_and_is_counted(harness_factory):
    """The second probe. An account Nora cannot name must render an anonymous
    handle — not the number, not the ACI, and not `name`, which IS the number.

    The counter is the only place the loss is visible, which is the whole
    reason it exists: `@someone` is honest and completely uninformative.
    """
    stranger = "+85299999999"
    stranger_uuid = "11111111-2222-3333-4444-555555555555"
    h = await harness_factory(script=_reply(), identity_toml=IDENTITY)
    await h.handler.on_event(group_event(
        body=f"{PLACEHOLDER} 講咩", ts=1,
        mentions=[_mention(stranger, start=0, uuid=stranger_uuid)]))
    await h.handler.on_event(group_event(body="nora.000 ?", ts=2))
    await h.drain()

    transcript = _transcript(h)
    assert "@someone 講咩" in transcript
    assert PLACEHOLDER not in transcript
    assert stranger not in transcript
    assert stranger_uuid not in transcript
    assert h.metrics.get("nora_unmapped_mentions_total", platform="signal") == 1


async def test_two_mentions_in_one_line_both_land_on_their_own_names(harness_factory):
    """The offsets index the ORIGINAL body, so substituting left to right
    shifts every later one by the growth of every earlier one. `@Miles` and
    `@Tyler Cortez` are different lengths on purpose: with a left-to-right pass
    the second lands mid-word, and with equal-length names it would not.
    """
    h = await harness_factory(script=_reply(), identity_toml=IDENTITY)
    body = f"{PLACEHOLDER} 同 {PLACEHOLDER} 邊個先"
    await h.handler.on_event(group_event(
        body=body, ts=1,
        mentions=[_mention("+852111", start=0),
                  _mention(TYLER, start=body.index(PLACEHOLDER, 1))]))
    await h.handler.on_event(group_event(body="nora.000 ?", ts=2))
    await h.drain()

    assert "@Miles 同 @Tyler Cortez 邊個先" in _transcript(h)


async def test_a_mention_of_a_person_whose_name_carries_markup_is_escaped(harness_factory):
    """R-13/R-13b. The name is inserted UPSTREAM of the prefill's own `escape`,
    so it is escaped exactly once — by the same call that escapes the
    group-authored text around it.

    Both halves are asserted: the entity is present (escaped at all) and the
    double-escaped form is absent (not escaped twice, which would put
    `&amp;lt;` in front of the model and teach it to write entities back).
    """
    identity = (
        '[persons.miles]\nname = "Miles"\n[[persons.miles.signal]]\nphone = "+852111"\n'
        '[persons.evil]\nname = "<b>Bo</b> & Co"\n[[persons.evil.signal]]\n'
        f'phone = "{TYLER}"\n'
    )
    h = await harness_factory(script=_reply(), identity_toml=identity)
    await h.handler.on_event(group_event(
        body=f"{PLACEHOLDER} hi", ts=1, mentions=[_mention(TYLER, start=0)]))
    await h.handler.on_event(group_event(body="nora.000 ?", ts=2))
    await h.drain()

    transcript = _transcript(h)
    assert "@&lt;b&gt;Bo&lt;/b&gt; &amp; Co hi" in transcript
    assert "&amp;lt;" not in transcript
    assert "<b>" not in transcript


async def test_a_row_whose_offsets_do_not_fit_is_left_exactly_as_stored(harness_factory):
    """Degrade to the placeholder, never to a hole in somebody's message.

    A `start` that does not land on U+FFFC means the offsets are not describing
    this body — a truncated row, a framing this parser has not met. Cutting
    `length` characters out on a guess would silently rewrite what a person
    said, which is worse than the void this change exists to remove.
    """
    h = await harness_factory(script=_reply(), identity_toml=IDENTITY)
    await h.handler.on_event(group_event(
        body=f"{PLACEHOLDER} 講咩", ts=1, mentions=[_mention(TYLER, start=3)]))
    await h.handler.on_event(group_event(body="nora.000 ?", ts=2))
    await h.drain()

    assert f"{PLACEHOLDER} 講咩" in _transcript(h)

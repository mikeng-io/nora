"""`OutboundComposer` — @-mention ranges and the preview card that ride with a reply.

The properties worth having tests for, in the order they can go wrong:

1. **The offsets are UTF-16 code units.** Every arithmetic test here is written
   against a string this group would actually send — Chinese, then an emoji
   before the mention — because that is the only shape where `len()` and the
   correct answer differ, and `len()` is what a reasonable person writes.
2. **Nothing resolvable becomes a range unless the ROOM confirms it.** Four
   refusals, four labels, all fail-closed: unmapped, not-in-room,
   roster-unreadable, ambiguous.
3. **No identifier ever enters the text.** The recipient rides in a parallel
   field. That is asserted directly, because it is the R-18 exposure the whole
   design is arranged around.
4. **The text is returned unchanged**, which is what makes (1) meaningful.
5. **Style ranges and mention ranges index the SAME string** (DEC-82). Two
   independent sources of offsets over one message is the whole hazard of
   converting `**bold**` at all, and the section at the bottom is where the two
   are asserted together — with CJK and with a non-BMP emoji, because that is
   the only shape in which a `len()`-based answer differs from the right one.
"""

from __future__ import annotations

import pytest
from aiohttp import web

from nora.agent.markdown import render_markdown
from nora.agent.outbound import Outbound, OutboundComposer, first_url
from nora.domain.models import PersonRef
from nora.domain.ports import LinkPreview, Roster, Style, Styled, TextStyle
from nora.domain.utf16 import utf16_len
from nora.obs.metrics import Metrics
from nora.persons import Persons
from nora.testing import FakeLinkPreviewSource, FakeRosterSource

GRANT_ACI = "b9f1c2de-4a55-4c1e-9a77-0c1d2e3f4a5b"
GRANT_PHONE = "+85251234567"
MILES_ACI = "0f8ab3c1-7d22-4e10-8b41-9c5e6f7a8b90"
CONVERSATION = "Z3JvdXA="


def _persons(**extra: PersonRef) -> Persons:
    by_person = {
        "grant": PersonRef(
            person_id="grant", name="Grant", honcho_peer_id="grant",
            first_name="Grant", aliases=("阿豹", "阿豹妹"),
        ),
        "miles": PersonRef(
            person_id="miles", name="Miles", honcho_peer_id="miles", first_name="Miles",
        ),
        **extra,
    }
    index = {
        ("signal", GRANT_ACI): "grant",
        ("signal", GRANT_PHONE): "grant",
        ("signal", MILES_ACI): "miles",
    }
    return Persons(by_person=by_person, index=index)


IN_ROOM = (frozenset({GRANT_ACI, GRANT_PHONE}), frozenset({MILES_ACI}))


def _roster(members=IN_ROOM, *, available: bool = True) -> FakeRosterSource:
    """`FakeRosterSource` keyed to this file's conversation. It answers like
    signal-cli does, not like a stub: `listGroups` pairs `number` WITH `uuid`
    per member — the whole reason `Roster.members` is a tuple of frozenSETS —
    and `available=False` is the daemon not listing the group (stale group
    state on a linked device, CLAUDE.md constraint 3), which the shared fake
    reaches through an id it was not told about."""
    return FakeRosterSource(groups={CONVERSATION: members} if available else {})


def _draft(text: str) -> Styled:
    """What `Deliverer.deliver` hands the composer: the reply, already rendered.

    Through the real `render_markdown` rather than a bare `Styled(text)`, and
    that is not ceremony. `compose` does not take a string — the whole safety
    argument is that the pre-conversion draft is not in its scope — so a helper
    that fabricated a `Styled` would be a second producer of the one type this
    design says has exactly one. Most strings below carry no Markdown and come
    back verbatim; the ones in the styles section do not, and they exercise the
    same path.
    """
    return render_markdown(text)


def _composer(
    *, roster: object | None = None, preview: object | None = None,
    metrics: Metrics | None = None, persons: Persons | None = None,
) -> OutboundComposer:
    return OutboundComposer(
        platform="signal", conversation_id=CONVERSATION,
        persons=persons if persons is not None else _persons(),
        metrics=metrics or Metrics(),
        roster_source=roster if roster is not None else _roster(),
        preview_source=preview,
    )


# --------------------------------------------------------------- utf-16 offsets

def test_utf16_len_counts_code_units_not_characters():
    """The three cases that decide every offset in this module.

    PROBE: replace `utf16_len`'s body with `len(text)` and the emoji case fails
    while the ASCII and CJK ones still pass — which is exactly why the emoji
    case has to be here and why an all-ASCII test suite would have shipped the
    bug.
    """
    assert utf16_len("abc") == 3
    assert utf16_len("你幾高") == 3          # CJK: BMP, one unit each
    assert utf16_len("😅") == 2              # non-BMP: one surrogate PAIR
    assert utf16_len("🇭🇰") == 4              # a flag is TWO non-BMP code points


async def test_a_mention_after_chinese_is_positioned_in_utf16_units():
    composer = _composer()
    text = "我唔知你幾高 @Grant"

    out = await composer.compose(_draft(text))

    (mention,) = out.mentions
    assert mention.start == len("我唔知你幾高 ")     # CJK: units == characters
    assert mention.length == len("@Grant")
    assert mention.recipient == GRANT_ACI


async def test_a_mention_after_an_emoji_is_offset_by_two_units_per_surrogate_pair():
    """The failure `len()` would ship, in the one shape it is visible in.

    Signal accepts a wrong range silently and renders the chip over the wrong
    span — so this cannot be caught in production by watching for errors, only
    by someone noticing the group looks odd.
    """
    composer = _composer()
    text = "😅 @Grant 你幾高?"

    out = await composer.compose(_draft(text))

    (mention,) = out.mentions
    assert mention.start == 3               # 😅 is 2 units, then a space
    assert mention.start != text.index("@")  # ...and Python would have said 2
    assert mention.length == len("@Grant")


async def test_two_mentions_in_one_message_each_carry_their_own_offset():
    composer = _composer()
    text = "@Grant 同 @Miles 你哋幾高?"

    out = await composer.compose(_draft(text))

    assert [m.recipient for m in out.mentions] == [GRANT_ACI, MILES_ACI]
    assert out.mentions[0].start == 0
    assert out.mentions[1].start == utf16_len("@Grant 同 ")


# --------------------------------------------------------------- the text itself

async def test_the_text_is_returned_unchanged_and_carries_no_placeholder():
    """Outbound mentions do NOT use Signal's inbound U+FFFC placeholder.

    Two reasons, and the second is the one a reader is likely to miss:
    signal-cli's own example replaces ordinary text (`-m "Hi X!" --mention
    "3:1:…"`), and Nora's OWN audit row stores this string — `MentionRenderer`
    cannot rehydrate a placeholder in a row of hers (her `raw` is tag
    bookkeeping, not an envelope), so a placeholder here would put DEC-34's
    exact void back into her own transcript history.
    """
    composer = _composer()
    text = "@Grant 你幾高?"

    out = await composer.compose(_draft(text))

    assert out.text == text
    assert "￼" not in out.text


async def test_no_identifier_ever_enters_the_message_text():
    """R-18, and the reason `Mention` is a parallel field rather than a
    substitution. An ACI in the text would be caught by `scan_reply` in
    `Deliverer.deliver` and make her MUTE — so the wrong design fails loudly
    here and leaks nowhere; this asserts the right one anyway, because a future
    "helpfully render the id" edit is exactly the shape that would not."""
    composer = _composer()

    out = await composer.compose(_draft("@Grant 你幾高?"))

    assert GRANT_ACI not in out.text
    assert GRANT_PHONE not in out.text
    assert out.mentions[0].recipient == GRANT_ACI


# ----------------------------------------------------------------- the fail-closed set

async def test_an_unknown_name_stays_plain_text_and_sends_no_range():
    metrics = Metrics()
    composer = _composer(metrics=metrics)

    out = await composer.compose(_draft("@nobody 你幾高?"))

    assert out.text == "@nobody 你幾高?"
    assert out.mentions == ()


async def test_a_person_who_is_not_in_this_room_is_not_tagged():
    """`identity.toml` is the address book for every room; membership is this
    room's. The same resolve-WITHIN-scope rule `nora.agent.room` enforces for
    lookups, applied outbound."""
    metrics = Metrics()
    composer = _composer(roster=_roster((frozenset({MILES_ACI}),)), metrics=metrics)

    out = await composer.compose(_draft("@Grant 你幾高?"))

    assert out.mentions == ()
    assert metrics.get("nora_outbound_mentions_total", outcome="not_in_room") == 1


async def test_an_unreadable_roster_tags_nobody_rather_than_guessing():
    """R-22's rule at the send path: «I could not check who is here» is not
    «they are here». signal-cli answering `[]` for a group Nora is demonstrably
    in is a state this deployment has actually hit (stale group state on a
    linked device), so this branch is a real one, not a hypothetical."""
    metrics = Metrics()
    composer = _composer(roster=_roster(available=False), metrics=metrics)

    out = await composer.compose(_draft("@Grant 你幾高?"))

    assert out.text == "@Grant 你幾高?"
    assert out.mentions == ()
    assert metrics.get("nora_outbound_mentions_total", outcome="roster_unavailable") == 1


async def test_no_roster_source_at_all_tags_nobody():
    """The offline/eval composition. Strict by default — an absent source is
    not permission, it is the absence of confirmation."""
    composer = OutboundComposer(
        platform="signal", conversation_id=CONVERSATION, persons=_persons(),
        metrics=Metrics(), roster_source=None,
    )

    out = await composer.compose(_draft("@Grant 你幾高?"))

    assert out.mentions == ()


async def test_a_roster_that_raises_never_costs_the_message():
    class _Exploding:
        platform = "signal"

        async def roster(self, *, conversation_id: str) -> Roster:
            raise RuntimeError("daemon on fire")

    composer = _composer(roster=_Exploding())

    out = await composer.compose(_draft("@Grant 你幾高?"))

    assert out.text == "@Grant 你幾高?"
    assert out.mentions == ()


async def test_an_ambiguous_handle_tags_neither_person():
    """Two people answering to one alias. Tagging the wrong member of a family
    group is worse than tagging nobody, and there is no tie-break here that is
    not a guess."""
    metrics = Metrics()
    twin = PersonRef(
        person_id="twin", name="Twin", honcho_peer_id="twin", aliases=("阿豹",),
    )
    composer = _composer(persons=_persons(twin=twin), metrics=metrics)

    out = await composer.compose(_draft("@阿豹 你幾高?"))

    assert out.mentions == ()
    assert metrics.get("nora_outbound_mentions_total", outcome="ambiguous") == 1


async def test_a_person_with_no_id_form_on_this_platform_is_unresolved():
    metrics = Metrics()
    persons = Persons(
        by_person={"ghost": PersonRef(
            person_id="ghost", name="Ghost", honcho_peer_id="ghost",
        )},
        index={},
    )
    composer = _composer(persons=persons, metrics=metrics)

    out = await composer.compose(_draft("@Ghost 你幾高?"))

    assert out.mentions == ()
    assert metrics.get("nora_outbound_mentions_total", outcome="unresolved") == 1


# --------------------------------------------------------- discord platform

TYLER_SNOWFLAKE = "100000000000000002"
DISCORD_CONVERSATION = "100000000000000203/100000000000000204"


def _tyler() -> PersonRef:
    return PersonRef(
        person_id="tyler_cortez", name="Tyler Cortez", honcho_peer_id="tyler_cortez",
        first_name="Tyler",
    )


def _discord_persons(**extra: PersonRef) -> Persons:
    by_person = {"tyler_cortez": _tyler(), **extra}
    index = {("discord", TYLER_SNOWFLAKE): "tyler_cortez"}
    return Persons(by_person=by_person, index=index)


def _discord_composer(
    *, roster: object, persons: Persons | None = None, metrics: Metrics | None = None,
) -> OutboundComposer:
    return OutboundComposer(
        platform="discord", conversation_id=DISCORD_CONVERSATION,
        persons=persons if persons is not None else _discord_persons(),
        metrics=metrics or Metrics(),
        roster_source=roster,
    )


IG_FBID = "186217604552756"
IG_CONVERSATION = "1183780976338188"


def _instagram_persons(**extra: PersonRef) -> Persons:
    by_person = {"tyler_cortez": _tyler(), **extra}
    index = {("instagram", IG_FBID): "tyler_cortez"}
    return Persons(by_person=by_person, index=index)


def _instagram_composer(*, roster=None, persons=None, metrics=None) -> OutboundComposer:
    return OutboundComposer(
        platform="instagram", conversation_id=IG_CONVERSATION,
        persons=persons if persons is not None else _instagram_persons(),
        metrics=metrics or Metrics(),
        roster_source=roster,
    )


async def test_an_instagram_fbid_is_accepted_as_the_preferred_recipient_form():
    """An Instagram messaging fbid is a bare digit string, so it matches neither
    the UUID nor the E.164 pattern and `_preferred_recipient` needs its own
    branch for it; without one, every Instagram mention returns None — Nora
    writes "@Name" as plain text, no ping, `status: sent`, nothing logged.
    The failure is silent on the wire: `nora/platform/instagram/send.py` builds
    the mention payload correctly and is simply never given one.

    PROBE: revert `_preferred_recipient`'s instagram branch and this goes red —
    `out.mentions` comes back empty.
    """
    metrics = Metrics()
    roster = FakeRosterSource(groups={IG_CONVERSATION: (frozenset({IG_FBID}),)})
    composer = _instagram_composer(roster=roster, metrics=metrics)

    out = await composer.compose(_draft("@Tyler Cortez 食咗飯未"))

    (mention,) = out.mentions
    assert mention.recipient == IG_FBID


async def test_the_REAL_instagram_roster_is_what_lets_a_mention_resolve(aiohttp_server):
    """The half `_preferred_recipient`'s Instagram branch is inert without.

    Every other Instagram mention test here drives a `FakeRosterSource`; without
    an Instagram roster nobody's membership is confirmed and Nora writes
    «@Name» as plain text with no ping. This runs the composer over
    the REAL `InstagramRoster` against a stand-in for the bridge's own
    `GET /threads/{thread_fbid}/members`, which is the only arrangement that measures that.

    The bridge sends BOTH id spaces per member. The recipient asserted here is
    the MESSAGING fbid: an @-mention can carry nothing else, and the public
    igid in the same position is accepted by the wire and pings nobody.
    """
    from nora.platform.instagram.roster import InstagramRoster

    async def handler(request):
        assert request.match_info["thread_fbid"] == IG_CONVERSATION
        return web.json_response({"members": [
            {"fbid": IG_FBID, "igid": "61652861337", "username": "nka_bpumvg"},
        ]})

    app = web.Application()
    app.router.add_get("/threads/{thread_fbid}/members", handler)
    server = await aiohttp_server(app)
    metrics = Metrics()
    composer = _instagram_composer(
        roster=InstagramRoster(str(server.make_url("")).rstrip("/")), metrics=metrics,
    )

    out = await composer.compose(_draft("@Tyler Cortez 食咗飯未"))

    (mention,) = out.mentions
    assert mention.recipient == IG_FBID
    assert metrics.get("nora_outbound_mentions_total", outcome="sent") == 1


async def test_an_instagram_mention_carries_the_operator_recorded_handle():
    """The fbid alone is not enough for Instagram to RENDER a mention — see
    `nora.platform.instagram.send`'s module docstring for the evidence:
    a correctly-resolved fbid still sends as plain text, because
    Instagram's own API requires the message TEXT at the mention's span to
    read `"@" + the account's own username`, never the display name Nora
    wrote. `Persons.instagram_handle` is where that username comes from
    (identity.toml's `username` metadata, keyed on this exact fbid), and this
    is the one place in the composer allowed to read it — see `Mention.
    handle`'s own docstring for why the substitution itself happens one layer
    further out, in `nora.platform.instagram.send`.

    PROBE: delete the `handle=` line in `OutboundComposer._mentions` and this
    goes red — `mention.handle` comes back `None` even though `Persons` has
    the username on file.
    """
    metrics = Metrics()
    roster = FakeRosterSource(groups={IG_CONVERSATION: (frozenset({IG_FBID}),)})
    persons = _instagram_persons()
    persons._instagram_handles[IG_FBID] = "nka_bpumvg"
    composer = _instagram_composer(roster=roster, persons=persons, metrics=metrics)

    out = await composer.compose(_draft("@Tyler Cortez 食咗飯未"))

    (mention,) = out.mentions
    assert mention.recipient == IG_FBID
    assert mention.handle == "nka_bpumvg"
    assert out.text == "@Tyler Cortez 食咗飯未"    # unchanged — see module docstring


async def test_an_instagram_mention_with_no_recorded_username_carries_no_handle():
    """The ordinary, until-the-operator-opts-in state: the mention still
    resolves (the fbid is right, the ping metadata is right) and `.handle`
    stays `None` — `nora.platform.instagram.send` then forwards the span
    unchanged, today's degraded-but-not-wrong behaviour."""
    metrics = Metrics()
    roster = FakeRosterSource(groups={IG_CONVERSATION: (frozenset({IG_FBID}),)})
    composer = _instagram_composer(roster=roster, metrics=metrics)

    out = await composer.compose(_draft("@Tyler Cortez 食咗飯未"))

    (mention,) = out.mentions
    assert mention.handle is None


async def test_a_discord_mention_never_carries_a_handle_even_with_matching_metadata():
    """`Mention.handle` is gated on `self._platform == "instagram"` — not
    merely on whether `Persons` happens to have a matching entry — because
    Discord's own mechanism (`nora.platform.discord.mention_resolve.
    splice_composed_mentions`) works off `.recipient` alone and a stray
    `.handle` would be a second, contradictory instruction reaching a
    consumer that was never meant to read it. `TYLER_SNOWFLAKE` is reused
    here as a would-be instagram fbid on the SAME `Persons` instance the
    Discord composer is handed, to prove the platform gate does the work,
    not merely the absence of data."""
    metrics = Metrics()
    roster = FakeRosterSource(groups={DISCORD_CONVERSATION: (frozenset({TYLER_SNOWFLAKE}),)})
    persons = _discord_persons()
    persons._instagram_handles[TYLER_SNOWFLAKE] = "would_be_a_handle"
    composer = _discord_composer(roster=roster, persons=persons, metrics=metrics)

    out = await composer.compose(_draft("@Tyler Cortez 柯屎係你嘅 escape plan"))

    (mention,) = out.mentions
    assert mention.handle is None


async def test_a_discord_snowflake_is_accepted_as_the_preferred_recipient_form():
    """`_preferred_recipient` accepts a Discord snowflake as well as a UUID or an
    E.164 number: otherwise a person who IS mapped in `identity.toml` and
    confirmed by the roster is refused, every such mention counts `unresolved`
    and the composer never emits a `Mention` on Discord at all.

    PROBE: revert `_preferred_recipient`'s discord branch and this goes red —
    `out.mentions` comes back empty and the outcome counts `unresolved`
    instead of `sent`.
    """
    metrics = Metrics()
    roster = FakeRosterSource(
        groups={DISCORD_CONVERSATION: (frozenset({TYLER_SNOWFLAKE}),)},
    )
    composer = _discord_composer(roster=roster, metrics=metrics)
    text = "@Tyler Cortez 柯屎係你嘅 escape plan"

    out = await composer.compose(_draft(text))

    (mention,) = out.mentions
    assert mention.recipient == TYLER_SNOWFLAKE
    assert mention.start == 0
    assert mention.length == utf16_len("@Tyler Cortez")
    assert out.text == text                       # unchanged — see module docstring
    assert metrics.get("nora_outbound_mentions_total", outcome="sent") == 1


async def test_a_discord_person_not_in_this_room_is_not_tagged():
    """The same resolve-WITHIN-scope rule as Signal's roster gate, exercised on
    Discord: `identity.toml` mapping Tyler is not enough on its own."""
    metrics = Metrics()
    roster = FakeRosterSource(groups={DISCORD_CONVERSATION: (frozenset({"999999999999999999"}),)})
    composer = _discord_composer(roster=roster, metrics=metrics)

    out = await composer.compose(_draft("@Tyler Cortez hi"))

    assert out.mentions == ()
    assert metrics.get("nora_outbound_mentions_total", outcome="not_in_room") == 1


async def test_an_unknown_handle_on_discord_stays_plain_text():
    """A handle the persons index does not know at all — never guessed, on
    either platform."""
    metrics = Metrics()
    roster = FakeRosterSource(
        groups={DISCORD_CONVERSATION: (frozenset({TYLER_SNOWFLAKE}),)},
    )
    composer = _discord_composer(roster=roster, metrics=metrics)

    out = await composer.compose(_draft("@Someone Unmapped hi"))

    assert out.mentions == ()
    assert out.text == "@Someone Unmapped hi"


# ------------------------------------------------------------------ the parser

async def test_a_handle_glued_to_a_word_is_not_a_mention():
    """`a@b.com` is an address. The `@` must start a token."""
    composer = _composer()

    out = await composer.compose(_draft("mail me at grant@Grant.example"))

    assert out.mentions == ()


async def test_an_ascii_handle_inside_a_longer_ascii_word_is_not_a_mention():
    """`@Mikeson` is not `@Miles`. The boundary check fires because BOTH sides
    are ASCII word characters — see `_is_word`'s docstring for why it is not
    asserted in `isalnum()`, which would also (wrongly) reject the CJK case
    below."""
    composer = _composer()

    out = await composer.compose(_draft("@Mikeson said hi"))

    assert out.mentions == ()


async def test_a_cjk_handle_runs_straight_into_the_next_character():
    """Chinese has no separators, so demanding a boundary would make every CJK
    alias unmatchable in an ordinary sentence — 「@阿豹同我講」 is how the
    group actually writes it."""
    composer = _composer()

    out = await composer.compose(_draft("@阿豹同我講"))

    (mention,) = out.mentions
    assert mention.recipient == GRANT_ACI
    assert mention.length == utf16_len("@阿豹")


async def test_the_longest_matching_handle_wins():
    """A shorter alias that is a PREFIX of a longer one is the normal case in a
    family group. `阿豹` and `阿豹妹` both belong to Grant here; the range
    must cover the longer one or the chip lands beside a stray 妹."""
    composer = _composer()

    out = await composer.compose(_draft("@阿豹妹 你幾高?"))

    (mention,) = out.mentions
    assert mention.length == utf16_len("@阿豹妹")


async def test_handles_are_matched_case_insensitively():
    composer = _composer()

    out = await composer.compose(_draft("@grant 你幾高?"))

    assert out.mentions[0].recipient == GRANT_ACI


async def test_a_message_with_no_at_sign_never_reads_the_roster():
    """The cost argument. A daemon round trip at the front of every reply is
    the thing `Room`'s laziness exists to avoid, and the overwhelming majority
    of replies mention nobody."""
    roster = _roster()
    composer = _composer(roster=roster)

    await composer.compose(_draft("我唔知你幾高"))

    assert roster.calls == []


async def test_two_mentions_read_the_roster_once():
    roster = _roster()
    composer = _composer(roster=roster)

    await composer.compose(_draft("@Grant 同 @Miles 你哋幾高?"))

    assert roster.calls == [CONVERSATION]


async def test_the_aci_is_preferred_over_the_phone_number():
    """Under sealed sender the same account arrives as a number on one message
    and an ACI on another; the ACI is the stable form and the one
    `SignalReactor` already sends as `targetAuthor`."""
    composer = _composer()

    out = await composer.compose(_draft("@Grant"))

    assert out.mentions[0].recipient == GRANT_ACI


async def test_only_the_id_the_platform_confirmed_is_sent():
    """The intersection, not the address book. `identity.toml` here claims a
    stale ACI that the group's membership list does not carry, so the phone
    number — which the roster DOES carry — is what goes on the wire."""
    stale = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    persons = Persons(
        by_person={"grant": PersonRef(
            person_id="grant", name="Grant", honcho_peer_id="grant",
        )},
        index={("signal", stale): "grant", ("signal", GRANT_PHONE): "grant"},
    )
    composer = _composer(
        persons=persons, roster=_roster((frozenset({GRANT_PHONE}),)),
    )

    out = await composer.compose(_draft("@Grant"))

    assert out.mentions[0].recipient == GRANT_PHONE


# ------------------------------------------------------------------- previews

CARD = LinkPreview(
    url="https://links.example.net/abc", title="A shortened link",
    description="What it points at",
)


async def test_a_url_in_the_reply_gets_its_card():
    source = FakeLinkPreviewSource({CARD.url: CARD})
    metrics = Metrics()
    composer = _composer(preview=source, metrics=metrics)

    out = await composer.compose(_draft(f"睇下 {CARD.url}"))

    assert out.preview == CARD
    assert metrics.get("nora_link_previews_total", outcome="attached") == 1


async def test_a_reply_with_no_url_never_asks_for_a_card():
    source = FakeLinkPreviewSource({CARD.url: CARD})
    composer = _composer(preview=source)

    out = await composer.compose(_draft("我唔知你幾高"))

    assert out.preview is None
    assert source.requested == []


async def test_a_preview_that_cannot_be_built_never_costs_the_message():
    """R-5, and the reason `LinkPreviewSource` returns `None` rather than
    raising. Whatever the page did, the sentence still goes out."""
    source = FakeLinkPreviewSource({CARD.url: CARD}, unavailable=True)
    metrics = Metrics()
    composer = _composer(preview=source, metrics=metrics)

    out = await composer.compose(_draft(f"睇下 {CARD.url}"))

    assert out.text == f"睇下 {CARD.url}"
    assert out.preview is None
    assert metrics.get("nora_link_previews_total", outcome="unavailable") == 1


async def test_a_preview_source_that_raises_never_costs_the_message():
    class _Exploding:
        async def preview_for(self, *, url: str) -> LinkPreview | None:
            raise RuntimeError("dns melted")

    composer = _composer(preview=_Exploding())

    out = await composer.compose(_draft(f"睇下 {CARD.url}"))

    assert out.text == f"睇下 {CARD.url}"
    assert out.preview is None


async def test_a_fetched_card_carrying_an_identifier_is_dropped():
    """R-18 applied to somebody ELSE's page. A number rendered in a card is in
    the room exactly as much as one she typed, and the page's author is not
    someone this codebase trusts."""
    leaky = LinkPreview(
        url=CARD.url, title="Call me", description=f"on {GRANT_PHONE}",
    )
    metrics = Metrics()
    composer = _composer(
        preview=FakeLinkPreviewSource({CARD.url: leaky}), metrics=metrics,
    )

    out = await composer.compose(_draft(f"睇下 {CARD.url}"))

    assert out.preview is None
    assert metrics.get("nora_link_previews_total", outcome="blocked") == 1
    assert metrics.get("nora_egress_blocks_total", kind="phone") == 1


async def test_a_card_mentioning_a_model_name_is_deliberately_not_dropped():
    """The other half of `nora.agent.egress`'s two-predicate split (DEC-30).
    `scan_reply`'s model-name pattern is «she does not SAY what she runs on»;
    a third party's page title is not her saying anything, and screening it
    here would silently drop the preview of any page about LLMs — which in a
    group of software engineers is a page they are likely to be discussing.

    PROBE: swap `scan_identifiers` for `scan_reply` in `_preview` and this is
    the assertion that goes red.
    """
    about_llms = LinkPreview(
        url=CARD.url, title="Claude and Gemini benchmarks", description="opus vs sonnet",
    )
    composer = _composer(preview=FakeLinkPreviewSource({CARD.url: about_llms}))

    out = await composer.compose(_draft(f"睇下 {CARD.url}"))

    assert out.preview == about_llms


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("睇下 https://links.example.net/abc", "https://links.example.net/abc"),
        ("睇下 https://links.example.net/abc。", "https://links.example.net/abc"),
        ("(https://links.example.net/abc)", "https://links.example.net/abc"),
        ("no link here", None),
        ("ftp://links.example.net/abc", None),
    ],
)
def test_first_url_extracts_a_substring_of_the_body(text, expected):
    """A SUBSTRING is the contract, not merely a URL: signal-cli requires the
    preview url to appear in the message body verbatim or no client shows the
    card at all, so a normalised url would ship a payload nobody sees."""
    url = first_url(text)
    assert url == expected
    if url is not None:
        assert url in text


async def test_no_preview_source_means_no_card_and_no_failure():
    composer = _composer(preview=None)

    out = await composer.compose(_draft(f"睇下 {CARD.url}"))

    assert out == Outbound(_draft(f"睇下 {CARD.url}"))


# ------------------------------------------------- styles beside mentions (DEC-82)
#
# A mention range and a style range are produced by DIFFERENT code, at
# different times, over one string — the styles by `render_markdown` before the
# guards run, the mentions by `compose` after — and Signal renders a mismatch
# rather than rejecting it. Everything below is written so that an
# off-by-one in EITHER producer is visible.


async def test_a_bold_span_and_a_mention_index_the_same_final_string():
    """Both ranges are asserted by SLICING the text that goes on the wire, not
    by comparing them to hand-counted constants. A test that asserted `start ==
    9` would keep passing if both the text and the offsets shifted together;
    this one cannot, because it re-derives the substring each range covers.
    """
    composer = _composer()

    out = await composer.compose(_draft("**投資**方面 @Grant 你點睇?"))

    assert out.text == "投資方面 @Grant 你點睇?"
    (style,) = out.styles
    assert out.text[style.start:style.start + style.length] == "投資"
    (mention,) = out.mentions
    assert out.text[mention.start:mention.start + mention.length] == "@Grant"
    assert mention.recipient == GRANT_ACI


async def test_an_emoji_before_both_ranges_shifts_them_by_two_units_each():
    """The one shape where `len()` and the answer differ, applied to BOTH
    producers at once. 😅 is one Python character and two UTF-16 units, so a
    naive implementation is off by one on the style and off by one on the
    mention — and the two errors are independent, which is why the assertions
    below are not a substring slice: Python slicing counts characters, so it
    would agree with the wrong answer.

    PROBE: replace `utf16_len` with `len` and both assertions go red.
    """
    composer = _composer()

    out = await composer.compose(_draft("😅 **好正** @Grant"))

    assert out.text == "😅 好正 @Grant"
    (style,) = out.styles
    assert style.start == 3                      # 😅 is 2 units, then a space
    assert style.start != out.text.index("好")    # ...and Python would say 2
    assert style.length == 2
    (mention,) = out.mentions
    assert mention.start == utf16_len("😅 好正 ")
    assert mention.length == utf16_len("@Grant")


async def test_a_mention_inside_the_bold_span_keeps_both_ranges():
    """Nesting the two sources of ranges, which is where a design that computed
    them against different strings would finally show. The mention lives INSIDE
    the emphasis, so its offset depends on the asterisks having already gone."""
    composer = _composer()

    out = await composer.compose(_draft("**@Grant 快啲**"))

    assert out.text == "@Grant 快啲"
    (style,) = out.styles
    assert (style.start, style.length) == (0, utf16_len("@Grant 快啲"))
    (mention,) = out.mentions
    assert (mention.start, mention.length) == (0, utf16_len("@Grant"))


async def test_a_reply_with_no_markdown_carries_no_styles_at_all():
    """The overwhelmingly common message. An empty tuple and not a range of
    length zero — `nora.platform.signal.send` omits the key entirely when there
    is nothing to say, and a zero-length range would be a parameter."""
    composer = _composer()

    out = await composer.compose(_draft("我唔知你幾高"))

    assert out.styles == ()
    assert out.text == "我唔知你幾高"


async def test_the_outbound_reads_its_text_and_its_styles_off_one_value():
    """The structural claim, asserted as a claim about the OBJECT rather than
    about a message. `Outbound` stores the `Styled`; `.text` and `.styles` are
    properties of it, so there is no assignment anywhere that could pair one
    message's text with another's ranges.

    PROBE: give `Outbound` separate `text: str` and `styles: tuple[...]` fields
    and this goes red at the `is` — which is the edit that reads as a
    simplification and silently reopens the hazard.
    """
    styled = _draft("**投資**方面")
    out = Outbound(styled)

    assert out.text is styled.text
    assert out.styles is styled.styles


def test_an_outbound_cannot_be_built_with_a_range_outside_its_text():
    """`Styled` refuses at construction, so the wrong pairing never becomes an
    `Outbound` in the first place. This is the runtime half of the property
    above: the type system cannot say «these offsets belong to that string», so
    the constructor says the part of it that is checkable."""
    with pytest.raises(ValueError, match="does not fit"):
        Styled(text="short", styles=(TextStyle(start=0, length=99, style=Style.BOLD),))

"""`policy.toml` — the two allowlists and the grant predicate (§4)."""

import pytest

from nora.policy import PolicyError, load_policy, permissive_policy

GROUP = "Lf2L9zqeNUahRsOz6BA+sTB8aWfbuvqu9vOhpvmJi34="

TOML = f"""
default = "deny"

[dm]
persons = ["miles"]

[[conversations]]
platform = "signal"
id       = "{GROUP}"
label    = "Ridgeline"
persons  = ["miles", "ada"]

[[conversations]]
platform = "telegram"
id       = "-1001234567890"
also_ids = ["-987654321"]
label    = "Work"
persons  = "*"
"""


def _write(tmp_path, body):
    p = tmp_path / "policy.toml"
    p.write_text(body)
    return p


@pytest.fixture
def policy(tmp_path):
    return load_policy(_write(tmp_path, TOML))


def _allowed(policy, conversation_id, person_id, platform="signal"):
    return policy.is_allowed(
        platform=platform, conversation_id=conversation_id, person_id=person_id
    )


# ── the grant predicate (§4.3) ───────────────────────────────────────────────


def test_a_listed_person_in_a_listed_room_is_allowed(policy):
    assert _allowed(policy, GROUP, "miles") is True
    assert _allowed(policy, GROUP, "ada") is True


def test_an_unlisted_person_in_a_listed_room_is_refused(policy):
    """A new group member is ignored until the operator adds them (§7.1)."""
    assert _allowed(policy, GROUP, "stranger") is False


def test_a_listed_person_in_an_unlisted_room_is_refused(policy):
    """Deny by default: being trusted somewhere is not being trusted here."""
    assert _allowed(policy, "some-other-group", "miles") is False


def test_an_allowed_room_with_no_label_refuses_to_start(tmp_path):
    """`UnitRoom.label`/`label_for` both render a floor for an unlabelled room
    rather than falling back to its id (R-18) — the right failure mode for old
    data a render meets after the fact, but the wrong one for a room the
    operator is adding today, where it silently makes two unlabelled rooms read
    as one. Refused at startup instead, the same shape as a second home room."""
    body = TOML.replace('label    = "Work"\n', "")
    with pytest.raises(PolicyError, match="has no label"):
        load_policy(_write(tmp_path, body))


def test_the_wildcard_admits_an_unmapped_sender(policy):
    """The room is the trust boundary. D6 already settled that an unmapped
    sender is answered but never remembered; refusing them here would reverse
    that and leave `"*"` admitting only people who are already mapped, which
    is what an explicit list is for."""
    assert _allowed(policy, "-1001234567890", None, platform="telegram") is True
    assert _allowed(policy, "-1001234567890", "anyone", platform="telegram") is True


def test_an_explicit_list_still_excludes_an_unmapped_sender(policy):
    assert _allowed(policy, GROUP, None) is False


def test_the_wildcard_still_respects_the_room_allowlist(policy):
    assert _allowed(policy, "-999", "anyone", platform="telegram") is False


def test_platforms_do_not_leak_into_each_other(policy):
    """The same id string on another platform is a different conversation."""
    assert _allowed(policy, GROUP, "miles", platform="telegram") is False


def test_also_ids_match_identically(policy):
    """Telegram changes chat.id on supergroup migration and reports the old id
    once; without also_ids the allowlist silently stops matching (§4.2)."""
    assert _allowed(policy, "-987654321", "anyone", platform="telegram") is True


def test_a_label_is_never_matched_on(policy):
    assert _allowed(policy, "Ridgeline", "miles") is False


def test_dm_persons_are_parsed(policy):
    assert policy.dm_persons == frozenset({"miles"})


# ── refusing to start (§4.1) ─────────────────────────────────────────────────


def test_an_empty_allowlist_refuses_to_start(tmp_path):
    """Not silent denial: under R-5 a silently-denied Nora is
    indistinguishable from a working one in a quiet group."""
    with pytest.raises(PolicyError, match="allows no conversations"):
        load_policy(_write(tmp_path, 'default = "deny"\n'))


def test_a_missing_file_refuses_to_start(tmp_path):
    with pytest.raises(PolicyError, match="policy file not found"):
        load_policy(tmp_path / "absent.toml")


def test_allow_by_default_is_refused(tmp_path):
    body = f'default = "allow"\n[[conversations]]\nplatform="signal"\nid="{GROUP}"\n'
    with pytest.raises(PolicyError, match="deny"):
        load_policy(_write(tmp_path, body))


def test_an_unknown_key_is_refused(tmp_path):
    """extra="forbid": a typo must be a startup failure, not a silently-open
    allowlist (`persosn = [...]` would otherwise admit nobody, quietly)."""
    body = (
        f'default = "deny"\n[[conversations]]\nplatform="signal"\nid="{GROUP}"\n'
        'persosn = ["miles"]\n'
    )
    with pytest.raises(PolicyError):
        load_policy(_write(tmp_path, body))


def test_a_room_description_is_optional_and_absent_by_default(policy):
    """`description` is optional: a policy.toml that has never heard of it must
    load, and `extra = "forbid"` means a schema that demanded it would refuse to
    start. Absent is `None`, not `""`."""
    convo = policy.conversation("signal", GROUP)
    assert convo is not None
    assert convo.description is None


def test_a_room_description_is_read_verbatim(tmp_path):
    """What the operator says the room IS, as opposed to what `label` says it
    is called — the sentence the note-candidates pass renders above a batch
    so a games room's shorthand is read as a games room's shorthand."""
    body = TOML + '''
[[conversations]]
platform    = "discord"
id          = "123456789012345678/1234567890123456789"
label       = "Zone 42"
persons     = ["miles"]
description = "a games room; 2200 is when people log on, not an appointment"
'''
    policy = load_policy(_write(tmp_path, body))

    convo = policy.conversation("discord", "123456789012345678/1234567890123456789")
    assert convo is not None
    assert convo.description == (
        "a games room; 2200 is when people log on, not an appointment"
    )


def test_a_blank_room_description_is_absent_not_empty(tmp_path):
    """Two spellings of "the operator wrote nothing" is how a block ends up
    rendered with nothing in it. `description = ""` normalises to `None`."""
    body = TOML + '''
[[conversations]]
platform    = "discord"
id          = "123456789012345678/1234567890123456789"
label       = "Zone 42"
persons     = ["miles"]
description = "   "
'''
    policy = load_policy(_write(tmp_path, body))

    convo = policy.conversation("discord", "123456789012345678/1234567890123456789")
    assert convo is not None
    assert convo.description is None


def test_a_bad_wildcard_string_is_refused(tmp_path):
    body = (
        f'default = "deny"\n[[conversations]]\nplatform="signal"\nid="{GROUP}"\n'
        'persons = "everyone"\n'
    )
    with pytest.raises(PolicyError, match=r"list of person_ids"):
        load_policy(_write(tmp_path, body))


def test_the_same_conversation_listed_twice_is_refused(tmp_path):
    """The second entry would be unreachable, so one of the two allowlists the
    operator thinks he wrote is doing nothing."""
    body = (
        f'default = "deny"\n[[conversations]]\nplatform="signal"\nid="{GROUP}"\n'
        f'persons=["miles"]\n[[conversations]]\nplatform="signal"\nid="{GROUP}"\n'
        'persons=["ada"]\n'
    )
    with pytest.raises(PolicyError, match="listed twice"):
        load_policy(_write(tmp_path, body))


def test_an_also_id_colliding_with_another_room_is_refused(tmp_path):
    body = (
        'default = "deny"\n[[conversations]]\nplatform="telegram"\nid="-1"\n'
        'persons=["miles"]\n[[conversations]]\nplatform="telegram"\nid="-2"\n'
        'also_ids=["-1"]\npersons=["ada"]\n'
    )
    with pytest.raises(PolicyError, match="listed twice"):
        load_policy(_write(tmp_path, body))


# ── MCP tool allowlist (§2.3) ───────────────────────────────────────────────


def test_conversation_tools_defaults_to_empty(tmp_path):
    policy_file = tmp_path / "policy.toml"
    policy_file.write_text("""
default = "deny"
[[conversations]]
platform = "signal"
id = "g1"
label = "g1"
persons = ["miles"]
""")
    policy = load_policy(policy_file)
    convo = policy.conversation("signal", "g1")
    assert convo.tools == frozenset()


def test_conversation_tools_parses_allowlist(tmp_path):
    policy_file = tmp_path / "policy.toml"
    policy_file.write_text("""
default = "deny"
[[conversations]]
platform = "signal"
id = "g1"
label = "g1"
persons = ["miles"]
tools = ["pulse"]
""")
    policy = load_policy(policy_file)
    convo = policy.conversation("signal", "g1")
    assert convo.tools == frozenset({"pulse"})


def test_location_flag_defaults_false_and_parses_true(policy, tmp_path):
    """DEC-74: `location` gates where_is's circle-subset relaxation. It DEFAULTS
    False (the fixture rooms set none), so the disclosure guard stays strict; a
    room with `location = true` carries it through to `Conversation.location`."""
    assert policy.conversation("signal", GROUP).location is False
    body = (
        'default = "deny"\n[[conversations]]\nplatform="signal"\n'
        f'id="{GROUP}"\nlabel="Ridgeline"\npersons=["miles"]\nlocation=true\n'
    )
    trusted = load_policy(_write(tmp_path, body))
    assert trusted.conversation("signal", GROUP).location is True


def test_relevance_gate_flag_defaults_false_and_parses_true(policy, tmp_path):
    """`relevance_gate` opts a room into the
    Relevance Gate's consideration of its untagged traffic. It DEFAULTS False
    (the fixture rooms set none) — being answerable when tagged has never
    implied being allowed to speak uncalled — and a room with
    `relevance_gate = true` carries it through to
    `Conversation.relevance_gate`. `[relevance_gate] enabled` in config.toml
    is the second, independent gate."""
    assert policy.conversation("signal", GROUP).relevance_gate is False
    body = (
        'default = "deny"\n[[conversations]]\nplatform="signal"\n'
        f'id="{GROUP}"\nlabel="Ridgeline"\npersons=["miles"]\nrelevance_gate=true\n'
    )
    opted_in = load_policy(_write(tmp_path, body))
    assert opted_in.conversation("signal", GROUP).relevance_gate is True


def test_permissive_policy_opts_every_room_into_the_relevance_gate():
    """The eval/testing double: a
    harness that wires `RelevanceGatePolicy(permissive_policy())` must have the
    room gate OPEN so an untagged message can reach the gate and, on GO,
    the participation occasion.
    Inert in production — nothing ships `permissive_policy` into a live
    `RelevanceGatePolicy`; and even in a harness it does nothing unless one is
    wired."""
    assert permissive_policy().conversation("signal", "g1").relevance_gate is True


# ── the home room and the proactive flag ─────────────────────────────────────


HOME_TOML = f"""
default = "deny"

[[conversations]]
platform  = "signal"
id        = "{GROUP}"
unit = "friends"
label     = "Friends"
persons   = ["miles", "ada"]
home_room = true
proactive = true
location  = true

[[conversations]]
platform  = "discord"
id        = "1/2"
unit = "friends"
label     = "Friends discord"
persons   = ["miles", "ada"]

[[conversations]]
platform  = "signal"
id        = "wedding-room"
unit = "wedding"
label     = "Wedding"
persons   = ["miles"]
"""


def test_both_flags_default_false(policy):
    """Unprompted speech is granted per room, never inherited from being
    answerable — the `relevance_gate` precedent, applied to both new flags."""
    convo = policy.conversation("signal", GROUP)
    assert convo is not None
    assert convo.home_room is False
    assert convo.proactive is False
    assert policy.home_rooms() == {}
    assert policy.proactive_allowed(platform="signal", conversation_id=GROUP) is False


def test_the_home_room_is_keyed_by_unit(tmp_path):
    policy = load_policy(_write(tmp_path, HOME_TOML))

    homes = policy.home_rooms()

    assert set(homes) == {"friends"}
    assert homes["friends"].primary_id == GROUP
    assert homes["friends"].platform == "signal"
    assert homes["friends"].location is True


def test_proactive_is_checked_on_the_room_not_on_a_person(tmp_path):
    """The owner of a proactive row is the reserved ai_peer id, which
    `is_allowed` would refuse in every explicit-persons room. The flag is the
    grant, and it is deny-by-default in both directions: an unflagged room and
    an unlisted room both answer False."""
    policy = load_policy(_write(tmp_path, HOME_TOML))

    assert policy.proactive_allowed(platform="signal", conversation_id=GROUP) is True
    assert policy.proactive_allowed(platform="discord", conversation_id="1/2") is False
    assert policy.proactive_allowed(platform="signal", conversation_id="nope") is False
    # The persons list still does not know Nora — the flag is a DIFFERENT grant.
    assert _allowed(policy, GROUP, "nora") is False


def test_two_home_rooms_in_one_unit_refuse_to_start(tmp_path):
    """Two would make the room the adaptor stands in a guess, and a guessed
    room is an R-31 disclosure decision nobody made."""
    body = HOME_TOML.replace(
        'label     = "Friends discord"\n',
        'label     = "Friends discord"\nhome_room = true\n',
    )
    with pytest.raises(PolicyError, match="two home rooms"):
        load_policy(_write(tmp_path, body))


def test_the_permissive_policy_names_no_home_room():
    """The offline/eval harness policy allows every room; it must not thereby
    invent a home room, or a unit-keyed harness run would stand in a
    room that does not exist."""
    assert permissive_policy().home_rooms() == {}


# --- `voice = true` -----------------------------------------------------------
# A guild's spoken utterances are recorded in the ONE room that claims them,
# resolved by GUILD.

VOICE_GUILD = "100000000000000203"
VOICE_ROOM = f"{VOICE_GUILD}/100000000000000204"


def _voice_toml(*, extra: str = "", voice_line: str = "voice = true") -> str:
    return f"""
default = "deny"

[[conversations]]
platform = "discord"
id       = "{VOICE_ROOM}"
label    = "Ridgeline #discussion"
persons  = ["miles"]
{voice_line}
{extra}
"""


def test_a_guilds_voice_resolves_to_the_room_that_claims_it(tmp_path):
    policy = load_policy(_write(tmp_path, _voice_toml()))

    room = policy.voice_room("discord", VOICE_GUILD)

    assert room is not None
    assert room.primary_id == VOICE_ROOM
    assert room.voice is True
    # It is the ROOM's own persons list and unit that speech inherits —
    # that is the whole point of folding voice into an existing room rather
    # than giving it one of its own.
    assert room.persons == frozenset({"miles"})


def test_an_unmarked_guild_resolves_to_nothing(tmp_path):
    """Deny-by-default, same as the room gate. `None` is a refusal the caller
    must turn into a 400 — never a fallback to the voice channel's own id,
    which is how per-channel rooms would come back one utterance at a time."""
    policy = load_policy(_write(tmp_path, _voice_toml(voice_line="")))

    assert policy.voice_room("discord", VOICE_GUILD) is None


def test_a_different_guild_resolves_to_nothing(tmp_path):
    policy = load_policy(_write(tmp_path, _voice_toml()))

    assert policy.voice_room("discord", "999999999999999999") is None


def test_two_voice_rooms_in_one_guild_refuse_to_start(tmp_path):
    """Two answers to "where does this guild's speech go" is not a preference
    to resolve — picking one would put a spoken plan in a room nobody chose."""
    extra = f"""
[[conversations]]
platform = "discord"
id       = "{VOICE_GUILD}/1302899882047175451"
label    = "Ridgeline #other"
persons  = ["miles"]
voice = true
"""
    with pytest.raises(PolicyError, match="two voice rooms"):
        load_policy(_write(tmp_path, _voice_toml(extra=extra)))


def test_voice_on_a_non_discord_room_refuses_to_start(tmp_path):
    """Voice is resolved by GUILD and only Discord has one. A Signal room
    with `voice = true` could never be reached, so it is an operator error
    stated at startup rather than a line that quietly does nothing."""
    body = f"""
default = "deny"

[[conversations]]
platform = "signal"
id       = "{GROUP}"
label    = "Ridgeline"
persons  = ["miles"]
voice = true
"""
    with pytest.raises(PolicyError, match="only Discord"):
        load_policy(_write(tmp_path, body))


def test_voice_on_an_id_without_a_guild_half_refuses_to_start(tmp_path):
    body = """
default = "deny"

[[conversations]]
platform = "discord"
id       = "100000000000000204"
label    = "bare channel id"
persons  = ["miles"]
voice = true
"""
    with pytest.raises(PolicyError, match="not \\\"<guild_id>/<channel_id>\\\""):
        load_policy(_write(tmp_path, body))


def test_voice_defaults_to_false(tmp_path):
    """A policy.toml that has never heard of the key loads exactly as before,
    and claims no guild's speech."""
    policy = load_policy(_write(tmp_path, TOML))

    assert all(not c.voice for c in policy.conversations)


# --- `music_channel` ----------------------------------------------------------
# A guild's music ANNOUNCEMENTS are posted in the SIBLING channel one of its
# rooms names, resolved by GUILD.
#
# A FIELD, not a `voice = true`-style flag, and the two reasons are what these
# tests are really pinning: a dedicated music channel is not the discussion
# room, so a flag would need a SECOND room per guild — DEC-105 forbids it, and
# a listed room is a room Nora RECORDS.

MUSIC_GUILD = "100000000000000203"
MUSIC_DECLARING_ROOM = f"{MUSIC_GUILD}/100000000000000204"
MUSIC_CHANNEL = "1480387369830973999"


def _music_toml(
    *, extra: str = "", music_line: str = f'music_channel = "{MUSIC_CHANNEL}"',
) -> str:
    return f"""
default = "deny"

[[conversations]]
platform = "discord"
id       = "{MUSIC_DECLARING_ROOM}"
label    = "Ridgeline #discussion"
persons  = ["miles"]
{music_line}
{extra}
"""


def test_a_guilds_music_resolves_to_the_channel_its_room_names(tmp_path):
    policy = load_policy(_write(tmp_path, _music_toml()))

    assert policy.music_channel_for("discord", MUSIC_GUILD) == MUSIC_CHANNEL


def test_a_music_channel_that_is_itself_a_listed_room_refuses_to_start(tmp_path):
    """The invariant the whole field-vs-flag argument rests on, ENFORCED
    rather than documented. A `[[conversations]]` entry answers questions
    about people; an announcement channel is written to and never read, so
    pointing one at a listed room puts the announcements back inside ingest
    and makes the argument false."""
    extra = f"""
[[conversations]]
platform = "discord"
id       = "{MUSIC_GUILD}/{MUSIC_CHANNEL}"
label    = "Ridgeline #music, listed by mistake"
persons  = ["miles"]
"""
    with pytest.raises(PolicyError, match="itself a listed conversation"):
        load_policy(_write(tmp_path, _music_toml(extra=extra)))


def test_the_declaring_rooms_own_channel_is_refused_too(tmp_path):
    """The declaring room is a listed conversation like any other, so naming
    its own channel hits the same refusal. Not a special case — the rule is
    "the destination is not a room", and the room writing the line is one."""
    own_channel = MUSIC_DECLARING_ROOM.partition("/")[2]
    with pytest.raises(PolicyError, match="itself a listed conversation"):
        load_policy(
            _write(tmp_path, _music_toml(music_line=f'music_channel = "{own_channel}"')),
        )


def test_the_destination_is_not_a_listed_room_in_the_accepted_case(tmp_path):
    """The positive half of the refusal above: what a VALID config resolves to
    is a channel this file does not list."""
    policy = load_policy(_write(tmp_path, _music_toml()))

    resolved = policy.music_channel_for("discord", MUSIC_GUILD)

    assert resolved == MUSIC_CHANNEL
    assert policy.conversation("discord", f"{MUSIC_GUILD}/{resolved}") is None


def test_an_unset_guild_has_no_music_channel(tmp_path):
    """`None` is the FALLBACK, not a refusal (contrast `voice_room`): the
    announcement goes to the channel the session started in."""
    policy = load_policy(_write(tmp_path, _music_toml(music_line="")))

    assert policy.music_channel_for("discord", MUSIC_GUILD) is None


def test_a_different_guild_has_no_music_channel(tmp_path):
    policy = load_policy(_write(tmp_path, _music_toml()))

    assert policy.music_channel_for("discord", "999999999999999999") is None


def test_two_rooms_naming_a_music_channel_in_one_guild_refuse_to_start(tmp_path):
    """A guild has one voice connection and one player, so two answers to
    "where do its announcements go" is the operator having written two —
    refused at startup rather than resolved by picking the first."""
    extra = f"""
[[conversations]]
platform = "discord"
id       = "{MUSIC_GUILD}/1302899882047175451"
label    = "Ridgeline #other"
persons  = ["miles"]
music_channel = "1419979384793289708"
"""
    with pytest.raises(PolicyError, match="two music channels"):
        load_policy(_write(tmp_path, _music_toml(extra=extra)))


def test_music_channel_on_a_non_discord_room_refuses_to_start(tmp_path):
    body = f"""
default = "deny"

[[conversations]]
platform = "signal"
id       = "{GROUP}"
label    = "Ridgeline"
persons  = ["miles"]
music_channel = "{MUSIC_CHANNEL}"
"""
    with pytest.raises(PolicyError, match="no other platform"):
        load_policy(_write(tmp_path, body))


@pytest.mark.parametrize(
    "declaring_id",
    [
        "100000000000000204",   # no guild half at all
        "/100000000000000204",  # empty guild half
        "100000000000000203/",  # empty CHANNEL half: `id = "<guild>/"` must not
                                 # be able to declare a music_channel
    ],
)
def test_a_malformed_declaring_room_id_refuses_to_start(tmp_path, declaring_id):
    """The DECLARING room's guild half is what a playing session matches on,
    and its channel half is what makes it a reachable room at all."""
    body = f"""
default = "deny"

[[conversations]]
platform = "discord"
id       = "{declaring_id}"
label    = "malformed"
persons  = ["miles"]
music_channel = "{MUSIC_CHANNEL}"
"""
    with pytest.raises(PolicyError, match='not \\"<guild_id>/<channel_id>\\"'):
        load_policy(_write(tmp_path, body))


#: Arabic-Indic digits spelling a plausible snowflake. `str.isdigit()` says yes
#: AND `int()` parses them, so a validator built on `isdigit()` would route
#: them — silently, while reading as something else entirely.
_ARABIC_INDIC = "".join(chr(0x660 + int(d)) for d in "148038736983097399")


@pytest.mark.parametrize(
    "bad",
    [
        f"{MUSIC_GUILD}/{MUSIC_CHANNEL}",  # a pair would post outside this guild
        "#music",                          # a name is not a stable id
        "music",
        "1480387369830973999x",            # not all digits
        "\u00b2",                              # superscripts: `str.isdigit()` says YES and
        "\u00b9\u00b2\u00b3",                            # `int()` says no, so the line could never
                                           # route and failed at send time looking exactly
                                           # like a deleted channel — PASS standing in for
                                           # ERROR
        _ARABIC_INDIC,
        "0",                               # parses; too short to be a snowflake
        "1" * 30,                          # parses; too long
    ],
)
def test_a_music_channel_that_is_not_a_bare_snowflake_refuses_to_start(tmp_path, bad):
    """ASCII decimal digits, 17-20 of them. The `<guild>/<channel>` pair is
    the dangerous-looking one — it reads plausibly — but the digit-lookalikes
    are the ones `str.isdigit()` would let through."""
    with pytest.raises(PolicyError, match="not a Discord channel id"):
        load_policy(_write(tmp_path, _music_toml(music_line=f'music_channel = "{bad}"')))


@pytest.mark.parametrize("length", [17, 18, 19, 20])
def test_a_snowflake_of_plausible_length_is_accepted(tmp_path, length):
    """The boundary, stated: 17-20 digits inclusive. A 19-digit string of
    zeros is SHAPE-valid and deliberately accepted — it names no channel, but
    that is a send-time fact the fallback covers, not something the loader can
    know."""
    value = "1" + "0" * (length - 2) + "9"
    policy = load_policy(
        _write(tmp_path, _music_toml(music_line=f'music_channel = "{value}"')),
    )

    assert policy.music_channel_for("discord", MUSIC_GUILD) == value


def test_the_loader_and_the_sender_agree_on_what_a_snowflake_is():
    """Two copies of the predicate exist because `nora.policy` and
    `nora.platform` may not import each other (the import-linter contract).
    Two copies drift, so this is the test that says they have not: the loader
    must never accept a value the sender will refuse, which would be a startup
    PASS for a line that can never route."""
    from nora.platform.discord.music_notify import is_snowflake
    from nora.policy import _is_snowflake

    cases = [
        "1" * 17, "1" * 20, "1" * 16, "1" * 21, "0", "", "12x", "\u00b2",
        _ARABIC_INDIC, "1480387369830973999",
    ]
    assert [_is_snowflake(c) for c in cases] == [is_snowflake(c) for c in cases]


def test_surrounding_whitespace_is_stripped_not_refused(tmp_path):
    """`music_channel = " 155... "` is a copy-paste, not an error. The same
    validator that turns `""` into absent strips this first, so the snowflake
    check never sees the whitespace — otherwise a stray space would refuse
    startup over a correct id."""
    policy = load_policy(
        _write(tmp_path, _music_toml(music_line=f'music_channel = " {MUSIC_CHANNEL} "')),
    )

    assert policy.music_channel_for("discord", MUSIC_GUILD) == MUSIC_CHANNEL


def test_a_blank_music_channel_is_absent_not_an_error(tmp_path):
    """`music_channel = ""` is the operator writing nothing. Two spellings of
    "no destination" is how one of them reaches the snowflake check and
    refuses startup over a line that meant nothing."""
    policy = load_policy(_write(tmp_path, _music_toml(music_line='music_channel = ""')))

    assert policy.music_channel_for("discord", MUSIC_GUILD) is None


def test_music_channel_defaults_to_absent(tmp_path):
    """A policy.toml that has never heard of the key loads exactly as before,
    and claims no guild's announcements."""
    policy = load_policy(_write(tmp_path, TOML))

    assert all(c.music_channel is None for c in policy.conversations)


def test_one_room_can_carry_both_voice_and_a_music_channel(tmp_path):
    """The realistic deployment, and the shape a flag could not express: the
    guild's ONE room records its speech AND names where announcements go,
    without the guild gaining a second room in its unit (DEC-105)."""
    policy = load_policy(
        _write(tmp_path, _music_toml(extra="voice = true")),
    )

    assert policy.voice_room("discord", MUSIC_GUILD) is not None
    assert policy.music_channel_for("discord", MUSIC_GUILD) == MUSIC_CHANNEL
    assert len(policy.conversations) == 1


def test_the_permissive_policy_names_no_music_channel(tmp_path):
    """`permissive_policy` overrides `voice_room` because `None` there would
    refuse; it deliberately does NOT override `music_channel_for`, because
    `None` here is the fallback a harness wants."""
    assert permissive_policy().music_channel_for("discord", MUSIC_GUILD) is None

"""`policy.toml` — the two allowlists and the grant predicate (design/10 §4)."""

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
    """Issue #161's lever 2 ships EMPTY: the deployment's policy.toml has
    never heard of `description`, and `extra = "forbid"` means a schema that
    demanded it would refuse to start. Absent is `None`, not `""`."""
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
        f'id="{GROUP}"\npersons=["miles"]\nlocation=true\n'
    )
    trusted = load_policy(_write(tmp_path, body))
    assert trusted.conversation("signal", GROUP).location is True


def test_relevance_gate_flag_defaults_false_and_parses_true(policy, tmp_path):
    """design/dreamer-agent-loop/36 §9: `relevance_gate` opts a room into the
    Relevance Gate's consideration of its untagged traffic. It DEFAULTS False
    (the fixture rooms set none) — being answerable when tagged has never
    implied being allowed to speak uncalled — and a room with
    `relevance_gate = true` carries it through to
    `Conversation.relevance_gate`. `[relevance_gate] enabled` in config.toml
    is the second, independent gate."""
    assert policy.conversation("signal", GROUP).relevance_gate is False
    body = (
        'default = "deny"\n[[conversations]]\nplatform="signal"\n'
        f'id="{GROUP}"\npersons=["miles"]\nrelevance_gate=true\n'
    )
    opted_in = load_policy(_write(tmp_path, body))
    assert opted_in.conversation("signal", GROUP).relevance_gate is True


def test_permissive_policy_opts_every_room_into_the_relevance_gate():
    """The eval/testing double (design/dreamer-agent-loop/36 §9): a
    harness that wires `RelevanceGatePolicy(permissive_policy())` must have the
    room gate OPEN so an untagged message can reach the gate and, on GO,
    the participation occasion.
    Inert in production — nothing ships `permissive_policy` into a live
    `RelevanceGatePolicy`; and even in a harness it does nothing unless one is
    wired."""
    assert permissive_policy().conversation("signal", "g1").relevance_gate is True


# ── the home room and the proactive flag (design/dreamer-agent-loop/39 D2/D5) ─


HOME_TOML = f"""
default = "deny"

[[conversations]]
platform  = "signal"
id        = "{GROUP}"
workspace = "friends"
label     = "Friends"
persons   = ["miles", "ada"]
home_room = true
proactive = true
location  = true

[[conversations]]
platform  = "discord"
id        = "1/2"
workspace = "friends"
label     = "Friends discord"
persons   = ["miles", "ada"]

[[conversations]]
platform  = "signal"
id        = "wedding-room"
workspace = "wedding"
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


def test_the_home_room_is_keyed_by_workspace(tmp_path):
    policy = load_policy(_write(tmp_path, HOME_TOML))

    homes = policy.home_rooms()

    assert set(homes) == {"friends"}
    assert homes["friends"].primary_id == GROUP
    assert homes["friends"].platform == "signal"
    assert homes["friends"].location is True


def test_proactive_is_checked_on_the_room_not_on_a_person(tmp_path):
    """39 D5: the owner of a proactive row is the reserved ai_peer id, which
    `is_allowed` would refuse in every explicit-persons room. The flag is the
    grant, and it is deny-by-default in both directions: an unflagged room and
    an unlisted room both answer False."""
    policy = load_policy(_write(tmp_path, HOME_TOML))

    assert policy.proactive_allowed(platform="signal", conversation_id=GROUP) is True
    assert policy.proactive_allowed(platform="discord", conversation_id="1/2") is False
    assert policy.proactive_allowed(platform="signal", conversation_id="nope") is False
    # The persons list still does not know Nora — the flag is a DIFFERENT grant.
    assert _allowed(policy, GROUP, "nora") is False


def test_two_home_rooms_in_one_workspace_refuse_to_start(tmp_path):
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
    invent a home room, or a workspace-keyed harness run would stand in a
    room that does not exist."""
    assert permissive_policy().home_rooms() == {}


# --- `voice = true` -----------------------------------------------------------
# design/voice-channel/07 §12.2, reversed 2026-09-17: a guild's spoken
# utterances are recorded in the ONE room that claims them, resolved by GUILD.

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
    # It is the ROOM's own persons list and workspace that speech inherits —
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
id       = "{VOICE_GUILD}/1785459208793411409"
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

"""ADR 0015 §1: a NEW tool is named `<domain>_<verb>`.

The domain is a service (`web`, `instagram`, `x`) or a resource (`note`,
`music`); the verb says what the tool takes or does. Today's names predate the
rule and stay (ADR 0004: a rename is measured one at a time), so they are
frozen below. The list only shrinks: a tool renamed to the pattern leaves it,
and a name added here needs a written reason in the same commit.
"""

from __future__ import annotations

import re

from nora.agent.tools import NATIVE_TOOLS

#: Every provider's safe set: Anthropic, OpenAI and Gemini all accept
#: `[a-zA-Z0-9_-]`, and OpenAI and Gemini cap names at 64. Lower case and `_`
#: only, because Gemini's best-practice page discourages `-` and `.` in names.
_CHARSET = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

#: `<domain>_<verb>`. `_read` takes a known subject, `_fetch` a URL, `_search`
#: free text, `_list` enumerates; the rest act. A verb outside this set is a
#: decision to write into ADR 0015 first, not a name to invent here.
_VERBS = ("read", "fetch", "search", "list", "create", "update", "cancel", "send")
_SHAPE = re.compile(rf"^[a-z][a-z0-9]*(_[a-z0-9]+)*_({'|'.join(_VERBS)})$")

#: The names that predate ADR 0015 (2026-09-24). Frozen, never grown.
_PREDATES_THE_RULE = frozenset({
    "about", "ask_memory", "calculate", "cancel_event", "cancel_reminder",
    "create_event", "find_parking", "generate_image", "group_members",
    "list_events", "list_reminders", "load_tools", "load_transcript",
    "lookup_person", "make_artifact", "media_fetch", "media_search",
    "media_send", "music_status", "note_create", "note_read", "note_search",
    "note_update", "now", "online_now", "parking_status", "place_info",
    "play_music", "recall_lookups", "remember", "remind", "reverse_geocode",
    "route", "rsvp_event", "saved_places", "search_gifs", "search_memory",
    "search_memory_workspace", "send_gif", "send_reply", "send_to",
    "set_music_volume", "skills", "skip_music", "social_fetch", "stop_music",
    "suggest_venue", "top_music", "transit", "update_event", "version",
    "weather", "web_fetch", "web_search", "where_been", "where_is", "who_said",
})


def test_every_tool_name_uses_the_safe_charset() -> None:
    bad = sorted(name for name in NATIVE_TOOLS if not _CHARSET.match(name))
    assert not bad, f"tool names outside [a-z0-9_], max 64: {bad}"


def test_a_new_tool_is_named_domain_then_verb() -> None:
    new = sorted(
        name for name in NATIVE_TOOLS
        if name not in _PREDATES_THE_RULE and not _SHAPE.match(name)
    )
    assert not new, (
        f"{new}: a tool added after ADR 0015 is named <domain>_<verb> with a "
        f"verb from {_VERBS} (e.g. instagram_read, note_search). Read "
        "docs/adr/0015-tool-surface-policy-v2.md before naming a tool."
    )


def test_the_frozen_list_only_names_tools_that_still_exist() -> None:
    """A tool removed or renamed must leave the list, or the list stops
    shrinking and quietly exempts a name nobody is using."""
    gone = sorted(_PREDATES_THE_RULE - set(NATIVE_TOOLS))
    assert not gone, f"remove from _PREDATES_THE_RULE, no longer tools: {gone}"


def test_the_shape_accepts_the_pattern_and_refuses_the_rest() -> None:
    """Positive control, so a broken regex cannot pass every name vacuously."""
    for good in ("instagram_read", "instagram_search", "x_fetch", "note_list"):
        assert _SHAPE.match(good), good
    for bad in ("read_instagram", "instagram", "instagramread", "instagram_get"):
        assert not _SHAPE.match(bad), bad

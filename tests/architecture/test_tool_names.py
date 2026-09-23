"""ADR 0015 §1: a NEW tool is named `<domain>_<verb>`.

The domain is a service (`web`, `instagram`, `x`) or a resource (`note`,
`room`, `music`); the verb says what the tool takes or does. Today's names
predate the rule and stay (ADR 0004: a rename is measured one at a time), so
they are frozen below. The frozen list only shrinks: a tool renamed to the
pattern leaves it, and its length is pinned.
"""

from __future__ import annotations

import re

import pytest

from nora.agent import tools as tools_module
from nora.agent.tools import NATIVE_TOOLS

#: Lower case, digits and `_`, at most 64 characters. Anthropic allows
#: `[a-zA-Z0-9_-]{1,128}` and MCP allows `.` up to 128; this is the
#: conservative intersection, so a name works under any provider.
_CHARSET = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

#: The verbs, as ADR 0015 §1 delegates them to this constant. `read` takes a
#: known subject, `fetch` a URL, `search` free text, `list` enumerates; the rest
#: act. A new verb is added HERE, in a PR that states why it is not one of these.
VERBS: tuple[str, ...] = (
    "read", "fetch", "search", "list", "create", "update", "cancel", "send",
)


def _shape(verbs: tuple[str, ...] = VERBS) -> re.Pattern[str]:
    return re.compile(rf"^[a-z][a-z0-9]*(_[a-z0-9]+)*_({'|'.join(verbs)})$")


#: The names that predate ADR 0015 (2026-09-24). Frozen; never grown.
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

#: The frozen list's size when the rule landed. It may go DOWN (a rename or a
#: removal), never up: raising this number is exempting a new tool by hand.
_FROZEN_AT_MOST = 57


def _misnamed(names) -> list[str]:
    shape = _shape()
    return sorted(n for n in names if n not in _PREDATES_THE_RULE and not shape.match(n))


def test_every_tool_name_uses_the_safe_charset() -> None:
    bad = sorted(name for name in NATIVE_TOOLS if not _CHARSET.match(name))
    assert not bad, f"tool names outside [a-z0-9_], max 64: {bad}"


def test_a_new_tool_is_named_domain_then_verb() -> None:
    new = _misnamed(NATIVE_TOOLS)
    assert not new, (
        f"{new}: a tool added after ADR 0015 is named <domain>_<verb> with a "
        f"verb from {VERBS} (e.g. instagram_read, room_list). Read "
        "docs/adr/0015-tool-surface-policy-v2.md before naming a tool."
    )


def test_a_misnamed_new_tool_is_actually_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """The live check has nothing to catch today (every current name is
    frozen), so without this a mutant that empties it would pass unseen. Put a
    badly named tool into the table and require the check to name it."""
    fake = dict(NATIVE_TOOLS)
    fake["read_instagram"] = NATIVE_TOOLS["web_fetch"]
    fake["instagram_read"] = NATIVE_TOOLS["web_fetch"]
    monkeypatch.setattr(tools_module, "NATIVE_TOOLS", fake)
    assert _misnamed(tools_module.NATIVE_TOOLS) == ["read_instagram"]


def test_the_frozen_list_only_shrinks() -> None:
    assert len(_PREDATES_THE_RULE) <= _FROZEN_AT_MOST, (
        "_PREDATES_THE_RULE grew: a new tool was exempted by hand instead of "
        "being named <domain>_<verb>"
    )
    gone = sorted(_PREDATES_THE_RULE - set(NATIVE_TOOLS))
    assert not gone, f"remove from _PREDATES_THE_RULE, no longer tools: {gone}"


def test_the_shape_accepts_the_pattern_and_refuses_the_rest() -> None:
    """Positive control, so a broken regex cannot pass every name vacuously."""
    shape = _shape()
    for good in ("instagram_read", "instagram_search", "x_fetch", "room_list", "room_read"):
        assert shape.match(good), good
    for bad in ("read_instagram", "instagram", "instagramread", "instagram_get",
                "my_rooms", "read_room"):
        assert not shape.match(bad), bad

"""ADR 0015 §1: a NEW tool is named `<domain>_<verb>`.

The domain is a service (`web`, `instagram`, `x`) or a resource (`note`,
`room`, `music`); the verb says what the tool takes or does. Today's names
predate the rule and stay (ADR 0004: a rename is measured one at a time), so
they are frozen in a snapshot whose digest is pinned. The exempt set only
shrinks: a tool renamed to the pattern moves to `_RENAMED_SINCE`.
"""

from __future__ import annotations

import hashlib
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
#:
#: `query` (`record_query`): a filtered read over an interval. Not `read` (it
#: names no one subject), not `search` (its free text is optional; «what was said
#: on Saturday» has none), not `list` (it filters by time, author and words, and
#: enumerates nothing fixed).
#:
#: `propose`, `revise`, `set_status`, `rsvp` (the event verbs, DEC-237):
#: `event_propose` is not `create` — it stages or proposes, a status
#: `event_set_status` may later move on, never a finished write; `event_revise`
#: is not `update` — it appends a REVISION to a chain (`base_revision_id`), never
#: an in-place change; `event_set_status` is not `cancel` — it moves an event
#: through the WHOLE lifecycle (confirmed, happened, lapsed, not only
#: cancelled), so one verb covers what `cancel` alone could not; `event_rsvp` is
#: not `create` or `update` — a person's own response, appended (RSVP transition
#: rows), naming neither.
VERBS: tuple[str, ...] = (
    "read", "fetch", "search", "list", "create", "update", "cancel", "send",
    "query", "propose", "revise", "set_status", "rsvp",
    # Nora's computer (DEC-243): the coding-agent convention, Claude Code's
    # Bash/Read/Write/Edit (and Pi's core set). `run` executes a command:
    # restricting the language is not restricting the capability, so there is
    # one runner, not a tool per language. `write` replaces a whole file and
    # `edit` swaps one exact match, two different operations none of
    # `create`/`update` names: `create` implies the thing is new, and `update` a
    # record's fields.
    "run", "write", "edit",
    # Files crossing the computer's edge. `export` moves a file OUT of the
    # workspace to a destination (a link, the room, private custody) and `import`
    # copies one IN. None of the existing verbs fits: `send` names one
    # destination of three and implies a message; `fetch` is taken for reading a
    # remote thing INTO the turn (`media_fetch`), the opposite direction of
    # `export`, and `create`/`write` name the file, not its crossing of the VM
    # boundary. The pair is Claude Code's own words for the two directions.
    "export", "import",
    # Documents (ADR 0020): `publish` makes a written piece readable in the web
    # app AND is how it is revised, one act with two outcomes (a new document, or
    # the next revision of one). `create` would say the thing is always new and
    # `update` that a record's fields change; neither says that the result is now
    # for other people to open, which is the point and the reason the model must
    # be told what it may put in it.
    "publish",
    # The browser (ADR 0022): `browse` hands a whole task in a web browser to a
    # sub-agent, many page actions behind one call. `fetch` reads one known thing
    # into the turn (`web_fetch` is one page's text); `search` asks an index;
    # `run` executes a command. None names an interactive session that navigates,
    # clicks and fills, which is what the model must know it is choosing.
    "browse",
    # `dispute` (35 §2): somebody says a remembered fact is wrong, or asks her
    # to forget it. `update`/`revise` name a record's fields changing; here the
    # record stands and is CONTESTED, which the model must choose on purpose:
    # the subject's own dispute hides the line at once, anyone else's is marked
    # for weighing, and `cancel` would read as taking the memory away.
    "dispute",
)


def _shape(verbs: tuple[str, ...] = VERBS) -> re.Pattern[str]:
    return re.compile(rf"^[a-z][a-z0-9]*(_[a-z0-9]+)*_({'|'.join(verbs)})$")


#: Every tool name that existed when ADR 0015 landed. A SNAPSHOT,
#: never edited: its digest is pinned below, so adding a name to it (exempting a
#: new tool by hand) fails the build. A name leaves the exemption by being
#: added to `_RENAMED_SINCE`, which is how the exempt set only shrinks.
_SNAPSHOT_2026_09_24 = frozenset({
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
_SNAPSHOT_SHA256 = (
    "51189c39c8934b8fe595bdad19f7bc0103c6da453706231f4b310aa2ff8789e3"
)

#: Snapshot names renamed to the pattern or removed since. Grows; the exempt
#: set below shrinks with it.
_RENAMED_SINCE: frozenset[str] = frozenset({
    # DEC-201: retired into `x_read` and `x_search`, which meet the pattern.
    "social_fetch",
    # DEC-237: renamed to the event verbs and `event_search`.
    "create_event", "update_event", "rsvp_event", "cancel_event", "list_events",
    # Retired. A file is made on the computer and leaves through
    # `computer_export`; a document is published through `document_publish`
    # (ADR 0020).
    "make_artifact",
})

_PREDATES_THE_RULE = _SNAPSHOT_2026_09_24 - _RENAMED_SINCE


def _digest(names: frozenset[str]) -> str:
    return hashlib.sha256("\n".join(sorted(names)).encode()).hexdigest()


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


def test_the_snapshot_is_never_edited() -> None:
    """Pinning a LENGTH let a name be swapped in at the same size. The digest
    pins the exact set: exempting a new tool now means changing this hash, a
    diff nobody can make by accident."""
    assert _digest(_SNAPSHOT_2026_09_24) == _SNAPSHOT_SHA256, (
        "the 2026-09-24 snapshot changed. Do not add to it: name the new tool "
        "<domain>_<verb>. A rename goes in _RENAMED_SINCE."
    )


def test_every_exempt_name_is_still_a_tool() -> None:
    """A tool removed or renamed must move to _RENAMED_SINCE, or the exempt
    set quietly keeps a name nobody is using."""
    gone = sorted(_PREDATES_THE_RULE - set(NATIVE_TOOLS))
    assert not gone, f"add to _RENAMED_SINCE, no longer tools: {gone}"


def test_the_shape_accepts_the_pattern_and_refuses_the_rest() -> None:
    """Positive control, so a broken regex cannot pass every name vacuously."""
    shape = _shape()
    for good in ("instagram_read", "instagram_search", "x_fetch", "room_list", "room_read"):
        assert shape.match(good), good
    for bad in ("read_instagram", "instagram", "instagramread", "instagram_get",
                "my_rooms", "read_room"):
        assert not shape.match(bad), bad

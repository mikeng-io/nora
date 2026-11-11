from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

RECALL_LOOKUPS_DEFAULT = 10

RECALL_LOOKUPS_MAX = 30

RECALL_LOOKUPS_WINDOW_DAYS = 7

RECALL_LOOKUPS_MAX_CHARS = 8000

NOTE_SEARCH_LIMIT = 5

_REASONING_LEVELS = frozenset({"minimal", "low", "medium", "high", "max"})

_ROLLUP_OFF = "I can't read the rollups right now. Say so; do not guess what anyone did."

_ROLLUP_NO_BUILD = (
    "No rollup build is current yet, so there are no rows to read. Say so; "
    "do not read it as nothing having happened."
)

_ROLLUP_SUBJECT = (
    "Name a person here. stay, trip and daybook take one name; colocation, "
    "outing and tie take one or several, separated by commas."
)

_ROLLUP_KIND = "That is not a rollup kind. Use stay, trip, daybook, colocation, outing or tie."

_ROLLUP_EVERYONE = (
    "«everyone» stands for the whole room and takes no other name beside it. "
    "Give either everyone or the names, not both."
)

_ROLLUP_NO_PERSON = (
    "Nobody in this conversation goes by that. Check the name with "
    "lookup_person; do not guess who was meant."
)

_ROLLUP_NONE = (
    "No rollup row matches in that stretch. The build reads the Record only "
    "up to `through`. Say you found none, not that nothing happened."
)

_ROLLUP_WITHHELD = (
    "Some rows are not shown in this conversation. Do not read the rest as "
    "complete, and do not hint at what the others hold."
)

_ROLLUP_MORE = (
    "More rows matched than are shown. Narrow the interval or the subject. "
    "A note cites a row by its kind, subject, from and to."
)

_ROLLUP_UNLABELLED = (
    "An unlabelled place is not a guess about where: say it was somewhere not yet named."
)

_STANDING_OFF = "I can't read this room's position right now. Say so; do not guess it."

_STANDING_NONE = "No supported position is held for this room. Say so, and do not reconstruct one."

_CHARACTER_OFF = "I can't read accounts of this group right now. Say so; do not guess one."

_CHARACTER_WITHHELD = (
    "An account is held but carries something that can't be repeated here. "
    "Work from what you already know."
)

_CHARACTER_NONE = (
    "No account is held of that room or person in this group. Say so, and do "
    "not describe them from memory as if one were."
)

_CHARACTER_OK = (
    "Each account is as of its own time and is one room's view. Link it by its "
    "`revision`; do not quote it as what someone said."
)

_CHARACTER_PROFILE_OK = (
    "Each account is as of its own time and is this group's view of that room "
    "or person; a (disputed) line is being weighed, not settled. Do not quote "
    "it as what someone said."
)

_STANDING_WITHHELD = (
    "Some sections are not shown in this conversation. Do not read the rest as complete."
)

_GRAPH_READ_NOTES: Mapping[str, str] = {
    "ok": "Scoped node and its evidence.",
    "partial": "More memory is available.",
    "nothing_found": "No node matched in this scope.",
    "unavailable": "The graph could not be read.",
}

_INDEX_OK = (
    "Each item is something said or written here, with the path that linked "
    "it to the question. Answer from these; an item is evidence, not a fact."
)

_INDEX_PARTIAL = (
    "My memory is still being rebuilt, so this may be incomplete. Use what is "
    "here; if it is empty, say you can't fully check right now, never that it "
    "did not happen."
)

_INDEX_NOTHING = (
    "I searched what is remembered here and found nothing on that. Say you "
    "don't recall it, not that it never happened."
)

_INDEX_WITHHELD = (
    "I found something but can't repeat it here. Answer from what you already "
    "know, or say you're not sure."
)

_INDEX_UNAVAILABLE = (
    "My memory is not answering. Say you can't check right now, and do not say you don't know."
)

_MEMORY_BAD_DATE: Mapping[str, str] = MappingProxyType(
    {
        "status": "unavailable",
        "note": "I couldn't read that date, so I didn't search. Give since and until "
        "as YYYY-MM-DD and call again.",
    }
)

_MEMORY_BAD_SPAN: Mapping[str, str] = MappingProxyType(
    {
        "status": "unavailable",
        "note": "That span ends before it starts, so I didn't search. Swap since and "
        "until and call again.",
    }
)

_PROFILE_UNAVAILABLE: Mapping[str, str] = MappingProxyType(
    {
        "status": "unavailable",
        "note": "I couldn't keep that right now, so nothing was saved. Say so, never "
        "say 記低咗, and don't write it down as a note instead.",
    }
)

_PROFILE_REFUSED_NOTES: Mapping[str, str] = MappingProxyType(
    {
        "instruction_not_subject": "Nothing was saved: only they can tell me how they "
        "want to be treated. Say so.",
        "unlisted_room": "Nothing was saved: they are not one of this room's members, so "
        "what is said about them here is not mine to keep. Say so.",
        "over_cap": "Nothing was saved: I already hold as much as I keep about them. "
        "Say you couldn't keep this one.",
        "identifier": "Nothing was saved: it carries a number or handle I never keep. "
        "Say it without that, then call again.",
        "coordinate": "Nothing was saved: it carries a location I never keep. Say it "
        "without that, then call again.",
        "ids_in_text": "Nothing was saved: a message number is not a fact. Say the fact "
        "itself, then call again.",
    }
)

_PROFILE_DISPUTE_UNAVAILABLE: Mapping[str, str] = MappingProxyType(
    {
        "status": "unavailable",
        "note": "I couldn't record that right now, so nothing was recorded. Say so.",
    }
)

_PROFILE_DISPUTE_NOTES: Mapping[str, str] = MappingProxyType(
    {
        "hidden": "It is forgotten. Say so, and do not bring it up again.",
        "disputed": "It is marked as disputed and I will weigh it. Meanwhile, do "
        "not state it as fact.",
        "recorded": "It is recorded. I could not tell which remembered fact it "
        "was about, so ask which one if that matters.",
    }
)

_PROFILE_DISPUTE_REFUSED_NOTES: Mapping[str, str] = MappingProxyType(
    {
        "forget_not_subject": "Nothing was recorded: only they can ask me to "
        "forget something about themselves. Say so.",
        "identifier": "Nothing was recorded: it carries a number or handle I never "
        "keep. Say it without that, then call again.",
        "coordinate": "Nothing was recorded: it carries a location I never keep. "
        "Say it without that, then call again.",
    }
)

_MEMORY_BAD_KIND: Mapping[str, str] = MappingProxyType(
    {
        "status": "unavailable",
        "note": "I don't know that kind of memory, so I didn't search. Use messages, "
        "media, notes, events or places, or leave kinds out.",
    }
)

_MEMORY_UNLISTED: Mapping[str, str] = MappingProxyType(
    {
        "status": "unavailable",
        "note": "That person is not one of this room's members, so I can't search "
        "for what is theirs here. Say you can't check, and do not say "
        "there was nothing.",
    }
)

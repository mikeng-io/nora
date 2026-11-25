from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

RECALL_LOOKUPS_DEFAULT = 10

RECALL_LOOKUPS_MAX = 30

RECALL_LOOKUPS_WINDOW_DAYS = 7

RECALL_LOOKUPS_MAX_CHARS = 8000

NOTE_SEARCH_LIMIT = 5

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

_STANDING_WITHHELD = (
    "Some sections are not shown in this conversation. Do not read the rest as complete."
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
    }
)

_PROFILE_DISPUTE_REFUSED_NOTES: Mapping[str, str] = MappingProxyType(
    {
        "no_tagger": "Nothing was recorded: I don't know who you are well enough to "
        "say who disputes it. Say you can't, and don't guess.",
        "empty": "Nothing was recorded: name the claim by the ID its row starts with, "
        "then call again.",
        "forget_not_subject": "Nothing was recorded: only they can ask me to "
        "forget something about themselves. Say so.",
    }
)

_PROFILE_DISPUTE_NOT_FOUND: Mapping[str, str] = MappingProxyType(
    {
        "status": "not_found",
        "note": "Nothing was recorded: no remembered claim here has that ID. Use the "
        "ID its row starts with, and don't guess one.",
    }
)

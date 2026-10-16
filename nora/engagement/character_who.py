"""Whose `<character>` account a turn renders (DEC-278/DEC-290, narrowed).

The block carries only the accounts of the people a turn is about, not every
person the room lists. The rule is deterministic and reads only ids the
pipeline already resolved, never the text:

* **tag** - the tagger, anyone the tag message @-mentions, the author of the
  message it replies to (and anyone THAT message mentions), and every person
  who spoke in the transcript window;
* **fire** - the occasion's own person (a reminder's requester) and every
  person who spoke in the window.

Always intersected with the room's listed persons, in roster order: an
account still renders only in a room that lists its person,
and the rest of the roster is out. Nobody
is lost to the unit: `character_read` serves any account, but only on the
Dreamer loop surface (`DREAMER_LOOP_TOOLS`), not on a tag or fire turn.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence

from nora.domain.models import NormalizedMessage, PersonRef, TranscriptRow
from nora.persons import Persons
from nora.platform.signal.parser import mention_spans


def _speakers(
    persons: Persons, platform: str, transcript: Sequence[TranscriptRow],
) -> set[str]:
    """Person ids of every non-Nora row, resolved through `Persons` like
    `FirePath._window_speakers`: an unmapped sender contributes nothing."""
    found: set[str] = set()
    for row in transcript:
        if row.is_nora:
            continue
        ref = persons.resolve(platform, row.sender_id)
        if ref is not None:
            found.add(ref.person_id)
        elif row.person_id:
            found.add(row.person_id)
    return found


def _resolve_all(persons: Persons, platform: str, ids: Iterable[str]) -> set[str]:
    return {
        ref.person_id for value in ids
        if (ref := persons.resolve(platform, value)) is not None
    }


def _row_mentions(row: TranscriptRow) -> frozenset[str]:
    """The ids a stored row's envelopes @-mention (the structural array, the
    same one `MentionRenderer` reads): the original's and, for an edited row,
    the edit's, so a mention an edit added counts. Unreadable means none."""
    found: set[str] = set()
    for raw in (row.raw_json, row.edit.raw_json if row.edit is not None else None):
        if not raw:
            continue
        try:
            event = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if isinstance(event, dict):
            found.update(i for span in mention_spans(event) for i in span.ids)
    return frozenset(found)


def tag_character_people(
    members: Sequence[PersonRef], *, persons: Persons, message: NormalizedMessage,
    tagger: PersonRef | None, transcript: Sequence[TranscriptRow],
) -> tuple[PersonRef, ...]:
    """The listed `members` whose account this tag turn renders."""
    platform = message.platform
    ids: set[str] = set()
    if tagger is not None:
        ids.add(tagger.person_id)
    ids |= _resolve_all(persons, platform, message.mentions)
    if message.quote is not None:
        # The parent row's recorded author is authoritative (R-20); the quote's
        # own `author_id` is the sender's claim, used only for a parent that
        # fell outside the window. Either way it only ever selects a LISTED
        # member's account, which a forged id cannot widen.
        if message.quote.author_id:
            ids |= _resolve_all(persons, platform, (message.quote.author_id,))
        parent = next(
            (r for r in transcript if r.ts == message.quote.quoted_ts and not r.is_nora),
            None,
        )
        if parent is not None:
            ids |= _resolve_all(persons, platform, (parent.sender_id, *_row_mentions(parent)))
    ids |= _speakers(persons, platform, transcript)
    return tuple(m for m in members if m.person_id in ids)


def fire_character_people(
    members: Sequence[PersonRef], *, persons: Persons, platform: str,
    person: PersonRef | None, transcript: Sequence[TranscriptRow],
) -> tuple[PersonRef, ...]:
    """The listed `members` whose account this fire turn renders."""
    ids = _speakers(persons, platform, transcript)
    if person is not None:
        ids.add(person.person_id)
    return tuple(m for m in members if m.person_id in ids)

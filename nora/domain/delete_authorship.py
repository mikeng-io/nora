"""Who may withdraw a message by deleting it, one rule for every place it is asked
(design/context-assembly/04-every-occasion.md §7 (A), DEC-276).

On Signal and WhatsApp only a message's author can delete it for everyone. The
platform cannot be trusted to have enforced it on WhatsApp (end-to-end encrypted:
the bridge forwards any REVOKE), so Nora checks, and she checks the same way at
every point a delete takes effect: at ingest (`nora.ingest.stages.touch`), when a
delete that overtook its message is applied (`RecordStore.match_orphan_deletes`),
when the start-up sweep reads the deletes already on record
(`nora.app.delete_withdrawal`), and when `nora person unforget` decides whether a
hidden message was deleted (`nora.app.person_forget`). Two copies of this rule are
two answers to «was that a real delete».
"""

from __future__ import annotations

from collections.abc import Collection, Iterable

from nora.domain.models import OrphanDelete

__all__ = [
    "AUTHOR_ONLY_DELETES",
    "DELETER_IDS_KEY",
    "ORPHAN_CAP_GLOBAL",
    "ORPHAN_CAP_PER_DELETER",
    "ORPHAN_CAP_PER_ROOM",
    "ORPHAN_CAP_UNNAMED_PER_ROOM",
    "ORPHAN_TTL_MS",
    "deleter_is_author",
    "orphan_names_message",
]

#: How long a delete waits for a message that has not arrived. The race it exists for
#: lasts seconds (a photo's custody fetch) to minutes, and a bridge backlog after an
#: outage hours: three days covers every real case. A row older than this is purged, by
#: the start-up sweep and opportunistically whenever one is written. A message that
#: arrives later than this after its own delete stays visible: there is nothing left to
#: apply, and that is a LEAK-direction loss, which is why the window is generous and
#: why a row is never refused for room (see the caps).
ORPHAN_TTL_MS = 3 * 24 * 3600 * 1000

#: Caps on held deletes, enforced when one is written, SOFT (counted before the insert
#: under READ COMMITTED, so concurrent writers can overshoot by a few) and by EVICTION,
#: never by refusal: a new held delete always lands, and a flood evicts the OLDEST rows of
#: its own scope first (`orphan_evicted`, logged without text). Authorship can only be
#: checked once a target exists, so any WhatsApp member can write rows by revoking ids that
#: do not exist, and Discord and Instagram deletes name nobody at all.
#:
#: Ordered so that one party cannot starve another: a NAMED deleter is bounded well below
#: the room (`PER_DELETER` < `PER_ROOM`), an UNNAMED deleter (Discord and Instagram name
#: nobody, so every row has `deleter_id == ""`) is bounded per ROOM, never as one shared
#: bucket for the platform, and the room is bounded well below the global ceiling.
#:
#: **Residual risk, stated plainly.** Eviction is a LEAK-direction event: an evicted held
#: delete that a message then arrives for leaves that message VISIBLE and fed to memory. It
#: needs a flood large enough to push a legitimate row out within its seconds-to-minutes
#: race window (more than `PER_DELETER` revokes of ids that do not exist from one
#: modified WhatsApp client for ITS OWN rows; more than `PER_ROOM` from many for a room's),
#: and the flood is loud (`orphan_evicted` counts it). What bounds it is that evictions
#: take the flooder's oldest rows first and that the table cannot grow without limit.
ORPHAN_CAP_PER_DELETER = 40
ORPHAN_CAP_UNNAMED_PER_ROOM = 100
ORPHAN_CAP_PER_ROOM = 200
ORPHAN_CAP_GLOBAL = 5000


#: The key under which Nora stores the deleter's id forms in a `message_delete` record's
#: event (`raw_json`). Written by the touch stage, by the pipeline's application of a held
#: delete and by the sweep, OVERWRITING anything an event of that name carried, and the
#: only key `genuine` trusts: a historic event that happens to contain an `actor_ids` (or
#: any other) key is not read as the deleter's id forms.
DELETER_IDS_KEY = "nora_deleter_ids"

#: Platforms where only a message's author can delete it for everyone, so a
#: delete naming someone else's message is not a member's action: Signal's remote
#: delete and WhatsApp's revoke. On WhatsApp the platform enforces nothing the
#: server can refuse (end-to-end encrypted: the bridge forwards any REVOKE), and
#: the event carries the REVOKER (`sender_*`, the envelope's sender), so the check
#: is ours; a group admin revoking someone else's message cannot be verified
#: without group metadata Python does not hold and is REFUSED (`refused_not_author`:
#: the message stays visible). Discord's gateway and Instagram's unsend name no
#: deleter at all (`sender_id == ""`), so there is nothing to compare; both are
#: server-authenticated events and are honoured as the platform reports them.
AUTHOR_ONLY_DELETES: frozenset[str] = frozenset({"signal", "whatsapp"})


def _id(value: str | None) -> str:
    """An id as the rule compares it: stripped, and a blank one is no id."""
    return (value or "").strip()


def deleter_is_author(
    *,
    author_sender_id: str,
    author_person_id: str | None,
    deleter_sender_id: str,
    deleter_person_id: str | None,
    deleter_ids: Iterable[str] = (),
) -> bool:
    """Whether the account that deleted a message is the one that wrote it.

    The same platform id, or the same resolved person (one person, two id forms,
    both mapped in identity.toml), or: `deleter_ids`, EVERY id form the platform
    gave for the deleter (WhatsApp's revoke event carries the phone, the LID and the
    raw JID, and the parser picks one; a message stored under the phone and revoked
    under the LID by an author nobody mapped is still the author's). A deleter that
    names nobody never matches."""
    author, deleter = _id(author_sender_id), _id(deleter_sender_id)
    if author and author == deleter:
        return True
    # Two EMPTY person ids are not «the same person»: both sides unresolved.
    person = _id(author_person_id)
    if person and person == _id(deleter_person_id):
        return True
    return bool(author) and author in {_id(i) for i in deleter_ids if _id(i)}


def orphan_names_message(
    orphan: OrphanDelete, *, platform: str, conversation_id: str, handle: str,
    timestamps: Collection[int], sender_id: str, person_id: str | None,
) -> bool:
    """Whether a held delete names a stored (or just-recorded) message: THE one rule,
    used by the pipeline's check after a message is audited
    (`RecordStore.match_orphan_deletes`), by its fake, and by the start-up sweep, so the
    two can never disagree about what a held delete applies to.

    The same platform and room; a NON-EMPTY platform handle equal to the message's, or
    Signal's sent-timestamp equal to the message's own timestamp or one of its edits'
    (`timestamps`); and, where only the author can delete (`AUTHOR_ONLY_DELETES`), a
    deleter who is the message's author. SQL narrows the candidates for speed; this
    decides."""
    if orphan.platform != platform or orphan.conversation_id != conversation_id:
        return False
    by_handle = bool(orphan.handle) and orphan.handle == handle
    by_ts = orphan.sent_ts is not None and orphan.sent_ts in timestamps
    if not (by_handle or by_ts):
        return False
    if platform not in AUTHOR_ONLY_DELETES:
        return True
    return deleter_is_author(
        author_sender_id=sender_id, author_person_id=person_id,
        deleter_sender_id=orphan.deleter_id, deleter_person_id=orphan.deleter_person_id,
        deleter_ids=orphan.deleter_ids,
    )

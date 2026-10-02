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
    "ORPHAN_RACE_WINDOW_MS",
    "ORPHAN_TTL_MS",
    "deleter_is_author",
    "orphan_names_message",
    "target_age_ms",
    "target_is_stale",
]

#: How long a delete waits for a message that has not arrived. The race it exists for
#: lasts seconds (a photo's custody fetch) to minutes, and a bridge backlog after an
#: outage hours: three days covers every real case. A row older than this is purged, by
#: the start-up sweep and opportunistically whenever one is written. A message that
#: arrives later than this after its own delete stays visible: there is nothing left to
#: apply, and that is a LEAK-direction loss, which is why the window is generous and
#: why a row is not refused for room (see the caps).
ORPHAN_TTL_MS = 3 * 24 * 3600 * 1000

#: How old a delete's TARGET may be, at the moment the delete arrives, for the delete to be
#: held when its message is not on record. **This is an ASSUMPTION about the ingest
#: paths, not a proof.** What it measures is the age of the target when the delete
#: ARRIVES (a Discord snowflake carries its creation time; a Signal target IS its sent
#: timestamp and the age is the event's time minus it; WhatsApp and Instagram handles
#: carry no time, so those deletes are always held). What a held delete is FOR is a
#: different interval: whether the delete reaches Nora BEFORE its own message does. The
#: assumption is that this never happens for a target older than the window, because a
#: message reaches Nora within seconds of being sent (a photo's custody fetch, a task
#: scheduled late), a restart replays nothing and there is no history backfill. Two paths
#: are known to break it, and neither is verified: a Discord gateway RESUME replaying an
#: outage longer than the window (how long Discord keeps a session resumable is not
#: confirmed), and a linked Signal device with a skewed clock. For those a delete of a
#: target older than the window is DROPPED (counted `orphan_stale_target`, nothing held),
#: the message is then recorded and stays VISIBLE and is fed to memory: the leak
#: direction. What an operator can do: `nora withdrawal reconcile-deletes` does NOT reach
#: it (a dropped delete leaves no record and no held row for it to apply); the sender
#: deleting the message AGAIN does (it is on record now, so the delete is found and
#: withdrawn), and `nora person forget` hides a whole person's messages. A rise of
#: `orphan_stale_target` after a Discord outage, or beside a Signal clock problem, is the
#: sign to ask. What the window buys: the ordinary flood (her own sends, embed-only posts,
#: history from before she joined, pruned messages) is not held.
ORPHAN_RACE_WINDOW_MS = 30 * 60 * 1000

_DISCORD_EPOCH_MS = 1420070400000          # a snowflake's creation time, in ms, is
_DISCORD_SHIFT = 22                        # (id >> 22) + this epoch


def target_age_ms(
    *, platform: str, handle: str, sent_ts: int | None, event_ts: int,
) -> int | None:
    """How old the deleted message was when the delete arrived, in ms, or None where
    the platform's handle carries no time (WhatsApp, Instagram, a non-numeric id)."""
    if sent_ts is not None:
        return event_ts - sent_ts if platform == "signal" else None
    handle = _id(handle)
    if platform == "discord" and handle.isascii() and handle.isdigit():
        return event_ts - ((int(handle) >> _DISCORD_SHIFT) + _DISCORD_EPOCH_MS)
    return None


def target_is_stale(
    *, platform: str, handle: str, sent_ts: int | None, event_ts: int,
) -> bool:
    """True when the delete's target is older than `ORPHAN_RACE_WINDOW_MS`: not the
    overtaking race, so not worth holding. A future-dated target is never stale."""
    age = target_age_ms(platform=platform, handle=handle, sent_ts=sent_ts, event_ts=event_ts)
    return age is not None and age > ORPHAN_RACE_WINDOW_MS


#: Caps on held deletes, enforced when one is written, SOFT (counted before the insert
#: under READ COMMITTED, so concurrent writers can overshoot by a few) and by EVICTION
#: of the oldest rows of a scope, never by refusal. A new held delete lands unless a
#: concurrent writer deadlocks and the retries are exhausted (`orphan_lost`), and any
#: held delete can be evicted by a large enough flood (`orphan_evicted`, logged without
#: text). Authorship can only be checked once a target exists, so any WhatsApp member can
#: write rows by revoking ids that do not exist, and Discord and Instagram deletes name
#: nobody at all and are often of messages Nora never recorded.
#:
#: Eviction takes the OLDEST rows, in three scopes tried in this order: per DELETER (a
#: named deleter's own rows, so a flooder evicts its OWN oldest first: true of this scope
#: only), per ROOM, then overall. The room scope evicts the oldest row of the HEAVIEST
#: contributor in that room (the deleter, or the unnamed bucket, holding the most rows),
#: so a victim's single row survives unless every deleter in the room holds a single row.
#: The overall scope is plain oldest-first. Ordered so that one party cannot starve
#: another: PER_DELETER < PER_ROOM, UNNAMED_PER_ROOM < PER_ROOM (every Discord and
#: Instagram row has `deleter_id == ""`, so they are bounded per ROOM, never as one bucket
#: for the platform), and GLOBAL is at least ten rooms.
#:
#: **Residual risk, stated plainly.** Eviction is a LEAK-direction event: an evicted held
#: delete that a message then arrives for leaves that message VISIBLE and fed to memory.
#: What it takes, by scope: (1) per deleter, more than `PER_DELETER` revokes of ids that
#: do not exist from one modified WhatsApp client evicts that client's OWN oldest rows
#: only; (2) per room, `PER_DELETER` caps every member across the platform, so a room
#: reaches `PER_ROOM` only with more than `PER_ROOM / PER_DELETER` (25) flooding members,
#: an ARITHMETIC bound (five colluding members hold at most 200 rows and never get there);
#: past it heaviest-first spares a victim's lone row unless every deleter holds a single
#: one; (3) overall, a flood across many rooms. On Discord and Instagram, which name
#: nobody, the unnamed cap (`UNNAMED_PER_ROOM`) always binds BEFORE the room scope, so
#: heaviest-first only matters for WhatsApp and Signal rooms. A Discord or Instagram flood
#: of `UNNAMED_PER_ROOM` ids that look recent CAN evict a legitimate unnamed row while its
#: message is on the way (the window rules out the old ids only). Nothing pages on a
#: single eviction; `OrphanDeleteEvicted` fires on a sustained rate and `OrphanDeleteLost`
#: on any lost write (`deploy/prometheus/rules/nora.yml`).
ORPHAN_CAP_PER_DELETER = 40
ORPHAN_CAP_UNNAMED_PER_ROOM = 500
ORPHAN_CAP_PER_ROOM = 1000
ORPHAN_CAP_GLOBAL = 10_000


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

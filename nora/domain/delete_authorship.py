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

from collections.abc import Iterable

__all__ = ["AUTHOR_ONLY_DELETES", "deleter_is_author"]

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
    if author_sender_id and author_sender_id == deleter_sender_id:
        return True
    if author_person_id is not None and author_person_id == deleter_person_id:
        return True
    return bool(author_sender_id) and author_sender_id in {i for i in deleter_ids if i}

"""WhatsApp JID normalization — the pure half of the wire contract

Everything the policy layer matches on is exact-string: `policy.toml` on
`conversation_id`, `identity.toml` on `sender_id`, `registry.self_aliases()`
on mention values. A JID that arrives as `85290000100:12@s.whatsapp.net`,
`85255080100@s.whatsapp.net`, or `60000000000001@lid` is the SAME account in
three spellings, and an adapter that forwards whichever spelling showed up
first would make admission depend on Baileys' mood. This module is the one
place the spellings collapse to Nora's forms:

- **phone form** — `"+" + digits`, deliberately the shape Signal's E.164
  values already take, so `identity.toml` reads the same on both platforms.
- **lid form** — bare digits, matching the `lid` kind already declared in
  `nora/domain/identity.py`.

Standard library only, no I/O — the same rule `nora/domain` holds itself to,
because these helpers are the contract's executable half and must be testable
without a bridge.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The three servers a chat or sender can live on, plus the two that are
#: never conversations at all. `status@broadcast` is the status feed and
#: `@newsletter` a one-to-many channel — neither is a room Nora is IN, so
#: both are dropped before a `NormalizedMessage` is ever built.
PHONE_SERVER = "s.whatsapp.net"
GROUP_SERVER = "g.us"
LID_SERVER = "lid"
NEWSLETTER_SERVER = "newsletter"


@dataclass(frozen=True)
class JidParts:
    """One JID decomposed: `<user>[:<device>]@<server>`.

    `device` is Baileys' multi-device suffix (`85290000100:12@…`). It is
    parsed out and then deliberately UNUSED everywhere: two devices of one
    account must resolve to one identity, and matching on a device suffix
    would make a phone upgrade silently unmap a person."""

    user: str
    server: str
    device: str | None = None


def parse_jid(jid: str) -> JidParts | None:
    """`<user>[:<device>]@<server>`, or None for anything that is not that.

    Total by construction: the bridge forwards Baileys' fields unmodified,
    so a malformed value is a real input, not a hypothetical one."""
    if not isinstance(jid, str):
        return None
    user_part, sep, server = jid.rpartition("@")
    if not sep or not user_part or not server:
        return None
    user, colon, device = user_part.partition(":")
    if not user:
        return None
    return JidParts(user=user, server=server, device=device if colon else None)


def is_group_jid(jid: str) -> bool:
    parts = parse_jid(jid)
    return parts is not None and parts.server == GROUP_SERVER


def is_lid_jid(jid: str) -> bool:
    parts = parse_jid(jid)
    return parts is not None and parts.server == LID_SERVER


def is_phone_jid(jid: str) -> bool:
    parts = parse_jid(jid)
    return parts is not None and parts.server == PHONE_SERVER


def is_broadcast(jid: str) -> bool:
    """`status@broadcast` (or any other `@broadcast`) — a feed, not a room."""
    parts = parse_jid(jid)
    return parts is not None and parts.server == "broadcast"


def is_newsletter(jid: str) -> bool:
    parts = parse_jid(jid)
    return parts is not None and parts.server == NEWSLETTER_SERVER


def phone_form(digits: str) -> str:
    """`"+85290000100"` — the one shape a phone number takes anywhere Nora
    matches on it: `sender_id`, mention values, quote authors, self aliases.

    The SAME shape Signal's E.164 values take, on purpose: an
    `identity.toml` entry reads identically whichever platform it came from."""
    return f"+{digits.strip().lstrip('+')}"


def lid_form(digits: str) -> str:
    """Bare digits — the shape the `lid` id-kind in `nora.domain.identity`
    already declares. A LID is NOT a phone number and never gets a `+`:
    conflating the two would let one account's LID match another's phone."""
    return digits.strip().lstrip("+")


def conversation_id_for(chat_jid: str, resolved_phone: str | None) -> str:
    """What `policy.toml` will match on, exact-string.

    - group: `chat_jid` verbatim (`<id>@g.us`) — a group JID is already
      the room's one name.
    - 1:1: `<phone>@s.whatsapp.net` when the chat's phone is known (a direct
      phone JID, or a LID JID the bridge resolved through its map); the raw
      `@lid` JID otherwise. The form matters because admission is
      exact-string, and because it is the address a 1:1 send goes back to:
      `nora.persons.Persons.direct_target` builds the same two forms, and the
      sender takes only the ones that belong to somebody in identity.toml
      (R-101).
    """
    if is_group_jid(chat_jid):
        return chat_jid
    if resolved_phone:
        return f"{lid_form(resolved_phone)}@{PHONE_SERVER}"
    return chat_jid


def sender_form(
    sender_jid: str, sender_phone: str | None, sender_lid: str | None
) -> str:
    """The one `sender_id` string `identity.toml` is matched against.

    Phone form when the phone is known, lid form when only the LID is, the
    raw JID as a last resort — which matches nothing and fails CLOSED, the
    correct answer for a sender Nora cannot name (R-31's spirit). The bridge
    resolves LID→phone from its session map (`sender_phone`), so the common
    LID-jid group sender still lands on the phone form; this ordering is
    what keeps a sender stable when Baileys flips which JID it reports."""
    if sender_phone:
        return phone_form(sender_phone)
    if sender_lid:
        return lid_form(sender_lid)
    return sender_jid

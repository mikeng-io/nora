"""Instagram id normalization — the pure half of the wire contract.

This is `nora/platform/whatsapp/jid.py`'s seat in the package, and it does a
much smaller job, because Instagram hands the adapter **bare numeric strings
in three disjoint spaces** rather than one composite address:

- **interop fbid** (`sender.fbid`, `mentions[].fbid`, `self.fbid`) — the
  MESSAGING identity. `client.GetOwnFBID()` returns it, and it is the only
  number "was I tagged?" may compare against.
- **igid** (`sender.igid`, `self.igid`) — the PUBLIC/social id, what
  `GetProfile` and `FetchReel` take. It is NOT a messaging address and must
  never be compared to a mention.
- **short thread id** (`room_id`) — the room key, and the ONLY thread id the
  contract exposes. The bridge owns the short↔long translation precisely so
  that exactly one piece of code can step in that trap.

**There is no parsing here, and that absence is the design.** A WhatsApp JID
carries its own server, so `parse_jid` can answer "is this a group?" from the
string. An Instagram id is digits and nothing else; every question about it
is answered by WHICH FIELD it arrived in, never by its shape. So these
functions normalise (strip, reject empty) and refuse to classify.

**Do not validate an id by shape.** Real interop
fbids on one three-person room were `186217604552756` (15 digits) and
`18457536310970624` (17 digits, sharing the `1784…` prefix with Nora's own
`18247361241432477`). Any length or prefix check would reject real members —
silently, because a rejected sender is simply a sender Nora cannot name.

The one shape check a reader reaches for — "is this all digits?" — is also
useless in the one place it is tempting: the LONG thread id
(`340282366841710301281152937372201004392`) is all digits too, which is
exactly why a long id in `room_id` is a **400 from the bridge** rather than
something this module could catch.

Standard library only, no I/O — the same rule `nora/domain` holds itself to,
because these helpers are the contract's executable half and must be testable
without a bridge.
"""

from __future__ import annotations

#: `mentions[].range_type` for Instagram's @everyone (`slidetypes.Mention.
#: ProfileRangeType`). The bridge passes it through unchanged and the bridge's
#: own comment says why it must: for a THREAD range `fbid` carries the CHAT's
#: id, not a person's. See `is_person_mention`.
THREAD_RANGE_TYPE = "THREAD"

#: The range type a mention of an actual account carries. Anything else is
#: refused by `is_person_mention` — fail-closed, because the whole cost of
#: getting this wrong is a room id sitting in a set that resolves people.
PROFILE_RANGE_TYPE = "PROFILE"


def fbid_form(value: object) -> str:
    """The one spelling an `interop_messaging_user_fbid` takes anywhere Nora
    matches on it: `sender_id`, mention values, quote authors, self aliases.

    Bare digits, whitespace stripped, and **never a `+`** — unlike WhatsApp's
    `phone_form`, which adds one so an `identity.toml` entry reads the same as
    Signal's. An Instagram fbid is not a phone number and a `+` would make one
    platform's id string-equal to another's; the self-alias union in
    `nora.platform.registry.AdapterRegistry.self_aliases` is only safe because
    the spaces stay disjoint.

    Returns `""` for anything that is not a usable string. An empty id matches
    nothing, which is the correct answer for a sender Nora cannot name
    (R-31's spirit) — and every caller here drops on empty rather than
    forwarding it.
    """
    if not isinstance(value, str):
        return ""
    return value.strip()


def igid_form(value: object) -> str:
    """The public Instagram numeric id, normalised the same way.

    A SEPARATE function from `fbid_form` despite doing the same thing today,
    because the two answer different questions and only one of them is a
    messaging address. Collapsing them into one helper is how an igid ends up
in a mention comparison — the failure where the wrong id produces no error
anywhere and Nora simply never notices she
    was tagged.
    """
    if not isinstance(value, str):
        return ""
    return value.strip()


def conversation_id_for(room_id: object) -> str:
    """What `policy.toml` matches on, exact-string: the SHORT thread id,
    verbatim.

    Verbatim and not decorated. WhatsApp's `conversation_id_for` has a real
    decision to make (group JID, phone JID, or the raw `@lid`); here the
    bridge has already made it — the contract exposes only the short id, inbound
    deltas carry only the short id, and every send route takes the short id.
    A second spelling invented here would be a second thing for the operator's
    `policy.toml` line to fail to equal.
    """
    if not isinstance(room_id, str):
        return ""
    return room_id.strip()


def is_person_mention(range_type: object) -> bool:
    """Whether a `mentions[]` entry's `fbid` names a PERSON.

    **False for `THREAD`, and this is load-bearing.** Instagram's @everyone
    arrives as an ordinary mention whose `user_fbid` is the CHAT's id — the
    library says so on the field itself, and the bridge documents it rather
    than dropping it, so the adapter can still read the room id if it ever
    wants one. Admitting that number into `NormalizedMessage.mentions` would
    put a ROOM's id into the set `MentionPolicy` and `nora.persons` resolve
    PEOPLE from. It can never equal Nora's own fbid, so it would not make her
    answer an @everyone; what it would do is make a room id a matchable
    person-shaped value, which is the id-confusion hole this module exists to close.

    An ABSENT `range_type` is admitted: the field is `omitempty` on the wire,
    so a plain profile mention may arrive without it, and refusing those would
    drop every real tag. Only a range type that is present and not `PROFILE`
    is refused — fail-closed on the values that are known to mean something
    other than "a person".
    """
    if range_type is None or range_type == "":
        return True
    return range_type == PROFILE_RANGE_TYPE

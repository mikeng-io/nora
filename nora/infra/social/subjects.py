"""`PersonsSubjectIndex` — the production `SocialSubjectIndex`.

Two questions, answered from two files that already exist, and never from
anything the model supplies:

* **Resolution** — which igid is this person? `identity.toml`, through
  `Persons.instagram_igids`, which is KIND-aware: the messaging `fbid` is a
  decimal string too, and handing it to a route that wants an igid names a
  different account or none with no error anywhere.
* **Authorization** — may Nora read this person's story? `policy.toml`: the
  person is LISTED in the `persons` of at least one configured room. That is
  Tier 0, «someone in a room Nora is in», and it is the only tier a story read
  has. That is a privacy choice: Nora does not read the stories of people the
  operator has not listed. It is not a claim that the author sees the view:
  whether they do is unknown.

**The policy is taken STRUCTURALLY, not imported.** `nora.infra` may not
import `nora.policy` (`pyproject.toml`: "the room policy is composed in, never
imported by a lower layer"), so the constructor types it as `_Rooms` below: the
two read-only attributes this class reads, which `nora.policy.Policy` and its
`Conversation` rows satisfy as they are. The composition root passes the real
`Policy`; nothing here learns that `nora.policy` exists.

Both answers are read once at startup, like the two files themselves (no hot
reload), so neither method does I/O and neither can raise on a healthy index.
Both still fail CLOSED behind a backstop, as the port requires: a broken index
costs a read that should have happened and never grants one that should not.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Protocol

from ...persons import Persons

log = logging.getLogger("nora.social.subjects")


class _Room(Protocol):
    """One configured room, as far as reach is concerned. `None` is
    `persons = "*"` (`nora.policy.Conversation`)."""

    @property
    def persons(self) -> frozenset[str] | None: ...


class _Rooms(Protocol):
    """Every configured room (`nora.policy.Policy.conversations`)."""

    @property
    def conversations(self) -> Sequence[_Room]: ...


class PersonsSubjectIndex:
    """`SocialSubjectIndex` over the person index and the room policy.

    Collaborators through `__init__` only (test-seam policy). Holds no copy of
    either file's facts: `Persons` and `Policy` are both frozen at startup, so
    reading through them on each call cannot drift from them.
    """

    def __init__(self, persons: Persons, policy: _Rooms) -> None:
        self._persons = persons
        self._policy = policy

    async def igids_for(self, *, person_id: str) -> frozenset[str] | None:
        """Every igid on file for the person — empty when there is none —
        or `None` when the index could not be read.

        It returns the SET and never a pick. Choosing is the router's
        question and its answer is «only if there is exactly one»: nothing in
        a `person_id` says which of a friend's accounts was meant, and
        choosing one reads a story its author did not expect Nora to open. A
        link names ONE account, so the link path goes through
        `person_for_igid` instead and never through here."""
        try:
            return frozenset(self._persons.instagram_igids(person_id))
        except Exception:
            log.exception("subject index: igid lookup failed")
            return None

    async def is_tier_zero(self, *, person_id: str) -> bool:
        """The person is LISTED by id in the `persons` of at least one
        configured room.

        **A `persons = "*"` room does not count** (`Conversation.persons is
        None`). An open room admits anyone and asserts nobody's membership:
        counting it would make every person in `identity.toml` Tier 0 the
        moment the operator opened one room, which is a reach decision nobody
        made. `[dm] persons` is not a room and does not count either."""
        try:
            return any(
                convo.persons is not None and person_id in convo.persons
                for convo in self._policy.conversations
            )
        except Exception:
            log.exception("subject index: tier check failed")
            return False

    async def person_for_igid(self, *, igid: str) -> str | None:
        """The person that igid belongs to, or `None` — the reverse lookup the
        link path needs.

        Backed by `Persons.resolve("instagram", igid)`, and then CHECKED
        against the kind: `resolve` reads the kind-blind index, so an fbid
        string would resolve to its owner too. An id that is not one of that
        person's igids is `None`, so a messaging id never resolves as an igid."""
        try:
            ref = self._persons.resolve("instagram", igid)
            if ref is None or igid not in self._persons.instagram_igids(ref.person_id):
                return None
            return ref.person_id
        except Exception:
            log.exception("subject index: reverse lookup failed")
            return None

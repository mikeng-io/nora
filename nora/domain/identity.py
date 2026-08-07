"""Typed identifiers and normalised membership (design/10 §3, §3.1, §4.4).

Standard library only — same rule as the rest of `nora/domain`
(`nora/domain/__init__.py`), enforced by `tests/test_boundaries.py` and
import-linter's "domain is framework-free" contract.

The schema this module implements is SETTLED (design/10-identity-and-policy-
schema.md) — this is not the place to re-litigate the two-value `Membership`
set or the fixed per-platform id-kind vocabulary; see that document for the
reasoning.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType

# Never matchable, on any platform (design/10 §1.3, §3.1). Signal profile
# names and WhatsApp push names are user-controlled and trivially spoofable —
# they may be shown to the operator and rendered in a transcript, but an
# `Identifier` built with this kind is a startup error, not a silently
# ignored key.
DISPLAY_NAME_KIND = "display_name"

# A per-account human label ("personal", "business"). Reserved by NAME, and
# excluded from the reverse index by name rather than incidentally: it is
# operator-authored free text, so letting it fall through into the matchable
# id set would make a spoofable string a resolvable identity. Same rule as
# `DISPLAY_NAME_KIND` above and as `policy.toml`'s conversation `label`
# (design/10 §1.3, §4.2) — shown to the operator, never matched on.
ACCOUNT_LABEL_KEY = "label"

# The fixed per-platform id-kind vocabulary (design/10 §3.1). A kind outside
# a platform's set here is a typo, and a typo must be a startup error, not a
# silently-ignored key — `nora.persons` enforces exactly that against these
# names when it parses `identity.toml`.
_ID_KINDS: MappingProxyType[str, frozenset[str]] = MappingProxyType(
    {
        "signal": frozenset({"phone", "uuid", "username"}),
        "telegram": frozenset({"user_id", "username"}),
        "discord": frozenset({"user_id", "username"}),
        "whatsapp": frozenset({"phone", "lid"}),
        # Life360 is a location provider, not a chat platform, but a person's
        # Life360 account is still "another account this person holds" — so it
        # binds the same way (design/19 §3, DEC-14): the circle-member UUID
        # Life360 returns is the ONE matchable id, and it resolves to the same
        # `person_id` as their Signal number. The presence collector joins a
        # live circle read to Nora's people through this, and identity.toml
        # stays the single home for "who someone is" across every platform.
        "life360": frozenset({"member_id"}),
    }
)


def id_kinds(platform: str) -> frozenset[str]:
    """The fixed, matchable id-kind vocabulary for `platform` (§3.1).

    Returns the empty set for a platform with no declared vocabulary, rather
    than raising — callers that want "unknown platform" to be an error (as
    `Identifier` does) check for that themselves.
    """
    return _ID_KINDS.get(platform, frozenset())


class UnmatchableIdentifierError(ValueError):
    """Raised by `Identifier` when asked to carry a kind that can never be
    matched on: `display_name` on any platform, or a kind outside the
    platform's fixed vocabulary (typically a typo)."""


@dataclass(frozen=True)
class Identifier:
    """One id form a person may present on one platform: `(platform, kind, value)`.

    `kind` must be a member of that platform's fixed vocabulary (§3.1).
    `display_name` is refused on every platform (§1.3) — supplying one is an
    error at construction time, not a value that is silently dropped later.
    """

    platform: str
    kind: str
    value: str

    def __post_init__(self) -> None:
        if self.kind == DISPLAY_NAME_KIND:
            raise UnmatchableIdentifierError(
                "display_name is never matchable (design/10 §1.3): Signal "
                "profile names, WhatsApp push names and Telegram display "
                "names are user-controlled and spoofable. They may be shown "
                "to the operator and rendered in a transcript, but never "
                "used to resolve a sender to a person."
            )
        allowed = _ID_KINDS.get(self.platform)
        if allowed is None:
            raise UnmatchableIdentifierError(
                f"unknown platform {self.platform!r} — no id-kind vocabulary "
                "is declared for it (design/10 §3.1)"
            )
        if self.kind not in allowed:
            raise UnmatchableIdentifierError(
                f"{self.kind!r} is not a valid id kind for platform "
                f"{self.platform!r}; expected one of {sorted(allowed)}"
            )


@dataclass(frozen=True)
class PlatformIdentity:
    """Nora's own identity on a platform — the same one-account-many-id-forms
    shape `nora.persons` already builds for everybody else (design/10 §5).

    Lives HERE, next to `Identifier`, rather than in `nora.domain.ports` where
    it was originally declared: once it carries typed identifiers it is part of
    the identity model, not a port-only vocabulary type, and ports.py was two
    lines from the 300-line hard cap. `nora.domain.ports` re-exports it, so
    `from nora.domain.ports import PlatformIdentity` keeps working everywhere.

    `identifiers` is the typed set — `(platform, kind, value)` triples, kind
    drawn from `id_kinds(platform)`. `aliases` is that same set FLATTENED to
    bare values, plus `primary`, and it is what R-12
    (`nora.engagement.stages.admission`) tests membership in. Two fields for
    one fact, deliberately, and this is the "one concept, one matching rule"
    design/10 §5 asks for: `nora.persons.load_persons` flattens every kind of
    every account into `index[(platform, value)] -> person_id` for exactly the
    same reason — a sender arrives as ONE opaque string and Nora does not get
    to know which kind it is. Nora's own identity is that same flattening with
    a single account, so the matching rule on both sides is "is this bare
    value in the flattened set", never "does this value match the phone field".

    Derivation is one-way and total: `aliases` is recomputed from
    `identifiers | {primary}` at construction, so no caller can hand in an
    `aliases` set that disagrees with `identifiers`. A caller may still pass
    `aliases` alone (a platform with no typed vocabulary yet, or a test), which
    is why it is a union rather than an overwrite.
    """

    platform: str
    primary: str
    aliases: frozenset[str] = field(default_factory=frozenset)
    identifiers: frozenset[Identifier] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        typed = frozenset(self.identifiers)
        object.__setattr__(self, "identifiers", typed)
        # primary is always an alias of itself, and every typed identifier's
        # bare value is matchable — see the class docstring.
        object.__setattr__(
            self,
            "aliases",
            frozenset(self.aliases) | {i.value for i in typed} | {self.primary},
        )


class Membership(Enum):
    """A person's normalised standing in a group conversation (design/10 §4.4).

    Exactly two values. `signal-cli` cannot distinguish a group's creator
    from any other admin, so a three-value owner/admin/member set would ship
    a value (`owner`) that silently never matches on Signal — the precise
    kind of platform divergence this design exists to eliminate. Owner,
    creator and superadmin all collapse into `ADMIN`.

    Telegram's `restricted`/`left`/`kicked` are absence, not a grade: such a
    sender is not a current member and their messages do not arrive as
    ordinary group traffic, so they map to no `Membership` value at all —
    there is no third member for them.

    DESCRIPTIVE ONLY. Policy never grants access on membership (§4.4) — this
    exists so the operator control plane can show e.g. "Family — 3 members,
    you are an admin", and so an adapter has one normalised word for what it
    observed. Do not wire this into any authorisation decision: checking
    admin status is a per-message platform round trip on Telegram and often
    on Signal (a real cost on a hot path that must not stall), and granting
    by membership would hand authorisation to whoever can promote a group
    admin — the policy file would stop being a complete description of who
    has access. Both are one-line changes to make later if that trade is
    ever worth it; neither is made here.
    """

    ADMIN = "admin"
    MEMBER = "member"

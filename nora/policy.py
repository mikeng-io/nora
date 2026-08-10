"""`policy.toml` → the grant predicate (design/10 §4, §7).

WHAT IS ALLOWED. Separate from `config.toml` (deploy cadence) and
`identity.toml` (changes when a human joins) because policy changes when TRUST
changes — the same argument DEC-8 used to split config from identity. An
allowlist buried among port numbers is an allowlist nobody reviews.

Everything here names people by `person_id`, never by a phone number or a
platform id. Adding an account to `identity.toml` therefore grants it access
everywhere that person is already allowed — one edit, not one per room — and
this file stays readable without knowing anyone's phone number. Note the
consequence: allowlisting `miles` grants EVERY account he holds (design/10 §3).

Deny by default, and an empty allowlist REFUSES TO START. Under R-5 Nora is
silent when broken, so a silently-denied Nora is indistinguishable from a
working one; a loud startup error is the only honest failure here. This
codebase's own `.env` states the principle: "You get a loud error instead of a
silent mute."
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

# Not a person_id: the wildcard admitting anyone in a conversation (§4.3).
EVERYONE = "*"


class PolicyError(Exception):
    """A policy problem that must stop the process at startup."""


@dataclass(frozen=True)
class Conversation:
    platform: str
    ids: frozenset[str]         # `id` plus `also_ids`, matched identically
    persons: frozenset[str] | None   # None == EVERYONE
    label: str = ""             # never matched on (§4.2)
    # The MCP servers this conversation may use (spec:
    # mcp-tool-provider-design §2.3). Empty means none — deny-by-default,
    # the same posture as the rest of this file (R-5).
    tools: frozenset[str] = frozenset()
    # LOCATION-DISCLOSURE TRUST (DEC-74). Default False: `where_is` refuses to
    # disclose a member's position unless this conversation is a verified SUBSET
    # of that member's Life360 circle (R-31/DisclosureGuard). Setting `location =
    # true` is the operator saying "I trust THIS room with my location."
    #
    # It is a WHOLE-ROOM switch, not a per-condition one (DEC-79): it relaxes the
    # SUBSET check AND the unnamed-account refusal, because real rooms hold silent
    # sealed-sender members with no id to map. It does NOT relax the degraded-view
    # refusal — a roster Nora could not read is an R-27 `unavailable`, and trust
    # can overrule what a roster says but not the absence of one.
    location: bool = False
    # The `id` the operator actually WROTE, kept alongside `ids` (which folds it
    # together with `also_ids` because matching must not care which form
    # arrived). Anything that SPEAKS INTO a conversation rather than matching one
    # needs this: `also_ids` exists for the id forms a platform hands us over
    # time — a Telegram supergroup's retired id, a re-encoded Signal group id —
    # and sending to a retired form fails. `ids` is a set, so "pick one" would be
    # arbitrary and would differ between runs. First user: the version
    # announcement (DEC-97), which speaks into every allowed room on the boot
    # after an upgrade. Defaulted so nothing that constructs a `Conversation`
    # without one breaks; `load_policy` always sets it.
    primary_id: str = ""


@dataclass(frozen=True)
class Policy:
    """The resolved allowlists. `is_allowed` is the whole public surface."""

    conversations: tuple[Conversation, ...]
    dm_persons: frozenset[str] = frozenset()
    _by_id: dict[tuple[str, str], Conversation] = field(repr=False, default_factory=dict)

    def conversation(self, platform: str, conversation_id: str) -> Conversation | None:
        return self._by_id.get((platform, conversation_id))

    def is_allowed(self, *, platform: str, conversation_id: str, person_id: str | None) -> bool:
        """§4.3's grant predicate, in full.

        Both gates must pass: the ROOM must be listed, and the SENDER must be
        allowed within it.

        `persons = "*"` admits an UNMAPPED sender too. The room is the trust
        boundary the operator drew, and D6 already settled that an unmapped
        sender is answered but never remembered (no `person_id`, so nothing is
        fed to Honcho). Refusing them here would both reverse that and make
        `"*"` almost pointless — it would then admit only people who are
        already mapped, which is what an explicit list is for.

        An explicit list still excludes them: `None` is not in `persons`, so
        an unrecognised sender in a `persons = ["miles"]` room is refused.
        """
        convo = self.conversation(platform, conversation_id)
        if convo is None:
            return False                      # room not listed → deny (§4.1)
        if convo.persons is None:
            return True                       # "*" — anyone in this room
        return person_id in convo.persons


def permissive_policy() -> Policy:
    """A Policy that allows every conversation and every sender.

    OFFLINE AND EVAL HARNESSES ONLY. `nora.app.offline` builds pipelines for
    `tests/integration` and `evals/`, which drive synthetic conversation ids
    that no real `policy.toml` would list; without this every harness message
    would be refused by the room gate and every test would exercise nothing.

    Production must never reach this — deny-by-default is the entire point of
    the file — and `tests/test_boundaries.py` asserts `nora/app/composition.py`
    does not reference it.
    """
    return _PermissivePolicy(conversations=())


@dataclass(frozen=True)
class _PermissivePolicy(Policy):
    def conversation(self, platform: str, conversation_id: str) -> Conversation:
        return Conversation(
            platform=platform, ids=frozenset({conversation_id}), persons=None,
            primary_id=conversation_id,
        )

    def is_allowed(self, *, platform: str, conversation_id: str, person_id: str | None) -> bool:
        return True


class _ConversationEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    platform: str
    id: str
    # Telegram CHANGES chat.id when a group is upgraded to a supergroup,
    # reporting the old id once via migrate_to_chat_id. Without this the
    # allowlist silently stops matching and Nora goes quiet — invisible under
    # R-5 (§4.2).
    also_ids: list[str] = Field(default_factory=list)
    label: str = ""
    persons: list[str] | str = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    # See `Conversation.location` (DEC-74/79): relaxes `where_is`'s disclosure
    # gate for this room. Default False — the disclosure guard stays strict.
    location: bool = False

    @field_validator("persons")
    @classmethod
    def _check_persons(cls, value: list[str] | str) -> list[str] | str:
        if isinstance(value, str) and value != EVERYONE:
            raise ValueError(
                f"persons must be a list of person_ids or the string "
                f'"{EVERYONE}"; got {value!r}'
            )
        return value


class _PolicyFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    default: str = "deny"
    dm: dict[str, list[str]] = Field(default_factory=dict)
    conversations: list[_ConversationEntry] = Field(default_factory=list)

    @field_validator("default")
    @classmethod
    def _only_deny(cls, value: str) -> str:
        if value != "deny":
            raise ValueError(
                'default must be "deny" — an allow-by-default Nora would join '
                "any group she is added to, and there is no second gate behind "
                "this one"
            )
        return value


def load_policy(path: Path) -> Policy:
    path = Path(path).expanduser()
    if not path.exists():
        raise PolicyError(
            f"policy file not found: {path}. Nora refuses to start without one "
            "rather than running mute — see policy.example.toml, and "
            "`nora groups` for the conversation ids to put in it."
        )
    with path.open("rb") as f:
        try:
            data = tomllib.load(f)
        except tomllib.TOMLDecodeError as exc:
            raise PolicyError(f"invalid TOML in {path}: {exc}") from exc
    try:
        parsed = _PolicyFile.model_validate(data)
    except ValidationError as exc:
        raise PolicyError(str(exc)) from exc

    conversations: list[Conversation] = []
    by_id: dict[tuple[str, str], Conversation] = {}
    for entry in parsed.conversations:
        ids = frozenset([entry.id, *entry.also_ids])
        persons = None if entry.persons == EVERYONE else frozenset(entry.persons)
        convo = Conversation(
            platform=entry.platform, ids=ids, persons=persons, label=entry.label,
            tools=frozenset(entry.tools), location=entry.location,
            primary_id=entry.id,
        )
        conversations.append(convo)
        for cid in ids:
            key = (entry.platform, cid)
            if key in by_id:
                raise PolicyError(
                    f"conversation id {cid!r} on {entry.platform} is listed "
                    "twice — the second entry would be unreachable, so one of "
                    "the two allowlists you think you wrote is doing nothing"
                )
            by_id[key] = convo

    if not conversations:
        raise PolicyError(
            f"{path} allows no conversations, so Nora would ingest and answer "
            "nothing. That is indistinguishable from a working Nora in a quiet "
            "group (R-5), so it is a startup error rather than a silent mute. "
            "Run `nora groups` for the ids, or delete the file to see this "
            "message again."
        )
    return Policy(
        conversations=tuple(conversations),
        dm_persons=frozenset(parsed.dm.get("persons", [])),
        _by_id=by_id,
    )

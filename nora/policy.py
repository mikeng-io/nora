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
        listed within it. An unmapped sender (`person_id is None`) is refused
        even in an allowlisted room with `persons = "*"` — the wildcard means
        "anyone in this chat", and Nora cannot tell that someone she cannot
        identify is in it. That is deliberate: the alternative grants a
        stranger the same access as a named friend.
        """
        convo = self.conversation(platform, conversation_id)
        if convo is None:
            return False                      # room not listed → deny (§4.1)
        if convo.persons is None:
            return person_id is not None      # "*" — anyone Nora can name
        return person_id in convo.persons


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
            platform=entry.platform, ids=ids, persons=persons, label=entry.label
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

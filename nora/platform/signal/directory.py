"""Read-only signal-cli directory reads: `listGroups` and `listContacts`.

Both go to the same JSON-RPC endpoint `nora.platform.signal.send` already uses
(`POST /api/v1/rpc`), and both are pure reads — this module holds the operator
control plane's half of the daemon conversation and nothing else. `_METHODS`
below is an allowlist, checked at the one call site, so a write method cannot
be added here by accident; `tests/test_signal_directory.py` asserts on it.

Field names are as the live daemon returns them, not taken from
documentation:

    listGroups   -> id, name, description, isMember, isBlocked, members[],
                    pendingMembers[], requestingMembers[], admins[], banned[],
                    permission*, messageExpirationTime, groupInviteLink
    listContacts -> number, uuid, username, name, givenName, familyName,
                    nickName, nickGivenName, nickFamilyName, note, color,
                    isArchived, isBlocked, isHidden, profileSharing,
                    unregistered, messageExpirationTime, profile{}

`id` on a group is the base64 groupId — the same string
`nora.platform.signal.parser` lifts out of `dataMessage.groupInfo.groupId` and
stores as `conversation_id`, which is what makes `nora groups` able to print a
policy verdict beside it.

`members[]` entries pair `number` WITH `uuid` for every member, and that turns
out to matter more than it looks: few contacts carry both forms, while
every group member does. Sealed sender means the same
account reaches Nora as a phone number on one message and as an ACI on
another, so the group roster is in practice the stronger correlator of the two
— `nora.app.pending` consumes both.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import aiohttp

# Reads only. `nora groups`/`nora persons pending` are read-only commands,
# and this list is what makes that a property of the module rather than a
# promise in a docstring.
_METHODS = frozenset({"listGroups", "listContacts"})


class DirectoryError(Exception):
    """The daemon could not be read. Carries an operator-readable reason —
    these commands must degrade with a sentence, never a traceback."""


@dataclass(frozen=True)
class SignalGroup:
    """One row of `listGroups`. `members` is one frozenset of id forms PER
    member, not one flat set: which ids belong to the same account is the whole
    point of reading it."""

    id: str
    name: str
    is_member: bool
    is_blocked: bool
    members: tuple[frozenset[str], ...] = ()


@dataclass(frozen=True)
class SignalContact:
    """One row of `listContacts`, reduced to what the control plane needs:
    the id forms that name one account, and the display name to print.

    `name` may be empty — some contacts have no name at all. An empty name is
    reported as such rather than back-filled from a number, because a
    fabricated name is worse than an absent one."""

    ids: frozenset[str]
    name: str = ""


def _ids_of(entry: dict[str, Any]) -> frozenset[str]:
    """Every non-empty id form on one contact or group-member record."""
    return frozenset(
        str(entry[key])
        for key in ("number", "uuid", "username")
        if entry.get(key)
    )


def _display_name(entry: dict[str, Any]) -> str:
    """The name signal-cli knows for a contact, in the order a human means it.

    A nickname the operator set himself outranks the profile name the contact
    chose: it is the string he will recognise, and it is the one Signal shows
    him. `username` is last and is a real id, not a display name — it is only
    reached when there is nothing else, and `nora.app.pending` still reports
    the account as unknown when this comes back empty.
    """
    for key in ("nickName", "name"):
        if entry.get(key):
            return str(entry[key])
    for first, last in (("nickGivenName", "nickFamilyName"), ("givenName", "familyName")):
        parts = [str(entry[k]) for k in (first, last) if entry.get(k)]
        if parts:
            return " ".join(parts)
    profile = entry.get("profile")
    if isinstance(profile, dict):
        parts = [str(profile[k]) for k in ("givenName", "familyName") if profile.get(k)]
        if parts:
            return " ".join(parts)
    return str(entry.get("username") or "")


async def _rpc(
    *,
    base_url: str,
    method: str,
    account: str,
    timeout: float,  # noqa: ASYNC109 -- caller budget, matching send.py
) -> list[dict[str, Any]]:
    if method not in _METHODS:
        raise DirectoryError(
            f"{method!r} is not a read-only directory method; this module may "
            f"only call {sorted(_METHODS)}"
        )
    url = f"{base_url.rstrip('/')}/api/v1/rpc"
    payload = {
        "jsonrpc": "2.0",
        "method": method,
        "params": {"account": account},
        "id": f"nora-{method}",
    }
    try:
        async with (
            aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session,
            session.post(url, json=payload) as resp,
        ):
            if resp.status != 200:
                raise DirectoryError(f"{method} failed: HTTP {resp.status} from {url}")
            body = await resp.json()
    except DirectoryError:
        raise
    except Exception as exc:  # every transport failure becomes one message
        raise DirectoryError(
            f"could not reach the signal-cli daemon at {url}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc

    if isinstance(body, dict) and body.get("error"):
        raise DirectoryError(f"{method} rejected by signal-cli: {body['error']}")
    result = body.get("result") if isinstance(body, dict) else None
    if not isinstance(result, list):
        raise DirectoryError(f"{method} returned no result list (got {type(result).__name__})")
    return [r for r in result if isinstance(r, dict)]


async def list_groups(
    *,
    base_url: str,
    account: str,
    timeout: float = 10.0,  # noqa: ASYNC109 -- caller budget, matching send.py
) -> list[SignalGroup]:
    """Every group the daemon knows, member or not. Never writes."""
    return [
        SignalGroup(
            id=str(g.get("id") or ""),
            name=str(g.get("name") or ""),
            is_member=bool(g.get("isMember")),
            is_blocked=bool(g.get("isBlocked")),
            members=tuple(
                ids
                for m in (g.get("members") or [])
                if isinstance(m, dict) and (ids := _ids_of(m))
            ),
        )
        for g in await _rpc(base_url=base_url, method="listGroups",
                            account=account, timeout=timeout)
        if g.get("id")
    ]


async def list_contacts(
    *,
    base_url: str,
    account: str,
    timeout: float = 10.0,  # noqa: ASYNC109 -- caller budget, matching send.py
) -> list[SignalContact]:
    """Every contact the daemon knows. Never writes.

    Contacts with no id form at all are dropped: they can neither be matched
    against an audited sender nor pasted into identity.toml.
    """
    return [
        SignalContact(ids=ids, name=_display_name(c))
        for c in await _rpc(base_url=base_url, method="listContacts",
                            account=account, timeout=timeout)
        if (ids := _ids_of(c))
    ]

"""Rendering for the operator control plane — text only, no I/O.

Both reports are built as `list[str]` so a test can assert on the lines
without capturing stdout, and so `nora.app.cli` keeps exactly one
responsibility: gather, then print. Nothing here reaches a file, a socket or
the database.

The bias throughout is toward PASTE-READY output. The operator's question is
never "what is the state of the world", it is "what do I type next" — so a
denied group prints the `[[conversations]]` block that would allow it, and a
pending account prints the `[[persons.<id>.<platform>]]` block that would map
it. Both are emitted in the current schema (design/10 §3, §4.2) and both are
suggestions the operator hand-edits into a file Nora never writes (D6).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from nora.app.pending import PendingAccount, id_kind
from nora.domain.identity import id_kinds
from nora.platform.signal.directory import SignalGroup
from nora.policy import Policy

_SLUG_STRIP_RE = re.compile(r"[^a-zA-Z0-9_-]+")

_PASTE_HINT = (
    "# Paste into identity.toml, EDIT THE person id AND name, then restart Nora.\n"
    "# If an account below is a person already in identity.toml, move only the\n"
    "# [[...]] block under that person and give every one of their blocks on\n"
    "# that platform a distinct `label` — one human is one person_id and one\n"
    "# Honcho peer card, and a second [persons.…] entry would split their memory.\n"
    "# On restart, lazy adoption backfills everything already audited (DEC-16)."
)


def _when(ts: int) -> str:
    """`ts` is signal-cli's millisecond epoch, as audited."""
    if ts <= 0:
        return "unknown"
    return datetime.fromtimestamp(ts / 1000, tz=UTC).strftime("%Y-%m-%d %H:%M UTC")


def slug(name: str, taken: set[str]) -> str:
    """A person_id candidate matching `^[a-zA-Z0-9_-]+$` — Honcho's peer-id
    rule, which `nora.persons` enforces. Deliberately plain for an unknown
    account: `person-2` is meant to be edited, and looks it."""
    base = _SLUG_STRIP_RE.sub("-", name).strip("-").lower() or "person"
    candidate, n = base, 1
    while candidate in taken:
        n += 1
        candidate = f"{base}-{n}"
    taken.add(candidate)
    return candidate


def stanza(account: PendingAccount, *, person_id: str) -> list[str]:
    """The paste-ready `identity.toml` block for one account, as lines.

    ONE `[[persons.<id>.<platform>]]` array-of-tables block, which is the
    current schema (design/10 §3) — and exactly one, so no `label` is needed:
    that key is only required once a person holds two or more accounts on one
    platform. `_PASTE_HINT` carries the warning for the case where this account
    belongs to somebody already in the file, which is when the label becomes
    mandatory.
    """
    lines = [f"[persons.{person_id}]"]
    if account.known:
        lines.append(f'name = "{account.name}"')
    lines.append("")
    lines.append(f"[[persons.{person_id}.{account.platform}]]")
    unclassified: list[str] = []
    for value in account.ids:
        kind = id_kind(account.platform, value)
        if kind is None:
            unclassified.append(value)
        else:
            lines.append(f'{kind} = "{value}"')
    lines += [
        f"# {value} — no id-kind classifier for platform {account.platform!r}; "
        f"pick one of {sorted(id_kinds(account.platform))} by hand (design/10 §3.1)"
        for value in unclassified
    ]
    return lines


def render_groups(
    *, groups: list[SignalGroup], policy: Policy, policy_path: str, base_url: str
) -> list[str]:
    """Every group signal-cli knows, with the policy verdict for each."""
    lines = [
        f"signal-cli groups from {base_url}",
        f"policy: {policy_path}",
        "",
    ]
    allowed = 0
    for group in sorted(groups, key=lambda g: (g.name.lower(), g.id)):
        convo = policy.conversation("signal", group.id)
        verdict = "ALLOWED" if convo is not None else "DENIED "
        allowed += convo is not None
        lines.append(f"{verdict}  {group.name or '(no name)'}")
        lines.append(f"         id = {group.id}")
        flags = [f"member={'yes' if group.is_member else 'NO'}"]
        if group.is_blocked:
            flags.append("blocked=YES")
        if group.members:
            flags.append(f"members={len(group.members)}")
        lines.append(f"         {'  '.join(flags)}")
        if convo is not None:
            who = "anyone in the room (persons = \"*\")" if convo.persons is None \
                else f"persons = {sorted(convo.persons)}"
            label = f' labelled "{convo.label}",' if convo.label else ""
            lines.append(f"         policy:{label} {who}")
        else:
            lines.append("         policy: not listed — Nora ignores this group.")
            lines += [f"           {line}" for line in _allow_stanza(group)]
        lines.append("")
    lines.append(
        f"{len(groups)} group(s): {allowed} allowed, {len(groups) - allowed} denied."
    )
    if not groups:
        lines.append(
            "The daemon reports no groups at all. If you expect some, a linked "
            "device's group state can go stale — `sendSyncRequest` over "
            "/api/v1/rpc asks the primary to re-send it."
        )
    return lines


def _allow_stanza(group: SignalGroup) -> list[str]:
    label = (group.name or "").replace('"', "'")
    return [
        "to allow it, paste into policy.toml and restart:",
        "[[conversations]]",
        'platform = "signal"',
        f'id       = "{group.id}"',
        f'label    = "{label}"',
        'persons  = ["CHANGEME"]   # or "*" for anyone in this room',
    ]


def render_pending(
    *,
    accounts: list[PendingAccount],
    identity_path: str,
    db_path: str,
    scanned: int,
    truncated: bool,
    daemon_note: str = "",
    daemon_ok: bool = True,
) -> list[str]:
    """Unmapped accounts, newest first, each with its paste-ready stanza.

    EVERY prose line is a TOML comment, which is not decoration. The first
    draft interleaved plain-text headings with the stanzas, and the result was
    output where no contiguous selection is valid TOML: the operator has to
    pick out the stanza lines by eye, from a report whose entire purpose is to
    save him from transcribing ids by eye. As comments, the whole report from
    the first `[persons.` onward parses — which is exactly what
    `tests/test_cli_control_plane.py` asserts by feeding a slice of it to
    `load_persons`.
    """
    lines = [
        f"# unmapped senders in {db_path}",
        f"# identity: {identity_path}",
    ]
    lines += [f"# {line}" for line in daemon_note.splitlines()]
    lines.append("#")
    if not accounts:
        lines.append(
            f"# No unmapped senders. Every one of the {scanned} unattributed "
            "audit row(s) scanned already resolves to a person in "
            "identity.toml — they are lazy adoption's to backfill on the next "
            "restart (DEC-16)."
            if scanned
            else "# No unattributed audit rows at all: every message Nora has "
                 "stored is already attributed to a person."
        )
        return lines

    taken: set[str] = set()
    for account in accounts:
        person_id = slug(account.name if account.known else "person", taken)
        lines.append(
            f"# ── {account.name}  ({account.platform}) — {account.messages} "
            f"message(s), {_when(account.first_ts)} → {_when(account.last_ts)}"
        )
        if not account.known:
            # Two different facts, and conflating them was the first version's
            # bug: "the daemon does not know this account" is a lookup that
            # RAN and came back empty, while "the daemon was unreachable" is a
            # lookup that never happened. Printing the former for the latter
            # asserts something unverified — the exact shape of dishonesty
            # this command exists to remove.
            lines.append(
                "# not in signal-cli's contacts — the name above is honestly "
                "unknown, not withheld."
                if daemon_ok
                else "# signal-cli was unreachable, so no name could be looked "
                     "up at all — this is not evidence the daemon does not know them."
            )
        if account.profile_names:
            lines.append(
                "# profile name(s) this sender set: "
                + ", ".join(repr(n) for n in account.profile_names)
                + " — sender-controlled and spoofable, never matched on (§1.3)."
            )
        lines += stanza(account, person_id=person_id)
        lines.append("")

    lines.append(_PASTE_HINT)
    lines.append("#")
    lines.append(
        f"# {len(accounts)} unmapped account(s) from {scanned} unattributed row(s)."
    )
    if truncated:
        lines.append(
            f"# The scan stopped at its {scanned}-row limit, so an account whose "
            "messages are all older than that is NOT listed. Raise --limit to "
            "see further back."
        )
    return lines

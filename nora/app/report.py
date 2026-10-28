"""Rendering for the operator control plane — text only, no I/O.

Both reports are built as `list[str]` so a test can assert on the lines
without capturing stdout, and so `nora.app.cli` keeps exactly one
responsibility: gather, then print. Nothing here reaches a file, a socket or
the database.

The bias throughout is toward PASTE-READY output. The operator's question is
never "what is the state of the world", it is "what do I type next" — so a
denied group prints the `[[conversations]]` block that would allow it, and a
pending account prints the `[[persons.<id>.<platform>]]` block that would map
it. Both are emitted in the current schema and both are suggestions the operator
hand-edits into a file Nora never writes.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta, timezone

from nora.app.pending import PendingAccount, id_kind
from nora.domain.identity import id_kinds
from nora.domain.reminders import Reminder
from nora.infra.life360 import DiscoveredCircle
from nora.platform.discord.directory import DiscordChannel, DiscordGuild
from nora.platform.signal.directory import SignalGroup
from nora.policy import Conversation, Policy, _fold

# The zone the operator reads in, and the one Nora's own prefill states
# (`nora.agent.tools._HKT`). A UTC stamp in a 3am terminal is one silent
# subtraction away from an eight-hour mistake about when something will fire.
_HKT = timezone(timedelta(hours=8), "HKT")

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
    current schema — and exactly one, so no `label` is needed:
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


def render_discord_guilds(
    *,
    guilds: list[DiscordGuild],
    channels: Mapping[str, list[DiscordChannel]],
    policy: Policy,
    error: str = "",
) -> list[str]:
    """Every guild the bot is in, and — for guilds a server entry covers —
    the channels the entry currently expands into, each with its mode.

    The mode answers the operator's only question here: `ambient`,
    `mention-only`, `ignored`, `listed` (an explicit per-channel entry wins
    over the server's), or `not admitted`. The same `Policy.conversation`
    the room gate runs decides it, so the report and admission cannot drift
    — `policy` must be loaded with the channel directory these REST reads
    filled (`StaticChannelDirectory`), which is exactly what `cmd_groups`
    does before calling this.
    """
    entries = {
        convo.guild: convo for convo in policy.conversations if convo.guild
    }
    lines: list[str] = ["", "discord guilds from the bot account", ""]
    if error:
        lines.append(f"WARNING: discord could not be read ({error}).")
        lines.append("")
    covered = 0
    for guild in sorted(guilds, key=lambda g: (g.name.lower(), g.id)):
        entry = entries.get(guild.id)
        verdict = "COVERED" if entry is not None else "not listed"
        covered += entry is not None
        label = f' "{entry.label}"' if entry and entry.label else ""
        lines.append(f"{verdict}  {guild.name or '(no name)'}  guild={guild.id}{label}")
        if entry is not None:
            for ch in sorted(
                channels.get(guild.id) or [], key=lambda c: (c.name.lower(), c.id)
            ):
                lines.append(f"         {_channel_mode(policy, entry, ch)}")
        lines.append("")
    listed = sorted(set(entries) - {g.id for g in guilds})
    for guild_id in listed:
        lines.append(f"COVERED  (not a guild this bot is in)  guild={guild_id}")
    lines.append(
        f"{len(guilds)} guild(s): {covered} covered by a server entry."
    )
    return lines


def _channel_mode(policy: Policy, entry: Conversation, ch: DiscordChannel) -> str:
    """`mode  #name  id` — the same verdict `conversation()` gives the gate."""
    folded = _fold(ch.name)
    if folded in {_fold(n) for n in entry.ignore}:
        mode = "ignored"
    else:
        room = policy.conversation("discord", f"{entry.guild}/{ch.id}")
        if room is None:
            mode = "not admitted (collision or unknown)"
        elif not room.discovered:
            mode = "listed"
        elif room.relevance_gate:
            mode = "ambient"
        else:
            mode = "mention-only"
    return f"{mode:<28} #{ch.name}  {ch.id}"


def render_reminders(
    *,
    reminders: Sequence[Reminder],
    now: datetime,
    source: str,
    truncated: bool,
) -> list[str]:
    """Every reminder still waiting, soonest first — the operator's read.

    **No paste-ready stanza, unlike the other two reports, and that is the
    point.** `nora groups` and `nora persons pending` end in a block the
    operator edits into a file; there is nothing equivalent here, because the
    only actions on a reminder are model-side (`cancel_reminder`) or
    deployment-side (`[heartbeat] enabled = false` plus a restart, which stops
    all firing rather than one row). The control plane is read-only by
    construction — `create_readonly_sql_engine` plus a `SELECT`-only grant — so
    a `nora reminders cancel` would break both the module's invariant and the
    database role, and it is not offered.

    `overdue` is the diagnostic this report exists for: a row whose `due_at` has
    passed and which is STILL pending means the tick is not running (or is not
    reaching that conversation). It is the same question
    `nora_heartbeat_last_tick_age_seconds` answers from the metrics side, asked
    of the data instead.
    """
    lines = [
        f"reminders from {source}",
        f"now: {_local(now)}",
        "",
    ]
    overdue = 0
    for row in reminders:
        late = row.due_at <= now
        overdue += late
        lines.append(f"{'OVERDUE' if late else 'waiting'}  {_local(row.due_at)}")
        lines.append(f"         id   = {row.id}")
        lines.append(f"         who  = {row.requester_person_id}")
        lines.append(f"         where= {row.platform}:{row.conversation_id}")
        lines.append(f"         text = {row.text}")
        # (DEC-85) A recurring series shows as ONE pending row — the next
        # occurrence — so without this line an operator counting rows would
        # read a fourteen-day countdown as a single reminder, and «1 pending»
        # would be true and misleading at once. `occurrence` is 0-based in the
        # row and 1-based here: the operator is being told which one this is.
        if row.series is not None:
            rule = row.series.rule
            ends = (
                "" if rule.until is None else f", until {_local(rule.until)}"
            )
            lines.append(
                f"         every= {rule.every.value} "
                f"({row.series.occurrence + 1} of {rule.count}{ends})"
            )
            lines.append(f"         series={row.series.id}")
        lines.append("")
    lines.append(f"{len(reminders)} pending, {overdue} overdue.")
    if overdue:
        lines.append(
            "An overdue row means the tick is not firing it. Check "
            "`[heartbeat] enabled` in config.toml, then "
            "nora_heartbeat_last_tick_age_seconds on /metrics — a climbing age "
            "is a loop that has stopped."
        )
    if truncated:
        lines.append("Listing was truncated — re-run with a larger --limit.")
    if not reminders:
        # True row-by-row (DEC-85): a RECURRING reminder is a chain of rows,
        # each of which resolves once and is never re-run — the next
        # occurrence is a NEW row, minted when this one is claimed. Nothing
        # here re-runs anything.
        lines.append("Nothing is waiting. Reminders resolve once and are never re-run.")
    return lines


def _local(when: datetime) -> str:
    """One instant, in the operator's own zone, with the offset stated."""
    return when.astimezone(_HKT).strftime("%a %-d %b %Y %H:%M %Z")


def render_presence_members(circles: list[DiscoveredCircle]) -> list[str]:
    """Every Life360 circle and member (id + name) this account can see, in a
    paste-ready shape: a `[[persons.<id>.life360]]` block per member carrying BOTH
    `member_id` and `circle_id`, for identity.toml. All the ids live there;
    config.toml needs none — just `[presence] enabled = true`."""
    lines: list[str] = ["Life360 circles for this account:", ""]
    for circle in circles:
        lines.append(f"Circle: {circle.name or '(no name)'}  (circle_id {circle.circle_id})")
        lines.append("  identity.toml — add to each matching person (edit CHANGEME):")
        for member in circle.members:
            lines.append(f"    # {member.name}")
            lines.append("    [[persons.CHANGEME.life360]]")
            lines.append(f'    member_id = "{member.member_id}"')
            lines.append(f'    circle_id = "{circle.circle_id}"')
        lines.append("")
    if circles:
        lines.append("config.toml carries no ids — set [presence] enabled = true to turn it on.")
    else:
        lines.append(
            "No circles returned. If you expect some, check the account is the one "
            "sharing location, and that LIFE360_CLIENT_CREDENTIAL is the app's."
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
    source: str,
    scanned: int,
    truncated: bool,
    daemon_note: str = "",
    daemon_ok: bool = True,
) -> list[str]:
    """Unmapped accounts, newest first, each with its paste-ready stanza.

    EVERY prose line is a TOML comment, which is not decoration. Plain-text
    headings interleaved with the stanzas would leave no contiguous selection
    that is valid TOML, forcing the operator to pick out stanza lines by eye.
    As comments, the whole report from the first `[persons.` onward parses — which is exactly what
    `tests/test_cli_control_plane.py` asserts by feeding a slice of it to
    `load_persons`.
    """
    lines = [
        f"# unmapped senders in {source}",
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
            # Two different facts, not to be conflated: "the daemon does not
            # know this account" is a lookup that RAN and came back empty,
            # while "the daemon was unreachable" is a lookup that never
            # happened. Printing the former for the latter
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


def render_measures(
    measures: Sequence[object],
    *,
    source: str,
    window: str,
    refusals: int,
    refusal_source: str,
) -> list[str]:
    """`nora measure participation` — the four measures, each above its own
    ceiling.

    **The ceiling is printed under every measure, not once at the bottom**, and
    that is the whole formatting decision here. These four differ in what they
    are blind to — one cannot see lookupable facts, one is a lower bound by
    construction, one over-reports, one is missing the arm that matters — so a
    footnote shared between them would be read as applying to none. A number
    that travels without its ceiling gets read as a rate.

    `refusals` and its source are in the header rather than beside the two
    measures that use them, because zero refusals is not the same reading as
    zero hits and an operator must see which one they are looking at before
    they read a single row.
    """
    lines = [
        f"participation measures — {window}",
        f"  rows: {source}",
        f"  refusals: {refusals} ({refusal_source})",
        "",
    ]
    for m in measures:
        name = getattr(m, "name", "?")
        hits = getattr(m, "hits", ())
        considered = getattr(m, "considered", 0)
        rate = getattr(m, "rate", None)
        # "n/a" and not "0.0%": nothing considered means the measure did not
        # run, and printing a zero there is the absent-series-read-as-zero
        # error.
        shown = "n/a (nothing considered)" if rate is None else f"{rate:.1%}"
        lines.append(f"{name}: {len(hits)} / {considered} = {shown}")
        for o in hits[:10]:
            lines.append(
                f"    {_when(o.at)}  {o.conversation_id}  {o.subject}"
            )
        if len(hits) > 10:
            lines.append(f"    ... {len(hits) - 10} more")
        lines.append(f"    ceiling: {getattr(m, 'ceiling', '')}")
        lines.append("")
    return lines

"""The judge's facts for a `recall_grounded` case: the records the case names,
rendered from its sandbox at the instant it was asked.

A recall reply is graded on whether what it claims to remember is SUPPORTED
(`evals.rebuilt.rubric.RECALL_GROUNDED`), and the window she was shown is not
the support: a photo's stored reading, a note's revisions, an outing the Rollup
recorded. A case names them by typed id under `metadata.evidence`
(`EVIDENCE_KEYS`), with no name, place or body in the corpus; this renders them
for the judge from `nora.subject.evidence.evidence_at`, which reads the sandbox
as it stood at the instant and refuses an id it did not hold.

The rendering names people the way the prompt does (the roster's name, her own
rows under her display name, a stranger by the self-set profile name), states
every time in the deployment's own timezone with its age against the instant,
and says what each kind of record is, because a block handed to a model is
evidence only when the model can tell what it is (AGENTS.md, «The spine»).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from nora.domain.external_files import render_file_links
from nora.subject.evidence import Evidence, HeldOuting, evidence_at

#: The criterion this module serves; a case naming it must state `evidence`.
CRITERION = "recall_grounded"

#: What `metadata.evidence` may state, and what each value must look like.
#: Closed: a key the builder does not render is a record nobody graded against.
EVIDENCE_KEYS: dict[str, str] = {
    "messages": "a list of `messages.id` integers",
    "media": "a list of 64-hex `media_objects.sha256` digests",
    "notes": "a list of `note.id` prefixes, 8 lowercase hex",
    "events": "a list of `events.id` prefixes, 8 lowercase hex",
    "rollup_outings": "a list of `rollup_outing.id` integers",
    "rollup_window_days": "an integer from 1 to 90: every outing in the days before the instant",
}

_SHA = re.compile(r"^[0-9a-f]{64}$")
#: A note or event is named by the first 8 hex of its uuid, never the whole
#: uuid: a full uuid is the shape of a Signal account id, which the repo's PII
#: hook refuses in a tracked file. `evidence_at` resolves the prefix against
#: what the sandbox held at the instant and refuses an ambiguous one.
_ID_PREFIX = re.compile(r"^[0-9a-f]{8}$")
#: How many hex characters of a digest she is shown as a handle
#: (`nora.domain.media_join.HANDLE_CHARS`).
_HANDLE = 6


def evidence_problems(metadata: Mapping[str, Any]) -> list[str]:
    """Why a case's `metadata.evidence` cannot be graded, or `[]`.

    Checked at LOAD (`evals.scenarios`), before any model call: a case opting
    into `recall_grounded` must name its evidence, a case that does not may
    not, and every value has its key's type."""
    criteria = tuple(metadata.get("criteria") or ())
    evidence = metadata.get("evidence")
    if CRITERION not in criteria:
        return ["`metadata.evidence` is read only by `recall_grounded`"] if evidence else []
    if not isinstance(evidence, Mapping) or not evidence:
        return ["`recall_grounded` needs `metadata.evidence`, the records it is graded on"]
    problems = [
        f"`evidence.{k}` is not a key; known: {sorted(EVIDENCE_KEYS)}"
        for k in evidence if k not in EVIDENCE_KEYS
    ]
    checks = {
        "messages": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "rollup_outings": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "media": lambda v: isinstance(v, str) and bool(_SHA.match(v)),
        "notes": lambda v: isinstance(v, str) and bool(_ID_PREFIX.match(v)),
        "events": lambda v: isinstance(v, str) and bool(_ID_PREFIX.match(v)),
    }
    for key, ok in checks.items():
        if key not in evidence:
            continue
        values = evidence[key]
        if not isinstance(values, list) or not values or not all(ok(v) for v in values):
            problems.append(f"`evidence.{key}` must be {EVIDENCE_KEYS[key]}")
    if "rollup_window_days" in evidence:
        days = evidence["rollup_window_days"]
        if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 90:
            problems.append(
                f"`evidence.rollup_window_days` must be {EVIDENCE_KEYS['rollup_window_days']}"
            )
    return problems


async def recall_facts(subject: Any, metadata: Mapping[str, Any], *, now_ms: int) -> str | None:
    """The facts section for a `recall_grounded` case, or `None` for every
    other case. Raises when a named record cannot be read: the draw is then an
    ERROR, never a verdict graded against half its evidence."""
    if CRITERION not in tuple(metadata.get("criteria") or ()):
        return None
    problems = evidence_problems(metadata)
    if problems:
        raise ValueError("; ".join(problems))
    evidence = dict(metadata["evidence"])
    at = datetime.fromtimestamp(now_ms / 1000, tz=UTC)
    held = await evidence_at(
        subject, at=at,
        messages=evidence.get("messages") or (),
        media=evidence.get("media") or (),
        notes=evidence.get("notes") or (),
        events=evidence.get("events") or (),
        outings=evidence.get("rollup_outings") or (),
        window_days=evidence.get("rollup_window_days"),
    )
    return render(
        held,
        names={pid: ref.name for pid, ref in subject.deps.persons.by_person.items()},
        display_name=str(subject.cfg.doctrine.display_name),
        tz=ZoneInfo(str(subject.cfg.analytics.tz_name)),
    )


def render(held: Evidence, *, names: Mapping[str, str], display_name: str, tz: ZoneInfo) -> str:
    """`held` as the judge reads it. Pure, so its wording is testable without
    a database."""
    now_ms = int(held.at.timestamp() * 1000)

    def when(ms: int) -> str:
        local = datetime.fromtimestamp(ms / 1000, tz=tz)
        return f"{local:%Y-%m-%d %H:%M} ({local:%a}), {_age(now_ms - ms)}"

    def person(pid: str) -> str:
        return names.get(pid, "someone outside this group's roster")

    moment = datetime.fromtimestamp(now_ms / 1000, tz=tz)
    lines = [
        "The records this case is about, read from Nora's own restored database as they "
        f"stood at the moment she was asked ({moment:%Y-%m-%d %H:%M} ({moment:%a}), "
        f"{tz.key}). They "
        "are what a correct recall rests on. She may have been shown some of them (in the "
        "conversation, in her context, or in what a tool returned) and not others: the "
        "source data says what she was shown and what her tools returned. A recalled claim "
        "these records support is grounded, not invented; one they contradict, or a "
        "specific they do not give, is not.",
    ]
    if held.messages:
        lines += ["", "Messages in this group:"]
        for m in held.messages:
            speaker = (
                display_name if m.is_nora
                else names.get(m.person_id or "") or m.sender_name or "unknown"
            )
            media = "".join(
                f" [{kind.split('/')[0] or 'media'} {sha[:_HANDLE]}]" for sha, kind in m.media
            )
            # A shared file's link reaches the judge as its handle, as it
            # reaches her (R-18): the judge is a model too.
            line = (
                f"- message {m.id}, {when(m.ts)}, from {speaker}:{media} "
                f"{render_file_links(m.body)}"
            )
            lines.append(line.rstrip())
    if held.media:
        lines += [
            "", "Pictures and clips: the stored reading Nora's ingest made of each when it "
            "arrived. It is everything the record holds about what it shows; no time it was "
            "taken is recorded (the time it was sent is its message's).",
        ]
        for o in held.media:
            kind = o.media_type.split("/")[0] or "media"
            head = f"- [{kind} {o.sha256[:_HANDLE]}] ({o.media_type})"
            if o.withheld:
                lines.append(f"{head}: its reading holds an identifier and is withheld here.")
                continue
            parts = [p for p in (o.title, o.description) if p]
            if o.content and o.content != o.description:
                parts.append(f"legible text: {o.content}")
            lines.append(f"{head}: " + (" | ".join(parts) or "no reading was stored."))
    if held.notes:
        lines += [
            "", "Notes from her notebook, each with its revisions oldest first; the one marked "
            "current is what the note said at the moment, and an earlier one is superseded.",
        ]
        for n in held.notes:
            dated = _date(n.anchor_date)
            state = "retired" if n.retired else "open"
            lines.append(f"- note «{n.title}»{f', dated {dated}' if dated else ''}, {state}:")
            for r in n.revisions:
                mark = "current" if r.current else "superseded"
                lines.append(
                    f"  - ({mark}) written {when(r.written_at)}: {render_file_links(r.body)}"
                )
    if held.events:
        lines += ["", "Events in her calendar for this group:"]
        for e in held.events:
            people = ", ".join(person(p) for p in e.people) or "nobody named"
            lines.append(
                f"- event «{e.title}», {e.status or 'no status'}, when {e.when or 'not set'}"
                f"{f' to {e.end}' if e.end else ''}, at {e.venue or 'no venue set'}, with {people}"
            )
    if held.outings:
        lines += [
            "", "Outings the Rollup worked out from the members' shared locations "
            "(who was out together, where and when):",
        ]
        lines += [_outing(o, when, person) for o in held.outings]
    if held.window_days is not None:
        lines += [
            "", f"Every outing the Rollup recorded for this group in the {held.window_days} "
            "days before the moment:",
        ]
        lines += [_outing(o, when, person) for o in held.window_outings] or [
            "- none: no outing was recorded in that window.",
        ]
    return "\n".join(lines)


def _outing(o: HeldOuting, when: Any, person: Any) -> str:
    together = ", ".join(person(p) for p in o.persons) or "nobody named"
    return (
        f"- outing {o.id}: from {when(o.valid_from_ms)} to {when(o.valid_to_ms)}, "
        f"{together}, at {o.place_label or 'a place with no label'}"
    )


def _date(anchor: int | None) -> str:
    if not anchor:
        return ""
    text = str(anchor)
    return f"{text[:4]}-{text[4:6]}-{text[6:]}" if len(text) == 8 else text


def _age(ms: int) -> str:
    """How long before the moment, in the largest two units."""
    if ms < 0:
        return "after the moment"
    minutes = ms // 60_000
    days, rem = divmod(minutes, 1440)
    hours, mins = divmod(rem, 60)
    if days:
        return f"{days} day{'s' if days != 1 else ''} {hours} h before she was asked"
    if hours:
        return f"{hours} h {mins} min before she was asked"
    return f"{mins} min before she was asked"

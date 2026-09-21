"""A SOURCE database that stands in for the deployment's clone, and the
`Spec` that drives a case against it.

The eval layer has ONE path: every case names a real room at a real instant,
`nora.subject.sandbox_from_snapshot` reads that moment out of the source and
writes it into a sandbox database of the case's own. A test therefore cannot
hand a case a transcript — it seeds a ROOM into a source, the way the
deployment's history seeds the clone, and the case names the moment.

`know()` is the seeding primitive for that: a throwaway subject composed
against the source writes the room's rows through the production stores at
explicit timestamps, then goes away. Nothing here is a second seeding path
for the runner — the runner never sees a `Knowledge`; it sees a source with
rows in it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from nora.subject import Knowledge, Message, SubjectSpec, know, obtain
from nora.subject.obtain import _DEFAULT_IDENTITY, default_policy

#: One room, the shape `SubjectSpec`'s defaults give a test.
ROOM = ("signal", "g1")
#: The instant every seeded room stands at: the newest row's own `ts`.
BEFORE_MS = int(datetime(2026, 9, 1, 12, 0, tzinfo=UTC).timestamp() * 1000)
STEP_MS = 60_000


@dataclass(frozen=True)
class Source:
    """What a test needs to drive a case against a seeded source."""

    url: str
    room: tuple[str, str]
    before_ms: int
    policy: str
    identity: str
    template: str

    @property
    def snapshot(self) -> dict[str, object]:
        """The `snapshot:` block a case states to name this moment."""
        return {
            "platform": self.room[0],
            "conversation_id": self.room[1],
            "before_ms": self.before_ms,
        }


def dated(
    transcript: tuple[Message | str, ...] | list, *, before_ms: int = BEFORE_MS,
) -> tuple[Message, ...]:
    """Rows stamped one minute apart, the NEWEST at `before_ms` — so a case
    anchored at `before_ms` sees the whole window and nothing after it."""
    rows = [Message(body=m) if isinstance(m, str) else m for m in transcript]
    base = before_ms - (len(rows) - 1) * STEP_MS
    return tuple(
        Message(
            body=m.body, name=m.name, person=m.person, label=m.label,
            at_ms=base + i * STEP_MS, is_nora=m.is_nora, media=m.media,
        )
        for i, m in enumerate(rows)
    )


async def seed_source(
    url: str,
    workdir: Path,
    knowledge: Knowledge,
    *,
    template: str,
    room: tuple[str, str] = ROOM,
    before_ms: int = BEFORE_MS,
    policy: str = "",
    identity: str = _DEFAULT_IDENTITY,
) -> Source:
    """Write `knowledge` into `url` as the history of `room`, ending at
    `before_ms`, and return the `Source` a case drives against.

    Rows without an `at_ms` are dated by `dated()`; `standing`'s age and the
    notes are written relative to `before_ms` exactly as `know()` does.
    """
    from evals.backbone import _close

    policy = policy or default_policy(room)
    subject = await obtain(SubjectSpec(
        database_url=url, workdir=workdir / "seed", room=room,
        policy=policy, identity=identity,
        now=datetime.fromtimestamp(before_ms / 1000, tz=UTC),
    ))
    try:
        transcript = knowledge.transcript
        if transcript and any(
            isinstance(m, str) or m.at_ms is None for m in transcript
        ):
            transcript = dated(transcript, before_ms=before_ms)
        await know(
            subject,
            Knowledge(
                transcript=transcript,
                standing=knowledge.standing,
                standing_age_hours=knowledge.standing_age_hours,
                notes=knowledge.notes,
                culture=knowledge.culture,
                conclusions=knowledge.conclusions,
                rooms=knowledge.rooms,
                spacing_seconds=knowledge.spacing_seconds,
            ),
            now_ms=before_ms,
            leave_room_for_arrival=False,
        )
    finally:
        await _close(subject)
    return Source(
        url=url, room=room, before_ms=before_ms, policy=policy, identity=identity,
        template=template,
    )

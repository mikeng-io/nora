"""What Nora KNOWS when a case starts — written into her real stores.

The seam `design/evaluation/01-boundaries.md` §3 calls *stores*, in the
operator's words: the data store, the state store and the fake store are
things you configure BEHIND her. A case saying what she knows is stating
INPUT. It is not substituting a collaborator, and the difference is the whole
rebuild:

- the old seeding built `FakeRecordStore`, `FakeMemoryStore` and
  `InMemoryNoteStore` and handed them to a hand-assembled pipeline — so what
  the suite measured was a graph nothing deploys;
- this writes the same rows into the SAME Postgres the subject was composed
  against, through the same store classes production runs, and then gets out
  of the way.

Nothing here is reachable from the eval layer as a store. `know()` takes a
`Subject` and returns `None`; there is no object to pass anywhere.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime

from nora.domain.models import NormalizedMessage
from nora.domain.standing import Standing
from nora.infra.sql import create_sql_engine
from nora.infra.sql.notes import SqlNoteStore
from nora.infra.sql.standing import SqlStandingStore

from .obtain import Subject

#: The room every seeded row lands in. It is the one `SubjectSpec.policy`'s
#: default admits, and `nora/policy.py`'s `workspace` defaults to `"nora"`, so
#: a note written under that workspace is one the turn in this room can read.
#: A case that needs another room says so in `policy` and passes `room=`.
DEFAULT_ROOM = ("signal", "g1")

#: The phone `SubjectSpec.identity`'s default maps to a person. A transcript
#: line with no `name` speaks as this sender, so it resolves to a person the
#: way a real member's line does; any OTHER name gets an unmapped sender, which
#: is a member `identity.toml` does not know — also a real case, and the one
#: that produces `person_name=None`.
_KNOWN_SENDER = "+852111"
_KNOWN_PERSON = "miles"
_KNOWN_NAME = "Miles"


@dataclass(frozen=True, slots=True)
class Message:
    """One prior room message.

    `row_id` and `at_ms` exist because some corpora CITE their own messages:
    the note-candidates cases assert that a staged candidate's `message_ids`
    contain a particular row. A case that says "note 101" and a store that
    assigns ids by insertion order are two different facts, and the second
    cannot be asserted against.

    `person` is separate from `name` for the reason `identity.toml` exists:
    the display name is what the platform reports and the person id is who
    that resolves to. A message from a member the identity does not know has a
    name and no person — which is a real case, and the one that produces
    `person_name=None`.
    """

    body: str
    name: str = _KNOWN_NAME
    #: Who this speaker resolves to. `None` means the identity does not know
    #: them; `""` means "use the default for this name".
    person: str | None = ""
    #: The case's own LABEL for this message, when it cites it elsewhere
    #: (`must_cite`). It is not a database id and is never forced onto one: the
    #: store assigns ids, and `know()` returns the mapping so a grader can
    #: translate a citation into the row it actually landed on.
    #:
    #: Forcing it was tried and rejected within the hour. A case that says
    #: "message 101" against a store that assigned 1 has two bad options —
    #: raise, which makes the corpus unrunnable, or renumber, which checks the
    #: citation against whatever happened to land in that slot. Translating is
    #: the third.
    label: int | str | None = None
    #: When it was said. `None` spaces the window a minute apart ending one
    #: minute before `now_ms`, which is the default every corpus but this one
    #: relies on.
    at_ms: int | None = None


@dataclass(frozen=True, slots=True)
class NoteSeed:
    """One note in her index, as the note tier's own fields."""

    title: str
    description: str
    body: str
    uncertainty: str = "open"
    closing_condition: str = "the room settles it"
    anchor_date: int | None = 20260901


@dataclass(frozen=True, slots=True)
class Written:
    """What `know()` laid down, and where it landed.

    `now_ms` is the instant the caller must stamp the incoming line with.
    `rows` maps each message's case-label to the id the store assigned, which
    is what lets a corpus cite its own messages without the case and the
    database having to agree on numbering.
    """

    now_ms: int
    rows: dict[int | str, int]


@dataclass(frozen=True, slots=True)
class Knowledge:
    """Everything a case says she knows. Empty is legal and is the knowledge
    cut — the `-unknown` twin of a pair is this class with one field dropped,
    which is why every field defaults."""

    transcript: tuple[Message, ...] = ()
    standing: str | None = None
    notes: tuple[NoteSeed, ...] = field(default_factory=tuple)


def _speakers(transcript: tuple[Message, ...]) -> dict[str, tuple[str, str | None]]:
    """name -> (sender_id, person_id), stable within one case."""
    out: dict[str, tuple[str, str | None]] = {}
    for msg in transcript:
        if msg.name in out:
            continue
        if msg.name == _KNOWN_NAME:
            out[msg.name] = (_KNOWN_SENDER, _KNOWN_PERSON)
        else:
            out[msg.name] = (f"+8529{len(out):03d}", None)
    return out


async def arrive(
    subject: Subject,
    body: str,
    *,
    room: tuple[str, str] = DEFAULT_ROOM,
    now_ms: int,
    name: str = _KNOWN_NAME,
) -> None:
    """The line that ARRIVES — one untagged message from a member.

    Separate from `know()` because it is a different fact: `know()` lays down
    what she already had, `arrive()` is the thing she is answering. The eval
    layer needs both and may import neither store directly, so both live here.

    Untagged by construction: nothing here mentions her, which is what makes
    the line a participation candidate rather than a tag.
    """
    platform, conversation_id = room
    sender_id, person_id = (
        (_KNOWN_SENDER, _KNOWN_PERSON) if name == _KNOWN_NAME else ("+8529900", None)
    )
    await subject.deps.audit.append(
        message=NormalizedMessage(
            platform=platform,
            conversation_id=conversation_id,
            conversation_type="group",
            sender_id=sender_id,
            sender_name=name,
            timestamp=now_ms,
            body=body,
            raw={},
        ),
        person_id=person_id,
    )


async def deliver(
    subject: Subject,
    body: str,
    *,
    room: tuple[str, str] = DEFAULT_ROOM,
    now_ms: int,
    name: str = _KNOWN_NAME,
) -> None:
    """Hand one message to `Dispatcher.on_message` — the deployment's own
    inbound path, exactly as a platform adapter's `listen()` loop does.

    NOT `arrive()` first: ingest's audit stage appends the row itself
    (`nora/ingest/stages/audit.py:33`), so writing it here as well would put the
    message in the transcript TWICE — once as history and once as itself — and
    the turn would read a room that had said everything twice. `arrive()` is
    for the paths that bypass ingest, which is why both exist.
    """
    platform, conversation_id = room
    sender_id, _ = (
        (_KNOWN_SENDER, _KNOWN_PERSON) if name == _KNOWN_NAME else ("+8529900", None)
    )
    await subject.deps.dispatcher.on_message(
        NormalizedMessage(
            platform=platform,
            conversation_id=conversation_id,
            conversation_type="group",
            sender_id=sender_id,
            sender_name=name,
            timestamp=now_ms,
            body=body,
            raw={},
        )
    )


async def settle(subject: Subject, *, timeout_seconds: float = 180.0) -> bool:
    """Wait until every turn the deployment SPAWNED has finished. `True` if it
    settled, `False` on timeout.

    `Dispatcher.on_message` does not await the turn — it hands the request to
    `BackgroundTasks.spawn` and returns, because a platform listen loop must not
    block on one room. So an entry point that drives the deployment's real
    inbound path has to wait for what that path fired.

    Polls `len(deps.background)`, which is `BackgroundTasks.__len__` — PUBLIC
    surface, deliberately. A `drain()` on `BackgroundTasks` would be tidier and
    would also be a method added to `nora.app` for evaluation's sake, which is
    the one thing `design/evaluation/01-boundaries.md` §5(2) forbids. Polling is
    the cost of not breaking that rule, and it is paid once, here.
    """
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while len(subject.deps.background):
        if asyncio.get_running_loop().time() >= deadline:
            return False
        await asyncio.sleep(0.05)
    return True


def silent_failures(subject: Subject) -> float:
    """`nora_silent_failures_total`, summed over every label set.

    Production's OWN instrumentation for "she did not speak and it was not a
    choice": `handler_error`, `invocation_timeout`, `no_send_call`,
    `audit_write_failed`. An entry point reads it before and after a turn, and a
    rise means the turn did not run — which must never be graded as silence.

    Needed because the tag path gives its caller nothing else. `speak` returns
    an outcome token and the gate returns a `source`; `Dispatcher.on_message`
    returns `None` and swallows the turn into a background task, so without this
    an unreachable provider produces "no rows, no error" — indistinguishable
    from a deliberate silence, and scored as one.

    Summed rather than read per-reason: the question here is "did anything go
    wrong", and enumerating the reasons would put a list in the eval layer that
    has to be kept in step with `nora/obs/metric_specs.py`.
    """
    return sum(
        v for key, v in subject.deps.metrics.snapshot().items()
        if key and key[0] == "nora_silent_failures_total"
    )


async def heard(
    subject: Subject,
    *,
    room: tuple[str, str] = DEFAULT_ROOM,
    after_id: int = 0,
    limit: int = 200,
) -> tuple[tuple[str, str], ...]:
    """What the ROOM HEARD since `after_id`: her own rows, as
    `(body, delivery_status)`.

    The observable is the audit ROW, not the wire. `nora/agent/guards.py:528`
    appends her reply RECORD-FIRST — before any send, `delivery_status=PENDING`
    — and a send that raises is caught there. So with no platform daemon in
    front of a case the row is exactly what production would have written, and
    the status says what the transport then did with it.

    Both are returned because they must never collapse: *she said nothing* and
    *the wire refused what she said* are different facts, and a suite that maps
    the second onto the first grades a healthy Nora as silent.
    """
    _, conversation_id = room
    rows = await subject.deps.audit.recent(
        conversation_id=conversation_id, limit=limit,
    )
    return tuple(
        (r.body or "", r.delivery_status.value)
        for r in rows
        if r.is_nora and r.id > after_id
    )


async def last_row_id(
    subject: Subject, *, room: tuple[str, str] = DEFAULT_ROOM,
) -> int:
    """The newest row id in the room, so `heard` can be asked for what came
    AFTER a turn rather than for everything she has ever said."""
    _, conversation_id = room
    rows = await subject.deps.audit.recent(conversation_id=conversation_id, limit=1)
    return rows[-1].id if rows else 0


async def know(
    subject: Subject,
    knowledge: Knowledge,
    *,
    room: tuple[str, str] = DEFAULT_ROOM,
    now_ms: int | None = None,
) -> Written:
    """Write `knowledge` into the subject's stores.

    The returned instant is the one the caller must stamp the INCOMING line
    with: the window is laid down ending one minute before it, so the judged
    line is strictly the newest row `recent`'s `(ts, id)` sort sees. Two
    independent reads of the clock a few lines apart is how a judged line ends
    up sorting above the window it is read against (issue #213).

    Ages, not absolute times, are what reach the model
    (`nora.agent.prefill._age`), so the base has to be a REAL recent instant. A
    frozen historical constant does not raise and does not fail a test; the age
    it prints simply grows every calendar day nobody touches it.
    """
    if now_ms is None:
        now_ms = int(datetime.now(UTC).timestamp() * 1000)
    platform, conversation_id = room
    rows: dict[int | str, int] = {}

    speakers = _speakers(knowledge.transcript)
    base = now_ms - len(knowledge.transcript) * 60_000
    for i, msg in enumerate(knowledge.transcript):
        sender_id, person_id = speakers[msg.name]
        if msg.person != "":
            person_id = msg.person
        row_id = await subject.deps.audit.append(
            message=NormalizedMessage(
                platform=platform,
                conversation_id=conversation_id,
                conversation_type="group",
                sender_id=sender_id,
                sender_name=msg.name,
                timestamp=msg.at_ms if msg.at_ms is not None else base + i * 60_000,
                body=msg.body,
                raw={},
            ),
            person_id=person_id,
        )
        if msg.label is not None:
            rows[msg.label] = row_id

    # A second engine over the SAME database, not a second database: the
    # composition root keeps its engine private and `Dependencies` does not
    # name it. Reaching for a private to avoid a connection pool would be the
    # trade the wrong way round.
    engine = create_sql_engine(subject.database_url)
    try:
        if knowledge.standing is not None:
            await SqlStandingStore(engine).write(
                standing=Standing(
                    platform=platform,
                    conversation_id=conversation_id,
                    body=knowledge.standing,
                    fold_count=1,
                    updated_at=now_ms,
                )
            )
        if knowledge.notes:
            notes = SqlNoteStore(engine)
            for n, note in enumerate(knowledge.notes):
                await notes.create(
                    workspace=subject.cfg.memory.workspace_id,
                    title=note.title,
                    description=note.description,
                    uncertainty=note.uncertainty,
                    closing_condition=note.closing_condition,
                    established_in=conversation_id,
                    body=note.body,
                    established_by="case-seed",
                    now_ms=now_ms + n,
                    anchor_date=note.anchor_date,
                )
    finally:
        await engine.dispose()
    return Written(now_ms=now_ms, rows=rows)

"""What Nora KNOWS when a case starts — written into her real stores.

The seam this module covers is the *stores*: the data store, the state store
and the fake store are things you configure BEHIND her. A case saying what she
knows is stating INPUT. It is not substituting a collaborator: seeding fakes
(`FakeRecordStore`, `FakeMemoryStore`, `InMemoryNoteStore`) into a
hand-assembled pipeline would measure a graph nothing deploys. This writes the
same rows into the SAME Postgres the subject was composed against, through the
same store classes production runs, and then gets out of the way.

Nothing here is reachable from the eval layer as a store. `know()` takes a
`Subject` and returns `None`; there is no object to pass anywhere.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.culture import character_key, nora_character_key, unit_culture_key
from nora.domain.media import MediaBlob
from nora.domain.models import NormalizedMessage
from nora.domain.note_body import citations_in
from nora.domain.notes import Note
from nora.domain.reminders import Reminder, ReminderState
from nora.domain.standing import Standing
from nora.infra.sql import create_sql_engine, memory_index
from nora.infra.sql.culture import SqlCultureStore
from nora.infra.sql.lessons import SqlLessonStore
from nora.infra.sql.notes import SqlNoteStore
from nora.infra.sql.reminders import SqlReminderStore
from nora.infra.sql.schema import NOTE, NOTE_REVISION
from nora.infra.sql.standing import SqlStandingStore

from .obtain import Subject

#: The phone `SubjectSpec.identity`'s default maps to a person. A transcript
#: line with no `name` speaks as this sender, so it resolves to a person the
#: way a real member's line does; any OTHER name gets an unmapped sender, which
#: is a member `identity.toml` does not know — also a real case, and the one
#: that produces `person_name=None`.
_HOUR_MS = 3_600_000
_KNOWN_SENDER = "+852111"
_KNOWN_PERSON = "miles_novak"
_KNOWN_NAME = "Miles"


@dataclass(frozen=True, slots=True)
class Message:
    """One prior room message.

    `row_id` and `at_ms` exist because some corpora CITE their own messages by
    row id. A case that says "message 101" and a store that assigns ids by
    insertion order are two different facts, and the second cannot be asserted
    against.

    `person` is separate from `name` for the reason `identity.toml` exists:
    the display name is what the platform reports and the person id is who
    that resolves to. A message from a member the identity does not know has a
    name and no person — which is a real case, and the one that produces
    `person_name=None`.
    """

    body: str
    #: The display name the platform reported. `None` is a REAL value, not a
    #: missing one — `messages.sender_name` is nullable and a sealed-sender
    #: Signal row carries no name at all. It is spelled here because
    #: `sandbox.py` copies real rows, and mapping a nameless sender onto
    #: "Unknown" would invent a display name the room never saw.
    name: str | None = _KNOWN_NAME
    #: Who this speaker resolves to. `None` means the identity does not know
    #: them; `""` means "use the default for this name".
    person: str | None = ""
    #: The case's own LABEL for this message, when it cites it elsewhere
    #: (`must_cite`). It is not a database id and is never forced onto one: the
    #: store assigns ids, and `know()` returns the mapping so a grader can
    #: translate a citation into the row it actually landed on.
    #:
    #: Forcing it is not an option: a case that says "message 101" against a
    #: store that assigned 1 would have to either raise, which makes the corpus
    #: unrunnable, or renumber, which checks the citation against whatever
    #: happened to land in that slot. Translating is the third.
    label: int | str | None = None
    #: When it was said. `None` spaces the window a minute apart ending one
    #: minute before `now_ms`, which is the default every corpus but this one
    #: relies on.
    at_ms: int | None = None

    #: This row CARRIES AN ATTACHMENT she holds.
    #:
    #: A real `MediaBlob` rather than a flag on the row, because the store
    #: serialises it through `nora.domain.media.media_json` and a hand-written
    #: string would be a second copy of that format. The bytes are fictional;
    #: what matters is that `media_join.enrich` has something to join and the
    #: renderer does not drop the row as an empty body.
    #:
    #: A pin that compares only body and id cannot see this flag, so a case
    #: named for stickers needs it set to actually carry any.
    media: bool = False

    #: HER OWN speech. Not `name="Nora"` — `nora.agent.prefill._speaker` reads
    #: the FLAG, so a row merely named Nora renders as an ordinary member who
    #: happens to be called that. A different input, and the wrong one.
    #:
    #: Needed by any case about "did she already say this". The matching flag
    #: is on `nora.testing.fakes.transcript_rows`.
    is_nora: bool = False


@dataclass(frozen=True, slots=True)
class NoteSeed:
    """One note in her index, as the note tier's own fields.

    `id` is `None` for a hand-authored seed (every existing test fixture) —
    `know()` then lets `SqlNoteStore.create` assign one.
    A seed built FROM a real snapshot (`nora.subject.sandbox._note_seed`)
    states its SOURCE id here, and `know()` inserts the row under it
    instead: a reminder seeded alongside keeps the SAME source `note_id` in
    its payload (`_reminder_seed` copies `payload` verbatim), and
    `evals.backbone`'s title -> id resolution is built from the source
    snapshot too — so a fresh id would disagree with every reminder and every
    resolution about what a note's id is.
    """

    title: str
    description: str
    body: str
    uncertainty: str = "open"
    closing_condition: str = "the room settles it"
    anchor_date: int | None = 20260901
    id: str | None = None
    #: The note's entity anchor (`Note.anchor_entity_ids`). A note from a real
    #: snapshot may carry this and no `anchor_date`; without it such a note
    #: could not be seeded at all.
    anchor_entity_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ReminderSeed:
    """One reminder OUTSTANDING in the room, as the reminder tier's own fields.

    Deliberately smaller than `domain.Reminder`: a seed states what a case can
    know — who asked, what for, and when it comes due — and everything else is
    the store's to assign or is fixed by the fact that the row is outstanding.
    `state` is not a field because there is only one state a seeded row can be
    in; `fired_at`/`outcome` are not fields for the same reason.

    `due_in_seconds` is RELATIVE, like `standing_age_hours` and for the same
    reason: an absolute instant written into a case goes stale every calendar
    day, while «due in four hours» keeps meaning what it meant. A sandbox
    seeded from a real snapshot passes the real offset it measured, so nothing
    is rounded away there either.
    """

    text: str
    #: Seconds from `now_ms` to `due_at`. NEGATIVE is legal and is the
    #: interesting case: a row already overdue is exactly what the proactive
    #: path fires on, and a seed that could not express one could not set that
    #: path up at all.
    due_in_seconds: float
    #: Seconds from `now_ms` back to `created_at`. How long the room has been
    #: waiting on it, which is part of what a turn reads.
    created_seconds_ago: float = 0.0
    requester_person_id: str = _KNOWN_PERSON
    #: `kind` names WHAT the row is (DEC-139): `"reminder"`, or `"proactive"`,
    #: the row a proactive act fires.
    kind: str = "reminder"
    payload: Mapping[str, str] | None = None


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
class RoomSeed:
    """One OTHER room of the same workspace, with what it knows.

    A workspace-wide pass picks which of the workspace's rooms to stand in, so
    a case about it names more than one room and keys its standing per room. A
    single-room `Knowledge` cannot state that, and a pass given an empty
    workspace finds nothing due, writes nothing, and answers `nothing_found` —
    which grades as a PASS for every case expecting none.

    `home` is the workspace's home room (`policy.toml`'s `home_room = true`):
    the operator's opt-in, and the room a
    workspace-keyed row fires into. Exactly one per workspace; two is a startup
    error rather than a guess.
    """

    conversation_id: str
    platform: str = "signal"
    label: str = ""
    home: bool = False
    transcript: tuple[Message, ...] = ()
    standing: str | None = None
    #: How long ago the standing was written. It renders as an AGE, and how
    #: stale a position is can be part of what a case judges.
    standing_age_hours: float | None = None


@dataclass(frozen=True, slots=True)
class AccountSeed:
    """One Character account as the revision it was: its `body`, and how old it
    was at the instant (`<character>` and `<unit_culture>` both render an AGE,
    so a copy written as "just now" would show the model a different block than
    production did)."""

    body: str
    age_hours: float = 0.0


@dataclass(frozen=True, slots=True)
class Knowledge:
    """Everything a case says she knows. Empty is legal and is the knowledge
    cut — the `-unknown` twin of a pair is this class with one field dropped,
    which is why every field defaults."""

    transcript: tuple[Message, ...] = ()
    standing: str | None = None

    #: How long ago the standing was written; it renders as an AGE.
    standing_age_hours: float | None = None

    #: What she has CONCLUDED about a person, keyed by Honcho peer id.
    #:
    #: Written through `MemoryStore.conclude` — production's own write lane, the
    #: one the `remember` tool uses, over the same (observer, observed) pair
    #: `representation` reads back. So this is not a substitution: it is the
    #: write side of the tier whose read side the turn uses.
    #:
    #: The key is the peer id, which IS the person id
    #: (`PersonRef.honcho_peer_id`). A key that is no person seeds a conclusion
    #: nothing can read: `representation` looks up `target or peer_id` and an
    #: unknown peer comes back reachable-and-knowing-nothing, so the case would
    #: grade a prompt missing the very block it is about.
    conclusions: dict[str, tuple[str, ...]] = field(default_factory=dict)

    #: OTHER rooms of the same workspace, each with its own transcript and
    #: standing. Empty for every single-room case, which is most of them.
    rooms: tuple[RoomSeed, ...] = ()

    #: Seconds between consecutive messages, oldest first, ending one step
    #: before `now_ms`.
    #:
    #: STATED rather than defaulted, because the rendered prompt shows an AGE
    #: per row and the gate is asked whether the room has moved past something.
    #: Different corpora want different steps (60s, 180s), so the ages the
    #: labels were assigned against belong to the corpus, not the driver.
    spacing_seconds: int = 60
    notes: tuple[NoteSeed, ...] = field(default_factory=tuple)

    #: What the room was still WAITING on. Written through the real
    #: `SqlReminderStore.create`, keyed on the subject's own room, so a
    #: proactive turn reads them back through `ReminderStore.due` /
    #: `pending_for` exactly as production does.
    reminders: tuple[ReminderSeed, ...] = field(default_factory=tuple)

    #: The room's CULTURE ACCOUNT — how this room TALKS, as one revision body.
    #:
    #: ONE BODY, NOT A CHAIN. The tier's genuine contribution is the ORDER of
    #: its revisions (DEC-183, `nora/infra/sql/schema.py`'s `CULTURE_REVISION`
    #: note), but nothing READS the chain on a turn — `CultureAdaptor.inputs`
    #: renders `head.body` and nothing else — so a case stating a chain would
    #: be stating input no entry point can observe.
    #: A seed is one account, written through `SqlCultureStore.append`, which
    #: is production's own write lane and moves the head in the same
    #: transaction.
    #:
    #: **Keyed on the ROOM'S `primary_id`, which for a subject IS its
    #: `conversation_id`** — not on `(platform, conversation_id)`, which is the
    #: mistake `nora.domain.culture.Culture` names in as many words. It is safe
    #: to key it here rather than require it the way `snapshot()` does, because
    #: `build_config` (obtain.py) RESOLVES the policy it writes through the real
    #: parser and refuses unless `Conversation.primary_id == spec.room[1]`; a
    #: substring check would only prove the room's id appears SOMEWHERE in the
    #: file, possibly on a different entry's conversation. A key that matched
    #: nothing, or matched the wrong room, would read exactly like a room that
    #: had never been folded, or silently key under a real other room, which is
    #: why this is pinned to a resolved refusal rather than to a convention.
    culture: str | None = None

    #: The UNIT's own account (`unit_culture_key`), what `<unit_culture>`
    #: renders in every room of the unit. Written for the subject's room only.
    unit_culture: AccountSeed | None = None

    #: Her own account in the subject's unit (`nora_character_key`), what
    #: `<self>` renders.
    nora_account: AccountSeed | None = None

    #: Her active lessons in the subject's unit, oldest first, what `<lessons>`
    #: renders under `[lessons] render`. Seeded as
    #: lessons written at the instant: the block renders no age.
    lessons: tuple[str, ...] = ()

    #: Each listed person's account in the subject's unit, keyed by person id
    #: (`character_key(unit, person_id)`), what `<character>` renders. A key
    #: that is no listed person of the room renders nothing, so
    #: `sandbox_from_snapshot` only ever fills the persons the room lists.
    characters: Mapping[str, AccountSeed] = field(default_factory=dict)


def _speakers(
    transcript: tuple[Message, ...]
) -> dict[str | None, tuple[str, str | None]]:
    """name -> (sender_id, person_id), stable within one case.

    Source account ids are never copied: `Message` has no `sender_id` field.
    These are initial synthetic ids; `know` substitutes a loaded roster account
    for a known human so production's name projection can resolve the row.
    """
    out: dict[str | None, tuple[str, str | None]] = {}
    for msg in transcript:
        if msg.name in out:
            continue
        if msg.name == _KNOWN_NAME:
            out[msg.name] = (_KNOWN_SENDER, _KNOWN_PERSON)
        else:
            out[msg.name] = (f"+8529{len(out):03d}", None)
    return out


def _sender_for(subject: Subject, platform: str, person: str) -> str:
    """One id the roster maps `person` to on `platform` — refused by name when
    there is none, because ingest would refuse the line and nothing would
    raise."""
    ids = subject.deps.persons.platform_ids(platform, person)
    if not ids:
        raise ValueError(
            f"person {person!r} has no {platform} id in the subject's "
            "roster; ingest would refuse the line and nothing would raise"
        )
    return sorted(ids)[0]


async def arrive(
    subject: Subject,
    body: str,
    *,
    room: tuple[str, str] | None = None,
    now_ms: int,
    name: str = _KNOWN_NAME,
    person: str | None = None,
    media_blobs: tuple[MediaBlob, ...] = (),
) -> int:
    """The line that ARRIVES — one untagged message from a member.

    Separate from `know()` because it is a different fact: `know()` lays down
    what she already had, `arrive()` is the thing she is answering. The eval
    layer needs both and may import neither store directly, so both live here.

    Untagged by construction: nothing here mentions her, which is what makes
    the line a participation candidate rather than a tag.

    `person` is `deliver()`'s: WHO said it, as a `person_id` the roster
    knows, with the sender id the roster maps to them on this platform.
    """
    platform, conversation_id = room or subject.room
    person_id: str | None
    if person is not None:
        sender_id, person_id = _sender_for(subject, platform, person), person
    elif name == _KNOWN_NAME:
        sender_id, person_id = _KNOWN_SENDER, _KNOWN_PERSON
    else:
        sender_id, person_id = "+8529900", None
    return await subject.deps.audit.append(
        message=NormalizedMessage(
            platform=platform,
            conversation_id=conversation_id,
            conversation_type="group",
            sender_id=sender_id,
            sender_name=name,
            timestamp=now_ms,
            body=body,
            raw={},
            media_blobs=media_blobs,
        ),
        person_id=person_id,
    )


async def deliver(
    subject: Subject,
    body: str,
    *,
    room: tuple[str, str] | None = None,
    now_ms: int,
    name: str = _KNOWN_NAME,
    person: str | None = None,
    mention: bool = False,
) -> None:
    """Hand one message to `Dispatcher.on_message` — the deployment's own
    inbound path, exactly as a platform adapter's `listen()` loop does.

    NOT `arrive()` first: ingest's audit stage appends the row itself
    (`nora/ingest/stages/audit.py`), so writing it here as well would put the
    message in the transcript TWICE — once as history and once as itself — and
    the turn would read a room that had said everything twice. `arrive()` is
    for the paths that bypass ingest, which is why both exist.

    `person` names WHO said it, as a `person_id` the subject's roster knows;
    the sender id is then one the roster maps to that person on this platform,
    so ingest admits the line the way it admits that member's own. A real
    room's `policy.toml` names its members — `persons = ["miles_novak", ...]` —
    and a line from a sender the roster cannot resolve is refused at ingest
    with nothing raised. So an unknown `person` is refused HERE, by name, and
    the default (`None`) keeps the synthetic `_KNOWN_SENDER` that
    `_DEFAULT_IDENTITY` maps to `miles_novak`.

    `mention` marks the line as @-mentioning her, STRUCTURALLY: Signal carries
    a mention in a parallel array rather than in the body
    (`NormalizedMessage.mentions`), and `MentionPolicy` matches that array
    against the registry's `self_aliases()`. With the deployment's text
    trigger off, a mention or a reply is how she is engaged at
    all, so a case that expects a reply states one. Refused when the registry
    answers to no id — a mention of nobody engages nothing and would read as
    silence.
    """
    platform, conversation_id = room or subject.room
    if person is not None:
        sender_id = _sender_for(subject, platform, person)
    else:
        sender_id = _KNOWN_SENDER if name == _KNOWN_NAME else "+8529900"
    mentions: frozenset[str] = frozenset()
    if mention:
        mentions = frozenset(subject.deps.registry.self_aliases())
        if not mentions:
            raise ValueError(
                "the case states a mention of her, and the registry answers "
                "to no id on any enabled platform — the mention would engage "
                "nothing and the case would read as silence"
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
            mentions=mentions,
        )
    )


class Spawned:
    """Every task the subject's graph spawns from the moment this is built,
    including what those tasks spawn in turn: the turn a delivered line fired,
    and nothing the graph was already carrying.

    Wraps `spawn` on THIS subject's own `BackgroundTasks` instance (public
    surface, the method production calls), so nothing is added to `nora.app`
    and no other subject is touched.
    `close()` puts it back.
    """

    def __init__(self, subject: Subject) -> None:
        self._background = subject.deps.background
        self.tasks: list[asyncio.Task[None]] = []
        spawn = self._background.spawn

        def recording(coro: Any) -> asyncio.Task[None]:
            task = spawn(coro)
            self.tasks.append(task)
            return task

        self._background.spawn = recording  # type: ignore[method-assign]

    def close(self) -> None:
        with contextlib.suppress(AttributeError):
            del self._background.spawn


def watch(subject: Subject) -> Spawned:
    """`settle()`'s argument, built BEFORE the line that starts the turn."""
    return Spawned(subject)


async def settle(
    subject: Subject, *, spawned: Spawned, timeout_seconds: float = 180.0,
) -> bool:
    """Wait until every turn the deployment SPAWNED for this case has
    finished. `True` if it settled, `False` on timeout.

    `Dispatcher.on_message` does not await the turn — it hands the request to
    `BackgroundTasks.spawn` and returns, because a platform listen loop must not
    block on one room. So an entry point that drives the deployment's real
    inbound path has to wait for what that path fired.

    **It waits on the TASKS the case spawned, not on a count.** A count of
    `len(deps.background)` against a baseline breaks when a task the graph was
    already carrying (such as the presence pre-flight) finishes mid-turn: the
    count falls back to the baseline with the turn still running, and the turn
    is torn down mid-model-call and graded as silence. Endless loops
    composition spawns (`ComputeJobPoller`, the heartbeat) are not in
    `spawned`, so they never hold this open.
    """
    try:
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while not all(t.done() for t in list(spawned.tasks)):
            if asyncio.get_running_loop().time() >= deadline:
                return False
            await asyncio.sleep(0.05)
        return True
    finally:
        spawned.close()


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
    room: tuple[str, str] | None = None,
    after_id: int = 0,
    limit: int = 200,
) -> tuple[tuple[str, str], ...]:
    """What the ROOM HEARD since `after_id`: her own rows, as
    `(body, delivery_status)`.

    The observable is the audit ROW, not the wire. `nora/agent/guards.py`
    appends her reply RECORD-FIRST — before any send, `delivery_status=PENDING`
    — and a send that raises is caught there. So with no platform daemon in
    front of a case the row is exactly what production would have written, and
    the status says what the transport then did with it.

    Both are returned because they must never collapse: *she said nothing* and
    *the wire refused what she said* are different facts, and a suite that maps
    the second onto the first grades a healthy Nora as silent. A well-formed
    Signal send lands in the sandbox (`nora.subject.sandbox_signal`), so the
    status reads `sent` as production's does.
    """
    _, conversation_id = room or subject.room
    rows = await subject.deps.audit.recent(
        conversation_id=conversation_id, limit=limit,
    )
    return tuple(
        (r.body or "", r.delivery_status.value)
        for r in rows
        if r.is_nora and r.id > after_id
    )


async def last_row_id(
    subject: Subject, *, room: tuple[str, str] | None = None,
) -> int:
    """The newest row id in the room, so `heard` can be asked for what came
    AFTER a turn rather than for everything she has ever said."""
    _, conversation_id = room or subject.room
    rows = await subject.deps.audit.recent(conversation_id=conversation_id, limit=1)
    return rows[-1].id if rows else 0


async def _write_standing(
    subject: Subject, room: tuple[str, str], body: str, *, at_ms: int,
) -> None:
    engine = create_sql_engine(subject.database_url)
    try:
        await SqlStandingStore(engine).write(
            standing=Standing(
                platform=room[0], conversation_id=room[1], body=body,
                fold_count=1, updated_at=at_ms,
            )
        )
    finally:
        await engine.dispose()


async def _seed_note_with_id(
    engine: AsyncEngine, *, note: Note, body: str, established_by: str, written_at: int,
) -> None:
    """Insert ONE note and its first revision under `note.id`, exactly as
    seeded — a SEED-ONLY primitive, independent of `nora.infra.sql.notes`.

    `SqlNoteStore.create` is the production write path and always assigns a
    fresh `uuid4`; it takes no id parameter, reasonably, since production
    never has one to give it. A note seeded FROM a real snapshot (`nora.
    subject.sandbox._note_seed`) already has one — the id every reminder
    seeded alongside keeps in its payload (`_reminder_seed` copies `payload`
    verbatim). Assigning a different id here would leave the sandbox
    internally inconsistent: the reminder and the note would disagree about
    what got inserted.

    Same two inserts, same columns, as `SqlNoteStore._create_on_conn` (there
    is one schema; this does not invent a second one) — kept as an
    independent statement here rather than a call into that (private)
    method, so this file's seeding path does not couple to its internals.

    `note`'s own `__post_init__` (`nora.domain.notes`) has already run by
    construction — it is a real `Note`, not a bag of fields — so the same
    refusals `SqlNoteStore.create` would raise (no anchor, an over-long
    index entry, a blank closing condition) raise here too, before either
    insert.

    `note.head_revision_id` is the CALLER's to set (it must, to construct a
    valid `Note` at all) and is reused verbatim as the revision's own `id` —
    one value, never generated twice, so the note's pointer and the
    revision's identity cannot disagree.
    """
    async with engine.begin() as conn:
        await conn.execute(sa.insert(NOTE).values(
            id=note.id, unit=note.unit, title=note.title,
            description=note.description, uncertainty=note.uncertainty,
            closing_condition=note.closing_condition,
            anchor_date=note.anchor_date,
            anchor_entity_ids=list(note.anchor_entity_ids),
            established_in=note.established_in, created_at=note.created_at,
            head_revision_id=note.head_revision_id, revisit_at=note.revisit_at,
        ))
        await conn.execute(sa.insert(NOTE_REVISION).values(
            id=note.head_revision_id, note_id=note.id, body=body,
            parent_revision_id=None, written_at=written_at,
            established_by=established_by, instruction_sha256=None, model=None,
            citations=list(citations_in(body)),
        ))
        # The sandbox's index holds what the case seeds, as production's would.
        await memory_index.project(conn, "note", note.id)


async def know(
    subject: Subject,
    knowledge: Knowledge,
    *,
    room: tuple[str, str] | None = None,
    now_ms: int | None = None,
    leave_room_for_arrival: bool = True,
) -> Written:
    """Write `knowledge` into the subject's stores.

    The returned instant is the one the caller must stamp the INCOMING line
    with: the window is laid down ending one step before it, so the judged line
    is strictly the newest row `recent`'s `(ts, id)` sort sees. Two independent
    reads of the clock a few lines apart is how a judged line ends up sorting
    above the window it is read against.

    `leave_room_for_arrival=False` ends the window AT `now_ms` instead, for an
    entry point where the window IS the input and nothing arrives after it —
    the gate. It is not cosmetic: the prompt renders an AGE per row, and with
    the step reserved anyway the newest line reads "3m ago" when it had in fact
    just landed. The gate is asked whether the room has moved past something,
    so the age of its last line is part of the question.

    Ages, not absolute times, are what reach the model
    (`nora.agent.prefill._age`), so the base has to be a REAL recent instant. A
    frozen historical constant does not raise and does not fail a test; the age
    it prints simply grows every calendar day nobody touches it.
    """
    if now_ms is None:
        now_ms = int(datetime.now(UTC).timestamp() * 1000)
    platform, conversation_id = room or subject.room
    rows: dict[int | str, int] = {}

    speakers = _speakers(knowledge.transcript)
    step = knowledge.spacing_seconds * 1000
    reserved = step if leave_room_for_arrival else 0
    base = now_ms - reserved - (len(knowledge.transcript) - 1) * step
    for i, msg in enumerate(knowledge.transcript):
        sender_id, person_id = speakers[msg.name]
        if msg.person != "":
            person_id = msg.person
        # Production projects names by platform account, not person_id.
        # Use the loaded roster; never copy source account ids into a fixture.
        ids = (subject.deps.persons.platform_ids(
                   platform, subject.deps.persons.subject_id_for(person_id))
               if not msg.is_nora and person_id else frozenset())
        if ids:
            sender_id = sorted(ids)[0]
        else:
            # A synthetic account must not accidentally name a roster person.
            while subject.deps.persons.resolve(platform, sender_id) is not None:
                sender_id += "0"
        row_id = await subject.deps.audit.append(
            message=NormalizedMessage(
                platform=platform,
                conversation_id=conversation_id,
                conversation_type="group",
                sender_id=sender_id,
                sender_name=msg.name,
                timestamp=msg.at_ms if msg.at_ms is not None else base + i * step,
                body=msg.body,
                raw={},
                media_blobs=(
                    (
                        MediaBlob(
                            # Deterministic, and NOT over the body: a row's
                            # body in an f-string is what
                            # tests/architecture/test_one_row_renders_one_line
                            # exists to catch, and the room plus the index
                            # already identify the row uniquely.
                            sha256=hashlib.sha256(
                                f'{conversation_id}:{i}'.encode()
                            ).hexdigest(),
                            media_type="image/jpeg",
                            size=1024,
                        ),
                    )
                    if msg.media
                    else ()
                ),
            ),
            person_id=person_id,
            is_nora=msg.is_nora,
        )
        if msg.label is not None:
            rows[msg.label] = row_id

    # WHAT SHE HAS CONCLUDED, through the production write lane.
    #
    # Fail-LOUD rather than best-effort: `conclude` answers False on any
    # failure because the `remember` tool must never take a reply path down
    # with it. Here the opposite is true — a conclusion that did not land makes
    # the `-known` half of a twin pair identical to its `-unknown` half, and
    # the pair reports that the knowledge cut works having cut nothing.
    known = set(await subject.deps.persons.all_ids()) if hasattr(
        subject.deps.persons, "all_ids"
    ) else {_KNOWN_PERSON, subject.cfg.memory.ai_peer}
    for peer, lines in knowledge.conclusions.items():
        if peer not in known:
            # LOUD. A conclusion under an id that is nobody is written and
            # unreadable, which is the quietest way for a knowledge-cut twin
            # pair to agree while asserting that it disagrees.
            raise ValueError(
                f"conclusion keyed {peer!r}, which is not a person this "
                f"subject knows ({sorted(known)}); it would be written and "
                "never read"
            )
        # Honcho requires `target` to already exist as a peer in this
        # workspace, and the sandbox's workspace (`_baseline()`'s `-eval`
        # sibling) is not production's — so unlike production, where a peer
        # is always created by a real `feed()` call before anyone concludes
        # about them, nothing here may have created `peer` yet. Skipping this
        # 404s the write and this loop's fail-loud check misreports it as
        # Honcho refusing a well-formed call.
        #
        # ROUTED by the room (DEC-214), exactly as the `remember` tool in a
        # turn in this room would be: the room's unit decides the Honcho
        # workspace and the named scope its reads look in, so a seed written
        # anywhere else would be written and never read.
        await subject.deps.memory.ensure_peer(
            peer_id=peer, conversation_id=conversation_id,
        )
        for line in lines:
            if not await subject.deps.memory.conclude(
                peer_id=subject.cfg.memory.ai_peer, content=line, target=peer,
                conversation_id=conversation_id,
            ):
                raise RuntimeError(
                    f"could not write a conclusion for {peer!r} — Honcho is "
                    f"at {subject.cfg.memory.base_url} and did not accept it"
                )

    # The OTHER rooms of this workspace, each written into its own room.
    for extra in knowledge.rooms:
        extra_room = (extra.platform, extra.conversation_id)
        if extra.transcript:
            await know(
                subject,
                Knowledge(
                    transcript=extra.transcript,
                    spacing_seconds=knowledge.spacing_seconds,
                ),
                room=extra_room,
                now_ms=now_ms,
                leave_room_for_arrival=leave_room_for_arrival,
            )
        if extra.standing is not None:
            await _write_standing(
                subject, extra_room, extra.standing,
                at_ms=now_ms - int((extra.standing_age_hours or 0) * 3_600_000),
            )

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
                    updated_at=now_ms - int(
                        (knowledge.standing_age_hours or 0) * 3_600_000
                    ),
                )
            )
        if knowledge.notes:
            notes = SqlNoteStore(engine)
            for n, note in enumerate(knowledge.notes):
                if note.id is not None:
                    # SOURCE ID PRESERVED: `NoteSeed.id`'s own
                    # docstring. `SqlNoteStore.create` always assigns a FRESH
                    # uuid4 and has no parameter to override it — reasonably,
                    # it is not a seeding API — so a note seeded FROM a real
                    # snapshot goes through `_seed_note_with_id` instead,
                    # which inserts under the id the caller already resolved
                    # everything else against.
                    await _seed_note_with_id(
                        engine,
                        note=Note(
                            id=note.id, unit=subject.unit, title=note.title,
                            description=note.description,
                            uncertainty=note.uncertainty,
                            closing_condition=note.closing_condition,
                            established_in=conversation_id,
                            created_at=now_ms + n,
                            head_revision_id=str(uuid.uuid4()),
                            anchor_date=note.anchor_date,
                            anchor_entity_ids=note.anchor_entity_ids,
                        ),
                        body=note.body,
                        established_by="case-seed",
                        written_at=now_ms + n,
                    )
                    continue
                await notes.create(
                    # The ROOM'S unit, resolved from its policy entry at
                    # `obtain()` — not `memory.workspace_id`, which is
                    # Honcho's per-deployment workspace and in production is
                    # not the unit of any room.
                    unit=subject.unit,
                    title=note.title,
                    description=note.description,
                    uncertainty=note.uncertainty,
                    closing_condition=note.closing_condition,
                    established_in=conversation_id,
                    body=note.body,
                    established_by="case-seed",
                    now_ms=now_ms + n,
                    anchor_date=note.anchor_date,
                    anchor_entity_ids=note.anchor_entity_ids,
                )
        if knowledge.reminders:
            # THE REAL STORE, so the row a proactive turn reads is the row
            # production would have read. `create` is the same method the
            # `remind` tool calls; nothing here writes the table directly.
            #
            # `state` is PENDING and `fired_at`/`outcome` are None on every
            # seeded row, which is not a default being accepted but the whole
            # definition of the tier: `Knowledge.reminders` states what the
            # room was still WAITING on, and a row that had already fired was
            # not something it was waiting on.
            # NOT `rows`: that name is the message-id map this function returns
            # as `Written.rows`, and shadowing it would return a store there.
            reminder_store = SqlReminderStore(engine)
            base = datetime.fromtimestamp(now_ms / 1000, tz=UTC)
            for seed in knowledge.reminders:
                await reminder_store.create(
                    Reminder(
                        # v4, not derived from the text: two cases seeding the
                        # same reminder text must be two rows, and a
                        # content-derived id would silently make them one
                        # under the primary key.
                        id=uuid.uuid4(),
                        requester_person_id=seed.requester_person_id,
                        platform=platform,
                        conversation_id=conversation_id,
                        conversation_type="group",
                        text=seed.text,
                        created_at=base - timedelta(seconds=seed.created_seconds_ago),
                        due_at=base + timedelta(seconds=seed.due_in_seconds),
                        state=ReminderState.PENDING,
                        fired_at=None,
                        outcome=None,
                        kind=seed.kind,
                        payload=seed.payload,
                    )
                )
        if knowledge.culture is not None:
            # `established_by` is NOT NULL on `culture_revision`
            # (`nora/infra/sql/schema.py`), so it has to be something; the
            # other two provenance columns are nullable and are deliberately
            # left null rather than filled with a plausible-looking digest and
            # model name. `"case-seed"` is the same value the notes above use —
            # one string for "this row came from a case statement, not from a
            # fold", so one grep finds every seeded row in a shared database.
            await SqlCultureStore(engine).append(
                # The room's `primary_id` (DEC-181), resolved at `obtain()`.
                key=subject.culture_key if room is None else conversation_id,
                body=knowledge.culture,
                written_at=now_ms,
                established_by="case-seed",
            )
        if room is None:
            # THE CHARACTER BLOCKS' ACCOUNTS, under the keys production reads
            # them by (`unit_culture_key`, `character_key`) and each at the
            # age it had, written only for the subject's own room: a unit's
            # and a person's account is per unit, not per `room`.
            culture_store = SqlCultureStore(engine)
            if knowledge.unit_culture is not None:
                await culture_store.append(
                    key=unit_culture_key(subject.unit),
                    body=knowledge.unit_culture.body,
                    written_at=now_ms - round(knowledge.unit_culture.age_hours * _HOUR_MS),
                    established_by="case-seed",
                )
            if knowledge.lessons:
                lesson_store = SqlLessonStore(engine)
                # STAGGERED by a millisecond each, oldest first: the block is
                # ordered by `created_at`, and a shared one falls back to a
                # random UUID, so the order would change draw to draw.
                for i, body in enumerate(knowledge.lessons):
                    await lesson_store.create(
                        unit=subject.unit, body=body, citations=("case-seed",),
                        written_at=now_ms - (len(knowledge.lessons) - i),
                        established_by="case-seed",
                    )
            if knowledge.nora_account is not None:
                await culture_store.append(
                    key=nora_character_key(subject.unit),
                    body=knowledge.nora_account.body,
                    written_at=now_ms - round(knowledge.nora_account.age_hours * _HOUR_MS),
                    established_by="case-seed",
                )
            for person_id, account in knowledge.characters.items():
                await culture_store.append(
                    key=character_key(subject.unit, person_id),
                    body=account.body,
                    written_at=now_ms - round(account.age_hours * _HOUR_MS),
                    established_by="case-seed",
                )
    finally:
        await engine.dispose()
    return Written(now_ms=now_ms, rows=rows)

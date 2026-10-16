"""The culture tier's only moving part — `CultureAdaptor`.

**The gap this closes.** A window judged against nothing is judged in a
vacuum. "Nobody picked this up" means nothing until you know whether this room
normally picks things up. A baseline turns a window into a DEVIATION, and a
deviation is readable with no reasoning budget because the reasoning happened
offline. (The Relevance Gate's model runs with `reasoning_effort = "none"`;
handed a taxonomy of `go` shapes it pattern-matches, and a pattern-matcher
handed ambiguity takes the safe label.)

**What it is, and almost nothing else.** A Dreamer adaptor. Everything it needs
already exists: the runtime owns the loop, the tool ceiling, the budget, its own
doctrine and R-18/R-28 screening, and it stamps `instruction_sha256`, `model`
and `established_by` onto every `Proposal` — so provenance is not this tier's
problem, and `accept` returns payloads precisely so an adaptor never has to know
its own instruction digest.

**It synthesises ACROSS the stores Nora holds; the batch is one input among
several (DEC-182).** What DEC-182 rules out is the CACHE shape — ask Honcho
once, store the answer, do nothing else — not the use of memory. Honcho does not
scope at the layer a cached `chat` answer would need: `chat` on a two-message
probe session answers freely about people appearing nowhere in it, and `search`
scoped to that session returns other rooms' hits byte-identical to the unscoped
query. What survives is the ATTRIBUTION: **producing an account is a property of
asking a model to write prose, not a property of that endpoint**, so the fold is
the author and the dialectic call is not on this path.

**What it reads.** `record.since(conversation_id=key, ...)` — `WHERE
conversation_id = :key` in our own Postgres, hard-scoped by construction — is
the batch. Beside it: the account written last time, the note index (what the
room has OPEN), the operator's description of the room, and the read verbs in
`CULTURE_TOOLS`. **The point is the join.** The transcript can say two people
talked about going out for 宵夜; memory can say what the room has done before,
the roster who a handle is. An adaptor handed one table and asked for a join
passes every gate the runtime owns, because a restated input leaves a row and a
row is what success looks like. The presence verbs are withheld because a
room's culture feeds accounts that render in rooms with other `location` flags
(see `CULTURE_TOOLS`).

**It writes the ROOM's account and nothing else.** People are their own
sessions, one per (unit, person), woken by their own evidence and keyed by unit
(`nora.language.character`); memory is a tool those sessions may call, never a
pre-fetch.

**It writes through a verb, not an envelope.**
The session calls `character_revise(entity, body, citations)`; code checks the
write AS IT IS MADE (every citation is a row id of the batch it was shown, the
body is within `BODY_MAX_CHARS`, the runtime's identifier and coordinate screen
on every argument) and answers a refusal the model can act on, or stages it.
`character_no_change` is the other way to settle the room. The runtime stamps
what was staged and `commit` appends it with its citations. A session that
settles nothing holds its batch.

**Nothing here retires (DEC-183)** and **`body` is prose with no section schema
(DEC-184)** — both are stated where they bite, in `nora.domain.culture`. DEC-184
stands under the verb: a citation is per ACCOUNT, not per line, so nothing in
the write needs a section to hang it on.
"""

from __future__ import annotations

import datetime as _dt
import logging
from collections.abc import Callable, Sequence
from typing import Protocol

from nora.domain.blocks import (
    BlockKey,
    ContextBlock,
    assemble,
    render_row_window,
    restamp,
    speaker_name,
)
from nora.domain.culture import (
    CULTURE_OUTCOMES,
    Citable,
    CultureProposal,
    CultureRevision,
)
from nora.domain.models import TranscriptRow
from nora.domain.notes import Note
from nora.domain.ports import Proposal
from nora.domain.prompt import escape
from nora.language.character_session import AccountNotRecordableError, EntitySessions
from nora.language.note_render import render_notes
from nora.obs.metrics import Metrics

log = logging.getLogger(__name__)

__all__ = [
    "CULTURE_ADAPTOR_NAME",
    "CULTURE_OUTCOMES",
    "CULTURE_TOOLS",
    "STORE_CALLS_PER_TICK",
    "AccountNotRecordableError",
    "CultureAdaptor",
]


#: The adaptor's `name`: the metric label, the schedule and budget key, and the
#: `AdaptorGrant` key the composition root reads — one constant, because a name
#: spelled in four places is a name that drifts in three.
CULTURE_ADAPTOR_NAME = "culture"

#: `DreamAdaptor.store_calls_per_tick`. `inputs` holds two — the previous
#: account and the note index — and `commit` two, a `head` and an `append`.
#: `tests/architecture/test_dream_store_calls_are_declared.py` counts them off
#: this module's AST and reddens when a call is added without this number
#: moving.
STORE_CALLS_PER_TICK = 4

#: Every value `nora_culture_fold_total{outcome}` may carry, DERIVED rather than
#: hand-copied into the metric's help text, and enforced at RUNTIME because
#: `Metrics.inc` takes `**labels: object` and mypy
#: cannot check a string passed through an `object` parameter.
#:
#: The outcomes are distinct facts about one fold that the runtime's own
#: `nora_dream_*` series cannot tell apart: `kept`, `unchanged` and `refused`
#: all leave a run looking like `nothing_found`, and the difference between
#: "the model rewrote the account" and "the model left it alone" is exactly what
#: an operator needs.
#:
#: **What it cannot support is a RATE.** One fold per room per wake is a handful
#: of increments a day, this process restarts 2-3x daily, and Prometheus labels
#: lazily — so a label is destroyed at every restart and only re-created by the
#: next increment of THAT value. An absent series therefore means "nothing since
#: the last restart", never "never happened". The durable per-outcome row that
#: WOULD carry a rate is excluded (DEC-179); `log.warning` on `refused` and
#: `unsettled` carries the news meanwhile, which is the half a counter cannot.
#:
#: Defined in `nora.domain.culture`, so the metric's help text reads the same
#: set.

#: The read verbs one fold holds.
#:
#: **Every name here is in `ToolRegistry.read_only_tools()`** (derived from
#: `NativeTool.read_only`), so the Dreamer's ceiling admits all of them and
#: none of them can act. They are reachable only because the composition root
#: also gives this adaptor an `AdaptorGrant(home_room=True)`
#: (`nora.agent.dreamer.culture_grant`): without the grant the `Room` handed to
#: `ToolRegistry.build` has no roster source, `view.available` is `False`, and
#: every room-scoped verb below answers `unavailable` forever. Ungranted is not
#: unreachable.
#:
#: The test for whether a verb belongs here: **name the second store this
#: objective would consult if it could.** Each line below names one.
CULTURE_TOOLS: frozenset[str] = frozenset({
    # MEMORY — what Nora has already CONCLUDED about these people, which is a
    # different corpus from what they said in this batch. The objective asks
    # the model to say what the room comes to her for and what it never
    # would; that is a claim about a relationship across months, and the batch
    # is a few days.
    "ask_memory", "search_memory",
    # The same reach on the graph surface (DEC-316): built instead of the two
    # above while `[memory_index] enabled`, never beside them.
    "memory_search",
    # THE ROSTER — who is actually in this room, and who a handle IS. A batch
    # renders display names; an account that calls one person by two names, or
    # two people by one, is wrong in the way nobody notices. A room's own
    # nicknames («@熱咖啡☕少奶多糖») resolve against an index
    # (`lookup_person`) and not against more occurrences of the same handle.
    "about", "lookup_person", "group_members",
    # PRESENCE IS OUT (`where_been`, `online_now`). A room's culture is
    # INPUT to accounts that render beyond its room: every person session of
    # the unit and the unit's own account read it, and those render in rooms
    # whose `location` flags and DisclosureGuard outcomes differ. Accounts are
    # prose with no sections (DEC-184), so nothing could drop a
    # presence-derived line at render, and `[lineage]` is off by ruling. The
    # rule that cannot leak is that no Character account rests on a presence
    # read at all (R-31). Upgrade path: sectioned accounts with per-section
    # presence lineage.
    # THE OPEN INTERNET — these earn their place BECAUSE
    # the pass is slow (DEC-288). When a room leans on a reference nobody explains — a
    # game, a meme, a place — the loop can find out what it is, so the account
    # can say WHY a name landed rather than merely that it did. A name whose
    # origin the account can explain is the objective's own stated bar.
    "web_search", "web_fetch",
    # --- DELIBERATELY ABSENT, each for its own reason ------------------------
    # Anything that WRITES: the read-only ceiling refuses it and the grant
    # names no `acts`, so it is refused twice (`culture_grant`).
    # `load_transcript`: the adaptor is HANDED its room's batch. A verb that
    # re-reads the same room is turns spent to arrive where it started.
    # `route` / `transit` / `parking_status` / `find_parking` / `weather` /
    # `suggest_venue`: these answer a question somebody is asking NOW. An
    # account is about what a room IS, and a background pass that looked up
    # tonight's traffic would be doing the participation turn's job a day
    # early.
})


class _NoteStore(Protocol):
    """The note tier's index — what the room has OPEN. Notes are a UNIT
    store (DEC-105), so a scope key must be translated to its unit.

    Not to be confused with the per-room `standing` tier, which this fold does
    not read (`CultureAdaptor.inputs`): `NoteStore.standing()` is the index of
    open notes.
    """

    async def standing(self, *, unit: str) -> Sequence[Note]: ...


class _CultureStore(Protocol):
    """The two verbs this adaptor needs. NOT `DreamSink`: that port's
    `propose`/`supersede` pair exists to express «this replaces that», and
    nothing in this tier ever supersedes anything (DEC-183). An append-only
    store modelled as a sink would have a verb it must never call, which is a
    verb somebody eventually calls."""

    async def head(self, *, key: str) -> CultureRevision | None: ...

    async def append(
        self, *, key: str, body: str, written_at: int, established_by: str,
        instruction_sha256: str | None = ..., model: str | None = ...,
        citations: tuple[str, ...] = ...,
    ) -> CultureRevision: ...


#: NO timestamps, and the stored ROW ID as `n`. The clock stays out for the
#: objective's load-bearing clause, «not dated observations»: a model handed
#: `12 Sep 14:03` writes about the 12th; handed a name and a line it
#: writes about the room. The id is in because an account CITES what it
#: rests on and the verb refuses a citation that is not a row it was shown
#: (`nora.domain.culture.check_revision`), so the handle must be the row's own
#: id, never a position (`render_row_window`'s rule).


def _batch_day(batch: Sequence[TranscriptRow], *, tz: _dt.tzinfo) -> int | None:
    """`yyyymmdd` for the newest row of a batch, in the ROOM's zone — what the
    note index renders its distances against.

    Its own small helper rather than an import of a private name from a
    sibling module.
    """
    if not batch:
        return None
    day = _dt.datetime.fromtimestamp(max(r.ts for r in batch) / 1000, tz=tz).date()
    return day.year * 10000 + day.month * 100 + day.day


def _render_batch(batch: Sequence[TranscriptRow]) -> str:
    """One message, one element, `n` its stored row id — `render_row_window`,
    the renderer every pass that cites rows back uses. No `age`: the culture
    fold reads a batch for how a room TALKS, and a clock on each line would
    invite it to reason about when instead."""
    return render_row_window(
        batch,
        row_id=lambda r: r.id,
        # `or "unknown"`, NEVER `or row.sender_id` — the raw id is an E.164 or
        # an ACI and this block is a prompt (R-18, DEC-80).
        attrs=lambda r: (("from", speaker_name(r.person_name, r.sender_name)),),
        body=lambda r: r.body or "",
    )


#: The entity name a room session writes its account under
#: (`character_revise(entity="room", ...)`), its one entity.
ROOM_ENTITY = "room"


#: What this pass carries, declared as data rather than left as the `parts`
#: list `CultureAdaptor.inputs` builds by hand.
#:
#: `inputs` also renders `notes` (via `nora.language.note_render.render_notes`)
#: when its input resolves, shared with other occasions: a block built by a call
#: to a shared function, not a `ContextBlock(name=...)` literal.
#:
#: `inputs` hands its blocks to `assemble()`, so `order` IS the order the model
#: reads; the goldens in `tests/unit/test_dreamer_assembly_goldens.py` pin it
#: byte for byte. `key="turn"` throughout: no established cache boundary,
#: nothing routes on `key` yet either way.
_BLOCKS: tuple[tuple[str, int, BlockKey], ...] = (
    ("room", 10, "turn"),
    ("previous_account", 20, "turn"),
    ("notes", 40, "turn"),
    ("room_messages", 50, "turn"),
)

#: `name -> (order, key)`, derived so no call site restates a row `_BLOCKS`
#: already gives it.
_ROW: dict[str, tuple[int, BlockKey]] = {
    name: (order, key) for name, order, key in _BLOCKS
}


class CultureAdaptor(EntitySessions):
    """One account per room per wake (DEC-288), appended to a chain — see the module
    docstring. Its write verbs and settle rule are `EntitySessions`'."""

    name = CULTURE_ADAPTOR_NAME
    key_kind = "scope"
    #: **`True`**: the fold advances a watermark. Without one there is no
    #: clock event to fire on — nothing here retires (DEC-183: «a quiet room's
    #: culture is accurate, not stale»), so a time-driven refresh would have
    #: nothing to refresh against.
    #:
    #: The consequence is that a SILENT ROOM DOES NOT FIRE, and that is correct
    #: rather than a defect. Re-running on silence buys either a duplicate
    #: account or pure model variance, and the second corrupts the one signal
    #: the revision chain carries.
    uses_watermark = True
    objective = "culture"
    #: Unread: this adaptor answers through `verbs`, never an envelope
    #: (`DreamerRuntime._sample`). Kept because `DreamAdaptor` declares it.
    envelope_key = "account"
    #: READ VERBS — see `CULTURE_TOOLS` for what each is for and what is
    #: deliberately absent.
    tools: frozenset[str] = CULTURE_TOOLS
    store_calls_per_tick = STORE_CALLS_PER_TICK

    def __init__(
        self,
        *,
        culture: _CultureStore,
        # THE POLICY RESOLUTIONS. `key` is a `Conversation.primary_id`, and
        # every one of these is the same `.get` over the allowlist that the
        # composition root builds off `conv.primary_id` in one place, so they
        # cannot disagree about which room a key names.
        #
        # REQUIRED rather than defaulted: a wiring gap here renders a
        # plausible, WRONG prompt and nothing counts it.
        unit_for: Callable[[str], str | None],
        label_for: Callable[[str], str | None],
        description_for: Callable[[str], str | None],
        # `None` is a real configuration, not a gap: `[notes] enabled = false`
        # is supported, and an absent store renders no block rather than an
        # empty one. The difference from a store that RAISES is the whole of
        # R-27 and is handled in `inputs`.
        notes: _NoteStore | None = None,
        metrics: Metrics | None = None,
        # The ROOM's zone, for the note index's distances — the
        # `[reminders] timezone`: «this Saturday» is a question about
        # the room's calendar, not the process's. UTC by default so a bare
        # construction still renders a true day.
        timezone: _dt.tzinfo = _dt.UTC,
        # THE ROOM'S PLATFORM and A NAME RESOLVER: this
        # fold builds its own `<room_messages>` block from the raw batch
        # (`_render_batch`) and never calls `Persons.name_transcript`, so
        # without resolution a mapped sender's line would be attributed to their
        # self-set, spoofable `sender_name` instead of the operator's canonical
        # name for them.
        # `None` (unwired, or the room maps to no platform) skips
        # resolution: `_render_batch`'s `speaker_name` already never renders
        # a raw id either way, so an unwired composition only loses the
        # canonical name, never gains a leak.
        platform_for: Callable[[str], str | None] | None = None,
        resolve_names: (
            Callable[[Sequence[TranscriptRow], str], Sequence[TranscriptRow]] | None
        ) = None,
    ) -> None:
        self._culture = culture
        self._notes = notes
        self._unit_for = unit_for
        self._label_for = label_for
        self._description_for = description_for
        self._metrics = metrics
        self._tz = timezone
        self._platform_for = platform_for
        self._resolve_names = resolve_names
        # The verbs' per-run state (`EntitySessions`): what the run in flight
        # may cite, what it staged, and the bounded run of unsettled sessions.
        self._sessions_init()

    def _count(self, outcome: str) -> None:
        self._inc(outcome)

    def _inc(self, outcome: str) -> None:
        if outcome not in CULTURE_OUTCOMES:
            raise ValueError(
                f"nora_culture_fold_total{{outcome={outcome!r}}} is not in this "
                f"metric's vocabulary {sorted(CULTURE_OUTCOMES)}. A typo here "
                f"creates a new series rather than an error, and the old one "
                f"silently goes flat"
            )
        if self._metrics is not None:
            self._metrics.inc("nora_culture_fold_total", outcome=outcome)

    # -- inputs ---------------------------------------------------------

    async def inputs(self, *, key: str, batch: Sequence[TranscriptRow]) -> str:
        """Which room this is, the account written last time, what the room
        has open, and the room's own messages.

        **The `<room>` block comes first** and frames everything under it: what
        the room is CALLED and what the operator says it IS. Without it nothing
        in the prompt says what kind of room is being read, and an account is a
        claim about a room: a prompt that never names the room is asking for a
        claim about nothing.

        **Then the previous account**, because it is how everything after it is
        meant to be read: the model is continuing an account, not writing one
        from nothing, and material handed over with no prior context produces a
        fresh snapshot every day — exactly the thing the chain exists to be
        instead of.

        **Then the note index**: an
        account of how a room works that cannot see what the room currently has
        OPEN is inferring a thing another store already states. **A question
        whose input cannot ground it does not fail loudly** — the model answers
        anyway, in prose, at a rate nothing reports.

        **NOT the room's `standing`.**
        `standing` is folded from the turn's raw tool
        results (`nora.engagement.stages.debrief`), a `where_been` or
        `online_now` among them, and the debrief is told to record who is
        present. A room's account is input to the accounts of every person
        of the unit and to the unit's own, which render in rooms whose
        `location` flags differ, so a line that reached the account through
        `standing` would reach them (R-31). Prose carries no mark to drop it by
        (DEC-184). The account rests on the room's own messages, which it cites.

        **The read is UNGUARDED, and that is the fail-closed side.** A store
        that raises takes `inputs` with it, the runtime reports the run
        `unavailable`, and the watermark is HELD — the batch was not folded, so
        it is folded later instead.

        `batch` empty renders `""`. `DreamAdaptor` has no `should_fire` verb;
        the runtime's fire condition IS "did `inputs` render anything", so a
        quiet room must cost nothing — and nothing above is read on that path.
        """
        if not batch:
            return ""
        # THE RUN: it may cite this batch's rows, and it writes one entity,
        # the room.
        self._begin(
            key, citable=Citable(messages=frozenset(row.id for row in batch)),
            entities={ROOM_ENTITY: None}, batch_start=batch[0].id,
        )
        # RESOLVE NAMES. Every row this fold gets from `record.since` carries
        # `person_name=None` — nothing on this path ever called
        # `Persons.name_transcript` — so without this a mapped sender's line in
        # `<room_messages>` would be attributed to their self-set, spoofable
        # `sender_name` instead of the operator's name for them.
        # `_render_batch`'s `speaker_name` never renders a raw id either way;
        # this is what makes the MAPPED case correct rather than merely
        # non-leaking.
        # A RAISING RESOLVER DEGRADES, IT NEVER SKIPS THE FOLD.
        # `resolve_names` is `Persons.name_transcript`, dict lookups only, so
        # this should never fire — but this adaptor's own fail-closed rule is
        # for a STORE this pass depends on (`inputs`'s own docstring); a name a
        # batch line is merely attributed BY is not one, and treating a resolver
        # fault as unavailable would hold the watermark and re-read the same
        # batch forever over a failure the fold does not need to answer. Caught
        # here, at the one call site, rather than inside `_render_batch`, which
        # has no logger or metrics collaborator.
        if self._resolve_names is not None:
            platform = self._platform_for(key) if self._platform_for is not None else None
            if platform is not None:
                try:
                    batch = tuple(self._resolve_names(batch, platform))
                except Exception:
                    log.exception(
                        "culture: resolve_names raised for scope=%s "
                        "platform=%s; rendering the batch on the "
                        "pre-resolution speaker_name floor instead",
                        key, platform,
                    )
                    if self._metrics is not None:
                        self._metrics.inc(
                            "nora_dream_name_resolution_failed_total",
                            adaptor=self.name,
                        )
        parts: list[ContextBlock] = [self._room_block(key)]
        # The fold's own prior account, so it can see what it wrote and supersede it.
        previous = await self._culture.head(key=key)
        # AN UNCITED ACCOUNT IS NOT HANDED OVER (R-31). One with no citations
        # predates the verb (migration `0098_culture_citations`): it was
        # written by the envelope fold, while the pass read `<standing>`,
        # `where_been` and `online_now`. A revision that CONTINUED that text
        # would carry its lines forward and gain citations, and so pass the
        # "has citations" filter of `character._room_cultures` into the person
        # and unit sessions, whose accounts render in rooms with different
        # `location` flags. The room is re-written from its own messages.
        if previous is not None and not previous.citations:
            previous = None
        if previous is not None:
            account_order, account_key = _ROW["previous_account"]
            parts.append(
                ContextBlock(
                    name="previous_account", order=account_order, key=account_key,
                    # HERS, not the room's — the account she wrote last time.
                    # The marker names the wrong axis, so this block drops it.
                    body=escape(previous.body),
                )
            )
        # WHAT THE ROOM HAS OPEN: the note store is keyed by UNIT (DEC-105),
        # so a scope key must be resolved and never guessed.
        unit = self._unit_for(key)
        if self._notes is not None and unit is not None:
            index = render_notes(
                await self._notes.standing(unit=unit),
                # WITH TODAY, so each entry renders a DISTANCE («in 3 days»)
                # rather than a bare `20260920`. `render_notes` falls back to
                # the raw anchor for a caller with no clock, and a raw calendar
                # date is the one shape this prompt is built to keep out: the
                # batch is rendered with no timestamps precisely so the model
                # writes about the room rather than about the 12th
                # (`_render_batch` passes no `timestamp`), and a date
                # reintroduced one block up would
                # undo that for free. Today is the ROOM's day, from the newest
                # row of the batch in the room's own zone — never a wall clock,
                # so «today» here cannot disagree with the material below it.
                today=_batch_day(batch, tz=self._tz),
            )
            if index is not None:
                # RESTAMPED: `render_notes` is shared with the tag path and
                # builds an unstamped block.
                parts.append(restamp(index, _ROW))
        if unit is None:
            # A key that names no policy conversation. Not silent — but not
            # counted here either, because `RegistryToolSource.build` counts
            # and logs a fault for this key on this same run
            # (`nora_dream_home_room_missing_total`, the scope-keyed branch),
            # and a second counter would mostly be the same fact under a
            # second name.
            #
            # **MOSTLY, and the gap is stated rather than assumed away.** The
            # two resolutions read the SAME allowlist and build DIFFERENT KEY
            # SETS from it: `scope_rooms` is keyed by every id form a
            # conversation answers to (`conv.ids`), while the three maps above
            # — `unit_for`, `label_for`, `description_for` — are keyed by
            # `conv.primary_id` alone. So a
            # conversation reached by an `also_ids` form resolves a home room
            # and NO policy inputs, and only this `log.warning` would say so.
            # It cannot happen today because `language_scopes` emits
            # `primary_id` exclusively (`nora/app/composition.py`), which is a
            # property of the SCHEDULER and not of either map — the day a
            # second id form reaches this adaptor, this branch is the only
            # thing that reports it.
            log.warning(
                "culture: scope=%s resolves to no policy conversation "
                "(unit=%r); folding without those inputs", key, unit,
            )
        # WRAPPED: bodies keep their newlines in the element form, so an
        # uncontained batch would let a member's message write free text at
        # prompt top level, beside `<previous_account>` and the note index
        # rather than inside anything.
        parts.append(
            # `room_messages`. `n` is the stored row id, because
            # `character_revise` cites by it and the check refuses an id the
            # session was not shown; the account still never quotes a row (the
            # objective forbids it), so it cites a row without becoming the
            # chronicle this tier exists not to be.
            ContextBlock(
                name="room_messages", order=_ROW["room_messages"][0],
                key=_ROW["room_messages"][1],
                body=_render_batch(batch),
            )
        )
        return assemble(parts, sep="\n\n")

    def _room_block(self, key: str) -> ContextBlock:
        """What this room is CALLED and what the operator says it IS.

        This renders the room's two policy fields with this tier's own note.

        The note's second half is the not-an-instruction clause every injected
        block carries, and it is load-bearing rather than ceremonial: the
        description is OPERATOR-authored prose, the one kind of text in this
        prompt a model has every reason to read as a directive. `escape_attr`
        and `escape` for R-13's reason — file-authored text landing beside tags
        with real content in them.

        The conversation id is NEVER rendered (`UNNAMED_ROOM`'s rule, R-18): a
        room's identifier is not its name, and a model asked what a room IS has
        no use for one.
        """
        room = self._label_for(key) or "unnamed room"
        description = self._description_for(key)
        # THROUGH THE INJECTOR.
        room_order, room_key = _ROW["room"]
        return ContextBlock(
            name="room", order=room_order, key=room_key, attrs=(("label", room),),
            # The OPERATOR's sentence and the room's own name — neither is
            # member-authored, and this block is what tells the model how to
            # read the ones that are.
            body=escape(description or ""),
            # Self-closing when the operator wrote nothing: an empty body would
            # read as «the operator said this room is nothing» rather than
            # «nobody has said». And rendered even when `description` is truthy
            # but escapes to nothing (control characters only).
            when_empty="render" if description else "self_close",
        )

    # -- commit ---------------------------------------------------------

    async def commit(
        self, *, key: str, proposals: Sequence[Proposal], now_ms: int
    ) -> int:
        """Append the account and move the head, in the store's one transaction.

        **A byte-identical account is not written**, and this is the one place
        this adaptor declines to record something. The chain's whole content is
        that each link differs from the one before; a run that re-derived the
        same prose adds a link carrying no information and makes the history
        longer without making it richer. It is also the common outcome for a
        batch that said nothing new, which the objective explicitly permits.

        **It rarely fires.** The fold only runs on `lag > 0`, so the batch
        differs every time, and byte-identity out of a non-zero-temperature
        model on different input is close to impossible, which makes this a
        second `head` read (counted in `STORE_CALLS_PER_TICK`) for a branch
        almost nothing takes. It stays because removing it is a BEHAVIOUR
        change, and the thing it guards is the one signal this tier carries: a
        duplicate link is indelible. A live
        `nora_culture_fold_total{outcome="unchanged"}` that stays at zero
        across a real window would be the measurement to remove it on.

        The parent is chosen inside `append`, never here: the Dreamer dispatches
        scopes as concurrent tasks, so a parent captured before the model call
        is a parent that may have moved.
        """
        written = 0
        for proposal in proposals:
            payload = proposal.payload
            if not isinstance(payload, CultureProposal):
                raise TypeError(
                    f"CultureAdaptor.commit only accepts a CultureProposal "
                    f"payload, got {type(payload).__name__}"
                )
            previous = await self._culture.head(key=key)
            if previous is not None and previous.body == payload.body:
                self._inc("unchanged")
                continue
            await self._culture.append(
                key=key,
                body=payload.body,
                written_at=now_ms,
                established_by=proposal.established_by,
                instruction_sha256=proposal.instruction_sha256,
                model=proposal.model,
                citations=payload.citations,
            )
            self._inc("written")
            written += 1
        return written

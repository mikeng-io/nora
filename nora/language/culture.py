"""The culture tier's only moving part — `CultureAdaptor` (design/culture-tier).

**The gap this closes.** The Relevance Gate admitted about 1% of windows over
seven days, twice measured. That is not a mistuned threshold: `participation.md`
is a taxonomy of `go` shapes read by a model configured
`reasoning_effort = "none"`, and a reasoning-less model handed a taxonomy
pattern-matches, while a pattern-matcher handed ambiguity takes the safe label.
The operator's framing was that you cannot define when she should intervene —
so stop defining it. **A window judged against nothing is judged in a vacuum.**
"Nobody picked this up" means nothing until you know whether this room normally
picks things up. A baseline turns a window into a DEVIATION, and a deviation is
readable with no reasoning budget because the reasoning happened offline.

**What it is, and almost nothing else.** A Dreamer adaptor. Everything it needs
already exists: the runtime owns the loop, the tool ceiling, the budget, its own
doctrine and R-18/R-28 screening, and it stamps `instruction_sha256`, `model`
and `established_by` onto every `Proposal` — so provenance is not this tier's
problem, and `accept` returns payloads precisely so an adaptor never has to know
its own instruction digest.

**It synthesises ACROSS the stores Nora holds; the batch is one input among
several (DEC-182 as narrowed 2026-09-17).** This paragraph said *"it reads its
own batch and asks nothing of the memory system"* until then, and that reading
is withdrawn: **what DEC-182 killed is the CACHE shape** — ask Honcho once,
store the answer, do nothing else — **not the use of memory.** An earlier design
called Honcho's dialectic `chat` and cached its answer; it was built and closed
unmerged (#237) because Honcho does not scope at the layer this needs — `chat`
on a two-message probe session answers freely about people appearing nowhere in
it, and `search` scoped to that session returns other rooms' hits byte-identical
to the unscoped query. What survives of that decision is the ATTRIBUTION:
**producing an account is a property of asking a model to write prose, not a
property of that endpoint**, so the fold is the author and the 17-30s dialectic
call is not on this path.

**What it reads.** `record.since(conversation_id=key, ...)` — `WHERE
conversation_id = :key` in our own Postgres, hard-scoped by construction — is
the batch. Beside it: the account written last time, the room's own `standing`
(what her position here actually IS, rather than what a transcript implies), the
note index (what the room has OPEN), the operator's description of the room, and
nine read verbs through `CULTURE_TOOLS`. **The point is the join.** The
transcript can say two people talked about going out for 宵夜; `where_been` can
say three others were co-located that Saturday, so it was the group; `online_now`
can say a fourth was at home gaming. The culture is not "those two eat late", it
is "this group goes out on Saturdays, and that one stays home" — and no single
store holds that (`design/dreamer-agent-loop/03-no-agent-loop §7.2`). Until
2026-09-17 this adaptor shipped `tools = frozenset()` and rendered two things,
so it was asked for a join and handed one table: §7.1's tautology, which passes
every gate the runtime owns because a restated input leaves a row and a row is
what success looks like ([17-failure-classes §11]).

**It is also the Character adaptor (DEC-278, operator ruling 2026-10-03).** On
the same wake (DEC-288), the same fold asks Nora's memory «what is the character of
X?» for each person the room lists who wrote in the batch (DEC-280: only
those; `memory.chat`, the dialectic verb
`ask_memory` calls), hands those answers to the model beside the room's own
account, and stores one short account per person under `character_key` in this
same store and chain. **That supersedes DEC-182's narrowing, for this use
only:** Honcho's `chat` is not scoped to a room, which is what closed #237, and
the operator accepted that here, because a unit is one circle of friends. The
cache shape (ask once, store the answer verbatim) is still not what this is:
the model combines memory's view with the room's culture and the fold is the
author. Rendered as `<character>` by `nora.language.culture_render`.

**It writes through a verb, not an envelope** (design/self-growth/10 §3, C2a).
The session calls `character_revise(entity, body, citations)`; code checks the
write AS IT IS MADE (every citation is a row id of the batch it was shown, the
body is within `BODY_MAX_CHARS`, the runtime's identifier and coordinate screen
on every argument) and answers a refusal the model can act on, or stages it.
`character_no_change` is the other way to settle the room. The runtime stamps
what was staged exactly as it stamped an envelope's items, and `commit`
appends it with its citations. A session that settles nothing holds its batch.

**Nothing here retires (DEC-183)** and **`body` is prose with no section schema
(DEC-184)** — both are stated where they bite, in `nora.domain.culture`. DEC-184
stands under the verb: a citation is per ACCOUNT, not per line, so nothing in
the write needs a section to hang it on, and no section has yet been shown to
be needed.

**Consumed by nobody, on purpose.** `02-measurement §5` sequences this as step
4: writing only, no behaviour change, so the chain accumulates real revisions
before any prompt reads it. A tier judged on its first snapshot is judged on the
one thing it does not contribute.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
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
    MAX_CONSECUTIVE_REJECTIONS,
    Citable,
    CultureProposal,
    CultureRevision,
    Refusal,
    character_key,
    check_revision,
)
from nora.domain.errors import NoraError
from nora.domain.models import PersonRef, TranscriptRow
from nora.domain.notes import Note
from nora.domain.ports import MemoryStore, Proposal
from nora.domain.prompt import escape
from nora.domain.standing import Standing
from nora.language.note_render import render_notes
from nora.language.standing_render import render_standing
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


class AccountNotRecordableError(NoraError):
    """The model wrote one account, it was read, and it cannot enter the chain.

    **Raised so the BATCH IS NOT CONSUMED**, which is the whole point of it
    being an exception rather than an empty return. `DreamerRuntime.run`
    reports an adaptor raising as `unavailable`, and `_dream_once` holds the
    watermark on `unavailable` alone — so the messages this fold read are
    folded again tomorrow instead of being spent on an account nobody kept.

    **The failure this closes, traced end to end.** `accept` returning `()`
    for an oversize body gave: no proposal -> `commit(key, ())` -> status `ok`
    -> the watermark advances. The account was discarded, the batch was
    consumed, and the ONLY record was
    `nora_culture_fold_total{outcome="oversize"}`, a counter whose own comment
    concedes it cannot support a rate because the label is destroyed at every
    process restart. A room whose fold habitually runs long would therefore
    have written nothing, forever, and nothing durable would have said so.

    **It is `unavailable` rather than `nothing_found` for R-27's reason, not
    as a convenience.** `nothing_found` is the claim «there was nothing in this
    batch», and a run whose model returned an account is not entitled to make
    it — the same rule that stops an empty bus catalogue becoming 「冇呢條線」.
    `unavailable` is «I could not record this», which is what happened. The
    module docstring's own line: a batch that will not parse is `unavailable`,
    never `nothing_found`, *because the loop must not advance a watermark past
    a batch nobody read*. A batch whose one account was refused is that batch.

    **The cost, OBSERVED rather than named.** If a draw for a room overruns
    the cap, that fire is spent and the watermark does not move; while it keeps
    overrunning, `lag` grows. This paragraph called that an accepted trade that
    "self-heals on any compliant draw, because the next fire is a fresh sample
    at non-zero temperature". **That sentence is CORRECT and a rewrite of this
    docstring briefly claimed it was not** — the claim was made on 2026-09-17
    from a four-fire window, while the fifth fire was still pending. It landed:
    the deployment's busiest room drew 1,876, 1,725, 1,677 and 1,583 against a
    1,400 cap, then 1,323, which was recorded. A Signal room did the same two
    fires earlier. Both self-healed exactly as the original sentence said.

    What the window DID show is the price of the healing. At the refusal rate
    measured that evening — 6 of 11 draws over the cap — a room re-draws for a
    geometrically distributed number of ticks and pays a model call each time;
    zone 42 paid four. Loud, it was: `nora_dream_run_total{status=
    "unavailable"}`, a growing lag and a WARNING naming the scope. Loud and
    self-healing still is not free. What makes the trade cheap is
    `BODY_MAX_CHARS` being set where an account written to the objective's own
    instruction rarely reaches it — a claim about what the model EMITS, which
    is why the cap is now sized against `ACCOUNT_CHARS_OBSERVED_MAX` rather
    than against a ratio to the worked example.

    **What would make the cap a quality instrument again is in the OBJECTIVE,
    and the objective is `change_policy: operator-only`.** The model is never
    told the cap — it is enforced after emission, on prose already written — so
    nothing in this module can stop a long account being produced, only stop it
    being lost. See the note in `CultureAdaptor.accept`.
    """

#: The adaptor's `name`: the metric label, the schedule and budget key, and the
#: `AdaptorGrant` key the composition root reads (`PROACTIVE_ADAPTOR_NAME`'s own
#: precedent — a name spelled in four places is a name that drifts in three).
CULTURE_ADAPTOR_NAME = "culture"

#: `DreamAdaptor.store_calls_per_tick`. `inputs` holds three — the previous
#: account, the room's `standing` position, the note index — and `commit` two,
#: a `head` and an `append`. `tests/architecture/test_dream_store_calls_are_
#: declared.py` counts them off this module's AST and reddens when a call is
#: added without this number moving.
#:
#: 3 -> 5 on 2026-09-17, when `standing` and the notes joined the inputs.
#: 5 -> 6 for the Character tier: ONE site, `memory.chat`, fanned out per person.
STORE_CALLS_PER_TICK = 6

#: Every value `nora_culture_fold_total{outcome}` may carry, DERIVED rather than
#: hand-copied into the metric's help text — the deleted `note_candidates`' own precedent,
#: enforced at RUNTIME because `Metrics.inc` takes `**labels: object` and mypy
#: cannot check a string passed through an `object` parameter.
#:
#: **KEPT 2026-09-17, deliberately, and with what it cannot support written
#: down.** It was flagged as unasked-for machinery and it nearly is — but these
#: five outcomes are five distinct facts about one fold and the runtime's own
#: `nora_dream_*` series cannot tell any of them apart: `too_many`, `oversize`
#: and `empty` all leave a run looking like `nothing_found`, and the difference
#: between "the model wrote two accounts" and "the model wrote none" is exactly
#: what an operator would need. Deleting it would trade a weak instrument for
#: none.
#:
#: **What it cannot support is a RATE, and that is measured rather than
#: feared.** One fold per room per day (DEC-185's clock, before DEC-288 made it
#: rarer) was ~6 increments a day here, this process
#: restarts 2-3x daily, and Prometheus labels lazily — so a label is destroyed
#: at every restart and only re-created by the next increment of THAT value
#: (`design/culture-tier 00-overview §1.0`: the `go` series was absent for five
#: days after three real increments). So an absent series means "nothing since
#: the last restart", never "never happened", and `§1.1`'s three clauses apply
#: to every reading of it. The durable per-outcome row that WOULD carry a rate
#: is DEC-179's, excluded there; `log.warning` on `too_many` and `oversize`
#: carries the news meanwhile, which is the half a counter cannot.
#:
#: Defined in `nora.domain.culture` since DEC-280 (2026-10-03), so the
#: metric's help text reads the same set; `dropped_person` and `abandoned`
#: joined it there.

#: The read verbs one fold holds — granted 2026-09-17, against
#: `tools = frozenset()` since the tier shipped.
#:
#: **Every name here is in `ToolRegistry.read_only_tools()`** (35 verbs, derived
#: from `NativeTool.read_only`), so the Dreamer's ceiling admits all of them and
#: none of them can act. They are reachable only because the composition root
#: also gives this adaptor an `AdaptorGrant(home_room=True)`
#: (`nora.agent.dreamer.culture_grant`): without the grant the `Room` handed to
#: `ToolRegistry.build` has no roster source, `view.available` is `False`, and
#: every room-scoped verb below answers `unavailable` forever. Ungranted is not
#: unreachable, and the two words are a built-and-closed PR apart
#: (`design/culture-tier 03-decisions` corrections 9 and 14).
#:
#: The test for whether a verb belongs here is
#: `03-no-agent-loop §7.5`'s: **name the second store this objective would
#: consult if it could.** Each line below names one.
CULTURE_TOOLS: frozenset[str] = frozenset({
    # MEMORY — what Nora has already CONCLUDED about these people, which is a
    # different corpus from what they said in this batch. `01-the-adaptor §2`
    # asks the model to say what the room comes to her for and what it never
    # would; that is a claim about a relationship across months, and the batch
    # is a few days.
    "ask_memory", "search_memory",
    # THE ROSTER — who is actually in this room, and who a handle IS. A batch
    # renders display names; an account that calls one person by two names, or
    # two people by one, is wrong in the way nobody notices. `lookup_person` is
    # here for the reason it was granted to the (deleted) proactive pass (issue #145): a
    # room's own nicknames («@熱咖啡☕少奶多糖») resolve against an index and
    # not against more occurrences of the same handle.
    "about", "lookup_person", "group_members",
    # PRESENCE ANALYTICS — the operator's worked example, and the verb this
    # grant exists for. The transcript shows two people talking about 宵夜;
    # `where_been` shows three of them were co-located that Saturday, so it was
    # the group. The conclusion is a property of the JOIN and no single store
    # holds it (`03-no-agent-loop §7.2`). **It was in NO adaptor's grant at all
    # before this** — presence analytics was closed to every Dreamer, which is
    # why §7.2's example could be written and not run.
    "where_been",
    # LIVE STATE — the other half of that example: who is around, and doing
    # what INSTEAD. Granted to the proactive adaptor on 2026-09-15 because of a
    # measured live failure of exactly this shape
    # (`nora/language/proactive.py:153-161`, design/online-status/04, #248): a
    # note saying a member was unwell and wanted the evening off was raised
    # into the home room while the group was mid-game, "because nothing in the
    # turn could observe the state contradicting the note". A culture account
    # inferring a habit from a transcript has the same blind spot one clock
    # tick wider. Its input domain here is THIS ROOM's own roster — the
    # widened decide-turn union is bound only for a unit-keyed build
    # (`RegistryToolSource.build`).
    "online_now",
    # THE OPEN INTERNET — `01-the-adaptor §2`: these earn their place BECAUSE
    # the pass is slow (days, DEC-288). When a room leans on a reference nobody explains — a
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


class _StandingReader(Protocol):
    """The per-room `standing` tier's one read (design/dreamer-agent-loop/26).

    Keyed `(platform, conversation_id)` because the debrief that writes it is
    handed the message that ARRIVED, while this adaptor is handed `scopes()`
    and so keys `primary_id` (`GLOSSARY.md`, DEC-181 withdrawn). The two are
    equal for every live conversation and that is an accident of data (#229),
    so the caller resolves one to the other through the POLICY rather than
    assuming they are one spelling — a read with the wrong key matches zero
    rows and renders as «this room has no standing», a real state that nothing
    anywhere reports as a miss.
    """

    async def read(
        self, *, platform: str, conversation_id: str,
    ) -> Standing | None: ...


class _NoteStore(Protocol):
    """The note tier's index — what the room has OPEN. the deleted `NoteCandidateAdaptor`
    read exactly this, keyed exactly this way (DEC-105: notes are a UNIT
    store, so a scope key must be translated).

    Note the vocabulary trap this bundle has fallen into before
    (`01-the-adaptor §1.2`): `NoteStore.standing()` is the index of open notes,
    a different thing from the per-room `standing` tier above. Two inputs, two
    stores, similar spelling.
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


#: NO timestamps, and since `character_revise` (design/self-growth/10 §3)
#: the stored ROW ID as `n`. The clock stays out for the objective's
#: load-bearing clause, «not dated observations»: a model handed
#: `2026-09-12 14:03` writes about the 12th; handed a name and a line it
#: writes about the room. The id is in because an account now CITES what it
#: rests on and the verb refuses a citation that is not a row it was shown
#: (`nora.domain.culture.check_revision`), so the handle must be the row's own
#: id, never a position (`render_row_window`'s rule, #314).


def _batch_day(batch: Sequence[TranscriptRow], *, tz: _dt.tzinfo) -> int | None:
    """`yyyymmdd` for the newest row of a batch, in the ROOM's zone — what the
    note index renders its distances against.

    Written as its own four-line helper (it was a copy of the deleted
    `nora.language.note_candidates`' `_batch_day`, DEC-277), rather than an import
    of a private name from a sibling module.
    """
    if not batch:
        return None
    day = _dt.datetime.fromtimestamp(max(r.ts for r in batch) / 1000, tz=tz).date()
    return day.year * 10000 + day.month * 100 + day.day


def _render_batch(batch: Sequence[TranscriptRow]) -> str:
    """One message, one element, `n` its stored row id — `render_row_window`,
    the renderer every pass that cites rows back uses. No `age`: the culture
    fold reads a batch for how a room TALKS, and a clock on each line invited
    it to reason about when instead."""
    return render_row_window(
        batch,
        row_id=lambda r: r.id,
        # `or "unknown"`, NEVER `or row.sender_id` — the raw id is an E.164 or
        # an ACI and this block is a prompt (R-18, DEC-80).
        attrs=lambda r: (("from", speaker_name(r.person_name, r.sender_name)),),
        body=lambda r: r.body or "",
    )


#: The entity name a room session writes its own account under
#: (`character_revise(entity="room", ...)`); a person is written under the name
#: the prompt showed for them.
ROOM_ENTITY = "room"


#: What this pass carries, declared as data
#: (`design/context-assembly/01-the-registry.md`) rather than left as the
#: `parts` list `CultureAdaptor.inputs` builds by hand.
#:
#: **Two rows the design bundle's own survey missed.** `00-overview.md`'s
#: table lists this occasion as `previous_account, room, room_messages` —
#: three — because it read `ContextBlock(name=...)` literals in this file
#: alone. `inputs` also renders `standing` (via
#: `nora.language.standing_render.render_standing`) and `notes` (via
#: `nora.language.note_render.render_notes`) when their inputs resolve, both
#: shared with other occasions. The same gap was found in `proactive`
#: (`loop_decisions`) and `note_candidates` (`notes`), both since deleted — a survey of literal
#: `ContextBlock(name=...)` calls misses a block built by a call to a shared
#: function.
#:
#: `inputs` hands its blocks to `assemble()`, so `order` IS the order the model
#: reads. The numbers were the append order of the hand join this replaced
#: (step 2a of `design/context-assembly/04-every-occasion.md`), and the goldens
#: in `tests/unit/test_dreamer_assembly_goldens.py` pin it byte for byte.
#: `key="turn"` throughout: no established cache boundary, nothing routes on
#: `key` yet either way.
_BLOCKS: tuple[tuple[str, int, BlockKey], ...] = (
    ("room", 10, "turn"),
    ("previous_account", 20, "turn"),
    ("standing", 30, "turn"),
    ("notes", 40, "turn"),
    ("people_views", 45, "turn"),
    ("person_view", 46, "turn"),
    ("room_messages", 50, "turn"),
)

#: `name -> (order, key)`, derived so no call site restates a row `_BLOCKS`
#: already gives it.
_ROW: dict[str, tuple[int, BlockKey]] = {
    name: (order, key) for name, order, key in _BLOCKS
}


class CultureAdaptor:
    """One account per room per wake (DEC-288), appended to a chain — see the module
    docstring."""

    name = CULTURE_ADAPTOR_NAME
    key_kind = "scope"
    #: **`True`, and an earlier draft had it `False`**, borrowed from the note
    #: curator (deleted 2026-10-03). The curator needed a watermark-free clock because AN ANCHOR
    #: DATE
    #: PASSING IS ITSELF THE EVENT — retirement is time-driven whether or not
    #: anyone speaks. Culture has no equivalent clock event, so the precedent
    #: does not transfer, and DEC-183 ("a quiet room's culture is accurate, not
    #: stale") already contradicted it.
    #:
    #: The consequence is that a SILENT ROOM DOES NOT FIRE, and that is correct
    #: rather than a defect: nothing here retires, so there is nothing to
    #: refresh against. Re-running on silence buys either a duplicate account or
    #: pure model variance, and the second corrupts the one signal the revision
    #: chain carries.
    uses_watermark = True
    objective = "culture"
    #: Unread: this adaptor answers through `verbs`, never an envelope
    #: (`DreamerRuntime._sample`). Kept because `DreamAdaptor` declares it.
    envelope_key = "account"
    #: NINE READ VERBS — see `CULTURE_TOOLS` for what each is for and what is
    #: deliberately absent. `frozenset()` until 2026-09-17, which made this an
    #: objective asked for a join and handed one table.
    tools: frozenset[str] = CULTURE_TOOLS
    store_calls_per_tick = STORE_CALLS_PER_TICK

    def __init__(
        self,
        *,
        culture: _CultureStore,
        # THE POLICY RESOLUTIONS. `key` is a `Conversation.primary_id`, and
        # every one of these is the same `.get` over the allowlist that the
        # composition root builds off `conv.primary_id` in one place, so they
        # cannot disagree about which room a key names —
        # the deleted `NoteCandidateAdaptor`'s arrangement (issue #161).
        #
        # REQUIRED rather than defaulted, for that adaptor's stated reason: a
        # wiring gap here renders a plausible, WRONG prompt and nothing counts
        # it. `label_for` was already a parameter on this class, typed `object`
        # and never read.
        #
        # `standing_key_for` carries the whole `(platform, conversation_id)`
        # pair rather than a platform alone, so the translation from this
        # adaptor's key to the `standing` tier's happens in ONE expression the
        # composition root owns — see `_StandingReader` for why it is a
        # translation and not an identity.
        unit_for: Callable[[str], str | None],
        standing_key_for: Callable[[str], tuple[str, str] | None],
        label_for: Callable[[str], str | None],
        description_for: Callable[[str], str | None],
        # `None` is a real configuration, not a gap: `[standing] enabled =
        # false` and `[notes] enabled = false` are both supported, and an
        # absent store renders no block rather than an empty one. The
        # difference from a store that RAISES is the whole of R-27 and is
        # handled in `inputs`.
        standing: _StandingReader | None = None,
        notes: _NoteStore | None = None,
        metrics: Metrics | None = None,
        # The ROOM's zone, for the note index's distances — the same
        # `[reminders] timezone` the (deleted) curator and note-candidate pass read
        # «today» from, for their reason: «this Saturday» is a question about
        # the room's calendar, not the process's. UTC by default so a bare
        # construction still renders a true day.
        timezone: _dt.tzinfo = _dt.UTC,
        # THE ROOM'S PLATFORM and A NAME RESOLVER (issue #200), the same
        # pair the deleted `NoteCandidateAdaptor` took and for the same reason: this
        # fold builds its own `<room_messages>` block from the raw batch
        # (`_render_batch`) and never called `Persons.name_transcript`, so a
        # mapped sender's line was attributed to their self-set, spoofable
        # `sender_name` instead of the operator's canonical name for them.
        # `None` (unwired, or the room maps to no platform) skips
        # resolution: `_render_batch`'s `speaker_name` already never renders
        # a raw id either way, so an unwired composition only loses the
        # canonical name, never gains a leak.
        platform_for: Callable[[str], str | None] | None = None,
        resolve_names: (
            Callable[[Sequence[TranscriptRow], str], Sequence[TranscriptRow]] | None
        ) = None,
        # THE CHARACTER TIER (DEC-278): the room's listed persons, Nora's
        # memory (the existing Honcho port, never a new client), her observer
        # peer id, and R-18's screen on what memory answers. `memory=None` is
        # the pre-Character fold, byte for byte: no people block, no person
        # accounts. The screen is INJECTED (`language` may not import
        # `agent`), and required with `memory` rather than defaulted, because
        # a missing screen would pass an identifier into a prompt silently.
        members_for: Callable[[str], Sequence[PersonRef] | None] | None = None,
        memory: MemoryStore | None = None,
        ai_peer: str = "",
        screen: Callable[[str], object] | None = None,
    ) -> None:
        if memory is not None and (screen is None or members_for is None or not ai_peer):
            raise ValueError(
                "CultureAdaptor: memory needs members_for, ai_peer and screen "
                "(R-18) together; a Character fold missing any of them would "
                "ask about nobody or pass an identifier into a prompt"
            )
        self._members_for = members_for
        self._memory = memory
        self._ai_peer = ai_peer
        self._screen = screen
        self._culture = culture
        self._standing = standing
        self._notes = notes
        self._unit_for = unit_for
        self._standing_key_for = standing_key_for
        self._label_for = label_for
        self._description_for = description_for
        self._metrics = metrics
        self._tz = timezone
        self._platform_for = platform_for
        self._resolve_names = resolve_names
        # Per key, for the run in flight: the names whose views `inputs`
        # showed (only those may get an account, DEC-280), the batch's first
        # row id, and the run of consecutive rejections of that batch.
        # ponytail: in memory, so a restart forgets a rejection run and the
        # batch gets up to `MAX_CONSECUTIVE_REJECTIONS` more tries; a column
        # on `dream_schedule_state` if that is ever seen to matter. The runtime
        # serializes runs per (adaptor, key), so no two runs share a key here.
        self._shown: dict[str, frozenset[str]] = {}
        self._batch_start: dict[str, int] = {}
        self._rejections: dict[str, tuple[int | None, int]] = {}
        # What the run in flight may cite (the batch's row ids), what its
        # `character_revise` calls staged per entity (`None` is the room), and
        # which entities it closed unchanged. Set by `inputs`, read by the
        # verbs and drained by `accept`.
        self._citable: dict[str, Citable] = {}
        self._staged: dict[str, dict[str | None, CultureProposal]] = {}
        self._kept: dict[str, set[str | None]] = {}

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
        """Which room this is, the account written last time, her position in
        this room, what the room has open, and the room's own messages.

        **The `<room>` block comes first** and frames everything under it: what
        the room is CALLED and what the operator says it IS. This is issue
        #161's defect one tier over — that pass attributed every line of every
        room of a unit to the unit and wrote «Bath gathering» twice
        out of a games room's shorthand, because nothing in the prompt said
        what kind of room it was reading. The instruction was not the broken
        half; the input was. An account is a claim about a room, so a prompt
        that never names the room is asking for a claim about nothing.

        **Then the previous account**, because it is how everything after it is
        meant to be read: the model is continuing an account, not writing one
        from nothing, and material handed over with no prior context produces a
        fresh snapshot every day — exactly the thing the chain exists to be
        instead of.

        **Then `standing` and the note index** (`01-the-adaptor §1.2`, added
        2026-09-17). The objective asks the model for "where you stand with
        them" while the `standing` tier holds precisely that, per room, written
        by the debrief — `relevance_gate.py:266-272` calls it the only
        authority on it — and this adaptor inferred it from raw transcript
        instead. The note index is the same seam one over: an account of how a
        room works that cannot see what the room currently has OPEN is
        inferring a thing another store already states. **A question whose
        input cannot ground it does not fail loudly** — the model answers
        anyway, in prose, at a rate nothing reports.

        **Both reads are UNGUARDED, and that is the fail-closed side.** A store
        that raises takes `inputs` with it, the runtime reports the run
        `unavailable`, and the watermark is HELD — the batch was not folded, so
        it is folded tomorrow instead. The alternative (proactive's fail-soft
        `_standing_lines`) is right for a pass that must still decide
        something; here it would let a silent store fault produce accounts that
        GUESS at her standing forever, which is the §1.2 defect re-created as a
        supported degraded mode. `notes.standing` was unguarded in
        the deleted `NoteCandidateAdaptor` for the same reason.

        `batch` empty renders `""`. `DreamAdaptor` has no `should_fire` verb;
        the runtime's fire condition IS "did `inputs` render anything", so a
        quiet room must cost nothing — and nothing above is read on that path.
        """
        if not batch:
            return ""
        self._batch_start[key] = batch[0].id
        self._shown[key] = frozenset()
        self._citable[key] = Citable(messages=frozenset(row.id for row in batch))
        self._staged[key] = {}
        self._kept[key] = set()
        # RESOLVE NAMES (issue #200), the same projection the deleted `NoteCandidateAdaptor`
        # applied before its own batch renders. Every row this fold gets from
        # `record.since` carries `person_name=None` — nothing on this path
        # ever called `Persons.name_transcript` — so without this a mapped
        # sender's line in `<room_messages>` was attributed to their
        # self-set, spoofable `sender_name` instead of the operator's name
        # for them. `_render_batch`'s `speaker_name` still never renders a
        # raw id either way; this is what makes the MAPPED case correct
        # rather than merely non-leaking.
        # A RAISING RESOLVER DEGRADES, IT NEVER SKIPS THE FOLD (#546 review).
        # `resolve_names` is `Persons.name_transcript`, dict lookups only, so
        # this should never fire — but this adaptor's own fail-closed rule
        # is for a STORE this pass depends on (`inputs`'s own docstring); a
        # name a batch line is merely attributed BY is not one, and treating
        # a resolver fault as unavailable would hold the watermark and
        # re-read the same batch forever over a failure the fold does not
        # need to answer. Caught here, at the one call site, rather than
        # inside `_render_batch`, which has no logger or metrics
        # collaborator and would have to grow both for this alone.
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
        if previous is not None:
            account_order, account_key = _ROW["previous_account"]
            parts.append(
                ContextBlock(
                    name="previous_account", order=account_order, key=account_key,
                    # HERS, not the room's — the account she wrote last time.
                    # See #314: the marker names the wrong axis, and this block
                    # is one of the two the culture chain drops it across.
                    body=escape(previous.body),
                )
            )
        # HER POSITION IN THIS ROOM, through the shared renderer — which
        # carries the block's own `note`, its not-an-instruction clause and,
        # load-bearing here, `written=`: nothing in this tier retires, so a
        # head account may legitimately be weeks old and a position beside it
        # must say how stale it is.
        standing_key = self._standing_key_for(key)
        if self._standing is not None and standing_key is not None:
            platform, conversation_id = standing_key
            block = render_standing(await self._standing.read(
                platform=platform, conversation_id=conversation_id,
            ))
            if block is not None:
                # RESTAMPED: `render_standing` is shared with the tag path
                # and builds an unstamped block.
                parts.append(restamp(block, _ROW))
        # WHAT THE ROOM HAS OPEN. the deleted `NoteCandidateAdaptor.inputs`' read, same
        # verb and same key translation (DEC-105: the note store is keyed by
        # UNIT, so a scope key must be resolved and never guessed).
        unit = self._unit_for(key)
        if self._notes is not None and unit is not None:
            index = render_notes(
                await self._notes.standing(unit=unit),
                # WITH TODAY, so each entry renders a DISTANCE («in 3 days»)
                # rather than a bare `20260920`. `render_notes` falls back to
                # the raw anchor for a caller with no clock, and a raw calendar
                # date is the one shape this prompt is built to keep out: the
                # batch is rendered with no ids and no timestamps precisely so
                # the model writes about the room rather than about the 12th
                # (`_render_batch` passes no `timestamp`), and a date
                # reintroduced one block up would
                # undo that for free. Today is the ROOM's day, from the newest
                # row of the batch in the room's own zone — never a wall clock,
                # so «today» here cannot disagree with the material below it.
                today=_batch_day(batch, tz=self._tz),
            )
            if index is not None:
                # RESTAMPED, like `render_standing` above: `render_notes` is
                # shared with the tag path and builds an unstamped block.
                parts.append(restamp(index, _ROW))
        if standing_key is None or unit is None:
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
            # conversation answers to (`conv.ids`), while the four maps above
            # — `unit_for`, `standing_key_for`, `label_for`,
            # `description_for` — are keyed by `conv.primary_id` alone. So a
            # conversation reached by an `also_ids` form resolves a home room
            # and NO policy inputs, and only this `log.warning` would say so.
            # It cannot happen today because `language_scopes` emits
            # `primary_id` exclusively (`nora/app/composition.py`), which is a
            # property of the SCHEDULER and not of either map — the day a
            # second id form reaches this adaptor, this branch is the only
            # thing that reports it.
            log.warning(
                "culture: scope=%s resolves to no policy conversation "
                "(unit=%r standing_key=%r); folding without those inputs",
                key, unit, standing_key,
            )
        # HONCHO'S VIEW OF EACH LISTED PERSON WHO WROTE IN THIS BATCH, asked
        # once here (comprehend once) so the model computes on a fixed answer
        # instead of deciding whether to ask. Absent when no person answered.
        # A sender is known by the name `resolve_names` gave their row, which
        # it resolved from the platform id: never a self-set profile name.
        senders = frozenset(
            row.person_name for row in batch if not row.is_nora and row.person_name
        )
        people = await self._people_views(key, unit, senders)
        if people is not None:
            parts.append(people)
        # WRAPPED, for the reason the (deleted) note-candidates batch was (#292 audit):
        # bodies keep their newlines in the element form, so an uncontained
        # batch let a member's message write free text at prompt top level,
        # beside `<previous_account>` and the note index rather than inside
        # anything.
        parts.append(
            # `room_messages`, NOT `batch`. They looked like one block and are
            # two: the deleted note-candidates' `<batch>` rendered `n` as the stored row id
            # so the pass can cite it in `message_ids`; this one renders a 1..N
            # position and suppresses ids by construction, because an account
            # that can cite rows becomes the chronicle this tier exists not to
            # be. Merging them shipped «`n` is the real row» into this prompt,
            # where it is false (#314).
            ContextBlock(
                name="room_messages", order=_ROW["room_messages"][0],
                key=_ROW["room_messages"][1],
                body=_render_batch(batch),
            )
        )
        return assemble(parts, sep="\n\n")

    def _roster(self, key: str) -> tuple[PersonRef, ...]:
        """This room's listed persons, or `()` when Character is not wired."""
        if self._memory is None or self._members_for is None:
            return ()
        return tuple(self._members_for(key) or ())

    async def _people_views(
        self, key: str, unit: str | None, senders: frozenset[str],
    ) -> ContextBlock | None:
        """`<people_views>`: what Nora's memory says each listed person who
        WROTE in this batch is like, one `<person>` per answer (the Character
        tier, DEC-278, narrowed by DEC-280).

        **Only the batch's senders are asked.** Live 2026-10-03: eight
        dialectic calls on a pass where two people had posted. A person who
        said nothing in the batch gives the fold nothing new to combine with
        memory, so they are not asked and get no entry; their account stands
        as it was (`commit` only appends, and `accept` takes an account only
        for a name shown here).

        **The operator's design, and nothing more:** ask the memory system
        «what is the character of X?», combine the answer with the room's
        culture, write it down. `chat` is the dialectic verb `ask_memory`
        already calls, with Nora as observer and the person observed. It
        blends across the unit, which DEC-182's narrowing refused for the
        cache shape; the operator ruled 2026-10-03 that a unit is one circle,
        so that is accepted here (DEC-278).

        **FAIL-SOFT PER PERSON, and the gap is stated:** `chat` returns `None`
        for unreachable and for nothing-to-say alike, so a person with no
        answer gets no account this fold rather than a guessed one; the room's
        own fold is unaffected. An answer carrying an identifier is withheld
        whole (R-18), as `ask_memory` does.
        """
        roster = tuple(ref for ref in self._roster(key) if ref.name in senders)
        if not roster or self._memory is None or self._screen is None or unit is None:
            return None
        screen = self._screen
        # ponytail: one dialectic call per listed person who wrote in the
        # batch, no cap; rooms here are a handful of friends. Add a cap key to
        # `[culture]` if one grows.
        answers = await asyncio.gather(
            *(
                self._memory.chat(
                    peer_id=self._ai_peer, target=ref.honcho_peer_id,
                    query=f"What is the character of {ref.name}?", unit=unit,
                )
                for ref in roster
            ),
            return_exceptions=True,
        )
        rows: list[str] = []
        shown: set[str] = set()
        for ref, answer in zip(roster, answers, strict=True):
            if isinstance(answer, BaseException) or not answer or not answer.strip():
                log.warning("character: no view of %s for scope=%s", ref.person_id, key)
                continue
            if screen(answer) is not None:
                log.warning("character: view of %s withheld (R-18)", ref.person_id)
                continue
            rows.append(ContextBlock(
                name="person_view", attrs=(("name", ref.name),), body=escape(answer.strip()),
            ).render())
            shown.add(ref.name)
        self._shown[key] = frozenset(shown)
        if not rows:
            return None
        order, bkey = _ROW["people_views"]
        return ContextBlock(
            name="people_views", order=order, key=bkey, body="\n".join(rows),
        )

    def _room_block(self, key: str) -> ContextBlock:
        """What this room is CALLED and what the operator says it IS.

        **INLINE.** The deleted `nora.language.note_candidates` held
        `_room_block` / `ROOM_BLOCK_NOTE` / `UNNAMED_ROOM` (issue #292 was moving
        them to a shared home). This renders the same two policy fields
        with this tier's own note.

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
        # THROUGH THE INJECTOR (#292). This block arrived hand-written while
        # the conversion was in flight, which is the census doing its job on a
        # module it had already cleared.
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
            # but escapes to nothing (control characters only), because that
            # is what the hand join this replaced did with it.
            when_empty="render" if description else "self_close",
        )

    # -- the write verbs --------------------------------------------------

    def verbs(self, *, key: str) -> list[Callable[..., Awaitable[dict[str, object]]]]:
        """`character_revise` and `character_no_change`, bound to this run's
        `key` (design/self-growth/10 §3, the Cognition session's verb shape).

        Each VALIDATES as the model calls it and answers `ok` or `refused`
        with a reason it can act on (`nora.domain.culture.check_revision`);
        nothing is written here. What was staged is returned by `accept`, the
        runtime stamps the provenance on it and `commit` appends it, so a
        refused write never reaches the store and an accepted one carries the
        same provenance an envelope account did."""

        async def character_revise(
            entity: str, body: str, citations: list[str],
        ) -> dict[str, object]:
            """Write the new account of one entity: this room, or one person
            shown in `<people_views>`. Give the WHOLE account, not a change to
            it. Call it once per entity; a later call for the same entity
            replaces the earlier one.

            Returns `status`: ok, or refused with a `reason` and a `detail`
            saying what to do instead.

            Args:
                entity: `room` for this room's account, or a person's name
                    exactly as `<people_views>` shows it.
                body: The account, in prose. No headings, no bullet points,
                    no dates.
                citations: The `n` of every message the account rests on, as
                    strings, at least one. Only messages you were shown.
            """
            return self._revise(key, entity, body, tuple(str(c) for c in citations))

        async def character_no_change(entity: str, why: str) -> dict[str, object]:
            """Close one entity with its account unchanged. Call this when the
            messages add nothing the account does not already say, or when
            there is nothing you can point at. The previous account stands.

            Returns `status`: ok, or refused with a `reason` and a `detail`.

            Args:
                entity: `room`, or a person's name exactly as `<people_views>`
                    shows it.
                why: One short sentence: why nothing changes.
            """
            return self._keep(key, entity, why)

        return [character_revise, character_no_change]

    def _entity(self, key: str, entity: str) -> str | Refusal | None:
        """`None` for the room, a `person_id` for a person shown this run, or
        the refusal. The id comes from the roster the prompt showed, never
        from anything the model typed."""
        name = entity.strip()
        if name.casefold() == ROOM_ENTITY:
            return None
        shown = self._shown.get(key, frozenset())
        for ref in self._roster(key):
            if ref.name == name and ref.name in shown:
                return ref.person_id
        allowed = ", ".join([ROOM_ENTITY, *sorted(shown)])
        return Refusal("unknown_entity", f"{entity!r} is not an entity of this "
                       f"session; write one of: {allowed}")

    def _refused(self, key: str, refusal: Refusal) -> dict[str, object]:
        log.warning("culture: scope=%s write refused: %s", key, refusal.reason)
        self._inc("refused")
        return {"status": "refused", "reason": refusal.reason, "detail": refusal.detail}

    def _revise(
        self, key: str, entity: str, body: str, citations: tuple[str, ...],
    ) -> dict[str, object]:
        if key not in self._staged:
            return {"status": "unavailable", "reason": "no_session",
                    "detail": "this session is over; nothing was written"}
        resolved = self._entity(key, entity)
        if isinstance(resolved, Refusal):
            return self._refused(key, resolved)
        refusal = check_revision(
            body=body, citations=citations, citable=self._citable[key],
        )
        if refusal is not None:
            return self._refused(key, refusal)
        self._kept[key].discard(resolved)
        self._staged[key][resolved] = CultureProposal(
            body=body.strip(), person_id=resolved, citations=citations,
        )
        return {"status": "ok"}

    def _keep(self, key: str, entity: str, why: str) -> dict[str, object]:
        if key not in self._staged:
            return {"status": "unavailable", "reason": "no_session",
                    "detail": "this session is over; nothing was written"}
        resolved = self._entity(key, entity)
        if isinstance(resolved, Refusal):
            return self._refused(key, resolved)
        self._staged[key].pop(resolved, None)
        self._kept[key].add(resolved)
        log.info("culture: scope=%s entity=%s unchanged: %s", key, entity, why[:200])
        return {"status": "ok"}

    # -- accept ---------------------------------------------------------

    async def accept(
        self, *, key: str, items: Sequence[Mapping[str, object]]
    ) -> Sequence[object]:
        """What this run's `character_revise` calls staged, or a refusal to
        spend the batch.

        `items` is empty: this adaptor answers through its verbs, never an
        envelope (`DreamerRuntime._sample`). Every staged account was checked
        when it was written, so nothing is re-judged here; what is judged is
        whether the session SETTLED the room: revised it, or closed it
        unchanged. A session that did neither (it ran out of turns, or every
        write was refused and it stopped) is `unsettled` and RAISES
        `AccountNotRecordableError`, so the runtime reports `unavailable` and
        HOLDS the watermark: the batch was not folded, so it is folded again.
        A person is optional: one shown and neither revised nor kept keeps
        the account they had.

        **A run of unsettled sessions is bounded** (DEC-280): the
        `MAX_CONSECUTIVE_REJECTIONS`-th in a row of the same batch for one key
        (the batch named by its first row, which a shrunk retry keeps) does not
        raise; it is logged, counted `abandoned`, and returns `()`, so the fold
        advances instead of re-reading a batch for ever. The run is in memory
        (a restart starts it again).
        """
        staged = self._staged.pop(key, {})
        kept = self._kept.pop(key, set())
        self._citable.pop(key, None)
        if None not in staged and None not in kept:
            log.warning(
                "culture: scope=%s ended without revising or keeping the room's "
                "account; HOLDING the watermark", key,
            )
            self._inc("unsettled")
            self._reject(key, f"the session for scope={key} did not settle the room")
            return ()
        self._rejections.pop(key, None)
        for _ in kept:
            self._inc("kept")
        # The room's account first, then the people, in the order written.
        room = [p for entity, p in staged.items() if entity is None]
        people = [p for entity, p in staged.items() if entity is not None]
        return (*room, *people)

    def _reject(self, key: str, why: str) -> None:
        """Refuse this run. RAISES `AccountNotRecordableError` (the runtime
        reports `unavailable` and holds the watermark) unless this is the
        `MAX_CONSECUTIVE_REJECTIONS`-th rejection in a row of the same batch for
        `key`: then it RETURNS, after a WARNING and `abandoned`, and the caller
        returns `()` so the batch is consumed (DEC-280)."""
        start = self._batch_start.get(key)
        previous, run = self._rejections.get(key, (start, 0))
        run = run + 1 if previous == start else 1
        if run < MAX_CONSECUTIVE_REJECTIONS:
            self._rejections[key] = (start, run)
            raise AccountNotRecordableError(why)
        self._rejections.pop(key, None)
        log.warning(
            "culture: scope=%s rejected %d times in a row on the batch from row %s "
            "(%s); advancing past it with no account written from it",
            key, run, start, why,
        )
        self._inc("abandoned")

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

        **KEPT 2026-09-17, deliberately, and the honest reading is that it
        rarely fires.** It was flagged as unasked-for machinery and the
        objection has force: the fold only runs on `lag > 0`, so the batch
        differs every time, and byte-identity out of a non-zero-temperature
        model on different input is close to impossible — which makes this a
        second `head` read (one of the five declared in `STORE_CALLS_PER_TICK`)
        for a branch almost nothing takes. It stays because removing it is a
        BEHAVIOUR change supported by no measurement, and the thing it guards
        is the one signal this tier carries: a duplicate link is indelible, and
        `01-the-adaptor §4` argues against exactly that. The reading that would
        remove it is a live `nora_culture_fold_total{outcome="unchanged"}` that
        stays at zero across a real window — which is a measurement, and it is
        the counter above that would have to take it.

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
            # A person's account is one more key in the same store and chain.
            store_key = (
                character_key(key, payload.person_id)
                if payload.person_id is not None else key
            )
            previous = await self._culture.head(key=store_key)
            if previous is not None and previous.body == payload.body:
                self._inc("unchanged")
                continue
            await self._culture.append(
                key=store_key,
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

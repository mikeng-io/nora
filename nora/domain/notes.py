"""The note tier's types. A note is TRACKED UNCERTAINTY — never a settled claim
(which is a fact, with a warrant) and never a conclusion about a person (which
is Honcho's `(observer, observed)` axis). See
`docs/superpowers/specs/2026-08-21-note-tier-design.md`.

**The four fields are the point of this module**, and three of them are only
load-bearing if the wrong thing is *unrepresentable* rather than discouraged:

- **`closing_condition`** — what would settle this. Retirement (§7.4),
  promotion-readiness (§7.3) and a revisit's success criterion (§10) are all
  read off this one field, so a note without one has three mechanisms with no
  input. A thing nothing would resolve is not an uncertainty being tracked; it
  is a fact, or it is ignorance.
- **the anchor** (`anchor_date` and/or `anchor_entity_ids`) — merge eligibility
  and mechanical retirement are computed from it. "We should hang out sometime"
  has no referent anything could later be looked up by; it dies as a candidate
  row nobody sees.
- **`title` / `description`** — the injected index entry (§5.1). Capped HERE,
  because truncating in the renderer is silent loss and a constructor that
  refuses is not.

**Where this tier deliberately differs from the fact tier, and why the port has
the shape it does.** `fact` carries a biconditional —
`(superseded_by is null) = (superseded_at is null)` — because a contradicted
fact is always replaced by the corrected one. **A note is not.** Retirement has
no successor: a dinner that happened is replaced by nothing. So this module
asserts the *implication* only (a replacement implies a retirement, never the
converse), and that asymmetry is exactly why `DreamSink.supersede` takes
`replaced_by: str | None` rather than a required id (DEC-130, spec §15.5).
Collapsing this into the fact tier's biconditional would force this tier to mint
a placeholder id to satisfy a signature — the invent-a-value-to-satisfy-a-schema
move §7.3 forbids elsewhere.

Framework-free, per `nora/domain/`'s rule: stdlib and dataclasses only.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from xml.sax.saxutils import unescape

#: The injected index carries one line per note on EVERY tag (§5.1), so these
#: are prompt-budget decisions, not cosmetic ones. They are enforced in
#: `__post_init__` rather than in `nora.language.note_render` because a renderer
#: that truncates loses content silently, and the whole tier is built on the
#: index being a complete listing of what exists.
TITLE_MAX_CHARS = 60
DESCRIPTION_MAX_CHARS = 200
#: 27 §5.2's OPERATING value — what a writer should produce. **Not
#: configurable, deliberately.** `[notes] max_body_chars` was declared with a
#: range validator and a stanza in `config.example.toml` and NOTHING EVER READ
#: IT — enforcement was this constant, in four places, the whole time. A
#: documented knob that changes nothing is the config-file form of a declared
#: metric with no call site: it reads as a control and is a decoration.
#:
#: It was deleted rather than threaded because the cap's own argument is that
#: it is enforced in the TYPE, where a refusal is reviewable and names the cap
#: — and `__post_init__` cannot see config. Threading it would have moved the
#: refusal out to a store boundary to make a knob true, which is the tail
#: wagging the dog. Enforced in `NoteRevision.__post_init__` and both proposal
#: types.
BODY_MAX_CHARS = 8000

#: 27 §5.2's ABSOLUTE ceiling — what the table will hold. NOT configurable, and
#: four times the operating value on purpose.
#:
#: The two numbers mean different things, which is why there are two: 8000 says
#: «no writer should produce that», 32000 says «this row is corrupt». A body
#: between them means something reached the table WITHOUT passing the domain
#: type — a bug to go and find, not a row to reject when somebody reads it. A
#: CHECK at the operating value would collapse the two meanings.
#:
#: **`nora/infra/sql/schema.py` reads this constant rather than repeating the
#: number**, on `STANDING_BODY_CEILING_CHARS`' precedent and for its stated
#: reason: a change to the ceiling must not leave the table and the type
#: disagreeing silently. The gate cannot catch that on its own —
#: `test_schema_parity` compares `schema.py` against the migrated database, so
#: two literals agree with each other and with no declared intent.
#:
#: Migration `0038` keeps its own literal, deliberately: a migration is a
#: snapshot of what was applied and must not import a constant that can move
#: underneath it.
NOTE_BODY_CEILING_CHARS = 32000

#: Anchor dates are `YYYYMMDD` integers, not epoch milliseconds. The anchor is
#: compared for EQUALITY when deciding merge eligibility (§7.1) and against
#: "today" when deciding expiry (§7.4) — both are calendar-day questions, and a
#: millisecond timestamp makes two notes about the same day unequal for a reason
#: that has nothing to do with the day. Every other timestamp in this module is
#: epoch-ms, matching the fact tier.
_MIN_ANCHOR_DATE = 1_000_00_00

#: One calendar day, in epoch ms — shared by `anchor_bounds_ms` and
#: `default_revisit_at` so the two never disagree about what a "day" is.
MS_PER_DAY = 86_400_000

#: The merged curator's wake policy (design/dreamer-agent-loop/34 §5.3, Kimi
#: finding M2). Module constants rather than config: `[notes]` already has
#: `max_revisits_per_tick`/`revisit_evidence_messages` as the operator-facing
#: knobs for the wake PASS, and these two numbers are a judgement about the
#: SHAPE of the schedule itself — how early to check an anchored note, how
#: long a dateless one may go untouched — which is closer to `BODY_MAX_CHARS`
#: (a domain decision, not a deployment one) than to a per-tick cap.
#:
#: **`REVISIT_LEAD_DAYS = 3`**: an anchored note's first (and, absent
#: progress, only) early-close check lands three days before its anchor. 33
#: §1's own framing is "an arrangement spans days, not minutes" — three days
#: is long enough that a genuinely early close (booked weeks in advance) is
#: still caught before the anchor, and short enough that the note is not
#: pestered while the anchor is still comfortably far off. Once the anchor
#: itself passes, `anchored_before` selects the note regardless of
#: `revisit_at`, so this number only governs the PRE-anchor check.
REVISIT_LEAD_DAYS = 3

#: **`DATELESS_REVISIT_HORIZON_DAYS = 7`**: 33/34's own worked example for an
#: undated note's wake cadence ("+7d, capped by the strike ceiling").
DATELESS_REVISIT_HORIZON_DAYS = 7

#: **`MAX_DATELESS_STRIKES = 4`**: at the horizon above, four strikes is
#: roughly a month (4 * 7 days) of a dateless note waking with no tag-path
#: progress before it is ELIGIBLE for dateless retirement (spec §9.6) — long
#: enough that an undated question ("what are we playing next") is not
#: aged out while people are still plausibly deciding, short enough that a
#: note nobody has touched in a month is a real candidate for the notebook
#: only growing. **Eligibility, not action**: `WakeOutcome` reports the
#: strike count and the CALLER decides what follows (its own docstring), and
#: this merge's curator only counts a note crossing the ceiling
#: (`nora_note_curator_total{action="stale"}`) rather than auto-retiring it —
#: building the retirement policy for a note nothing has said is a
#: discharge-pipeline decision (issue #101) this merge does not make.
MAX_DATELESS_STRIKES = 4


def anchor_bounds_ms(anchor_date: int) -> tuple[int, int] | None:
    """The FIRST and LAST instants of a `YYYYMMDD` anchor, in epoch ms, or
    `None` if the value is not a date this can read.

    Moved here from `nora.language.note_revisitor` in the note-curator merge
    (design 34) — a pure function two modules need (the merged curator's
    grounding check, and `default_revisit_at` below) belongs in the domain,
    not duplicated or imported sideways between two `nora.language` modules.

    Both start and end are returned, but **only the end had a reader before
    this merge** — the curator's `default_revisit_at` is this function's
    second caller and its first reader of the START.

    END of the day, not the start: a note anchored to the 15th is not stale
    at 00:00 on the 15th. Computed from the calendar rather than from a
    stored timestamp because `anchor_date` IS an integer date — the tier
    chose that shape deliberately, and re-deriving a moment from it is this
    function's job rather than the store's.

    Returns `None` rather than raising on a malformed value; the caller
    treats `None` as "not decidable" — an unreadable anchor must leave the
    note standing, not expire it, and must not compute a nonsense
    `revisit_at` either.

    **UTC, while the deployment runs in HKT — stated rather than fixed.** A
    note anchored to the 9th therefore only reads as passed at 08:00 local on
    the 10th. That is the CONSERVATIVE direction for expiry (it retires
    later, never earlier) and it is why this is recorded instead of changed:
    picking a timezone is a decision about what a note's DATE MEANS to the
    people writing it, and that is the operator's, not this module's. *The
    reading that would settle it:* a note whose anchor day matters to within
    a few hours — a same-day deadline rather than a dinner.
    """
    import datetime as _dt

    try:
        day = _dt.date(
            anchor_date // 10_000, anchor_date // 100 % 100, anchor_date % 100,
        )
    except (ValueError, TypeError):
        return None
    start = int(
        _dt.datetime.combine(day, _dt.time.min, tzinfo=_dt.UTC).timestamp() * 1000
    )
    return start, start + MS_PER_DAY - 1


def default_revisit_at(
    *,
    anchor_date: int | None,
    now_ms: int,
    lead_days: int = REVISIT_LEAD_DAYS,
    dateless_horizon_days: int = DATELESS_REVISIT_HORIZON_DAYS,
) -> int:
    """The `revisit_at` a note gets, either at CREATION (design 34 §5.3
    resolution (c), `nora.language.note_candidates`) or after a WAKE that
    left the note standing (`nora.language.note_curator`'s `record_wake`
    call) — the SAME formula both callers use, so a gate-created note's first
    wake is a normal scheduled wake rather than a strike bootstrap.

    **Anchored**: the anchor's own day START minus `lead_days` — early enough
    to catch a note answered well before its moment, on the reasoning
    `REVISIT_LEAD_DAYS` states. Once the anchor itself has passed this value
    is in the past too (and stays there), which is harmless: `anchored_before`
    selects a past-anchor note unconditionally, so an already-elapsed
    `revisit_at` on such a note changes nothing about whether it is selected,
    only that `due_for_revisit` would ALSO select it — deduplicated by the
    caller, exactly as a merge-group member is excluded from the expired
    render today.

    **Dateless, or an anchor this cannot read**: `now_ms` plus
    `dateless_horizon_days` — the bounded horizon 33/34 name for a note with
    no moment to count down to. Falling back to the dateless horizon on an
    unreadable anchor (rather than raising or returning `now_ms`) matches
    `anchor_bounds_ms`'s own fail-conservative contract: a value this cannot
    parse must still get SOME future wake, not an immediate one and not none
    at all.
    """
    if anchor_date is not None:
        bounds = anchor_bounds_ms(anchor_date)
        if bounds is not None:
            return bounds[0] - lead_days * MS_PER_DAY
    return now_ms + dateless_horizon_days * MS_PER_DAY


def note_handle(title: str) -> str:
    """The key a note is addressed BY, normalised — §16.1's decision made
    mechanical.

    A note has no model-reachable id: every index that renders notes carries
    title and description and nothing else, so the title is the handle
    (DEC-105's shape for `send_to`, which takes a room's operator-written
    label for the same reason — «an id is not something a person says in a
    chat»). This function is what makes that handle comparable.

    **It lives in the DOMAIN because there are now four readers and they must
    agree exactly** — `nora.agent.tools`' `note_read`/`note_update` resolve a
    title against the standing set, `ReplyGuard`'s R-55 latch keys on the very
    same string, the debrief's note-read latch reads it back, and since
    2026-09-13 `nora.language.note_candidates`' create gate resolves the title
    the model names for an update (#228). That fourth reader is what moved it:
    `nora.language` sits BELOW `nora.agent` in the layered import contract, so
    the only alternative to moving it down was a second copy — and two copies
    of a `casefold()` is how a latch silently stops matching the tool it
    guards.

    `casefold`, not `lower`, and `strip` before it: the database's own
    uniqueness is `(workspace, lower(title)) WHERE retired_at IS NULL`, so case
    is already not part of a note's identity, and a model that types the index
    line back with a trailing space has not named a different note.

    **And UNESCAPED, for the same reason one step further on.** The handle is
    the title as the model SAW it, and every index escapes what it renders
    (R-13): `note_render` and the proactive `<open_notes>` both put the title
    through `escape`. So a note called «Dinner & drinks» reaches the model as
    «Dinner &amp;amp; drinks», and the tool docstrings tell it to type that back
    «exactly as `<notes>` shows it» — which then failed to match the stored
    title and answered «No note in `<notes>` has that title».

    **`&quot;` IS IN THAT SET, and it was not until #292.** `unescape` from
    `xml.sax.saxutils` decodes `&amp;`, `&lt;` and `&gt;` and NOTHING ELSE
    unless it is handed the extra entities — `escape_attr`'s own asymmetry with
    `escape`, one layer down. It did not bite while the index rendered the title
    BETWEEN tags, where `escape` leaves `"` alone. The moment the title moved
    into an attribute — which is what closes the forgeable ` — ` separator — a
    note called «She said "no"» began reaching the model as
    «She said &amp;quot;no&amp;quot;», and typing that back exactly as shown
    stopped resolving. Same defect as the paragraph above, same consequence (a
    duplicate note instead of an update, an unlinkable decision), reached from
    the fix for a different one.

    The decision was unlinkable after that, not merely refused: a proactive
    `remind` written without its `note_id` falls through `ProactiveScreen` to
    the flag-only path, so the note-moved cancellation D11 exists for never
    runs. Normalising here rather than at the call sites is the same argument
    the paragraph above makes about `casefold` — the readers must agree
    exactly.
    """
    return unescape(title.strip(), _ATTR_ENTITIES).casefold()


#: What `escape_attr` writes and `escape` does not, so `note_handle` decodes the
#: index's attribute form as well as its text form.
#:
#: **This widens a pre-existing asymmetry rather than closing one, and the
#: limit is written down because it was measured rather than assumed.**
#: `note_handle` unescapes BOTH sides — the stored title and what the model
#: typed — so a title whose own characters spell an entity does not round-trip.
#: Already true of `&amp;` before this line existed; now also true of `&quot;`:
#: a note literally titled «literal &quot; in the title» normalises to a quote
#: on the stored side and to the entity text on the rendered side, and stops
#: resolving. Verified, not reasoned — an earlier version of this comment
#: claimed `unescape`'s `&amp;`-last ordering made that case safe, and running
#: it showed it does not.
#:
#: Out of scope here: the real fix is to unescape only the MODEL's input and
#: never the stored title, which is a change to a function four readers share
#: (`tools`' resolve, R-55's latch, the debrief latch, #228's create gate) and
#: belongs with them in view. The case needs a title containing the literal
#: text of an XML entity, which nothing has produced in the live tier.
_ATTR_ENTITIES = {"&quot;": '"'}


def _required(value: str, what: str) -> str:
    """Non-empty after stripping, or refuse. `'   '` is non-empty to `if not x`,
    which is the hole `fact_model_is_named_check` closes with `btrim` — the same
    hole, one layer up."""
    if not value or not value.strip():
        raise ValueError(f"a note needs a {what}")
    return value


@dataclass(frozen=True, slots=True)
class Note:
    """One tracked uncertainty, scoped to a workspace.

    `established_in` is the conversation it was born in. It is a **routing key,
    never a filter** (§3): the workspace already is the disclosure boundary
    (DEC-105/R-49 — rooms sharing a workspace are one social unit), so this
    never decides who sees a note. What it decides is where a promoted `Event`
    lands, since `Event` is conversation-scoped and a note is not (§7.3).

    `head_revision_id` is what §6.3's compare-and-swap compares. It is on the
    note rather than derived by "latest revision" because a CAS needs one value
    both sides can name, and "whatever is newest right now" is the thing being
    guarded against.
    """

    id: str
    workspace: str
    title: str
    description: str
    uncertainty: str
    closing_condition: str
    established_in: str
    created_at: int
    head_revision_id: str
    anchor_date: int | None = None
    anchor_entity_ids: tuple[str, ...] = ()
    retired_at: int | None = None
    replaced_by: str | None = None
    # WHY it was retired — `NoteSupersedeReason`'s vocabulary, written by
    # `DreamSink.supersede`. A closed vocabulary rather than free text because
    # "how many notes did the curator retire this week, and on what ground"
    # is the reading that justifies the curator existing, and prose does not
    # aggregate. (It was written as "how many did it merge"; merge was
    # deleted 2026-09-08 and `NoteSupersedeReason.ALL` no longer names it.)
    # `None` until retired; the `retired_at is None` invariant below is what
    # pairs them.
    retire_reason: str | None = None
    revisit_at: int | None = None
    strikes: int = 0
    last_wake_revision_id: str | None = None
    event_id: str | None = None
    reminder_id: str | None = None

    def __post_init__(self) -> None:
        _required(self.title, "title")
        _required(self.description, "description")
        _required(self.uncertainty, "statement of what is uncertain")
        _required(self.closing_condition, "closing condition")
        if len(self.title) > TITLE_MAX_CHARS:
            raise ValueError(
                f"a note title is at most {TITLE_MAX_CHARS} characters "
                f"(got {len(self.title)}) — it is injected on every tag"
            )
        if len(self.description) > DESCRIPTION_MAX_CHARS:
            raise ValueError(
                f"a note description is at most {DESCRIPTION_MAX_CHARS} "
                f"characters (got {len(self.description)}) — it is injected on "
                "every tag"
            )
        if self.anchor_date is None and not self.anchor_entity_ids:
            raise ValueError(
                "a note needs an anchor — a date, entities, or both. Without "
                "one there is no referent to look it up by later, nothing to "
                "compare for a merge, and no way to retire it mechanically"
            )
        if self.anchor_date is not None and self.anchor_date < _MIN_ANCHOR_DATE:
            raise ValueError(
                f"anchor_date is a YYYYMMDD calendar day, got {self.anchor_date}"
            )
        if self.replaced_by is not None and self.retired_at is None:
            raise ValueError(
                "a note replaced by another must also be retired: a standing "
                "note would render in the index beside its own replacement"
            )
        if self.strikes < 0:
            raise ValueError(f"strikes cannot be negative, got {self.strikes}")

    @property
    def is_standing(self) -> bool:
        """True until retired. Reads only `retired_at` and never a successor,
        because a note may retire with no successor at all — an anchor date
        passed and the thing happened (see the module docstring). The
        knowledge tier's own standing predicate required both, which is why
        `DreamSink.supersede` types `replaced_by` as `str | None`."""
        return self.retired_at is None

    @property
    def written_anchor_entities(self) -> tuple[str, ...]:
        """The entity strings the WRITER typed, sorted and de-duplicated.

        **This is free text, never a key.** 「Grant 婚禮」, 「Gabe's wedding」
        and 「婚禮」 are three different strings and one occasion, while two
        unrelated notes that both say 「wedding」 are one string and two
        occasions — a key built from these is wrong in both directions at
        once, which is why the curator's merge (removed 2026-09-08) never
        compared them, and why nothing compares them now.

        Kept because `note_has_an_anchor_check` reads the column and a reader
        asking «what did somebody write here» deserves an answer.
        """
        return tuple(sorted(set(self.anchor_entity_ids)))


@dataclass(frozen=True, slots=True)
class NoteRevision:
    """One append to a note's body. **Never an edit** (§6.1).

    `parent_revision_id` is the head the writer read, which is what makes
    §6.3's compare-and-swap possible: a revision that cannot name what it was
    written against cannot be refused when the head moved under it. `None` is
    the first revision and nothing else.

    `established_by` is load-bearing beyond provenance. §9.6's strike resets
    **only on a tag-path revision** — if the revisitor's own writes reset it,
    the loop breaker resets itself forever and is decorative. That distinction
    is unmakeable without knowing who wrote each revision, so this field is
    required rather than optional.

    Frozen because append-only has to be true of the object as well as the
    table: an editable revision makes "append a revision" one of two available
    verbs instead of the only one.
    """

    id: str
    note_id: str
    body: str
    parent_revision_id: str | None
    written_at: int
    established_by: str
    entity_ids: tuple[str, ...] = field(default_factory=tuple)
    # DEC-125/DEC-126, and `None` is a REAL state rather than an absence: this
    # table has two writers and only one of them is an adaptor. A curator
    # revision arrives as a `Proposal` and carries both; a tag-path revision has
    # no `Proposal` and no instruction digest to carry. `established_by` already
    # tells the two apart (`tag:<person_id>` versus a run id) and is required
    # for that reason, so these say only what it cannot: WHICH instruction
    # produced an adaptor's revision. See `0036_note_revision_provenance` for
    # why this is nullable where `fact_candidate`'s equivalent is not.
    instruction_sha256: str | None = None
    model: str | None = None

    def __post_init__(self) -> None:
        if not self.body or not self.body.strip():
            raise ValueError("a revision needs a body")
        # 27 §5.2, and the reason it is a cap at all: the body is NOT injected
        # — it is read on demand — so the exposure is the CURATOR, which reads
        # bodies per tick against a budget counted in ROWS. A row budget is
        # only a budget if a row has a bounded size; one unbounded body can
        # consume a whole tick's allowance, and the pass then reports «nothing
        # to do» about the notes it never reached.
        #
        # TWO NUMBERS, deliberately. This is the operating value (8000, roughly
        # a long page); the database CHECK is 32000. The gap is not slack — it
        # is the difference between «this writer should not do that» and «this
        # row is corrupt». A body between them means a writer bypassed this
        # type, which is a bug to find rather than a row to reject at read
        # time, and a database that refused at 8000 would make the two
        # indistinguishable.
        if len(self.body) > BODY_MAX_CHARS:
            raise ValueError(
                f"a note body is at most {BODY_MAX_CHARS} characters (got "
                f"{len(self.body)}). Bodies are read whole by the curator "
                f"against a per-tick budget counted in rows, so an unbounded "
                f"one silently eats the allowance"
            )
        if not self.established_by or not self.established_by.strip():
            raise ValueError(
                "a revision needs established_by: the strike reset in §9.6 "
                "distinguishes a tag-path revision from the revisitor's own, "
                "and cannot without it"
            )


@dataclass(frozen=True, slots=True)
class NoteRevisionProposal:
    """What the curator hands `DreamSink.propose` — a revision to append, named
    against the head it was written from.

    **This type exists so the note sink has something to `isinstance` against.**
    `Proposal.payload` is `object` at the port, deliberately: the runtime never
    names an adaptor's own value type, which is what keeps THIS type out of
    `nora/domain/ports.py` and makes "one runtime, many adaptors" true. The cost
    of an opaque field is that a wrong value travels a long way quietly, and the
    rule that pays it is that **the sink refuses anything else by `isinstance`
    and never duck-types** (spec §15.4). A sink that accepts whatever arrives is
    `unavailable` reported as `ok` at the storage boundary.

    It is NOT `NoteRevision`. A `NoteRevision` is a row that exists — it has an
    `id` and a `written_at`, both of which the STORE assigns. A proposal is a
    request that one be written, so it carries neither, and using the row type
    here would force the curator to mint an id the store then ignores or
    honours, and neither answer is good. `written_at` in particular belongs to
    `propose`'s `now_ms`, on the verb rather than the payload, because one tick
    writes many proposals at a single instant and a per-proposal clock invites a
    batch whose rows disagree about when the batch happened.

    `parent_revision_id` is REQUIRED here, unlike on `NoteRevision` where `None`
    means the first revision. The curator never writes a note's first revision —
    it acts ACROSS notes that already exist (§2), so it always has a head to
    name, and §6.3's compare-and-swap is the only thing standing between a
    background pass and the tag path silently overwriting each other. Typing it
    optional would make the one case that must never happen expressible.
    """

    note_id: str
    body: str
    parent_revision_id: str
    entity_ids: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.body or not self.body.strip():
            raise ValueError("a proposed revision needs a body")
        if len(self.body) > BODY_MAX_CHARS:
            raise ValueError(
                f"a proposed revision's body is at most {BODY_MAX_CHARS} "
                f"characters (got {len(self.body)})"
            )
        if not self.note_id or not self.note_id.strip():
            raise ValueError("a proposed revision needs the note it revises")
        if not self.parent_revision_id or not self.parent_revision_id.strip():
            raise ValueError(
                "a proposed revision needs parent_revision_id: the curator "
                "acts across notes that already exist, so it always has a head "
                "to name, and the CAS in §6.3 is what stops a background pass "
                "and the tag path overwriting each other"
            )


@dataclass(frozen=True, slots=True)
class NoteCreationProposal:
    """A note that does not exist yet — the debrief's shape, and the OTHER half
    of what `DreamSink.propose` accepts for this tier.

    **Two shapes with a discriminant, never one shape with optional fields**
    (spec §7). A single type covering create and revise would make every field
    optional, and a sink whose payload has no required field can refuse
    nothing: `isinstance` would pass on a half-built value and the store would
    discover the gap at the INSERT, as a database error on a background clock
    rather than a refusal at the boundary. Two frozen types with disjoint
    required fields is what makes the refusal possible at all.

    **Why the debrief needs its own shape rather than reusing
    `NoteRevisionProposal`.** That type's docstring says it plainly: *the
    curator never writes a note's first revision — it acts ACROSS notes that
    already exist.* The debrief's job is the opposite one. It reviews a
    finished turn, and the valuable thing it can see is an uncertainty NOBODY
    tracked, which is by definition a note that is not there. Revising an
    existing note is the tag path's job in-turn (R-55's latch) and the
    curator's across notes (§2).

    **The anchor is required, and in practice it is a DATE.** `Note`
    `__post_init__` refuses a note anchored to nothing — without a referent
    there is nothing to look it up by later, nothing for the create gate to
    compare against, and no way to retire it mechanically. `anchor_entity_ids`
    is the writer's free text and nothing resolves it (the entity-resolution
    chain was removed 2026-09-08), so an entity-only anchor is a note the
    clock can never retire. Both are offered and at least one is required,
    which is `Note`'s own rule stated one layer earlier, where the proposal
    can be refused instead of raising inside a background tick.

    **A create proposal never names a note, so it cannot collide with one — and
    the sink does NOT check whether a similar note already exists.** That is
    deliberate. Deduplication is anchor-keyed and belongs to the curator (§7.1),
    which is the only thing permitted to act across notes; a sink that
    reinterpreted a create as a revision because it found a plausible match
    would be doing cross-note reasoning at the storage boundary, silently, on
    a guess. Two notes about the same occasion is a state the curator is built
    to resolve. One note silently absorbing another's content is not
    recoverable in the direction that matters.
    """

    title: str
    description: str
    uncertainty: str
    closing_condition: str
    established_in: str
    body: str
    anchor_date: int | None = None
    anchor_entity_ids: tuple[str, ...] = field(default_factory=tuple)
    #: The note-curator merge's §5.3 resolution (c): a note this proposal
    #: creates gets an explicit wake time at CREATION, derived from its own
    #: anchor via `default_revisit_at` — so its first wake is a normal
    #: scheduled one, not a strike bootstrap (a gate-created chain has no
    #: `tag:` revision either way, so `record_wake`'s progress test would
    #: otherwise charge a strike on a note nobody has had a chance to answer
    #: yet). `None` is a legal value — the tag path's own creates (through
    #: `SqlNoteStore.create`, never this proposal type) do not set it, and
    #: `record_wake` remains the only writer of a note's SUBSEQUENT
    #: `revisit_at`.
    revisit_at: int | None = None

    def __post_init__(self) -> None:
        for value, what in (
            (self.title, "title"),
            (self.description, "description"),
            (self.uncertainty, "uncertainty"),
            (self.closing_condition, "closing_condition"),
            (self.established_in, "the conversation it was raised in"),
            (self.body, "body"),
        ):
            if not value or not value.strip():
                raise ValueError(f"a proposed note needs {what}")
        if len(self.body) > BODY_MAX_CHARS:
            raise ValueError(
                f"a proposed note's body is at most {BODY_MAX_CHARS} "
                f"characters (got {len(self.body)})"
            )
        # THE SAME CAPS `Note` ENFORCES, mirrored here on purpose. This type's
        # docstring argues that a refusal at the boundary is reviewable while a
        # raise inside a tick is a log line nobody reads — and until 2026-08-27
        # it copied only the anchor rule, so a 61-character title constructed
        # fine and blew up at `_new_note` INSIDE `engine.begin()`, on the
        # background clock, discarding the whole batch. That is exactly the
        # failure this type exists to prevent, and the type did not implement
        # it. A debrief model emitting a slightly long title is the likely
        # case, not an exotic one.
        if len(self.title) > TITLE_MAX_CHARS:
            raise ValueError(
                f"a note title is at most {TITLE_MAX_CHARS} characters (got "
                f"{len(self.title)}) — the index is injected on EVERY tag, so "
                f"this is a prompt-budget cap and not a style rule"
            )
        if len(self.description) > DESCRIPTION_MAX_CHARS:
            raise ValueError(
                f"a note description is at most {DESCRIPTION_MAX_CHARS} "
                f"characters (got {len(self.description)})"
            )
        if self.anchor_date is not None and self.anchor_date < 10000000:
            raise ValueError(
                f"anchor_date is a YYYYMMDD calendar day, not epoch ms or a "
                f"bare year (got {self.anchor_date})"
            )
        if self.anchor_date is None and not self.anchor_entity_ids:
            raise ValueError(
                "a proposed note needs an anchor — a date, entities, or both. "
                "Without one there is no referent to look it up by later, "
                "nothing to compare for a merge, and no way to retire it "
                "mechanically. In practice this is a date: the entity registry "
                "is not populated yet"
            )


#: The prefix `nora.agent.tools` stamps on a revision it writes for a person.
#: **The one thing §9.6's strike reset turns on**, and it is a prefix rather
#: than a flag column because `established_by` already had to distinguish the
#: writers for provenance — a second column saying the same thing is a second
#: thing to keep in step.
TAG_PATH_PREFIX = "tag:"


def is_tag_path_revision(established_by: str) -> bool:
    """Whether a revision was written by a person on the tag path.

    **§9.6's trap, and the whole reason this predicate exists rather than a
    bare `head != last_wake` comparison:** the strike reset must be «head moved
    BY THE TAG PATH», not «head moved». If a background pass's own writes
    counted, a note the revisitor keeps rewriting would reset its own strike
    forever and the breaker would be decorative — a self-referential reset is
    not a reset.

    The spec states this plainly and says why: *"the mechanism reads correct
    without the qualifier and loops forever with it missing."*
    """
    return established_by.startswith(TAG_PATH_PREFIX)


def tag_path_progressed_since_wake(
    *,
    head_revision_id: str,
    last_wake_revision_id: str | None,
    chain: Mapping[str, tuple[str | None, str]],
) -> bool:
    """Whether the TAG PATH wrote anything since the last wake.

    `chain` maps a revision id to `(parent_revision_id, established_by)` for
    every revision of the note. The walk goes head → parent until it reaches
    `last_wake_revision_id`, so what it asks is «is there a tag-path revision
    in the span this wake has not seen».

    **It used to ask who wrote the CURRENT HEAD**, and that is a different
    question with a different answer on the sequence that matters:

        wake at R5 → the room revises (R6, `tag:miles`) → a background pass
        appends (R7, a run id)

    The head is R7, background-authored, so the old test said «no progress»,
    charged a strike, and LEFT THE WATERMARK AT R5 — so the same R6 could never
    count on any later tick either. A note the room is actively working, that a
    background pass also touches each cycle, never resets and marches to the
    strike ceiling. The curator's `merge` wrote exactly such a revision and
    is gone (2026-09-08); the note-candidates gate's appended revision
    (`nora.language.note_candidates`, #228) writes one on the same shape, so
    the sequence is live and this walk still earns its keep.

    Both implementations agreed on the wrong answer, which is why the contract
    suite could not see it: they shared a belief, not a check.

    A `last_wake_revision_id` of `None` is a note that has never been woken, so
    the whole chain is unseen and any tag-path revision in it counts.

    The walk terminates on a revision it has already visited. An append-only
    chain cannot cycle; a walk over rows it did not write must not assume that.
    """
    seen: set[str] = set()
    current: str | None = head_revision_id
    while current is not None and current != last_wake_revision_id:
        if current in seen:
            break
        seen.add(current)
        entry = chain.get(current)
        if entry is None:
            break
        parent, established_by = entry
        if is_tag_path_revision(established_by):
            return True
        current = parent
    return False


@dataclass(frozen=True, slots=True)
class WakeOutcome:
    """What a wake did — returned so the CALLER decides what follows.

    `strikes` is the count AFTER this wake. `reset` says the head had moved on
    the tag path, so the note is making progress and the counter went back to
    zero.

    **The store does not retire at the ceiling, and that is deliberate.**
    §9.6 says a note past K strikes "stops waking and becomes eligible for
    dateless retirement" — ELIGIBLE, which is a judgement about what to do
    next, not a fact about the row. Retirement is a `supersede` with a reason
    and a `replaced_by`; hiding one inside a bookkeeping verb would make one
    call do two things, and the destructive half would be the invisible one. So
    the wake reports and the revisitor decides, which is also where K lives.
    """

    strikes: int
    reset: bool


#: `note_candidate.status` for a `kind='create'` row (migration `0049`,
#: design/dreamer-agent-loop/33-note-dreamer-schedule.md §4). `pending` is the
#: staged proposal before the deterministic gate has run (or re-run after a
#: transient failure); `created` is a proposal the gate materialized, as a new
#: note OR as a revision to an existing one sharing its anchor key — the
#: store's `status` column does not distinguish the two, because both are
#: "this occasion is now represented in the notebook", which is the property
#: `nora.language.note_candidates` needs to avoid reopening a settled
#: candidate, not which verb did it; `refused` is a proposal the gate declined
#: — a duplicate that added nothing, or a malformed candidate the domain type
#: itself rejected.
CANDIDATE_PENDING = "pending"
CANDIDATE_CREATED = "created"
CANDIDATE_REFUSED = "refused"
CANDIDATE_STATUSES: tuple[str, ...] = (
    CANDIDATE_PENDING, CANDIDATE_CREATED, CANDIDATE_REFUSED,
)


@dataclass(frozen=True, slots=True)
class NoteCreateCandidate:
    """One `kind='create'` row of `note_candidate` — a full note proposal the
    `note-candidates` adaptor (`nora.language.note_candidates`) read off a
    room's transcript, staged for a deterministic gate to decide create,
    update or refuse over. Migration `0049`; see its own docstring for the
    column shapes and the two uniqueness domains this table now holds.

    **In the SAME shape `NoteCreationProposal` requires**, deliberately: this
    row exists only because there is no note yet to build one against, and it
    is turned into exactly that proposal (or a `NoteRevisionProposal`, if the
    gate finds a standing note sharing this occasion's anchor key) once the
    gate has decided. A row that could not become one of those two proposals
    would not have been accepted into this table to begin with.

    `anchor_entity_ids` is stored SORTED and DE-DUPLICATED — never as the
    model wrote it — because it is half of `note_candidate_create_anchor_key`;
    see that index's own comment in `nora/infra/sql/schema.py`.
    """

    id: str
    workspace: str
    title: str
    description: str
    body: str
    closing_condition: str
    anchor_date: int
    anchor_entity_ids: tuple[str, ...]
    message_ids: tuple[int, ...]
    status: str
    attempts: int
    first_seen_at: int
    last_seen_at: int


class NoteSupersedeReason:
    """The `because` values `SqlNoteStore.supersede` accepts, and the shape of
    the tier's retirement vocabulary.

    A class of constants rather than an enum on the port: `DreamSink.supersede`
    types `because` as `str` on purpose, because the fact tier's reasons
    (`contradicted`) and this tier's have nothing in common, and a shared enum
    would make the port fact-shaped or note-shaped depending on who wrote it
    last. The contract suite's normalised row view keeps `because` opaque for
    the same reason.
    """

    RETIRED_EXPIRED = "retired-expired"
    RETIRED_CLOSED = "retired-closed"

    #: `"merged"` was the third value until 2026-09-08, written only by the
    #: curator's merge verb, which is gone. A live row carrying it would
    #: still read back (`retire_reason` is free text at the column); nothing
    #: writes it now, so the vocabulary no longer names it.
    ALL: tuple[str, ...] = (RETIRED_EXPIRED, RETIRED_CLOSED)


class StaleNoteHeadError(Exception):
    """The compare-and-swap in §6.3 refused: the head this revision was written
    against is not the head any more.

    **Why a refusal and not a merge, an overwrite, or a silent no-op.** A note's
    value is the *arrangement* — the writer read one body, reasoned over it, and
    is now appending prose that only makes sense against that body. If the head
    moved (the note-candidates gate appended to it mid-turn, or a parallel turn
    in the other room of the same workspace revised it), the text being appended is an answer
    to a question that has changed. Writing it on top destroys the intervening
    revision's arrangement with nothing recording that it happened; returning 0
    and carrying on reports «nothing to do» for what was actually «I could not
    do it» — R-27's `unavailable`-as-`nothing_found` failure, one layer down.
    Raising is the only one of the three the caller cannot mistake for success.

    `observed_head` is what the head actually is, so the tool layer's message can
    be «this note changed under you, read it again» rather than a bare failure.
    **`None` means the note is not there at all** *from the caller's workspace* —
    absent, retired out of existence, or in someone else's workspace, which this
    store deliberately cannot tell apart (a caller must not be able to confirm a
    note exists in a workspace it may not read). Both cases have the same remedy,
    which is why they are one exception rather than two: read it again.

    A plain `Exception` rather than `nora.domain.errors.NoraError`: `NoraError`
    carries a `FailureClass`, and how a refused write is *surfaced to the room*
    is the tool layer's decision (`note_update` reports `unavailable`), not this
    type's. Baking INFRASTRUCTURE in here would default the refusal to silence.
    """

    def __init__(self, message: str, *, observed_head: str | None) -> None:
        super().__init__(message)
        self.observed_head = observed_head

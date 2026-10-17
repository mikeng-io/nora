"""The note tier's types. A note is TRACKED UNCERTAINTY: never a settled claim
(which is a fact, with a warrant) and never a conclusion about a person (which
is Honcho's `(observer, observed)` axis).

**The four fields are the point of this module**, and three of them are only
load-bearing if the wrong thing is *unrepresentable* rather than discouraged:

- **`closing_condition`**: what would settle this. Retirement, promotion-
  readiness and a revisit's success criterion are all read off this one field,
  so a note without one has three mechanisms with no input. A thing nothing
  would resolve is not an uncertainty being tracked; it is a fact, or it is
  ignorance.
- **the anchor** (`anchor_date` and/or `anchor_entity_ids`): merge eligibility
  and mechanical retirement are computed from it. "We should hang out sometime"
  has no referent anything could later be looked up by.
- **`title` / `description`**: the injected index entry. Capped HERE, because
  truncating in the renderer is silent loss and a constructor that refuses is
  not.

**Where this tier differs from the fact tier.** `fact` carries a biconditional,
`(superseded_by is null) = (superseded_at is null)`, because a contradicted
fact is always replaced by the corrected one. **A note is not.** Retirement has
no successor: a dinner that happened is replaced by nothing. So this module
asserts the *implication* only (a replacement implies a retirement, never the
converse), which is why `DreamSink.supersede` takes `replaced_by: str | None`
rather than a required id (DEC-130). A required id would force this tier to mint
a placeholder to satisfy a signature.

Framework-free, per `nora/domain/`'s rule: stdlib and dataclasses only.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from xml.sax.saxutils import unescape

from nora.domain.lineage import Lineage
from nora.domain.note_body import citations_in
from nora.domain.prompt import one_line

#: The injected index carries one line per note on EVERY tag, so these are
#: prompt-budget decisions, not cosmetic ones. They are enforced in
#: `__post_init__` rather than in `nora.language.note_render` because a renderer
#: that truncates loses content silently, and the index is a complete listing of
#: what exists.
TITLE_MAX_CHARS = 60
DESCRIPTION_MAX_CHARS = 200
#: The OPERATING body cap: what a writer should produce. Not configurable:
#: it is enforced in the TYPE, where a refusal is reviewable and names the cap,
#: and `__post_init__` cannot see config. Enforced in
#: `NoteRevision.__post_init__` and both proposal types.
BODY_MAX_CHARS = 8000

#: The ABSOLUTE body ceiling: what the table will hold. Not configurable, and
#: four times the operating value on purpose.
#:
#: The two numbers mean different things: 8000 says «no writer should produce
#: that», 32000 says «this row is corrupt». A body between them means something
#: reached the table WITHOUT passing the domain type, a bug to find rather than
#: a row to reject when somebody reads it. A CHECK at the operating value would
#: collapse the two meanings.
#:
#: `nora/infra/sql/schema.py` reads this constant rather than repeating the
#: number, on `STANDING_BODY_CEILING_CHARS`' precedent: `test_schema_parity`
#: compares `schema.py` against the migrated database, so two literals would
#: agree with each other and with no declared intent. Migration `0038` keeps its
#: own literal: a migration is a snapshot of what was applied and must not
#: import a constant that can move underneath it.
NOTE_BODY_CEILING_CHARS = 32000

#: Anchor dates are `YYYYMMDD` integers, not epoch milliseconds. The anchor is
#: compared for EQUALITY when deciding merge eligibility and against "today"
#: when deciding expiry; both are calendar-day questions, and a millisecond
#: timestamp makes two notes about the same day unequal for a reason that has
#: nothing to do with the day. Every other timestamp in this module is epoch-ms,
#: matching the fact tier.
_MIN_ANCHOR_DATE = 1_000_00_00

#: One calendar day, in epoch ms — shared by `anchor_bounds_ms` and
#: `default_revisit_at` so the two never disagree about what a "day" is.
MS_PER_DAY = 86_400_000

#: When a note is next due to be looked at (`default_revisit_at`): a domain
#: decision about the schedule's shape, not a deployment knob.
#:
#: **`REVISIT_LEAD_DAYS = 3`**: an anchored note's first early-close check
#: lands three days before its anchor: early enough that a close booked weeks
#: ahead is caught, late enough not to pester while the anchor is far off.
REVISIT_LEAD_DAYS = 3

#: **`DATELESS_REVISIT_HORIZON_DAYS = 7`**: an undated note's wake cadence
#: (+7d, capped by the strike ceiling).
DATELESS_REVISIT_HORIZON_DAYS = 7


def anchor_bounds_ms(anchor_date: int) -> tuple[int, int] | None:
    """The FIRST and LAST instants of a `YYYYMMDD` anchor, in epoch ms, or
    `None` if the value is not a date this can read.

    A pure function that `default_revisit_at` and the grounding check both need,
    so it lives in the domain rather than imported sideways between two
    `nora.language` modules.

    END of the day, not the start: a note anchored to the 15th is not stale
    at 00:00 on the 15th. Computed from the calendar rather than from a
    stored timestamp because `anchor_date` IS an integer date.

    Returns `None` rather than raising on a malformed value; the caller
    treats `None` as "not decidable": an unreadable anchor must leave the
    note standing, not expire it, and must not compute a nonsense
    `revisit_at` either.

    **UTC, while the deployment runs in HKT.** A note anchored to the 9th
    therefore only reads as passed at 08:00 local on the 10th. That is the
    CONSERVATIVE direction for expiry (it retires later, never earlier).
    Picking a timezone is a decision about what a note's DATE MEANS to the
    people writing it, and that is the operator's, not this module's.
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
    """The `revisit_at` a note gets at CREATION: the Cognition session's
    `note_create` calls it, so a created note's first wake is a normal scheduled
    wake rather than a strike bootstrap.

    **Anchored**: the anchor's own day START minus `lead_days`, early enough
    to catch a note answered well before its moment (`REVISIT_LEAD_DAYS`). Once
    the anchor itself has passed this value is in the past too (and stays
    there), which is harmless: an already-elapsed `revisit_at` on a past-anchor
    note changes nothing about whether it is selected.

    **Dateless, or an anchor this cannot read**: `now_ms` plus
    `dateless_horizon_days`, the bounded horizon for a note with no moment to
    count down to. Falling back to it on an unreadable anchor (rather than
    raising or returning `now_ms`) matches `anchor_bounds_ms`'s
    fail-conservative contract: a value this cannot parse must still get SOME
    future wake, not an immediate one and not none at all.
    """
    if anchor_date is not None:
        bounds = anchor_bounds_ms(anchor_date)
        if bounds is not None:
            return bounds[0] - lead_days * MS_PER_DAY
    return now_ms + dateless_horizon_days * MS_PER_DAY


def note_handle(title: str) -> str:
    """The key a note is addressed BY, normalised.

    A note has no model-reachable id: every index that renders notes carries
    title and description and nothing else, so the title is the handle
    (DEC-105's shape for `send_to`, which takes a room's operator-written
    label for the same reason: «an id is not something a person says in a
    chat»). This function is what makes that handle comparable.

    **It lives in the DOMAIN because several readers must agree exactly**:
    `nora.agent.tools`' `note_read`/`note_update` resolve a title against the
    standing set, `ReplyGuard`'s R-55 latch keys on the very same string, and
    the debrief's note-read latch reads it back. `nora.language` sits BELOW
    `nora.agent` in the layered import contract, so the only alternative to
    living down here is a second copy, and two copies of a `casefold()` is how a
    latch silently stops matching the tool it guards.

    `casefold`, not `lower`, and `strip` before it: the database's own
    uniqueness is `(unit, lower(title)) WHERE retired_at IS NULL`, so case
    is already not part of a note's identity, and a model that types the index
    line back with a trailing space has not named a different note.

    **And UNESCAPED.** The handle is the title as the model SAW it, and every
    index escapes what it renders (R-13): `note_render` renders `<notes>` and
    puts the title through `escape`. So a note called «Dinner & drinks» reaches
    the model as «Dinner &amp;amp; drinks», and the tool docstrings tell it to
    type that back «exactly as `<notes>` shows it»; without unescaping that
    would not match the stored title.

    **`&quot;` is in that set.** `unescape` from `xml.sax.saxutils` decodes
    `&amp;`, `&lt;` and `&gt;` and nothing else unless it is handed the extra
    entities (`escape_attr`'s asymmetry with `escape`). The index renders the
    title in an attribute, so a note called «She said "no"» reaches the model as
    «She said &amp;quot;no&amp;quot;», and typing that back exactly as shown must
    still resolve. A miss means a duplicate note instead of an update, and a
    proactive `remind` written without its `note_id` falls through
    `ProactiveScreen` to the flag-only path, so the note-moved cancellation never
    runs. Normalising here rather than at the call sites keeps the readers in
    agreement.
    """
    # COLLAPSED, because the index collapses it. `render_note_rows` puts the
    # title in an attribute and `render_elements` runs `one_line` on every
    # attribute value, so a title holding a newline reaches the model as one
    # line, and the model types back what it was SHOWN. Without this, a note
    # titled «Dinner\nplan» never matches its displayed one-line form:
    # `_resolve_updates` finds nothing and the pass writes a DUPLICATE.
    return one_line(unescape(title.strip(), _ATTR_ENTITIES)).casefold()


def _title_words(title: str) -> frozenset[str]:
    return frozenset(w for w in note_handle(title).split() if len(w) > 2)


def likely_duplicates(notes: Sequence[Note]) -> dict[str, str]:
    """Note id -> the id of another open note whose title shares at least half
    of the two titles' words (Jaccard >= 0.5, words of three letters or more):
    the code-side hint that two notes may track one question. Each note names
    its most similar partner; a note with none is absent."""
    words = {n.id: _title_words(n.title) for n in notes}
    out: dict[str, str] = {}
    for a in notes:
        best, score = None, 0.5
        for b in notes:
            if a.id == b.id or not words[a.id] or not words[b.id]:
                continue
            union = words[a.id] | words[b.id]
            jaccard = len(words[a.id] & words[b.id]) / len(union)
            if jaccard >= score:
                best, score = b.id, jaccard
        if best is not None:
            out[a.id] = best
    return out


def has_duplicate_open_title(title: str, open_titles: Sequence[str]) -> bool:
    """An open title refuses a second note under the same handle. Shared by the
    tag path's `note_create` and the Cognition session's, so both refuse
    identically. `open_titles` is READ-ONLY input the caller already fetched
    (`book.standing(unit=...)`'s titles); this function makes no store call."""
    wanted = note_handle(title)
    return any(note_handle(t) == wanted for t in open_titles)


#: What `escape_attr` writes and `escape` does not, so `note_handle` decodes the
#: index's attribute form as well as its text form.
#:
#: Limit: `note_handle` unescapes BOTH sides (the stored title and what the model
#: typed), so a title whose own characters spell an entity does not round-trip.
#: True of `&amp;` and of `&quot;`: a note literally titled «literal &quot; in
#: the title» normalises to a quote on the stored side and to the entity text on
#: the rendered side, and stops resolving. Fixing it means unescaping only the
#: MODEL's input and never the stored title, a change to a function several
#: readers share (`tools`' resolve, R-55's latch, the debrief latch). It needs a
#: title containing the literal text of an XML entity.
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
    """One tracked uncertainty, scoped to a unit.

    `established_in` is the conversation it was born in. It is a **routing key,
    never a filter**: the unit already is the disclosure boundary
    (DEC-105/R-49: rooms sharing a unit are one social unit), so this
    never decides who sees a note. What it decides is where a promoted `Event`
    lands, since `Event` is conversation-scoped and a note is not.

    `head_revision_id` is what the compare-and-swap compares. It is on the
    note rather than derived by "latest revision" because a CAS needs one value
    both sides can name, and "whatever is newest right now" is the thing being
    guarded against.
    """

    id: str
    unit: str
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
    # WHY it was retired: `NoteSupersedeReason`'s vocabulary, written by
    # `DreamSink.supersede`. A closed vocabulary rather than free text because
    # "how many notes were retired this week, and on what ground" is the reading
    # that justifies keeping the reason, and prose does not aggregate. A fold is
    # `retired-merged`, written by `merge_into` (the Cognition session's
    # `note_merge`). `None` until retired; the `retired_at is None` invariant
    # below is what pairs them.
    retire_reason: str | None = None
    revisit_at: int | None = None
    strikes: int = 0
    last_wake_revision_id: str | None = None
    event_id: str | None = None
    reminder_id: str | None = None
    # WHERE IT MAY BE SHOWN (migration `0073_lineage`): the
    # `(kind, subject, origin room)` set its citations reach, and the people it
    # is never shown to. Written by the store from the row's own citations,
    # never by a model; read at render by `nora.agent.lineage`.
    # `compare=False`: equality stays about content, and a row built by hand (no
    # lineage, every section unknown) is on the fail-closed side.
    lineage: Lineage = field(default=Lineage(), compare=False)
    exclude_persons: tuple[str, ...] = field(default=(), compare=False)

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
        occasions: a key built from these is wrong in both directions at
        once, so nothing compares them.

        Kept because `note_has_an_anchor_check` reads the column and a reader
        asking «what did somebody write here» deserves an answer.
        """
        return tuple(sorted(set(self.anchor_entity_ids)))


@dataclass(frozen=True, slots=True)
class NoteRevision:
    """One append to a note's body. **Never an edit.**

    `parent_revision_id` is the head the writer read, which is what makes
    the compare-and-swap possible: a revision that cannot name what it was
    written against cannot be refused when the head moved under it. `None` is
    the first revision and nothing else.

    `established_by` is load-bearing beyond provenance. The strike resets
    **only on a tag-path revision**: if a background pass's own writes reset it,
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
    # DEC-125/DEC-126. `None` is a REAL state rather than an absence: this
    # table has two writers and only one of them is an adaptor. A background
    # revision arrives as a `Proposal` and carries both; a tag-path revision has
    # no `Proposal` and no instruction digest. `established_by` already tells the
    # two apart (`tag:<person_id>` versus a run id), so these say only what it
    # cannot: WHICH instruction produced an adaptor's revision. See
    # `0036_note_revision_provenance`.
    instruction_sha256: str | None = None
    model: str | None = None

    @property
    def citations(self) -> tuple[str, ...]:
        """What this revision cites, as `note_revision.citations` stores it
        (`citations_in`): derived from the body every writer has already held
        to the schema, so the column and the body cannot disagree."""
        return citations_in(self.body)

    def __post_init__(self) -> None:
        if not self.body or not self.body.strip():
            raise ValueError("a revision needs a body")
        # The body is NOT injected (it is read on demand), so the exposure is
        # background passes, which read bodies against a budget counted in ROWS.
        # A row budget is only a budget if a row has a bounded size.
        #
        # TWO NUMBERS: this is the operating value (8000); the database CHECK
        # is `NOTE_BODY_CEILING_CHARS`. A body between them means a writer
        # bypassed this type, a bug to find rather than a row to reject at read
        # time.
        if len(self.body) > BODY_MAX_CHARS:
            raise ValueError(
                f"a note body is at most {BODY_MAX_CHARS} characters (got "
                f"{len(self.body)}). Bodies are read whole by background passes "
                f"against a per-tick budget counted in rows, so an unbounded "
                f"one silently eats the allowance"
            )
        if not self.established_by or not self.established_by.strip():
            raise ValueError(
                "a revision needs established_by: the strike reset in §9.6 "
                "distinguishes a tag-path revision from the revisitor's own, "
                "and cannot without it"
            )


#: The prefix `nora.agent.tools` stamps on a revision it writes for a person.
#: **The one thing the strike reset turns on**, and a prefix rather than a flag
#: column because `established_by` already distinguishes the writers for
#: provenance; a second column saying the same thing is a second thing to keep
#: in step.
TAG_PATH_PREFIX = "tag:"


def is_tag_path_revision(established_by: str) -> bool:
    """Whether a revision was written by a person on the tag path.

    The strike reset must be «head moved BY THE TAG PATH», not «head moved», which
    is why this predicate exists rather than a bare `head != last_wake`
    comparison. If a background pass's own writes counted, a note it keeps
    rewriting would reset its own strike forever and the breaker would be
    decorative: a self-referential reset is not a reset.
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

    It does not ask who wrote the CURRENT HEAD. Take:

        wake at R5 → the room revises (R6, `tag:miles`) → a background pass
        appends (R7, a run id)

    The head is R7, background-authored, but R6 is tag-path progress since the
    wake. Judging by the head would charge a strike and leave the watermark at
    R5, so the same R6 could never count later, and a note the room is actively
    working would march to the strike ceiling. Background passes and a
    Cognition-session `note_update` append such revisions.

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


def proactive_row_forwarded_is_moved(
    *,
    duplicate_head: str,
    duplicate_chain: Mapping[str, tuple[str | None, str]],
    revision_id: str | None,
    created_at_ms: int,
    survivor_chain: Iterable[tuple[int, str]],
) -> bool:
    """Whether a proactive row that names a `retired-merged` note (or, from
    `SqlNoteStore.merge_into`'s side, one ABOUT to become one) has gone stale.
    `nora.app.proactive_screen.ProactiveScreen._follow_survivor`'s two-step
    order, shared so the merge-time re-point cannot diverge from the fire-time
    screen.

    1. **The DUPLICATE's own chain.** `revision_id` is a genuine ancestor of
       `duplicate_head` (that is where the row was linked), so this is an
       ANCESTRY WALK (`tag_path_progressed_since_wake`), exactly the check an
       ordinary, never-forwarded row gets. A tag-path edit to the duplicate
       after the row was linked and before it forwards is caught here.
    2. **The SURVIVOR's chain**, only reached if step 1 found nothing. Nothing
       links this row to any revision of the survivor (it was never about the
       survivor), so this cannot be an ancestry walk: it is a SCAN of
       `created_at_ms` (the row's own booking time) against every revision the
       survivor's chain carries, for the reason `_follow_survivor`'s docstring
       gives (paraphrase-proofing via `is_tag_path_revision`).

    The two steps cannot be swapped, skipped or reordered: step 1 answers "did
    anyone settle the ORIGINAL question, on the note that originally held it"
    and step 2 "did anyone settle the FOLDED question, on the note it now lives
    in". Both are premises the row was booked against, and either failing means
    Nora should not raise it.
    """
    if tag_path_progressed_since_wake(
        head_revision_id=duplicate_head, last_wake_revision_id=revision_id,
        chain=duplicate_chain,
    ):
        return True
    return any(
        is_tag_path_revision(established_by) and written_at > created_at_ms
        for written_at, established_by in survivor_chain
    )


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
    #: Folded into another open note, which `replaced_by` names (DEC-202).
    #: Written by `merge_into` (the Cognition session's `note_merge`) and by
    #: operator triage. A lookup that lands here FORWARDS to that note rather
    #: than reporting nothing: see `NoteStore.successor`.
    RETIRED_MERGED = "retired-merged"
    #: Never an open question of the group: an entry about Nora's own turn,
    #: or one nobody raised. For operator triage only; no pass writes it.
    RETIRED_WITHDRAWN = "retired-withdrawn"

    #: A legacy `"merged"` value is not in the vocabulary: a live row carrying
    #: it still reads back (`retire_reason` is free text at the column), but
    #: nothing writes it and nothing forwards from it, because its `replaced_by`
    #: was never an open-note guarantee.
    ALL: tuple[str, ...] = (
        RETIRED_EXPIRED, RETIRED_CLOSED, RETIRED_MERGED, RETIRED_WITHDRAWN,
    )


#: How many `retired-merged` hops a forward follows before giving up. A merge
#: always folds into an OPEN note, so a chain only grows when a survivor is
#: itself merged later; a loop is impossible through `merge_into` and bounded
#: here anyway, because a hand-edited row must not hang a lookup.
MERGE_FORWARD_HOPS = 8


# --- note-merge (DEC-202): the `note_merge_decision` table and these constants
# are retained data (DEC-277) ---------------------------------------------

#: The modes recorded on `note_merge_decision` rows. Nothing writes new rows.
NOTE_MERGE_MODES: tuple[str, ...] = ("shadow", "on")

#: What one considered fold came to, recorded per (survivor, duplicate) pair
#: on `note_merge_decision`. The CHECK constraint reads this tuple.
#:
#: - `would_merge` — `shadow` only: every code check passed and nothing was
#:   written. The row the operator labels.
#: - `merged` — `on` only: `merge_into` committed.
#: - `stale` — a head moved between the render and the fold (or the
#:   transaction's own compare-and-swap refused); retried on the next change.
#: - `too_long` — the folded body would pass `BODY_MAX_CHARS`; refused, never
#:   truncated.
#: - `not_open` — one of the two was retired between the render and the fold.
#: - `overlap` — the note is in two groups of one answer; every such group is
#:   refused whole and no transitive closure is taken.
#: - `cross_room` — the group's notes were born in different rooms (v1 folds
#:   same-room groups only).
#: - `not_changed` — no note in the group changed since the last pass, so the
#:   group was already judged; re-judging it every pass would turn one sample
#:   into a union over passes, which fails toward merging.
NOTE_MERGE_OUTCOMES: tuple[str, ...] = (
    "would_merge", "merged", "stale", "too_long", "not_open", "overlap",
    "cross_room", "not_changed",
)

#: The model's `why` as kept on a decision row. A sentence, not a document.
MERGE_REASON_MAX_CHARS = 500


@dataclass(frozen=True, slots=True)
class NoteMergeDecision:
    """One fold the `note-merge` pass considered, as `note_merge_decision` stores
    it: the row the operator labelled in `shadow` and the audit of a fold in
    `on`.

    **Not the shared decision log** (`nora.domain.decision_log`). That table is
    the ambient deciders' shared state: its rows render into the participation
    and proactive prompts, its `reason` is a code label capped at 80 characters,
    and it refuses model text and note titles by its own rule. A merge row needs
    both titles and the model's reason, and must never reach a prompt.

    Titles are stored beside the ids so a row reads on its own; a note's title
    never changes, so the two cannot disagree.
    """

    id: str
    unit: str
    mode: str
    outcome: str
    survivor_id: str
    duplicate_id: str
    survivor_title: str
    duplicate_title: str
    reason: str
    established_by: str
    ts: int

    def __post_init__(self) -> None:
        if self.mode not in NOTE_MERGE_MODES:
            raise ValueError(
                f"merge mode {self.mode!r} is not one of {NOTE_MERGE_MODES}"
            )
        if self.outcome not in NOTE_MERGE_OUTCOMES:
            raise ValueError(
                f"merge outcome {self.outcome!r} is not one of "
                f"{NOTE_MERGE_OUTCOMES}"
            )
        if self.survivor_id == self.duplicate_id:
            raise ValueError("a note cannot be folded into itself")
        if len(self.reason) > MERGE_REASON_MAX_CHARS:
            raise ValueError(
                f"a merge reason is at most {MERGE_REASON_MAX_CHARS} "
                f"characters (got {len(self.reason)}); clip it at the caller"
            )


def latest_anchor(first: int | None, second: int | None) -> int | None:
    """The later of two anchor days, a dated one over a dateless one.

    A survivor that kept its own earlier anchor could be expired on that day
    with the later question folded inside it.
    """
    if first is None:
        return second
    if second is None:
        return first
    return max(first, second)


class StaleNoteHeadError(Exception):
    """The compare-and-swap refused: the head this revision was written
    against is not the head any more.

    **Why a refusal and not a merge, an overwrite, or a silent no-op.** A note's
    value is the *arrangement*: the writer read one body, reasoned over it, and
    is now appending prose that only makes sense against that body. If the head
    moved (a background pass appended mid-turn, or a parallel turn in the other
    room of the same unit revised it), the text being appended answers a
    question that has changed. Writing it on top destroys the intervening
    revision's arrangement with nothing recording that it happened; returning 0
    and carrying on reports «nothing to do» for what was «I could not do it»
    (R-27's `unavailable`-as-`nothing_found`). Raising is the only one of the
    three the caller cannot mistake for success.

    `observed_head` is what the head actually is, so the tool layer's message can
    be «this note changed under you, read it again» rather than a bare failure.
    **`None` means the note is not there at all** *from the caller's unit* —
    absent, retired out of existence, or in someone else's unit, which this
    store deliberately cannot tell apart (a caller must not be able to confirm a
    note exists in a unit it may not read). Both cases have the same remedy,
    which is why they are one exception rather than two: read it again.

    A plain `Exception` rather than `nora.domain.errors.NoraError`: `NoraError`
    carries a `FailureClass`, and how a refused write is *surfaced to the room*
    is the tool layer's decision (`note_update` reports `unavailable`), not this
    type's. Baking INFRASTRUCTURE in here would default the refusal to silence.
    """

    def __init__(self, message: str, *, observed_head: str | None) -> None:
        super().__init__(message)
        self.observed_head = observed_head


class RetiredNoteError(StaleNoteHeadError):
    """The compare-and-swap refused because the note is RETIRED, not because
    its head moved. The append requires the note to be open: `supersede` never
    touches the head, so a writer that lost a race to a retirement would
    otherwise append into a note nothing reads again.

    A SUBCLASS so every existing `except StaleNoteHeadError` still holds
    («read it again» is still the remedy), but its own type, so a lost race to
    a retirement is not counted as an ordinary stale head, and so a caller
    that can forward knows where to: `replaced_by` is the note it was folded
    into when `retire_reason` is `retired-merged`, and `None` otherwise.
    """

    def __init__(
        self, message: str, *, observed_head: str | None,
        retire_reason: str | None, replaced_by: str | None,
    ) -> None:
        super().__init__(message, observed_head=observed_head)
        self.retire_reason = retire_reason
        self.replaced_by = replaced_by

"""`Standing` — Nora's own position in one conversation: one row per
`(platform, conversation_id)`, revised in place by the debrief stage.

**It is a POSITION, not a record**, and that is the whole distinction against
its sibling tiers. A fact is a claim with a warrant; a note is a question with
a closing condition; a lexeme is a meaning. Standing is *what Nora currently
thinks about this conversation*, rewritten every turn and **lossy by design**
— which is exactly why nothing that has to survive may live here.

**Two caps, doing two different jobs:**

- `STANDING_BODY_CEILING_CHARS` — the **absolute ceiling**, asserted in
  `__post_init__` here and again as `standing_body_fits_the_ceiling_check` in
  the database. Not the operating value: a backstop against a config typo or a
  future writer nobody has thought of. A constraint is DDL and cannot be a
  knob, so this is the layer that cannot move.
- `DEFAULT_STANDING_MAX_CHARS` — the default for `[standing] max_chars`, the
  **operating** cap the sink refuses at. Tunable without a migration, because
  the right size is a judgement about prompts and judgements about prompts
  change. Config that exceeds the ceiling refuses to START (`StandingConfig`),
  rather than failing at the first oversized fold.

**Why 1200.** Standing is injected on the volatile tail of EVERY turn, so its
size is a per-turn tax rather than a storage question. 1200 characters is
roughly 300 tokens in English and **approaches 1200 tokens in a CJK-heavy
room** — this room is mixed by default, so the cap has to be read against the
denser case, not the average. And a conversational position that cannot be
stated in 1200 characters is a summary, not a position: the tier is lossy by
design, so a cap that never binds would mean it had stopped being lossy.

Framework-free, per `nora/domain/`'s rule: stdlib and dataclasses only.

**`working_set`.** A
second, small structured field beside `body`: things the fold found this turn
and has not yet given the room — a restaurant a search returned but the reply
only sent two of, not yet a position because nobody has been told. Its own
budget, own render, own TTL; `body`'s own cap, rank order and consolidation
are not touched by this field. See `WorkingSetItem`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from nora.domain.lineage import Lineage

#: The absolute ceiling on `Standing.body`, enforced HERE and by
#: `standing_body_fits_the_ceiling_check`. Deliberately far above the operating
#: cap: it is not the size decision, it is the floor under every size decision
#: an operator can make. See the module docstring.
STANDING_BODY_CEILING_CHARS = 4000

#: The default operating cap — `[standing] max_chars`. The sink refuses a fold
#: above this and tells the model to consolidate; it never truncates, because
#: silently dropping the oldest position is how a rolling summary quietly
#: becomes a recent summary.
DEFAULT_STANDING_MAX_CHARS = 1200

#: The default bound on the debrief's store calls, each bounded by it. It
#: lives here, beside the caps, so `StandingConfig` and `FirePath` share one
#: number: a number asserted in two places without a cross-check drifts.
DEFAULT_STANDING_STORE_TIMEOUT_SECONDS = 5.0

#: How many bounded store awaits ONE fold makes, and therefore how many
#: `store_timeout_seconds` the slot hold has to budget for: the prior
#: position, the watermark batch read (`record_store.since_including_nora` —
#: the fold reads the stretch since the last fold's watermark), the media
#: join, and the revised position.
#:
#: **It lives HERE and not in the stage**, even though the stage is what makes
#: the calls. `Config._the_standing_slot_hold_fits_its_budget` needs it, and a
#: module-scope `from nora.engagement.stages.debrief import ...` in
#: `nora.config` violates that file's own documented rule — `nora.config` is
#: imported by everything, so it would drag `nora.engagement` (and through it
#: `nora.agent.tools`) into every process that merely reads config.
#: `nora.domain` is already imported at module scope on the line above, and it
#: is where a number two packages must agree on belongs.
#:
#: `test_the_slot_hold_counts_every_store_await_the_stage_makes` counts the
#: `asyncio.wait_for` calls in the stage and asserts they match, so adding a
#: fifth fails a test instead of silently widening the hold.
STANDING_STORE_CALLS_PER_FOLD = 4

#: The ABSOLUTE ceiling on how many items `Standing.working_set` may hold —
#: the `working_set` sibling of `STANDING_BODY_CEILING_CHARS`: asserted HERE,
#: in `Standing.__post_init__`, and again as
#: `standing_working_set_item_count_fits_the_ceiling_check` in the database.
#: Not the operating value (`[standing] working_set_max_items`, `StandingConfig`)
#: — a backstop against a config typo, far above the operating default the same
#: way 4000 sits above 1200 for the body.
WORKING_SET_CEILING_ITEMS = 20

#: The ABSOLUTE ceiling on the rendered JSON size, in BYTES, of
#: `Standing.working_set` as a whole — asserted HERE and again as
#: `standing_working_set_fits_the_ceiling_check` in the database. `octet_length`
#: rather than a character count because the check on the Postgres side reads
#: the jsonb value cast to `text`, which is byte-denominated; this domain-side
#: figure is computed from the same field order (`_working_set_payload`) and is
#: a close, not exact, match to Postgres's own re-serialisation (jsonb
#: normalises key order and spacing on the way back out) — close enough for a
#: backstop that exists to catch a typo, not to be a second copy of Postgres's
#: serialiser.
WORKING_SET_CEILING_BYTES = 2400

#: Render-time TTL: a working-set item older than this renders
#: ABSENT, never stale — no new deterministic sweep, the render function
#: filters on `now - found_at` before it builds the block. The same order as
#: DEC-203's two-day past-anchor lapse.
WORKING_SET_TTL_HOURS = 48.0

#: The default OPERATING cap — `[standing] working_set_max_items` —
#: `DEFAULT_STANDING_MAX_CHARS`'s sibling: how many items the write path
#: keeps before refusing the whole set and keeping the prior's (§1). Its own
#: budget, separate from the body's 1200 characters, on purpose: a found
#: option is not prose competing with "concluded or committed" clauses for
#: the same cap under a rank order that puts it last.
DEFAULT_WORKING_SET_MAX_ITEMS = 5

#: The default OPERATING cap — `[standing] working_set_max_chars` — the
#: rendered JSON size (all items combined) the write path refuses at.
#: Refuse-and-keep-the-prior on overflow, never a silent truncation, the same
#: argument `DEFAULT_STANDING_MAX_CHARS` makes for the body.
DEFAULT_WORKING_SET_MAX_CHARS = 600


@dataclass(frozen=True, slots=True)
class WorkingSetItem:
    """One thing the fold found this turn and has not yet given the room —
    `Standing.working_set`'s element.

    Not prose: a working-set item is a short, independently droppable string,
    not a clause competing inside the 1200-character paragraph for the same
    rank `body`'s own consolidation order already gives "concluded or
    committed" over "found".

    `found_at` is epoch ms, the SAME clock `Standing.updated_at` uses — **the
    write path stamps it from its own instant; the model is never asked for
    it and a model-supplied value is never trusted**, for `Standing`'s own
    rule: "there is no second clock". `source` names the step (a tool call,
    named) the item came from — provenance constrained to what the fold was
    actually shown, never a paraphrase of the room's question. `bears_on` is
    nullable: filled only when an open plan already named in the position
    tells the reader what the item is FOR.
    """

    what: str
    source: str
    found_at: int
    bears_on: str | None = None

    def __post_init__(self) -> None:
        if not self.what or not self.what.strip():
            raise ValueError("a WorkingSetItem needs a what")
        if not self.source or not self.source.strip():
            raise ValueError("a WorkingSetItem needs a source")
        if self.found_at < 0:
            raise ValueError("found_at is never negative")
        if self.bears_on is not None and not self.bears_on.strip():
            # `None` is "no plan named"; a value has to be a real one, not a
            # second spelling of absence — the same argument
            # `last_folded_row_id`'s `None`-vs-`0` split makes.
            raise ValueError("bears_on is a plan or None, never a blank string")


def _working_set_payload(
    items: tuple[WorkingSetItem, ...],
) -> list[dict[str, object]]:
    """`working_set`, as the plain JSON-shaped value both the store and the
    ceiling check serialise — ONE mapping, so a field added to
    `WorkingSetItem` cannot land in the database and not in the ceiling
    check, or the reverse."""
    return [
        {
            "what": item.what, "source": item.source,
            "found_at": item.found_at, "bears_on": item.bears_on,
        }
        for item in items
    ]


def working_set_to_json(items: tuple[WorkingSetItem, ...]) -> list[dict[str, object]]:
    """`Standing.working_set` as the value `SqlStandingStore`/
    `InMemoryStandingStore` hand to a jsonb column — a plain list of dicts,
    which SQLAlchemy's asyncpg dialect serialises itself (`tool_call_log`'s
    own precedent: a JSONB column is written the Python value directly, never
    a hand-rolled `json.dumps`)."""
    return _working_set_payload(items)


def working_set_from_json(raw: object) -> tuple[WorkingSetItem, ...]:
    """The inverse of `working_set_to_json` — a stored jsonb value (already
    decoded to Python by the driver) back into typed items.

    `None` and anything not a list read as "no working set" rather than
    raising: a row written before this column existed reads back `[]` from
    the column's own `server_default`, but a defensive reader costs nothing
    and a NULL is not a state this column should be able to reach malformed.
    """
    if not isinstance(raw, list):
        return ()
    items: list[WorkingSetItem] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        what = entry.get("what")
        source = entry.get("source")
        found_at = entry.get("found_at")
        bears_on = entry.get("bears_on")
        if not isinstance(what, str) or not isinstance(source, str):
            continue
        if not isinstance(found_at, int):
            continue
        items.append(WorkingSetItem(
            what=what, source=source, found_at=found_at,
            bears_on=bears_on if isinstance(bears_on, str) else None,
        ))
    return tuple(items)


@dataclass(frozen=True, slots=True)
class Standing:
    """One conversation's position, as last folded.

    Keyed `(platform, conversation_id)` and not on `conversation_id` alone:
    every lock, queue and slot in this repo already keys that way and the
    `messages` table carries the platform column, so a different key here is a
    join nobody can make.

    `fold_count` is how many folds have been written onto this row; nothing
    resets it.

    **No per-turn field lives here.** A per-turn fact on a row revised every
    turn is a fact with a lifetime of one turn.
    """

    platform: str
    conversation_id: str
    body: str
    fold_count: int
    updated_at: int
    # The ROW-ID watermark: the `messages.id`
    # the last fold read up to. `None` means the conversation has never folded
    # — the cold-start case, which reads from the beginning (after_id=0). A
    # sentinel that could compare equal to a real row id is a watermark
    # satisfiable by accident, so the only "never folded" spelling is `None`.
    last_folded_row_id: int | None = None
    # Things the fold found this turn and has not yet given the room — its
    # own budget, own render, own TTL (module docstring). `()` is the
    # correct reading of a row written before this field existed: no backfill.
    # LAST, because a defaulted field cannot precede the ones above it.
    working_set: tuple[WorkingSetItem, ...] = ()
    # WHERE IT MAY BE SHOWN (migration `0073_lineage`): the
    # `(kind, subject, origin room)` set its citations reach, and the people it
    # is never shown to. Written by the store from
    # the row's own citations, never by a model; read at render by
    # `nora.agent.lineage`. `compare=False`: equality stays about content, and
    # a row built by hand (no lineage, every section unknown) is on the
    # fail-closed side.
    lineage: Lineage = field(default=Lineage(), compare=False)
    exclude_persons: tuple[str, ...] = field(default=(), compare=False)
    # THE SECTIONED SCHEMA (migration `0076_standing_sections`). Room
    # Standing has four sections: `Where it stands` (required, `body` above),
    # `I hold open`, `I owe` and `Found, not yet given` (`working_set`
    # above). These two are OPTIONAL — `None` is "nothing revised this
    # section yet". Written and
    # read only through `standing_revise`
    # (`nora.infra.sql.standing_revise`), which enforces the schema (a
    # section given blank rather than omitted is refused) before either
    # reaches this dataclass.
    hold_open: str | None = None
    i_owe: str | None = None

    def __post_init__(self) -> None:
        if not self.body or not self.body.strip():
            raise ValueError("a Standing needs a body")
        if len(self.body) > STANDING_BODY_CEILING_CHARS:
            raise ValueError(
                f"Standing.body is {len(self.body)} characters, over the "
                f"{STANDING_BODY_CEILING_CHARS}-character ceiling"
            )
        if self.fold_count < 0:
            raise ValueError("fold_count is never negative")
        if (self.last_folded_row_id is not None
                and self.last_folded_row_id < 1):
            # `None` means "never folded" and the read starts from the
            # beginning; `0` would be a second spelling of that state that
            # compares unequal to it, and messages ids start at 1 anyway.
            raise ValueError("last_folded_row_id is a row id or None")
        if len(self.working_set) > WORKING_SET_CEILING_ITEMS:
            raise ValueError(
                f"Standing.working_set holds {len(self.working_set)} items, "
                f"over the {WORKING_SET_CEILING_ITEMS}-item ceiling"
            )
        rendered_bytes = len(json.dumps(
            _working_set_payload(self.working_set),
        ).encode("utf-8"))
        if rendered_bytes > WORKING_SET_CEILING_BYTES:
            raise ValueError(
                f"Standing.working_set is {rendered_bytes} bytes rendered, "
                f"over the {WORKING_SET_CEILING_BYTES}-byte ceiling"
            )
        for name, value in (("hold_open", self.hold_open), ("i_owe", self.i_owe)):
            if value is not None and not value.strip():
                # `None` is "not revised"; a blank string is a second
                # spelling of the same absence and is refused rather than
                # stored — `WorkingSetItem.bears_on`'s own rule, applied to
                # these two sections.
                raise ValueError(
                    f"Standing.{name} is a section body or None, never a "
                    f"blank string"
                )
            if value is not None and len(value) > STANDING_BODY_CEILING_CHARS:
                raise ValueError(
                    f"Standing.{name} is {len(value)} characters, over the "
                    f"{STANDING_BODY_CEILING_CHARS}-character ceiling"
                )


@dataclass(frozen=True, slots=True)
class StandingHead:
    """A room's Standing head with the id of the `standing_revision` row that
    IS that head (`standing_read`). `revision_id` is `None` when the chain holds no revision
    that matches the head: the chain predates migration 0059 for that room, or
    it was written outside `SqlStandingStore.write`. `None` is «no token», never
    a guess at the newest row: an id that named another position would let a
    stale `standing_revise` pass."""

    standing: Standing
    revision_id: str | None

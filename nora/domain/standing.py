"""`Standing` — Nora's own position in one conversation: one row per
`(platform, conversation_id)`, revised in place by the debrief stage
(`design/dreamer-agent-loop/26-standing.md`).

**It is a POSITION, not a record**, and that is the whole distinction against
its sibling tiers. A fact is a claim with a warrant; a note is a question with
a closing condition; a lexeme is a meaning. Standing is *what Nora currently
thinks about this conversation*, rewritten every turn and **lossy by design**
(26 §8) — which is exactly why nothing that has to survive may live here.

**Two caps, doing two different jobs** (26 §4.0, operator decision
2026-08-24). An earlier draft of that node argued a storage cap is an invariant
and therefore must never be a knob; the operator overrode it, and the
reconciliation kept both halves:

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
"""

from __future__ import annotations

from dataclasses import dataclass

#: The absolute ceiling on `Standing.body`, enforced HERE and by
#: `standing_body_fits_the_ceiling_check`. Deliberately far above the operating
#: cap: it is not the size decision, it is the floor under every size decision
#: an operator can make. See the module docstring.
STANDING_BODY_CEILING_CHARS = 4000

#: The default operating cap — `[standing] max_chars`. The sink refuses a fold
#: above this and tells the model to consolidate; it never truncates, because
#: silently dropping the oldest position is how a rolling summary quietly
#: becomes a recent summary (26 §4.0).
DEFAULT_STANDING_MAX_CHARS = 1200

#: The default bound on the debrief's TWO store calls — the read of the prior
#: position and the write of the next. It lives here, beside the caps, rather
#: than being written `5.0` in `StandingConfig` and again in `FirePath`: the
#: branch's own argument is that a number asserted in two places without a
#: cross-check drifts, and this file already holds the other two (review
#: finding, 2026-08-27).
DEFAULT_STANDING_STORE_TIMEOUT_SECONDS = 5.0

#: How many bounded store awaits ONE fold makes, and therefore how many
#: `store_timeout_seconds` the slot hold has to budget for: the prior
#: position, the watermark batch read (`record_store.since_including_nora` —
#: the fold reads the stretch since the last fold's watermark,
#: standing-watermark 20), the media join, the revised position, and the note
#: proposals.
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
#:
#: 6 -> 7 on 2026-09-08: the media join (issue #116). The fold renders these
#: rows for a model, so it reads `media_objects` for whatever the
#: comprehension pass made of each attachment — a real store call on a
#: slot-held path, bounded by the same `store_timeout_seconds` as its six
#: siblings. The gate caught it the moment the call was written, which is what
#: it is for; the number was understated by 2x once before because nobody
#: updated it by hand.
#:
#: 7 -> 5 on 2026-09-24: the turn verdict's save and the re-derivation
#: trigger's verdict read went with the verdict (design/standing-compact/).
#:
#: 5 -> 4 the same day: the debrief stopped creating notes, and
#: `notes_sink.propose` went with them.
STANDING_STORE_CALLS_PER_FOLD = 4


@dataclass(frozen=True, slots=True)
class Standing:
    """One conversation's position, as last folded.

    Keyed `(platform, conversation_id)` and not on `conversation_id` alone
    (S4): every lock, queue and slot in this repo already keys that way and the
    `messages` table carries the platform column, so a different key here is a
    join nobody can make.

    `fold_count` is how many folds have been written onto this row. It fed
    the fold-count re-derivation ceiling until 2026-09-24
    (design/standing-compact/10 §1); it is a plain count now and nothing
    resets it.

    **No per-turn field lives here.** A per-turn fact on a row revised every
    turn is a fact with a lifetime of one turn (26 §3).
    """

    platform: str
    conversation_id: str
    body: str
    fold_count: int
    updated_at: int
    # The ROW-ID watermark (design/standing-watermark/20): the `messages.id`
    # the last fold read up to. `None` means the conversation has never folded
    # — the cold-start case, which reads from the beginning (after_id=0). A
    # sentinel that could compare equal to a real row id is a watermark
    # satisfiable by accident, so the only "never folded" spelling is `None`.
    last_folded_row_id: int | None = None

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

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
#: `store_timeout_seconds` the slot hold has to budget for: the trigger's
#: verdict read, the prior position, the revised position, the turn verdict,
#: and the note proposals.
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
#: sixth fails a test instead of silently widening the hold.
STANDING_STORE_CALLS_PER_FOLD = 5


@dataclass(frozen=True, slots=True)
class Standing:
    """One conversation's position, as last folded.

    Keyed `(platform, conversation_id)` and not on `conversation_id` alone
    (S4): every lock, queue and slot in this repo already keys that way and the
    `messages` table carries the platform column, so a different key here is a
    join nobody can make.

    `fold_count` is how many folds have been written onto this row since the
    last re-derivation. It is the ceiling half of S2's re-derivation trigger
    (forced when a turn is ungrounded, PLUS a fold-count ceiling), and it lives
    on the row rather than being counted from `tag_trace` because the traces
    are retained for a bounded window and the count must survive their prune.

    **No per-turn field lives here.** That split was found by a bug (26 §3):
    the first draft put a per-turn flag on the per-conversation row, and the
    re-derivation trigger then read a flag the NEXT turn had already
    overwritten. A per-turn fact on a row revised every turn is a fact with a
    lifetime of one turn. `TurnVerdict` is where those go.

    `consumed_verdict_ms` is the `created_at` of the ungrounded `TurnVerdict`
    that a re-derivation ACTED ON — S2's watermark, and **the reason it is
    state rather than a timestamp comparison.**

    The first implementation asked "is the latest ungrounded verdict newer than
    the position's last write", which uses `updated_at` as a PROXY for "a
    rebuild consumed this verdict". Any proxy is falsifiable by a path that
    moves it without doing the thing, and this one has such a path: if
    `TurnVerdictStore.latest` fails for a single turn, the trigger correctly
    declines to fire (R-27 — an unreadable instrument is «I could not check»),
    the fold then REVISES normally and advances `updated_at` past the verdict,
    and the next turn's comparison is false forever. One transient read failure
    and an ungrounded verdict is not deferred but GONE — «I could not check»
    silently converted into «there was nothing», which is the exact
    substitution the debrief's own handler exists to prevent. Driven through
    the real stage over three turns; the verdict was dropped and the counter
    read zero (cold review, 2026-08-28).

    So the row records what was consumed. `None` means no re-derivation has
    ever acted on a verdict here, which is why it is nullable rather than `0`:
    a sentinel that compares equal to a real `created_at` is a watermark that
    can be satisfied by accident. It advances ONLY when an ungrounded-triggered
    rebuild is WRITTEN — never on a refusal (so the retry survives), and never
    on a `ceiling` rebuild, which has not looked at the verdict and must not
    claim to have consumed it.
    """

    platform: str
    conversation_id: str
    body: str
    fold_count: int
    updated_at: int
    consumed_verdict_ms: int | None = None

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
        if self.consumed_verdict_ms is not None and self.consumed_verdict_ms < 0:
            raise ValueError("consumed_verdict_ms is never negative")

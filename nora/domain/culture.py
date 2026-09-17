"""An account of how a room works and why, kept over time.

`design/culture-tier`. The tier exists because of one gap and it is worth
stating before any type: **a chronicle is not a culture.**

The memory system's `representation` returns exactly one shape —
`[when] what happened` — every row pinned to an instant and independent of
every other. A list of dynasties and their dates is not a culture: the format
has no slot for continuity or causation, so 「漢人」 — a term that outlived its
dynasty, meant something different each century, and is *why* people still say
it — cannot be expressed in it. Frequency is not continuity.

Culture is an **account**: what this is, and why it became so. That is what a
model produces when asked for prose over a room's own messages, and this module
is the shape it is kept in.

**What it is for.** The Relevance Gate admitted about 1% of windows over seven
days. That is not a mistuned threshold: a window judged against nothing is
judged in a vacuum — "nobody picked this up" means nothing until you know
whether this room normally picks things up. A baseline is what turns a window
into a DEVIATION, and a deviation is readable by a model with no reasoning
budget, because the reasoning already happened offline.

**Three decisions are load-bearing and each is stated on the thing it shapes:**
`Culture.head_revision_id` (the chain, DEC-182), `CultureRevision.parent_revision_id`
(nothing retires, DEC-183), and `CultureRevision.body` (prose, no sections,
DEC-184).
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "ACCOUNT_OUTPUT_TOKENS",
    "BODY_MAX_CHARS",
    "Culture",
    "CultureProposal",
    "CultureRevision",
]

#: The OPERATING cap on an account, in characters. The database ceiling
#: (`CULTURE_BODY_CEILING_CHARS`, 32,000) is far above it and the gap is
#: deliberate, the same split `notes` makes: this number says "no writer should
#: produce that", the ceiling says "this row is corrupt".
#:
#: **1,600, DOWN FROM 8,000 on 2026-09-17, and the two reasons are
#: independent.**
#:
#: *What the objective asks for.* `nora/dreamer/objectives/culture.md` says
#: "one paragraph or two" and prints a worked example body of ~890 characters.
#: 1,600 is 1.8x that example: room for the second paragraph the objective
#: allows, and nothing left for a third. 8,000 invited the essay — it was nine
#: times the operator's own example, so the cap said "short account" while
#: admitting something no reader would call one.
#:
#: *What can physically be emitted.* This is a cap in CHARACTERS on prose a
#: model must produce under two ceilings measured in TOKENS, and nobody had
#: written down the bridge between the units. It is: **one output token per
#: character**, the CJK worst case — this room's prose is mostly Cantonese, and
#: a cap that assumed English would be wrong by a factor of two in the
#: direction that truncates. `ACCOUNT_OUTPUT_TOKENS` below is that conversion,
#: and it is what the two ceilings are checked against
#: (`tests/unit/test_culture_adaptor.py`). At 8,000 characters the conversion
#: gives ~8,000 tokens against a default loop budget of 4,000 and a shipped
#: provider ceiling of 2,000, so an account written to the cap could not be
#: emitted at all — **and the failure is silent and self-perpetuating**: the
#: completion truncates, `parse_envelope` reads no envelope, the run is
#: `unavailable`, the watermark is HELD, and the same batch is re-read and
#: re-truncated on the next fire, forever. That is `note-candidates`'
#: `AdaptorSchedule` trap (`nora/app/composition.py`) reached from the other
#: end: there the batch was too big for the output, here the output cap was.
BODY_MAX_CHARS = 1_600

#: What emitting an account at `BODY_MAX_CHARS` costs in OUTPUT TOKENS, worst
#: case — the one place the character cap and the token budgets are related, so
#: that they are not two independently chosen numbers again.
#:
#: `BODY_MAX_CHARS` at one token per character (above), plus the JSON envelope
#: the objective requires around it (`{"account": [{"body": "…"}]}`, its
#: escaping, and the newline the provider usually adds).
#:
#: **Two ceilings must clear this, and they are different things** (see
#: `config.example.toml`'s `[dreamer] max_tokens`, which spells the confusion
#: out):
#:
#: - `DreamBudgetConfig.output_tokens` — the Strands LOOP budget, 4,000 by
#:   default, spent across every turn of the fold. The rule is that the loop
#:   must be able to emit a full account TWICE: once for the tool-call
#:   arguments and any preamble the pass spends before it writes, once for the
#:   account itself. 2 x 1,664 = 3,328, inside the 4,000 an adaptor with no
#:   `[dreamer.budgets.culture]` block gets.
#: - `[dreamer] max_tokens` (0 -> `[llm] max_tokens`) — the PROVIDER's ceiling
#:   on ONE completion, which binds as `MaxTokensReachedException` ->
#:   `PartialReplyError` rather than as a `stop_reason`. `config.example.toml`
#:   ships 2,000, so an account at the cap uses ~83% of one completion. That is
#:   the tighter of the two and the one to raise if a fold is ever seen
#:   truncating — the example file says so in those words.
#:
#: Both inequalities are asserted rather than argued
#: (`tests/unit/test_culture_adaptor.py::test_an_account_at_the_cap_fits_...`).
ACCOUNT_OUTPUT_TOKENS = BODY_MAX_CHARS + 64


@dataclass(frozen=True, slots=True)
class Culture:
    """One scope's culture: a key, and a pointer at the current account.

    **`key` is a `primary_id`.** Not `(platform, conversation_id)` — the rule is
    `GLOSSARY.md`'s and it follows from which scheduler hands the key. A Dreamer
    adaptor is handed `scopes()`, so it keys the conversation; the ingest and tag
    paths get the message that arrived, so they key the thread. A store keyed
    differently from its loop matches zero rows on the watermark query, and then
    the adaptor is scheduled, raises nothing, logs nothing and never fires.

    **`head_revision_id` is a pointer, not a copy of the body.** It is what makes
    the chain a chain: the head is one link, and walking `parent_revision_id`
    back from it is the room's 演變. A single account is a snapshot — the model
    that wrote it had no memory of the one before — so the ORDER is the only
    thing here that a per-turn prompt could not re-derive.
    """

    key: str
    head_revision_id: str
    updated_at: int


@dataclass(frozen=True, slots=True)
class CultureRevision:
    """One account, and its link to the account before it.

    **`body` is prose and there is no section schema (DEC-184).** A
    three-section JSONB body under a CHECK was drafted and rejected: it is the
    same enumeration error as `participation.md`'s five `go` shapes one layer
    down, and it could not have held 「秘書」 — a role a room invented that no
    column could have anticipated. Requiring a "what shifted" section also makes
    the first revision unwritable, or invites a `reasoning_effort = "none"`
    model to confabulate a diff to satisfy a constraint.

    **`parent_revision_id` means "the same thing, earlier" (DEC-183).** Never
    "superseded". Nothing in this tier retires: there is no `retired_at`, no
    `closing_condition` and no TTL, because a quiet room's culture is not stale,
    it is accurate. The old account does not become false — it becomes history,
    and the history is part of what the new one means.

    `None` is the FIRST revision of a key and nothing else.

    **The provenance three are the RUNTIME's**, never the adaptor's. The runtime
    read the instructions, built the model provider and named the run, so
    `accept` returns payloads and the runtime wraps them. `instruction_sha256`
    and `model` are nullable only because a row written by something other than
    a Dreamer pass would have neither; every row this tier writes has both.
    """

    id: str
    culture_key: str
    body: str
    written_at: int
    established_by: str
    parent_revision_id: str | None = None
    instruction_sha256: str | None = None
    model: str | None = None


@dataclass(frozen=True, slots=True)
class CultureProposal:
    """What the adaptor's `accept` returns: an account and nothing else.

    No id, no parent, no provenance and no timestamp — every one of those is
    decided by something that is not the model. The parent is whatever the head
    is at commit time, the provenance is the runtime's, and the clock is the
    store's. A proposal that carried them would be four more places for a model
    to be believed about something it cannot know.
    """

    body: str

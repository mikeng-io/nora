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
from math import ceil

__all__ = [
    "ACCOUNT_OUTPUT_TOKENS",
    "BODY_MAX_CHARS",
    "OUTPUT_TOKENS_PER_CHAR",
    "Culture",
    "CultureProposal",
    "CultureRevision",
]

#: **MEASURED, 2026-09-17** — what one character of a culture account costs in
#: OUTPUT TOKENS, and the one place the character cap and the token budgets are
#: related, so that they are not two independently chosen numbers again.
#:
#: The bridge was `1.0` and asserted rather than taken. Taken, with
#: `tiktoken`'s `o200k_base` over a realistic account for this deployment's
#: room — dense Cantonese, Latin technical terms unlocalised (`deploy`,
#: `latency`), an emoji nickname, the room's own coined names — inside the
#: JSON envelope the objective requires:
#:
#: | what was encoded | tok/char |
#: |---|---|
#: | the objective's own worked example (English prose, 1% CJK) | **0.227** |
#: | a realistic dense-Cantonese account (89% CJK) | **1.094** |
#: | continuous Cantonese prose, no Latin | **1.131** |
#: | Cantonese-specific particles alone (`𡃁嘥揼冚唞氹`) | **2.080** |
#:
#: `1.15` is the continuous-prose worst case with margin. The particle string
#: is real — those characters fall to BYTE-level tokens, 2-3 each — but it is
#: not prose; no account is written in it, and sizing every budget for it
#: would triple the Dreamer's ceiling against a shape nothing produces.
#:
#: **What this number does NOT cover, stated because the omission is the whole
#: risk.** A model that emits `\uXXXX` ESCAPES instead of raw UTF-8 pays
#: **3.117 tok/char — 2.85x** this figure (measured the same way,
#: `json.dumps(..., ensure_ascii=True)`). That is the MODEL's choice of
#: emission, not the transport's: the HTTP frame's own escaping costs no output
#: tokens, so this only bites when the model itself writes escapes. When it
#: does, an account at the cap costs ~4,400 tokens against a 2,000-token
#: completion, the completion truncates, and the run lands in the HELD branch
#: below — loud, recoverable, and fixed by `[dreamer] max_tokens`, which is the
#: knob `config.example.toml` already names for exactly this. It is not fixed
#: by lowering the cap, and the cap does not pretend to.
OUTPUT_TOKENS_PER_CHAR = 1.15

#: The OPERATING cap on an account, in characters. The database ceiling
#: (`CULTURE_BODY_CEILING_CHARS`, 32,000) is far above it and the gap is
#: deliberate, the same split `notes` makes: this number says "no writer should
#: produce that", the ceiling says "this row is corrupt".
#:
#: **WHAT IT GUARDS, and what it does not.** It guards ONE thing: an account
#: that has drifted back into chronicle — restating what happened instead of
#: saying what the room is — does not enter the chain, where nothing retires
#: and every link is indelible. That is a QUALITY refusal and it is the only
#: claim this constant is entitled to make.
#:
#: **It does NOT guard the self-perpetuating truncation loop**, and an earlier
#: version of this comment said it did. The cap is checked in
#: `nora.language.culture.CultureAdaptor.accept`, which only ever sees a body
#: the model ALREADY EMITTED IN FULL — so by the time this number is read, the
#: truncation it was said to prevent either did not happen or happened one
#: layer up, where no envelope was parsed at all. What governs truncation is
#: `[dreamer] max_tokens` (0 -> `[llm] max_tokens`), the PROVIDER's ceiling on
#: one completion. Two different failures, two different constants; the merged
#: version of this paragraph is what let 8,000 be replaced by a number derived
#: from the wrong one of them.
#:
#: **1,400, and both ends of it are measured.**
#:
#: *From above — what can be emitted.* `ACCOUNT_OUTPUT_TOKENS` below converts
#: this cap into output tokens at the MEASURED `OUTPUT_TOKENS_PER_CHAR`. The
#: shipped per-completion ceiling is 2,000 (`config.example.toml`'s `[llm]
#: max_tokens`, inherited because `[dreamer] max_tokens = 0`), and the default
#: loop budget is 4,000 and must emit a full account TWICE. 1,400 spends
#: **83.7% of each** — headroom that is real rather than nominal. 1,600, the
#: number this PR first carried, spends 95% of both once the bridge is measured
#: rather than assumed, which is the edge and not a derivation.
#:
#: *From below — what the objective asks for.*
#: `nora/dreamer/objectives/culture.md` says "one paragraph or two" and prints
#: a worked example body of **739 characters** (extracted, not eyeballed;
#: `tests/unit/test_culture_adaptor.py` re-extracts it and pins the ratio).
#: 1,400 is **1.89x** that example: room for the second paragraph the
#: objective allows, and nothing left for a third. An earlier version of this
#: comment said "~890 characters" and "1.8x" and both were wrong — the example
#: is 739 and 1,600 was 2.17x it.
#:
#: 8,000, until 2026-09-17, was 10.8x the operator's own example: the cap said
#: "short account" while admitting something no reader would call one.
BODY_MAX_CHARS = 1_400

#: What emitting an account at `BODY_MAX_CHARS` costs in OUTPUT TOKENS.
#:
#: `BODY_MAX_CHARS` at the MEASURED `OUTPUT_TOKENS_PER_CHAR` — rounded UP,
#: because rounding a fail-closed conversion down is the one direction that
#: lets the two assertions below pass while the account truncates — plus 64 for
#: the JSON envelope the objective requires around it
#: (`{"account": [{"body": "…"}]}`, its escaping, and the newline the provider
#: usually adds), measured at 15 tokens for a one-account envelope, so 64 is
#: four times the observed overhead.
#:
#: **Two ceilings must clear this, and they are different things** (see
#: `config.example.toml`'s `[dreamer] max_tokens`, which spells the confusion
#: out):
#:
#: - `DreamBudgetConfig.output_tokens` — the Strands LOOP budget, 4,000 by
#:   default, spent across every turn of the fold. The rule is that the loop
#:   must be able to emit a full account TWICE: once for the tool-call
#:   arguments and any preamble the pass spends before it writes, once for the
#:   account itself. 2 x 1,674 = 3,348, inside the 4,000 an adaptor with no
#:   `[dreamer.budgets.culture]` block gets.
#: - `[dreamer] max_tokens` (0 -> `[llm] max_tokens`) — the PROVIDER's ceiling
#:   on ONE completion, which binds as `MaxTokensReachedException` ->
#:   `PartialReplyError` rather than as a `stop_reason`. `config.example.toml`
#:   ships 2,000, so an account at the cap uses ~84% of one completion. That is
#:   the tighter of the two and the one to raise if a fold is ever seen
#:   truncating — the example file says so in those words, and the escape case
#:   in `OUTPUT_TOKENS_PER_CHAR` above is the reading that would raise it.
#:
#: Both inequalities are asserted rather than argued
#: (`tests/unit/test_culture_adaptor.py::test_an_account_at_the_cap_fits_...`).
ACCOUNT_OUTPUT_TOKENS = ceil(BODY_MAX_CHARS * OUTPUT_TOKENS_PER_CHAR) + 64


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

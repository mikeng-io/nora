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

import re
from dataclasses import dataclass, field
from math import ceil
from typing import TYPE_CHECKING

from nora.domain.lineage import Lineage

if TYPE_CHECKING:  # `nora.domain.models` imports this module
    from nora.domain.models import TranscriptRow

__all__ = [
    "ACCOUNT_CHARS_OBSERVED_MAX",
    "ACCOUNT_OUTPUT_TOKENS",
    "BODY_MAX_CHARS",
    "CHARACTER_KINDS",
    "CHARACTER_OUTCOMES",
    "CULTURE_OUTCOMES",
    "MAX_CONSECUTIVE_REJECTIONS",
    "OUTPUT_TOKENS_PER_CHAR",
    "REVISE_REFUSALS",
    "Citable",
    "Culture",
    "CultureProposal",
    "CultureRevision",
    "Refusal",
    "RoomMessage",
    "character_key",
    "check_revision",
]

#: **MEASURED, 2026-09-17** — what one character of a culture account costs in
#: OUTPUT TOKENS, and the one place a length in characters and a budget in
#: tokens are related, so that they are not two independently chosen numbers
#: again. The length it converts is `ACCOUNT_CHARS_OBSERVED_MAX`, what the
#: model is seen to emit — NOT `BODY_MAX_CHARS`, what we decline to record.
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
#: does, a realistic account costs ~5,850 tokens instead of ~2,222, the
#: completion truncates, and the run lands in the HELD branch below — loud,
#: recoverable, and fixed by `[dreamer] max_tokens`, which is the knob
#: `config.example.toml` already names for exactly this. It is not fixed by
#: lowering `BODY_MAX_CHARS`, and that cap does not pretend to.
OUTPUT_TOKENS_PER_CHAR = 1.15

#: The OPERATING cap on an account, in characters. The database ceiling
#: (`CULTURE_BODY_CEILING_CHARS`, 32,000) is above it and the gap is
#: deliberate, the same split `notes` makes: this number says "no writer should
#: produce that", the ceiling says "this row is corrupt".
#:
#: **20,000 (operator, 2026-09-18), and what changed is what this number IS.**
#: It was 1,400 for about twenty minutes of production and the reading that
#: ended it is written below, because it is the whole justification and it cost
#: a room its account.
#:
#: *What 1,400 was.* A QUALITY refusal, sized as 1.89x the objective's own
#: 739-character worked example — "room for the second paragraph the objective
#: allows, and nothing left for a third". Both ends of it were derived and
#: neither end was the model. Nothing had asked what an account actually comes
#: out at.
#:
#: *What the model actually writes.* Measured on norty the evening 1,400
#: shipped, every account the live fold produced or tried to, n=10 across five
#: rooms:
#:
#: | outcome | characters |
#: |---|---|
#: | written | 563, 811, 1,095, 1,316, 1,323 |
#: | REFUSED | 1,583, 1,677, 1,725, 1,812, 1,826, 1,876 |
#:
#: **6 of 11 draws were refused — 55%.** The cap sits above every account that
#: landed and below every one that did not, and 1,316 is 94% of it, so it is
#: not a bar the writer clears with room to spare; it is a coin the fold
#: tosses every fire.
#:
#: **CORRECTED 2026-09-18, and the correction matters.** The first version of
#: this comment said the busiest room (a Discord games room,
#: `100000000000000201/…`) "never got an account at all" and "re-folded
#: forever". Both were wrong, and they were written while the room was still
#: mid-sequence. It was refused four times — 1,876, 1,725, 1,677, 1,583 — and
#: then its FIFTH draw came in at 1,323 and was recorded. The Signal room
#: `MP4ODQ…` did the same thing two fires earlier: refused at 1,812 and 1,826,
#: written at 1,316.
#:
#: So the wedge is a LOTTERY, not a deadlock: `accept` raises, the Dreamer
#: holds the watermark, and the fold re-draws until one lands under the cap —
#: one model call per tick, for a geometrically distributed number of ticks at
#: a 55% refusal rate. Zone 42 paid four calls and 26 minutes. That is a real
#: cost and it is why this number moved, but "forever" was an overclaim made
#: from a four-point window, and the fifth point was already on its way.
#:
#: *What 20,000 is.* Not a quality refusal — it is a CORRUPTION ceiling one
#: notch under the database's, and it is stated plainly that this tier now has
#: no automatic guard against an account drifting back into chronicle. That
#: guard is the objective's job and always was: the `accept` docstring already
#: says so, and the change that would restore it is a line in "What to write"
#: naming the worked example AS the target length. That is a prompt change
#: under `change_policy: operator-only` and it needs the arm that measures it.
#: Refusing 13%-over prose in the meantime bought nothing and cost the chain
#: the one room with the most to say.
#:
#: **The cap is no longer what sizes the token budgets.** That coupling is what
#: made this number look expensive to move, and it was wrong on its own terms:
#: a cap is a refusal threshold, not a reservation. See
#: `ACCOUNT_CHARS_OBSERVED_MAX` below, which is what the budgets are sized on
#: now — a measurement of what gets emitted, not an arithmetic consequence of
#: what gets refused.
BODY_MAX_CHARS = 20_000

#: Every value `nora_culture_fold_total{outcome}` may carry: the label
#: vocabulary the adaptor checks at runtime (`CultureAdaptor._inc`) and the
#: metric's help text reads (`nora.obs.metric_specs`). Here, in the domain,
#: so the metric spec can import it.
#:
#: Since the write verb (`character_revise`, design/self-growth/10 §3) the
#: outcomes are about what a SESSION did with its entity, not about an
#: envelope: `written` (a revision appended), `unchanged` (a revision
#: byte-identical to the head, not appended), `kept` (the session closed its
#: entity «no change»), `refused` (one call of the verb was refused, with a
#: reason the model read), `unsettled` (the session ended with its entity
#: neither revised nor kept, so the run holds its watermark) and `abandoned`
#: (the same batch was unsettled `MAX_CONSECUTIVE_REJECTIONS` times in a row
#: for one key, and the fold advanced past it, DEC-280).
CULTURE_OUTCOMES = frozenset({
    "written", "unchanged", "kept", "refused", "unsettled", "abandoned",
})

#: The entity kinds a Character session writes besides a room
#: (`nora.language.character`), the `kind` label of
#: `nora_character_session_total`.
CHARACTER_KINDS = frozenset({"person"})

#: That metric's `outcome` vocabulary: the room's own, which every session
#: shares (`nora.language.character_session.EntitySessions`).
CHARACTER_OUTCOMES = CULTURE_OUTCOMES

#: How many consecutive unsettled sessions of the same batch a key takes
#: before the fold gives up on it and advances (DEC-280). Each one holds the
#: watermark, so without a bound a batch the model keeps failing to settle is
#: re-read for ever.
MAX_CONSECUTIVE_REJECTIONS = 3

#: **MEASURED, 2026-09-17/18** — the longest account this deployment's model has
#: been observed to emit, and the basis every token budget for this tier is
#: sized on.
#:
#: This constant exists because `ACCOUNT_OUTPUT_TOKENS` used to be
#: `BODY_MAX_CHARS * OUTPUT_TOKENS_PER_CHAR`, which tied the provider's
#: completion ceiling to a QUALITY refusal. The two answer different questions
#: — "how long a body do we decline to record" and "how long a completion must
#: the transport be able to carry" — and binding them meant a cap could not be
#: raised without a 23,000-token reservation nothing would ever use.
#:
#: 1,876 is the maximum of the n=10 sample in `BODY_MAX_CHARS`' table above.
#: **n=10 is small and this number is expected to move**; it is the honest
#: basis available, and it is a great deal better than the derivation it
#: replaced. Widen the sample before treating the budgets below as tight — and
#: note the direction of the risk is asymmetric: a reservation that is too
#: small TRUNCATES, which holds the watermark and re-reads the same batch
#: forever, so this is the one to keep loose.
ACCOUNT_CHARS_OBSERVED_MAX = 1_876

#: What emitting a REALISTIC account costs in OUTPUT TOKENS.
#:
#: `ACCOUNT_CHARS_OBSERVED_MAX` at the MEASURED `OUTPUT_TOKENS_PER_CHAR` —
#: rounded UP, because rounding a fail-closed conversion down is the one
#: direction that lets the two assertions below pass while the account
#: truncates — plus 64 for the JSON envelope the objective requires around it
#: (`{"account": [{"body": "…"}]}`, its escaping, and the newline the provider
#: usually adds), measured at 15 tokens for a one-account envelope, so 64 is
#: four times the observed overhead.
#:
#: **Two ceilings must clear this, and they are different things** (see
#: `config.example.toml`'s `[dreamer] max_tokens`, which spells the confusion
#: out):
#:
#: - `DreamBudgetConfig.output_tokens` — the Strands LOOP budget, spent across
#:   every turn of the fold. The rule is that the loop must be able to emit a
#:   full account TWICE: once for the tool-call arguments and any preamble the
#:   pass spends before it writes, once for the account itself. The shipped
#:   `[dreamer.budgets.culture] output_tokens = 6000` clears 2 x 2,222.
#: - `[dreamer] max_tokens` (0 -> `[llm] max_tokens`) — the PROVIDER's ceiling
#:   on ONE completion, which binds as `MaxTokensReachedException` ->
#:   `PartialReplyError` rather than as a `stop_reason`. The deployment ships
#:   6,000 and that is the tighter one to raise if a fold is ever seen
#:   truncating — the example file says so in those words, and the escape case
#:   in `OUTPUT_TOKENS_PER_CHAR` above is the reading that would raise it.
#:
#: Both inequalities are asserted rather than argued
#: (`tests/unit/test_culture_adaptor.py::test_an_account_at_the_cap_fits_...`).
ACCOUNT_OUTPUT_TOKENS = ceil(
    ACCOUNT_CHARS_OBSERVED_MAX * OUTPUT_TOKENS_PER_CHAR) + 64


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
    # WHERE IT MAY BE SHOWN (design/cognition-loop/10-tiers.md §3, migration
    # `0073_lineage`): the `(kind, subject, origin room)` set its citations
    # reach, and the people it is never shown to. Written by the store from
    # the row's own citations, never by a model; read at render by
    # `nora.agent.lineage`. `compare=False`: equality stays about content, and
    # a row built by hand (no lineage, every section unknown) is on the
    # fail-closed side.
    lineage: Lineage = field(default=Lineage(), compare=False)
    exclude_persons: tuple[str, ...] = field(default=(), compare=False)
    #: What this revision rests on, as `character_revise` checked it: Record
    #: message ids (`"4812"`) and Rollup response episodes
    #: (`"response:4790"`, by her send's id). Empty on every row written
    #: before the verb (migration `0098_culture_citations`). Provenance, so
    #: `compare=False` like `lineage`.
    citations: tuple[str, ...] = field(default=(), compare=False)


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
    #: `None` is the ROOM's account. A `person_id` makes it that one person's
    #: account in this room (`character_key`), written by the same fold from
    #: Honcho's view of them and the room's culture. It comes from the roster
    #: the prompt showed, never from an id the model typed.
    person_id: str | None = None
    #: The citations `check_revision` accepted, in the model's order.
    citations: tuple[str, ...] = ()


#: Why one `character_revise` call was refused, as the `reason` the model
#: reads (each with a `detail` saying what to do instead):
#:
#: - `unknown_entity`: the entity is not one this session writes;
#: - `empty`: no body;
#: - `too_long`: the body is past `BODY_MAX_CHARS`;
#: - `uncited`: no citation at all; an account rests on what it read;
#: - `unresolved_citation`: a citation that is not one of the rows or
#:   episodes this session was handed;
#: - `echo`: a Character · Nora revision whose citations are only her own
#:   sends (design/cognition-loop/10-tiers.md §2 rule 4, the echo rule);
#: - `identifier`, `coordinate`: R-18/R-28, the Dreamer's egress screen
#:   (`nora.agent.dream_screen`), applied to the body before it is staged.
REVISE_REFUSALS = frozenset({
    "unknown_entity", "empty", "too_long", "uncited", "unresolved_citation",
    "echo", "identifier", "coordinate",
})

#: `response:<her send's message id>`: how a session cites a Rollup response
#: episode. The episode's own Rollup key is `(response, <unit>|<id>,
#: <interval>)` (`nora.infra.sql.rollups._citation_scope`); inside one unit's
#: session the send id names it exactly, and it is what the session was shown.
_EPISODE = re.compile(r"response:(\d+)")


@dataclass(frozen=True, slots=True)
class Refusal:
    """One refused write: the `reason` (a `REVISE_REFUSALS` member) and a
    `detail` the model can act on."""

    reason: str
    detail: str


@dataclass(frozen=True, slots=True)
class Citable:
    """What one session may cite, built by CODE from what it handed the model.

    `messages` are the Record ids it was shown (or loaded through a read it
    holds); `nora` is the subset that are her own sends; `episodes` are the
    Rollup response episodes it was shown, by her send's id. `echo_rule` is
    on for Character · Nora alone: a revision of HER account must rest on
    something other than her own words (10-tiers §2 rule 4)."""

    messages: frozenset[int] = frozenset()
    nora: frozenset[int] = frozenset()
    episodes: frozenset[int] = frozenset()
    echo_rule: bool = False


def check_revision(
    *, body: str, citations: tuple[str, ...], citable: Citable,
) -> Refusal | None:
    """`None` when this revision may be staged, else why not.

    Every citation must resolve to something this session was HANDED: a
    citation the session cannot have read is a claim nobody can re-derive,
    which is the whole point of a citation (AGENTS.md, re-derivable). At
    least one is required: an account that rests on nothing it read is a
    guess. The body is prose (DEC-184): its length is checked, its shape is
    not.
    """
    if not body.strip():
        return Refusal("empty", "the body is empty; write the account, or close "
                       "this entity as unchanged")
    if len(body) > BODY_MAX_CHARS:
        return Refusal("too_long", f"the body is {len(body)} characters, past the "
                       f"{BODY_MAX_CHARS} cap; write it shorter")
    if not citations:
        return Refusal("uncited", "cite at least one message id (`n` of a message "
                       "you were shown) that the account rests on")
    messages: set[int] = set()
    episodes: set[int] = set()
    for raw in citations:
        text = str(raw).strip()
        episode = _EPISODE.fullmatch(text)
        if episode is not None and int(episode.group(1)) in citable.episodes:
            episodes.add(int(episode.group(1)))
        elif text.isdigit() and int(text) in citable.messages:
            messages.add(int(text))
        else:
            return Refusal("unresolved_citation", f"{text!r} is not a message or "
                           "episode you were shown; cite only the `n` of a shown "
                           "message or a shown `response:<n>` episode")
    if citable.echo_rule and not episodes and messages <= citable.nora:
        return Refusal("echo", "every citation is one of your own sends; your "
                       "account must rest on how people responded: cite a "
                       "`response:<n>` episode or a message someone else wrote")
    return None


def character_key(unit: str, person_id: str) -> str:
    """The `culture` key one person's account is stored under, in `unit`
    (design/self-growth/10 §3, C2b): ONE account per (unit, person), written by
    that person's own session from their messages across the unit's rooms.

    The Character tier REUSES the culture store and its revision chain rather
    than growing a parallel one: `culture.key` is free text, so a person's
    account is one more key. It cannot collide with a room's key (a room's
    `primary_id` carries no `#character:`), and nothing scans the table for
    every key. Until C2b the first half was the ROOM's `primary_id` (one
    account per person per room, DEC-278); those keys stay in the store
    (nothing here retires, DEC-183) and are read by nothing but the one-time
    move to these (`nora.app.character_heads.seed_unit_person_heads`).
    """
    return f"{unit}#character:{person_id}"


@dataclass(frozen=True, slots=True)
class RoomMessage:
    """One person's message as their session reads it: the Record row and the
    room it was said in (the room's `primary_id`, its culture key)."""

    room: str
    row: TranscriptRow

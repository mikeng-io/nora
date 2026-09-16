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
    "BODY_MAX_CHARS",
    "Culture",
    "CultureProposal",
    "CultureRevision",
]

#: The OPERATING cap on an account, in characters. The database ceiling is four
#: times this (`CULTURE_BODY_CEILING_CHARS`) and the gap is deliberate, the same
#: split `notes` makes: this number says "no writer should produce that", the
#: ceiling says "this row is corrupt".
#:
#: 8000 because an account is a SHORT account — how they talk, how decisions get
#: made, what they call themselves, her own standing, and why. A body that runs
#: past this has almost certainly drifted back into chronicle, restating what
#: happened instead of saying what the room is.
BODY_MAX_CHARS = 8_000


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

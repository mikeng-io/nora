"""An account of how a room works and why, kept over time.

The tier exists because of one gap, worth stating before any type:
**a chronicle is not a culture.**

The memory system's `representation` returns exactly one shape —
`[when] what happened` — every row pinned to an instant and independent of
every other. A list of dynasties and their dates is not a culture: the format
has no slot for continuity or causation, so 「漢人」 — a term that outlived its
dynasty, meant something different each century, and is *why* people still say
it — cannot be expressed in it. Frequency is not continuity.

Culture is an **account**: what this is, and why it became so. That is what a
model produces when asked for prose over a room's own messages, and this module
is the shape it is kept in.

**What it is for.** A Relevance Gate window judged against nothing is judged
in a vacuum — "nobody picked this up" means nothing until you know
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
from typing import TYPE_CHECKING, Literal

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
    "NORA_MIN_EVIDENCE_EPISODES",
    "OUTPUT_TOKENS_PER_CHAR",
    "REVISE_REFUSALS",
    "Citable",
    "Culture",
    "CultureKeyForm",
    "CultureProposal",
    "CultureRevision",
    "Episode",
    "Refusal",
    "RoomMessage",
    "character_key",
    "check_revision",
    "nora_character_key",
    "social_citation",
    "split_culture_key",
    "unit_culture_key",
]

#: What one character of a culture account costs in OUTPUT TOKENS, and the one
#: place a length in characters and a budget in tokens are related. The length
#: it converts is `ACCOUNT_CHARS_OBSERVED_MAX`, what the model is seen to emit,
#: NOT `BODY_MAX_CHARS`, what we decline to record.
#:
#: Counted with `tiktoken`'s `o200k_base` over a realistic account for this
#: deployment's room (dense Cantonese, Latin technical terms unlocalised, an
#: emoji nickname, the room's own coined names) inside the JSON envelope the
#: objective requires:
#:
#: | what was encoded | tok/char |
#: |---|---|
#: | the objective's own worked example (English prose, 1% CJK) | 0.227 |
#: | a realistic dense-Cantonese account (89% CJK) | 1.094 |
#: | continuous Cantonese prose, no Latin | 1.131 |
#: | Cantonese-specific particles alone (`𡃁嘥揼冚唞氹`) | 2.080 |
#:
#: `1.15` is the continuous-prose worst case with margin. The particle string
#: falls to BYTE-level tokens, 2-3 each, but is not prose; sizing every budget
#: for it would triple the Dreamer's ceiling against a shape nothing produces.
#:
#: **What this number does NOT cover.** A model that emits `\uXXXX` ESCAPES
#: instead of raw UTF-8 pays 3.117 tok/char, 2.85x this figure
#: (`json.dumps(..., ensure_ascii=True)`). That is the MODEL's choice of
#: emission, not the transport's: the HTTP frame's own escaping costs no output
#: tokens. In that case a realistic account costs ~5,850 tokens instead of
#: ~2,222, the completion truncates, and the run lands in the HELD branch:
#: loud, recoverable, and fixed by `[dreamer] max_tokens`, the knob
#: `config.example.toml` names for exactly this. Lowering `BODY_MAX_CHARS` does
#: not fix it.
OUTPUT_TOKENS_PER_CHAR = 1.15

#: The OPERATING cap on an account, in characters. The database ceiling
#: (`CULTURE_BODY_CEILING_CHARS`, 32,000) is above it and the gap is
#: deliberate, the same split `notes` makes: this number says "no writer should
#: produce that", the ceiling says "this row is corrupt".
#:
#: This is a CORRUPTION ceiling one notch under the database's, not a quality
#: refusal. A refusal on length is a coin the fold tosses every fire: a
#: rejected account makes `accept` raise, the Dreamer holds the watermark, and
#: the fold re-draws, one model call per tick, until a draw lands under the
#: cap. So the cap sits well above what the model writes. The tier therefore
#: has no automatic guard against an account drifting back into chronicle; that
#: guard is the objective's job (a line in "What to write" naming the worked
#: example AS the target length, a prompt change under
#: `change_policy: operator-only` that needs the arm that measures it).
#:
#: The cap does not size the token budgets: a cap is a refusal threshold, not a
#: reservation. See `ACCOUNT_CHARS_OBSERVED_MAX`, which is what the budgets are
#: sized on.
BODY_MAX_CHARS = 20_000

#: Every value `nora_culture_fold_total{outcome}` may carry: the label
#: vocabulary the adaptor checks at runtime (`CultureAdaptor._inc`) and the
#: metric's help text reads (`nora.obs.metric_specs`). Here, in the domain,
#: so the metric spec can import it.
#:
#: Since the write verb (`character_revise`) the outcomes are about what a
#: SESSION did with its entity, not about an envelope: `written` (a revision appended), `unchanged`
#: (a revision byte-identical to the head, not appended), `kept` (the session closed its
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
CHARACTER_KINDS = frozenset({"person", "unit", "nora"})

#: That metric's `outcome` vocabulary: the room's own, which every session
#: shares (`nora.language.character_session.EntitySessions`).
CHARACTER_OUTCOMES = CULTURE_OUTCOMES

#: How many consecutive unsettled sessions of the same batch a key takes
#: before the fold gives up on it and advances (DEC-280). Each one holds the
#: watermark, so without a bound a batch the model keeps failing to settle is
#: re-read for ever.
MAX_CONSECUTIVE_REJECTIONS = 3

#: The longest account this deployment's model has been observed to emit, and
#: the basis every token budget for this tier is sized on.
#:
#: It is separate from `BODY_MAX_CHARS` because the two answer different
#: questions: "how long a body do we decline to record" and "how long a
#: completion must the transport be able to carry". Binding them would mean a
#: cap could not be raised without a 23,000-token reservation nothing would
#: ever use.
#:
#: The value is the maximum of a small sample of live accounts and is expected
#: to move. The direction of the risk is asymmetric: a reservation that is too
#: small TRUNCATES, which holds the watermark and re-reads the same batch
#: forever, so keep the budgets loose.
ACCOUNT_CHARS_OBSERVED_MAX = 1_876

#: What emitting a REALISTIC account costs in OUTPUT TOKENS.
#:
#: `ACCOUNT_CHARS_OBSERVED_MAX` at `OUTPUT_TOKENS_PER_CHAR` —
#: rounded UP, because rounding a fail-closed conversion down is the one
#: direction that lets the two assertions below pass while the account
#: truncates — plus 64 for the JSON envelope the objective requires around it
#: (`{"account": [{"body": "…"}]}`, its escaping, and the newline the provider
#: usually adds); a one-account envelope costs about 15 tokens, so 64 is
#: generous.
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
    # WHERE IT MAY BE SHOWN (migration `0073_lineage`): the `(kind, subject, origin room)` set its
    # citations reach, and the people it is never shown to. Written by the store from
    # the row's own citations, never by a model; read at render by
    # `nora.agent.lineage`. `compare=False`: equality stays about content, and
    # a row built by hand (no lineage, every section unknown) is on the
    # fail-closed side.
    lineage: Lineage = field(default=Lineage(), compare=False)
    exclude_persons: tuple[str, ...] = field(default=(), compare=False)
    #: What this revision rests on, as `character_revise` checked it: Record
    #: message ids (`"4812"`), Rollup response episodes
    #: (`"response:4790"`, by her send's id) and a person's social items
    #: (`"social:instagram:<item id>"`, `social_citation`). Empty on a row written without
    #: the verb (migration `0098_culture_citations`). Provenance, so
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
    #: The citations `check_revision` accepted, in the model's order.
    citations: tuple[str, ...] = ()


#: Why one `character_revise` call was refused, as the `reason` the model
#: reads (each with a `detail` saying what to do instead):
#:
#: - `unknown_entity`: the entity is not one this session writes;
#: - `empty`: no body;
#: - `too_long`: the body is past `BODY_MAX_CHARS`;
#: - `uncited`: no citation at all; an account rests on what it read;
#: - `unresolved_citation`: a citation that is not one of the rows,
#:   episodes or social items this session was handed;
#: - `echo`: a Character · Nora revision whose citations are only her own
#:   sends (the echo rule);
#: - `identifier`, `coordinate`: R-18/R-28, the Dreamer's egress screen
#:   (`nora.agent.dream_screen`), applied to the body before it is staged;
#: - `ids_in_body`: the account's text carries a message number or a social
#:   item's id it was handed: numbers belong in `citations`, and an account
#:   that reads "(6888, 7020)" is a record of messages, not a description.
REVISE_REFUSALS = frozenset({
    "unknown_entity", "empty", "too_long", "uncited", "unresolved_citation",
    "echo", "thin_evidence", "identifier", "coordinate", "ids_in_body",
})

#: `response:<her send's message id>`: how a session cites a Rollup response
#: episode. The episode's own Rollup key is `(response, <unit>|<id>,
#: <interval>)` (`nora.infra.sql.rollups._citation_scope`); inside one unit's
#: session the send id names it exactly, and it is what the session was shown.
_EPISODE = re.compile(r"response:(\d+)")

#: `social:<network>:<item id>`: how a person session cites one of that
#: person's social items (design/cognition-loop/22 §6). Its own id space: the
#: item's natural key `(network, item_id)`, the one `social_item` is unique
#: on, never a Record id, so it cannot be confused with a message.
_SOCIAL = re.compile(r"social:([a-z]+):(\S+)")


def social_citation(network: str, item_id: str) -> str:
    """The citation a session writes for one social item: `social:` and the
    item's natural key, `network:item_id` (a network name holds no `:`)."""
    return f"social:{network}:{item_id}"

#: The outcomes that are evidence on their own, whatever the counts say
#: (`Episode.is_evidence`).
EPISODE_EVIDENCE_OUTCOMES = frozenset({"responded", "silent"})


#: How many evidence episodes (`Episode.is_evidence`) a revision of Nora's own
#: account must cite (DEC-290): one reaction is an anecdote. A floor, clamped by
#: the adaptor to how many evidence episodes the session was shown, so a session
#: woken by the stale rule on one episode is not given a bar it cannot meet.
NORA_MIN_EVIDENCE_EPISODES = 3


@dataclass(frozen=True, slots=True)
class Refusal:
    """One refused write: the `reason` (a `REVISE_REFUSALS` member) and a
    `detail` the model can act on."""

    reason: str
    detail: str


@dataclass(frozen=True, slots=True)
class Citable:
    """What one session may cite, built by CODE from what it handed the model.

    `messages` are the Record ids it was shown; `nora` is the subset that are
    her own sends; `episodes` are the Rollup response episodes it was shown
    that are evidence (`Episode.is_evidence`), by her send's id; `social` are
    the person's social items it was shown, as `network:item_id`. `echo_rule` is
    on for Character · Nora alone: a revision of HER account must rest on
    something other than her own words. `min_episodes` is
    how many distinct evidence episodes such a revision must cite (0: none)."""

    messages: frozenset[int] = frozenset()
    nora: frozenset[int] = frozenset()
    episodes: frozenset[int] = frozenset()
    social: frozenset[str] = frozenset()
    echo_rule: bool = False
    min_episodes: int = 0
    #: Messages that told her directly how to act (Lessons L4). A write citing one is exempt from
    #: `min_episodes`: a correction is enough on its own, which is the point of the fast path.
    corrections: frozenset[int] = frozenset()


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
    shown = citable.messages
    numbers = re.findall(r"(?<![\d.])\d{3,}(?![\d.])", body)
    # An item id may be composite (a story frame's `<digits>_<digits>`): each
    # numeric part counts on its own.
    shown_items = {
        part for key in citable.social
        for part in re.findall(r"\d{3,}", key.partition(":")[2])
    }
    if any(int(n) in shown or n in shown_items for n in numbers):
        return Refusal("ids_in_body", "the account's text carries message or item numbers; put "
                       "them in `citations` only, and write the account as a description")
    if len(body) > BODY_MAX_CHARS:
        return Refusal("too_long", f"the body is {len(body)} characters, past the "
                       f"{BODY_MAX_CHARS} cap; write it shorter")
    if not citations:
        return Refusal("uncited", "cite at least one message id (`n` of a message "
                       "you were shown), or the `cite` of a social item you were "
                       "shown, that the account rests on")
    messages: set[int] = set()
    episodes: set[int] = set()
    items: set[str] = set()
    for raw in citations:
        text = str(raw).strip()
        episode = _EPISODE.fullmatch(text)
        social = _SOCIAL.fullmatch(text)
        if episode is not None and int(episode.group(1)) in citable.episodes:
            episodes.add(int(episode.group(1)))
        elif text.isdigit() and int(text) in citable.messages:
            messages.add(int(text))
        elif social is not None and f"{social.group(1)}:{social.group(2)}" in citable.social:
            items.add(f"{social.group(1)}:{social.group(2)}")
        else:
            return Refusal("unresolved_citation", f"{text!r} is not something you "
                           "can cite; cite only the `n` of a message you were "
                           "shown, the `cite` of a social item you were shown, "
                           "or the `response:<n>` of a shown episode "
                           "someone responded to (outcome `responded` or `silent`)")
    if citable.echo_rule and not episodes and not items and messages <= citable.nora:
        return Refusal("echo", "every citation is one of your own sends; your "
                       "account must rest on how people responded: cite the "
                       "`response:<n>` of an episode whose outcome is `responded` "
                       "or `silent`, or a message someone else wrote")
    if len(episodes) < citable.min_episodes and not (messages & citable.corrections):
        return Refusal("thin_evidence", f"cite at least {citable.min_episodes} "
                       f"different `response:<n>` episodes someone responded to "
                       f"(outcome `responded` or `silent`); you cited "
                       f"{len(episodes)}")
    return None


_CHARACTER_MARK = "#character:"


def character_key(unit: str, person_id: str) -> str:
    """The `culture` key one person's account is stored under, in `unit`: ONE account per (unit,
    person), written by that person's own session from their messages across the unit's rooms.

    The Character tier REUSES the culture store and its revision chain rather
    than growing a parallel one: `culture.key` is free text, so a person's
    account is one more key. It cannot collide with a room's key (a room's
    `primary_id` carries no `#character:`), and nothing scans the table for
    every key. Until C2b the first half was the ROOM's `primary_id` (one
    account per person per room, DEC-278); those keys stay in the store
    (nothing here retires, DEC-183) and nothing reads them. They are NOT
    copied to the unit key: an account written from one room's messages (and
    possibly a presence read there) would then render in every room of the
    unit that lists the person, a wider disclosure than it was written for.
    Each person's unit account is written fresh by their first session, which
    wakes on the first tick (a key never run has an infinitely old watermark
    and a person with an old account has posted).
    """
    return f"{unit}{_CHARACTER_MARK}{person_id}"


def unit_culture_key(unit: str) -> str:
    """The `culture` key of a UNIT's own account, what the unit is across its
    rooms: one per unit, written by the
    unit's Character session from its rooms' cultures. No room's
    `primary_id` and no person key ends in `#unit`."""
    return f"{unit}#unit"


def nora_character_key(unit: str) -> str:
    """The `culture` key of Nora's own account in `unit`: what she is like HERE, as its people have
    met her. Per unit, never per room. No room, person or unit key ends in `#nora`."""
    return f"{unit}#nora"


#: What a `culture` key names, read back by `split_culture_key`.
CultureKeyForm = Literal["room", "unit", "nora", "character"]


def split_culture_key(key: str) -> tuple[CultureKeyForm, str, str | None]:
    """The inverse of the three builders above: `(form, owner, person)`.

    `owner` is the unit for `unit`, `nora` and `character`, and the room's bare
    `primary_id` for `room`; `person` is set for `character` only. The split
    cannot tell a unit-keyed person account from a pre-C2b ROOM-keyed one
    (`<room primary_id>#character:<p>`, `character_key`'s docstring): both
    read `character` here, and a caller that must not widen the old one's
    disclosure checks `owner` against the Record's rooms itself."""
    if _CHARACTER_MARK in key:
        owner, _, person = key.partition(_CHARACTER_MARK)
        return "character", owner, person
    if key.endswith("#unit"):
        return "unit", key.removesuffix("#unit"), None
    if key.endswith("#nora"):
        return "nora", key.removesuffix("#nora"), None
    return "room", key, None


@dataclass(frozen=True, slots=True)
class Episode:
    """One settled Rollup `response` episode as Character · Nora reads it
    (`nora.infra.sql.rollups`): her send, and what followed inside the
    window. Cited as `response:<send_id>`, when it `is_evidence`.

    `replies` are the Record rows that quoted her send inside the window;
    `reactions` and `edits` are the episode's own counts; `outcome` is
    `responded`, `unanswered` or `silent`. A store hands over neither `open` nor
    `unknown` (`nora.infra.sql.character`), and the session drops both if one
    arrives."""

    send_id: int
    room: str
    text: str
    outcome: str
    reactions: int
    edits: int
    replies: tuple[TranscriptRow, ...] = ()
    #: Unquoted `follow` responses. Not shown to the session yet, so an
    #: episode with only these is not evidence (`is_evidence`).
    follows: int = 0
    turn_id: int | None = None

    @property
    def turn_handle(self) -> str | None:
        return f"t{self.turn_id}" if self.turn_id is not None else None

    @property
    def is_evidence(self) -> bool:
        """Whether this episode is something people DID: it drew a reply, a
        reaction or an edit, or an answer was expected and none came
        (`silent`). `unanswered` (none expected) and `unknown` (an outage, not
        silence, R-27) are not, and `open` is not yet. Only an evidence episode
        may be cited, so her own send relabelled as an episode cannot meet the
        echo rule.

        An episode whose ONLY responses are `follows` is not evidence either:
        the session is shown her send and nothing of the follow messages, so
        citing it would rest on a response it cannot see. That lifts when the
        follow messages are rendered."""
        if self.follows and not (self.replies or self.reactions or self.edits):
            return False
        return (
            self.outcome in EPISODE_EVIDENCE_OUTCOMES
            or bool(self.replies or self.reactions or self.edits)
        )


@dataclass(frozen=True, slots=True)
class RoomMessage:
    """One person's message as their session reads it: the Record row and the
    room it was said in (the room's `primary_id`, its culture key)."""

    room: str
    row: TranscriptRow

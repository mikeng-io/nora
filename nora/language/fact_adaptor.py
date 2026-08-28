"""The fact tier's two `DreamAdaptor`s — the writer that makes the tier
non-dormant (docs/superpowers/specs/2026-08-20-fact-tier-design.md §4, §5).

Two adaptors, not one, and the split IS the cost decision (§5). Phase A
(`FactCandidateAdaptor`) is one cheap, TOOL-FREE call per tick that reads a
room's message batch and names the claims somebody asserted. Phase B
(`FactAdaptor`) is one research loop per surviving candidate, capped by
`[facts] max_adjudications_per_tick`. Folding them into one adaptor would mean
a research loop per message, which is unbounded cost on a background clock
nobody is watching — and it fails quietly, because nobody is waiting on the
Dreamer.

**Why these live in `nora/language/` and name their tools by STRING.** The
layer contract (`pyproject.toml`'s "layered architecture") forbids
`nora.agent` from holding tier logic and forbids `nora.language` from
importing `nora.agent`, `nora.doctrine` or `nora.config`. So an adaptor
declares tool NAMES and the runtime resolves them against its registry —
`DreamAdaptor.tools`' own inversion, the same one `ToolRegistry.tool_names()`
already performs for the doctrine loader. It is also why
`max_adjudications_per_tick` arrives through `__init__` as a plain int rather
than being read from `nora.config` here.

**The parse discipline is `nora.language.observer`'s, deliberately copied
rather than reinvented**: a malformed item is DROPPED, never raised, because
the runtime maps a raised parse to `unavailable` — «nobody read this batch»,
watermark held — and two bad items must not cost a room its whole tick. Every
model-authored string field carries a length cap at parse time, ahead of the
store: an unbounded model-authored string reaching a durable column is the
audit's F14 shape, and here it would additionally become half of
`fact_candidate`'s natural key, where nothing can ever match it back.

**The two adaptors' `accept` differ in one place, and it is not stylistic.**
Phase A's `CandidateClaim` has no invariants beyond its field types, so its
parse is a plain typed read. Phase B's `FactVerdict.__post_init__` already
enforces R-27's batch invariants (`not_covered` ⇒ no `supersedes`;
`contradicted` ⇒ `supersedes` + `addresses_claim` + a source; `supported` ⇒
no `supersedes`) by RAISING — so `FactAdaptor.accept` catches that
`ValueError` and drops the item. The domain type is the enforcement; this
module's job is to not let its enforcement escape as a tick-killing
exception.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Protocol

from ..domain.facts import (
    DEFAULT_MAX_PREDICATE_CHARS,
    DEFAULT_MAX_TERM_CHARS,
    CandidateClaim,
    Fact,
    FactCandidate,
    FactVerdict,
)
from ..domain.models import TranscriptRow
from ..domain.ports import DreamSink, FactCandidateReader, FactReader, Proposal
from ..obs.metrics import Metrics

log = logging.getLogger(__name__)

# Reject, at parse time, a model-authored field implausibly longer than any
# genuine one could be — `nora.language.observer._MAX_FIELD_CHARS`' own
# argument, applied to this tier's columns. Generous enough never to reject a
# real claim; `nora.language.fact_render`'s caps are the far tighter DISPLAY
# bound and are what a legitimately long value is actually cut down to for a
# reader. The triple fields are the strictest case: they are half of
# `fact_candidate_triple_key`, so an over-long one is not merely a big column,
# it is a key nobody can ever match a later sighting against.
# The three triple fields take the OPERATING caps from `[facts]`, injected
# through each adaptor's constructor — `nora.domain.facts`'
# `DEFAULT_MAX_TERM_CHARS` (200) and `DEFAULT_MAX_PREDICATE_CHARS` (100) are
# the defaults, and the ceilings above them are DDL. They were module
# constants, with the object at 500 while the subject sat at 200 for no reason
# either could state; 25 §9.1 gathers them and gives both terms one number.
#
# 30 §5 is why they are injected rather than read from a module: "every writer
# validates against the CURRENT operating value". A cap that only refuses a
# config above its ceiling leaves oversized rows satisfying the CHECK while
# defeating the prompt budget the cap exists to protect.
# A URL past this is not a citation, and an excerpt past it is not a quoted
# sentence — both are the model pasting a page into a durable column.
_MAX_SOURCE_URL_CHARS = 2_000
_MAX_SOURCE_EXCERPT_CHARS = 2_000
# `supersedes` names a `fact.id`, which is a uuid. Capped rather than
# UUID-validated here because the membership check in `FactAdaptor.accept` is
# strictly stronger: the id must be one this run actually SHOWED the model.
_MAX_FACT_ID_CHARS = 64

# How many standing facts one adjudication tick puts in front of the model.
# NOT `[facts] max_rendered_facts` (8), which is the per-TURN render cap on
# the `<facts>` block a tag reads: this is a background research prompt, the
# facts here are what `supersedes` is allowed to name, and a cap that tight
# would make a genuine contradiction unnameable — the model may only supersede
# a fact it was shown. Still bounded, because §4.2's whole point is that the
# live set is a code-selected INPUT and selection stays where it can be capped
# and counted.
DEFAULT_MAX_STANDING_FACTS = 50


class _FactStore(DreamSink, FactReader, Protocol):
    """`SqlFactStore`, seen from here: the fact tier's sink AND its reader.

    One collaborator rather than two parameters holding the same object,
    because they ARE the same object — `SqlFactStore` satisfies both and
    splitting it at this constructor would invite a wiring where the adaptor
    reads one store and writes another.
    """


class _CandidateStore(DreamSink, FactCandidateReader, Protocol):
    """`SqlFactCandidateStore`, seen from here — sink and reader, for the
    reason `_FactStore` gives. `candidate_by_id` is deliberately NOT in this
    shape: it is that store's own surface, not the port's, and nothing here
    reads a candidate by id."""


class _EntityPromoter(Protocol):
    """`SqlEntityStore.promote`, seen from here. A Protocol rather than the
    class because `nora.language` may not import `nora.infra` — the same seam
    `_FactStore` and `_CandidateStore` above use, and the reason all three are
    structural."""

    async def promote(self, *, workspace: str, now_ms: int) -> object: ...


class _EntityResolver(Protocol):
    """The three verbs pass 6 needs off `SqlEntityStore`, same seam and same
    reason. Narrower than the class on purpose: this adaptor never promotes and
    never creates an entity, so neither verb is in the shape it is handed."""

    async def unresolved_names(
        self, *, workspace: str, limit: int,
    ) -> tuple[str, ...]: ...

    async def similar_entities(
        self, *, workspace: str, name: str, limit: int,
    ) -> tuple[tuple[str, str], ...]: ...

    async def attach_alias(
        self, *, workspace: str, entity_id: str, alias: str,
    ) -> bool: ...


def _text(value: object, *, limit: int) -> str | None:
    """One model-authored string field, or `None` if it is not one.

    STRICT `isinstance(str)` rather than `nora.language.observer._parse_item`'s
    `str(item[name])` coercion, and this is the one place this module
    deliberately diverges from that discipline. These three fields become a
    durable natural key (`fact_candidate_triple_key`) and the words a later
    verdict is matched back by; coercion turns `None` into the string
    `"None"` and `7` into `"7"`, which are keys that look real, never match
    anything, and cannot be told apart from a claim somebody actually made.
    The objectives both say "strings", so a non-string is the model not
    answering the question.
    """
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped or len(stripped) > limit:
        return None
    return stripped


def _optional_text(value: object, *, limit: int) -> tuple[str | None, bool]:
    """An optional string field: `(value, ok)`.

    Absent and explicitly null both yield `(None, True)` — the objective marks
    `source_url`/`source_excerpt`/`supersedes` absent for the statuses that
    forbid them, and a missing key is the correct way to say so. A present
    value of the wrong type or the wrong length is `(None, False)`: the model
    tried to say something and this module could not read it, which is not the
    same as it saying nothing.
    """
    if value is None:
        return None, True
    text = _text(value, limit=limit)
    return (None, False) if text is None else (text, True)


# ---------------------------------------------------------------------------
# Phase A — extraction
# ---------------------------------------------------------------------------


class FactCandidateAdaptor:
    """Phase A: read one room's message batch, name the claims, write them to
    the candidate table. Scope-keyed and watermarked — its input IS the
    messages.

    **`tools` is EMPTY on purpose, and that is a COST decision (§5), not an
    oversight.** One cheap tool-free call per tick decides what is worth a
    research loop; a research loop per message is unbounded cost on a
    background clock nobody is watching. The objective says the same thing to
    the model in its own words ("You are NOT deciding whether any of them is
    true"): checking a claim here would spend Phase B's budget on every
    message in the room, including the ones the deterministic filter between
    the phases exists to throw away.

    **`workspace_for` is a required collaborator, not a convenience.** This
    adaptor is keyed on a SCOPE (a watermark and a message batch are per room)
    and writes into a table keyed on a WORKSPACE (`Conversation.workspace`,
    DEC-105) — see `commit` for what happens without the translation, and why
    an unresolvable scope raises rather than guessing. The composition root is
    where the two keys are both known: `language_scopes` reads
    `conv.primary_id` and `workspace_scopes` reads `conv.workspace` off the
    same allowlist, so the resolver is the map between those two closures.

    A plain callable rather than a declared Protocol, following
    `dream_loop`'s own `scopes`/`workspaces` parameters and
    `Dependencies.workspace_scopes`: this is a policy-derived lookup the
    composition root already expresses as a closure, not a collaborator with
    behaviour of its own. It is NOT a `*_fn` test seam — the production
    wiring is the closure, and no test-only path exists beside it.

    Collaborators through `__init__` only, per the test-seam policy. No
    metrics collaborator, because this class emits none: a malformed item is
    dropped SILENTLY here (`DreamAdaptor.accept`'s own contract), and the
    fact-tier counter names declared in `nora.obs.metric_specs` all belong to
    adjudication rather than extraction. Counting extraction drops would need
    a new metric name, which is a change to that module and not this one's to
    make.
    """

    name = "fact-candidates"
    key_kind = "scope"
    uses_watermark = True
    objective = "fact-candidates"
    envelope_key = "candidates"
    tools: frozenset[str] = frozenset()

    def __init__(
        self,
        *,
        candidates: DreamSink,
        workspace_for: Callable[[str], str | None],
        max_term_chars: int = DEFAULT_MAX_TERM_CHARS,
        max_predicate_chars: int = DEFAULT_MAX_PREDICATE_CHARS,
    ) -> None:
        self._candidates = candidates
        self._workspace_for = workspace_for
        self._max_term_chars = max_term_chars
        self._max_predicate_chars = max_predicate_chars
        # Per key, written by `inputs` and read by `accept` — the only channel
        # there is between them, because `DreamAdaptor.accept` takes no batch.
        # Keyed by `key` and not a bare attribute because `dream_loop`
        # dispatches each key as its own task: its per-(adaptor, key) `running`
        # lock is what makes ONE key's inputs/accept pair non-overlapping, and
        # nothing makes two DIFFERENT keys' pairs non-overlapping. Bounded by
        # the number of scopes, which `policy.toml` fixes at startup.
        self._message_ids: dict[str, frozenset[int]] = {}

    async def inputs(self, *, key: str, batch: Sequence[TranscriptRow]) -> str:
        """`id: body` lines, which is exactly what the objective points at
        when it says "the integer id of the message the claim was asserted
        in, exactly as it appears in the batch you were given".

        The claim's attribution is PLURAL (`CandidateClaim.message_ids`)
        because D31 makes an episode a conversational chunk, so a claim can
        span turns. This render is unchanged by that: the model is still shown
        one line per message and still copies ids off it.

        `TranscriptRow.body` is `str | None` — a media-only message has no
        text at all — so it is rendered as an empty body rather than assumed
        to be a string; an `inputs` that assumed one would take the tick down
        on a sticker. Such a row is still rendered, with its id, rather than
        skipped: `accept` validates every returned `message_ids` entry against the
        whole BATCH, so skipping a row here would make the set the model was shown
        smaller than the set `accept` will accept from it.

        The speaker is deliberately absent. The objective's first exclusion is
        "anything about a person in this room", and naming the speakers is how
        a batch of ordinary chat starts looking like a batch of claims about
        the people in it.
        """
        self._message_ids[key] = frozenset(row.id for row in batch)
        lines = [f"{row.id}: {row.body or ''}" for row in batch]
        if not lines:
            # Same rule as the adjudicator's: blank means "nothing to reason
            # about", and the runtime skips the model rather than paying for a
            # prompt that says "(none)". A watermarked adaptor reaches this on
            # the 24h age condition, which fires at `lag == 0` on purpose —
            # so without it a dormant room bought one model call a day for
            # nothing.
            return ""
        return "Messages in this batch:\n" + "\n".join(lines)

    async def accept(
        self, *, key: str, items: Sequence[Mapping[str, object]]
    ) -> Sequence[object]:
        """Typed parse into `CandidateClaim`, dropping a malformed item
        silently rather than raising.

        Two guards beyond the field types:

        - **Length caps** (the injected `max_term_chars`/`max_predicate_chars`), so an
          unbounded model-authored string never reaches a durable column.
        - **EVERY `message_ids` entry must be IN `batch`.** The objective
          tells the model "do not invent one, and do not use a message id that
          is not in the batch"; an invented id points a later reader at a
          message that does not exist, which is fabricated provenance — worse
          than a missing entry, and the same drop `LexemeObserver._extract`
          makes for the same reason. A claim with one bad id is dropped whole
          rather than trimmed to its good ones.
          `accept` has no `batch` parameter, so the set of ids
          this run showed is remembered by `inputs` (see `_message_ids`).
          When `inputs` has not run for this key — nothing in production, and
          the shared contract suite calling `accept` on its own — the check
          is skipped rather than failing every item closed: refusing a batch
          because this class has no record of showing it would be a guess
          about a call it did not observe.
        """
        seen = self._message_ids.get(key)
        parsed: list[object] = []
        for item in items:
            claim = _parse_claim(
                item, max_term=self._max_term_chars,
                max_predicate=self._max_predicate_chars,
            )
            if claim is None:
                continue
            if seen is not None and not set(claim.message_ids) <= seen:
                # The WHOLE claim goes, not the offending ids: a claim
                # half-attributed to messages that exist and half to messages
                # that do not is fabricated provenance wearing a partial
                # answer's clothes, and a later reader cannot tell which half
                # it is holding.
                log.warning(
                    "fact candidate dropped: message_ids %s are not all in "
                    "the batch (scope=%s)",
                    sorted(set(claim.message_ids) - seen), key,
                )
                continue
            parsed.append(claim)
        return tuple(parsed)

    async def commit(
        self, *, key: str, proposals: Sequence[Proposal], now_ms: int
    ) -> int:
        """Upsert every claim through the candidate store; return rows
        touched.

        **`key` is a SCOPE here and the candidate table is keyed on a
        WORKSPACE, so it is translated — this is not a detail.** This adaptor
        is scope-keyed because a watermark and a message batch are per room;
        `fact_candidate.workspace` is `Conversation.workspace` (DEC-105),
        which is the unit `FactAdaptor` adjudicates over. Writing the scope id
        into that column would put every room's candidates in a "workspace"
        named after the room, where the workspace-keyed adjudicator's
        `pending()` would never find one — a tier wired end to end and inert,
        which is the failure mode this whole subsystem is most exposed to
        because nobody is waiting on the Dreamer.

        **An unresolvable scope RAISES rather than guessing.** The runtime
        turns it into a counted `unavailable`, which holds the watermark so
        the batch is retried rather than consumed — and, more importantly,
        nothing is written. A guessed workspace is not a bad label: within one
        workspace a fact crosses rooms by design (R-49/DEC-105), so a
        candidate filed under the wrong one is a claim from one social unit
        entering another's standing facts. Every allowlisted room has a
        workspace (`Conversation.workspace` defaults to `"nora"`), so this is
        a wiring fault and reads as one.

        `commit(key, ())` still reaches the sink — it is R-27's
        `nothing_found` («I looked and there was nothing»), a legal call the
        sink may act on, and collapsing it into "nothing to do, skip the call"
        is what turns a wiring gap into a false negative one layer up.
        """
        workspace = self._workspace_for(key)
        if workspace is None:
            raise LookupError(
                f"fact-candidates: scope {key!r} resolves to no workspace, so "
                f"there is no key to file its candidates under. Filing them "
                f"under the scope id would hide them from the workspace-keyed "
                f"adjudicator; filing them under a guessed workspace would let "
                f"one social unit's claims enter another's (DEC-105/R-49)."
            )
        return len(
            await self._candidates.propose(
                key=workspace, proposals=proposals, now_ms=now_ms,
            )
        )


def _parse_claim(
    item: Mapping[str, object], *, max_term: int, max_predicate: int,
) -> CandidateClaim | None:
    """One envelope item -> a `CandidateClaim`, or `None` if it is malformed.

    `_parse_item`'s shape: every failure is the same silent drop, and there is
    deliberately no partial parse — a claim missing its object is not a claim
    with an empty object.
    """
    subject = _text(item.get("subject"), limit=max_term)
    predicate = _text(item.get("predicate"), limit=max_predicate)
    obj = _text(item.get("object"), limit=max_term)
    if subject is None or predicate is None or obj is None:
        return None
    raw = item.get("message_ids")
    if not isinstance(raw, list) or not raw:
        return None
    try:
        # `bool` is an `int` in Python and `int(True)` is 1, so a model
        # returning `[true]` would otherwise mint message 1 — a real message
        # in every conversation. Refused rather than coerced.
        ids = tuple(
            int(v) for v in raw if not isinstance(v, bool)
        )
    except (TypeError, ValueError):
        return None
    if len(ids) != len(raw):
        return None
    try:
        return CandidateClaim(
            subject=subject, predicate=predicate, object=obj, message_ids=ids,
        )
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Phase B — adjudication
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Shown:
    """What ONE adjudication run put in front of the model, remembered by
    `inputs` for `accept` and `commit` to check against.

    Both halves exist for the same reason and it is the tier's own rule: the
    model may only name something it was shown.

    - `candidate_ids` maps a candidate's TRIPLE to its row id. The triple is
      the natural key on both sides — `fact_candidate_triple_key` is
      `(workspace, subject_id, predicate, object)`, and the objective tells
      the model to copy the claim's words verbatim ("the words are how it is
      matched back"). It is what lets `commit` retire the CANDIDATE a verdict
      settled, which is §5.1's negative cache and the difference between a
      bounded cost and an unbounded one.
    - `by_handle` maps the per-tick ORDINAL a standing fact was rendered under
      to its real id. It is what `accept` checks `supersedes` against, and what
      it translates through: a handle the model invented resolves to nothing,
      which is the same refusal an unshown uuid used to get. A verdict naming
      a row nobody put in front of it is not a verdict, it is a guess with a
      delete attached.

      **It is a handle and not the id because a uuid in an envelope item is
      DESTROYED before this adaptor ever sees it.** `DreamerRuntime` runs
      `nora.agent.dream_screen.screen` over the model's raw items before
      `accept`, and the screen drops the whole item on any reachable
      identifier. `supersedes` carried a `fact.id`, so **every `contradicted`
      verdict was screened, always** — while `supported` and `not_covered`
      flowed untouched, so the tier wrote facts, moved counters and looked
      healthy without ever once recording a contradiction. Two other adaptors
      hit the same screen the same day and failed loudly; this one failed
      selectively, which is why it survived three reviews
      (`design/dreamer-agent-loop/02-instrument-blindness.md`, the
      screened-identifier family).
    """

    candidate_ids: Mapping[tuple[str, str, str], str]
    by_handle: Mapping[str, str]


class FactAdaptor:
    """Phase B: adjudicate pending candidates against the open web, write the
    facts, retire what they settled. Workspace-keyed (`Conversation.workspace`,
    DEC-105) and NOT watermarked — its input is the candidate table, not a
    message batch, so it has no watermark, lag or quiet condition and runs on
    every tick with an empty batch (`RoomPortraitSynthesizer` is the standing
    precedent).

    **`tools` is a subset of `nora.agent.dreamer.WORKSPACE_SAFE_TOOLS`, and
    that set is small for a structural reason.** Every other read tool is
    bound to a CONVERSATION — `load_transcript` reads one room's record,
    `group_members`/`lookup_person`/`about`/`ask_memory`/`search_memory` all
    answer against one room's live roster, and `recall_lookups`/`media_fetch`
    replay one room's own calls. A workspace has no single room to bind them
    to, and binding one to an arbitrary room of that workspace would be a
    disclosure decision (R-31) nobody has made. So they are refused rather
    than guessed at, and the cost is named rather than hidden: this adaptor
    researches the open web and cannot read the group's own transcript. That
    is the right side to fail on — the candidate EXTRACTOR is scope-keyed and
    reads the room; the adjudicator checks the world.

    (`weather` and `version` are in `WORKSPACE_SAFE_TOOLS` and are left out
    here. Neither answers a question about a claim's truth: `weather` is one
    live reading of one place with no bearing on an adjudication, and
    `version` reports this process. A tool granted "because it was allowed" is
    a tool the model will find a use for.)
    """

    name = "fact-adjudication"
    key_kind = "workspace"
    uses_watermark = False
    objective = "fact-adjudication"
    envelope_key = "verdicts"
    tools: frozenset[str] = frozenset({"web_search", "web_fetch", "now", "calculate"})

    def __init__(
        self,
        *,
        facts: _FactStore,
        candidates: _CandidateStore,
        max_adjudications_per_tick: int,
        max_standing_facts: int = DEFAULT_MAX_STANDING_FACTS,
        max_term_chars: int = DEFAULT_MAX_TERM_CHARS,
        max_predicate_chars: int = DEFAULT_MAX_PREDICATE_CHARS,
        entities: _EntityPromoter | None = None,
        metrics: Metrics | None = None,
    ) -> None:
        self._facts = facts
        self._candidates = candidates
        self._entities = entities
        self._max_adjudications = max_adjudications_per_tick
        self._max_standing_facts = max_standing_facts
        self._max_term_chars = max_term_chars
        self._max_predicate_chars = max_predicate_chars
        self._metrics = metrics
        self._shown: dict[str, _Shown] = {}

    # -- inputs -------------------------------------------------------------

    async def inputs(self, *, key: str, batch: Sequence[TranscriptRow]) -> str:
        """The pending candidates and the standing facts for their subjects —
        an INPUT, never a tool (§4.2).

        That is the whole of §4.2's answer to "should there be a fact-read
        tool": no. Selection stays in code where it can be capped and counted,
        and the tool subset stays read-only about the WORLD rather than
        read-only about ourselves.

        `batch` is always empty here (`uses_watermark = False`) and is
        accepted and ignored, which is the port's shape rather than a
        surprise.

        **Raises rather than degrading to an empty prompt.**
        `FactCandidateReader.pending` raises by contract and this does not
        catch it: an empty candidate list would tell the model there is
        nothing to settle when the truth is that this run could not check —
        `unavailable` reported as `nothing_found`, the one substitution R-27
        exists to forbid. The runtime's own try/except turns it into a counted
        `unavailable`, which is the honest outcome.
        `FactReader.standing_facts` never raises and degrades to `()` on its
        own; that direction is safe, because a run with no standing facts
        shown simply has no fact it is allowed to supersede.
        """
        pending = await self._candidates.pending(
            workspace=key, limit=self._max_adjudications,
        )
        subject_ids = tuple(dict.fromkeys(c.subject_id for c in pending))
        standing: tuple[Fact, ...] = ()
        if subject_ids:
            standing = await self._facts.standing_facts(
                workspace=key, subject_ids=subject_ids,
                limit=self._max_standing_facts,
            )
        self._shown[key] = _Shown(
            candidate_ids={
                (c.subject_id, c.predicate, c.object): c.id for c in pending
            },
            by_handle={str(i): f.id for i, f in enumerate(standing, start=1)},
        )
        if not pending:
            # BLANK, not "(none)" — and the difference is a model call every
            # 300s per workspace, forever. `DreamAdaptor` has no `should_fire`
            # verb; the runtime's fire condition is "did `inputs` render
            # anything to reason about", so an adaptor that renders a polite
            # empty form instead of nothing defeats it. Observed live
            # 2026-08-21: `invoke_agent dreamer:fact-adjudication` on every
            # tick against an empty candidate table.
            #
            # Returning "" makes the runtime skip the agent entirely, still
            # call `commit(key, ())`, and report `nothing_found` — which is
            # what actually happened.
            return ""
        return _render_adjudication(pending, standing)

    # -- accept -------------------------------------------------------------

    async def accept(
        self, *, key: str, items: Sequence[Mapping[str, object]]
    ) -> Sequence[object]:
        """Typed parse into `FactVerdict`. Drop, log, count.

        Three drop reasons, all counted under
        `nora_fact_adjudications_skipped_total{reason}`:

        - `malformed` — a field missing, of the wrong type, or over its cap.
        - `invariant` — `FactVerdict.__post_init__` refused it. That is the
          domain type enforcing R-27's batch invariants BY RAISING, which is
          right for a constructor and fatal for a background tick, so it is
          caught here and the item is dropped like any other bad one.
        - `unknown_supersedes` — the verdict names a HANDLE this run did not
          render. A supersession of a row nobody showed the model is not a
          verdict, it is a guess with a delete attached. Checked against
          `_Shown.by_handle` rather than against the store, because "does this
          row exist" is the wrong question — a real fact from a different
          subject is still one nobody put in front of it. Skipped entirely when
          `inputs` did not run for this key (the contract suite calling
          `accept` on its own), for the reason `FactCandidateAdaptor.accept`
          states.

        **`supersedes` arrives as a handle and leaves as a fact id.** The
        translation happens here and nowhere else, so `commit` and
        `_supersede_fact` go on seeing a real id and neither had to learn about
        handles. The handle exists only between the prompt and this line —
        which is the whole point, since an id in an envelope item is destroyed
        by the runtime's screen before `accept` runs (`_Shown.by_handle`).
        """
        shown = self._shown.get(key)
        kept: list[object] = []
        for item in items:
            verdict, reason = _parse_verdict(
                item, max_term=self._max_term_chars,
                max_predicate=self._max_predicate_chars,
            )
            if verdict is None:
                self._skip(reason or "malformed")
                continue
            if shown is not None and verdict.supersedes is not None:
                fact_id = shown.by_handle.get(verdict.supersedes.strip())
                if fact_id is None:
                    log.warning(
                        "fact verdict dropped: supersedes %r names no handle "
                        "this run rendered (workspace=%s)",
                        verdict.supersedes, key,
                    )
                    self._skip("unknown_supersedes")
                    continue
                # Handle -> id, once, here. Everything downstream keeps taking
                # a real fact id.
                verdict = replace(verdict, supersedes=fact_id)
            self._inc("nora_fact_verdicts_total", status=verdict.status)
            kept.append(verdict)
        return tuple(kept)

    # -- commit -------------------------------------------------------------

    async def commit(
        self, *, key: str, proposals: Sequence[Proposal], now_ms: int
    ) -> int:
        """The tier's two-verb sequence, in order, and the reason `commit`
        exists on `DreamAdaptor` at all: the runtime cannot do this, because
        knowing the sequence means knowing `FactVerdict`.

        1. **`propose` every proposal**, `not_covered` included. The store
           skips those by design (silence adds nothing and removes nothing)
           and returns ids only for the rows it WROTE.
        2. **`supersede` the fact each `contradicted` verdict named**, with
           `replaced_by` set to the id `propose` just returned for THAT
           verdict.
        3. **Retire the CANDIDATE**, `not_covered` into §5.1's negative cache
           and everything else as `adjudicated`.

        **How the propose-ids are matched back, since they cannot be zipped
        against `proposals` positionally.** `SqlFactStore.propose` walks
        `proposals` in order and appends an id for each one it writes, so the
        returned sequence is the ids of the SUBSEQUENCE of proposals whose
        status is not `not_covered`, in the same order. So the correspondence
        is recovered by building that same subsequence here, with the same
        filter and in the same order, and zipping — `strict=True`, and guarded
        by a length check first. If the two ever disagree, the supersession
        step is SKIPPED entirely rather than guessed at: superseding the wrong
        fact is a silent, permanent, unauditable deletion, and no count of
        rows written is worth risking one.

        Returns rows WRITTEN — `len(ids)`, not `len(proposals)`: a
        `not_covered` verdict writes no fact, so a legal commit of three can
        write one. Candidate retirements are not rows of this tier and are not
        counted here.
        """
        # Built BEFORE the call, from the same filter the store applies, so
        # the correspondence is stated rather than inferred from the result.
        writable = [
            p.payload for p in proposals
            if isinstance(p.payload, FactVerdict)
            and p.payload.status != "not_covered"
        ]
        # Each proposal is stamped with the CANDIDATE it settles, from the
        # same `_Shown` lookup `_retire_candidates` uses — so the store joins
        # back by identity instead of re-deriving the candidate from the
        # verdict's words. Re-deriving was wrong twice: the model rewording a
        # claim by one character (which `_retire_candidates` documents as
        # expected, logs, and counts) made the join miss and report a WIRING
        # GAP, and a triple match against the whole table could hand a settled
        # candidate's asserters to an unrelated later fact.
        proposals = tuple(
            replace(p, candidate_id=self._candidate_id_for(key, p.payload))
            for p in proposals
        )
        # Unconditional, empty batch included: `commit(key, ())` is R-27's
        # `nothing_found` and a legal call the sink may act on. A payload that
        # is not a `FactVerdict` raises `TypeError` in the store, which is the
        # one place that can catch it (`Proposal.payload` is `object` at the
        # port on purpose).
        ids = await self._facts.propose(
            key=key, proposals=proposals, now_ms=now_ms,
        )
        if ids:
            self._inc("nora_facts_proposed_total", value=len(ids))

        if len(ids) != len(writable):
            log.error(
                "fact commit: %d ids for %d writable verdicts (workspace=%s); "
                "supersessions SKIPPED rather than matched by guess",
                len(ids), len(writable), key,
            )
            self._skip("id_mismatch")
        else:
            for verdict, fact_id in zip(writable, ids, strict=True):
                await self._supersede_fact(
                    key=key, verdict=verdict, fact_id=fact_id, now_ms=now_ms,
                )

        await self._retire_candidates(key=key, proposals=proposals, now_ms=now_ms)
        await self._promote(key=key, wrote=bool(ids), now_ms=now_ms)
        return len(ids)

    async def _promote(self, *, key: str, wrote: bool, now_ms: int) -> None:
        """Pass 3, after the writes and OUTSIDE their transaction.

        30 §4: "promotion and embedding are downstream and idempotent, keyed
        off the fingerprint, never inside it". Inside, a promotion failure
        would roll back an adjudication that had already spent its research
        loop; outside, it is simply re-run next tick over a store that has not
        changed.

        **Only when this tick actually wrote a fact.** 25 §4: degree changes
        only when a fact lands, so a tick that wrote nothing has nothing new to
        promote — and promotion is an aggregate over the workspace, which is
        cheap but not free, and the Dreamer fires on its own clock over rooms
        that are mostly quiet.

        A failure is CAUGHT and counted for `_supersede_fact`'s reason: the
        facts are already written by the time this runs, so letting it reach
        the runtime would report the whole run `unavailable` with `written=0`
        while rows sit in the table.
        """
        if self._entities is None or not wrote:
            return
        try:
            await self._entities.promote(workspace=key, now_ms=now_ms)
        except Exception:
            log.exception("fact promotion failed: workspace=%s", key)
            self._skip("promotion_failed")

    def _candidate_id_for(self, key: str, payload: object) -> str | None:
        """The candidate a verdict settles, or `None`. ONE lookup, used by both
        the stamp above and `_retire_candidates` — they were two readings of
        the same question and only one of them was against what this run
        actually showed."""
        shown = self._shown.get(key)
        if shown is None or not isinstance(payload, FactVerdict):
            return None
        return shown.candidate_ids.get(
            (payload.subject, payload.predicate, payload.object)
        )

    async def _supersede_fact(
        self, *, key: str, verdict: FactVerdict, fact_id: str, now_ms: int,
    ) -> None:
        """Retire the fact a `contradicted` verdict named, pointing at the
        correction `propose` just wrote.

        `because="wrong"` and not `"stale"`: a contradiction says the standing
        row was never right, which is a different claim from "it was true then
        and isn't now" — and `supersede_reason` cannot be reconstructed from
        the row afterwards at any price. `source` is the verdict's own URL,
        which is a WARRANT and never a row id (`DreamSink.supersede`'s own
        separation; `replaced_by` already carries the successor).

        A failure here is caught rather than propagated, and the direction
        matters: the fact has ALREADY been written by the time this runs, so
        letting the exception reach the runtime would report the whole run
        `unavailable` with `written=0` while a row sits in the table. Counted
        instead, so a supersession that never happened is visible rather than
        merely survived.
        """
        if verdict.status != "contradicted" or verdict.supersedes is None:
            return
        try:
            affected = await self._facts.supersede(
                key=key, target=verdict.supersedes, replaced_by=fact_id,
                because="wrong", source=verdict.source_url, now_ms=now_ms,
            )
        except Exception:
            log.exception(
                "fact supersede failed: target=%s workspace=%s",
                verdict.supersedes, key,
            )
            self._skip("supersede_failed")
            return
        if affected:
            self._inc("nora_facts_superseded_total", value=affected)
        else:
            # Already superseded, or gone. Not an error — a background pass
            # may legitimately re-fire over work it already did — but silence
            # here would read as "the old fact was retired".
            log.info(
                "fact supersede affected no rows: target=%s workspace=%s",
                verdict.supersedes, key,
            )
            self._skip("supersede_missed")

    async def _retire_candidates(
        self, *, key: str, proposals: Sequence[Proposal], now_ms: int,
    ) -> None:
        """§5.1's negative cache, and the ordinary settled case beside it.

        A `not_covered` verdict retires its CANDIDATE, not the fact —
        `because="not_covered"`. Without it the same unprovable claim is
        re-extracted and re-adjudicated every tick forever, and it fails
        quietly because nobody is waiting on the Dreamer; with it, the tier
        remembers what it could not settle. A `supported`/`contradicted`
        verdict retires its candidate with `because="adjudicated"`.

        The verdict is mapped back to its candidate by the TRIPLE (see
        `_Shown`), against what THIS run showed the model. A verdict whose
        triple was not shown retires nothing: the model reworded the claim
        (which the objective forbids for exactly this reason) or invented one,
        and retiring some other candidate on a near-match would settle a claim
        nobody adjudicated.
        """
        if self._shown.get(key) is None:
            return
        for proposal in proposals:
            verdict = proposal.payload
            if not isinstance(verdict, FactVerdict):
                continue
            candidate_id = self._candidate_id_for(key, verdict)
            if candidate_id is None:
                log.info(
                    "fact verdict settled no candidate: %r/%r (workspace=%s)",
                    verdict.subject, verdict.predicate, key,
                )
                self._skip("unmatched_candidate")
                continue
            because = (
                "not_covered" if verdict.status == "not_covered" else "adjudicated"
            )
            try:
                await self._candidates.supersede(
                    key=key, target=candidate_id, replaced_by=None,
                    because=because, source=None, now_ms=now_ms,
                )
            except Exception:
                log.exception(
                    "candidate retire failed: target=%s workspace=%s",
                    candidate_id, key,
                )
                self._skip("retire_failed")

    # -- metrics ------------------------------------------------------------

    def _inc(self, name: str, value: float = 1.0, **labels: object) -> None:
        if self._metrics is not None:
            self._metrics.inc(name, value, **labels)

    def _skip(self, reason: str) -> None:
        self._inc("nora_fact_adjudications_skipped_total", reason=reason)


def _parse_verdict(
    item: Mapping[str, object], *, max_term: int, max_predicate: int,
) -> tuple[FactVerdict | None, str | None]:
    """One envelope item -> `(verdict, None)` or `(None, reason)`.

    The reason is returned rather than counted here so this stays a pure
    function the way `_parse_item` is; the caller holds the metrics.

    `addresses_claim` is required to be a real `bool`. A string `"true"` is
    the model not answering the field, and coercing it would make the one
    field that decides whether a source may contradict anything depend on a
    guess about what the model meant.
    """
    subject = _text(item.get("subject"), limit=max_term)
    predicate = _text(item.get("predicate"), limit=max_predicate)
    obj = _text(item.get("object"), limit=max_term)
    status = item.get("status")
    addresses = item.get("addresses_claim")
    if subject is None or predicate is None or obj is None:
        return None, "malformed"
    if not isinstance(status, str) or not isinstance(addresses, bool):
        return None, "malformed"
    url, url_ok = _optional_text(
        item.get("source_url"), limit=_MAX_SOURCE_URL_CHARS,
    )
    excerpt, excerpt_ok = _optional_text(
        item.get("source_excerpt"), limit=_MAX_SOURCE_EXCERPT_CHARS,
    )
    supersedes, supersedes_ok = _optional_text(
        item.get("supersedes"), limit=_MAX_FACT_ID_CHARS,
    )
    if not (url_ok and excerpt_ok and supersedes_ok):
        return None, "malformed"
    try:
        # `FactVerdict.__post_init__` is the enforcement of R-27's batch
        # invariants and it enforces them by RAISING. Caught here so the
        # domain type keeps its teeth without a malformed item taking down a
        # tick four other adaptors are sharing.
        verdict = FactVerdict(
            subject=subject, predicate=predicate, object=obj,
            status=status,  # type: ignore[arg-type]
            addresses_claim=addresses, source_url=url,
            source_excerpt=excerpt, supersedes=supersedes,
        )
    except ValueError as exc:
        log.warning("fact verdict dropped: %s", exc)
        return None, "invariant"
    return verdict, None


def _render_adjudication(
    pending: Sequence[FactCandidate], standing: Sequence[Fact],
) -> str:
    """The adjudication prompt: the claims to settle, then the facts already
    standing for their subjects.

    **Every standing fact carries a per-tick ORDINAL, never its id**, and that
    is load-bearing twice over. `supersedes` names one, so a fact rendered
    without a handle is one the model cannot correct — and a fact rendered with
    its UUID is one whose correction is destroyed before this adaptor sees it,
    because the runtime's egress screen drops any envelope item carrying an
    identifier (see `_Shown.by_handle`). A handle shown without its source
    excerpt is still one the model cannot judge as overturned, which is the
    check the objective spends its longest paragraph on.

    Candidates still render their id. It is NOT asked for and NOT read —
    `_parse_verdict` never looks at one, and `_retire_candidates` maps a
    verdict back to its candidate by the TRIPLE — so it reaches no envelope
    item and trips no screen. Left alone here deliberately: removing it changes
    what the model is shown with no defect behind it, and this change is a
    format correction rather than a prompt rewrite.
    """
    lines = ["Claims to settle:"]
    if pending:
        lines.extend(
            f"- [{c.id}] {c.subject_id} | {c.predicate} | {c.object} "
            f"(asserted {c.occurrence_count}x)"
            for c in pending
        )
    else:
        lines.append("(none)")
    lines.append("")
    lines.append("Facts already standing for these subjects:")
    if standing:
        lines.extend(
            _render_standing(f, handle=str(i))
            for i, f in enumerate(standing, start=1)
        )
    else:
        lines.append("(none)")
    return "\n".join(lines)


def _render_standing(fact: Fact, *, handle: str) -> str:
    """One standing fact, under the per-tick handle `supersedes` names.

    `handle`, never `fact.id`: see `_Shown.by_handle`. The id does not appear
    in the prompt at all, which is the second half of the fix — a model cannot
    echo back an identifier it was never shown.

    **And that applies to the OBJECT column too.** This rendered
    `object_entity_id` whenever `object_literal` was NULL — which, under the
    pre-0039 exactly-one CHECK, was every entity-object fact. The one function
    whose docstring says no identifier reaches the prompt was the function
    putting a bare uuid in it. Since 0039 `object_literal` is NOT NULL and
    the entity id is never rendered anywhere.
    """
    parts = [
        f"- [{handle}] {fact.subject_id} | {fact.predicate} | {fact.object_literal}"
    ]
    if fact.source_url:
        parts.append(f"  source: {fact.source_url}")
    if fact.source_excerpt:
        parts.append(f'  excerpt: "{fact.source_excerpt}"')
    return "\n".join(parts)




# ---------------------------------------------------------------------------
# Pass 6 — entity-resolution adjudication
# ---------------------------------------------------------------------------


#: How many unresolved names one tick puts in front of the model, and how many
#: near-misses each carries. Both small: this pass is tool-free and single-shot,
#: and a prompt listing forty names against ten candidates each is four hundred
#: judgements in one call, which is how a model starts answering by pattern
#: rather than by case.
DEFAULT_MAX_RESOLUTIONS_PER_TICK = 8
DEFAULT_MAX_CANDIDATES_PER_NAME = 5

#: Ordinal handles, and TWO alphabets on purpose. Upstream numbers both the new
#: entity and the existing candidates, which puts two integer spaces in one
#: prompt and makes `1` ambiguous at a glance. Numbers name the thing being
#: decided; letters name what it might be. It also means a handle from the
#: wrong space is REFUSED rather than silently resolving to the wrong row.
_NAME_HANDLES = "123456789"
_ENTITY_HANDLES = "abcdefghijklmnopqrstuvwxyz"


@dataclass(frozen=True)
class EntityResolution:
    """One decision: this name IS another way of writing that entity.

    Only the positive case is a value. A `null` verdict — no match, or unsure —
    produces NOTHING, because there is nothing to write: the name simply stays
    unresolved, which is the state it was already in. Representing "no" as a
    row would make the negative case indistinguishable from a decision to keep
    two things apart forever, and 21 §5's whole argument is that a split is
    provisional and a merge is not.
    """

    name: str
    entity_id: str


@dataclass(frozen=True)
class _Offered:
    """What ONE resolution run put in front of the model, remembered by
    `inputs` for `accept` to check against — `_Shown`'s shape, one pass over,
    and the same rule: the model may only name something it was shown.

    `entity_ids` maps a LETTER to a real `fact_entity.id`. The id never appears
    in the prompt, so a model cannot echo one back and `dream_screen` has
    nothing to drop.
    """

    names: dict[str, str]
    entity_ids: dict[str, dict[str, str]]


class FactEntityResolutionAdaptor:
    """Pass 6: decide the near-misses the deterministic pass surfaced.

    **This is the half of resolution that needs judgement** (21 §5). The
    deterministic half runs in the sink on every write — normalise, look up the
    canonical name and the aliases — and handles the overwhelming majority for
    free. What reaches here is what exact matching cannot settle: an
    abbreviation, a synonym, a spelling the trigram channel found but nothing
    could prove.

    **Its only write is attaching an ALIAS**, never merging two entity rows.
    §5: two entities wrongly kept apart is a missing edge, fixable later; two
    wrongly merged is a corrupted node that has absorbed both sets of edges,
    and unmerging needs provenance §8 leaves open. So the irreversible
    operation is not available to this pass at all — not discouraged, absent.

    **Workspace-keyed and watermark-free**, like the adjudicator: its input is
    the entity table and the candidate queue, not a message batch.

    **Tool-free.** Deciding whether two names mean one thing is a judgement
    about the two names, not research — and a pass that could search would be
    tempted to settle `HKO` by reading a page, which is the adjudicator's job
    on a claim, not this one's on a name.
    """

    name = "fact-entity-resolution"
    key_kind = "workspace"
    uses_watermark = False
    objective = "fact-entity-resolution"
    envelope_key = "resolutions"
    tools: frozenset[str] = frozenset()

    def __init__(
        self,
        *,
        entities: _EntityResolver,
        max_resolutions_per_tick: int = DEFAULT_MAX_RESOLUTIONS_PER_TICK,
        max_candidates_per_name: int = DEFAULT_MAX_CANDIDATES_PER_NAME,
        metrics: Metrics | None = None,
    ) -> None:
        self._entities = entities
        self._max_resolutions = max_resolutions_per_tick
        self._max_candidates = max_candidates_per_name
        self._metrics = metrics
        self._offered: dict[str, _Offered] = {}

    async def inputs(self, *, key: str, batch: Sequence[TranscriptRow]) -> str:
        """The unresolved names, each under the entities it might be another
        way of writing. `batch` is unused — this adaptor is workspace-keyed and
        reads the store, not a message window.

        **Blank when nothing is unresolved, or when nothing resembles
        anything.** The runtime skips the model on a blank input, which is the
        difference between a quiet workspace costing nothing and costing one
        call per tick forever. A name with no near-misses is not offered at
        all: there is nothing for the model to choose between, and asking would
        invite it to invent one.
        """
        names = await self._entities.unresolved_names(
            workspace=key, limit=self._max_resolutions,
        )
        offered_names: dict[str, str] = {}
        offered_entities: dict[str, dict[str, str]] = {}
        blocks: list[str] = []
        for name in names:
            handle = _NAME_HANDLES[len(offered_names)]
            candidates = await self._entities.similar_entities(
                workspace=key, name=name, limit=self._max_candidates,
            )
            if not candidates:
                continue
            offered_names[handle] = name
            by_letter = {
                _ENTITY_HANDLES[i]: entity_id
                for i, (entity_id, _canonical) in enumerate(candidates)
            }
            offered_entities[handle] = by_letter
            lines = [f"[{handle}] {name}"]
            lines += [
                f"    [{_ENTITY_HANDLES[i]}] {canonical}"
                for i, (_entity_id, canonical) in enumerate(candidates)
            ]
            blocks.append("\n".join(lines))
            if len(offered_names) >= len(_NAME_HANDLES):
                break

        self._offered[key] = _Offered(
            names=offered_names, entity_ids=offered_entities,
        )
        if not blocks:
            return ""
        self._inc("nora_fact_resolution_offered_total", value=len(offered_names))
        return "Names to resolve:\n\n" + "\n\n".join(blocks)

    async def accept(
        self, *, key: str, items: Sequence[Mapping[str, object]]
    ) -> Sequence[object]:
        """Typed parse into `EntityResolution`, dropping anything the model was
        not shown.

        **A `null` entity produces no value at all**, which is the expected and
        most common answer. It is counted, because a pass that decided "no"
        forty times and a pass that never ran look identical from the outside.

        A handle from the wrong alphabet, or one this run did not render, is
        dropped and counted — `_Shown`'s rule, one pass over: the model may
        only name something it was shown, and a resolution naming a row nobody
        put in front of it is a guess with a merge attached.
        """
        offered = self._offered.get(key)
        kept: list[object] = []
        for item in items:
            resolution = _parse_resolution(item)
            if resolution is None:
                self._inc("nora_fact_resolutions_total", status="malformed")
                continue
            name_handle, entity_handle = resolution
            if entity_handle is None:
                self._inc("nora_fact_resolutions_total", status="split")
                continue
            if offered is None:
                # `inputs` did not run for this key — the shared contract suite
                # calling `accept` on its own. Skipped rather than failing every
                # item closed, matching `FactCandidateAdaptor.accept`: refusing
                # a batch because this class has no record of showing it would
                # be a guess about a call it did not observe.
                self._inc("nora_fact_resolutions_total", status="merged")
                kept.append(EntityResolution(
                    name=name_handle, entity_id=entity_handle,
                ))
                continue
            name = offered.names.get(name_handle)
            entity_id = offered.entity_ids.get(name_handle, {}).get(entity_handle)
            if name is None or entity_id is None:
                log.warning(
                    "entity resolution dropped: [%s]->[%s] names nothing this "
                    "run rendered (workspace=%s)",
                    name_handle, entity_handle, key,
                )
                self._inc("nora_fact_resolutions_total", status="unknown_handle")
                continue
            self._inc("nora_fact_resolutions_total", status="merged")
            kept.append(EntityResolution(name=name, entity_id=entity_id))
        return tuple(kept)

    async def commit(
        self, *, key: str, proposals: Sequence[Proposal], now_ms: int
    ) -> int:
        """Attach each resolved name as an alias. Returns rows changed.

        An alias already present returns False from the store and is not
        counted — a re-run of a decision already made is not a second decision,
        and counting it would make a loop that re-fires look like a loop that
        is learning.
        """
        written = 0
        for proposal in proposals:
            resolution = proposal.payload
            if not isinstance(resolution, EntityResolution):
                raise TypeError(
                    f"FactEntityResolutionAdaptor.commit only accepts an "
                    f"EntityResolution payload, got "
                    f"{type(resolution).__name__}"
                )
            if await self._entities.attach_alias(
                workspace=key, entity_id=resolution.entity_id,
                alias=resolution.name,
            ):
                written += 1
        if written:
            self._inc("nora_fact_aliases_attached_total", value=written)
        return written

    def _inc(self, name: str, value: float = 1.0, **labels: object) -> None:
        if self._metrics is not None:
            self._metrics.inc(name, value, **labels)


def _parse_resolution(
    item: Mapping[str, object],
) -> tuple[str, str | None] | None:
    """One envelope item -> `(name_handle, entity_handle_or_None)`, or `None`
    if it is malformed.

    `entity` is `null` for the no-match case, which is a LEGAL answer and the
    expected one — distinguished here from a malformed item, which is the model
    not answering. A missing `entity` key is malformed; an explicit `null` is a
    decision.
    """
    name = item.get("name")
    if not isinstance(name, str) or name not in _NAME_HANDLES or len(name) != 1:
        return None
    if "entity" not in item:
        return None
    entity = item["entity"]
    if entity is None:
        return name, None
    if (
        not isinstance(entity, str)
        or len(entity) != 1
        or entity not in _ENTITY_HANDLES
    ):
        return None
    return name, entity

"""The culture tier's only moving part — `CultureAdaptor` (design/culture-tier).

**The gap this closes.** The Relevance Gate admitted about 1% of windows over
seven days, twice measured. That is not a mistuned threshold: `participation.md`
is a taxonomy of `go` shapes read by a model configured
`reasoning_effort = "none"`, and a reasoning-less model handed a taxonomy
pattern-matches, while a pattern-matcher handed ambiguity takes the safe label.
The operator's framing was that you cannot define when she should intervene —
so stop defining it. **A window judged against nothing is judged in a vacuum.**
"Nobody picked this up" means nothing until you know whether this room normally
picks things up. A baseline turns a window into a DEVIATION, and a deviation is
readable with no reasoning budget because the reasoning happened offline.

**What it is, and almost nothing else.** A Dreamer adaptor. Everything it needs
already exists: the runtime owns the loop, the tool ceiling, the budget, its own
doctrine and R-18/R-28 screening, and it stamps `instruction_sha256`, `model`
and `established_by` onto every `Proposal` — so provenance is not this tier's
problem, and `accept` returns payloads precisely so an adaptor never has to know
its own instruction digest.

**It reads its own batch and asks nothing of the memory system (DEC-182).** An
earlier design called Honcho's dialectic `chat` and cached the answer. It was
built and closed unmerged (#237): Honcho does not scope at the layer this needs
— `chat` on a two-message probe session answers freely about people appearing
nowhere in it, and `search` scoped to that session returns other rooms' hits
byte-identical to the unscoped query. What the adaptor reads instead is
`record.since(conversation_id=key, ...)`, `WHERE conversation_id = :key` in our
own Postgres, **hard-scoped by construction**. The chronicle-versus-account
distinction that motivated the Honcho route still stands and is still the point;
what was wrong was the attribution. **Producing an account is a property of
asking a model to write prose, not a property of that endpoint.**

**Nothing here retires (DEC-183)** and **`body` is prose with no section schema
(DEC-184)** — both are stated where they bite, in `nora.domain.culture`.

**Consumed by nobody, on purpose.** `02-measurement §5` sequences this as step
4: writing only, no behaviour change, so the chain accumulates real revisions
before any prompt reads it. A tier judged on its first snapshot is judged on the
one thing it does not contribute.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Protocol

from nora.domain.blocks import RowStyle, render_rows
from nora.domain.culture import BODY_MAX_CHARS, CultureProposal, CultureRevision
from nora.domain.models import TranscriptRow
from nora.domain.ports import Proposal
from nora.domain.prompt import escape
from nora.obs.metrics import Metrics

log = logging.getLogger(__name__)

__all__ = ["CULTURE_OUTCOMES", "STORE_CALLS_PER_TICK", "CultureAdaptor"]

#: `DreamAdaptor.store_calls_per_tick`. One `head` in `inputs`, one `head` and
#: one `append` in `commit`. `tests/architecture/test_dream_store_calls_are_
#: declared.py` counts them off this module's AST and reddens when a call is
#: added without this number moving.
STORE_CALLS_PER_TICK = 3

#: Every value `nora_culture_fold_total{outcome}` may carry, DERIVED rather than
#: hand-copied into the metric's help text — `note_candidates`' own precedent,
#: enforced at RUNTIME because `Metrics.inc` takes `**labels: object` and mypy
#: cannot check a string passed through an `object` parameter.
CULTURE_OUTCOMES = frozenset({"written", "empty", "too_many", "unchanged", "oversize"})


class _CultureStore(Protocol):
    """The two verbs this adaptor needs. NOT `DreamSink`: that port's
    `propose`/`supersede` pair exists to express «this replaces that», and
    nothing in this tier ever supersedes anything (DEC-183). An append-only
    store modelled as a sink would have a verb it must never call, which is a
    verb somebody eventually calls."""

    async def head(self, *, key: str) -> CultureRevision | None: ...

    async def append(
        self, *, key: str, body: str, written_at: int, established_by: str,
        instruction_sha256: str | None = ..., model: str | None = ...,
    ) -> CultureRevision: ...


#: NO row ids and NO timestamps, unlike every other adaptor's batch style.
#: Both are deliberate and both serve the objective's load-bearing clause —
#: "not dated observations". A model handed `[482] 2026-09-12 14:03` writes
#: about the 12th; handed a name and a line it writes about the room. The
#: chronicle this tier exists to not be is exactly `[when] what happened`, so
#: the rendering must not hand that shape over and then ask for its opposite.
#:
#: Nothing downstream needs the ids either: the account cites no messages, and
#: `accept` therefore has no id set to validate against — which is why this
#: adaptor has no `_message_ids` channel between `inputs` and `accept`.
#:
#: **Two things suppress them and EITHER ONE is sufficient**, measured by
#: mutation 2026-09-16: this style, and the fact that `_render_batch` passes no
#: `row_id=` / `timestamp=` accessors (they default to `""` and `None`).
#: Flipping the style alone changes nothing because there is nothing to render;
#: adding the accessors alone changes nothing because the style says `none`.
#: Only both together leak. That is defence in depth rather than redundancy, and
#: it is written down because a reader deleting "the unused style constant"
#: would find the suite still green and the property still held — until someone
#: later added the accessors for an unrelated reason.
_BATCH_STYLE = RowStyle(handle="none", time="none")


def _render_batch(batch: Sequence[TranscriptRow]) -> str:
    return render_rows(
        batch,
        style=_BATCH_STYLE,
        # `or "unknown"`, NEVER `or row.sender_id` — the raw id is an E.164 or
        # an ACI and this block is a prompt (R-18, DEC-80).
        speaker=lambda r: r.person_name or r.sender_name or "unknown",
        body=lambda r: r.body or "",
    )


def _parse(item: Mapping[str, object]) -> CultureProposal | None:
    """One envelope item -> a proposal, or `None` for anything malformed.

    Dropping rather than raising is `DreamAdaptor.accept`'s own contract. The
    length cap is applied HERE, before the row exists: `BODY_MAX_CHARS` says
    "no writer should produce that" and a body past it has almost certainly
    drifted back into chronicle — restating what happened instead of saying what
    the room is. The database ceiling four times higher is a corruption check
    and not this one.
    """
    body = item.get("body")
    if not isinstance(body, str):
        return None
    body = body.strip()
    if not body:
        return None
    return CultureProposal(body=body)


class CultureAdaptor:
    """One account per room per day, appended to a chain — see the module
    docstring."""

    name = "culture"
    key_kind = "scope"
    #: **`True`, and an earlier draft had it `False`**, borrowed from the note
    #: curator. The curator needs a watermark-free clock because AN ANCHOR DATE
    #: PASSING IS ITSELF THE EVENT — retirement is time-driven whether or not
    #: anyone speaks. Culture has no equivalent clock event, so the precedent
    #: does not transfer, and DEC-183 ("a quiet room's culture is accurate, not
    #: stale") already contradicted it.
    #:
    #: The consequence is that a SILENT ROOM DOES NOT FIRE, and that is correct
    #: rather than a defect: nothing here retires, so there is nothing to
    #: refresh against. Re-running on silence buys either a duplicate account or
    #: pure model variance, and the second corrupts the one signal the revision
    #: chain carries.
    uses_watermark = True
    objective = "culture"
    envelope_key = "account"
    #: TOOL-FREE for now. `web_search` earns a place in the design — when a room
    #: leans on a reference nobody explains, the loop could find out what it is,
    #: so the account can say WHY a name landed rather than merely that it did —
    #: but step 4 is writing-only and a tool call per fold is a cost to justify
    #: against a measurement that does not exist yet.
    tools: frozenset[str] = frozenset()
    store_calls_per_tick = STORE_CALLS_PER_TICK

    def __init__(
        self,
        *,
        culture: _CultureStore,
        label_for: object = None,
        metrics: Metrics | None = None,
    ) -> None:
        self._culture = culture
        self._label_for = label_for
        self._metrics = metrics

    def _inc(self, outcome: str) -> None:
        if outcome not in CULTURE_OUTCOMES:
            raise ValueError(
                f"nora_culture_fold_total{{outcome={outcome!r}}} is not in this "
                f"metric's vocabulary {sorted(CULTURE_OUTCOMES)}. A typo here "
                f"creates a new series rather than an error, and the old one "
                f"silently goes flat"
            )
        if self._metrics is not None:
            self._metrics.inc("nora_culture_fold_total", outcome=outcome)

    # -- inputs ---------------------------------------------------------

    async def inputs(self, *, key: str, batch: Sequence[TranscriptRow]) -> str:
        """The room's own messages, and the account written last time.

        **The previous account comes FIRST**, because it is how the batch is
        meant to be read: the model is continuing an account, not writing one
        from nothing, and a batch handed over with no prior context produces a
        fresh snapshot every day — which is exactly the thing the chain exists
        to be instead of.

        `batch` empty renders `""`. `DreamAdaptor` has no `should_fire` verb;
        the runtime's fire condition IS "did `inputs` render anything", so a
        quiet room must cost nothing.
        """
        if not batch:
            return ""
        parts: list[str] = []
        previous = await self._culture.head(key=key)
        if previous is not None:
            parts.append(
                "<previous_account>\n"
                f"{escape(previous.body)}\n"
                "</previous_account>"
            )
        parts.append("Messages in this batch:\n" + _render_batch(batch))
        return "\n\n".join(parts)

    # -- accept ---------------------------------------------------------

    async def accept(
        self, *, key: str, items: Sequence[Mapping[str, object]]
    ) -> Sequence[object]:
        """At most ONE account, and more than one is refused entirely.

        A room has one account. Two or more is not a partial answer, it is a
        misread of the task — and picking one of them would be an arbitrary
        choice made silently, on the row that claims to say how the room works.
        Refusing is visible (`outcome="too_many"`); picking is not.
        """
        if not items:
            self._inc("empty")
            return ()
        if len(items) > 1:
            log.warning(
                "culture: %d accounts proposed for scope=%s; a room has one, "
                "so the whole reply is discarded", len(items), key,
            )
            self._inc("too_many")
            return ()
        parsed = _parse(items[0])
        if parsed is None:
            self._inc("empty")
            return ()
        if len(parsed.body) > BODY_MAX_CHARS:
            log.warning(
                "culture: account for scope=%s is %d chars, past the %d cap — "
                "almost certainly drifted back into chronicle",
                key, len(parsed.body), BODY_MAX_CHARS,
            )
            self._inc("oversize")
            return ()
        return (parsed,)

    # -- commit ---------------------------------------------------------

    async def commit(
        self, *, key: str, proposals: Sequence[Proposal], now_ms: int
    ) -> int:
        """Append the account and move the head, in the store's one transaction.

        **A byte-identical account is not written**, and this is the one place
        this adaptor declines to record something. The chain's whole content is
        that each link differs from the one before; a run that re-derived the
        same prose adds a link carrying no information and makes the history
        longer without making it richer. It is also the common outcome for a
        batch that said nothing new, which the objective explicitly permits.

        The parent is chosen inside `append`, never here: the Dreamer dispatches
        scopes as concurrent tasks, so a parent captured before the model call
        is a parent that may have moved.
        """
        written = 0
        for proposal in proposals:
            payload = proposal.payload
            if not isinstance(payload, CultureProposal):
                raise TypeError(
                    f"CultureAdaptor.commit only accepts a CultureProposal "
                    f"payload, got {type(payload).__name__}"
                )
            previous = await self._culture.head(key=key)
            if previous is not None and previous.body == payload.body:
                self._inc("unchanged")
                continue
            await self._culture.append(
                key=key,
                body=payload.body,
                written_at=now_ms,
                established_by=proposal.established_by,
                instruction_sha256=proposal.instruction_sha256,
                model=proposal.model,
            )
            self._inc("written")
            written += 1
        return written

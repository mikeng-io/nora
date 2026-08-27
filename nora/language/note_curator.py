"""The note tier's housekeeping pass — `NoteCuratorAdaptor` (spec §9).

**The one rule that carries the design: merge is keyed on the ANCHOR, never on
topical similarity, so two weddings stay two notes.** That is enforced
structurally rather than by prompting — eligibility is a `GROUP BY
(anchor_date, entity_set) HAVING count(*) > 1` in `SqlNoteStore.merge_groups`,
so **the model is never asked whether two notes should merge.** It is asked only
what a body SAYS, which is the one thing SQL cannot read.

Topical similarity would merge two weddings: same vocabulary, same shape, same
kind of uncertainty. And the merge is unrecoverable in the direction that
matters, because two notes wrongly merged have already lost which revision
belonged to which occasion.

**Scope: merge and retire only.** Promote-to-Event needs the Events tier and
fact-coherence rewriting needs the knowledge tier, which is checkpointed with
blocking items. These two are also the pair that addresses the live problem —
`config.toml` says it plainly: *"No curator yet… the notebook only grows for
now."* Growing is what merge and retire stop.

**This module lives in `nora/language/` beside `note_render.py`** for the same
historical reason the Dreamer's watermark table does: the package predates the
tiers that use it. Recorded rather than fixed here — the extraction is its own
port change with its own migration.
"""

from __future__ import annotations

import itertools
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar, Protocol

from nora.domain.notes import (
    BODY_MAX_CHARS,
    Note,
    NoteSupersedeReason,
    StaleNoteHeadError,
)
from nora.domain.ports import Proposal
from nora.obs.metrics import Metrics

log = logging.getLogger("nora.notes.curator")

#: The verdicts the objective may return, and nothing else. A model writing
#: anything outside this set is DROPPED and counted rather than guessed at.
_VERDICTS = frozenset({"closed", "open", "moot"})

#: Every value `nora_note_curator_total{action}` can carry, DERIVED rather than
#: hand-copied into the metric's help text.
#:
#: A hand-written vocabulary drifts the moment a branch adds an action — this
#: one already had, silently: `retire_failed` and `retire_stale` were emitted
#: for a round before the help text knew about them. `tests/unit/
#: test_note_curator.py` asserts the help text names exactly this set, and it
#: is enforced at RUNTIME rather than by types because `nora/language` is
#: outside `make gate`'s mypy scope (#61) — a `Literal` alone would be checked
#: by nothing here.
CURATOR_ACTIONS: frozenset[str] = frozenset({
    "merge",           # a group was merged into its survivor
    "merge_refused",   # the survivor moved, or the merge was refused — NOT an
                       # error: the group is still a group next tick
    "merge_failed",    # the store refused the merge for a domain reason; the
                       # tick continues and earlier merges stand
    "retire_failed",   # supersede raised; the tick continues
    "retire_stale",    # the note moved between inputs and commit, so the
                       # reading the model judged no longer describes it
    "closed",          # retired: the question was answered
    "moot",            # retired: the occasion passed
})

#: Every value `nora_note_curator_dropped_total{reason}` can carry.
CURATOR_DROPS: frozenset[str] = frozenset({
    "malformed",                # a field missing, wrong type, verdict outside
                                # the three, or a body over the cap
    "unknown_note",             # a handle this run did not render
    "not_shown_in_group",       # a merge for a group SQL did not select
    "conflicting_verdict",      # two contradictory answers about one note
    "conflicting_merged_body",  # two different bodies for one survivor
    "body_too_long",            # a merged body over BODY_MAX_CHARS — the
                                # objective asked for more than the cap allows
})

#: Every value `nora_note_curator_groups_refused_total{reason}` can carry.
CURATOR_GROUP_REFUSALS: frozenset[str] = frozenset({"no_entity_anchor"})


class _NoteStore(Protocol):
    async def merge_groups(
        self, *, workspace: str, limit: int,
    ) -> tuple[
        tuple[tuple[tuple[Note, str], ...], ...],
        tuple[tuple[tuple[Note, str], ...], ...],
    ]: ...

    async def anchored_before(
        self, *, workspace: str, day: int, limit: int,
    ) -> tuple[tuple[Note, str], ...]: ...

    async def read(
        self, *, workspace: str, note_id: str,
    ) -> tuple[Note, Any] | None: ...

    async def merge(
        self, *, workspace: str, survivor: str, survivor_parent: str,
        merged_body: str, retire: Sequence[str], established_by: str,
        instruction_sha256: str | None, model: str | None, now_ms: int,
    ) -> int: ...

    async def supersede(
        self, *, key: str, target: str, replaced_by: str | None,
        because: str, source: str | None, now_ms: int,
    ) -> int: ...


@dataclass(frozen=True)
class _Verdict:
    """One parsed answer about one note. Not a domain type: it never leaves
    this module and it is not written anywhere — the curator's OUTPUT is a
    merge or a retirement, and this is what the model said on the way there."""

    note_id: str
    verdict: str
    why: str


@dataclass(frozen=True)
class _MergedBody:
    """The one body that keeps what every note in a group knew.

    Keyed on the SURVIVOR's id — the group's oldest note — because that is the
    handle `commit` merges into and the only one the model was given for the
    group. A separate type from `_Verdict` for the reason the payload shapes
    are separate one layer down: two things with different required fields
    should not be one thing with optional ones, or nothing can be refused.
    """

    note_id: str
    body: str


@dataclass(frozen=True)
class _Shown:
    """What THIS run put in front of the model, so `accept` can refuse a
    verdict about a note the run never showed.

    The same defence `FactAdaptor` keeps for the same reason: a model can
    invent an id, and a retirement of a note nobody asked about is a silent,
    permanent removal from an index the group cannot see the inside of.
    """

    #: handle -> note id, THE JOIN THIS TICK ASKED ABOUT. The model never sees
    #: a note id and never returns one; it answers about ordinals this run
    #: rendered, and `accept` resolves them here.
    by_handle: Mapping[str, str]
    #: group handles, survivor first — the same ordering `merge_groups`
    #: returned, so `commit` can still name the survivor without an id ever
    #: reaching the model.
    merge_groups: tuple[tuple[str, ...], ...]
    heads: Mapping[str, str]


class NoteCuratorAdaptor:
    """One `DreamAdaptor`: read a workspace's notebook, decide what has closed
    and what is the same occasion twice, and write merges and retirements.

    `key_kind = "workspace"` — the notebook is workspace-scoped (§3/DEC-105),
    so both `friends` rooms tick ONE notebook rather than two. That is what
    `Dependencies.workspace_scopes` exists for.

    `uses_watermark = False`, and this is the interesting one. The other
    adaptors read an append-only log through a cursor. This one reads LIVE
    STATE: which notes collide on an anchor, and which anchors have passed.
    Neither question has a "since when" — a duplicate is a duplicate however
    long it has been one, and **retirement is TIME-driven, not evidence-driven**
    (§9.1). A watermark would make the pass stop noticing an expired note the
    moment the notebook went quiet, which is exactly when expiry matters.

    **The pass is self-limiting FOR THE ACTIONS IT TAKES, and not otherwise.**
    A merge removes its group; a retirement removes its note. But a group the
    model DECLINES to merge — or one refused for having no entity anchor — is
    re-selected every tick, forever, and permanently occupies a
    `max_groups_per_tick` slot. Observed: five consecutive ticks, model fired
    each time, nothing written, the same two notes standing.

    The general claim ("everything it does removes its own trigger") was in
    this docstring and was FALSE, which is worth saying rather than quietly
    narrowing: it is true of the write paths and says nothing about the paths
    that write nothing, which are the common ones. `nora_note_curator_groups_
    refused_total` and a flat `nora_note_curator_total{action="merge"}` are
    what make that visible; a cooldown is the fix if the number says it hurts,
    and is deliberately not built on a guess.
    """

    name = "note-curation"
    key_kind = "workspace"
    uses_watermark = False
    objective = "note-curation"
    envelope_key = "verdicts"
    #: No tools. The curator reasons about bodies this process selected and
    #: handed it; it has nothing to look up, and `read_only_tools` being a
    #: ceiling rather than a grant means an empty subset is the honest
    #: declaration rather than a missing one.
    tools: frozenset[str] = frozenset()

    def __init__(
        self,
        *,
        notes: _NoteStore,
        today: Callable[[], int],
        max_groups_per_tick: int,
        max_expired_per_tick: int,
        metrics: Metrics | None = None,
    ) -> None:
        self._notes = notes
        # A callable, not a date: the pass compares against a YYYYMMDD calendar
        # day and the clock is the caller's to own — a store or an adaptor that
        # reads the wall clock cannot be tested for the boundary case that
        # matters (a note anchored to TODAY is not expired).
        self._today = today
        self._max_groups = max_groups_per_tick
        self._max_expired = max_expired_per_tick
        self._metrics = metrics
        self._shown: dict[str, _Shown] = {}

    #: metric name -> the vocabulary its label may take. Checked on every
    #: increment, because a label typo produces a NEW SERIES rather than an
    #: error, and a new series is invisible until somebody notices the old one
    #: went flat.
    _VOCABULARIES: ClassVar[dict[str, tuple[str, frozenset[str]]]] = {
        "nora_note_curator_total": ("action", CURATOR_ACTIONS),
        "nora_note_curator_dropped_total": ("reason", CURATOR_DROPS),
        "nora_note_curator_groups_refused_total": (
            "reason", CURATOR_GROUP_REFUSALS,
        ),
    }

    def _inc(self, name: str, value: float = 1.0, **labels: object) -> None:
        vocab = self._VOCABULARIES.get(name)
        if vocab is not None:
            label, allowed = vocab
            got = labels.get(label)
            if got not in allowed:
                raise ValueError(
                    f"{name}{{{label}={got!r}}} is not in this metric's "
                    f"vocabulary {sorted(allowed)}. A typo here creates a new "
                    f"series rather than an error, and the old one silently "
                    f"goes flat"
                )
        if self._metrics is not None:
            self._metrics.inc(name, value, **labels)

    # -- inputs -------------------------------------------------------------

    async def inputs(self, *, key: str, batch: Sequence[Any]) -> str:
        """The colliding groups and the expired notes, rendered.

        `batch` is always empty (`uses_watermark = False`) and is accepted and
        ignored — the port's shape rather than a surprise.

        **Returns `""` when there is nothing, and that is load-bearing.**
        `DreamAdaptor` has no `should_fire` verb; the runtime's fire condition
        is "did `inputs` render anything to reason about", so an adaptor that
        renders a polite empty form instead of nothing buys a model call every
        300s per workspace forever. Observed live on the fact adaptor
        2026-08-21. A curator will have nothing to do on the overwhelming
        majority of ticks, so this matters more here than anywhere.
        """
        groups, refused = await self._notes.merge_groups(
            workspace=key, limit=self._max_groups,
        )
        # A REFUSED group is a reading, not a silence. Without this, a year of
        # a quiet curator cannot be told apart from a year of no duplicates —
        # and this number is what says whether populating `note_entity` would
        # unlock anything, measured against the real notebook.
        for _ in refused:
            self._inc(
                "nora_note_curator_groups_refused_total",
                reason="no_entity_anchor",
            )
        expired = await self._notes.anchored_before(
            workspace=key, day=self._today(), limit=self._max_expired,
        )
        # ORDINAL HANDLES, NOT IDS, and this is not a readability choice.
        #
        # The Dreamer screens every item an adaptor returns against R-18/R-28
        # (`nora.agent.dream_screen`), and `scan_identifiers` matches ANY
        # hyphenated uuid. Note ids are uuid4. So a curator that asked the
        # model about ids got every answer rejected at the screen — the tick
        # fired, spent a model call, `accept` received nothing, `commit` wrote
        # nothing, and the run reported OK because the envelope was non-empty.
        # Silent, forever, with `nora_dream_screened_total{reason="identifier"}`
        # as the only tell.
        #
        # **Every unit test missed it because `InMemoryNoteStore` ids are
        # `note-1`**, which the screen passes. The double said yes on exactly
        # the property that decides it.
        #
        # The screen is NOT exempted — it is the disclosure floor and an
        # adaptor arguing its way around it is the thing R-18 exists to stop.
        # The handles also remove `unknown_note` by construction: a handle this
        # run did not render is simply absent from the map.
        by_handle: dict[str, str] = {}
        group_handles: list[tuple[str, ...]] = []
        ordinal = itertools.count(1)
        for g in groups:
            handles: list[str] = []
            for note, _ in g:
                handle = str(next(ordinal))
                by_handle[handle] = note.id
                handles.append(handle)
            group_handles.append(tuple(handles))
        expired_handles: list[tuple[str, Note, str]] = []
        for note, body in expired:
            handle = str(next(ordinal))
            by_handle[handle] = note.id
            expired_handles.append((handle, note, body))

        self._shown[key] = _Shown(
            by_handle=by_handle,
            merge_groups=tuple(group_handles),
            heads={
                n.id: n.head_revision_id
                for n in [x for g in groups for x, _ in g]
                + [n for n, _ in expired]
            },
        )
        if not groups and not expired:
            return ""
        # A LEVEL, not a running total: the reading that matters is how much
        # of this tick's budget was spent, and a counter cannot answer it.
        if self._metrics is not None:
            self._metrics.gauge(
                "nora_note_curator_bodies_read",
                float(sum(len(g) for g in groups) + len(expired)),
                workspace=key,
            )
        return _render(
            list(zip(group_handles, groups, strict=True)),
            expired_handles,
        )

    # -- accept -------------------------------------------------------------

    async def accept(
        self, *, key: str, items: Sequence[Mapping[str, object]]
    ) -> Sequence[object]:
        """Typed parse into `_Verdict`. Drop, log, count — never guess.

        Three drop reasons, all counted under
        `nora_note_curator_dropped_total{reason}`:

        - `malformed` — a field missing, of the wrong type, or a `verdict`
          outside the three the objective defines. A model writing "expired"
          instead of "moot" is a drop, not a synonym: guessing here is guessing
          about whether to delete somebody's note.
        - `unknown_note` — names a note this RUN did not show. A model can
          invent a uuid, and a retirement is a permanent removal from an index
          nobody can see the inside of. Same defence `FactAdaptor` keeps
          against a supersession of a fact it never displayed.
        - `not_shown_in_group` — a `merged_body` for a group this run did not
          select. Merge eligibility is SQL's decision (§9.3); a model proposing
          its own grouping is the one thing this design exists to prevent, so
          it is refused rather than merged.
        """
        shown = self._shown.get(key, _Shown({}, (), {}))
        # BOTH shapes. A model answering about a merge group returns a
        # `merged_body`; a model answering about an expired note returns a
        # `verdict`. One item may legitimately carry both — the survivor of a
        # group whose occasion has also passed.
        out: list[object] = list(self._merged_bodies(key=key, items=items))
        for item in items:
            if "verdict" not in item:
                # A merge-only answer is not malformed. Counting it as such
                # would make `dropped_total{reason="malformed"}` fire on every
                # correct merge.
                continue
            note_id = item.get("note_id")
            verdict = item.get("verdict")
            why = item.get("why", "")
            if (
                not isinstance(note_id, str)
                or not isinstance(verdict, str)
                or verdict not in _VERDICTS
                or not isinstance(why, str)
            ):
                self._inc(
                    "nora_note_curator_dropped_total", reason="malformed")
                log.warning("note curator dropped a malformed verdict")
                continue
            resolved = shown.by_handle.get(note_id)
            if resolved is None:
                # A handle this run did not render. Impossible to reach by
                # invention in the way a uuid was — an ordinal outside the
                # rendered range simply is not in the map — but still counted,
                # because "the model answered about something we did not ask"
                # is a real reading and silence is not.
                self._inc(
                    "nora_note_curator_dropped_total", reason="unknown_note")
                log.warning(
                    "note curator named a handle this run did not render")
                continue
            out.append(_Verdict(note_id=resolved, verdict=verdict, why=why))
        return tuple(out)

    def _merged_bodies(
        self, *, key: str, items: Sequence[Mapping[str, object]]
    ) -> list[_MergedBody]:
        """The merge half of `accept`, and it exists because the first version
        of this module HAD NO WAY TO PRODUCE ONE.

        `_MergedBody` was defined, `commit` checked for it, and nothing ever
        constructed it — so `SqlNoteStore.merge` was unreachable dead code and
        two declared metrics had no reachable call site, in the same commit
        that declared them. A model obeying the objective and returning
        `{"note_id": survivor, "merged_body": "..."}` has no `verdict`
        field, so it failed the verdict parse and was dropped as malformed:
        there was NO INPUT the model could produce that resulted in a merge.

        Split from the verdict parse rather than folded into it because the two
        shapes have disjoint required fields — the same reason the payload
        types one layer down are two types and not one with optional fields. A
        single parser accepting either would have to treat every field as
        optional and could refuse nothing.

        `not_shown_in_group` is enforced HERE, which the docstring above
        promised and the first version did not implement: merge eligibility is
        SQL's decision, so a `merged_body` for a note that is not the survivor
        of a group THIS RUN selected is refused rather than merged. A model
        proposing its own grouping is the one thing this design exists to
        prevent.
        """
        shown = self._shown.get(key, _Shown({}, (), {}))
        survivors = {g[0] for g in shown.merge_groups}
        out: list[_MergedBody] = []
        for item in items:
            if "merged_body" not in item:
                continue
            body = item.get("merged_body")
            note_id = item.get("note_id")
            if isinstance(body, str) and len(body) > BODY_MAX_CHARS:
                # ITS OWN REASON, not `malformed`. The remedies differ: a
                # missing field means the model answered the wrong shape, and
                # an over-cap body means it answered the RIGHT shape and the
                # objective asked for something it cannot honour — "keep every
                # concrete thing, do not summarise" over two bodies of up to
                # BODY_MAX_CHARS each. One is fixed in the model, the other in
                # the instruction, and a shared label cannot tell an operator
                # which.
                self._inc(
                    "nora_note_curator_dropped_total", reason="body_too_long")
                log.warning(
                    "note curator returned a merged body over the cap; the "
                    "group is left alone")
                continue
            if (
                not isinstance(note_id, str)
                or not isinstance(body, str)
                or not body.strip()
            ):
                self._inc(
                    "nora_note_curator_dropped_total", reason="malformed")
                # OVER-CAP IS COUNTED HERE, not raised at the store. It is
                # the INSTRUCTED outcome: the objective says "keep every
                # concrete thing, do not summarise" over two bodies of up to
                # BODY_MAX_CHARS each and cannot honour both. Left to reach
                # `merge`, it raises `ValueError` out of `commit`, discards an
                # already-committed merge's count, and re-raises every tick —
                # stalling that workspace's retirements permanently.
                log.warning("note curator dropped a malformed merged_body")
                continue
            if note_id not in survivors:
                self._inc(
                    "nora_note_curator_dropped_total",
                    reason="not_shown_in_group")
                log.warning(
                    "note curator proposed a merge for a group this run did "
                    "not select; dropped")
                continue
            resolved = shown.by_handle[note_id]
            seen = next((m for m in out if m.note_id == resolved), None)
            if seen is not None:
                # THE SAME RULE AS CONFLICTING VERDICTS, on the more
                # destructive path. Two merged bodies for one survivor was
                # silent last-write-wins ON CONTENT — the second overwrote the
                # first and nothing counted it. "The model said both" is «I
                # cannot tell», and the fail-closed answer on a merge is to
                # leave the group alone.
                if seen.body != body:
                    out.remove(seen)
                    self._inc(
                        "nora_note_curator_dropped_total",
                        reason="conflicting_merged_body")
                    log.warning(
                        "note curator returned two different merged bodies for "
                        "one survivor; the group is left alone")
                continue
            out.append(_MergedBody(note_id=resolved, body=body))
        return out

    # -- commit -------------------------------------------------------------

    async def commit(
        self, *, key: str, proposals: Sequence[Proposal], now_ms: int
    ) -> int:
        """The tier's write sequence, and the reason `commit` exists on
        `DreamAdaptor` at all: the runtime cannot do this, because knowing the
        sequence means knowing what a `_Verdict` is.

        1. **Merges first**, through `SqlNoteStore.merge` — ONE transaction per
           group, appending the merged body to the survivor and retiring the
           rest. That verb exists because `propose`-then-`supersede` spanned
           two transactions and left the exact half-applied state `propose`'s
           atomicity was justified by preventing.
        2. **Retirements**, for a verdict of `closed` or `retired-expired`.
           `open` writes nothing and is the common answer.

        **The survivor is the OLDEST note in the group.** `merge_groups`
        returns each group ordered by `(created_at, id)`, and the first one is
        kept — an arbitrary but STABLE rule, which matters because it must give
        the same answer if the same group is seen again after a partial
        failure. Choosing by body length or by recency would let a retried
        merge pick a different survivor and produce two merged notes.

        Returns rows written, which for this pass means notes retired.
        """
        merged_bodies = {
            p.payload.note_id: p.payload
            for p in proposals
            if isinstance(p.payload, _MergedBody)
        }
        # CONFLICTING VERDICTS ARE DROPPED, NOT RESOLVED. A dict comprehension
        # here was last-write-wins: two contradictory answers about one note
        # collapsed to whichever came second, uncounted, on an operation that
        # RETIRES things. "The model said both" is not a tie to break — it is
        # «I cannot tell», and the fail-closed answer to that is to leave the
        # note standing. Agreeing duplicates are fine and collapse silently.
        verdicts: dict[str, _Verdict] = {}
        conflicted: set[str] = set()
        for p in proposals:
            if not isinstance(p.payload, _Verdict):
                continue
            seen = verdicts.get(p.payload.note_id)
            if seen is not None and seen.verdict != p.payload.verdict:
                conflicted.add(p.payload.note_id)
                continue
            verdicts[p.payload.note_id] = p.payload
        for note_id in conflicted:
            verdicts.pop(note_id, None)
            self._inc(
                "nora_note_curator_dropped_total", reason="conflicting_verdict")
            log.warning(
                "note curator returned contradictory verdicts for one note; "
                "leaving it standing")
        shown = self._shown.get(key, _Shown({}, (), {}))
        provenance = proposals[0] if proposals else None
        retired = 0
        merged_groups: list[tuple[str, ...]] = []

        for group in shown.merge_groups:
            survivor = shown.by_handle[group[0]]
            body = merged_bodies.get(survivor)
            if body is None:
                continue
            try:
                count = await self._notes.merge(
                    workspace=key, survivor=survivor,
                    survivor_parent=shown.heads[survivor],
                    merged_body=body.body,
                    retire=[shown.by_handle[h] for h in group[1:]],
                    established_by=(
                        provenance.established_by if provenance else "curator"
                    ),
                    instruction_sha256=(
                        provenance.instruction_sha256 if provenance else None
                    ),
                    model=provenance.model if provenance else None,
                    now_ms=now_ms,
                )
            except StaleNoteHeadError:
                # The survivor moved. NOT an error: the group is still a group
                # and the next tick sees it again, because the selection read
                # is live state rather than a cursor.
                log.info("note merge refused; the survivor moved under it")
                self._inc("nora_note_curator_total", action="merge_refused")
                continue
            except ValueError:
                # ValueError as well as the CAS loss: `merge` builds a
                # `NoteRevision`, whose `__post_init__` refuses a body over the
                # cap. One group's bad body must not discard the tick — an
                # earlier group's merge is already COMMITTED in Postgres, and
                # letting this escape reports written=0 over rows that exist.
                # A lost CAS means the tag path revised the survivor while this
                # pass was thinking. That is the case the transaction exists
                # for and it is not an error: the group is still a group, and
                # the next tick sees it again because `merge_groups` reads live
                # state rather than a cursor.
                # THE BRACES. `_merged_bodies` refuses an over-cap body before
                # it reaches here, so this is unreachable by that route — and
                # it is kept because the store builds a `NoteRevision` whose
                # `__post_init__` can refuse for reasons this module does not
                # enumerate, and one group's domain refusal must not discard a
                # tick in which an earlier group ALREADY COMMITTED. Letting it
                # escape reports written=0 over rows that exist in Postgres.
                log.exception("note merge failed; continuing with the tick")
                self._inc("nora_note_curator_total", action="merge_failed")
                continue
            retired += count
            merged_groups.append(
                tuple(shown.by_handle[h] for h in group))
            self._inc("nora_note_curator_total", action="merge")

        # ONLY the groups that actually merged, and the SURVIVOR is in here
        # too. Two findings in one line:
        #
        #   - computing this from `shown.merge_groups` unconditionally skipped
        #     retirements for groups whose merge never ran or was REFUSED on a
        #     lost CAS, so a tick could end with the survivor retired and the
        #     duplicate still standing — inverted, silently.
        #   - `g[1:]` protected the duplicates and not the survivor, so a model
        #     returning a merged body for a group AND `closed` for its survivor
        #     merged into it and then retired it. The whole occasion leaves the
        #     index, with the duplicate pointing at a retired row.
        #
        # The store's own `test_merge_never_retires_the_survivor_itself` cannot
        # see that: the curator retires it through a SEPARATE `supersede` call
        # the store has no way to associate with the merge.
        untouchable = {n for g in merged_groups for n in g}
        for note_id, verdict in verdicts.items():
            if verdict.verdict == "open" or note_id in untouchable:
                continue
            because = (
                NoteSupersedeReason.RETIRED_CLOSED
                if verdict.verdict == "closed"
                else NoteSupersedeReason.RETIRED_EXPIRED
            )
            # GUARDED PER NOTE. Retirements are independent of one another in
            # a way merge-and-retire is not, so one failing is N-of-M retired
            # rather than a half-applied edit — but an exception escaping here
            # would discard `retired`, INCLUDING a merge that already
            # committed, and the runtime would report `unavailable` with
            # written=0 over notes that have left the index. That is `ok`
            # wearing `unavailable`'s clothes, which is the reporting failure
            # this tier refuses one layer down.
            # THE HEAD CHECK, and it is the retirement half of the CAS the
            # merge path has had all along. `supersede` takes an id and no
            # parent, so a note whose body changed between `inputs` and
            # `commit` was retired on a reading of a body that no longer
            # exists — and the objective's ONE warning is that «we moved it to
            # the 16th» leaves the question open. The model would have said
            # `open` had it seen it. `replaced_by` is None for a retirement, so
            # nothing points at what was lost.
            #
            # `_NoteStore.read` was declared for this and never called: the
            # protocol member for a check that was not wired.
            current = await self._notes.read(workspace=key, note_id=note_id)
            if current is None or current[0].head_revision_id != shown.heads.get(
                note_id
            ):
                log.info(
                    "note moved under the curator between inputs and commit; "
                    "not retiring on a stale reading")
                self._inc("nora_note_curator_total", action="retire_stale")
                continue
            try:
                count = await self._notes.supersede(
                    key=key, target=note_id, replaced_by=None,
                    because=because, source=None, now_ms=now_ms,
                )
            except Exception:
                log.exception("note retirement failed; continuing")
                self._inc(
                    "nora_note_curator_total", action="retire_failed")
                continue
            retired += count
            if count:
                self._inc("nora_note_curator_total", action=verdict.verdict)
        return retired


def _render(
    groups: Sequence[tuple[Sequence[str], Sequence[tuple[Note, str]]]],
    expired: Sequence[tuple[str, Note, str]],
) -> str:
    """What the model is shown. Titles and bodies, no ids it did not need.

    The note ids ARE included, because the objective's answer has to name what
    it is answering about and a title is not unique — two notes about the same
    occasion written twice is precisely the case where titles collide, and it
    is the case this pass exists for.

    Bodies are rendered whole. `render_notes`' index elides them on the tag
    path because awareness is the point there; here the body IS the question,
    and a truncation would have the model deciding whether an arrangement has
    closed from a prefix of it.
    """
    parts: list[str] = []
    for handles, group in groups:
        parts.append(f"<merge_group survivor=\"{handles[0]}\">")
        for handle, (note, body) in zip(handles, group, strict=True):
            # THE BODY, which this omitted until 2026-08-28 while the docstring
            # twelve lines above claimed otherwise. The model is asked to write
            # one body that keeps everything every note in the group knew — and
            # was being shown titles and closing conditions only. A merged body
            # synthesised from two titles, written over a survivor that had
            # accumulated real detail, is content loss produced BY the merge.
            parts.append(
                f"  <note id=\"{handle}\" title=\"{note.title}\">\n"
                f"    uncertainty: {note.uncertainty}\n"
                f"    closing condition: {note.closing_condition}\n"
                f"    body: {body}\n"
                f"  </note>"
            )
        parts.append("</merge_group>")
    for handle, note, body in expired:
        parts.append(
            f"<note id=\"{handle}\" title=\"{note.title}\" "
            f"anchor=\"{note.anchor_date}\">\n"
            f"  uncertainty: {note.uncertainty}\n"
            f"  closing condition: {note.closing_condition}\n"
            f"  body: {body}\n"
            f"</note>"
        )
    return "\n".join(parts)

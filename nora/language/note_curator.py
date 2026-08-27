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

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from nora.domain.notes import Note, NoteSupersedeReason, StaleNoteHeadError
from nora.domain.ports import Proposal
from nora.obs.metrics import Metrics

log = logging.getLogger("nora.notes.curator")

#: The verdicts the objective may return, and nothing else. A model writing
#: anything outside this set is DROPPED and counted rather than guessed at.
_VERDICTS = frozenset({"closed", "open", "moot"})


class _NoteStore(Protocol):
    async def merge_groups(
        self, *, workspace: str, limit: int,
    ) -> tuple[tuple[Note, ...], ...]: ...

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

    note_ids: frozenset[str]
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

    That also makes the pass self-limiting without a strike: everything it does
    REMOVES its own trigger. A merged group is no longer a group; a retired
    note is no longer standing.
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

    def _inc(self, name: str, value: float = 1.0, **labels: object) -> None:
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
        groups = await self._notes.merge_groups(
            workspace=key, limit=self._max_groups,
        )
        expired = await self._notes.anchored_before(
            workspace=key, day=self._today(), limit=self._max_expired,
        )
        self._shown[key] = _Shown(
            note_ids=frozenset(
                [n.id for g in groups for n in g] + [n.id for n, _ in expired],
            ),
            merge_groups=tuple(tuple(n.id for n in g) for g in groups),
            heads={
                n.id: n.head_revision_id
                for n in [x for g in groups for x in g] + [n for n, _ in expired]
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
        return _render(groups, expired)

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
        shown = self._shown.get(key, _Shown(frozenset(), (), {}))
        out: list[object] = []
        for item in items:
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
            if note_id not in shown.note_ids:
                self._inc(
                    "nora_note_curator_dropped_total", reason="unknown_note")
                log.warning(
                    "note curator named a note this run did not show; dropped")
                continue
            out.append(_Verdict(note_id=note_id, verdict=verdict, why=why))
        return tuple(out)

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
        verdicts = {
            p.payload.note_id: p.payload
            for p in proposals
            if isinstance(p.payload, _Verdict)
        }
        shown = self._shown.get(key, _Shown(frozenset(), (), {}))
        provenance = proposals[0] if proposals else None
        retired = 0

        for group in shown.merge_groups:
            body = merged_bodies.get(group[0])
            if body is None:
                continue
            survivor = group[0]
            try:
                count = await self._notes.merge(
                    workspace=key, survivor=survivor,
                    survivor_parent=shown.heads[survivor],
                    merged_body=body.body, retire=group[1:],
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
                # A lost CAS means the tag path revised the survivor while this
                # pass was thinking. That is the case the transaction exists
                # for and it is not an error: the group is still a group, and
                # the next tick sees it again because `merge_groups` reads live
                # state rather than a cursor.
                log.info("note merge refused; the survivor moved under it")
                self._inc("nora_note_curator_total", action="merge_refused")
                continue
            retired += count
            self._inc("nora_note_curator_total", action="merge")

        merged_away = {n for g in shown.merge_groups for n in g[1:]}
        for note_id, verdict in verdicts.items():
            if verdict.verdict == "open" or note_id in merged_away:
                continue
            because = (
                NoteSupersedeReason.RETIRED_CLOSED
                if verdict.verdict == "closed"
                else NoteSupersedeReason.RETIRED_EXPIRED
            )
            count = await self._notes.supersede(
                key=key, target=note_id, replaced_by=None, because=because,
                source=None, now_ms=now_ms,
            )
            retired += count
            if count:
                self._inc("nora_note_curator_total", action=verdict.verdict)
        return retired


def _render(
    groups: Sequence[Sequence[Note]], expired: Sequence[tuple[Note, str]],
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
    for group in groups:
        parts.append(
            f"<merge_group survivor=\"{group[0].id}\">"
        )
        for note in group:
            parts.append(
                f"  <note id=\"{note.id}\" title=\"{note.title}\">\n"
                f"    uncertainty: {note.uncertainty}\n"
                f"    closing condition: {note.closing_condition}\n"
                f"  </note>"
            )
        parts.append("</merge_group>")
    for note, body in expired:
        parts.append(
            f"<note id=\"{note.id}\" title=\"{note.title}\" "
            f"anchor=\"{note.anchor_date}\">\n"
            f"  uncertainty: {note.uncertainty}\n"
            f"  closing condition: {note.closing_condition}\n"
            f"  body: {body}\n"
            f"</note>"
        )
    return "\n".join(parts)

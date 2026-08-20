"""The one LLM call for Dreamer Plan 2 (2026-08-20 exchange-patterns design) —
the exchange sibling of `nora.language.observer`. Same split as Plan 1:
`nora.domain.exchange_scoring`'s arithmetic and `nora.infra.sql.exchange`'s
rescore never touch a model; this is the single place that decides WHICH
closed-set act a group of messages performs.

**This module is the withhold rule's first structural guarantee** (DEC-111,
DEC-120): the prompt below offers the model a FIXED vocabulary of acts
(`nora.infra.sql.schema.EXCHANGE_ACTS`) and nothing resembling a free-text
gloss field. `_parse_item` rejects, at parse time, any `act` not in that set —
the same "reject at parse time" discipline `nora.language.observer._parse_item`
applies to oversized fields — so there is no path by which a model-authored
claim about a PERSON (rather than a selected ACT two-or-more people performed)
can reach `ExchangeCandidate` at all.

**The instruction text is not in this file any more** — it is
`nora/dreamer/objectives/room-exchange.md`, lifted VERBATIM (substrate spec
§7.1 step a) and arriving through the injected `objective` collaborator, read
per call because the file hot-reloads on the bind mount. The act vocabulary in
that body is `EXCHANGE_ACTS`, and the parse-time rejection of anything outside
it stays HERE, in Python: the withhold rule's structural half is not something
an operator can soften by editing prose.

Same layering constraints as `nora.language.observer`: no `strands`, no
`nora.agent.factory`, no `nora.config` — the model client is an injected
`__init__` collaborator, structurally typed rather than a
`nora.domain.ports.Protocol`, for the same single-caller reasoning that
module's own docstring gives.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from nora.domain.models import ExchangeCandidate, TranscriptRow
from nora.domain.provenance import instruction_sha256
from nora.infra.sql.schema import EXCHANGE_ACTS
from nora.obs.metrics import Metrics

log = logging.getLogger(__name__)

_ACT_SET = frozenset(EXCHANGE_ACTS)
_MIN_PARTICIPANTS = 2


class _ModelClient(Protocol):
    """The lexeme observer's `_ModelClient` shape, duplicated here for the
    reason that module documents. `model` is the name of the model
    `complete` calls — read `nora.language.observer._ModelClient` for why
    provenance asks the client rather than taking the name as a second
    `__init__` argument. It matters more here, not less: this adaptor is
    wired from `[language].exchange_model or [language].model`, so the name
    beside the client would be a DIFFERENT expression from the one inside
    it."""

    @property
    def model(self) -> str: ...

    async def complete(self, *, prompt: str) -> str: ...


# The objective file this adaptor sends, by name. A name, not a body: the body
# is on disk and reloads without a restart.
ROOM_EXCHANGE_OBJECTIVE = "room-exchange"


class _ObjectiveSource(Protocol):
    """`nora.language.observer._ObjectiveSource`, duplicated here for the
    reason that module documents for `_ModelClient`: a documented duck-type
    with one consumer, not a shared `nora.domain.ports` Protocol. A SOURCE
    rather than a string because the objective hot-reloads — an observer handed
    the text at construction would keep sending whatever was on disk when the
    process started."""

    def objective_body(self, name: str) -> str: ...


def _build_prompt(preamble: str, batch: Sequence[TranscriptRow]) -> str:
    lines = [preamble]
    for row in batch:
        speaker = row.person_name or row.sender_name or row.sender_id
        lines.append(f"[{row.id}] {speaker}: {row.body or ''}")
    return "\n".join(lines)


def _parse_patterns(raw: str) -> list[Any] | None:
    """Mirrors `nora.language.observer._parse_observations` exactly: any
    shape that is not `{"patterns": [...]}` is `None`, never raised — R-5,
    silence beats a wrong answer."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    patterns = data.get("patterns")
    if not isinstance(patterns, list):
        return None
    return patterns


def _parse_item(item: Any) -> dict[str, Any] | None:
    """One pattern dict -> its typed fields, or `None` if malformed — the
    withhold rule's parse-time half: `act` outside `EXCHANGE_ACTS` and fewer
    than `_MIN_PARTICIPANTS` message ids are both rejected HERE, before
    `_extract`'s evidence join ever runs, exactly the same "reject at parse
    time" ordering `nora.language.observer._parse_item` uses for its own
    field-length guard."""
    if not isinstance(item, dict):
        return None
    act = item.get("act")
    if not isinstance(act, str) or act not in _ACT_SET:
        return None
    raw_ids = item.get("message_ids")
    if not isinstance(raw_ids, list) or len(raw_ids) < _MIN_PARTICIPANTS:
        return None
    try:
        # Deduplicated and SORTED — canonical order, so the same underlying
        # evidence extracted twice (a retried cycle, or the model listing the
        # same ids in a different order) natural-keys to the same row in
        # `exchange_observation` (`UNIQUE (scope_id, act, message_ids)`)
        # instead of silently inserting a near-duplicate.
        message_ids = tuple(sorted({int(i) for i in raw_ids}))
        confidence = float(item["confidence"])
    except (KeyError, TypeError, ValueError):
        return None
    if len(message_ids) < _MIN_PARTICIPANTS:
        return None
    return {"act": act, "message_ids": message_ids, "confidence": confidence}


@dataclass(frozen=True)
class ExchangeObservationResult:
    """`observe()`'s return — mirrors `nora.language.observer.ObservationResult`
    field-for-field, including the same `parsed=False` vs. genuinely-empty
    distinction and the same reason `LanguageConsolidator`/`ExchangeConsolidator`
    need it: a batch the model never actually read must not let the watermark
    advance past it."""

    candidates: tuple[ExchangeCandidate, ...]
    parsed: bool


class ExchangePatternObserver:
    """Runs the ONE model call and joins what comes back to real evidence —
    the exchange sibling of `nora.language.observer.LexemeObserver`. Same
    collaborator shape, same optional `metrics`."""

    def __init__(
        self, model: _ModelClient, *, objective: _ObjectiveSource,
        metrics: Metrics | None = None,
    ) -> None:
        self._model = model
        self._objective = objective
        self._metrics = metrics
        # The other half of the provenance pair, read from the client itself
        # — see `model` below and `LexemeObserver`'s own note. The instruction
        # digest is NOT computed here: the objective hot-reloads, so it is read
        # per run (`observe`) and a value captured at construction would name
        # the corpus as it was at process start.
        self._model_name = model.model

    @property
    def model(self) -> str:
        """The model name stamped onto every candidate this instance
        produces — the sibling of `nora.language.observer.LexemeObserver
        .model`, and independently meaningful here because
        `[language].exchange_model` may name a different model from the
        lexeme adaptor's for the same room and the same tick."""
        return self._model_name

    @property
    def instruction_sha256(self) -> str:
        """The digest this instance stamps onto every candidate right now
        (`nora.domain.provenance`), over `room-exchange`'s body — the whole of
        the instruction text this adaptor sends. There is no verification
        sibling here (`nora.language.observer.LexemeObserver` has one, and folds
        it in only when it is wired), so the set is one part and the digest
        necessarily differs from that adaptor's even where the vocabulary
        overlaps.

        Re-read on every access rather than cached, because the objective file
        hot-reloads: a cached value would keep naming the corpus as it was at
        process start.

        The Dreamer doctrine is deliberately NOT in this digest yet — Phase 1 is
        the verbatim lift and no adaptor sends the doctrine, so hashing it would
        bind the stamp to text that never reached the model. The re-partition
        adds it to the prompt and to the digest in one change.
        """
        return instruction_sha256(
            self._objective.objective_body(ROOM_EXCHANGE_OBJECTIVE)
        )

    async def observe(
        self, *, batch: Sequence[TranscriptRow]
    ) -> ExchangeObservationResult:
        if not batch:
            return ExchangeObservationResult(candidates=(), parsed=True)
        # Read ONCE, before the call: every row this run writes carries the
        # digest of the text this run actually sent.
        preamble = self._objective.objective_body(ROOM_EXCHANGE_OBJECTIVE)
        prompt = _build_prompt(preamble, batch)
        raw = await self._model.complete(prompt=prompt)
        return self._extract(raw, batch, instruction_sha256(preamble))

    def _extract(
        self, raw: str, batch: Sequence[TranscriptRow], stamp: str,
    ) -> ExchangeObservationResult:
        patterns = _parse_patterns(raw)
        if patterns is None:
            if self._metrics is not None:
                self._metrics.inc("nora_exchange_parse_failures_total")
            log.warning(
                "exchange-pattern extraction: model response did not parse as "
                "the expected JSON shape (%d chars)", len(raw),
            )
            return ExchangeObservationResult(candidates=(), parsed=False)
        by_id = {row.id: row for row in batch}
        candidates: list[ExchangeCandidate] = []
        for item in patterns:
            parsed = _parse_item(item)
            if parsed is None:
                continue
            rows = [by_id.get(mid) for mid in parsed["message_ids"]]
            if any(row is None for row in rows):
                # A well-formed claim whose evidence is not (entirely) in the
                # batch — dropped and counted, same shape
                # `LexemeObserver._extract` applies to a hallucinated
                # `message_id`, extended to every id in the array.
                if self._metrics is not None:
                    self._metrics.inc(
                        "nora_exchange_candidates_dropped_total", reason="no_evidence",
                    )
                continue
            # `participants`: the JOINED rows' own `person_id`s — NEVER a
            # model claim — deduplicated, sorted, and re-checked for the
            # >= 2-DISTINCT-PEOPLE guarantee: two message ids from the SAME
            # sender name a pattern with only one participant, which the
            # withhold rule forbids just as firmly as a single message_id
            # would.
            participants = sorted({
                row.person_id for row in rows if row is not None and row.person_id
            })
            if len(participants) < _MIN_PARTICIPANTS:
                if self._metrics is not None:
                    self._metrics.inc(
                        "nora_exchange_candidates_dropped_total",
                        reason="single_participant",
                    )
                continue
            candidates.append(ExchangeCandidate(
                act=parsed["act"],
                message_ids=parsed["message_ids"],
                participants=tuple(participants),
                confidence=parsed["confidence"],
                # Provenance, stamped where the model's answer becomes a
                # candidate — the sibling of `LexemeObserver`'s own stamp,
                # both fields on the one construction.
                instruction_sha256=stamp,
                model=self._model_name,
            ))
        return ExchangeObservationResult(candidates=tuple(candidates), parsed=True)

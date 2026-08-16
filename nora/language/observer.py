"""The one LLM call in the Dreamer (Task 6). Everything downstream —
`nora.domain.language_scoring`'s arithmetic, `nora.infra.sql.language`'s
rescore — never touches a model; this is the single place where one decides
what a candidate word MEANS. The split is `nora.domain.language_scoring`'s own
argument run the other direction: an LLM glosses, arithmetic decides adoption,
and folding them into one call would hand a policy decision to a stochastic
process and make the lexicon unreproducible from its own evidence.

**Boundaries this module is held to, and why the obvious approach is
blocked** (see `tests/test_boundaries.py::test_strands_import_boundary`,
`lint-imports`' "layered architecture" and "only app and __main__ read
config" contracts): `nora/language/` may not import `strands` (only
`nora.agent`/`nora.compute` may), may not import `nora.agent.factory` (a
layer `nora.language` cannot reach into), and may not import `nora.config`
(the composition root reads config and passes values in). So `LexemeObserver`
takes its model client as an **injected `__init__` collaborator** rather than
building one itself — a later task's composition root constructs the real
one.

**Collaborator shape: structural typing (`_ModelClient` below), not a
`nora.domain.ports.Protocol`.** This call is a single `prompt -> str`
completion used from exactly one place; promoting it to the shared port
registry would commit a second implementation (the production client) and a
fake to CLAUDE.md's shared contract-suite rule for a shape with no second
consumer to justify one. `evals/judge.py:33-38` sets the precedent this
follows: it builds its own model-client wiring rather than sharing
`nora.agent.factory`'s, because a duplicated seam here is cheaper than a
cross-boundary import. The structural type is documented, not
`@runtime_checkable`-declared, so nothing in `nora.language` needs to import
`nora.domain.ports` just to describe it.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from typing import Any, Protocol

from nora.domain.models import LexemeCandidate, TranscriptRow
from nora.obs.metrics import Metrics

log = logging.getLogger(__name__)

# Fields every well-formed observation must carry. Anything short of this —
# a missing key, the wrong type, a `message_id` that won't parse as an int —
# is a MALFORMED item, dropped silently (R-5): it never reached evidence at
# all, so it is not the same failure `nora_language_candidates_dropped_total`
# exists to count (a well-formed item whose evidence is absent from the
# batch — see `LexemeObserver._extract`).
_STRING_FIELDS = ("surface_form", "canonical_form", "sense", "part_of_speech")


class _ModelClient(Protocol):
    """The structural shape `LexemeObserver`'s `model` collaborator must
    satisfy — one method, one call per `observe()`. See module docstring for
    why this stays a documented duck-type instead of a declared Protocol on
    `nora.domain.ports`."""

    async def complete(self, *, prompt: str) -> str: ...


_PROMPT_PREAMBLE = (
    "You are reading a batch of messages from one Signal group chat. Your "
    "only job is to spot SLANG THIS ROOM HAS MADE ITS OWN — a word, "
    "abbreviation, or phrase used with a specific local meaning that an "
    "outsider would not get from a dictionary. This is NOT a transcription "
    "or translation task.\n\n"
    "Do NOT report:\n"
    "- ordinary Cantonese or English words used in their ordinary sense "
    "(係, 唔係, 好, OK, lor, la, thanks, ...);\n"
    "- standard internet slang already in general use everywhere "
    "(lol, 笑死, yyds meaning its dictionary sense);\n"
    "- anything you are not confident is genuinely used with a LOCAL, "
    "room-specific sense.\n\n"
    "It is completely correct — and expected most of the time — to find "
    "NOTHING. Reporting an ordinary word just because a batch was given to "
    "you makes the output worse than empty.\n\n"
    "For each genuine candidate you MUST point at the exact message it came "
    "from, by its numeric id from the list below. A candidate whose "
    "message_id is not one of the ids given is worthless and will be "
    "discarded.\n\n"
    "Respond with ONLY a JSON object of this exact shape, nothing else — no "
    "prose, no markdown fence:\n"
    '{"observations": [{"surface_form": "<as written>", '
    '"canonical_form": "<normalised form>", "sense": "<what it means here, '
    'in Cantonese or English>", "part_of_speech": "<noun/verb/adjective/'
    'interjection/...>", "message_id": <int, must be one of the ids below>, '
    '"confidence": <0.0-1.0 float>}]}\n'
    "If nothing qualifies, respond with exactly: "
    '{"observations": []}\n\n'
    "Messages:\n"
)


def _build_prompt(batch: Sequence[TranscriptRow]) -> str:
    lines = [_PROMPT_PREAMBLE]
    for row in batch:
        speaker = row.person_name or row.sender_name or row.sender_id
        lines.append(f"[{row.id}] {speaker}: {row.body or ''}")
    return "\n".join(lines)


def _parse_observations(raw: str) -> list[Any] | None:
    """The model's raw text -> a list of candidate dicts, or None on any
    shape that is not exactly `{"observations": [...]}`. Never raises —
    prose, an empty body, and truncated JSON all land here as None, which is
    what makes them indistinguishable from "nothing found" at `observe`'s
    return value (R-5: silence beats a wrong answer)."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    observations = data.get("observations")
    if not isinstance(observations, list):
        return None
    return observations


def _parse_item(item: Any) -> dict[str, Any] | None:
    """One observation dict -> its typed fields, or None if it is missing
    any required field or carries the wrong type for one. This is the
    MALFORMED-ITEM guard, separate from (and checked before) the
    evidence-join in `LexemeObserver._extract` — an item that fails here
    never had a real claim to check evidence against."""
    if not isinstance(item, dict):
        return None
    try:
        message_id = int(item["message_id"])
        confidence = float(item["confidence"])
        fields: dict[str, Any] = {name: str(item[name]) for name in _STRING_FIELDS}
    except (KeyError, TypeError, ValueError):
        return None
    fields["message_id"] = message_id
    fields["confidence"] = confidence
    return fields


class LexemeObserver:
    """Runs the ONE model call and joins what comes back to real evidence.

    `model` is the single required collaborator (see module docstring for
    its shape and why it is not a `nora.domain.ports.Protocol`). `metrics`
    is optional and defaulted, following this repo's own standing pattern
    for a decoration-counter collaborator (`nora.agent.relay.RelayHandler`,
    `nora.agent.whereis.WhereisTool`): a caller with no metrics wired
    (today's unit tests) still gets correct behaviour, and the composition
    root threads the real `Metrics` instance through.
    """

    def __init__(self, model: _ModelClient, *, metrics: Metrics | None = None) -> None:
        self._model = model
        self._metrics = metrics

    async def observe(self, *, batch: Sequence[TranscriptRow]) -> list[LexemeCandidate]:
        """One model call over `batch`, or nothing for an empty batch (no
        call is worth making over zero messages). Never raises on a
        malformed response — see `_parse_observations`/`_parse_item` — and
        every returned candidate is guaranteed to point at a row that is
        actually in `batch` (see `_extract`)."""
        if not batch:
            return []
        prompt = _build_prompt(batch)
        raw = await self._model.complete(prompt=prompt)
        return self._extract(raw, batch)

    def _extract(
        self, raw: str, batch: Sequence[TranscriptRow]
    ) -> list[LexemeCandidate]:
        observations = _parse_observations(raw)
        if observations is None:
            # A model that degrades into returning prose forever is
            # INDISTINGUISHABLE from one correctly finding nothing unless
            # something counts THIS branch specifically — a well-formed
            # `{"observations": []}` never reaches here at all (it parses
            # fine and falls through to an empty `candidates` list below).
            # Both states look identical from the outside (a lexicon that
            # stops growing); this is the one trace that tells them apart.
            if self._metrics is not None:
                self._metrics.inc("nora_language_parse_failures_total")
            log.warning(
                "lexeme extraction: model response did not parse as the "
                "expected JSON shape (%d chars)", len(raw),
            )
            return []
        by_id = {row.id: row for row in batch}
        candidates: list[LexemeCandidate] = []
        for item in observations:
            parsed = _parse_item(item)
            if parsed is None:
                # Malformed shape — never had evidence to check, so it is
                # not what nora_language_candidates_dropped_total counts.
                continue
            row = by_id.get(parsed["message_id"])
            if row is None:
                # A well-formed claim whose evidence is not in the batch —
                # the model hallucinated a message_id. Fabricated provenance
                # is worse than a missing entry (a quote rendered under a
                # gloss is what makes a wrong gloss visible), so this is
                # dropped and counted, never kept.
                if self._metrics is not None:
                    self._metrics.inc(
                        "nora_language_candidates_dropped_total",
                        reason="no_evidence",
                    )
                continue
            candidates.append(LexemeCandidate(
                surface_form=parsed["surface_form"],
                canonical_form=parsed["canonical_form"],
                sense=parsed["sense"],
                part_of_speech=parsed["part_of_speech"],
                message_id=parsed["message_id"],
                # NEVER the model's own claim: the JOINED row's own
                # person_id is the roster's canonical id, and it is what
                # nora.infra.sql.language rescores speaker spread against.
                person_id=row.person_id,
                confidence=parsed["confidence"],
            ))
        return candidates

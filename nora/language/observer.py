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
from dataclasses import dataclass
from typing import Any, Protocol

from nora.domain.models import LexemeCandidate, TranscriptRow
from nora.domain.ports import SearchProvider
from nora.domain.provenance import instruction_sha256
from nora.obs.metrics import Metrics

log = logging.getLogger(__name__)

# Fields every well-formed observation must carry. Anything short of this —
# a missing key, the wrong type, a `message_id` that won't parse as an int —
# is a MALFORMED item, dropped silently (R-5): it never reached evidence at
# all, so it is not the same failure `nora_language_candidates_dropped_total`
# exists to count (a well-formed item whose evidence is absent from the
# batch — see `LexemeObserver._extract`).
_STRING_FIELDS = ("surface_form", "canonical_form", "sense", "part_of_speech")

# Reject, at parse time, a field implausibly longer than any genuine gloss
# could be — so an over-long MODEL-AUTHORED field never reaches
# `LanguageConsolidator.append_observations` and the store at all, ahead of
# (not instead of) `nora.language.render`'s own render-time truncation of
# `canonical_form`/`sense`/the quote. Render-time truncation alone still
# bounds what ever reaches a reader, but a huge value would still sit in
# Postgres, ride every `rescore` pass's `jsonb_agg`, and cost bandwidth on
# every read even though a reader never sees past the render cap — this is
# the store-side half of that same defence, generous enough to never reject
# a genuine gloss (`render.py`'s caps are the DISPLAY bound, an order of
# magnitude tighter, and are what a legitimate over-length value actually
# gets cut down to). `surface_form` is bounded too even though it no longer
# feeds the rendered quote (`context` does, from the message body) — it is
# still a stored, model-authored column and part of the append's natural key.
_MAX_FIELD_CHARS = {
    "surface_form": 300,
    "canonical_form": 100,
    "sense": 500,
    "part_of_speech": 50,
}


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


# The Dreamer's known-entity check (2026-08-20 design doc,
# docs/superpowers/specs/2026-08-20-dreamer-known-entity-check-design.md):
# a SECOND, narrow question over `web_search` results, kept separate from
# `_PROMPT_PREAMBLE`'s extraction question rather than folded into one call —
# the module docstring's own "an LLM glosses, arithmetic decides" split,
# applied a second time to keep this one call's job to exactly one question.
_VERIFICATION_PROMPT_TEMPLATE = (
    "A room-language extractor flagged the phrase below as slang a Signal "
    "group chat has made its own. Your ONLY job is to check whether it is "
    "actually already a KNOWN PUBLIC thing — a real game, place, brand, "
    "meme, or other name with an established meaning OUTSIDE this specific "
    "group — rather than something this group coined.\n\n"
    "Phrase: {surface_form}\n"
    "Claimed local sense: {sense}\n\n"
    "Web search results for the phrase:\n{results}\n\n"
    "Respond with ONLY a JSON object of this exact shape, nothing else — no "
    "prose, no markdown fence:\n"
    '{{"known_entity": <true if the results show this is an established '
    "public thing whose own meaning the room's use matches or refers to; "
    'false if the results are irrelevant, sparse, or establish no public '
    'meaning>}}\n'
)


def _build_verification_prompt(
    candidate: LexemeCandidate, results: Sequence[Any],
) -> str:
    lines = [
        str(item["content"]) for item in results
        if isinstance(item, dict) and item.get("content")
    ]
    return _VERIFICATION_PROMPT_TEMPLATE.format(
        surface_form=candidate.surface_form,
        sense=candidate.sense,
        results="\n".join(lines) if lines else "(no results)",
    )


def _parse_verification(raw: str) -> bool | None:
    """The verification call's raw text -> its known-entity verdict, or
    `None` on any shape that is not exactly `{"known_entity": <bool>}`.
    Never raises — `observe`'s caller treats `None` exactly like `False`
    (keep the candidate): a verification response this codebase cannot even
    parse carries no basis to drop real evidence on (R-5's `_extract` sibling
    argument, applied to this second call)."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    verdict = data.get("known_entity")
    return verdict if isinstance(verdict, bool) else None


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
    any required field, carries the wrong type for one, or carries a
    STRING field longer than `_MAX_FIELD_CHARS` allows (module docstring's
    "Reject, at parse time" note) — all three are the same MALFORMED-ITEM
    guard, separate from (and checked before) the evidence-join in
    `LexemeObserver._extract`: an item that fails here never had a real
    claim to check evidence against."""
    if not isinstance(item, dict):
        return None
    try:
        message_id = int(item["message_id"])
        confidence = float(item["confidence"])
        fields: dict[str, Any] = {name: str(item[name]) for name in _STRING_FIELDS}
    except (KeyError, TypeError, ValueError):
        return None
    for name, limit in _MAX_FIELD_CHARS.items():
        if len(fields[name]) > limit:
            return None
    fields["message_id"] = message_id
    fields["confidence"] = confidence
    return fields


@dataclass(frozen=True)
class ObservationResult:
    """`observe()`'s return: the joined candidates, plus whether the model's
    response PARSED as the expected `{"observations": [...]}` shape at all.

    `parsed=False` means the extraction itself is untrustworthy — `candidates`
    is always `()` in that case, but the reverse is not true: a well-formed
    `{"observations": []}` (the model correctly finding nothing) also yields
    empty `candidates`, with `parsed=True`. That distinction is the whole
    reason this is a dataclass and not a bare `list` (`observe()`'s original
    shape): a model degrading into permanent prose looks IDENTICAL to one
    correctly finding nothing if only `candidates` is inspected — both are an
    empty list forever — and `LanguageConsolidator.consolidate` needs to tell
    them apart to decide whether `_dream_once` may advance the watermark past
    this batch. Advancing over a batch the model never actually read would
    consume those messages permanently with nothing to retry.
    """

    candidates: tuple[LexemeCandidate, ...]
    parsed: bool


class LexemeObserver:
    """Runs the ONE model call and joins what comes back to real evidence.

    `model` is the single required collaborator (see module docstring for
    its shape and why it is not a `nora.domain.ports.Protocol`). `metrics`
    is optional and defaulted, following this repo's own standing pattern
    for a decoration-counter collaborator (`nora.agent.relay.RelayHandler`,
    `nora.agent.whereis.WhereisTool`): a caller with no metrics wired
    (today's unit tests) still gets correct behaviour, and the composition
    root threads the real `Metrics` instance through.

    `search` is likewise optional and defaulted — `None` (every existing
    caller, and any deployment with `[language].verify_known_entities =
    false`) reproduces EXACTLY today's behaviour: no known-entity check ever
    runs. Unlike `model`, `search` IS typed against
    `nora.domain.ports.SearchProvider` directly rather than a local
    structural type — that port already has a second consumer
    (`nora.agent.tools`'s `web_search`/`web_fetch`) and a passing contract
    suite (`tests/contract/test_port_conformance.py`), so there is no "single
    caller" argument here the way there is for `_ModelClient` (see module
    docstring); reusing the shared port is strictly cheaper than inventing a
    duplicate shape for the same collaborator. See
    `docs/superpowers/specs/2026-08-20-dreamer-known-entity-check-design.md`.
    """

    def __init__(
        self, model: _ModelClient, *, metrics: Metrics | None = None,
        search: SearchProvider | None = None, verify_max_candidates: int = 10,
    ) -> None:
        self._model = model
        self._metrics = metrics
        self._search = search
        self._verify_max_candidates = verify_max_candidates
        # Computed ONCE, from the instruction text this instance is
        # CONFIGURED to send — see the property below for what that set is
        # and why the verification template is in it only when `search` is
        # wired. Constant per instance, so every row one Dreamer cycle
        # writes carries the same stamp and a corpus can be split by
        # revision.
        self._instruction_sha256 = instruction_sha256(*(
            (_PROMPT_PREAMBLE, _VERIFICATION_PROMPT_TEMPLATE)
            if search is not None else (_PROMPT_PREAMBLE,)
        ))

    @property
    def instruction_sha256(self) -> str:
        """The digest stamped onto every candidate this instance produces
        (`nora.domain.provenance`), over the instruction text that actually
        governs a row here: `_PROMPT_PREAMBLE`, plus
        `_VERIFICATION_PROMPT_TEMPLATE` when — and only when — a `search`
        collaborator is wired.

        **Why the verification template is in the set at all**, given that it
        authors no field: it decides whether a candidate becomes a row. Two
        corpora written either side of an edit to it differ in POPULATION,
        and a stamp that could not tell them apart would be bound to half its
        input — the same defect §4.3 rejects `objective_sha256` for. **Why it
        drops out when `search is None`**: it is then never sent, and
        stamping text that did not reach a model is provenance bound to the
        wrong property, which is the defect this column exists to remove.

        The set is a property of the CONFIGURED instance, not of each
        candidate's control flow: a candidate kept because verification was
        `unavailable`, or because it fell past `verify_max_candidates`,
        carries the same stamp as its verified siblings. That is deliberate —
        the question the column answers is "which instruction revision was
        this Dreamer running when it wrote this row", which is what makes an
        eval's «does the row from revision X survive» answerable at all
        (spec §3.3). Per-candidate branch-accurate stamping would make the
        digest vary within one cycle for reasons that have nothing to do with
        the instruction text.

        Exposed rather than private because §4.4 wants a Dreamer tick to LOG
        its instruction versions the way a tag start logs
        `doctrine_versions={…}`; that is a later change, and this is the value
        it will read.
        """
        return self._instruction_sha256

    async def observe(self, *, batch: Sequence[TranscriptRow]) -> ObservationResult:
        """One model call over `batch`, or nothing for an empty batch (no
        call is worth making over zero messages — returned as `parsed=True`,
        `candidates=()`: there was nothing to fail to parse). Never RAISES on
        a malformed response — see `_parse_observations`/`_parse_item` — that
        case is reported through `ObservationResult.parsed`, not an
        exception; every returned candidate is guaranteed to point at a row
        that is actually in `batch` (see `_extract`).

        When `search` is wired AND extraction produced at least one
        well-formed candidate, each is additionally checked against
        `_verify_known_entities` before being returned — see that method's
        docstring for the fail-open contract. A `parsed=False` result (the
        model degraded to prose) or an empty candidate list short-circuits
        before ever touching `search`: there is nothing worth a search call
        either way."""
        if not batch:
            return ObservationResult(candidates=(), parsed=True)
        prompt = _build_prompt(batch)
        raw = await self._model.complete(prompt=prompt)
        result = self._extract(raw, batch)
        if self._search is None or not result.parsed or not result.candidates:
            return result
        verified = await self._verify_known_entities(self._search, result.candidates)
        return ObservationResult(candidates=verified, parsed=True)

    async def _verify_known_entities(
        self, search: SearchProvider, candidates: tuple[LexemeCandidate, ...],
    ) -> tuple[LexemeCandidate, ...]:
        """Check up to `verify_max_candidates` of `candidates` against
        `search` before they can reach the store (2026-08-20 design doc) —
        FAILS OPEN on every branch: a search `unavailable`, a `nothing_found`,
        and an unparseable classification response all KEEP the candidate,
        the same direction `_extract`'s own "fabricated provenance is worse
        than a missing entry" argument takes — a wrongly-dropped candidate is
        gone forever (the watermark advances past this batch regardless of
        what `observe` returns), while a wrongly-kept one only reproduces
        today's already-shipped behaviour. Candidates past the cap are kept
        unverified for the identical reason: "did not get to check" is never
        treated as "assume the worst and drop it"."""
        kept: list[LexemeCandidate] = []
        for index, candidate in enumerate(candidates):
            if index >= self._verify_max_candidates:
                kept.append(candidate)
                continue
            outcome = await search.search(query=candidate.canonical_form)
            if not isinstance(outcome, dict):
                kept.append(candidate)
                continue
            status = outcome.get("status")
            if self._metrics is not None and isinstance(status, str):
                self._metrics.inc("nora_language_verification_total", status=status)
            if status != "ok":
                # "unavailable" (fail-open) and "nothing_found" (no evidence
                # of a public meaning) both keep the candidate unchanged.
                kept.append(candidate)
                continue
            results = outcome.get("results")
            verdict_raw = await self._model.complete(
                prompt=_build_verification_prompt(
                    candidate, results if isinstance(results, list) else [],
                ),
            )
            if _parse_verification(verdict_raw) is True:
                if self._metrics is not None:
                    self._metrics.inc(
                        "nora_language_candidates_dropped_total",
                        reason="known_entity",
                    )
                log.info(
                    "lexeme %r dropped: verified as a known public entity",
                    candidate.canonical_form,
                )
                continue
            # False, or None (unparseable — fail open): keep.
            kept.append(candidate)
        return tuple(kept)

    def _extract(
        self, raw: str, batch: Sequence[TranscriptRow]
    ) -> ObservationResult:
        observations = _parse_observations(raw)
        if observations is None:
            # A model that degrades into returning prose forever is
            # INDISTINGUISHABLE from one correctly finding nothing unless
            # something counts THIS branch specifically — a well-formed
            # `{"observations": []}` never reaches here at all (it parses
            # fine and falls through to an empty `candidates` list below).
            # Both states look identical from the outside (a lexicon that
            # stops growing) unless something ALSO carries `parsed=False`
            # through the return value — the metric alone is observable only
            # to an operator watching Prometheus, not to
            # `LanguageConsolidator`, which is what actually has to decide
            # whether the watermark may advance over this batch.
            if self._metrics is not None:
                self._metrics.inc("nora_language_parse_failures_total")
            log.warning(
                "lexeme extraction: model response did not parse as the "
                "expected JSON shape (%d chars)", len(raw),
            )
            return ObservationResult(candidates=(), parsed=False)
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
                # Provenance, stamped at the ONE place a model's answer
                # becomes a candidate — see the `instruction_sha256`
                # property.
                instruction_sha256=self._instruction_sha256,
            ))
        return ObservationResult(candidates=tuple(candidates), parsed=True)

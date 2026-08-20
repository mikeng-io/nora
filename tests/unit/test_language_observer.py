"""Tests for `nora.language.observer.LexemeObserver` — the Dreamer's
extraction, and (2026-08-20) its optional known-entity verification step.
Every fixture here builds a `FakeModel` that stands in for the real model
client — the SAME collaborator shape `LexemeObserver.__init__` takes
(`async def complete(self, *, prompt: str) -> str`) — and it must reproduce
the real client's WRONG-CALL SHAPES (an empty body, prose instead of JSON, a
hallucinated `message_id`), not just its clean one. A fake that only ever
returned one well-formed candidate would keep every test below green through
a regression in the prompt or the parser (CLAUDE.md's fake-test policy).
`FakeSearch` is the equivalent double for the optional `search` collaborator
(`nora.domain.ports.SearchProvider` shape) — see its own docstring below.
"""

from __future__ import annotations

import json

import pytest

from nora.domain.models import DeliveryStatus, TranscriptRow
from nora.domain.provenance import instruction_sha256
from nora.language import observer as observer_module
from nora.language.observer import LexemeObserver
from nora.obs.metrics import Metrics

pytestmark = pytest.mark.asyncio


class FakeModel:
    """A same-process double for the `model` collaborator. `responses` is
    consumed in order, one per `observe()` call, mirroring exactly how a real
    client answers one prompt with one completion — never a canned "always
    one clean candidate" reply."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)

    async def complete(self, *, prompt: str) -> str:
        return self._responses.pop(0)


def rows(*bodies: str) -> list[TranscriptRow]:
    """A batch of ordinary transcript rows, ids assigned in order starting
    at 1 — the shape `LexemeObserver.observe` is handed in production."""
    return [
        TranscriptRow(
            id=i, sender_id="+84164353469", sender_name="Miles", ts=1_000 + i,
            body=body, is_nora=False, delivery_status=DeliveryStatus.SENT,
            raw_json=None, person_id=None,
        )
        for i, body in enumerate(bodies, start=1)
    ]


class FakeSearch:
    """A same-process double for the `search` collaborator
    (`nora.domain.ports.SearchProvider` shape). `by_query` maps an exact query
    string to the dict `search()` answers with — mirrors how the real
    `TavilySearchProvider` answers one real query, not a canned always-`ok`
    reply; a query not in the map is a test bug (raises `KeyError`, not a
    silently-wrong default)."""

    def __init__(self, by_query: dict[str, dict]) -> None:
        self._by_query = by_query
        self.queries: list[str] = []

    async def search(self, *, query: str) -> dict:
        self.queries.append(query)
        return self._by_query[query]

    async def fetch(self, *, url: str) -> dict:
        raise AssertionError("LexemeObserver's known-entity check never calls fetch()")


def rows_with_id(
    message_id: int, body: str, *, person_id: str | None = None
) -> list[TranscriptRow]:
    """One row batch, with a caller-chosen id — what the join-back tests
    need to control precisely."""
    return [
        TranscriptRow(
            id=message_id, sender_id="+84164353469", sender_name="Miles",
            ts=1_000, body=body, is_nora=False,
            delivery_status=DeliveryStatus.SENT, raw_json=None,
            person_id=person_id,
        )
    ]


async def test_a_batch_of_ordinary_cantonese_yields_no_candidates():
    """The extractor must not treat normal speech as novel. A model that
    returns something for every batch would fill the lexicon with 係/唔係
    and the block would be worthless."""
    observer = LexemeObserver(FakeModel(responses=['{"observations": []}']))
    result = await observer.observe(batch=rows("今日食咩好", "隨便啦"))
    assert result.candidates == ()
    assert result.parsed is True, "a well-formed, genuinely empty response DID parse"


async def test_malformed_json_yields_nothing_rather_than_raising():
    """R-5: silence beats a wrong answer. The loop must survive a model that
    returns prose, and the cycle then appends nothing rather than crashing —
    `observe` itself never raises. But "nothing appended" is not the whole
    story: `parsed=False` is the signal that distinguishes THIS case from a
    model that genuinely found nothing (see the sibling test above), which is
    what lets `LanguageConsolidator` refuse to treat the batch as read."""
    observer = LexemeObserver(FakeModel(responses=["sorry, I can't help with that"]))
    result = await observer.observe(batch=rows("好撚kam"))
    assert result.candidates == ()
    assert result.parsed is False


async def test_a_candidate_carries_its_own_message_and_speaker():
    """Provenance is not decoration — the example rendered under a gloss is
    what makes a wrong gloss visible, and it must point at a real row."""
    observer = LexemeObserver(FakeModel(responses=[json.dumps({"observations": [
        {"surface_form": "kam", "canonical_form": "kam", "sense": "尷",
         "part_of_speech": "adjective", "message_id": 7, "confidence": 0.9},
    ]})]))
    result = await observer.observe(batch=rows_with_id(7, "好撚kam", person_id="tyler"))
    assert result.parsed is True
    [c] = result.candidates
    assert (c.canonical_form, c.message_id, c.person_id) == ("kam", 7, "tyler")


async def test_a_candidate_naming_a_message_not_in_the_batch_is_dropped():
    """The model can hallucinate a message_id. An observation whose evidence
    is not in the batch has no evidence, and fabricated provenance is worse
    than a missing entry."""
    observer = LexemeObserver(FakeModel(responses=[json.dumps({"observations": [
        {"surface_form": "kam", "canonical_form": "kam", "sense": "尷",
         "part_of_speech": "adjective", "message_id": 999, "confidence": 0.9},
    ]})]))
    result = await observer.observe(batch=rows_with_id(7, "好撚kam"))
    assert result.candidates == ()
    assert result.parsed is True, "the response DID parse — the item was dropped, not the batch"


async def test_a_candidate_with_an_implausibly_long_field_is_dropped():
    """A crafted message can make the model emit an oversized `sense`/
    `canonical_form` — the store-side half of the defence against that
    (`nora.language.render`'s truncation is the render-side half): an
    over-long STRING field is a malformed item, dropped exactly like a
    missing key, so it never reaches `SqlLanguageStore.append_observations`
    at all. `canonical_form` is capped at 100 chars; 101 is the least input
    that must trip it."""
    observer = LexemeObserver(FakeModel(responses=[json.dumps({"observations": [
        {"surface_form": "kam", "canonical_form": "k" * 101, "sense": "尷",
         "part_of_speech": "adjective", "message_id": 7, "confidence": 0.9},
    ]})]))
    result = await observer.observe(batch=rows_with_id(7, "好撚kam"))
    assert result.candidates == ()
    assert result.parsed is True, "the response parsed fine — one item was rejected, not the batch"


async def test_an_empty_batch_makes_no_model_call():
    """Zero messages is not worth a completion — the sibling of the other
    three tests' `[...]`/prose responses, checking the OTHER short-circuit:
    `observe` must never even touch `model.complete` for an empty batch. A
    `FakeModel` with an EMPTY `responses` list makes this mechanically
    enforced — any call to `complete` raises `IndexError` (`list.pop` on
    empty), not just an assertion after the fact."""
    observer = LexemeObserver(FakeModel(responses=[]))
    result = await observer.observe(batch=[])
    assert result.candidates == ()
    assert result.parsed is True, "nothing to fail to parse — an empty batch is not a parse failure"


async def test_a_parse_failure_is_counted_distinctly_from_an_empty_extraction():
    """Task 6 carry-forward (folded into Task 11): a model that degrades into
    returning prose forever is INDISTINGUISHABLE from one correctly finding
    nothing unless something counts the parse failure itself.
    `nora_language_parse_failures_total` must increment on the prose response
    (the exact input `test_malformed_json_yields_nothing_rather_than_raising`
    above uses) and must NOT increment on a well-formed, genuinely empty
    `{"observations": []}` response — reverting the counter call must turn
    the first assertion red, and counting the empty case too would turn the
    second red."""
    metrics = Metrics()
    prose_observer = LexemeObserver(
        FakeModel(responses=["sorry, I can't help with that"]), metrics=metrics,
    )
    await prose_observer.observe(batch=rows("好撚kam"))
    assert metrics.get("nora_language_parse_failures_total") == 1

    empty_observer = LexemeObserver(
        FakeModel(responses=['{"observations": []}']), metrics=metrics,
    )
    await empty_observer.observe(batch=rows("好撚kam"))
    assert metrics.get("nora_language_parse_failures_total") == 1, (
        "a well-formed empty response must not be counted as a parse failure"
    )


async def test_a_dropped_candidate_is_counted_on_the_no_evidence_reason():
    """The drop in `test_a_candidate_naming_a_message_not_in_the_batch_is_dropped`
    must be OBSERVABLE, not just silent — `nora_language_candidates_dropped_total
    {reason="no_evidence"}` is the only trace a hallucinated message_id leaves.
    Reverting the counter call (but keeping the drop) must turn this red."""
    metrics = Metrics()
    observer = LexemeObserver(
        FakeModel(responses=[json.dumps({"observations": [
            {"surface_form": "kam", "canonical_form": "kam", "sense": "尷",
             "part_of_speech": "adjective", "message_id": 999, "confidence": 0.9},
        ]})]),
        metrics=metrics,
    )
    await observer.observe(batch=rows_with_id(7, "好撚kam"))
    assert metrics.get("nora_language_candidates_dropped_total", reason="no_evidence") == 1


def _one_candidate_response(surface_form: str = "big walk") -> str:
    return json.dumps({"observations": [
        {"surface_form": surface_form, "canonical_form": surface_form,
         "sense": "a group walking activity", "part_of_speech": "noun",
         "message_id": 7, "confidence": 0.9},
    ]})


_SEARCH_HIT = {
    "status": "ok",
    "results": [{"url": "https://example.com/big-walk", "content": "Big Walk is a survival game."}],
}


async def test_a_known_entity_is_dropped_after_verification():
    """The confirmed live false positive this change exists for: "Big Walk" is
    an actual game, not room-coined slang. When search finds it AND the
    verification call agrees, the candidate must not survive."""
    observer = LexemeObserver(
        FakeModel(responses=[
            _one_candidate_response(), json.dumps({"known_entity": True}),
        ]),
        search=FakeSearch(by_query={"big walk": _SEARCH_HIT}),
    )
    result = await observer.observe(batch=rows_with_id(7, "?? Big walk ??"))
    assert result.candidates == ()
    assert result.parsed is True, "verified away, not dropped as malformed"


async def test_a_room_coined_candidate_survives_a_negative_verification():
    """Search finding SOMETHING is not enough on its own — the classification
    call must independently agree it's a known public entity. A coincidental,
    irrelevant hit must not cost a genuine local coinage its place."""
    observer = LexemeObserver(
        FakeModel(responses=[
            _one_candidate_response("kam"), json.dumps({"known_entity": False}),
        ]),
        search=FakeSearch(by_query={"kam": _SEARCH_HIT}),
    )
    result = await observer.observe(batch=rows_with_id(7, "好撚kam"))
    [c] = result.candidates
    assert c.canonical_form == "kam"


async def test_search_unavailable_keeps_the_candidate_and_never_classifies():
    """R-5's fail-open direction: a Tavily outage must not silently suppress
    real evidence. `FakeModel` is given only ONE response — if the code
    wrongly still tried a second (classification) call, `list.pop` on the
    now-empty list would raise `IndexError`, catching the regression
    mechanically rather than by assertion alone."""
    observer = LexemeObserver(
        FakeModel(responses=[_one_candidate_response()]),
        search=FakeSearch(by_query={"big walk": {"status": "unavailable"}}),
    )
    result = await observer.observe(batch=rows_with_id(7, "?? Big walk ??"))
    [c] = result.candidates
    assert c.canonical_form == "big walk"


async def test_search_nothing_found_keeps_the_candidate_and_never_classifies():
    """No results is not evidence of a known public entity — the candidate is
    kept, and (same mechanical guard as the `unavailable` sibling test) no
    second model call is ever made."""
    observer = LexemeObserver(
        FakeModel(responses=[_one_candidate_response()]),
        search=FakeSearch(by_query={"big walk": {"status": "nothing_found"}}),
    )
    result = await observer.observe(batch=rows_with_id(7, "?? Big walk ??"))
    [c] = result.candidates
    assert c.canonical_form == "big walk"


async def test_an_unparseable_verification_response_keeps_the_candidate():
    """Fail-open applies to the verification call's OWN malformed response
    too, exactly like a search failure — a model degrading to prose here must
    not cost a real candidate its place."""
    observer = LexemeObserver(
        FakeModel(responses=[_one_candidate_response(), "sorry, I can't help with that"]),
        search=FakeSearch(by_query={"big walk": _SEARCH_HIT}),
    )
    result = await observer.observe(batch=rows_with_id(7, "?? Big walk ??"))
    [c] = result.candidates
    assert c.canonical_form == "big walk"


async def test_verification_is_off_when_no_search_collaborator_is_wired():
    """The default (`search=None`, every existing caller before this change)
    must reproduce EXACTLY today's behaviour — no known-entity check ever
    runs, matching `[language].verify_known_entities = false`'s shipped-inert
    default. A single-item `FakeModel` response list proves no second call
    happens."""
    observer = LexemeObserver(FakeModel(responses=[_one_candidate_response()]))
    result = await observer.observe(batch=rows_with_id(7, "?? Big walk ??"))
    [c] = result.candidates
    assert c.canonical_form == "big walk"


async def test_verification_respects_the_candidate_cap():
    """`verify_max_candidates` bounds the tail, not the common case: with the
    cap at 1 and two well-formed candidates, only the FIRST is searched —
    `FakeSearch.queries` proves it, and the second candidate is kept
    unverified rather than dropped or erroring."""
    two_candidates = json.dumps({"observations": [
        {"surface_form": "big walk", "canonical_form": "big walk", "sense": "a game",
         "part_of_speech": "noun", "message_id": 7, "confidence": 0.9},
        {"surface_form": "kam", "canonical_form": "kam", "sense": "尷",
         "part_of_speech": "adjective", "message_id": 8, "confidence": 0.9},
    ]})
    search = FakeSearch(by_query={"big walk": _SEARCH_HIT})
    observer = LexemeObserver(
        FakeModel(responses=[two_candidates, json.dumps({"known_entity": False})]),
        search=search, verify_max_candidates=1,
    )
    batch = rows_with_id(7, "?? Big walk ??") + rows_with_id(8, "好撚kam")
    result = await observer.observe(batch=batch)
    assert search.queries == ["big walk"], "only the first (capped) candidate was searched"
    assert {c.canonical_form for c in result.candidates} == {"big walk", "kam"}


async def test_the_search_outcome_is_counted_by_status():
    """`nora_language_verification_total` is the search step's OWN liveness
    signal, independent of whether a drop happened — without it an operator
    cannot tell "verification runs and finds nothing to drop" from
    "verification never runs at all."""
    metrics = Metrics()
    observer = LexemeObserver(
        FakeModel(responses=[_one_candidate_response(), json.dumps({"known_entity": True})]),
        metrics=metrics, search=FakeSearch(by_query={"big walk": _SEARCH_HIT}),
    )
    await observer.observe(batch=rows_with_id(7, "?? Big walk ??"))
    assert metrics.get("nora_language_verification_total", status="ok") == 1
    assert metrics.get("nora_language_candidates_dropped_total", reason="known_entity") == 1


# ── instruction provenance (2026-08-20 instruction-substrate spec §4.3) ──────
#
# The wrong row this column exists for was written by THIS observer: `big walk`
# glossed as 「群內約定俗成嘅散步/行路活動」, a nighttime stroll, for a co-op puzzle
# video game — and nothing recorded which instruction text asked for it, so no
# later edit could be shown to have fixed it. These tests are the writer half
# of the guarantee; `tests/infra/test_language_store.py`'s own provenance block
# is the row half.

async def test_every_candidate_carries_the_instruction_that_produced_it():
    observer = LexemeObserver(FakeModel(responses=[_one_candidate_response()]))
    result = await observer.observe(batch=rows_with_id(7, "?? Big walk ??"))
    [c] = result.candidates
    assert c.instruction_sha256 == observer.instruction_sha256
    assert len(c.instruction_sha256) == 64, "a real digest, not a placeholder"


async def test_the_stamp_is_the_digest_of_the_instruction_actually_sent():
    """Bound to the real property, not a proxy: the digest is over
    `_PROMPT_PREAMBLE` itself, so a row's stamp is reproducible by hand from
    the constant with `sha256sum`. An observer that hashed a version number,
    or the assembled per-batch prompt (which varies with the transcript and
    would give every batch its own digest), would pass a weaker assertion and
    fail this one."""
    observer = LexemeObserver(FakeModel(responses=['{"observations": []}']))
    assert observer.instruction_sha256 == instruction_sha256(observer_module._PROMPT_PREAMBLE)


async def test_editing_the_instruction_text_changes_every_row_it_writes(monkeypatch):
    """The whole point of the column: two corpora written either side of an
    instruction edit must be distinguishable. Patching the constant is the
    only way to simulate the edit in-process — production changes it by
    editing the file."""
    before = LexemeObserver(FakeModel(responses=[_one_candidate_response()]))
    result_before = await before.observe(batch=rows_with_id(7, "?? Big walk ??"))
    monkeypatch.setattr(
        observer_module, "_PROMPT_PREAMBLE",
        observer_module._PROMPT_PREAMBLE + "\nAlso: a name with a public meaning is NOT slang.\n",
    )
    after = LexemeObserver(FakeModel(responses=[_one_candidate_response()]))
    result_after = await after.observe(batch=rows_with_id(7, "?? Big walk ??"))

    assert before.instruction_sha256 != after.instruction_sha256
    [c_before] = result_before.candidates
    [c_after] = result_after.candidates
    assert c_before.instruction_sha256 != c_after.instruction_sha256


async def test_wiring_the_known_entity_check_changes_the_stamp():
    """The verification instruction decides whether a candidate becomes a
    row, so two deployments differing only in `verify_known_entities` write
    different POPULATIONS. A stamp that could not tell them apart would be
    bound to half its input — §4.3's own reason for rejecting
    `objective_sha256`."""
    unverified = LexemeObserver(FakeModel(responses=[]))
    verified = LexemeObserver(
        FakeModel(responses=[]), search=FakeSearch(by_query={}),
    )
    assert unverified.instruction_sha256 != verified.instruction_sha256
    assert verified.instruction_sha256 == instruction_sha256(
        observer_module._PROMPT_PREAMBLE, observer_module._VERIFICATION_PROMPT_TEMPLATE,
    )

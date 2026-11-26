"""The in-memory records collaborator's receipt custody."""
from __future__ import annotations

from datetime import UTC, datetime

from nora.domain.memory_graph import Admission, Evidence, Relation
from nora.testing.comprehend import InMemoryComprehendRecords
from tests.unit.test_comprehend import ITEM, PERSON, PLACE, SOURCE, UNIT, WORK


async def test_retained_page_is_exact_read_text_with_hash_and_tool_call():
    records = InMemoryComprehendRecords()
    text = "Generated public venue page"
    call = {
        "name": "web_fetch",
        "toolUseId": "generated-call",
        "input": {"url": "https://example.invalid"},
    }
    key = await records.web_page(
        unit=UNIT,
        session_id=1,
        url="https://example.invalid",
        fetched_at=datetime.now(UTC),
        text=text,
        tool_call=call,
    )
    assert key[0] == "web_page"
    page = records.pages[0]
    import hashlib

    assert page["text"] == text and page["tool_call"] == call
    assert page["content_hash"] == hashlib.sha256(text.encode()).hexdigest()


async def test_graph_receipt_retains_the_relation_and_its_evidence():
    records = InMemoryComprehendRecords()
    relation = Relation(
        "generated-relation", PERSON.key, "likes", PLACE.key, "notebook", UNIT,
        asserted_at=WORK.as_of,
    )
    admission = Admission(
        relations=(relation,), evidence=(Evidence(SOURCE.key, relation_id=relation.id),)
    )
    await records.graph_write(WORK, 1, admission)
    assert records.graphs == [(WORK, 1, admission)]


async def test_a_retry_persists_additional_distinct_claims():
    """A partial completion retry keeps both distinct claims for the same work."""
    records = InMemoryComprehendRecords()
    first = [{"what": "Generated first claim", "source_ids": [1]}]
    second = [{"what": "Generated second claim", "source_ids": [1]}]
    await records.readings(WORK, 1, ITEM, first)
    stored = await records.readings(WORK, 2, ITEM, second)
    assert len(records.claims[(WORK.id, WORK.input_hash)]) == 2
    assert first[0] in stored and second[0] in stored


async def test_a_quiet_session_has_one_completion_receipt_independent_of_readings():
    records = InMemoryComprehendRecords()
    await records.complete_session(
        "generated-unit", 77, {"1": "digest-one", "2": "digest-two"},
    )
    payloads = [r["payload"] for r in records.receipts if r["part"] == "session_complete"]
    assert payloads == [{
        "unit": "generated-unit", "inputs": {"1": "digest-one", "2": "digest-two"},
    }]
    assert records.claims == {}

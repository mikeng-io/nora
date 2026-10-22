"""R-100 on the deferred-job delivery path: a finished job's documents keep their links.

The delivery is a fire turn with the documents tools wired, so the link carrier is armed and a
document link the turn was not handed is removed from what she sends. Code therefore reads each
`document_id` the result named back through the document store, and seeds the turn's carrier with
the ones that read back; one that does not is named missing in the body and is never linked.
Everything below goes through the real poller, `FirePath.speak`, the real tool surface and the
real `DocumentService`; only the model's reply is scripted.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from nora.agent.registry import ToolRegistry
from nora.app.compute_poller import ComputeJobPoller
from nora.domain.compute import ComputeJob, ComputeJobState
from nora.domain.models import PersonRef
from nora.domain.ports import JobResult, JobStatus
from nora.engagement.slots import ConversationSlots
from nora.obs.metrics import Metrics
from nora.persons import Persons
from nora.policy import permissive_policy
from nora.testing import FakeAgentRunner, FakeComputeBackend, FakeComputeJobStore, ScriptedTurn
from tests.unit.test_document_links_are_minted import PUBLIC, _service
from tests.unit.test_document_tools import _doc
from tests.unit.test_fire_path import _fire_path, _FireAdapter

MILES = PersonRef(person_id="miles", name="Miles", honcho_peer_id="miles")
ROOM = "conv-1"
NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)


@pytest.fixture
def persons() -> Persons:
    return Persons(by_person={"miles": MILES}, index={("signal", "+85212345678"): "miles"})


def _job() -> ComputeJob:
    return ComputeJob(
        id=uuid.uuid4(), backend_job_id="job_1", task="turn the archive into documents",
        artifact_id=None, platform="signal", conversation_id=ROOM,
        conversation_type="group", requester_person_id="miles", created_at=NOW,
        state=ComputeJobState.PENDING, completed_at=None, result_url=None,
        result_summary=None, error=None, task_sha256="x", context_sha256="y",
        kind="subagent",
    )


async def _deliver(doctrine_store, persons, reply, document_ids, service):
    """Run one finished job through the poller and a real fire turn; return what was sent."""
    adapter = _FireAdapter()
    path = _fire_path(
        doctrine_store=doctrine_store, persons=persons, metrics=Metrics(), adapter=adapter,
        registry=ToolRegistry(documents=service),
        runner=FakeAgentRunner([
            ScriptedTurn(tool_calls=[("send_reply", {"text": reply})], final_text=reply),
        ]),
    )
    store = FakeComputeJobStore()
    job = _job()
    await store.create(job)
    backend = FakeComputeBackend()
    backend.set_result(job_id="job_1", result=JobResult(
        JobStatus.SUCCEEDED, report="documents made", document_ids=document_ids,
    ))
    poller = ComputeJobPoller(
        policy=permissive_policy(),
        store=store, backends={"subagent": backend}, speak=path.speak,
        slots=ConversationSlots(maxlen=16, spawn=lambda c: None), metrics=Metrics(),
        tick_seconds=1.0, clock=lambda: NOW, documents=service,
    )
    await poller.tick()
    assert (await store.get(job.id)).state is ComputeJobState.DONE
    return adapter.texts


async def _published(service) -> tuple[str, str]:
    out = await service.publish(
        platform="signal", conversation_id=ROOM, person_id="miles", raw=_doc(),
        written_by="subagent",
    )
    assert out.status == "ok", out
    return str(out.document_id), out.url


async def test_a_delivered_documents_link_reaches_the_sent_text(doctrine_store, persons):
    service = _service()
    document_id, url = await _published(service)
    assert url.startswith(PUBLIC + "/documents/")

    texts = await _deliver(
        doctrine_store, persons, f"Done, it is here: {url}", (document_id,), service,
    )

    assert texts and url in texts[0]


async def test_a_document_that_does_not_read_back_is_named_missing_and_never_linked(
    doctrine_store, persons,
):
    service = _service()
    document_id, url = await _published(service)
    ghost = uuid.uuid4()
    made_up = f"{PUBLIC}/documents/{ghost.hex[:12]}"

    texts = await _deliver(
        doctrine_store, persons, f"Both made: {url} and {made_up}",
        (document_id, str(ghost)), service,
    )

    assert url in texts[0]                      # positive control: the real one survives
    assert made_up not in texts[0]              # the one that never read back has no link
    assert "documents/" + ghost.hex[:12] not in texts[0]

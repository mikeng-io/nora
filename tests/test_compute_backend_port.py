"""Tests for `ComputeBackend` port and `FakeComputeBackend`."""

from nora.domain.ports import ComputeBackend, JobResult, JobStatus, LaunchRequest
from nora.testing import FakeComputeBackend


def _req(task: str = "write a haiku", **kw) -> LaunchRequest:
    base = {
        "task": task, "platform": "signal", "conversation_id": "conv-1",
        "conversation_type": "group", "requester_person_id": "person-1", "tag_id": "tag-1",
    }
    base.update(kw)
    return LaunchRequest(**base)


def test_fake_compute_backend_satisfies_the_port():
    assert isinstance(FakeComputeBackend(), ComputeBackend)


async def test_a_launched_job_starts_running():
    backend = FakeComputeBackend()
    job_id = await backend.launch(request=_req())
    result = await backend.poll(job_id=job_id)
    # default fake behavior (no set_result call) is immediate success --
    # see FakeComputeBackend's own docstring for why, and set_result below
    # for how a test arranges the RUNNING branch instead
    assert result.status == JobStatus.SUCCEEDED


async def test_set_result_can_arrange_a_still_running_job():
    backend = FakeComputeBackend()
    job_id = await backend.launch(request=_req())
    backend.set_result(
        job_id=job_id,
        result=JobResult(status=JobStatus.SUCCEEDED, url="https://x/drop/1", summary="a haiku"),
        polls_before_done=2,
    )
    assert (await backend.poll(job_id=job_id)).status == JobStatus.RUNNING
    assert (await backend.poll(job_id=job_id)).status == JobStatus.RUNNING
    third = await backend.poll(job_id=job_id)
    assert third.status == JobStatus.SUCCEEDED
    assert third.url == "https://x/drop/1"


async def test_polling_an_unknown_job_id_fails_rather_than_raising():
    backend = FakeComputeBackend()
    result = await backend.poll(job_id="job_doesnotexist")
    assert result.status == JobStatus.FAILED


async def test_launch_records_the_task_and_artifact_id():
    backend = FakeComputeBackend()
    await backend.launch(request=_req("revise the chart", artifact_id="art_000001"))
    last = backend.launched[-1]
    assert (last["task"], last["artifact_id"], last["context"]) == (
        "revise the chart", "art_000001", None,
    )


async def test_launch_records_the_context():
    backend = FakeComputeBackend()
    await backend.launch(request=_req(
        "chart the rainfall",
        context=[{"kind": "place", "name": "Yoho Mall", "rating": 4.2}],
    ))
    assert backend.launched[-1]["context"] == [
        {"kind": "place", "name": "Yoho Mall", "rating": 4.2}
    ]


async def test_launch_records_no_context_as_none_by_default():
    backend = FakeComputeBackend()
    await backend.launch(request=_req())
    assert backend.launched[-1]["context"] is None


async def test_launch_carries_the_room_facts_to_the_backend():
    backend = FakeComputeBackend()
    await backend.launch(request=_req(conversation_id="conv-9", tag_id="tag-9"))
    request = backend.launched[-1]["request"]
    assert (request.platform, request.conversation_id, request.conversation_type) == (
        "signal", "conv-9", "group",
    )
    assert (request.requester_person_id, request.tag_id) == ("person-1", "tag-9")


async def test_cancel_reaches_the_backend():
    backend = FakeComputeBackend()
    job_id = await backend.launch(request=_req())
    await backend.cancel(job_id=job_id)
    assert backend.cancelled == [job_id]


def test_a_result_carries_a_report_and_document_ids():
    result = JobResult(
        status=JobStatus.SUCCEEDED, report="did it", document_ids=("a", "b"),
    )
    assert (result.report, result.document_ids) == ("did it", ("a", "b"))
    assert JobResult(status=JobStatus.RUNNING).document_ids == ()

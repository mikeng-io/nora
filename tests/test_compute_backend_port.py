"""Tests for `ComputeBackend` port and `FakeComputeBackend`."""

from nora.domain.ports import ComputeBackend, JobResult, JobStatus
from nora.testing import FakeComputeBackend


def test_fake_compute_backend_satisfies_the_port():
    assert isinstance(FakeComputeBackend(), ComputeBackend)


async def test_a_launched_job_starts_running():
    backend = FakeComputeBackend()
    job_id = await backend.launch(task="write a haiku")
    result = await backend.poll(job_id=job_id)
    # default fake behavior (no set_result call) is immediate success --
    # see FakeComputeBackend's own docstring for why, and set_result below
    # for how a test arranges the RUNNING branch instead
    assert result.status == JobStatus.SUCCEEDED


async def test_set_result_can_arrange_a_still_running_job():
    backend = FakeComputeBackend()
    job_id = await backend.launch(task="write a haiku")
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
    await backend.launch(task="revise the chart", artifact_id="art_000001")
    assert backend.launched[-1] == {
        "task": "revise the chart", "artifact_id": "art_000001",
        "job_id": backend.launched[-1]["job_id"],
    }

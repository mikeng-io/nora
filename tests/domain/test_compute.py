from datetime import UTC, datetime
from uuid import uuid4

import pytest

from nora.domain.compute import (
    ComputeJob,
    ComputeJobState,
    NaiveComputeJobDatetimeError,
    ensure_utc_job,
)


def test_a_naive_datetime_is_refused():
    with pytest.raises(NaiveComputeJobDatetimeError):
        ensure_utc_job(datetime(2026, 1, 1), field="created_at")  # noqa: DTZ001


def test_an_aware_datetime_normalises_to_utc():
    aware = datetime(2026, 1, 1, 12, tzinfo=UTC)
    assert ensure_utc_job(aware, field="created_at") == aware


def test_a_compute_job_constructs_with_pending_state():
    job = ComputeJob(
        id=uuid4(), backend_job_id="job_abc123", task="write a haiku",
        artifact_id=None, platform="signal", conversation_id="conv-1",
        conversation_type="group", requester_person_id="person-1",
        created_at=datetime.now(UTC), state=ComputeJobState.PENDING,
        completed_at=None, result_url=None, result_summary=None, error=None,
    )
    assert job.state is ComputeJobState.PENDING

from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest

from nora.domain.compute import (
    ComputeJob,
    ComputeJobState,
    NaiveComputeJobDatetimeError,
    ensure_utc_job,
    normalise_compute_job,
)


def test_a_naive_datetime_is_refused():
    with pytest.raises(NaiveComputeJobDatetimeError):
        ensure_utc_job(datetime(2026, 1, 1), field="created_at")


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
        task_sha256="hash-task", context_sha256="hash-context",
    )
    assert job.state is ComputeJobState.PENDING


def test_a_compute_job_carries_its_own_provenance_hashes():
    job = ComputeJob(
        id=uuid4(), backend_job_id="job_abc123", task="write a haiku",
        artifact_id=None, platform="signal", conversation_id="conv-1",
        conversation_type="group", requester_person_id="person-1",
        created_at=datetime.now(UTC), state=ComputeJobState.PENDING,
        completed_at=None, result_url=None, result_summary=None, error=None,
        task_sha256="a" * 64, context_sha256="b" * 64,
    )
    assert job.task_sha256 == "a" * 64
    assert job.context_sha256 == "b" * 64


def test_normalise_compute_job_with_non_utc_created_at_and_none_completed_at():
    """Verify normalise_compute_job converts non-UTC created_at to UTC.

    completed_at stays None.
    """
    # Create a datetime with +05:00 offset
    tz_offset = timezone(timedelta(hours=5))
    non_utc_created = datetime(2026, 1, 1, 12, 0, 0, tzinfo=tz_offset)

    job = ComputeJob(
        id=uuid4(), backend_job_id="job_xyz789", task="test task",
        artifact_id=None, platform="signal", conversation_id="conv-2",
        conversation_type="direct", requester_person_id="person-2",
        created_at=non_utc_created, state=ComputeJobState.PENDING,
        completed_at=None, result_url=None, result_summary=None, error=None,
        task_sha256="hash-task", context_sha256="hash-context",
    )

    normalized = normalise_compute_job(job)

    # Expected UTC equivalent: 12:00 +05:00 = 07:00 UTC
    expected_utc = datetime(2026, 1, 1, 7, 0, 0, tzinfo=UTC)
    assert normalized.created_at == expected_utc
    assert normalized.completed_at is None


def test_normalise_compute_job_with_both_non_utc_datetimes():
    """Verify normalise_compute_job converts both created_at and completed_at to UTC."""
    # Create datetimes with +05:00 offset
    tz_offset = timezone(timedelta(hours=5))
    non_utc_created = datetime(2026, 1, 1, 12, 0, 0, tzinfo=tz_offset)
    non_utc_completed = datetime(2026, 1, 1, 14, 30, 0, tzinfo=tz_offset)

    job = ComputeJob(
        id=uuid4(), backend_job_id="job_def456", task="another task",
        artifact_id=None, platform="signal", conversation_id="conv-3",
        conversation_type="direct", requester_person_id="person-3",
        created_at=non_utc_created, state=ComputeJobState.DONE,
        completed_at=non_utc_completed, result_url="https://example.com",
        result_summary="test", error=None,
        task_sha256="hash-task", context_sha256="hash-context",
    )

    normalized = normalise_compute_job(job)

    # Expected UTC equivalents: 12:00 +05:00 = 07:00 UTC, 14:30 +05:00 = 09:30 UTC
    expected_created_utc = datetime(2026, 1, 1, 7, 0, 0, tzinfo=UTC)
    expected_completed_utc = datetime(2026, 1, 1, 9, 30, 0, tzinfo=UTC)
    assert normalized.created_at == expected_created_utc
    assert normalized.completed_at == expected_completed_utc

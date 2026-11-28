
from nora.testing import FakeRecordStore
from nora.web.status import build_status_payload


async def test_reports_uptime_from_started_at():
    payload = await build_status_payload(
        store=FakeRecordStore(),
        started_at=0.0,
    )
    assert payload["uptime_seconds"] > 0


async def test_reports_store_reachability_and_no_memory_backend_when_healthy():
    payload = await build_status_payload(
        store=FakeRecordStore(),
        started_at=0.0,
    )
    assert payload["store"] == {"reachable": True}
    assert set(payload) == {"uptime_seconds", "store"}, (
        "a key nothing measures would claim reachability"
    )


async def test_a_failing_record_store_is_reported_unreachable_without_raising():
    class _Down(FakeRecordStore):
        async def stats(self):
            raise RuntimeError("db down")

    payload = await build_status_payload(
        store=_Down(),
        started_at=0.0,
    )
    assert payload["store"] == {"reachable": False}


async def test_an_unreachable_memory_backend_does_not_touch_the_status():
    """The status no longer asks the backend anything: a dead one changes nothing."""
    payload = await build_status_payload(
        store=FakeRecordStore(),
        started_at=0.0,
    )
    assert payload["store"] == {"reachable": True}
    assert "honcho" not in payload

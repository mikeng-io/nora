"""`video_create` (design/video-generation): the MiniMax CN adapter, the daily cap, the tool's
gates, boot recovery for a durable kind, and the link-delivered clip.

The adapter runs against `httpx.MockTransport` answering the way the provider does, including
its two wrong-call shapes: a refusal at create, and a task id followed by a failure that only
the POLL reports (`task.error`, measured 2026-10-02). No test reaches the network.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from nora.agent.guards import ReplyGuard
from nora.agent.links import LinkCarry
from nora.agent.registry import ToolRegistry
from nora.agent.video_desk import VideoDesk
from nora.app.compute_poller import ComputeJobPoller, recover
from nora.app.fire import SENT, compute_occasion
from nora.domain.compute import ComputeJob, ComputeJobState
from nora.domain.models import PersonRef
from nora.domain.ports import JobResult, JobStatus
from nora.domain.subagent import SUBAGENT_KIND
from nora.domain.video import VIDEO_KIND, VideoReference, VideoRequest, VideoStart
from nora.engagement.slots import ConversationSlots
from nora.infra.video_gen import MAX_WAIT_SECONDS, MiniMaxVideoBackend
from nora.obs.logging import bind_tag_id
from nora.obs.metrics import Metrics
from nora.persons import Persons
from nora.testing import (
    FakeAgentRunner,
    FakeBlobStore,
    FakeComputeBackend,
    FakeComputeJobStore,
    ScriptedTurn,
)
from tests.unit.test_fire_path import _fire_path, _FireAdapter
from tests.unit.test_subagent_tools import _room
from tests.unit.test_subagent_worker import MILES, PLATFORM, ROOM, _Persons

HKT = "Asia/Hong_Kong"
NOW = datetime(2026, 10, 9, 4, 0, tzinfo=UTC)        # 12:00 HKT
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
CLIP = (
    "https://algeng-video-infer.oss-cn-shanghai.aliyuncs.com/output/clip_8f2a.mp4"
    "?Expires=1791000000&OSSAccessKeyId=LTAI5tExampleKey&Signature=abc%2Fdef%3D"
)


# ---- the provider, as a mock transport ---------------------------------------------------


class _Provider:
    """MiniMax's two endpoints. `create` is the answer to the POST; `polls` the successive
    answers to GET, the last one repeating."""

    def __init__(self, *, create: httpx.Response | Exception | None = None,
                 polls: list[httpx.Response | Exception] | None = None) -> None:
        self.create = create if create is not None else httpx.Response(
            200, json={"task_id": "T123", "base_resp": {"status_code": 0, "status_msg": "ok"}})
        self.polls = polls or [_task("queued")]
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.method == "POST":
            assert request.url.path == "/v2/video_generation"
            answer: Any = self.create
        else:
            assert request.url.path == "/v2/query/video_generation/T123"
            answer = self.polls.pop(0) if len(self.polls) > 1 else self.polls[0]
        if isinstance(answer, Exception):
            raise answer
        return answer

    def body(self) -> dict[str, Any]:
        post = next(r for r in self.requests if r.method == "POST")
        return json.loads(post.content)


def _task(status: str, **extra: Any) -> httpx.Response:
    return httpx.Response(200, json={"task": {"id": "T123", "status": status, **extra}})


class _Clock:
    def __init__(self, t: float = 1_790_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def _backend(provider: _Provider, clock: _Clock | None = None,
             metrics: Metrics | None = None) -> MiniMaxVideoBackend:
    return MiniMaxVideoBackend(
        api_key="k", model="MiniMax-H3", resolution="480P", duration_seconds=15, ratio="3:4",
        metrics=metrics or Metrics(), transport=httpx.MockTransport(provider.handler),
        clock=clock or _Clock(),
    )


# ---- the adapter -------------------------------------------------------------------------


async def test_create_sends_the_fixed_settings_and_each_reference_as_a_data_uri():
    provider = _Provider()
    out = await _backend(provider).start(VideoRequest(
        prompt="a mascot waves", references=(VideoReference("a" * 64, PNG),)))
    assert out.status == "started"
    assert out.job_id == "T123@1790000000"
    assert out.params == {"model": "MiniMax-H3", "resolution": "480P", "duration": 15,
                          "ratio": "3:4"}
    body = provider.body()
    assert (body["model"], body["resolution"], body["duration"], body["ratio"]) == (
        "MiniMax-H3", "480P", 15, "3:4")
    text, image = body["content"]
    assert text == {"type": "text", "text": "a mascot waves"}
    assert image["role"] == "reference_image"
    assert image["image_url"]["url"].startswith("data:image/png;base64,")
    assert provider.requests[0].headers["Authorization"] == "Bearer k"


@pytest.mark.parametrize("answer", [
    httpx.Response(500, text="upstream down"),
    httpx.Response(200, json={"base_resp": {"status_code": 2013, "status_msg": "invalid params"}}),
])
async def test_a_refused_create_starts_nothing(answer):
    metrics = Metrics()
    out = await _backend(_Provider(create=answer), metrics=metrics).start(VideoRequest("x"))
    assert out.status == "refused" and out.job_id == ""
    assert metrics.get("nora_video_jobs_total", outcome="refused") == 1


async def test_an_unreachable_provider_at_create_is_unavailable():
    provider = _Provider(create=httpx.ConnectError("refused"))
    out = await _backend(provider).start(VideoRequest("x"))
    assert out.status == "unavailable"


async def test_an_accepted_create_with_no_task_id_is_uncertain():
    provider = _Provider(create=httpx.Response(200, json={"base_resp": {"status_code": 0}}))
    out = await _backend(provider).start(VideoRequest("x"))
    assert (out.status, out.uncertain) == ("unavailable", True)


async def test_a_reference_that_is_not_an_image_is_refused_before_any_call():
    provider = _Provider()
    out = await _backend(provider).start(
        VideoRequest("x", references=(VideoReference("b" * 64, b"not an image"),)))
    assert out.status == "refused"
    assert provider.requests == []


async def test_poll_reads_queued_then_running_then_the_succeeded_clip():
    provider = _Provider(polls=[
        _task("queued"), _task("running"),
        _task("succeeded", content={"url": CLIP}, usage={"output_seconds": 15}),
    ])
    backend = _backend(provider)
    assert (await backend.poll(job_id="T123@1790000000")).status is JobStatus.RUNNING
    assert (await backend.poll(job_id="T123@1790000000")).status is JobStatus.RUNNING
    done = await backend.poll(job_id="T123@1790000000")
    assert done.status is JobStatus.SUCCEEDED
    assert done.url == CLIP
    assert "24 hours" in (done.summary or "")


async def test_a_failure_only_the_poll_reports_is_read_from_task_error_and_logged(caplog):
    """Create returned a task id; the refusal arrives seconds later in the poll (2026-10-02)."""
    provider = _Provider(polls=[_task("failed", error={
        "code": 2013, "message": 'aspect ratio "adaptive" is not supported for task ref2va'})])
    with caplog.at_level(logging.WARNING, logger="nora.video_gen"):
        out = await _backend(provider).poll(job_id="T123@1790000000")
    assert out.status is JobStatus.FAILED and out.failure_reason == "failed"
    assert "adaptive" in (out.error or "")
    assert any("code=2013" in r.getMessage() for r in caplog.records)


async def test_a_cancelled_task_is_failed_as_cancelled():
    out = await _backend(_Provider(polls=[_task("cancelled")])).poll(job_id="T123@1790000000")
    assert (out.status, out.failure_reason) == (JobStatus.FAILED, "cancelled")


async def test_a_task_still_running_past_the_wait_is_a_timeout():
    clock = _Clock()
    backend = _backend(_Provider(polls=[_task("running")]), clock=clock)
    assert (await backend.poll(job_id="T123@1790000000")).status is JobStatus.RUNNING
    clock.t += MAX_WAIT_SECONDS + 1
    out = await backend.poll(job_id="T123@1790000000")
    assert (out.status, out.failure_reason) == (JobStatus.FAILED, "timeout")


async def test_a_transient_poll_failure_waits_for_the_next_tick():
    provider = _Provider(polls=[httpx.ConnectError("blip"), _task("running")])
    out = await _backend(provider).poll(job_id="T123@1790000000")
    assert out.status is JobStatus.RUNNING


async def test_a_task_the_provider_no_longer_knows_has_expired():
    out = await _backend(_Provider(polls=[httpx.Response(404)])).poll(job_id="T123@1790000000")
    assert (out.status, out.failure_reason) == (JobStatus.FAILED, "expired")


async def test_succeeded_without_a_link_is_a_failure_not_a_blank_delivery():
    out = await _backend(_Provider(polls=[_task("succeeded", content={})])).poll(
        job_id="T123@1790000000")
    assert (out.status, out.failure_reason) == (JobStatus.FAILED, "no_url")


# ---- the desk: the daily cap ---------------------------------------------------------------


class _Starter:
    def __init__(self, status: str = "started") -> None:
        self.status = status
        self.calls: list[VideoRequest] = []

    async def start(self, request: VideoRequest) -> VideoStart:
        self.calls.append(request)
        if self.status != "started":
            return VideoStart(self.status, reason="no")  # type: ignore[arg-type]
        return VideoStart("started", job_id=f"T{len(self.calls)}@1", params={"ratio": "3:4"})


def _desk(store: FakeComputeJobStore, starter: _Starter, *, cap: int = 1,
          now: datetime = NOW) -> VideoDesk:
    return VideoDesk(
        jobs=store, backend=starter, clock=lambda: now, tz=HKT, max_per_room_per_day=cap)


OTHER_ROOM = "another-room"


async def _make(desk: VideoDesk, tag: str = "t1", room: str = ROOM) -> Any:
    return await desk.create(
        platform=PLATFORM, conversation_id=room, conversation_type="group",
        requester_person_id="miles", tag_id=tag, request=VideoRequest("a wave"))


async def test_the_cap_is_read_from_the_table_so_a_restart_does_not_reset_it():
    store = FakeComputeJobStore()
    starter = _Starter()
    first = await _make(_desk(store, starter))
    assert first.status == "started"
    row = await store.get(uuid.UUID(first.id))
    assert row is not None and row.kind == VIDEO_KIND
    assert dict(row.payload) == {"prompt": "a wave", "reference_sha256": [], "ratio": "3:4"}

    restarted = _desk(store, starter)                 # a new process: nothing in memory
    second = await _make(restarted, tag="t2")
    assert (second.status, second.used, second.cap) == ("at_cap", 1, 1)
    assert len(starter.calls) == 1                    # refused BEFORE the provider


async def test_each_room_has_its_own_cap():
    """Operator 2026-10-09: the cap is per room. A second room starts its own clip while the
    first is at its limit, and a third clip in either is refused; the refusal is that room's."""
    store = FakeComputeJobStore()
    starter = _Starter()
    desk = _desk(store, starter, cap=2)
    assert [(await _make(desk, tag=f"a{i}")).status for i in range(2)] == ["started"] * 2
    refused = await _make(desk, tag="a3")
    assert (refused.status, refused.used, refused.cap) == ("at_cap", 2, 2)
    assert len(starter.calls) == 2                    # refused BEFORE the provider
    assert [(await _make(desk, tag=f"b{i}", room=OTHER_ROOM)).status for i in range(2)] == [
        "started"] * 2
    assert (await _make(desk, tag="b3", room=OTHER_ROOM)).status == "at_cap"
    assert len(starter.calls) == 4


async def test_another_rooms_clips_do_not_use_this_rooms_cap():
    store = FakeComputeJobStore()
    starter = _Starter()
    desk = _desk(store, starter, cap=1)
    await _make(desk, tag="a1")
    assert await desk.used_today(platform=PLATFORM, conversation_id=ROOM) == 1
    assert await desk.used_today(platform=PLATFORM, conversation_id=OTHER_ROOM) == 0
    assert await desk.used_today(platform="discord-elsewhere", conversation_id=ROOM) == 0
    other = await _make(desk, tag="b1", room=OTHER_ROOM)
    assert (other.status, other.used, other.cap) == ("started", 1, 1)


async def test_a_clip_the_provider_failed_in_one_room_frees_that_room_only():
    store = FakeComputeJobStore()
    starter = _Starter()
    desk = _desk(store, starter, cap=1)
    await _make(desk, tag="a1")
    await _make(desk, tag="b1", room=OTHER_ROOM)
    await _deliver_through_the_poller(store, JobResult(
        JobStatus.FAILED, error="could not make it", failure_reason="failed"),
        only_room=ROOM)
    assert (await _make(desk, tag="a2")).status == "started"
    assert (await _make(desk, tag="b2", room=OTHER_ROOM)).status == "at_cap"


async def test_the_cap_resets_at_local_midnight_not_utc():
    store = FakeComputeJobStore()
    starter = _Starter()
    late = datetime(2026, 10, 9, 15, 30, tzinfo=UTC)  # 23:30 HKT
    assert (await _make(_desk(store, starter, now=late))).status == "started"
    next_day = late + timedelta(hours=1)              # 00:30 HKT the next day, still 9 Oct UTC
    assert (await _make(_desk(store, starter, now=next_day), tag="t2")).status == "started"


async def _deliver_through_the_poller(
    store: FakeComputeJobStore, result: JobResult, *, only_room: str | None = None,
) -> None:
    """The real poller, with a fire path that says it SENT: the row ends `done` even when the
    result it told the room about was a failure. `only_room` leaves every other room's clip
    still rendering."""
    backend = FakeComputeBackend()
    for row in await store.pending(limit=10):
        if only_room is None or row.conversation_id == only_room:
            backend.set_result(job_id=row.backend_job_id, result=result)
        else:
            backend.set_result(
                job_id=row.backend_job_id, result=JobResult(JobStatus.RUNNING),
                polls_before_done=99)

    async def speak(event: Any) -> str:
        return SENT

    poller = ComputeJobPoller(
        store=store, backends={VIDEO_KIND: backend}, speak=speak,
        slots=ConversationSlots(maxlen=16, spawn=lambda c: None), metrics=Metrics(),
        tick_seconds=1.0, clock=lambda: NOW,
    )
    await poller.tick()


async def test_a_clip_the_provider_failed_does_not_count_once_its_failure_is_delivered():
    """Review 2026-10-09: the poller marks a delivered failure `done`, so the cap cannot read
    the row's state; it reads `failure_reason`."""
    store = FakeComputeJobStore()
    starter = _Starter()
    desk = _desk(store, starter)
    first = await _make(desk)
    await _deliver_through_the_poller(store, JobResult(
        JobStatus.FAILED, error="could not make it", failure_reason="failed"))
    row = await store.get(uuid.UUID(first.id))
    assert row.state is ComputeJobState.DONE and row.failure_reason == "failed"
    assert (await _make(desk, tag="t2")).status == "started"


@pytest.mark.parametrize("reason", ["timeout", "expired", "lost"])
async def test_a_clip_that_may_have_rendered_still_counts(reason):
    store = FakeComputeJobStore()
    starter = _Starter()
    desk = _desk(store, starter)
    await _make(desk)
    await _deliver_through_the_poller(store, JobResult(
        JobStatus.FAILED, error="x", failure_reason=reason))
    assert (await _make(desk, tag="t2")).status == "at_cap"


async def test_a_create_that_broke_after_sending_counts_and_is_never_delivered():
    """A read timeout after the POST may have started a billed clip with no task id: it is
    recorded against the cap as a terminal row, so a retry cannot spend again."""
    store = FakeComputeJobStore()

    class _Uncertain(_Starter):
        async def start(self, request: VideoRequest) -> VideoStart:
            self.calls.append(request)
            return VideoStart("unavailable", reason="timed out", uncertain=True)

    starter = _Uncertain()
    desk = _desk(store, starter)
    out = await _make(desk)
    assert (out.status, out.reason) == ("unavailable", "uncertain")
    assert await store.pending(limit=10) == []
    (row,) = await store.kind_since(kind=VIDEO_KIND, since=NOW - timedelta(days=1))
    assert (row.state, row.failure_reason) == (ComputeJobState.FAILED, "uncertain")
    assert (await _make(desk, tag="t2")).status == "at_cap"
    assert len(starter.calls) == 1


async def test_a_read_timeout_after_sending_is_uncertain_and_a_connect_failure_is_not():
    sent = await _backend(_Provider(create=httpx.ReadTimeout("slow"))).start(VideoRequest("x"))
    assert (sent.status, sent.uncertain) == ("unavailable", True)
    never = await _backend(_Provider(create=httpx.ConnectError("no"))).start(VideoRequest("x"))
    assert (never.status, never.uncertain) == ("unavailable", False)


async def test_a_second_call_in_one_turn_returns_the_first_clip():
    store = FakeComputeJobStore()
    starter = _Starter()
    desk = _desk(store, starter, cap=5)
    first = await _make(desk)
    again = await _make(desk)
    assert again.repeated and again.id == first.id
    assert len(starter.calls) == 1


async def test_a_create_that_may_have_reached_the_provider_is_not_resent_in_the_turn():
    """The tool's budget allows a second call; an uncertain first create must not be re-sent
    by it, or one turn could pay for two clips."""
    store = FakeComputeJobStore()

    class _Uncertain(_Starter):
        async def start(self, request: VideoRequest) -> VideoStart:
            self.calls.append(request)
            return VideoStart("unavailable", reason="timed out", uncertain=True)

    starter = _Uncertain()
    desk = _desk(store, starter, cap=5)
    await _make(desk)
    again = await _make(desk)
    assert (again.status, again.reason) == ("unavailable", "uncertain")
    assert len(starter.calls) == 1


async def test_a_clip_whose_row_was_not_written_is_not_started_again_in_the_turn():
    class _Broken(FakeComputeJobStore):
        async def create(self, job: ComputeJob) -> None:
            raise RuntimeError("db down")

    starter = _Starter()
    desk = _desk(_Broken(), starter, cap=5)
    await _make(desk)
    again = await _make(desk)
    assert (again.status, again.reason) == ("unavailable", "row_not_written")
    assert len(starter.calls) == 1


async def test_a_refused_start_writes_no_row_and_spends_no_cap():
    store = FakeComputeJobStore()
    out = await _make(_desk(store, _Starter("refused")))
    assert out.status == "refused"
    assert await store.kind_since(kind=VIDEO_KIND, since=NOW - timedelta(days=1)) == []


# ---- the tool --------------------------------------------------------------------------------


class _Media:
    """The transcript rows `_held_image` reads: one photo in this room."""

    def __init__(self, sha: str) -> None:
        self._sha = sha

    async def recent(self, *, conversation_id: str, limit: int) -> list[Any]:
        row = type("Row", (), {})()
        row.media_json = json.dumps([{"sha256": self._sha, "media_type": "image/png"}])
        return [row]


async def _tool_rig(*, refs: bool, cap: int = 1) -> tuple[Any, _Starter, FakeComputeJobStore]:
    blobs = FakeBlobStore()
    sha = await blobs.put(data=PNG)
    store = FakeComputeJobStore()
    starter = _Starter()
    registry = ToolRegistry(video=_desk(store, starter, cap=cap), video_refs_allowed=refs,
                            blobs=blobs)
    specs = registry.specs(
        store=_Media(sha), conversation_id=ROOM, conversation_type="group",
        persons=_Persons(), honcho=None, deliverer=None, metrics=Metrics(), room=_room(),
    )
    tools = {s.name: s.call for s in specs}
    return (tools["video_create"], sha), starter, store


async def test_the_tool_is_withheld_unless_video_is_wired():
    assert "video_create" in ToolRegistry()._withheld
    wired = ToolRegistry(video=_desk(FakeComputeJobStore(), _Starter()))
    assert "video_create" not in wired._withheld


async def test_a_reference_is_refused_when_references_are_off():
    (call, sha), starter, _ = await _tool_rig(refs=False)
    bind_tag_id("tag-v")
    out = await call(prompt="her waving", reference=sha[:6])
    assert out["status"] == "unavailable" and "switched off" in out["note"]
    assert starter.calls == []


async def test_after_a_refused_reference_the_words_only_call_still_runs():
    """2026-10-10, live: the refusal offered a clip from words alone, and the per-turn
    budget vetoed that call because the refused one had spent it."""
    (call, sha), starter, _ = await _tool_rig(refs=False)
    guard = ReplyGuard(conversation_id=ROOM, budgets=ToolRegistry().budgets(),
                       terminal=ToolRegistry().terminal_tools(), metrics=Metrics())
    bind_tag_id("tag-w")
    assert guard.evaluate("video_create", {"prompt": "x", "reference": sha[:6]}) is None
    assert (await call(prompt="a cat sings", reference=sha[:6]))["status"] == "unavailable"
    guard.record("video_create")
    assert guard.evaluate("video_create", {"prompt": "a cat sings"}) is None
    assert (await call(prompt="a cat sings"))["status"] == "started"
    assert len(starter.calls) == 1


async def test_a_held_photo_travels_as_its_bytes_and_the_row_keeps_only_its_sha():
    (call, sha), starter, store = await _tool_rig(refs=True)
    bind_tag_id("tag-v")
    out = await call(prompt="her waving", reference=sha[:6])
    assert out["status"] == "started", out
    (sent,) = starter.calls
    assert sent.references == (VideoReference(sha, PNG),)
    (row,) = await store.kind_since(kind=VIDEO_KIND, since=NOW - timedelta(days=1))
    assert row.payload["reference_sha256"] == [sha]
    assert row.requester_person_id == MILES.person_id and row.tag_id == "tag-v"


async def test_an_external_link_is_never_a_reference():
    (call, _), starter, _ = await _tool_rig(refs=True)
    out = await call(prompt="x", reference="https://example.com/a.png")
    assert out["status"] == "nothing_found"
    assert starter.calls == []


async def test_the_cap_refusal_says_why():
    (call, _), _, _ = await _tool_rig(refs=False)
    bind_tag_id("tag-a")
    assert (await call(prompt="one"))["status"] == "started"
    bind_tag_id("tag-b")
    out = await call(prompt="two")
    assert out["status"] == "refused"
    assert "limit" in out["note"] and "1 of 1" in out["note"]


# ---- boot recovery ---------------------------------------------------------------------------


def _row(kind: str, backend_job_id: str = "T123@1790000000") -> ComputeJob:
    return ComputeJob(
        id=uuid.uuid4(), backend_job_id=backend_job_id, task="a wave", artifact_id=None,
        platform=PLATFORM, conversation_id=ROOM, conversation_type="group",
        requester_person_id="miles", created_at=NOW, state=ComputeJobState.PENDING,
        completed_at=None, result_url=None, result_summary=None, error=None,
        task_sha256="x", context_sha256="y", kind=kind,
    )


async def test_recovery_keeps_a_durable_video_row_pending_and_sweeps_the_rest():
    store = FakeComputeJobStore()
    video, worker = _row(VIDEO_KIND), _row(SUBAGENT_KIND, "bj1")
    await store.create(video)
    await store.create(worker)
    swept = await recover(store=store, metrics=Metrics(), now=NOW,
                          durable_kinds=frozenset({VIDEO_KIND}))
    assert [r.id for r in swept] == [worker.id]
    assert (await store.get(video.id)).state is ComputeJobState.PENDING


# ---- delivery --------------------------------------------------------------------------------


def test_the_occasion_tells_her_the_link_expires_and_attaches_it():
    result = JobResult(JobStatus.SUCCEEDED, url=CLIP, summary="x", report=(
        "The video clip is ready. Its link is attached to your message for you, so do not "
        "write the link out yourself. Tell them the link stops working 24 hours after the "
        "clip was made, so they should open or save it today."))
    event = compute_occasion(_row(VIDEO_KIND), result)
    assert "24 hours" in event.body
    assert event.attached_urls == (CLIP,)
    failed = compute_occasion(_row(VIDEO_KIND), JobResult(JobStatus.FAILED, error="no"))
    assert failed.attached_urls == ()


def test_an_attached_link_is_added_once_and_not_repeated_after_it_is_sent():
    links = LinkCarry()
    links.attach(CLIP)
    text, added = links.apply("Here it is!")
    assert added == 1 and text.endswith(CLIP)
    links.mark_sent(text)
    assert links.apply("anything else?") == ("anything else?", 0)


async def test_a_finished_clip_reaches_the_room_with_its_link(doctrine_store):
    """Through the real poller, `FirePath.speak` and the deliverer: she writes no link, and
    the sent message carries the clip's signed URL intact (R-18 lets it through)."""
    persons = Persons(by_person={"miles": PersonRef(person_id="miles", name="Miles",
                                                    honcho_peer_id="miles")},
                      index={("signal", "+85212345678"): "miles"})
    reply = "Your clip is ready! The link stops working in a day, so grab it today."
    adapter = _FireAdapter()
    path = _fire_path(
        doctrine_store=doctrine_store, persons=persons, metrics=Metrics(), adapter=adapter,
        registry=ToolRegistry(),
        runner=FakeAgentRunner([
            ScriptedTurn(tool_calls=[("send_reply", {"text": reply})], final_text=reply),
        ]),
    )
    store = FakeComputeJobStore()
    job = _row(VIDEO_KIND)
    job = ComputeJob(**{**{f: getattr(job, f) for f in job.__dataclass_fields__},
                        "conversation_id": "conv-1", "platform": "signal"})
    await store.create(job)
    backend = _backend(_Provider(polls=[_task("succeeded", content={"url": CLIP})]))
    poller = ComputeJobPoller(
        store=store, backends={VIDEO_KIND: backend}, speak=path.speak,
        slots=ConversationSlots(maxlen=16, spawn=lambda c: None), metrics=Metrics(),
        tick_seconds=1.0, clock=lambda: NOW,
    )
    await poller.tick()
    row = await store.get(job.id)
    assert row.state is ComputeJobState.DONE and row.result_url == CLIP
    assert adapter.texts and CLIP in adapter.texts[0]
    assert adapter.texts[0].count(CLIP) == 1

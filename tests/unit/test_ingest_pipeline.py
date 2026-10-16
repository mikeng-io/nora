"""`IngestPipeline` wiring: does it call the four stages in the right order
and assemble the right `IngestResult`? Per-stage failure behaviour is
`test_ingest_stages.py`'s job; this file is about sequencing.

`IngestPipeline.run` takes an already-normalized `message`, not a raw event —
parsing happens in the adapter (`SignalAdapter.listen`). The drop cases (a DM,
a sync message, a self-send, an empty body all coming back `None`) are covered
in `tests/test_signal_adapter.py`.
"""

from __future__ import annotations

import dataclasses
import hashlib

import pytest

from nora.domain.media import MediaRef
from nora.domain.models import EditTarget, NormalizedMessage, PersonRef
from nora.domain.ports import MemoryHealth
from nora.ingest.pipeline import IngestPipeline, IngestResult
from nora.ingest.policy import TriggerTextPolicy
from nora.obs.metrics import Metrics
from nora.persons import Persons
from nora.platform.registry import MediaSourceRegistry
from nora.policy import permissive_policy
from nora.testing import FakeBlobStore, FakeMediaSource, FakeRecordStore, FakeTranscriber


def _msg(sender_id: str = "+852111", body: str = "hello") -> NormalizedMessage:
    return NormalizedMessage(
        platform="signal", conversation_id="g1", conversation_type="group",
        sender_id=sender_id, sender_name="Miles", timestamp=1, body=body, raw={},
    )


class FakeMemory:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def ensure_workspace(self) -> None:
        return None

    async def ensure_peer(self, *, peer_id: str, **_route: str | None) -> None:
        self.calls.append(("ensure_peer", peer_id))

    async def feed(self, *, peer_id: str, text: str, conversation_id: str = "",
                   metadata: dict | None = None) -> bool:
        self.calls.append(("feed", peer_id))
        return True

    async def peer_card(self, *, peer_id: str, **_route: str | None) -> list[str]:
        return []

    async def health(self) -> MemoryHealth:
        return MemoryHealth(reachable=True)

    async def aclose(self) -> None:
        return None


def _pipeline(*, memory, persons, policy, spawn, audit=None, allowlist=None,
              media_sources=None, blobs=None, metrics=None,
              transcriber=None, language=""):
    return IngestPipeline(
        audit=audit if audit is not None else FakeRecordStore(),
        memory=memory,
        persons=persons,
        policy=policy,
        # These tests are about stage sequencing, not the allowlist; the room
        # gate has its own tests in tests/unit/test_ingest_scope.py.
        allowlist=allowlist if allowlist is not None else permissive_policy(),
        metrics=metrics if metrics is not None else Metrics(),
        spawn=spawn,
        media_sources=media_sources,
        blobs=blobs,
        transcriber=transcriber,
        language=language,
    )


async def test_when_the_audit_write_fails_the_pipeline_stops_before_identity_and_gate(
    monkeypatch,
):
    """A failed audit write drops the message before identity,
    memory-feed or the gate ever run."""
    msg = _msg()
    store = FakeRecordStore()

    async def _boom(**_kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(store, "append", _boom)
    spawned: list = []
    memory = FakeMemory()
    pipeline = _pipeline(
        audit=store, memory=memory,
        persons=Persons(by_person={}, index={}),
        policy=TriggerTextPolicy("nora.000"), spawn=spawned.append,
    )

    result = await pipeline.run(message=msg)

    assert result == IngestResult(message=None, decision=None)
    assert spawned == []
    assert memory.calls == []


async def test_an_unmapped_sender_still_reaches_the_gate():
    """D6: an unmapped sender continues through the pipeline — identity
    returning None is not a stopping condition, only memory-feed skips it."""
    msg = _msg(body="nora.000 hi")
    spawned: list = []
    memory = FakeMemory()
    pipeline = _pipeline(
        memory=memory, persons=Persons(by_person={}, index={}),
        policy=TriggerTextPolicy("nora.000"), spawn=spawned.append,
    )

    result = await pipeline.run(message=msg)

    assert result.message == msg
    assert result.decision is not None
    assert result.decision.engage is True
    assert spawned == []          # unmapped sender: never fed
    assert memory.calls == []


async def test_a_mapped_trigger_message_engages_and_feeds_memory_off_path():
    """The happy path: audited, identified, fed off-path via spawn, and
    engaged by the real TriggerTextPolicy."""
    msg = _msg(sender_id="+852111", body="nora.000 what's up")
    persons = Persons(
        by_person={"miles": PersonRef(person_id="miles", name="Miles", honcho_peer_id="miles")},
        index={("signal", "+852111"): "miles"},
    )
    spawned: list = []
    memory = FakeMemory()
    store = FakeRecordStore()
    pipeline = _pipeline(
        audit=store, memory=memory, persons=persons,
        policy=TriggerTextPolicy("nora.000"), spawn=spawned.append,
    )

    result = await pipeline.run(message=msg)

    assert result.message == msg
    assert result.decision.engage is True
    assert len(store.rows) == 1
    assert len(spawned) == 1
    assert memory.calls == []      # off-path: not run until the spawned coroutine is
    await spawned[0]
    assert memory.calls == [("ensure_peer", "miles"), ("feed", "miles")]


# ── the two policy gates ───────────────────────────────────


async def test_a_denied_conversation_is_never_recorded():
    """The room gate runs BEFORE the audit write: auditing a room the operator
    has not allowed would store content he never authorised Nora to keep, and
    anyone can add her to a group."""
    import tempfile
    from pathlib import Path

    from nora.policy import load_policy

    d = tempfile.mkdtemp()
    p = Path(d) / "policy.toml"
    p.write_text(
        'default = "deny"\n[[conversations]]\nplatform = "signal"\n'
        'id = "allowed"\nlabel = "allowed"\npersons = "*"\n'
    )
    store = FakeRecordStore()
    pipeline = _pipeline(
        memory=FakeMemory(), persons=Persons(by_person={}, index={}),
        policy=TriggerTextPolicy("nora.000"), spawn=lambda c: c.close(),
        audit=store, allowlist=load_policy(p),
    )
    msg = dataclasses.replace(_msg(body="nora.000 hi"), conversation_id="not-allowed")
    result = await pipeline.run(message=msg)
    assert store.rows == [], "a denied conversation must leave no audit row"
    assert result.message is None and result.decision is None


async def test_a_denied_sender_is_recorded_but_not_answered():
    """The opposite placement, and deliberately so: the message IS audited, so
    an unrecognised sender in an allowed room remains discoverable."""
    import tempfile
    from pathlib import Path

    from nora.policy import load_policy

    d = tempfile.mkdtemp()
    p = Path(d) / "policy.toml"
    p.write_text(
        'default = "deny"\n[[conversations]]\nplatform = "signal"\n'
        'id = "g1"\nlabel = "g1"\npersons = ["someone-else"]\n'
    )
    store = FakeRecordStore()
    pipeline = _pipeline(
        memory=FakeMemory(), persons=Persons(by_person={}, index={}),
        policy=TriggerTextPolicy("nora.000"), spawn=lambda c: c.close(),
        audit=store, allowlist=load_policy(p),
    )
    result = await pipeline.run(
        message=dataclasses.replace(_msg(body="nora.000 hi"), conversation_id="g1")
    )
    assert len(store.rows) == 1, "the message must still be audited"
    assert result.decision.engage is False
    assert result.decision.reason == "sender_not_allowed"


JPEG = b"\xff\xd8\xff\xe0 pretend jpeg"


async def test_custody_runs_before_the_audit_write_so_one_row_carries_both():
    """DEC-43's ordering, asserted on the ROW rather than on call order.

    The body's handle and the hash that backs it land in the same INSERT. A
    spawned copy would write the promise first and keep it second — a window in
    which the record claims custody that does not exist, which is the A9
    violation this change exists to end at a smaller size.
    """
    import hashlib
    import json

    store = FakeRecordStore()
    blobs = FakeBlobStore()
    msg = dataclasses.replace(
        _msg(body="睇下 [image]"),
        media=(MediaRef(platform_id="att1", media_type="image/jpeg", filename="a.jpg"),),
    )
    pipeline = _pipeline(
        memory=FakeMemory(), persons=Persons(by_person={}, index={}),
        policy=TriggerTextPolicy("nora.000"), spawn=lambda c: c.close(),
        audit=store, media_sources=MediaSourceRegistry([FakeMediaSource({"att1": JPEG})]),
        blobs=blobs,
    )

    result = await pipeline.run(message=msg)

    digest = hashlib.sha256(JPEG).hexdigest()
    assert len(store.rows) == 1
    row = store.rows[0]
    assert row["body"] == f"睇下 [image {digest[:6]}]"
    assert json.loads(row["media_json"])[0]["sha256"] == digest
    assert blobs.blobs[digest] == JPEG
    # And the message handed on to the tagging path is the one with the handle,
    # so what Nora reads and what was recorded are the same string.
    assert result.message.body == row["body"]


async def test_with_custody_unwired_the_row_is_recorded_and_the_loss_is_counted():
    """No blob store wired: the message must still be audited, with the
    placeholder and no handle, and `nora_media_custody_total{status="not_configured"}`
    is the only place that loss is visible."""
    store = FakeRecordStore()
    metrics = Metrics()
    msg = dataclasses.replace(
        _msg(body="[voice note]"),
        media=(MediaRef(platform_id="att1", media_type="audio/aac", voice_note=True),),
    )
    pipeline = _pipeline(
        memory=FakeMemory(), persons=Persons(by_person={}, index={}),
        policy=TriggerTextPolicy("nora.000"), spawn=lambda c: c.close(),
        audit=store, metrics=metrics,
    )

    await pipeline.run(message=msg)

    assert store.rows[0]["body"] == "[voice note]"
    assert store.rows[0]["media_json"] is None
    assert metrics.get("nora_media_custody_total", status="not_configured") == 1


AUDIO = b"\x00\xff pretend opus"


async def test_a_voice_note_is_transcribed_at_custody_and_the_row_keeps_the_handle():
    """A voice note is conversation CONTENT — the ingest path transcribes it
    INLINE, before the audit write, so the words are captured on the same pass
    that takes custody of the bytes. A song (`voice_note=False`) stays on the
    bare-handle path even when a transcriber is wired (the gate is the
    `voiceNote` flag, NOT the audio/* MIME type).

    **The words do not ride the BODY.** `messages.body` is read
    verbatim by `memory_feed` and by the prefill, so one string could not serve
    both; the transcript lands in `media_objects.content` and is joined back
    into the transcript block at render time, in full. What this test pins is
    the half that has to stay true regardless: the STT call still happens
    inline, with the right type and language hint, and the durable row carries
    the handle that names the bytes.
    """
    store = FakeRecordStore()
    blobs = FakeBlobStore()
    metrics = Metrics()
    transcriber = FakeTranscriber()
    msg = dataclasses.replace(
        _msg(body="[voice note]"),
        media=(MediaRef(platform_id="att1", media_type="audio/aac", voice_note=True),),
    )
    pipeline = _pipeline(
        memory=FakeMemory(), persons=Persons(by_person={}, index={}),
        policy=TriggerTextPolicy("nora.000"), spawn=lambda c: c.close(),
        audit=store, media_sources=MediaSourceRegistry([FakeMediaSource({"att1": AUDIO})]),
        blobs=blobs, metrics=metrics, transcriber=transcriber, language="yue-HK",
    )

    await pipeline.run(message=msg)

    digest = hashlib.sha256(AUDIO).hexdigest()
    assert len(store.rows) == 1
    row = store.rows[0]
    assert row["body"] == f"[voice note {digest[:6]}]"
    assert transcriber.calls[0][1] == "audio/aac"
    assert transcriber.calls[0][2] == "yue-HK"
    assert metrics.get("nora_speech_transcriptions_total", status="ok") == 1


async def test_a_failed_transcription_still_records_the_voice_note_with_a_bare_handle():
    """R-27: an ERROR is never a PASS. A transcription failure leaves the
    voice note recorded with the handle and no guessed words — the note is
    still a note, and the message is never dropped over STT.
    """
    store = FakeRecordStore()
    blobs = FakeBlobStore()
    metrics = Metrics()
    transcriber = FakeTranscriber(available=False, reason="error")
    msg = dataclasses.replace(
        _msg(body="[voice note]"),
        media=(MediaRef(platform_id="att1", media_type="audio/aac", voice_note=True),),
    )
    pipeline = _pipeline(
        memory=FakeMemory(), persons=Persons(by_person={}, index={}),
        policy=TriggerTextPolicy("nora.000"), spawn=lambda c: c.close(),
        audit=store, media_sources=MediaSourceRegistry([FakeMediaSource({"att1": AUDIO})]),
        blobs=blobs, metrics=metrics, transcriber=transcriber,
    )

    await pipeline.run(message=msg)

    digest = hashlib.sha256(AUDIO).hexdigest()
    assert store.rows[0]["body"] == f"[voice note {digest[:6]}]"
    assert metrics.get("nora_speech_transcriptions_total", status="error") == 1


async def test_a_song_is_never_transcribed_even_when_a_transcriber_is_wired():
    """The gate is the parser's `voiceNote` flag, NOT the audio/* MIME type.

    A song is an audio file, not conversation content: its bytes must never
    reach the STT endpoint even when a transcriber is present. This is the
    negative half of `test_a_voice_note_is_transcribed...` — a future refactor
    that relaxed the gate to `media_type.startswith("audio/")` must turn this
    red.
    """
    store = FakeRecordStore()
    blobs = FakeBlobStore()
    metrics = Metrics()
    transcriber = FakeTranscriber()
    msg = dataclasses.replace(
        _msg(body="[audio]"),
        media=(MediaRef(platform_id="att1", media_type="audio/mpeg", voice_note=False),),
    )
    pipeline = _pipeline(
        memory=FakeMemory(), persons=Persons(by_person={}, index={}),
        policy=TriggerTextPolicy("nora.000"), spawn=lambda c: c.close(),
        audit=store, media_sources=MediaSourceRegistry([FakeMediaSource({"att1": AUDIO})]),
        blobs=blobs, metrics=metrics, transcriber=transcriber, language="yue-HK",
    )

    await pipeline.run(message=msg)

    digest = hashlib.sha256(AUDIO).hexdigest()
    assert transcriber.calls == [], "a song must never be sent to STT"
    assert store.rows[0]["body"] == f"[audio {digest[:6]}]"


class _RaisingGetBlobStore(FakeBlobStore):
    """A `BlobStore` whose `get` always raises, e.g. a corrupted blob or a
    rotated encryption key on an otherwise-successful `put`."""

    async def get(self, *, key: str) -> bytes:
        raise RuntimeError("simulated decrypt failure")


async def test_a_voice_note_transcribes_even_when_the_blob_store_cannot_be_read_back():
    """The custody stage must never call `blobs.get` to transcribe a voice
    note it JUST wrote — it reuses the plaintext `_take` already holds. A
    store whose `get` raises (corrupted blob, rotated key) must not stop the
    message from ever transcribing or being recorded (a `blobs.get` round trip
    could raise uncaught and drop the message)."""
    store = FakeRecordStore()
    blobs = _RaisingGetBlobStore()
    metrics = Metrics()
    transcriber = FakeTranscriber()
    msg = dataclasses.replace(
        _msg(body="[voice note]"),
        media=(MediaRef(platform_id="att1", media_type="audio/aac", voice_note=True),),
    )
    pipeline = _pipeline(
        memory=FakeMemory(), persons=Persons(by_person={}, index={}),
        policy=TriggerTextPolicy("nora.000"), spawn=lambda c: c.close(),
        audit=store, media_sources=MediaSourceRegistry([FakeMediaSource({"att1": AUDIO})]),
        blobs=blobs, metrics=metrics, transcriber=transcriber, language="yue-HK",
    )

    await pipeline.run(message=msg)

    digest = hashlib.sha256(AUDIO).hexdigest()
    assert len(store.rows) == 1
    assert store.rows[0]["body"] == f"[voice note {digest[:6]}]"
    # The transcriber saw the SAME plaintext `_take` held — which is the whole
    # point: a `blobs.get` round trip through the just-written key would have
    # raised on this store, and the message would have been lost to a read the
    # stage never needed to make.
    assert transcriber.calls[0][0] == AUDIO
    assert metrics.get("nora_speech_transcriptions_total", status="ok") == 1


# ---------------------------------------------------------------------------
# WHAT AN INGEST REPORTS, AND WHERE (R-76).
#
# TWO INSTRUMENTS, and which one carries what is the decision under test.
#
# An ingest contains no model call — six deterministic stages, a row, a feed —
# so its trace is PLUMBING and never reaches Langfuse (`nora.obs.tracing`'s
# sampler drops the root, and `ParentBased` takes the five children with it).
# Langfuse is for traces with a generation in them; ingest is the
# highest-volume entry point in the system. What the trace would say lives on
# `nora_ingest_outcomes_total`, which is a Prometheus question asked in
# Prometheus.
#
# The trace is UNEXPORTED, not deleted: the pipeline still opens it and still
# populates it, which is what makes «the collector gets nothing» a measurement
# of the RULE rather than of an absence — a pipeline that opened no spans would
# satisfy that assertion for the wrong reason. `unfiltered_spans` below is the
# fixture that reads the trace production drops, so both halves are visible.
#
# These drive the REAL pipeline and read what it ACTUALLY EXPORTED and ACTUALLY
# COUNTED. Nothing here asserts that a helper was called: a test of that shape
# would stay green while `_outcome` is called on some exits and never on the
# one that runs a thousand times a day.
# ---------------------------------------------------------------------------


@pytest.fixture
def unfiltered_spans(monkeypatch):
    """`exported_spans`, but WITHOUT production's sampler.

    The only thing in the suite that can see an ingest trace. `exported_spans`
    is built with `build_sampler()` so it models the deployment; this one models
    the pipeline, so «what the ingest trace carries» and «what the collector
    receives» can each be asserted against the thing that actually decides it.

    Patches `get_tracer` and shuts its provider down on the way out, for the
    reason `tests/conftest.py` spells out: `set_tracer_provider` is one-shot per
    process, and a leaked provider can break unrelated timing tests.
    """
    from opentelemetry.sdk.trace import TracerProvider as _TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    from nora.obs import tracing as _tracing

    exporter = InMemorySpanExporter()
    provider = _TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(_tracing.trace, "get_tracer", provider.get_tracer)
    try:
        yield exporter
    finally:
        provider.shutdown()


def _spans_by_name(exporter) -> dict:
    out: dict[str, list] = {}
    for recorded in exporter.get_finished_spans():
        out.setdefault(recorded.name, []).append(recorded)
    return out


def _only_root(exporter):
    """The one span with no parent, or a failure that names what it found.

    Identified by `parent is None` rather than by name, so a change that
    re-parented the root reddens here instead of quietly reading a child.
    """
    roots = [s for s in exporter.get_finished_spans() if s.parent is None]
    assert len(roots) == 1, \
        f"one ingest must be exactly one trace; roots: {[s.name for s in roots]}"
    return roots[0]


def _root(exporter):
    """The span an operator sees without expanding anything.

    Identified by `parent is None` rather than by name, so a change that
    re-parented the root would redden here instead of quietly moving the
    outcome onto a child.
    """
    roots = [s for s in exporter.get_finished_spans() if s.parent is None]
    assert len(roots) == 1, \
        f"one ambient message must be exactly one trace; roots: {[s.name for s in roots]}"
    return roots[0]


def _policy_of(*, room: str = "g1", persons: str = '"*"'):
    """A real `Policy` read from a real policy.toml, not a permissive stub —
    the room gate and the sender gate are two of the exits under test."""
    import tempfile
    from pathlib import Path

    from nora.policy import load_policy

    p = Path(tempfile.mkdtemp()) / "policy.toml"
    p.write_text(
        'default = "deny"\n[[conversations]]\nplatform = "signal"\n'
        f'id = "{room}"\nlabel = "{room}"\npersons = {persons}\n'
    )
    return load_policy(p)


def _known_persons() -> Persons:
    return Persons(
        by_person={"miles": PersonRef(person_id="miles", name="Miles", honcho_peer_id="miles")},
        index={("signal", "+852111"): "miles"},
    )


async def _drive_recorded(metrics=None):
    """An ordinary ambient message: allowlisted room, no trigger. THE AMBIENT
    MAJORITY."""
    pipeline = _pipeline(
        memory=FakeMemory(), persons=_known_persons(),
        policy=TriggerTextPolicy("nora.000"), spawn=lambda c: c.close(),
        allowlist=_policy_of(), metrics=metrics,
    )
    return await pipeline.run(message=_msg(body="just chatting"))


async def _drive_engaged(metrics=None):
    pipeline = _pipeline(
        memory=FakeMemory(), persons=_known_persons(),
        policy=TriggerTextPolicy("nora.000"), spawn=lambda c: c.close(),
        allowlist=_policy_of(), metrics=metrics,
    )
    return await pipeline.run(message=_msg(body="nora.000 what's up"))


async def _drive_scope_refused(metrics=None):
    pipeline = _pipeline(
        memory=FakeMemory(), persons=_known_persons(),
        policy=TriggerTextPolicy("nora.000"), spawn=lambda c: c.close(),
        allowlist=_policy_of(room="some-other-room"), metrics=metrics,
    )
    return await pipeline.run(message=_msg(body="nora.000 hi"))


async def _drive_audit_refused(monkeypatch, metrics=None):
    store = FakeRecordStore()

    async def _boom(**_kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(store, "append", _boom)
    pipeline = _pipeline(
        audit=store, memory=FakeMemory(), persons=_known_persons(),
        policy=TriggerTextPolicy("nora.000"), spawn=lambda c: c.close(),
        allowlist=_policy_of(), metrics=metrics,
    )
    return await pipeline.run(message=_msg(body="nora.000 hi"))


async def _drive_sender_not_allowed(metrics=None):
    pipeline = _pipeline(
        memory=FakeMemory(), persons=_known_persons(),
        policy=TriggerTextPolicy("nora.000"), spawn=lambda c: c.close(),
        allowlist=_policy_of(persons='["someone-else"]'), metrics=metrics,
    )
    return await pipeline.run(message=_msg(body="nora.000 hi"))


async def _drive_edit(metrics, *, edit_body: str, original_is_nora: bool = False,
                      sender_id: str = "+852111", recorded: bool = True,
                      stale: bool = False):
    """An EDIT of a Signal message sent at ts=1.
    `recorded=False` leaves the original off the record — an orphan.
    `stale=True` records a LATER edit first, so this one (ts=5) is older."""
    store = FakeRecordStore()
    if recorded:
        row = await store.append(
            message=_msg(body="dinner at 7"), is_nora=original_is_nora,
            person_id=None if original_is_nora else "miles",
        )
        if stale:
            await store.append_edit(message_id=row, message=dataclasses.replace(
                _msg(body="dinner at 9"), timestamp=9, edit_of=EditTarget(sent_ts=1),
            ))
    pipeline = _pipeline(
        audit=store, memory=FakeMemory(), persons=_known_persons(),
        policy=TriggerTextPolicy("nora.000"), spawn=lambda c: c.close(),
        allowlist=_policy_of(), metrics=metrics,
    )
    edit = dataclasses.replace(
        _msg(sender_id=sender_id, body=edit_body), timestamp=5,
        edit_of=EditTarget(sent_ts=1),
    )
    return await pipeline.run(message=edit)


async def _drive_touch(metrics, *, kind: str, recorded: bool = True,
                       sender_id: str = "+852111"):
    """A REACTION or a DELETE of the Signal message sent at ts=1.
    `recorded=False` leaves the target off the record — an orphan."""
    from nora.domain.models import Touch

    store = FakeRecordStore()
    if recorded:
        await store.append(message=_msg(body="dinner at 7"), person_id="miles")
    pipeline = _pipeline(
        audit=store, memory=FakeMemory(), persons=_known_persons(),
        policy=TriggerTextPolicy("nora.000"), spawn=lambda c: c.close(),
        allowlist=_policy_of(), metrics=metrics,
    )
    touch = dataclasses.replace(
        _msg(sender_id=sender_id, body=""), timestamp=5,
        touch=Touch(kind=kind, target=EditTarget(sent_ts=1), emoji="👍"),  # type: ignore[arg-type]
    )
    return await pipeline.run(message=touch)


#: Every exit, paired with the outcome it must count. `_OUTCOMES` is checked
#: against the KEYS of this table rather than the table being written from the
#: vocabulary, so an outcome added without a driver — or a driver added without
#: an outcome — reddens instead of drifting.
def _all_drivers(monkeypatch, metrics):
    return {
        "recorded": lambda: _drive_recorded(metrics),
        "engaged": lambda: _drive_engaged(metrics),
        "scope_refused": lambda: _drive_scope_refused(metrics),
        "sender_not_allowed": lambda: _drive_sender_not_allowed(metrics),
        "audit_refused": lambda: _drive_audit_refused(monkeypatch, metrics),
        "edit_recorded": lambda: _drive_edit(metrics, edit_body="dinner at 8"),
        "edit_engaged": lambda: _drive_edit(metrics, edit_body="nora.000 dinner at 8?"),
        "edit_unchanged": lambda: _drive_edit(metrics, edit_body="dinner at 7"),
        "edit_stale": lambda: _drive_edit(metrics, edit_body="dinner at 8", stale=True),
        "edit_orphaned": lambda: _drive_edit(
            metrics, edit_body="dinner at 8", sender_id="", recorded=False,
        ),
        "edit_refused": lambda: _drive_edit(
            metrics, edit_body="dinner at 8", original_is_nora=True,
        ),
        "reaction_recorded": lambda: _drive_touch(metrics, kind="reaction"),
        "reaction_orphaned": lambda: _drive_touch(metrics, kind="reaction", recorded=False),
        "delete_recorded": lambda: _drive_touch(metrics, kind="delete"),
        "delete_orphaned": lambda: _drive_touch(metrics, kind="delete", recorded=False),
        "delete_refused": lambda: _drive_touch(metrics, kind="delete",
                                               sender_id="+852999"),
    }


async def test_an_ingest_reaches_the_collector_with_nothing(exported_spans):
    """R-76. THE RULE: a trace reaches Langfuse when the work under its root can
    contain a generation. An ingest cannot — six deterministic stages, a row,
    a feed, no model call anywhere — so none of its spans are exported.

    Langfuse is an LLM-observability tool, and ingest is the highest-volume
    entry point in the system; a collector full of plumbing cannot be read for
    the turns that matter.

    TWO POSITIVE CONTROLS, because `== []` is also true of a harness that never
    ran and of an exporter that was never live:

    1. the ingest really happened — it returns a recorded message, and its
       outcome is on the counter that replaced the trace;
    2. the SAME fixture exports a root that does not declare itself plumbing,
       so the drop is selective rather than total.

    PROBE: delete `TRACE_KIND: "plumbing"` from the root `span(...)` call in
    `run` and the six ingest spans come back.
    """
    metrics = Metrics()
    result = await _drive_recorded(metrics)

    assert result.message is not None, "positive control 1: the ingest really ran"
    assert metrics.get("nora_ingest_outcomes_total", outcome="recorded") == 1, \
        "positive control 1: it reported itself, just not to the collector"
    leaked = [s.name for s in exported_spans.get_finished_spans()]
    assert leaked == [], f"an ingest reached the collector: {leaked}"

    from nora.obs.tracing import span as _span
    with _span("a-root-that-declares-nothing"):
        pass
    assert [s.name for s in exported_spans.get_finished_spans()] == [
        "a-root-that-declares-nothing"
    ], "positive control 2: the exporter is live and the drop is selective"


async def test_no_ingest_exit_reaches_the_collector(exported_spans, monkeypatch):
    """The same property over ALL FIVE exits, not just the ambient one. A
    refusal, a failed audit write and a real tag are the exits most likely to be
    argued back into the collector one at a time.

    The positive control is the counter: every driver must be seen to have run,
    so «nothing exported» can never pass as «nothing happened».
    """
    from nora.ingest.pipeline import _OUTCOMES

    metrics = Metrics()
    for outcome, drive in _all_drivers(monkeypatch, metrics).items():
        exported_spans.clear()
        await drive()
        leaked = [s.name for s in exported_spans.get_finished_spans()]
        assert leaked == [], f"{outcome} reached the collector: {leaked}"
        assert metrics.get("nora_ingest_outcomes_total", outcome=outcome) == 1, \
            f"positive control: {outcome} did not actually run"

    assert set(_all_drivers(monkeypatch, metrics)) == _OUTCOMES


async def test_the_ingest_trace_is_opened_and_populated_before_it_is_dropped(
    unfiltered_spans,
):
    """WHAT IS BEING DROPPED, read with the sampler removed.

    The trace is unexported, NOT deleted, and that distinction is load-bearing
    twice over. It is what makes the zero-assertions above measure the rule
    rather than an absence — the pipeline really does build a complete,
    populated, correctly-parented trace on every message — and it is what makes
    R-76's reversal condition one attribute rather than a re-instrumentation.

    PROBE: delete the `with span("ingest", ...)` wrapper in `run` and this
    reddens on the parenting; delete `_outcome`'s `annotate` and it reddens on
    the outcome.
    """
    await _drive_engaged()

    exported = unfiltered_spans.get_finished_spans()
    assert {s.name for s in exported} == {
        "ingest", "ingest.scope", "ingest.identity", "ingest.media",
        "ingest.external_files", "ingest.quote_target", "ingest.audit",
        "ingest.memory_feed", "ingest.gate",
    }
    assert len({s.context.trace_id for s in exported}) == 1
    root = _only_root(unfiltered_spans)
    for recorded in exported:
        if recorded is not root:
            assert recorded.parent.span_id == root.context.span_id

    assert root.attributes["nora.trace_kind"] == "plumbing"
    assert root.attributes["nora.outcome"] == "engaged"
    assert root.attributes["nora.engage_reason"] == "trigger_text"
    assert root.attributes["nora.person_known"] is True
    assert root.attributes["nora.conversation_type"] == "group"


async def test_the_root_declares_itself_plumbing_at_span_start(unfiltered_spans):
    """The declaration has to be an argument to `span(...)`, not an `annotate`
    afterwards: a `Sampler` is handed only the attributes the span was STARTED
    with, so a kind set one line later is a kind the decision was already made
    without.

    This is the half a reader cannot see from the sampler alone — it would look
    identical, and silently never fire.

    PROBE: move `TRACE_KIND: "plumbing"` out of the `span(...)` call in `run`
    into an `annotate(...)` on the next line. This test stays green and
    `test_an_ingest_reaches_the_collector_with_nothing` reddens, which is the
    pair that pins it.
    """
    await _drive_recorded()
    root = _only_root(unfiltered_spans)

    from opentelemetry.sdk.trace.sampling import Decision

    from nora.obs.tracing import TRACE_KIND, _PlumbingIsNotATrace

    verdict = _PlumbingIsNotATrace().should_sample(
        None, 1, "ingest", attributes=dict(root.attributes),
    )
    assert verdict.decision is Decision.DROP, \
        "the attributes the root STARTS with must be enough to decide against it"
    assert TRACE_KIND in root.attributes


async def test_every_exit_is_counted_with_its_outcome(monkeypatch):
    """THE REPORTING INSTRUMENT. The counter says what an ingest did, and every
    exit must reach it — the ambient majority included, which is the exit most
    easily lost.

    PROBE: delete any one `_outcome(...)` call in `_run` and this reddens on
    that outcome.
    """
    from nora.ingest.pipeline import _OUTCOMES

    metrics = Metrics()
    for outcome, drive in _all_drivers(monkeypatch, metrics).items():
        await drive()
        assert metrics.get("nora_ingest_outcomes_total", outcome=outcome) == 1, \
            f"{outcome} was not counted"

    assert metrics.get("nora_ingest_outcomes_total") == len(_OUTCOMES), \
        "an exit counted twice, or one counted an outcome another exit owns"


async def test_every_declared_outcome_is_reachable(monkeypatch):
    """The vocabulary is DERIVED from the code, and this keeps it honest in the
    other direction: a name declared in `IngestOutcome` that no exit ever
    records is the label equivalent of a metric with no call site — it renders
    as a filter value that is genuinely always empty.

    The expected set is read off the `Literal` with `get_args` and the observed
    set off the RENDERED counter, never hand-copied, so adding an outcome
    without an exit — or an exit without an outcome — reddens here.
    """
    from nora.ingest.pipeline import _OUTCOMES

    metrics = Metrics()
    for drive in _all_drivers(monkeypatch, metrics).values():
        await drive()

    seen = {
        dict(labels)["outcome"]
        for (name, labels), value in metrics.snapshot().items()
        if name == "nora_ingest_outcomes_total" and value
    }
    assert seen == _OUTCOMES, (
        f"declared but unreachable: {_OUTCOMES - seen}; "
        f"recorded but undeclared: {seen - _OUTCOMES}"
    )


async def test_the_outcome_counter_says_which_platform_and_which_kind_of_room():
    """The two dimensions that make the series readable. A stuck platform is
    invisible in a total, and a group Nora watches and a DM are different
    questions — these are the trace attributes worth keeping, as labels.

    PROBE: drop either label from the `metrics.inc` in `_outcome`. Both degrade
    to `""` rather than raising (`Metrics._full_labels`), so this is the only
    thing that would notice.
    """
    metrics = Metrics()
    await _drive_recorded(metrics)

    assert metrics.get(
        "nora_ingest_outcomes_total",
        platform="signal", conversation_type="group", outcome="recorded",
    ) == 1
    assert metrics.get("nora_ingest_outcomes_total", platform="discord") == 0


async def test_the_outcome_counter_carries_no_identifier():
    """R-73's floor, applied to the outcome counter. The
    counter's labels are three closed vocabularies — never the conversation_id
    the span carried, which would be an unbounded label as well as an egress.

    The POSITIVE CONTROL is what makes the null result mean anything: the screen
    is first shown to FIRE on the sender id, so «it found nothing in the
    metrics» cannot be «the screen cannot see this».
    """
    from nora.agent.egress import scan_identifiers

    sender = "+85298765432"
    room = "Lf2L9zqeNUahRsOz6BA+sTB8aWfbuvqu9vOhpvmJi34="
    assert scan_identifiers(sender) == "phone", \
        "positive control: the screen must be able to see this identifier"

    metrics = Metrics()
    pipeline = _pipeline(
        memory=FakeMemory(), persons=Persons(by_person={}, index={}),
        policy=TriggerTextPolicy("nora.000"), spawn=lambda c: c.close(),
        allowlist=_policy_of(room=room), metrics=metrics,
    )
    await pipeline.run(message=dataclasses.replace(
        _msg(sender_id=sender, body="nora.000 my number is " + sender),
        conversation_id=room,
    ))

    rendered = metrics.render()
    assert "nora_ingest_outcomes_total" in rendered, \
        "positive control: the counter was actually recorded"
    for (name, labels), _value in metrics.snapshot().items():
        if name != "nora_ingest_outcomes_total":
            continue
        for key, value in labels:
            assert scan_identifiers(str(value)) is None, \
                f"the outcome counter's {key} label carries an identifier: {value!r}"


async def test_an_undeclared_outcome_is_still_recorded_and_reported(caplog):
    """The vocabulary check LOGS; it never withholds. Refusing to record an
    unexpected value would leave the case least understood with no record at
    all. `make gate`'s mypy does not reach `nora/ingest`, so this runtime check
    is the only guard the `Literal` actually has.

    PROBE: make `_outcome` `return` early on an unknown value, or delete the
    `log.warning`.
    """
    import logging as _logging

    from nora.ingest.pipeline import _outcome

    metrics = Metrics()
    with caplog.at_level(_logging.WARNING, logger="nora.ingest.pipeline"):
        _outcome(metrics, _msg(), "something_nobody_declared")  # type: ignore[arg-type]

    assert metrics.get(
        "nora_ingest_outcomes_total", outcome="something_nobody_declared",
    ) == 1
    assert "not in the declared vocabulary" in caplog.text


async def test_a_broken_tracer_never_stops_an_ingest(monkeypatch):
    """THE HARD CONSTRAINT. Every observation added here is guarded, and a
    tracer that cannot record degrades to a log line — an ingest is on the
    listener's await path, so an exception from an observation would stop
    ingest for every conversation on the adapter.

    Both ends are broken at once: `get_tracer` (which `span` needs) and
    `get_current_span` (which `annotate` needs).
    """
    import nora.obs.tracing as tracing_module

    def _boom(*_a, **_kw):
        raise RuntimeError("tracer is broken")

    monkeypatch.setattr(tracing_module.trace, "get_tracer", _boom)
    monkeypatch.setattr(tracing_module.trace, "get_current_span", _boom)

    store = FakeRecordStore()
    metrics = Metrics()
    pipeline = _pipeline(
        audit=store, memory=FakeMemory(), persons=_known_persons(),
        policy=TriggerTextPolicy("nora.000"), spawn=lambda c: c.close(),
        allowlist=_policy_of(), metrics=metrics,
    )

    result = await pipeline.run(message=_msg(body="nora.000 hi"))

    assert result.decision is not None and result.decision.engage is True
    assert len(store.rows) == 1, "the message must still have been recorded"
    assert metrics.get("nora_ingest_outcomes_total", outcome="engaged") == 1, \
        "the counter is the surviving instrument; it must still have fired"


async def test_a_broken_counter_never_stops_an_ingest(monkeypatch):
    """The SAME constraint on the counter that carries reporting: a metrics
    recorder that raises is still an observation, and an observation must never
    change control flow.

    PROBE: delete the `try/except` around `metrics.inc` in `_outcome` and this
    reddens with the ingest itself failing.
    """
    class _BrokenMetrics(Metrics):
        def inc(self, name, value=1.0, **labels):
            if name == "nora_ingest_outcomes_total":
                raise RuntimeError("registry is broken")
            super().inc(name, value, **labels)

    store = FakeRecordStore()
    pipeline = _pipeline(
        audit=store, memory=FakeMemory(), persons=_known_persons(),
        policy=TriggerTextPolicy("nora.000"), spawn=lambda c: c.close(),
        allowlist=_policy_of(), metrics=_BrokenMetrics(),
    )

    result = await pipeline.run(message=_msg(body="nora.000 hi"))

    assert result.decision is not None and result.decision.engage is True
    assert len(store.rows) == 1, "the message must still have been recorded"


async def test_an_ingest_that_crashes_still_ends_on_its_own_root(unfiltered_spans):
    """The sixth ending, and the reason `IngestOutcome` does not own a name for
    it: `span` stamps the exception type and sets the status to ERROR itself, so
    a crash can never be read as one of the five ordinary outcomes.

    Read with the sampler removed, because that is the only place an ingest
    trace exists — and it still matters: an unexported trace is what the
    reversal condition would restore, so it must not be silently wrong.
    """
    from nora.ingest import pipeline as pipeline_module

    async def _boom(**_kwargs):
        raise RuntimeError("custody exploded")

    monkeypatch_free_metrics = Metrics()
    original = pipeline_module.media_stage.run
    pipeline_module.media_stage.run = _boom  # type: ignore[assignment]
    try:
        pipeline = _pipeline(
            memory=FakeMemory(), persons=_known_persons(),
            policy=TriggerTextPolicy("nora.000"), spawn=lambda c: c.close(),
            allowlist=_policy_of(), metrics=monkeypatch_free_metrics,
        )
        with pytest.raises(RuntimeError):
            await pipeline.run(message=_msg(body="hi"))
    finally:
        pipeline_module.media_stage.run = original  # type: ignore[assignment]

    root = _only_root(unfiltered_spans)
    assert root.attributes["nora.outcome"] == "RuntimeError"
    assert root.attributes["nora.outcome"] not in pipeline_module._OUTCOMES
    assert root.status.status_code.name == "ERROR"
    assert monkeypatch_free_metrics.get("nora_ingest_outcomes_total") == 0, \
        "a crash is not one of the five outcomes and must not be counted as one"


# --- shared file links ----------

_DRIVE_ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz012345"
_DOC_URL = f"https://docs.google.com/document/d/{_DRIVE_ID}/edit?usp=sharing"


@pytest.fixture
def _no_network(monkeypatch):
    """Any socket opened while this is active fails the test: recording a
    link must make no network call."""
    import socket

    def refuse(*_a, **_k):
        raise AssertionError("ingest opened a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)


async def test_a_shared_file_link_is_recorded_beside_its_message_and_fed_as_a_handle(_no_network):
    """The stage's whole job, end to end through the pipeline: the row keeps
    the body VERBATIM and gains the ref, and the Honcho feed carries the
    handle, never the URL (R-18)."""
    import json

    class _TextMemory(FakeMemory):
        async def feed(self, *, peer_id, text, conversation_id="", metadata=None):
            self.calls.append(("feed", text))
            return True

    audit = FakeRecordStore()
    memory = _TextMemory()
    fed: list = []
    pipeline = _pipeline(
        audit=audit, memory=memory, persons=_known_persons(),
        policy=TriggerTextPolicy("nora.000"), spawn=fed.append,
        allowlist=_policy_of(),
    )
    await pipeline.run(message=_msg(body=f"plan here {_DOC_URL} ok"))
    for coro in fed:
        await coro

    row = audit.rows[-1]
    assert row["body"] == f"plan here {_DOC_URL} ok"
    stored = json.loads(row["external_files_json"])
    assert stored == [{"provider": "google_drive", "file_id": _DRIVE_ID,
                       "url": _DOC_URL, "label": "Google Doc link"}]
    texts = [c[1] for c in memory.calls if c[0] == "feed"]
    assert texts and all(_DRIVE_ID not in t and "[Google Doc link " in t for t in texts)


async def test_a_message_without_a_file_link_stores_no_refs():
    audit = FakeRecordStore()
    pipeline = _pipeline(
        audit=audit, memory=FakeMemory(), persons=_known_persons(),
        policy=TriggerTextPolicy("nora.000"), spawn=lambda c: c.close(),
        allowlist=_policy_of(),
    )
    await pipeline.run(message=_msg(body="https://drive.google.com/drive/folders/x and hi"))
    assert audit.rows[-1]["external_files_json"] is None


@pytest.mark.parametrize("platform", ["signal", "discord", "whatsapp", "instagram"])
async def test_the_stage_records_links_on_every_platform(platform, _no_network):
    from nora.ingest.stages import external_files as stage

    msg = dataclasses.replace(_msg(body=f"see {_DOC_URL}"), platform=platform)
    out = await stage.run(message=msg)
    assert [r.file_id for r in out.external_files] == [_DRIVE_ID]
    assert out.body == msg.body

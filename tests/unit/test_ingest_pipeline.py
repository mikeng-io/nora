"""`IngestPipeline` wiring: does it call the four stages in the right order
and assemble the right `IngestResult`? Per-stage failure behaviour is
`test_ingest_stages.py`'s job; this file is about sequencing.

`IngestPipeline.run` takes an already-normalized `message`, not a raw event —
parsing (the former stage 1, "row 1" of spec §4.3's failure table) moved onto
the adapter (`SignalAdapter.listen`, Trap B in
docs/superpowers/plans/2026-08-05-nora-phase4-platforms.md). The drop-case
coverage that test used to give — a DM, a sync message, a self-send, an empty
body all coming back `None` — now lives in `tests/test_signal_adapter.py`,
against the adapter that actually does that parsing.
"""

from __future__ import annotations

import dataclasses
import hashlib

import pytest

from nora.domain.media import MediaRef
from nora.domain.models import NormalizedMessage, PersonRef
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

    async def ensure_peer(self, *, peer_id: str) -> None:
        self.calls.append(("ensure_peer", peer_id))

    async def feed(self, *, peer_id: str, text: str, conversation_id: str = "",
                   metadata: dict | None = None) -> bool:
        self.calls.append(("feed", peer_id))
        return True

    async def peer_card(self, *, peer_id: str) -> list[str]:
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
    """Row 2: a failed audit write drops the message before identity,
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


# ── the two policy gates (design/10 §4.3) ───────────────────────────────────


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
        'id = "allowed"\npersons = "*"\n'
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
        'id = "g1"\npersons = ["someone-else"]\n'
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
    """The state on the day this shipped: the composition root wires no blob
    store yet. The message must still be audited, with the placeholder and no
    handle, and `nora_media_custody_total{status="not_configured"}` is the only
    place that loss is visible."""
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


async def test_a_voice_note_is_transcribed_at_custody_and_the_transcript_rides_the_body():
    """A voice note is conversation CONTENT — the ingest path transcribes it
    inline and the words ride the body beside the handle, so Nora sees them
    without ever calling a tool. A song (`voice_note=False`) stays on the
    bare-handle path even when a transcriber is wired (the gate is the
    `voiceNote` flag, NOT the audio/* MIME type).
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
    assert row["body"] == f"[voice note {digest[:6]}: 喂你今日得唔得閒啊]"
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
    message from ever transcribing or being recorded (regression for the
    `blobs.get` round trip that could raise uncaught and drop the message)."""
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
    assert store.rows[0]["body"] == f"[voice note {digest[:6]}: 喂你今日得唔得閒啊]"
    assert transcriber.calls[0][0] == AUDIO
    assert metrics.get("nora_speech_transcriptions_total", status="ok") == 1


# ---------------------------------------------------------------------------
# WHAT THE TRACE SAYS (issue #64, ingest half).
#
# The root span carried a platform and a room key and nothing else, and only
# the two REFUSING exits stamped an outcome at all — so a message that passed
# through cleanly, which is every ambient message, produced a root with six
# named children and no statement of what the ingest DID. In Langfuse that is
# an empty trace, and the operator was looking at a page of them.
#
# These drive the REAL pipeline and read the spans it ACTUALLY EXPORTED
# (`exported_spans`, a real `InMemorySpanExporter` behind a real
# `TracerProvider`). Nothing here asserts that a helper was called: a test of
# that shape would have stayed green for the whole of the defect, because
# `_outcome` WAS being called — twice, on two of the exits, and never on the
# one that runs a thousand times a day.
# ---------------------------------------------------------------------------


def _spans_by_name(exporter) -> dict:
    out: dict[str, list] = {}
    for recorded in exporter.get_finished_spans():
        out.setdefault(recorded.name, []).append(recorded)
    return out


def _root(exporter):
    """The span an operator sees without expanding anything.

    Identified by `parent is None` rather than by name, so a change that
    re-parented the root would redden here instead of quietly moving the
    outcome onto a child — which is the shape of the original defect.
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
        f'id = "{room}"\npersons = {persons}\n'
    )
    return load_policy(p)


def _known_persons() -> Persons:
    return Persons(
        by_person={"miles": PersonRef(person_id="miles", name="Miles", honcho_peer_id="miles")},
        index={("signal", "+852111"): "miles"},
    )


async def _drive_recorded():
    """An ordinary ambient message: allowlisted room, no trigger. THE PATH THAT
    HAD NOTHING."""
    pipeline = _pipeline(
        memory=FakeMemory(), persons=_known_persons(),
        policy=TriggerTextPolicy("nora.000"), spawn=lambda c: c.close(),
        allowlist=_policy_of(),
    )
    return await pipeline.run(message=_msg(body="just chatting"))


async def _drive_engaged():
    pipeline = _pipeline(
        memory=FakeMemory(), persons=_known_persons(),
        policy=TriggerTextPolicy("nora.000"), spawn=lambda c: c.close(),
        allowlist=_policy_of(),
    )
    return await pipeline.run(message=_msg(body="nora.000 what's up"))


async def _drive_scope_refused():
    pipeline = _pipeline(
        memory=FakeMemory(), persons=_known_persons(),
        policy=TriggerTextPolicy("nora.000"), spawn=lambda c: c.close(),
        allowlist=_policy_of(room="some-other-room"),
    )
    return await pipeline.run(message=_msg(body="nora.000 hi"))


async def _drive_audit_refused(monkeypatch):
    store = FakeRecordStore()

    async def _boom(**_kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(store, "append", _boom)
    pipeline = _pipeline(
        audit=store, memory=FakeMemory(), persons=_known_persons(),
        policy=TriggerTextPolicy("nora.000"), spawn=lambda c: c.close(),
        allowlist=_policy_of(),
    )
    return await pipeline.run(message=_msg(body="nora.000 hi"))


async def _drive_sender_not_allowed():
    pipeline = _pipeline(
        memory=FakeMemory(), persons=_known_persons(),
        policy=TriggerTextPolicy("nora.000"), spawn=lambda c: c.close(),
        allowlist=_policy_of(persons='["someone-else"]'),
    )
    return await pipeline.run(message=_msg(body="nora.000 hi"))


async def test_an_ordinary_ambient_message_says_so_on_the_root(exported_spans):
    """THE DEFECT, as a property. A message that passes cleanly is the ambient
    majority and it stamped NOTHING — the root said which room, and left the
    reader to infer from six children that all of them ran.

    PROBE: delete `_outcome("engaged" if decision.engage else "recorded")` in
    `_run`'s final `else` and this reddens with the attribute simply absent.
    """
    result = await _drive_recorded()

    assert result.message is not None            # positive control: it really ran
    root = _root(exported_spans)
    assert root.name == "ingest"
    assert root.attributes["nora.outcome"] == "recorded"


async def test_a_tagged_message_is_recorded_as_engaged(exported_spans):
    """The same attribute, a different value — so the test above is reading the
    ingest rather than a constant. `engaged` is the ONE ingest whose story
    continues, into the engagement trace."""
    result = await _drive_engaged()

    assert result.decision is not None and result.decision.engage is True
    assert _root(exported_spans).attributes["nora.outcome"] == "engaged"


async def test_a_denied_room_leaves_a_root_that_says_it_was_refused(exported_spans):
    """This exit already stamped an outcome — the open question was WHERE it
    landed, and the answer is the root. This pins it: the assertion is on the
    span whose parent is `None`, and the child is asserted CLEAN.

    PROBE: move `_outcome("scope_refused")` inside the `with span("ingest.scope")`
    block and the outcome lands on the child — this reddens, while a test that
    only checked «some span carries it» would stay green.
    """
    result = await _drive_scope_refused()

    assert result == IngestResult(message=None, decision=None)
    root = _root(exported_spans)
    assert root.attributes["nora.outcome"] == "scope_refused"
    scope_child = _spans_by_name(exported_spans)["ingest.scope"][0]
    assert "nora.outcome" not in (scope_child.attributes or {}), \
        "the outcome belongs on the root, not on the stage that happened to run last"


async def test_a_failed_audit_write_leaves_a_root_that_says_it_was_refused(
    exported_spans, monkeypatch,
):
    """The other refusal, and the one that is a FAILURE rather than a policy
    decision — a dropped message is exactly what an operator goes looking for."""
    result = await _drive_audit_refused(monkeypatch)

    assert result == IngestResult(message=None, decision=None)
    root = _root(exported_spans)
    assert root.attributes["nora.outcome"] == "audit_refused"
    assert "nora.outcome" not in (
        _spans_by_name(exported_spans)["ingest.audit"][0].attributes or {}
    )


async def test_a_refused_sender_keeps_the_trigger_it_matched(exported_spans):
    """Two facts, deliberately on two attributes. The pipeline REPLACES the
    decision with `sender_not_allowed`, so recording only the final disposition
    would lose that the message was a real tag — which is the whole reason this
    exit is interesting rather than routine.

    PROBE: delete the `annotate(nora.engage_reason=...)` after the gate and the
    second assertion reddens; delete `_outcome("sender_not_allowed")` and the
    first does.
    """
    result = await _drive_sender_not_allowed()

    assert result.decision is not None and result.decision.engage is False
    root = _root(exported_spans)
    assert root.attributes["nora.outcome"] == "sender_not_allowed"
    assert root.attributes["nora.engage_reason"] == "trigger_text"


async def test_a_gate_that_degraded_closed_does_not_read_as_an_untagged_message(
    exported_spans,
):
    """R-5's cost, made visible. The gate stage swallows a policy failure and
    returns `engage=False`, so a classifier outage renders IDENTICALLY to an
    ordinary chat message — `outcome=recorded` either way. `nora.engage_reason`
    is the only thing that separates them.

    PROBE: delete the `annotate` after `ingest.gate`.
    """
    class _BrokenPolicy:
        async def should_engage(self, *, message):
            raise RuntimeError("classifier down")

    pipeline = _pipeline(
        memory=FakeMemory(), persons=_known_persons(),
        policy=_BrokenPolicy(), spawn=lambda c: c.close(), allowlist=_policy_of(),
    )
    await pipeline.run(message=_msg(body="nora.000 hi"))

    root = _root(exported_spans)
    assert root.attributes["nora.outcome"] == "recorded"
    assert root.attributes["nora.engage_reason"] == "policy_error"


async def test_the_root_says_what_arrived_and_whether_the_sender_is_known(
    exported_spans,
):
    """The rest of «tell one trace from another at a glance». `attachments` is
    what the PLATFORM said arrived, read before custody runs, so a slow
    `ingest.media` child explains itself; `person_known` is a BOOL, never the
    person_id, and it is the one thing `nora_unmapped_senders_total` cannot say
    about THIS message.

    PROBE: drop either key from the root `span(...)` call, or the `annotate`
    after `ingest.identity`.
    """
    pipeline = _pipeline(
        memory=FakeMemory(), persons=Persons(by_person={}, index={}),
        policy=TriggerTextPolicy("nora.000"), spawn=lambda c: c.close(),
        allowlist=_policy_of(),
    )
    msg = dataclasses.replace(
        _msg(body="[image]"),
        media=(MediaRef(platform_id="att1", media_type="image/jpeg", voice_note=False),),
    )
    await pipeline.run(message=msg)

    root = _root(exported_spans)
    assert root.attributes["nora.attachments"] == 1
    assert root.attributes["nora.conversation_type"] == "group"
    assert root.attributes["nora.person_known"] is False


async def test_a_known_sender_is_reported_as_known(exported_spans):
    """The other value of the same bool, so the assertion above reads the
    identity stage rather than a default."""
    await _drive_recorded()
    assert _root(exported_spans).attributes["nora.person_known"] is True


async def test_every_exit_stamps_an_outcome_on_the_root(exported_spans, monkeypatch):
    """The property the fix exists for, over ALL the exits at once rather than
    one test per exit that could each be deleted separately.

    Positive control beside the presence-assertion: `ingest.scope` must have run
    in every case, so «no outcome anywhere» cannot pass as «no ingest happened».
    """
    from nora.ingest.pipeline import _OUTCOMES

    seen = set()
    for drive in (
        _drive_recorded, _drive_engaged, _drive_scope_refused,
        _drive_sender_not_allowed, lambda: _drive_audit_refused(monkeypatch),
    ):
        exported_spans.clear()
        await drive()
        root = _root(exported_spans)
        assert "ingest.scope" in _spans_by_name(exported_spans), \
            "positive control: the pipeline must actually have run"
        assert "nora.outcome" in root.attributes, \
            f"{drive} left a root that says nothing about what happened"
        seen.add(root.attributes["nora.outcome"])

    assert seen <= _OUTCOMES, f"an exit invented an outcome: {seen - _OUTCOMES}"


async def test_every_declared_outcome_is_reachable(exported_spans, monkeypatch):
    """The vocabulary is DERIVED from the code, and this is the half that keeps
    it honest in the other direction: a name declared in `IngestOutcome` that no
    exit ever stamps is the span equivalent of a metric with no call site — it
    renders as a filter value that is genuinely always empty.

    The expected set is read off the `Literal` with `get_args`, never
    hand-copied, so adding an outcome without an exit — or an exit without an
    outcome — reddens here rather than drifting.
    """
    from nora.ingest.pipeline import _OUTCOMES

    seen = set()
    for drive in (
        _drive_recorded, _drive_engaged, _drive_scope_refused,
        _drive_sender_not_allowed, lambda: _drive_audit_refused(monkeypatch),
    ):
        exported_spans.clear()
        await drive()
        seen.add(_root(exported_spans).attributes["nora.outcome"])

    assert seen == _OUTCOMES, (
        f"declared but unreachable: {_OUTCOMES - seen}; "
        f"stamped but undeclared: {seen - _OUTCOMES}"
    )


async def test_an_ingest_that_crashes_ends_on_the_root_as_an_error(
    exported_spans, monkeypatch,
):
    """The sixth ending, and the reason `IngestOutcome` does not own a name for
    it: `span` already stamps the exception type and sets the status to ERROR.
    A crash must never be readable as one of the five ordinary outcomes.
    """
    from nora.ingest import pipeline as pipeline_module

    async def _boom(**_kwargs):
        raise RuntimeError("custody exploded")

    monkeypatch.setattr(pipeline_module.media_stage, "run", _boom)
    pipeline = _pipeline(
        memory=FakeMemory(), persons=_known_persons(),
        policy=TriggerTextPolicy("nora.000"), spawn=lambda c: c.close(),
        allowlist=_policy_of(),
    )

    with pytest.raises(RuntimeError):
        await pipeline.run(message=_msg(body="hi"))

    root = _root(exported_spans)
    assert root.attributes["nora.outcome"] == "RuntimeError"
    assert root.attributes["nora.outcome"] not in pipeline_module._OUTCOMES
    assert root.status.status_code.name == "ERROR"


async def test_no_identifier_reaches_the_collector(exported_spans):
    """R-73 — A TRACE IS AN EGRESS. Every attribute of every span this pipeline
    opens is screened with the repo's own predicate, not with a reviewer's eye.

    The sender is a real E.164 and the room a real Signal group key, and the
    POSITIVE CONTROL is what makes the null result mean anything: the screen is
    first shown to FIRE on that sender id, so «it found nothing on the spans»
    cannot be «the screen cannot see this».
    """
    from nora.agent.egress import scan_identifiers

    sender = "+85298765432"
    room = "Lf2L9zqeNUahRsOz6BA+sTB8aWfbuvqu9vOhpvmJi34="
    assert scan_identifiers(sender) == "phone", \
        "positive control: the screen must be able to see this identifier"

    pipeline = _pipeline(
        memory=FakeMemory(), persons=Persons(by_person={}, index={}),
        policy=TriggerTextPolicy("nora.000"), spawn=lambda c: c.close(),
        allowlist=_policy_of(room=room),
    )
    msg = dataclasses.replace(
        _msg(sender_id=sender, body="nora.000 my number is " + sender),
        conversation_id=room,
    )
    await pipeline.run(message=msg)

    exported = exported_spans.get_finished_spans()
    assert exported, "positive control: spans were actually exported"
    for recorded in exported:
        for key, value in (recorded.attributes or {}).items():
            assert scan_identifiers(str(value)) is None, \
                f"{recorded.name}.{key} carries an identifier: {value!r}"


async def test_one_ambient_message_is_exactly_one_trace(exported_spans):
    """The decision this change deliberately KEPT: an ingest is a unit of work
    and it gets a trace. What it must not be is SEVERAL — every stage span
    shares the root's trace id and there is exactly one root.

    PROBE: delete the `with span("ingest", ...)` wrapper in `run` and the six
    stage spans become six rootless traces.
    """
    await _drive_engaged()

    exported = exported_spans.get_finished_spans()
    names = {s.name for s in exported}
    assert {"ingest", "ingest.scope", "ingest.identity", "ingest.media",
            "ingest.audit", "ingest.memory_feed", "ingest.gate"} == names
    assert len({s.context.trace_id for s in exported}) == 1
    root = _root(exported_spans)
    for recorded in exported:
        if recorded is not root:
            assert recorded.parent.span_id == root.context.span_id


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
    pipeline = _pipeline(
        audit=store, memory=FakeMemory(), persons=_known_persons(),
        policy=TriggerTextPolicy("nora.000"), spawn=lambda c: c.close(),
        allowlist=_policy_of(),
    )

    result = await pipeline.run(message=_msg(body="nora.000 hi"))

    assert result.decision is not None and result.decision.engage is True
    assert len(store.rows) == 1, "the message must still have been recorded"


async def test_an_undeclared_outcome_is_still_recorded_and_reported(
    exported_spans, caplog,
):
    """The vocabulary check LOGS; it never withholds. The defect being fixed is
    a root that says nothing, so refusing to stamp an unexpected value would
    reintroduce exactly that for the case least understood. `make gate`'s mypy
    does not reach `nora/ingest`, so this runtime check is the only guard the
    `Literal` actually has.

    PROBE: make `_outcome` `return` early on an unknown value, or delete the
    `log.warning`.
    """
    import logging as _logging

    from nora.ingest.pipeline import _outcome
    from nora.obs.tracing import span as _span

    with caplog.at_level(_logging.WARNING, logger="nora.ingest.pipeline"), _span("ingest"):
        _outcome("something_nobody_declared")      # type: ignore[arg-type]

    root = _root(exported_spans)
    assert root.attributes["nora.outcome"] == "something_nobody_declared"
    assert "not in the declared vocabulary" in caplog.text

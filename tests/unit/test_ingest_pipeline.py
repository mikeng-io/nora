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

from nora.domain.media import MediaRef
from nora.domain.models import NormalizedMessage, PersonRef
from nora.domain.ports import MemoryHealth
from nora.ingest.pipeline import IngestPipeline, IngestResult
from nora.ingest.policy import TriggerTextPolicy
from nora.obs.metrics import Metrics
from nora.persons import Persons
from nora.policy import permissive_policy
from nora.testing import FakeBlobStore, FakeMediaSource, FakeRecordStore


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
              media_source=None, blobs=None, metrics=None):
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
        media_source=media_source,
        blobs=blobs,
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
        audit=store, media_source=FakeMediaSource({"att1": JPEG}), blobs=blobs,
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

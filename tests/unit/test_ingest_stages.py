"""One test per row of the ingest failure-behaviour table.

`nora.testing.FakeRecordStore` is the audit double throughout — the project
rule is that a port never gains a second implementation, including a test
fake, without a shared contract suite both pass, and `FakeRecordStore` already
has one (`tests/contract/test_audit_store_contract.py`). `MemoryStore` has no
such suite yet, so `FakeMemory` below stays local to this test module rather
than joining `nora.testing`.
"""

from __future__ import annotations

from nora.domain.models import NormalizedMessage, PersonRef
from nora.domain.ports import EngageDecision, MemoryHealth
from nora.ingest.stages import audit, gate, identity, memory_feed
from nora.obs.metrics import Metrics
from nora.persons import Persons
from nora.testing import FakeRecordStore


def _msg(sender_id: str = "+852111", body: str = "hello") -> NormalizedMessage:
    return NormalizedMessage(
        platform="signal", conversation_id="g1", conversation_type="group",
        sender_id=sender_id, sender_name="Miles", timestamp=1, body=body, raw={},
    )


class FakeMemory:
    """Local `MemoryStore` double for Honcho — see module docstring."""

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


class _RaisingPolicy:
    """A stand-in for the future `ClassifierPolicy`: `TriggerTextPolicy`
    cannot raise (it does no I/O), but a network-backed policy will."""

    async def should_engage(self, *, message: NormalizedMessage) -> EngageDecision:
        raise RuntimeError("classifier backend timed out")


# --- row 2: audit -----------------------------------------------------------


async def test_audit_failure_drops_the_message_and_never_raises(monkeypatch):
    """The listener must survive a full disk. `on_event` is awaited by the SSE
    reader, so a raise here stops ingest for every conversation on this
    adapter — which is why stage 2's documented behaviour is drop, not fail."""
    store = FakeRecordStore()

    async def _boom(**_kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(store, "append", _boom)
    metrics = Metrics()

    recorded = await audit.run(message=_msg(), audit=store, metrics=metrics)

    assert recorded is None
    assert store.rows == []


async def test_audit_failure_counts_nora_errors_total_stage_ambient(monkeypatch):
    store = FakeRecordStore()

    async def _boom(**_kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(store, "append", _boom)
    metrics = Metrics()

    await audit.run(message=_msg(), audit=store, metrics=metrics)

    assert metrics.get("nora_errors_total", stage="ambient") == 1


# --- row 3: identity ---------------------------------------------------------


async def test_an_unmapped_sender_is_audited_but_never_fed():
    """Peer mapping is manual and unmapped senders are never fed to the
    semantic store. The audit row is still written — the audit record is not
    conditional on identity."""
    store = FakeRecordStore()
    fake_memory = FakeMemory()
    metrics = Metrics()
    persons = Persons(by_person={}, index={})   # nobody mapped
    spawned: list = []
    msg = _msg(sender_id="+85299999999")

    person = await identity.run(message=msg, persons=persons, metrics=metrics)
    assert person is None

    recorded = await audit.run(
        message=msg, audit=store, metrics=metrics, person=person
    )
    assert recorded == 1
    assert len(store.rows) == 1
    assert store.rows[0]["person_id"] is None, "unmapped must be recorded as NULL"

    # The whole point: an unmapped sender is otherwise SYMPTOMLESS. It is
    # audited, the gate still runs, and the prefill still shows the sender's
    # platform display name — so without this counter "identity.toml is wrong"
    # looks exactly like "nobody talked to her".
    assert metrics.get("nora_unmapped_senders_total", platform="signal") == 1

    await memory_feed.run(
        message=msg, person=person, memory=fake_memory, metrics=metrics,
        spawn=spawned.append,
    )

    assert spawned == []
    assert fake_memory.calls == []


# --- row 4: memory_feed -------------------------------------------------------


async def test_memory_feed_runs_off_the_listener_path():
    """Asserts `spawn` was called rather than the coroutine awaited inline.
    `on_event` is awaited by the SSE reader (listener.py), so an inline
    200-800ms Honcho call stalls the socket read for every adapter in the
    process."""
    person = PersonRef(person_id="miles", name="Miles", honcho_peer_id="miles")
    msg = _msg()
    fake_memory = FakeMemory()
    metrics = Metrics()
    spawned: list = []

    await memory_feed.run(
        message=msg, person=person, memory=fake_memory, metrics=metrics,
        spawn=spawned.append,
    )

    # Nothing ran synchronously: the feed was handed to spawn, not awaited.
    assert len(spawned) == 1
    assert fake_memory.calls == []

    # Running the spawned coroutine off-path confirms it actually does the feed.
    await spawned[0]
    assert fake_memory.calls == [("ensure_peer", "miles"), ("feed", "miles")]
    assert metrics.get("honcho_feed_success") == 1


# --- row 5: gate ---------------------------------------------------------------


async def test_a_policy_error_becomes_a_no_engage_decision():
    """TriggerTextPolicy cannot raise, but ClassifierPolicy will. Under R-5 a
    gate that propagates would take the listener down; the decision must
    degrade closed."""
    metrics = Metrics()

    decision = await gate.run(message=_msg(), policy=_RaisingPolicy(), metrics=metrics)

    assert decision.engage is False
    assert decision.reason == "policy_error"


async def test_a_policy_error_counts_nora_errors_total_stage_engage_gate():
    metrics = Metrics()

    await gate.run(message=_msg(), policy=_RaisingPolicy(), metrics=metrics)

    assert metrics.get("nora_errors_total", stage="engage_gate") == 1

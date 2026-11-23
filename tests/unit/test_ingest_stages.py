"""One test per row of the ingest failure-behaviour table.

`nora.testing.FakeRecordStore` is the audit double throughout — the project
rule is that a port never gains a second implementation, including a test
fake, without a shared contract suite both pass, and `FakeRecordStore` already
has one (`tests/contract/test_audit_store_contract.py`).
"""

from __future__ import annotations

from nora.access import IdentityAccess
from nora.domain.models import NormalizedMessage
from nora.domain.ports import EngageDecision
from nora.ingest.stages import audit, gate, identity
from nora.obs.metrics import Metrics
from nora.persons import Persons
from nora.policy import permissive_policy
from nora.testing import FakeRecordStore


def _msg(sender_id: str = "+852111", body: str = "hello") -> NormalizedMessage:
    return NormalizedMessage(
        platform="signal", conversation_id="g1", conversation_type="group",
        sender_id=sender_id, sender_name="Miles", timestamp=1, body=body, raw={},
    )


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


async def test_an_unmapped_sender_is_audited_with_no_person():
    """Person mapping is manual. The audit row is still written — the audit
    record is not conditional on identity."""
    store = FakeRecordStore()
    metrics = Metrics()
    persons = Persons(by_person={}, index={})   # nobody mapped
    msg = _msg(sender_id="+85299999999")

    person = await identity.run(message=msg, metrics=metrics, access=IdentityAccess.for_files(
        persons=persons, policy=permissive_policy()))
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

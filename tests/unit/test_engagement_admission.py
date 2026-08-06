"""`admission.run` — R-9 (debounce), R-10 (in-flight lock), R-12 (self-tag),
R-29 (per-conversation FIFO queue + flood bound).

See `nora.engagement.stages.admission`'s module docstring for the R-10
ownership split this file assumes: `run` only ever ACQUIRES (adds `key` to
`in_flight`), and only on the `RUN_NOW` verdict. Nothing here releases — that
is the pipeline's `finally`, exercised by the pipeline's own test suite, not
this one. The `QUEUED` verdict does NOT touch `in_flight`: the conversation's
lock already belongs to the turn that is running.

R-12 takes a resolved `PlatformIdentity`, not an adapter — the caller
(`EngagementPipeline.run`) resolves it once via `adapter.resolve_identity()`
before calling this stage. `identity.aliases` is a `frozenset`, so this file
tests self-tag refusal by *membership*, matching `nora.domain.ports`'s module
docstring on why R-12 must never be an equality check against a single id.
"""

from __future__ import annotations

from nora.domain.models import NormalizedMessage
from nora.domain.ports import PlatformIdentity
from nora.engagement.stages import admission
from nora.engagement.stages.admission import Admission
from nora.obs.metrics import Metrics
from nora.tag import Debounce

QUEUE_MAX = 16


class _FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def _identity(*, primary: str = "+852000", aliases: frozenset[str] = frozenset()) -> (
    PlatformIdentity
):
    return PlatformIdentity(platform="signal", primary=primary, aliases=aliases)


def _msg(sender_id: str = "+852111", conversation_id: str = "g1") -> NormalizedMessage:
    return NormalizedMessage(
        platform="signal", conversation_id=conversation_id, conversation_type="group",
        sender_id=sender_id, sender_name="Miles", timestamp=1, body="nora.000 hi", raw={},
    )


def _run(msg, identity, debounce, in_flight, metrics, *, queue_depth=0, queue_max=QUEUE_MAX):
    return admission.run(
        message=msg, identity=identity, debounce=debounce, in_flight=in_flight,
        queue_depth=queue_depth, queue_max=queue_max, metrics=metrics,
    )


def test_a_self_tag_is_refused():
    verdict = _run(
        _msg(sender_id="+852000"), _identity(primary="+852000"),
        Debounce(30, clock=_FakeClock()), set(), Metrics(),
    )

    assert verdict is Admission.REJECTED


def test_a_self_tag_arriving_under_a_non_primary_alias_is_refused():
    """R-12 must test membership in `aliases`, not equality against `primary` —
    this is the case that would break if it didn't (see
    `nora.domain.ports`'s module docstring and this stage's own)."""
    verdict = _run(
        _msg(sender_id="uuid-abc"),
        _identity(primary="+852000", aliases=frozenset({"uuid-abc"})),
        Debounce(30, clock=_FakeClock()), set(), Metrics(),
    )

    assert verdict is Admission.REJECTED


def test_a_debounced_tag_is_refused_and_counted():
    clock = _FakeClock()
    debounce = Debounce(30, clock=clock)
    metrics = Metrics()
    in_flight: set[tuple[str, str]] = set()
    identity = _identity()

    first = _run(_msg(), identity, debounce, in_flight, metrics)
    in_flight.clear()   # simulate the pipeline releasing the first tag's lock
    clock.t += 1         # still inside the debounce window
    second = _run(_msg(), identity, debounce, in_flight, metrics)

    assert first is Admission.RUN_NOW
    assert second is Admission.REJECTED
    assert metrics.get("nora_tags_debounced_total") == 1


def test_a_second_concurrent_tag_for_the_same_conversation_is_queued_not_dropped():
    """R-29: the in-flight key from the first tag is still held (the pipeline
    has not released it), so a second tag for the same conversation is QUEUED —
    the whole point of this change — rather than dropped. Admission does not
    touch `in_flight` for the queued tag; the running turn still owns it."""
    metrics = Metrics()
    in_flight: set[tuple[str, str]] = set()
    identity = _identity()

    first = _run(_msg(), identity, Debounce(0, clock=_FakeClock()), in_flight, metrics)
    second = _run(
        _msg(), identity, Debounce(0, clock=_FakeClock()), in_flight, metrics, queue_depth=0,
    )

    assert first is Admission.RUN_NOW
    assert second is Admission.QUEUED
    assert metrics.get("nora_tags_queued_total") == 1
    # A queued tag IS accepted for handling, so it is counted like a run-now one.
    assert metrics.get("nora_tags_total", platform="signal") == 2
    # It never went through the old drop counter.
    assert metrics.get("nora_tags_dropped_total", reason="in_flight") == 0
    # The queued tag left the first tag's lock untouched.
    assert in_flight == {("signal", "g1")}


def test_a_tag_arriving_at_a_full_queue_is_dropped_with_its_own_counter():
    """R-29's flood bound: when the queue is already at `queue_max`, the NEWEST
    arrival is shed — a distinct outcome and a distinct counter from both the
    debounce drop and the (now removed) in-flight drop."""
    metrics = Metrics()
    in_flight: set[tuple[str, str]] = {("signal", "g1")}
    identity = _identity()

    verdict = _run(
        _msg(), identity, Debounce(0, clock=_FakeClock()), in_flight, metrics,
        queue_depth=QUEUE_MAX, queue_max=QUEUE_MAX,
    )

    assert verdict is Admission.QUEUE_FULL
    assert metrics.get("nora_tags_dropped_total", reason="queue_full") == 1
    # A shed flood tag is NOT counted as accepted-for-handling and NOT queued.
    assert metrics.get("nora_tags_total", platform="signal") == 0
    assert metrics.get("nora_tags_queued_total") == 0


def test_the_last_slot_before_the_bound_still_queues():
    """Boundary: depth == max_depth - 1 is the last acceptable slot; it queues,
    the next (depth == max_depth) is the one that is shed."""
    metrics = Metrics()
    in_flight: set[tuple[str, str]] = {("signal", "g1")}
    identity = _identity()

    verdict = _run(
        _msg(), identity, Debounce(0, clock=_FakeClock()), in_flight, metrics,
        queue_depth=QUEUE_MAX - 1, queue_max=QUEUE_MAX,
    )

    assert verdict is Admission.QUEUED


def test_a_different_conversation_is_admitted_concurrently():
    """Proves the lock is per-conversation, not global: a second conversation
    runs now rather than queueing behind the first."""
    metrics = Metrics()
    in_flight: set[tuple[str, str]] = set()
    debounce = Debounce(0, clock=_FakeClock())
    identity = _identity()

    first = _run(_msg(conversation_id="g1"), identity, debounce, in_flight, metrics)
    second = _run(_msg(conversation_id="g2"), identity, debounce, in_flight, metrics)

    assert first is Admission.RUN_NOW
    assert second is Admission.RUN_NOW
    assert in_flight == {("signal", "g1"), ("signal", "g2")}

"""`admission.run` — R-9 (debounce), R-10 (in-flight lock), R-12 (self-tag),
R-30 (per-conversation FIFO queue + flood bound).

See `nora.engagement.stages.admission`'s module docstring for the R-10
ownership split this file assumes: `run` only ever ACQUIRES (`slots.acquire`),
and only on the `RUN_NOW` verdict. Nothing here releases — that is the
pipeline's `finally`, exercised by the pipeline's own test suite, not this one.
The `QUEUED` verdict does NOT acquire: the conversation's lock already belongs
to the turn that is running.

The lock and the queue reach this stage as one `ConversationSlots`, so a "full
queue" is set up here by genuinely enqueueing that many waiting turns rather
than by passing a `queue_depth` integer the real caller could never disagree
with. The depth and the bound have one source.

R-12 takes a resolved `PlatformIdentity`, not an adapter — the caller
(`EngagementPipeline.run`) resolves it once via `adapter.resolve_identity()`
before calling this stage. `identity.aliases` is a `frozenset`, so this file
tests self-tag refusal by *membership*, matching `nora.domain.ports`'s module
docstring on why R-12 must never be an equality check against a single id.
"""

from __future__ import annotations

from nora.domain.models import NormalizedMessage
from nora.domain.ports import PlatformIdentity
from nora.engagement.slots import ConversationSlots
from nora.engagement.stages import admission
from nora.engagement.stages.admission import Admission
from nora.obs.metrics import Metrics
from nora.tag import Debounce

QUEUE_MAX = 16
G1 = ("signal", "g1")
G2 = ("signal", "g2")


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


async def _queued_turn() -> None:
    """A stand-in for a waiting tag's turn. Never dispatched by these tests —
    admission only ever reads the depth."""
    return None


def _slots(*, maxlen: int = QUEUE_MAX, held=(), depth: int = 0) -> ConversationSlots:
    """Slots in a given state: `held` conversations already running a turn, and
    `depth` turns genuinely waiting behind `G1`."""
    slots = ConversationSlots(maxlen=maxlen, spawn=lambda coro: coro.close())
    for key in held:
        slots.acquire(key)
    for _ in range(depth):
        slots.enqueue(G1, _queued_turn)
    return slots


def _run(msg, identity, debounce, slots, metrics):
    return admission.run(
        message=msg, identity=identity, debounce=debounce, slots=slots, metrics=metrics,
    )


def test_a_self_tag_is_refused():
    verdict = _run(
        _msg(sender_id="+852000"), _identity(primary="+852000"),
        Debounce(30, clock=_FakeClock()), _slots(), Metrics(),
    )

    assert verdict is Admission.REJECTED


def test_a_self_tag_arriving_under_a_non_primary_alias_is_refused():
    """R-12 must test membership in `aliases`, not equality against `primary` —
    this is the case that would break if it didn't (see
    `nora.domain.ports`'s module docstring and this stage's own)."""
    verdict = _run(
        _msg(sender_id="uuid-abc"),
        _identity(primary="+852000", aliases=frozenset({"uuid-abc"})),
        Debounce(30, clock=_FakeClock()), _slots(), Metrics(),
    )

    assert verdict is Admission.REJECTED


def test_a_debounced_tag_is_refused_and_counted():
    clock = _FakeClock()
    debounce = Debounce(30, clock=clock)
    metrics = Metrics()
    slots = _slots()
    identity = _identity()

    first = _run(_msg(), identity, debounce, slots, metrics)
    slots.release_and_dispatch(G1)   # the pipeline releasing the first tag's lock
    clock.t += 1                     # still inside the debounce window
    second = _run(_msg(), identity, debounce, slots, metrics)

    assert first is Admission.RUN_NOW
    assert second is Admission.REJECTED
    assert metrics.get("nora_tags_debounced_total") == 1


def test_a_second_concurrent_tag_for_the_same_conversation_is_queued_not_dropped():
    """R-30: the in-flight key from the first tag is still held (the pipeline
    has not released it), so a second tag for the same conversation is QUEUED
    rather than dropped. Admission does not
    acquire the slot for the queued tag; the running turn still owns it."""
    metrics = Metrics()
    slots = _slots()
    identity = _identity()

    first = _run(_msg(), identity, Debounce(0, clock=_FakeClock()), slots, metrics)
    second = _run(_msg(), identity, Debounce(0, clock=_FakeClock()), slots, metrics)

    assert first is Admission.RUN_NOW
    assert second is Admission.QUEUED
    assert metrics.get("nora_tags_queued_total") == 1
    # A queued tag IS accepted for handling, so it is counted like a run-now one.
    assert metrics.get("nora_tags_total", platform="signal") == 2
    # A queued tag is not an in-flight drop.
    assert metrics.get("nora_tags_dropped_total", reason="in_flight") == 0
    # The queued tag left the first tag's lock untouched, and the caller — not
    # this stage — is what actually puts it in the queue.
    assert slots.held(G1) is True
    assert slots.depth(G1) == 0


def test_a_tag_arriving_at_a_full_queue_is_dropped_with_its_own_counter():
    """R-30's flood bound: when the queue is already at the slots' `maxlen`, the
    NEWEST arrival is shed — a distinct outcome and a distinct counter from both
    the debounce drop."""
    metrics = Metrics()
    slots = _slots(held=(G1,), depth=QUEUE_MAX)
    identity = _identity()

    verdict = _run(_msg(), identity, Debounce(0, clock=_FakeClock()), slots, metrics)

    assert verdict is Admission.QUEUE_FULL
    assert metrics.get("nora_tags_dropped_total", reason="queue_full") == 1
    # A shed flood tag is NOT counted as accepted-for-handling and NOT queued.
    assert metrics.get("nora_tags_total", platform="signal") == 0
    assert metrics.get("nora_tags_queued_total") == 0


def test_the_last_slot_before_the_bound_still_queues():
    """Boundary: depth == max_depth - 1 is the last acceptable slot; it queues,
    the next (depth == max_depth) is the one that is shed."""
    metrics = Metrics()
    slots = _slots(held=(G1,), depth=QUEUE_MAX - 1)
    identity = _identity()

    verdict = _run(_msg(), identity, Debounce(0, clock=_FakeClock()), slots, metrics)

    assert verdict is Admission.QUEUED


def test_a_different_conversation_is_admitted_concurrently():
    """Proves the lock is per-conversation, not global: a second conversation
    runs now rather than queueing behind the first."""
    metrics = Metrics()
    slots = _slots()
    debounce = Debounce(0, clock=_FakeClock())
    identity = _identity()

    first = _run(_msg(conversation_id="g1"), identity, debounce, slots, metrics)
    second = _run(_msg(conversation_id="g2"), identity, debounce, slots, metrics)

    assert first is Admission.RUN_NOW
    assert second is Admission.RUN_NOW
    assert slots.held(G1) is True
    assert slots.held(G2) is True

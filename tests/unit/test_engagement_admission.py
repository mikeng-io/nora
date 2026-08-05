"""`admission.run` — R-9 (debounce), R-10 (in-flight lock), R-12 (self-tag).

See `nora.engagement.stages.admission`'s module docstring for the R-10
ownership split this file assumes: `run` only ever ACQUIRES (adds `key` to
`in_flight` on admission). Nothing here releases — that is the pipeline's
`finally`, exercised by a later task's test suite, not this one.

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
from nora.obs.metrics import Metrics
from nora.tag import Debounce


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


async def test_a_self_tag_is_refused():
    admitted = await admission.run(
        message=_msg(sender_id="+852000"),
        identity=_identity(primary="+852000"),
        debounce=Debounce(30, clock=_FakeClock()),
        in_flight=set(),
        metrics=Metrics(),
    )

    assert admitted is False


async def test_a_self_tag_arriving_under_a_non_primary_alias_is_refused():
    """R-12 must test membership in `aliases`, not equality against `primary` —
    this is the case that would break if it didn't (see
    `nora.domain.ports`'s module docstring and this stage's own)."""
    admitted = await admission.run(
        message=_msg(sender_id="uuid-abc"),
        identity=_identity(primary="+852000", aliases=frozenset({"uuid-abc"})),
        debounce=Debounce(30, clock=_FakeClock()),
        in_flight=set(),
        metrics=Metrics(),
    )

    assert admitted is False


async def test_a_debounced_tag_is_refused_and_counted():
    clock = _FakeClock()
    debounce = Debounce(30, clock=clock)
    metrics = Metrics()
    in_flight: set[tuple[str, str]] = set()
    identity = _identity()

    first = await admission.run(
        message=_msg(), identity=identity, debounce=debounce,
        in_flight=in_flight, metrics=metrics,
    )
    in_flight.clear()   # simulate the pipeline releasing the first tag's lock
    clock.t += 1         # still inside the debounce window
    second = await admission.run(
        message=_msg(), identity=identity, debounce=debounce,
        in_flight=in_flight, metrics=metrics,
    )

    assert first is True
    assert second is False
    assert metrics.get("nora_tags_debounced_total") == 1


async def test_a_second_concurrent_tag_for_the_same_conversation_is_refused_and_counted():
    """R-10: the in-flight key from the first tag is still held (the pipeline
    has not released it yet), so a second tag for the same conversation is
    dropped, not queued."""
    metrics = Metrics()
    in_flight: set[tuple[str, str]] = set()
    identity = _identity()

    first = await admission.run(
        message=_msg(), identity=identity, debounce=Debounce(0, clock=_FakeClock()),
        in_flight=in_flight, metrics=metrics,
    )
    second = await admission.run(
        message=_msg(), identity=identity, debounce=Debounce(0, clock=_FakeClock()),
        in_flight=in_flight, metrics=metrics,
    )

    assert first is True
    assert second is False
    assert metrics.get("nora_tags_dropped_total", reason="in_flight") == 1
    assert in_flight == {("signal", "g1")}


async def test_a_different_conversation_is_admitted_concurrently():
    """Proves the lock is per-conversation, not global."""
    metrics = Metrics()
    in_flight: set[tuple[str, str]] = set()
    debounce = Debounce(0, clock=_FakeClock())
    identity = _identity()

    first = await admission.run(
        message=_msg(conversation_id="g1"), identity=identity, debounce=debounce,
        in_flight=in_flight, metrics=metrics,
    )
    second = await admission.run(
        message=_msg(conversation_id="g2"), identity=identity, debounce=debounce,
        in_flight=in_flight, metrics=metrics,
    )

    assert first is True
    assert second is True
    assert in_flight == {("signal", "g1"), ("signal", "g2")}

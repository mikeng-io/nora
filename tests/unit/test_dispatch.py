"""`Dispatcher`'s room-verdict branch .

An untagged message in an opted-in room arrives with
`EngageDecision(engage=False, relevance_gate=True)`. It is NOT a tag admission:
the dispatcher only NOTES it with the quiescence trigger — no R-9 debounce, no
`EngagementRequest`, no spawned turn. These fakes are the narrow seam the
class declares (`ingest.run`, `engagement.run`, `background.spawn`,
`trigger.note_message`), which is all the branch touches.
"""

from __future__ import annotations

from nora.app.dispatch import Dispatcher
from nora.domain.models import NormalizedMessage
from nora.domain.ports import EngageDecision
from nora.ingest.pipeline import IngestResult


def _msg(body: str = "hi") -> NormalizedMessage:
    return NormalizedMessage(
        platform="signal", conversation_id="g1", conversation_type="group",
        sender_id="+852111", sender_name="Miles", timestamp=1, body=body, raw={},
    )


class _Ingest:
    def __init__(self, decision: EngageDecision) -> None:
        self._decision = decision

    async def run(self, *, message: NormalizedMessage) -> IngestResult:
        return IngestResult(message=message, decision=self._decision)


class _Engagement:
    def __init__(self) -> None:
        self.requests: list = []

    async def run(self, request) -> None:
        self.requests.append(request)


class _Background:
    def __init__(self) -> None:
        self.spawned: list = []

    def spawn(self, coro) -> None:
        self.spawned.append(coro)


class _Trigger:
    def __init__(self) -> None:
        self.noted: list = []

    def note_message(self, message: NormalizedMessage) -> None:
        self.noted.append(message)


def _dispatcher(decision: EngageDecision, *, trigger=None):
    engagement, background = _Engagement(), _Background()
    dispatcher = Dispatcher(
        ingest=_Ingest(decision), engagement=engagement,
        background=background, participation=trigger,
    )
    return dispatcher, engagement, background


async def test_a_relevance_gate_verdict_is_noted_not_admitted() -> None:
    trigger = _Trigger()
    dispatcher, engagement, background = _dispatcher(
        EngageDecision(engage=False, reason="relevance_gate_room", relevance_gate=True),
        trigger=trigger,
    )

    await dispatcher.on_message(_msg())

    assert [m.body for m in trigger.noted] == ["hi"]
    assert engagement.requests == []
    assert background.spawned == []


async def test_a_relevance_gate_verdict_without_a_trigger_is_a_no_op() -> None:
    """The `[relevance_gate] enabled = false` shape: the verdict can only exist
    when the policy surface is on, but the dispatcher stays safe if it ever
    sees one with no trigger wired."""
    dispatcher, engagement, background = _dispatcher(
        EngageDecision(engage=False, reason="relevance_gate_room", relevance_gate=True),
    )

    await dispatcher.on_message(_msg())

    assert engagement.requests == []
    assert background.spawned == []


async def test_an_ordinary_ambient_message_is_never_noted() -> None:
    """R-1 clause 2, from the dispatcher's side: only the
    room VERDICT reaches the trigger — the rest of the room's traffic
    is recorded by ingest and goes no further."""
    trigger = _Trigger()
    dispatcher, _, background = _dispatcher(
        EngageDecision(engage=False, reason="no_trigger"), trigger=trigger,
    )

    await dispatcher.on_message(_msg())

    assert trigger.noted == []
    assert background.spawned == []


async def test_a_tag_still_spawns_the_engagement_turn() -> None:
    """The control: the room-verdict branch must not disturb the one path
    that existed before it."""
    trigger = _Trigger()
    dispatcher, engagement, background = _dispatcher(
        EngageDecision(engage=True, reason="trigger_text"), trigger=trigger,
    )

    await dispatcher.on_message(_msg(body="nora.000 ?"))

    assert len(background.spawned) == 1
    assert trigger.noted == []
    await background.spawned[0]   # the spawned engagement turn itself
    assert len(engagement.requests) == 1
    assert engagement.requests[0].decision.engage is True

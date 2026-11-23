"""An executor's entry to the envelope after its own admission."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypeVar

from nora.memory.app.act_scope import declare_act
from nora.memory.app.acts import ActEnvelope
from nora.memory.domain.acts import ActAuthority, Outcome

T = TypeVar("T")


class EffectRecorder:
    def __init__(self, envelope: ActEnvelope) -> None:
        self._envelope = envelope

    async def invoke(
        self, executor: str, destination: str, event: str, authority: ActAuthority,
        call: Callable[[], Awaitable[T]], *, judge: Callable[[T], Outcome],
    ) -> T:
        act = declare_act(executor, destination, event=event, authority=authority)
        result = await self._envelope.invoke(act, self._invoke(call, judge))
        if result is None:
            raise RuntimeError(f"{executor} already invoked this act")
        return result.value

    @staticmethod
    def _invoke(call: Callable[[], Awaitable[T]], judge: Callable[[T], Outcome]):
        from nora.memory.app.acts import ActInvocation

        async def invoke(_key: str | None) -> ActInvocation[T]:
            value = await call()
            return ActInvocation(value, judge(value))

        return invoke

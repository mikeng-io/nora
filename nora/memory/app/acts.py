"""Reserve an act before invoking it and append its observed outcome."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Generic, Protocol, TypeVar

from nora.memory.domain.acts import Act, ActAuthority, Outcome
from nora.memory.domain.authored_output import authored_text, without_reasoning

T = TypeVar("T")
T_co = TypeVar("T_co", covariant=True)


class ActStore(Protocol):
    async def reserve(self, act: Act) -> bool: ...

    async def latest(self, act: Act) -> Outcome | None: ...

    async def observe(self, act: Act, outcome: Outcome) -> None: ...

    async def by_key(self, executor: str, key: str) -> Act | None: ...

    async def by_destination(self, executor: str, destination: str) -> Act | None: ...

    async def append_pass(
        self, authority: ActAuthority, kind: str, content: list[str] | None,
        *, ordinal: int | None = None, silence_reason: str | None = None,
    ) -> None: ...

    async def read_pass(self, unit: str, pass_id: str) -> dict[str, list[str] | None]: ...


@dataclass(frozen=True, slots=True)
class PassItem:
    id: str
    kind: str
    at: datetime
    who: str
    content: object
    delivery: str | None = None


@dataclass(frozen=True, slots=True)
class ActInvocation(Generic[T_co]):
    value: T_co
    outcome: Outcome


class ActEnvelope:
    def __init__(self, store: ActStore) -> None:
        self._store = store

    async def by_key(self, executor: str, key: str) -> Act | None:
        return await self._store.by_key(executor, key)

    async def by_destination(self, executor: str, destination: str) -> Act | None:
        return await self._store.by_destination(executor, destination)

    async def record_act_outcome(self, act: Act, outcome: Outcome) -> None:
        await self._store.observe(act, outcome)

    async def record_expression(self, authority: ActAuthority, raw: object) -> None:
        content = authored_text(raw)
        if content:
            await self._store.append_pass(authority, "expression", content)

    async def record_tool(
        self, authority: ActAuthority, ordinal: int,
        tool_use: dict[str, Any], status: str, result: object,
    ) -> None:
        content = {
            "tool": tool_use.get("name", ""),
            "arguments": without_reasoning(tool_use.get("input", {})),
            "status": status,
            "result": [self._tool_text(text) for text in authored_text(without_reasoning(result))],
        }
        await self._store.append_pass(
            authority, "tool_call", [json.dumps(content, ensure_ascii=False, default=str)],
            ordinal=ordinal,
        )

    @staticmethod
    def _tool_text(text: str) -> str:
        try:
            value = json.loads(text)
        except ValueError:
            return text
        return json.dumps(without_reasoning(value), ensure_ascii=False)

    async def record_silence(self, authority: ActAuthority, *, reason: str | None = None) -> None:
        if reason is None:
            await self._store.append_pass(authority, "silence", None)
        else:
            await self._store.append_pass(authority, "silence", None, silence_reason=reason)

    async def read_pass(self, unit: str, pass_id: str) -> dict[str, list[str] | None]:
        return await self._store.read_pass(unit, pass_id)

    async def stream(
        self, act: Act, call: Callable[[], AsyncIterator[T]],
        judge: Callable[[T], Outcome | None], duplicate: T,
    ) -> AsyncIterator[T]:
        if not await self._store.reserve(act):
            yield duplicate
            return
        observed = False
        try:
            async for value in call():
                outcome = judge(value)
                if outcome is not None:
                    await self._store.observe(act, outcome)
                    observed = True
                yield value
        finally:
            if not observed:
                await self._store.observe(act, Outcome("uncertain"))

    async def invoke(
        self, act: Act, call: Callable[[str | None], Awaitable[ActInvocation[T]]],
    ) -> ActInvocation[T] | None:
        if not await self._store.reserve(act):
            return None
        return await self._attempt(act, call)

    async def perform(
        self, act: Act, call: Callable[[], Awaitable[T]], observe: Callable[[T], Outcome],
    ) -> T | None:
        async def invoke_once(_key: str | None) -> ActInvocation[T]:
            result = await call()
            return ActInvocation(result, observe(result))

        invocation = await self.invoke(act, invoke_once)
        return invocation.value if invocation is not None else None

    async def _attempt(
        self, act: Act, call: Callable[[str | None], Awaitable[ActInvocation[T]]],
    ) -> ActInvocation[T]:
        try:
            invocation = await call(act.key)
        except (Exception, asyncio.CancelledError):
            await self._store.observe(act, Outcome("uncertain"))
            raise
        await self._store.observe(act, invocation.outcome)
        return invocation

    async def settle(
        self, act: Act, *,
        read_back: Callable[[str], Awaitable[Outcome]] | None = None,
        replay: Callable[[str | None], Awaitable[ActInvocation[object]]] | None = None,
    ) -> Outcome | None:
        current = await self._store.latest(act)
        if current is None or current.verdict != "uncertain" or act.guarantee == "unknown":
            return current
        if act.guarantee == "read_back" and read_back is not None:
            outcome = await read_back(act.identity.id)
            await self._store.observe(act, outcome)
            return outcome
        if act.guarantee == "idempotent_effect" and replay is not None:
            return (await self._attempt(act, replay)).outcome
        raise ValueError(f"{act.identity.executor} cannot settle under its declared guarantee")

"""Scripted Strands model and tool calls for Dreamer tests."""

from __future__ import annotations

from collections.abc import AsyncIterable, Sequence
from typing import Any

from strands.models.model import Model

from nora.agent.ports import ToolSpec, adapt

ENVELOPE = '{"observations": [{"term": "bath tonight, place and hour open"}]}'


class _Tools:
    """Real `ToolSpec`s under the real memory NAMES, with callables that only
    write down that they ran.

    The argument schema and tool names let the tests drive tool calls through Strands.
    """

    def __init__(self, names: Sequence[str]) -> None:
        self.ran: list[str] = []
        self._names = tuple(names)

    def read_only(self) -> frozenset[str]:
        return frozenset(self._names)

    def tool_param_names(self) -> dict[str, frozenset[str]]:
        """Every fake tool this source can build declares one real parameter
        (`person`, `_spec` below) — the accepted-name table an argument check
        against one of these would actually see."""
        return {name: frozenset({"person"}) for name in self._names}

    def _spec(self, name: str) -> Any:
        async def call(person: str = "") -> dict:
            self.ran.append(name)
            return {"status": "ok", "answer": f"{name} says {person or 'nothing'}"}

        call.__name__ = name
        call.__doc__ = f"{name}.\n\nArgs:\n    person: who to ask about.\n"
        return adapt(ToolSpec(
            name=name, description=f"{name}", schema={}, call=call, budget=4,
        ))

    def build(
        self, *, key: str, names: frozenset[str],
        adaptor: str = "", grant: Any = None,
    ) -> list[Any]:
        return [self._spec(n) for n in self._names if n in names]


class _ScriptedModel(Model):
    """One scripted assistant turn per entry: a tool name to call, or None to
    stop and emit `ENVELOPE`.

    `seen` keeps every `messages` list the loop hands back, which is the only
    place a tool RESULT is observable from outside the SDK — and the result is
    the thing this file is about.
    """

    def __init__(
        self, script: Sequence[str | None], *, inputs: Sequence[str] | None = None,
    ) -> None:
        self._script = list(script)
        # Per-turn tool-call input, as the raw JSON string Strands streams
        # (`contentBlockDelta.delta.toolUse.input`). `None` keeps every turn's
        # the same hardcoded `person`, unless the scenario supplies an argument for each tool call.
        self._inputs = list(inputs) if inputs is not None else None
        self.turn = 0
        self.seen: list[list[dict]] = []

    def update_config(self, **model_config: Any) -> None:
        return None

    def get_config(self) -> Any:
        return {}

    async def structured_output(self, output_model: Any, prompt: Any = None,
                                **kwargs: Any) -> Any:
        raise NotImplementedError

    async def stream(self, *args: Any, **kwargs: Any) -> AsyncIterable[Any]:
        messages = kwargs.get("messages") or (args[0] if args else [])
        self.seen.append([dict(m) for m in messages])
        call = self._script[self.turn] if self.turn < len(self._script) else None
        self.turn += 1
        yield {"messageStart": {"role": "assistant"}}
        if call is None:
            yield {"contentBlockStart": {"start": {}}}
            yield {"contentBlockDelta": {"delta": {"text": ENVELOPE}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "end_turn"}}
        else:
            call_input = (
                self._inputs[self.turn - 1] if self._inputs is not None
                else '{"person": "熱咖啡"}'
            )
            yield {"contentBlockStart": {"start": {"toolUse": {
                "name": call, "toolUseId": f"t{self.turn}"}}}}
            yield {"contentBlockDelta": {"delta": {"toolUse": {
                "input": call_input}}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "tool_use"}}
        yield {"metadata": {
            "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
            "metrics": {"latencyMs": 1},
        }}


def _tool_results(model: _ScriptedModel) -> list[dict]:
    """Every `toolResult` block the loop fed back to the model, in order."""
    out: list[dict] = []
    seen: set[str] = set()
    for messages in model.seen:
        for message in messages:
            for block in message.get("content") or ():
                result = block.get("toolResult") if isinstance(block, dict) else None
                if result is None:
                    continue
                ref = str(result.get("toolUseId"))
                if ref in seen:
                    continue
                seen.add(ref)
                out.append(result)
    return out


class _Before:
    def __init__(self, name: str, ref: str) -> None:
        self.tool_use = {"name": name, "input": {}, "toolUseId": ref}
        self.cancel_tool: str | None = None


class _After:
    def __init__(
        self, name: str, ref: str, *, cancel_message: str | None = None,
        exception: Exception | None = None,
    ) -> None:
        self.tool_use = {"name": name, "input": {}, "toolUseId": ref}
        self.exception = exception
        self.cancel_message = cancel_message
        self.result: dict = (
            {"toolUseId": ref, "status": "error", "content": [{"text": cancel_message}]}
            if cancel_message is not None
            else {"toolUseId": ref, "status": "success", "content": [{"text": "ok"}]}
        )

"""Signal sends land in a sandbox, the way they do in production.

The sandbox keeps Signal's adapter ENABLED with a placeholder account
(`obtain._baseline`) and no signal-cli behind it, so before this every send
dialled `127.0.0.1:18080` and failed: 794 `send failed: ClientConnectorError`
in one `tool_selection` run on 2026-10-08, every one of them a `send_reply`
that answered `refused`. Production answers `sent`. A tool result is model
input, so the sandbox was measuring a Nora whose every reply bounced and who
retried until the message budget ran out (2+ sends in 141 of 141 replying
draws; live, 302 of 317 replying turns send once). That is the prompt the
deployment does not send (`design/evaluation/01-boundaries.md` §5(4)), the same
rule `nora.subject.transport` holds for health.

Only the wire is replaced (`SignalSender._rpc`): the direct-message
allow-list, the conversation-type refusal and the payload building in
`SignalSender.send` stay real. The answer carries no platform timestamp, so
the row keeps the stamp `Deliverer` wrote before sending. That stamp is the
wall clock, not the sandbox clock, so in an anchored case her reply sits in the
anchor's future; that was already so before sends landed, and is not fixed here.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator, Mapping
from typing import Any

from nora.platform.signal.send import SignalSender


class LandedSignalSender(SignalSender):
    """A `SignalSender` whose every well-formed send is accepted, dialling nothing."""

    async def _rpc(
        self, payload: Mapping[str, object], client_timeout: Any,
    ) -> tuple[int, str | None, object]:
        return 200, None, {"jsonrpc": "2.0", "result": {}, "id": payload.get("id")}


@contextlib.contextmanager
def landed_signal_sends() -> Iterator[None]:
    """Make every `SignalAdapter` built inside the block send through
    `LandedSignalSender`. The adapter keeps its sender for its life, so the
    patch is needed only while `build_dependencies` composes.

    Re-entrant the way `recording_subagent_worker` is: two subjects composing
    at once cannot restore each other's patch, and the original goes back only
    when the last block leaves."""
    from nora.platform.signal import adapter

    current: Any = adapter.SignalSender
    landed: Any
    if getattr(current, "_landed_depth", None) is not None:
        landed = current
    else:
        landed = type("LandedSignalSender", (LandedSignalSender,), {})
        landed._landed_depth = 0
        landed._original = current
        setattr(adapter, "SignalSender", landed)  # noqa: B010
    landed._landed_depth += 1
    try:
        yield
    finally:
        landed._landed_depth -= 1
        if landed._landed_depth == 0:
            setattr(adapter, "SignalSender", landed._original)  # noqa: B010

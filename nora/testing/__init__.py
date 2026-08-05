"""Test doubles and builders, shipped inside the package.

This is the package `tests/` and `evals/` are meant to share, so there is
exactly one definition of each fake. `evals/harness.py` and
`tests/integration/harness.py` both build their pipeline from
`FakeAuditStore`/`FakeMemoryStore`/`FakeAdapter` plus `ScriptedTurn`/
`FakeAgentRunner`/`group_event` from here — the rewiring `evals/` needing
`tests/` used to force. On the `tests/` side it is already used beyond
`tests/contract/` — `tests/test_health.py` imports `FakeAuditStore` from
here too.

Every fake that is a second implementation of one of `nora/domain/ports.py`'s
ports is bound by a contract suite in `tests/contract/` that it and the
production implementation both pass — `FakeAuditStore` (`AuditStore`) and
`FakeMemoryStore` (`MemoryStore`). Adding such a fake without one is a
project rule violation, not a shortcut. `FakeAdapter` is exempt: it keeps
`.parse()` public and `.listen()` a no-op rather than mirroring
`SignalAdapter.listen(*, on_message=...)`'s internal parse-and-drop (nothing
drives `FakeAdapter` through a real SSE loop), so it never claims to satisfy
`PlatformAdapter` as a second implementation — see `FakeAdapter`'s docstring.
`ScriptedTurn`/`FakeAgentRunner` satisfy `nora.engagement.stages.invoke.
AgentRunner`, a stage-level `Protocol`, not one of `nora/domain/ports.py`'s
ports — also exempt; see their docstrings.
"""

from nora.testing.fakes import FakeAdapter, FakeAuditStore, FakeMemoryStore
from nora.testing.scripted_agent import FakeAgentRunner, ScriptedTurn, group_event

__all__ = [
    "FakeAdapter",
    "FakeAgentRunner",
    "FakeAuditStore",
    "FakeMemoryStore",
    "ScriptedTurn",
    "group_event",
]

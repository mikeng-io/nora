"""A source-event read made as of an instant resolves the affected messages as of it too."""

import asyncio
from types import SimpleNamespace

from nora.conversation.adapters.tools import query as module
from nora.domain.observations import SourceScope


class _Observations:
    async def recent(self, **_):
        return []

    async def heads(self, **_):
        return []


def test_the_affected_message_read_carries_the_callers_instant(monkeypatch):
    seen = []

    def resolver(deps):
        async def resolve(platform, conversation_id, ids, *, as_of_ms=None):
            seen.append(as_of_ms)
            return {}

        return resolve

    monkeypatch.setattr(module, "_make__resolve_targets", resolver)
    monkeypatch.setattr(module, "_make__source_name_of", lambda deps: lambda *a: None)
    read = module._make__room_source_events(SimpleNamespace(observations=_Observations()))
    scope = SourceScope("signal", "acct", "g1")
    asyncio.run(read(scope, since_ms=0, as_of_ms=12_345))
    assert seen == [12_345]

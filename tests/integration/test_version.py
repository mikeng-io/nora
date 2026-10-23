"""«你而家咩version」 — end to end, from a real inbound Signal event (DEC-97).

WHY THIS IS NOT A DIRECT CALL TO THE TOOL
-----------------------------------------
`tests/test_version_tool.py` calls the closure and asserts its payload. That
proves the tool; it proves nothing about whether a message asking the question
can REACH it. Everything between — the parser, the ingest pipeline, the trigger
gate, the engagement pipeline, `ToolRegistry.build`, `ReplyGuard`'s R-7
default-deny (which vetoes any tool absent from `NATIVE_TOOL_BUDGETS`, and
would veto this one if the table entry were missing), `Deliverer`'s audit-first
write and the R-6/R-18 guards — is real here, and every one of them is a place
the capability could be wired and still not work.

The MODEL is the scripted double, as everywhere else in this suite: it replays
the tool calls against the REAL tools and the REAL guards. What is asserted is
therefore "the answer she sends comes from the stamp", not "the model chose to
call `version_list` or `version_read`", which the tool_selection corpus measures
measured against a live model, not a scripted one.
"""

from __future__ import annotations

import json

import httpx

from nora.agent.registry import ToolRegistry
from nora.domain.models import DeliveryStatus
from nora.infra.github_releases import GitHubReleases
from nora.infra.version import VersionManifest
from nora.testing import ScriptedTurn, group_event

STAMP = {
    "version": "V3",
    "versions": [
        {"version": "V3", "released": "2026-08-11",
         "notes": "You can ask her which version she is running.",
         "changes": ["feat(version): a baked build stamp"]},
        {"version": "V2", "released": "2026-07-01", "notes": "Reminders."},
    ],
}


def _registry(tmp_path, payload=None) -> ToolRegistry:
    """A production `ToolRegistry` with a real stamp on disk — the one
    collaborator this scenario needs answering `ok`. Everything else in it stays
    inert, exactly as the offline pipeline builds it."""
    path = tmp_path / "version.json"
    path.write_text(json.dumps(payload if payload is not None else STAMP),
                    encoding="utf-8")
    def respond(request):
        if request.url.path.endswith("/releases"):
            return httpx.Response(200, json=[])
        if request.url.path.endswith("/tags/V2"):
            return httpx.Response(200, json={
                "tag_name": "V2", "draft": False, "prerelease": False,
                "published_at": "2026-01-01T00:00:00Z",
                "html_url": "https://github.com/mikeng-io/nora/releases/tag/V2",
                "body": "Synthetic reminder release.",
            })
        return httpx.Response(404)

    return ToolRegistry(versions=VersionManifest(path), releases=GitHubReleases(
        transport=httpx.MockTransport(respond)))


def _asks_and_answers(*, which: str | None = None, reply: str) -> list[ScriptedTurn]:
    args = {} if which is None else {"version": which}
    tool = "version_list" if which is None else "version_read"
    return [ScriptedTurn(
        tool_calls=[(tool, args), ("send_reply", {"text": reply})],
        final_text=reply,
    )]


async def test_asked_which_version_she_answers_from_the_baked_stamp(
    harness_factory, tmp_path,
):
    h = await harness_factory(
        script=_asks_and_answers(reply="而家 V3，可以問返我自己版本"),
        tool_registry=_registry(tmp_path),
    )

    await h.handler.on_event(group_event(body="nora.000 你而家咩version"))
    await h.drain()

    assert h.sink.texts == ["而家 V3，可以問返我自己版本"]
    # The list ran and found no published releases; without this the assertion above
    # would pass on a scripted reply the guard had vetoed the tool for (R-7).
    assert h.metrics.get("nora_tool_calls_total", tool="version_list", status="nothing_found") == 1
    assert h.metrics.get("nora_guard_vetoes_total", rule="R-7") == 0
    nora_rows = [r for r in await h.store.recent(conversation_id="g1", limit=10)
                 if r.is_nora]
    assert len(nora_rows) == 1
    assert nora_rows[0].delivery_status is DeliveryStatus.SENT


async def test_asked_what_was_in_an_earlier_version_she_reads_that_one(
    harness_factory, tmp_path,
):
    h = await harness_factory(
        script=_asks_and_answers(which="V2", reply="V2 嗰陣加咗提醒"),
        tool_registry=_registry(tmp_path),
    )

    await h.handler.on_event(group_event(body="nora.000 V2 有咩?"))
    await h.drain()

    assert h.sink.texts == ["V2 嗰陣加咗提醒"]
    assert h.metrics.get("nora_tool_calls_total", tool="version_read", status="ok") == 1


async def test_an_unstamped_build_reaches_her_as_unavailable_not_as_nothing(
    harness_factory, tmp_path,
):
    """The R-27 half, end to end. The registry is the one the offline pipeline
    builds by default — no stamp anywhere — and what must arrive at the model is
    «I can't check», never «there are no versions»."""
    h = await harness_factory(
        script=_asks_and_answers(reply="唔肯定自己而家咩version"),
        tool_registry=ToolRegistry(versions=VersionManifest(tmp_path / "none.json")),
    )

    await h.handler.on_event(group_event(body="nora.000 你而家咩version"))
    await h.drain()

    assert h.sink.texts == ["唔肯定自己而家咩version"]
    assert h.metrics.get(
        "nora_tool_calls_total", tool="version_list", status="unavailable") == 1
    assert h.metrics.get(
        "nora_tool_calls_total", tool="version_list", status="nothing_found") == 0


async def test_a_version_that_never_shipped_is_nothing_found_end_to_end(
    harness_factory, tmp_path,
):
    """The other side of the same distinction, so the pair cannot both be
    satisfied by one status: here the public release endpoint answered 404 for V9."""
    h = await harness_factory(
        script=_asks_and_answers(which="V9", reply="冇 V9 呢個版本"),
        tool_registry=_registry(tmp_path),
    )

    await h.handler.on_event(group_event(body="nora.000 V9 有咩?"))
    await h.drain()

    assert h.metrics.get(
        "nora_tool_calls_total", tool="version_read", status="nothing_found") == 1
    assert h.metrics.get(
        "nora_tool_calls_total", tool="version_read", status="unavailable") == 0

"""Scope and read-only admission for Cognition registry tools."""

from __future__ import annotations

import contextlib
import logging
from types import SimpleNamespace
from typing import Any

from nora.agent.dreamer import (
    AdaptorGrant,
    HomeRoom,
    RegistryToolSource,
    cognition_grant,
)
from nora.obs.metrics import Metrics


def test_the_tool_source_never_builds_a_tool_outside_the_ceiling():
    from nora.agent.registry import ToolRegistry

    source = RegistryToolSource(
        registry=ToolRegistry(), store=None, persons=None,
        metrics=Metrics(),
    )
    asked = frozenset({"send_reply", "computer_run", "remind", "now"})

    ungranted = _names(source.build(key="friends", names=asked))
    assert ungranted == {"now"}

    granted = _names(source.build(
        key="friends", names=asked, adaptor="cognition",
        grant=AdaptorGrant(home_room=True),
    ))
    assert granted == {"now"}


def _names(built: list[Any]) -> set[str]:
    return {
        getattr(t, "tool_name", None) or getattr(t, "__name__", "") for t in built
    }


class _RecordingRegistry:
    """`ToolRegistry`'s two verbs the tool source calls, with every keyword
    `surface` was handed kept — the home-room arguments are VALUES on
    that call and nothing else observes them."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def read_only_tools(self) -> frozenset[str]:
        return frozenset({"where_is", "now"})

    def surface(self, **kw: Any) -> Any:
        # `surface()` is the one builder; the source reads `.tools` off what
        # it returns.
        self.calls.append(kw)
        return SimpleNamespace(tools=[])


def test_a_SCOPE_keyed_grant_resolves_the_adaptors_OWN_room():
    registry = _RecordingRegistry()
    roster = object()
    source = RegistryToolSource(
        registry=registry, store=None, persons=None,
        metrics=Metrics(),
        scope_rooms={"g1-alias": HomeRoom(
            unit="friends", platform="discord", conversation_id="g1", location_trusted=True,
        )},
        rosters={"discord": roster},
        self_ids=frozenset({"+85200000000"}),
    )

    source.build(
        key="g1-alias", names=frozenset({"where_is"}),
        adaptor="cognition", grant=cognition_grant(),
    )

    (call,) = registry.calls
    assert call["conversation_id"] == "g1"
    assert call["location_trusted"] is True
    # STILL `None`: the key is a conversation, not a unit, and the
    # unit-scoped verbs must keep resolving through the room.
    assert call.get("supplied_unit") is None
    room = call["room"]
    assert room.platform == "discord"
    assert room._roster_source is roster
    assert room._self_ids == frozenset({"+85200000000"})


def test_a_scope_keyed_grant_for_an_unknown_room_falls_back_closed_and_says_so(
    caplog,
):
    metrics = Metrics()
    source = RegistryToolSource(
        registry=_RecordingRegistry(), store=None, persons=None,
        metrics=metrics, scope_rooms={},
    )

    with caplog.at_level(logging.ERROR, logger="nora.dreamer"):
        source.build(
            key="g-unknown", names=frozenset({"where_is"}),
            adaptor="cognition", grant=AdaptorGrant(home_room=True),
        )

    assert metrics.get(
        "nora_dream_home_room_missing_total", adaptor="cognition", unit="",
    ) == 1
    said = [r.getMessage() for r in caplog.records]
    assert any("'g-unknown'" in m and "scope" in m for m in said), said


def test_two_broken_scopes_are_logged_SEPARATELY_not_collapsed_onto_one():
    source = RegistryToolSource(
        registry=_RecordingRegistry(), store=None, persons=None,
        metrics=Metrics(), scope_rooms={},
    )

    with caplog_at_error() as records:
        for key in ("g1", "g2", "g1"):
            source.build(
                key=key, names=frozenset({"where_is"}),
                adaptor="cognition", grant=AdaptorGrant(home_room=True),
            )

    said = [r.getMessage() for r in records]
    assert len(said) == 2, said
    assert any("'g1'" in m for m in said) and any("'g2'" in m for m in said)


@contextlib.contextmanager
def caplog_at_error():
    """A local capture rather than the `caplog` fixture: this test asserts on
    the DEDUPE, so it needs every record the one `RegistryToolSource` emitted
    across three builds and nothing else."""
    records: list[logging.LogRecord] = []

    class _Sink(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = logging.getLogger("nora.dreamer")
    handler = _Sink(level=logging.ERROR)
    logger.addHandler(handler)
    try:
        yield records
    finally:
        logger.removeHandler(handler)


def test_without_a_grant_a_SCOPE_keyed_build_is_the_closed_empty_room():
    registry = _RecordingRegistry()
    source = RegistryToolSource(
        registry=registry, store=None, persons=None,
        metrics=Metrics(),
        scope_rooms={"g1": HomeRoom(
            unit="friends", platform="discord", conversation_id="g1",
            location_trusted=True,
        )},
        rosters={"discord": object()},  # type: ignore[dict-item]
    )

    # NO `grant=`, and a tool asked for: the ungranted scope-keyed adaptor of
    # tomorrow, not the tool-free one of today.
    source.build(key="g1", names=frozenset({"where_is"}))

    (call,) = registry.calls
    room = call["room"]
    assert room.platform == "", "a granted room would name its platform"
    assert room._roster_source is None, "no roster: the view is «I could not look»"
    assert room._self_ids == frozenset()
    assert call["location_trusted"] is False, "the strict gate, never the room's flag"
    assert call["conversation_id"] == "g1"
    assert call.get("supplied_unit") is None


def test_asking_for_nothing_builds_nothing_and_touches_no_collaborator():
    from nora.agent.registry import ToolRegistry

    source = RegistryToolSource(
        registry=ToolRegistry(), store=None, persons=None,
        metrics=Metrics(),
    )

    assert source.build(key="nora", names=frozenset()) == []

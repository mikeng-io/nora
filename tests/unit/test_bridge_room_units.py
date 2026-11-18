"""A bridge can place its acts only using the mapping for its admission revision."""

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from nora.memory import EffectRecorder
from nora.platform.bridge.projection import projected_room_unit
from nora.platform.bridge.service import DurableActionService
from tests.unit.test_bridge_projection import (
    CONTRACT_KEYS,
    ROOM_SIGNAL,
    ROOM_SIGNAL_ALSO,
    _publisher,
    _read,
    _sources,
    _write,
)


async def test_room_units_are_separate_and_limited_to_admitted_rooms(example_config_path: Path):
    _sources(example_config_path.parent)
    pub = _publisher(example_config_path.parent)
    await pub.tick()

    projection = _read(pub)
    assert set(projection) == CONTRACT_KEYS
    assert json.loads(pub.target.with_suffix(".room-units.json").read_bytes()) == {
        ROOM_SIGNAL: "nora",
        ROOM_SIGNAL_ALSO: "nora",
    }
    assert projected_room_unit(pub.target, projection, ROOM_SIGNAL) == "nora"


@pytest.mark.parametrize("bad_mapping", [None, "{", json.dumps({ROOM_SIGNAL: "other-unit"})])
async def test_bridge_refuses_an_act_without_matching_room_units(
    example_config_path: Path, bad_mapping: str | None,
):
    _sources(example_config_path.parent)
    pub = _publisher(example_config_path.parent)
    await pub.tick()
    artifact = pub.target.with_suffix(".room-units.json")
    if bad_mapping is None:
        artifact.unlink()
    else:
        _write(artifact, bad_mapping)

    effects = AsyncMock(spec=EffectRecorder)
    send = AsyncMock()
    service = DurableActionService(
        store=None, platform=pub.platform, account_key=pub.account_key, execute=send,
        receipt_intents=frozenset, effects=effects,
        room_unit=lambda room: projected_room_unit(pub.target, _read(pub), room),
    )
    result = await service._invoke_action({
        "op": "send", "action_id": "generated", "request": {"target": {"id": ROOM_SIGNAL}},
    })

    assert (result.state, result.kind) == ("rejected", "placement")
    effects.invoke.assert_not_awaited()
    send.assert_not_awaited()

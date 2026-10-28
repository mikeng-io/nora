"""A Discord server entry and its `ignore`/`ambient` names hot-reload.

In the style of `test_policy_reload.py`, but pointed at the one thing that
file cannot reach: `build_dependencies` has no Discord client, so the
channel directory is driven straight into `ReloadFiles` — the same
`policy_channels` seam composition wires — and the swaps are polled by
hand instead of through the async loop. The assertion shape is identical:
the NEXT admission under the live holder answers differently after the
edit applies.
"""

from __future__ import annotations

import pytest

from nora.config_reload import ReloadFiles
from nora.domain.models import NormalizedMessage
from nora.ingest.stages import scope
from nora.obs.metrics import Metrics
from nora.platform.discord.channels import StaticChannelDirectory

GUILD = "100000000000000042"
GENERAL = "100000000000000101"
OPS = "100000000000000102"

CHANNELS = {GUILD: {GENERAL: "general", OPS: "ops"}}


def _entry(extra=""):
    return (
        '[[conversations]]\nplatform = "discord"\n'
        f'guild = "{GUILD}"\nlabel = "Zone 9"\npersons = "*"\n{extra}'
    )


@pytest.fixture
def files(tmp_path):
    policy = tmp_path / "policy.toml"
    identity = tmp_path / "identity.toml"
    policy.write_text(
        'default = "deny"\n'
        '[[conversations]]\nplatform = "signal"\n'
        'id = "g1"\nlabel = "First"\npersons = "*"\n'
    )
    identity.write_text('[persons.miles]\nname = "Miles"\n')
    files = ReloadFiles(
        policy_path=policy, identity_path=identity,
        ai_peer="nora", default_workspace="nora", metrics=Metrics(),
        policy_channels=StaticChannelDirectory(CHANNELS, threads={}),
    )
    files._test_metrics = Metrics()  # ReloadFiles stores no handle; scope needs one
    return files


async def _admitted(files, channel_id):
    return await scope.run(
        message=NormalizedMessage(
            "discord", f"{GUILD}/{channel_id}", "group",
            "424242424242424242", "Miles", 1, "hi", {},
        ),
        policy=files.policy, persons=files.persons, metrics=files._test_metrics,
    )


async def test_a_server_entry_added_at_reload_admits_the_next_message(files):
    assert not await _admitted(files, GENERAL)

    files.files[0].path.write_text('default = "deny"\n' + _entry())
    files.files[0].poll()

    assert await _admitted(files, GENERAL)
    room = files.policy.conversation("discord", f"{GUILD}/{GENERAL}")
    assert room is not None and room.discovered and not room.absorbed


async def test_an_ignore_name_added_at_reload_denies_the_next_message(files):
    files.files[0].path.write_text('default = "deny"\n' + _entry())
    files.files[0].poll()
    assert await _admitted(files, GENERAL)

    files.files[0].path.write_text(
        'default = "deny"\n' + _entry('ignore = ["general"]\n'),
    )
    files.files[0].poll()

    assert not await _admitted(files, GENERAL)
    assert await _admitted(files, OPS)


async def test_an_ambient_name_added_at_reload_makes_the_room_ambient(files):
    files.files[0].path.write_text('default = "deny"\n' + _entry())
    files.files[0].poll()
    assert not files.policy.conversation(
        "discord", f"{GUILD}/{GENERAL}").absorbed

    files.files[0].path.write_text(
        'default = "deny"\n' + _entry('ambient = ["general"]\n'),
    )
    files.files[0].poll()

    room = files.policy.conversation("discord", f"{GUILD}/{GENERAL}")
    assert room is not None and room.absorbed and room.relevance_gate


async def test_removing_a_server_entry_denies_its_channels_at_reload(files):
    files.files[0].path.write_text('default = "deny"\n' + _entry())
    files.files[0].poll()
    assert await _admitted(files, GENERAL)

    files.files[0].path.write_text(
        'default = "deny"\n'
        '[[conversations]]\nplatform = "signal"\n'
        'id = "g1"\nlabel = "First"\npersons = "*"\n'
    )
    files.files[0].poll()

    assert not await _admitted(files, GENERAL)

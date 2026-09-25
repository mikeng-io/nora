"""The room gate: a conversation absent from policy.toml is never recorded.

The placement is the point. The ROOM is enforced before the audit write, so a
denied conversation leaves no trace; the SENDER is enforced at the point of
answering, so an unrecognised sender in an ALLOWED room is still audited —
which is exactly what `nora persons pending` reads.
"""

from pathlib import Path

import pytest

from nora.domain.models import NormalizedMessage
from nora.ingest.stages import scope
from nora.obs.metrics import Metrics
from nora.policy import load_policy

ALLOWED = "Lf2L9zqeNUahRsOz6BA+sTB8aWfbuvqu9vOhpvmJi34="

pytestmark = pytest.mark.asyncio


@pytest.fixture
def policy(tmp_path):
    p = tmp_path / "policy.toml"
    p.write_text(
        'default = "deny"\n[[conversations]]\nplatform = "signal"\n'
        f'id = "{ALLOWED}"\nlabel = "allowed"\npersons = ["miles"]\n'
    )
    return load_policy(Path(p))


def _msg(conversation_id, platform="signal"):
    return NormalizedMessage(
        platform, conversation_id, "group", "+852111", "Miles", 1, "hi", {}
    )


async def test_an_allowlisted_conversation_continues(policy):
    metrics = Metrics()
    assert await scope.run(message=_msg(ALLOWED), policy=policy, metrics=metrics) is True
    assert metrics.get("nora_denied_conversations_total", platform="signal") == 0


async def test_an_unlisted_conversation_is_refused_and_counted(policy):
    """Counted, not silent: 'she stopped answering in that group' has to be
    answerable without attaching a debugger (R-5)."""
    metrics = Metrics()
    assert await scope.run(message=_msg("some-other"), policy=policy, metrics=metrics) is False
    assert metrics.get("nora_denied_conversations_total", platform="signal") == 1


async def test_the_same_id_on_another_platform_is_a_different_room(policy):
    metrics = Metrics()
    refused = await scope.run(
        message=_msg(ALLOWED, platform="telegram"), policy=policy, metrics=metrics
    )
    assert refused is False

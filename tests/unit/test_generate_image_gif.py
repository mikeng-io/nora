"""`generate_image` asks for a GIF only from a generator that makes one.

`max_gif_frames` is the switch: an operator sets it for a model that animates
(gpt-image-2.6) and leaves it 0 for one that does not, so going back to an older
model is a config change and the tool then refuses a GIF before any call.
"""

from __future__ import annotations

import pytest

from nora.agent.room import Room
from nora.agent.tools import build_tool_specs
from nora.domain.ports import ImageResult
from nora.obs.metrics import Metrics
from nora.persons import load_persons
from nora.testing import FakeBlobStore, FakeMediaObjectStore, FakeRecordStore, FakeRosterSource
from tests.fakes import SinkSender

GIF = b"GIF89a" + b"\x00" * 16


class _Generator:
    max_reference_images = 3

    def __init__(self, max_gif_frames: int) -> None:
        self.max_gif_frames = max_gif_frames
        self.calls: list[dict] = []

    async def generate(self, **kwargs) -> ImageResult:
        self.calls.append(kwargs)
        return ImageResult(status="ok", provider="fake", raw=GIF, media_type="image/gif")


class _Deliverer(SinkSender):
    held_count = 0

    def __init__(self) -> None:
        super().__init__()
        self.attachments: list = []

    async def deliver(self, text, *, source, attachments, record_text, record_metadata,
                      media_blobs) -> bool:
        self.attachments.extend(attachments)
        return True


@pytest.fixture
def persons(tmp_path):
    path = tmp_path / "identity.toml"
    path.write_text('[persons.p]\nname = "P"\n[[persons.p.signal]]\nphone = "+10000000001"\n')
    return load_persons(path)


def _tool(persons, generator, deliverer):
    specs = build_tool_specs(
        store=FakeRecordStore(), conversation_id="g", conversation_type="group",
        persons=persons, deliverer=deliverer, metrics=Metrics(),
        room=Room(platform="signal", conversation_id="g",
                  roster_source=FakeRosterSource(groups={}), persons=persons),
        transit_source=None, image_generator=generator, image_refs_allowed=False,
        media_store=FakeMediaObjectStore([]), blob_store=FakeBlobStore(),
    )
    return next(s for s in specs if s.name == "generate_image").call


async def test_a_gif_reaches_the_chat_as_a_gif(persons):
    gen, out = _Generator(max_gif_frames=16), _Deliverer()
    result = await _tool(persons, gen, out)(prompt="a ball bouncing", gif_frames=6)
    assert result["status"] == "ok"
    assert gen.calls[0]["gif_frames"] == 6
    (sent,) = out.attachments
    assert (sent.media_type, sent.filename.endswith(".gif"), sent.content) == (
        "image/gif", True, GIF,
    )


async def test_a_stills_only_model_refuses_a_gif_before_any_call(persons):
    gen, out = _Generator(max_gif_frames=0), _Deliverer()
    result = await _tool(persons, gen, out)(prompt="a ball bouncing", gif_frames=6)
    assert result["status"] == "unavailable"
    assert "still" in result["note"]
    assert gen.calls == [] and out.attachments == []


@pytest.mark.parametrize("frames", [0, 1])
async def test_fewer_than_two_frames_is_refused_not_made_a_still(persons, frames):
    gen = _Generator(max_gif_frames=16)
    result = await _tool(persons, gen, _Deliverer())(prompt="a ball", gif_frames=frames)
    assert result["status"] == "unavailable" and "2 to 16" in result["note"]
    assert gen.calls == []


async def test_a_still_asks_for_no_frames(persons):
    gen = _Generator(max_gif_frames=16)
    await _tool(persons, gen, _Deliverer())(prompt="a ball")
    assert gen.calls[0]["gif_frames"] is None

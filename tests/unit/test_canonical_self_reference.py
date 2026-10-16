"""DEC-193: her canonical self picture resolves as a reference in ANY room.

The custody rule `generate_image` enforces (R-21/DEC-24) is that a reference
code resolves to bytes Nora already holds, from THIS conversation's own media
window, and nothing else. DEC-193 carves exactly one exception: the media row
whose `provenance.role` is exactly `"self"`, matched on its FULL sha256, and
only when the conversation itself has no match (conversation first). Everything
here is a negative of that exception as much as its positive: a different role,
a short handle, a URL and a missing store all stay refused.

Exercised through `build_tool_specs`, the same wiring the tag path uses, with a
generator fake that calls the resolver the way the real adapter does
(`nora.infra.image_gen.xai_adapter`: every reference must resolve or the whole
edit is `nothing_found`, and the provider is never reached).
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from nora.agent.room import Room
from nora.agent.tools import build_tool_specs
from nora.domain.media import MediaBlob, MediaObject
from nora.domain.models import NormalizedMessage
from nora.domain.ports import ImageResult
from nora.obs.metrics import Metrics
from nora.persons import load_persons
from nora.testing import FakeBlobStore, FakeMediaObjectStore, FakeRecordStore, FakeRosterSource
from scripts.ingest_self_asset import ingest_self, skill_self_handle

ROOT = Path(__file__).resolve().parents[2]
ASSET = ROOT / "assets" / "nora-canonical-self.png"
SKILL = ROOT / "nora" / "skills" / "image-generation" / "SKILL.md"

HERE = "g-here"
SELF_BYTES = b"NORA-CANONICAL-SELF"
SELF_SHA = hashlib.sha256(SELF_BYTES).hexdigest()
SELF_PROVENANCE = {"role": "self", "source": "operator-provided", "canonical": True}


class _ResolvingGenerator:
    """Behaves like `XaiImageGenerator` on the reference path: resolve every
    marker through the injected resolver; any `None` is `nothing_found`
    without a provider call; all resolved is an `ok` with a hosted artifact
    (no raw bytes, so the tool does not reach the deliverer)."""

    def __init__(self) -> None:
        self.resolved: list[bytes | None] = []
        self.provider_calls = 0
        self.sizes: list[object] = []

    max_reference_images = 3

    async def generate(self, *, prompt, reference_images=None, size=None,
                       quality=None, reference_resolver=None) -> ImageResult:
        self.sizes.append(size)
        if reference_images:
            if reference_resolver is None:
                return ImageResult(status="unavailable", reason="no_reference_resolver")
            for ref in reference_images:
                data = await reference_resolver(ref.marker_code)
                self.resolved.append(data)
                if data is None:
                    return ImageResult(status="nothing_found", reason="unknown_reference")
        self.provider_calls += 1
        return ImageResult(status="ok", artifact="https://images.invalid/out.png",
                           provider="fake")


def _self_row(sha: str = SELF_SHA, provenance: object = None) -> MediaObject:
    return MediaObject(
        sha256=sha, media_type="image/png", size=len(SELF_BYTES), source="fetched",
        description="",
        provenance=SELF_PROVENANCE if provenance is None else provenance,  # type: ignore[arg-type]
        created_at_ms=1,
    )


async def _post(store: FakeRecordStore, conversation: str, *blobs: MediaBlob) -> None:
    await store.append(message=NormalizedMessage(
        "signal", conversation, "group", "+10000000001", "Someone", 1,
        "[image]", {}, media_blobs=blobs,
    ))


@pytest.fixture
def persons(tmp_path):
    path = tmp_path / "identity.toml"
    path.write_text('[persons.p]\nname = "P"\n[[persons.p.signal]]\nphone = "+10000000001"\n')
    return load_persons(path)


def _tool(persons, *, store, media_store, blob_store, generator, refs_allowed=True):
    specs = build_tool_specs(
        store=store, conversation_id=HERE, conversation_type="group",
        persons=persons, honcho=None, deliverer=None, metrics=Metrics(),
        room=Room(platform="signal", conversation_id=HERE,
                  roster_source=FakeRosterSource(groups={}), persons=persons),
        transit_source=None, image_generator=generator,
        image_refs_allowed=refs_allowed, media_store=media_store,
        blob_store=blob_store,
    )
    return next(s for s in specs if s.name == "generate_image").call


@pytest.fixture
def world():
    blobs = FakeBlobStore()
    blobs.blobs[SELF_SHA] = SELF_BYTES
    return {"store": FakeRecordStore(), "media": FakeMediaObjectStore([_self_row()]),
            "blobs": blobs, "gen": _ResolvingGenerator()}


def _call(persons, world, **kw):
    return _tool(persons, store=world["store"], media_store=world["media"],
                 blob_store=world["blobs"], generator=world["gen"], **kw)


async def test_the_canonical_self_resolves_in_a_room_that_never_held_it(persons, world):
    """The positive: this conversation has no media at all, and the full self
    sha still resolves to her own bytes."""
    result = await _call(persons, world)(prompt="you, in the rain", reference_images=[SELF_SHA])
    assert result["status"] == "ok"
    assert world["gen"].resolved == [SELF_BYTES]


async def test_only_a_photo_sent_here_asks_the_reply_to_say_so(persons, world):
    """The reply says a picture was made from photos when somebody's photo
    went to the provider: that is what the rule is for. The statement is in the
    result, and only when a photo from THIS conversation resolved. Her own
    canonical picture is nobody's photo."""
    own = await _call(persons, world)(prompt="you, in the rain", reference_images=[SELF_SHA])
    assert own["status"] == "ok"
    assert "photos sent here" not in own["note"]

    local = "ab" * 32
    world["blobs"].blobs[local] = b"LOCAL"
    await _post(world["store"], HERE, MediaBlob(sha256=local, media_type="image/png", size=5))
    theirs = await _call(persons, world)(prompt="edit", reference_images=[local[:6]])
    assert theirs["status"] == "ok"
    assert "Say in your reply that it was made from photos sent here." in theirs["note"]


async def test_the_marker_form_of_the_canonical_self_resolves_too(persons, world):
    """The skill writes it as `[image <sha>]`; she may pass it back in that form."""
    result = await _call(persons, world)(prompt="you", reference_images=[f"[image {SELF_SHA}]"])
    assert result["status"] == "ok"
    assert world["gen"].resolved == [SELF_BYTES]


@pytest.mark.parametrize("role", [None, "Self", "SELF", "self ", " self", "selfie",
                                  "operator", ["self"], True])
async def test_a_role_other_than_exactly_self_never_gets_the_exemption(persons, world, role):
    """A full-sha row held for ANOTHER conversation stays refused unless its role
    is the exact string "self" — no case-folding, no strip, no prefix, no set."""
    other = hashlib.sha256(b"someone else's photo").hexdigest()
    world["blobs"].blobs[other] = b"OTHER"
    provenance = {"conversation_id": "g-elsewhere"}
    if role is not None:
        provenance["role"] = role
    world["media"].objs[other] = _self_row(sha=other, provenance=provenance)
    result = await _call(persons, world)(prompt="edit", reference_images=[other])
    assert result["status"] == "nothing_found"
    assert world["gen"].resolved == [None]
    assert world["gen"].provider_calls == 0


@pytest.mark.parametrize("cut", [6, 12, 63])
async def test_a_short_handle_of_the_canonical_self_never_reaches_it(persons, world, cut):
    """The exemption matches the FULL sha. A prefix is a conversation handle,
    and this conversation holds nothing, so it resolves to nothing."""
    result = await _call(persons, world)(prompt="you", reference_images=[SELF_SHA[:cut]])
    assert result["status"] == "nothing_found"
    assert world["gen"].resolved == [None]


async def test_a_conversation_blob_sharing_the_short_handle_wins_over_the_canonical(
    persons, world,
):
    """Conversation first. A blob in this room whose sha shares the canonical's
    short handle is what that handle means here; the canonical is not consulted."""
    local = SELF_SHA[:6] + "0" * 58
    world["blobs"].blobs[local] = b"LOCAL"
    await _post(world["store"], HERE, MediaBlob(sha256=local, media_type="image/png", size=5))
    result = await _call(persons, world)(prompt="edit", reference_images=[SELF_SHA[:6]])
    assert result["status"] == "ok"
    assert world["gen"].resolved == [b"LOCAL"]


async def test_an_ambiguous_conversation_handle_resolves_to_nothing(persons, world):
    """Two blobs in this room share the handle: ambiguous, so nothing, and the
    canonical row sharing the same short handle is never the tie-breaker."""
    for tail in ("1", "2"):
        sha = SELF_SHA[:6] + tail * 58
        world["blobs"].blobs[sha] = tail.encode()
        await _post(world["store"], HERE, MediaBlob(sha256=sha, media_type="image/png", size=1))
    result = await _call(persons, world)(prompt="edit", reference_images=[SELF_SHA[:6]])
    assert result["status"] == "nothing_found"
    assert world["gen"].resolved == [None]


async def test_the_full_self_sha_still_resolves_beside_a_short_handle_collision(
    persons, world,
):
    """The collision above does not shadow the canonical: the FULL sha matches
    no conversation blob, so it falls through to the self row."""
    local = SELF_SHA[:6] + "0" * 58
    world["blobs"].blobs[local] = b"LOCAL"
    await _post(world["store"], HERE, MediaBlob(sha256=local, media_type="image/png", size=5))
    result = await _call(persons, world)(prompt="you", reference_images=[SELF_SHA])
    assert result["status"] == "ok"
    assert world["gen"].resolved == [SELF_BYTES]


@pytest.mark.parametrize("code", [
    f"https://images.invalid/{SELF_SHA}",
    f"http://x/{SELF_SHA}.png",
    "data:image/png;base64,iVBORw0KGgo=",
])
async def test_an_external_reference_is_refused_before_the_provider(persons, world, code):
    """A URL is never a reference, even one that wraps the canonical sha: the
    hex-extraction that reads `[image a3f2c1]` would otherwise turn it into
    the self handle. Refused before any lookup and before the provider."""
    result = await _call(persons, world)(prompt="edit", reference_images=[code])
    assert result["status"] == "nothing_found"
    assert world["gen"].resolved == []
    assert world["gen"].provider_calls == 0


async def test_without_a_media_store_the_canonical_self_resolves_to_nothing(persons, world):
    """The exemption reads the media index; unwired, it fails closed."""
    tool = _tool(persons, store=world["store"], media_store=None,
                 blob_store=world["blobs"], generator=world["gen"])
    result = await tool(prompt="you", reference_images=[SELF_SHA])
    assert result["status"] == "nothing_found"


async def test_a_self_row_whose_bytes_are_missing_resolves_to_nothing(persons, world):
    """The row is a promise about the blob store; a broken promise is nothing."""
    del world["blobs"].blobs[SELF_SHA]
    result = await _call(persons, world)(prompt="you", reference_images=[SELF_SHA])
    assert result["status"] == "nothing_found"


async def test_the_skills_embedded_handle_resolves_to_the_ingested_asset(persons):
    """The chain the whole decision rests on: the handle written into the skill,
    the operator's ingest of the real asset, and the resolver agree. A re-render
    of the asset that changes its bytes fails HERE, not as a dead handle live."""
    handle = skill_self_handle(SKILL)
    data = ASSET.read_bytes()
    assert hashlib.sha256(data).hexdigest() == handle
    media, blobs = FakeMediaObjectStore(), FakeBlobStore()
    await ingest_self(data=data, expected_sha256=handle, blob_store=blobs, media_store=media)
    gen = _ResolvingGenerator()
    tool = _tool(persons, store=FakeRecordStore(), media_store=media, blob_store=blobs,
                 generator=gen)
    result = await tool(prompt="you", reference_images=[f"[image {handle}]"])
    assert result["status"] == "ok"
    assert gen.resolved == [data]


def test_the_skill_writes_the_handle_as_a_full_sha():
    assert re.fullmatch(r"[0-9a-f]{64}", skill_self_handle(SKILL))


async def test_with_no_size_or_aspect_the_shape_is_left_to_the_provider(persons, world):
    """What the docstring claims about shape, as the tool actually behaves: no
    `size` and no `aspect` sends no `ImageSize`, so the adapter sends no
    `aspect_ratio` and xAI's default applies (with references, the first
    image's shape — docs.x.ai multi-image editing). Naming either fills the
    other: `size` alone fixes 1:1."""
    tool = _call(persons, world)
    await tool(prompt="you", reference_images=[SELF_SHA])
    await tool(prompt="you", reference_images=[SELF_SHA], size="large")
    await tool(prompt="you", reference_images=[SELF_SHA], aspect="16:9")
    unset, size_only, aspect_only = world["gen"].sizes
    assert unset is None
    assert (size_only.tier, size_only.aspect_ratio) == ("large", "1:1")
    assert (aspect_only.tier, aspect_only.aspect_ratio) == ("medium", "16:9")


async def test_an_unknown_size_or_aspect_falls_back_to_the_documented_default(persons, world):
    """The parameters are `Literal`s and Strands validates a model's call, but
    `ImageSize` is a plain dataclass: a caller that bypasses the schema must
    not carry an unknown value to the provider. It gets the default the
    docstring names (medium, 1:1)."""
    tool = _call(persons, world)
    await tool(prompt="you", reference_images=[SELF_SHA], size="huge", aspect="4:3")
    await tool(prompt="you", reference_images=[SELF_SHA], size="small", aspect="bogus")
    both_bad, aspect_bad = world["gen"].sizes
    assert (both_bad.tier, both_bad.aspect_ratio) == ("medium", "1:1")
    assert (aspect_bad.tier, aspect_bad.aspect_ratio) == ("small", "1:1")

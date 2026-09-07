"""`media_fetch` — reading a stored image back through MiMo (DEC-56, R-33).

The tool's own logic: resolve a handle within THIS conversation's `media_json`,
confirm it is an image, decrypt the blob Nora holds, describe it under a cache
keyed by content hash, and screen the untrusted description on every serve.

The MiMo call itself is a duck-typed collaborator here (a counting stub), exactly
as `route_source` is in `tests/test_routes.py` — the raw call is exercised
against a fake server in `tests/test_vision.py`. The blob store and the audit
store are the REAL implementations (a `FilesystemBlobStore` over a tmp dir and a
`SqlRecordStore`), because both are `nora.domain.ports` ports and a test fake of
either would need the shared contract suite (CLAUDE.md); the real ones are cheap.
"""

from __future__ import annotations

import base64
import hashlib

import pytest

from nora.agent.guards import Deliverer
from nora.agent.room import Room
from nora.agent.tools import NATIVE_TOOL_BUDGETS, build_tools
from nora.doctrine.loader import DoctrineStore
from nora.domain.media import MediaBlob, MediaObject
from nora.domain.models import ConversationTarget, NormalizedMessage
from nora.domain.ports import MediaDescription
from nora.infra.blobs import FilesystemBlobStore
from nora.infra.blobs.cipher import BlobCipher
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.vision import MediaComprehender, VisionCache, VisionResult
from nora.obs.metrics import Metrics
from nora.persons import load_persons
from nora.testing import (
    FakeMediaDescriptionStore,
    FakeMediaObjectStore,
    FakeMemoryStore,
)
from tests.fakes import SinkSender
from tests.test_doctrine_loader import write

TARGET = ConversationTarget("signal", "g1", "group")

RED_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
PNG_SHA = hashlib.sha256(RED_PNG).hexdigest()
# A base64 32-byte key, the shape the env var carries.
KEY_A = base64.b64encode(b"\x01" * 32).decode()
KEY_B = base64.b64encode(b"\x02" * 32).decode()


class CountingVision:
    """A MiMo stand-in that counts calls, so a cache hit is provable. Returns a
    real `VisionResult`, the value the client returns — never a bare dict, so a
    test cannot pass by agreeing with a wrong shape."""

    def __init__(self, description: str = "A red one-pixel square.") -> None:
        self.calls = 0
        self.description = description
        self.available = True

    async def describe(self, *, data: bytes, media_type: str,
                       prompt: str = "", model: str = "",
                       provider: str = "omniroute",
                       max_bytes: int | None = None) -> VisionResult:
        self.calls += 1
        if not self.available:
            return VisionResult(available=False, reason="timeout")
        return VisionResult(available=True, description=self.description)


async def _append_image(store, *, conversation_id: str, sha256: str,
                        media_type: str = "image/png", ts: int = 100) -> None:
    """One message whose only content is an image Nora holds — the row carries
    `media_json`, which is what `media_fetch` resolves against."""
    blob = MediaBlob(sha256=sha256, media_type=media_type, size=len(RED_PNG), filename=None)
    await store.append(message=NormalizedMessage(
        "signal", conversation_id, "group", "+852111", "Miles", ts,
        f"[image {sha256[:6]}]", {}, media_blobs=(blob,),
    ))


@pytest.fixture
async def wiring(tmp_path, pg_url):
    run_migrations(pg_url)
    store = SqlRecordStore(create_sql_engine(pg_url))

    blobs = FilesystemBlobStore(tmp_path / "media", cipher=BlobCipher.from_base64(KEY_A))
    stored_sha = await blobs.put(data=RED_PNG)
    assert stored_sha == PNG_SHA

    persons_file = tmp_path / "identity.toml"
    persons_file.write_text(
        "[persons.miles]\nname = \"Miles\"\n[[persons.miles.signal]]\nphone = \"+852111\"\n"
    )
    pdir = tmp_path / "doctrine"
    pdir.mkdir()
    write(pdir, "identity.md", name="soul", order=10, body="You are Nora.")
    doctrine = DoctrineStore(pdir, canary="C", metrics=Metrics()).get()
    deliverer = Deliverer(store=store, adapter=SinkSender(), target=TARGET, doctrine=doctrine,
                          canary="C", tag_id="t1", nora_sender_id="nora",
                          message_cap=NATIVE_TOOL_BUDGETS["send_reply"], audience="tag",
                          metrics=Metrics())
    metrics = Metrics()
    persons = load_persons(persons_file)
    room = Room(platform="signal", conversation_id="g1", roster_source=None, persons=persons)
    vision = CountingVision()
    cache = VisionCache()

    def build(*, vision_source=vision, blob_store=blobs, vision_cache=cache,
              describe_models=None, describe_providers=None, media_store=None):
        # Image-only by default, which is the shipped configuration: audio and
        # video are wired but inert until a model is named for them. A NAMED
        # model, not "": since the split an empty model means «not configured»
        # and is answered `unavailable`, so "" here would turn every image test
        # into a describer-off test.
        describe_models = (
            {"image": "test/image-model"} if describe_models is None else describe_models
        )
        tools = build_tools(
            store=store, conversation_id="g1", conversation_type="group",
            persons=persons, honcho=FakeMemoryStore(), deliverer=deliverer, metrics=metrics,
            room=room, transit_source=None,
            vision_source=vision_source, blob_store=blob_store, vision_cache=vision_cache,
            describe_models=describe_models,
            describe_providers=describe_providers,
            question="what is in the photo", context_window=40,
            media_store=media_store,
        )
        return {t.tool_name: t for t in tools}["media_fetch"]

    ctx = {"store": store, "blobs": blobs, "vision": vision, "cache": cache,
           "metrics": metrics, "build": build, "tmp_path": tmp_path}
    try:
        yield ctx
    finally:
        await store.aclose()


async def _call(tool, **kw):
    return await tool._tool_func(**kw)


# ---- the ok path -------------------------------------------------------------

async def test_an_image_in_the_conversation_is_described(wiring):
    await _append_image(wiring["store"], conversation_id="g1", sha256=PNG_SHA)
    out = await _call(wiring["build"](), code=PNG_SHA[:6])
    assert out["status"] == "ok"
    assert out["description"] == "A red one-pixel square."
    assert out["code"] == PNG_SHA[:6]
    assert wiring["vision"].calls == 1


# ---- the generic URL path (DEC-56 extension, 2026-08-31) --------------------
# A `media_fetch(url=…)` ingestion is the cross-tool read path: ANY tool that
# surfaces an image/video URL — social_fetch media rows, web_fetch page images,
# a future tool — funnels into the same describe pipeline. The downloader is a
# real byte/time-bounded GET; these tests stub `_download_media` (the module's
# own seam) so the URL-path LOGIC is pinned without a network dependency.

async def test_a_url_is_downloaded_and_described_like_a_code(wiring, monkeypatch):
    async def fake_download(url: str) -> tuple[bytes, str]:
        assert url == "https://x.com/pic.jpg"
        return RED_PNG, "image/jpeg"
    monkeypatch.setattr("nora.agent.tools._download_media", fake_download)
    out = await _call(wiring["build"](), url="https://x.com/pic.jpg")
    assert out["status"] == "ok"
    assert out["description"] == "A red one-pixel square."
    # NO reusable code: URL bytes are never stored in this conversation's
    # media_json, so a code the model could hand back would not resolve on a
    # later call (external audit finding, 2026-08-31).
    assert out["code"] is None
    assert wiring["vision"].calls == 1


async def test_a_url_cache_hit_makes_no_vision_call(wiring, monkeypatch):
    """The description cache is keyed by content hash, and the URL path uses
    the SAME key as the code path — a URL view of bytes the conversation
    already described is a cache hit, not a second model call."""
    await _append_image(wiring["store"], conversation_id="g1", sha256=PNG_SHA)
    first = await _call(wiring["build"](), code=PNG_SHA[:6])
    assert first["status"] == "ok"

    async def fake_download(url: str) -> tuple[bytes, str]:
        return RED_PNG, "image/jpeg"
    monkeypatch.setattr("nora.agent.tools._download_media", fake_download)
    second = await _call(wiring["build"](), url="https://x.com/same.jpg")
    assert second["status"] == "ok"
    assert second["description"] == first["description"]
    assert wiring["vision"].calls == 1  # the code-path describe, not a second


async def test_a_failed_download_is_unavailable_not_a_guess(wiring, monkeypatch):
    async def fake_download(url: str) -> tuple[bytes, str]:
        return b"", ""
    monkeypatch.setattr("nora.agent.tools._download_media", fake_download)
    out = await _call(wiring["build"](), url="https://x.com/dead.jpg")
    assert out["status"] == "unavailable"
    assert wiring["vision"].calls == 0


async def test_a_url_with_an_identifier_is_refused_before_any_download(wiring, monkeypatch):
    calls = []

    async def fake_download(url: str) -> tuple[bytes, str]:
        calls.append(url)
        return RED_PNG, "image/jpeg"
    monkeypatch.setattr("nora.agent.tools._download_media", fake_download)
    out = await _call(wiring["build"](), url="https://x.com/p.jpg?u=%2B85212345678")
    assert out["status"] == "nothing_found"
    assert calls == []  # the egress screen ran first


async def test_a_non_http_url_is_refused(wiring, monkeypatch):
    calls = []

    async def fake_download(url: str) -> tuple[bytes, str]:
        calls.append(url)
        return RED_PNG, "image/jpeg"
    monkeypatch.setattr("nora.agent.tools._download_media", fake_download)
    out = await _call(wiring["build"](), url="file:///etc/passwd")
    assert out["status"] == "nothing_found"
    assert calls == []


async def test_code_and_url_together_or_neither_is_nothing_found(wiring):
    out = await _call(wiring["build"](), code=PNG_SHA[:6], url="https://x.com/p.jpg")
    assert out["status"] == "nothing_found"
    assert wiring["vision"].calls == 0
    out2 = await _call(wiring["build"]())
    assert out2["status"] == "nothing_found"
    assert wiring["vision"].calls == 0


async def test_a_url_describes_a_video_with_the_video_model(wiring, monkeypatch):
    """The URL path resolves the media type from the response and routes to
    the per-modality describer exactly like a stored blob — a linked video
    uses [media.describe.video]'s model, never the image default."""
    async def fake_download(url: str) -> tuple[bytes, str]:
        return b"\x00\x00\x00\x18ftypmp42", "video/mp4"
    monkeypatch.setattr("nora.agent.tools._download_media", fake_download)
    tool = wiring["build"](
        describe_models={"image": "test/image-model", "video": "test/video-model"},
    )
    out = await _call(tool, url="https://x.com/clip.mp4")
    assert out["status"] == "ok"
    # CountingVision records no model; a video WITHOUT a named describer is
    # `unavailable`, which the video-model wiring above must have avoided.
    assert wiring["vision"].calls == 1
    # And the same URL with video unconfigured is honestly `unavailable`.
    out2 = await _call(wiring["build"](), url="https://x.com/clip.mp4")
    assert out2["status"] == "unavailable"


async def test_the_bracketed_marker_resolves_too(wiring):
    """The model is shown `[image a3f2c1]` and may quote the whole marker back."""
    await _append_image(wiring["store"], conversation_id="g1", sha256=PNG_SHA)
    out = await _call(wiring["build"](), code=f"[image {PNG_SHA[:6]}]")
    assert out["status"] == "ok"


# ---- the cache: no second MiMo call for the same content hash (§3, criterion 5)

async def test_a_second_fetch_of_the_same_hash_makes_no_mimo_call(wiring):
    """The cache is keyed by sha256, checked before any MiMo call. Reverting the
    cache lookup in `media_fetch` turns this red: the count becomes 2."""
    await _append_image(wiring["store"], conversation_id="g1", sha256=PNG_SHA)
    tool = wiring["build"]()
    first = await _call(tool, code=PNG_SHA[:6])
    second = await _call(tool, code=PNG_SHA[:6])
    assert first["description"] == second["description"] == "A red one-pixel square."
    assert wiring["vision"].calls == 1, "the second fetch called MiMo again"
    assert wiring["metrics"].get("nora_media_fetch_total", status="ok", cached="true") == 1
    assert wiring["metrics"].get("nora_media_fetch_total", status="ok", cached="false") == 1


async def test_the_cache_survives_across_tags_not_just_within_one(wiring):
    """The cache lives on the registry (per process), so a fresh tool built for a
    later tag still hits it — a per-tag cache would never hit."""
    await _append_image(wiring["store"], conversation_id="g1", sha256=PNG_SHA)
    await _call(wiring["build"](), code=PNG_SHA[:6])       # tag 1
    await _call(wiring["build"](), code=PNG_SHA[:6])       # tag 2, new tool, same cache
    assert wiring["vision"].calls == 1


# ---- VisionCache as a read-through over the durable store (Task 10) ----------
# The defect this closes: the LRU is per-process, so every deploy threw away
# every description and the next sighting of every image paid MiMo again. Nora
# restarts often; stickers repeat over weeks.

async def test_a_cold_process_still_hits_the_durable_store():
    store = FakeMediaDescriptionStore()
    store.descriptions["a" * 64] = "a cartoon cat"
    cache = VisionCache(store=store)

    assert cache.get("a" * 64) is None, "memory is genuinely cold"
    assert await cache.aget("a" * 64) == "a cartoon cat"
    assert cache.get("a" * 64) == "a cartoon cat", "a durable hit warms memory"


async def test_a_memory_hit_never_touches_the_store():
    store = FakeMediaDescriptionStore()
    cache = VisionCache(store=store)
    cache.put("a" * 64, "a cartoon cat")

    assert await cache.aget("a" * 64) == "a cartoon cat"
    assert store.described == []


async def test_a_store_outage_degrades_to_a_miss_not_an_error():
    cache = VisionCache(store=FakeMediaDescriptionStore(unavailable=True))

    assert await cache.aget("a" * 64) is None
    await cache.aput("a" * 64, "a cartoon cat", model="mimo")
    assert cache.get("a" * 64) == "a cartoon cat", "memory still works"


async def test_aput_writes_only_the_content_level_no_sticker_identity():
    """`media_fetch` has no platform-side sticker identity for an image a
    human pointed at by handle — only content. `aput` must write the CONTENT
    level (`describe`) with an empty platform/token, never invent one."""
    store = FakeMediaDescriptionStore()
    cache = VisionCache(store=store)

    await cache.aput("a" * 64, "a cartoon cat", model="mimo")

    assert store.remembered == [("", "", "a" * 64)]
    assert await store.describe(sha256="a" * 64) == "a cartoon cat"


async def test_a_durable_hit_still_runs_the_identifier_screen_on_serve(wiring):
    """The property `VisionCache`'s docstring argues for: a hit at EITHER tier
    can never bypass `scan_identifiers`, because the screen runs on every
    SERVE, in the tool — never at store time. Simulate a cold process (memory
    empty, durable store already holds a description with a UUID in it) and
    confirm the served result is still screened."""
    uuid = "12345678-1234-1234-1234-1234567890ab"
    descriptions = FakeMediaDescriptionStore()
    descriptions.descriptions[PNG_SHA] = f"A screenshot showing id {uuid}."
    cache = VisionCache(store=descriptions)
    await _append_image(wiring["store"], conversation_id="g1", sha256=PNG_SHA)

    tool = wiring["build"](vision_cache=cache)
    out = await _call(tool, code=PNG_SHA[:6])

    assert out["status"] == "ok"
    assert out.get("withheld") is True
    assert uuid not in repr(out)
    # The durable hit made no MiMo call — it warmed memory instead.
    assert wiring["vision"].calls == 0


async def test_media_fetch_threads_the_real_model_name_not_a_hardcoded_one(wiring):
    """`aput`'s `model` must be whatever the registry's vision client is
    actually configured with — hardcoding it would silently mislabel every
    future model change in the durable `media_descriptions` row."""
    await _append_image(wiring["store"], conversation_id="g1", sha256=PNG_SHA)
    descriptions = FakeMediaDescriptionStore()
    cache = VisionCache(store=descriptions)

    tool = wiring["build"](vision_cache=cache,
                           describe_models={"image": "opencode-go/mimo-v2.5"})
    out = await _call(tool, code=PNG_SHA[:6])

    assert out["status"] == "ok"
    assert descriptions.remembered_models == ["opencode-go/mimo-v2.5"]


# ---- the untrusted-egress screen, on EVERY serve (§4, criterion 6) -----------

async def test_a_transcribed_uuid_is_screened_out_even_on_a_cache_hit(wiring):
    """`scan_identifiers` runs on every SERVE, not once at store time, so a cache
    hit cannot bypass it. A UUID MiMo read off the image never reaches the
    result — reverting the screen turns this red on the UUID."""
    uuid = "12345678-1234-1234-1234-1234567890ab"
    wiring["vision"].description = f"A screenshot of a settings page showing id {uuid}."
    await _append_image(wiring["store"], conversation_id="g1", sha256=PNG_SHA)
    tool = wiring["build"]()
    first = await _call(tool, code=PNG_SHA[:6])
    assert first["status"] == "ok"
    assert first.get("withheld") is True
    assert "description" not in first
    assert uuid not in repr(first)
    # Second serve is a cache HIT (no new MiMo call) and is screened all the same.
    second = await _call(tool, code=PNG_SHA[:6])
    assert uuid not in repr(second)
    assert wiring["vision"].calls == 1


async def test_the_screen_does_not_catch_a_bare_local_number_and_says_so(wiring):
    """Honest about the residual risk: `scan_identifiers` does NOT match a bare
    local number (R-18's standing blind spot), so a business card MiMo
    transcribes as `9000 0101` PASSES the screen and is served. This pins the
    limitation rather than claiming a protection the tool does not deliver."""
    wiring["vision"].description = "A business card. The number reads 9000 0101."
    await _append_image(wiring["store"], conversation_id="g1", sha256=PNG_SHA)
    out = await _call(wiring["build"](), code=PNG_SHA[:6])
    assert out["status"] == "ok"
    assert "9000 0101" in out["description"]


async def test_a_transcribed_e164_number_is_screened_out(wiring):
    """The other half of the same honesty: a `+`-prefixed E.164 IS caught."""
    wiring["vision"].description = "A contact card for +85290000101."
    await _append_image(wiring["store"], conversation_id="g1", sha256=PNG_SHA)
    out = await _call(wiring["build"](), code=PNG_SHA[:6])
    assert out["status"] == "ok"
    assert out.get("withheld") is True
    assert "+85290000101" not in repr(out)


# ---- resolution: scope, window, ambiguity (§1, criterion 1) ------------------

async def test_a_handle_from_another_conversation_is_nothing_found(wiring):
    """R-21: resolved WITHIN this conversation's media_json only. An image in g2
    is un-resolvable in g1 — nothing_found, never described."""
    await _append_image(wiring["store"], conversation_id="g2", sha256=PNG_SHA)
    out = await _call(wiring["build"](), code=PNG_SHA[:6])
    assert out["status"] == "nothing_found"
    assert wiring["vision"].calls == 0


async def test_a_bare_image_with_no_handle_is_nothing_found(wiring):
    """The tool must not fire on a bare `[image]` with no code — custody holds
    nothing to resolve, so nothing_found and no MiMo call."""
    out = await _call(wiring["build"](), code="[image]")
    assert out["status"] == "nothing_found"
    assert wiring["vision"].calls == 0


async def test_two_blobs_sharing_the_prefix_are_ambiguous_never_pick_first(wiring):
    """24 bits collide (§1). Two DISTINCT blobs sharing the 6-hex handle is a
    question, not a default — ambiguous, listing both, and no MiMo call."""
    a = "abcdef" + "0" * 58
    b = "abcdef" + "1" * 58
    await _append_image(wiring["store"], conversation_id="g1", sha256=a, ts=100)
    await _append_image(wiring["store"], conversation_id="g1", sha256=b, ts=101)
    out = await _call(wiring["build"](), code="abcdef")
    assert out["status"] == "ambiguous"
    assert set(out["could_be"]) == {"abcdef"}  # both render to the same 6-hex
    assert wiring["vision"].calls == 0


async def test_the_same_blob_in_two_rows_is_not_an_ambiguity(wiring):
    """A forwarded meme is one blob in two rows — dedup by full sha256, so it
    resolves rather than reading as a collision."""
    await _append_image(wiring["store"], conversation_id="g1", sha256=PNG_SHA, ts=100)
    await _append_image(wiring["store"], conversation_id="g1", sha256=PNG_SHA, ts=101)
    out = await _call(wiring["build"](), code=PNG_SHA[:6])
    assert out["status"] == "ok"


# ---- non-image blobs, before any MiMo call (§4, criterion 4) -----------------

# `test_a_voice_note_is_not_an_image_before_any_mimo_call` MOVED and SPLIT
# (2026-08-17 spec). It asserted one status for two different situations, which
# the per-modality split separates: a voice note with audio switched off is now
# `unavailable` (a wiring gap — «I couldn't check»), while a blob no describer
# reads stays `not_an_image`. Both assertions, including the «before any model
# call» half this one existed for, live in `tests/test_media_describers.py`.


# ---- unavailable: absent, corrupt, and the wiring gate (§4/§5, criterion 3) --

async def test_an_absent_blob_is_unavailable_not_nothing_found(wiring):
    """The row names a blob whose bytes are not in the store — `store.get`
    returns None. That is `unavailable` (I can't open it), never nothing_found
    (there was never one) and never a raise."""
    absent = "b" * 64
    await _append_image(wiring["store"], conversation_id="g1", sha256=absent)
    out = await _call(wiring["build"](), code="b" * 6)
    assert out["status"] == "unavailable"


async def test_a_wrong_key_blob_raises_and_is_wrapped_into_unavailable(wiring):
    """The day `MEDIA_ENCRYPTION_KEY` rotates: the blob is present but decrypts
    under the wrong key, so `store.get` RAISES `InvalidTag`. Both None and the
    raise map to `unavailable`, and the tool never raises into the loop."""
    wrong_key_store = FilesystemBlobStore(
        wiring["tmp_path"] / "media", cipher=BlobCipher.from_base64(KEY_B),
    )
    await _append_image(wiring["store"], conversation_id="g1", sha256=PNG_SHA)
    tool = wiring["build"](blob_store=wrong_key_store)
    out = await _call(tool, code=PNG_SHA[:6])
    assert out["status"] == "unavailable"


async def test_a_hung_model_is_unavailable_not_a_guess(wiring):
    wiring["vision"].available = False
    await _append_image(wiring["store"], conversation_id="g1", sha256=PNG_SHA)
    out = await _call(wiring["build"](), code=PNG_SHA[:6])
    assert out["status"] == "unavailable"
    assert "description" not in out


async def test_a_describe_that_raises_is_wrapped_not_propagated(wiring):
    """Belt-and-braces for `test_a_tool_never_raises_into_the_agent_loop`: even
    if the vision collaborator raises, the tool lands on `unavailable`."""
    class _Exploding:
        async def describe(self, *, data, media_type, prompt="", model="",
                           provider="omniroute", max_bytes: int | None = None):
            raise RuntimeError("vision on fire")

    await _append_image(wiring["store"], conversation_id="g1", sha256=PNG_SHA)
    out = await _call(wiring["build"](vision_source=_Exploding()), code=PNG_SHA[:6])
    assert out["status"] == "unavailable"


async def test_the_store_going_down_is_unavailable_not_a_raise(wiring, monkeypatch):
    async def boom(*a, **kw):
        raise RuntimeError("db down")

    monkeypatch.setattr(wiring["store"], "recent", boom)
    out = await _call(wiring["build"](), code=PNG_SHA[:6])
    assert out["status"] == "unavailable"


# ---- inert-by-config: the gate (§5) ------------------------------------------

async def test_no_blob_store_is_unavailable_the_inert_by_config_shape(wiring):
    """`[media] enabled=false` or no key → no blob store → the tool answers
    `unavailable`, the route-with-no-key shape, never nothing_found."""
    out = await _call(wiring["build"](blob_store=None), code=PNG_SHA[:6])
    assert out["status"] == "unavailable"


async def test_no_vision_client_is_unavailable_reachable_omniroute_gate(wiring):
    await _append_image(wiring["store"], conversation_id="g1", sha256=PNG_SHA)
    out = await _call(wiring["build"](vision_source=None), code=PNG_SHA[:6])
    assert out["status"] == "unavailable"


# ---- R-33: the named rule test (DEC-56) --------------------------------------

async def test_a_stored_image_is_read_only_within_scope_and_screened_on_serve(wiring):
    """R-33, in one place: `media_fetch` reads a blob ONLY from this
    conversation's custody, and the model-written description is untrusted egress
    screened on EVERY serve.

    Two halves, both load-bearing and both reverting to a real leak:
    * SCOPE (R-21). An image that exists only in another conversation is
      un-resolvable here — `nothing_found`, never described. Widening the resolver
      past `bound_conversation_id` would pull another room's photo across the
      boundary.
    * UNTRUSTED EGRESS (design/14 §2.1). An ACI/UUID the vision model transcribed
      off the image is dropped with a note, on the FIRST serve and on the cache
      hit alike — reverting the `scan_identifiers` screen puts the id in the
      result. The per-call vision timeout and byte ceiling that keep a slow MiMo
      out of the loop's silent `invocation_timeout` are pinned in
      `tests/test_vision.py`.
    """
    uuid = "12345678-1234-1234-1234-1234567890ab"

    # Scope: the image lives in g2; this tool is bound to g1.
    await _append_image(wiring["store"], conversation_id="g2", sha256=PNG_SHA)
    cross = await _call(wiring["build"](), code=PNG_SHA[:6])
    assert cross["status"] == "nothing_found"
    assert wiring["vision"].calls == 0

    # Untrusted egress: same image, now in g1, description carries a UUID.
    wiring["vision"].description = f"A form pre-filled with account {uuid}."
    await _append_image(wiring["store"], conversation_id="g1", sha256=PNG_SHA)
    tool = wiring["build"]()
    served = await _call(tool, code=PNG_SHA[:6])
    assert served["status"] == "ok" and served.get("withheld") is True
    assert uuid not in repr(served)
    cached = await _call(tool, code=PNG_SHA[:6])       # cache hit, screened all the same
    assert uuid not in repr(cached)
    assert wiring["vision"].calls == 1


def test_the_advertised_schema_names_the_parameter_code(wiring):
    """The regression guard for the defect that motivated the rename.

    A live trace (Langfuse, 2026-08-10) shows the model calling this tool with
    `{"code": "bae370"}` and failing schema validation, because the parameter
    was `handle` while the docstring says "the short code" four times. The model
    used the word the prose uses.

    Every other test here calls `tool._tool_func(**kw)` directly, which bypasses
    Strands and therefore bypasses the SCHEMA — the exact surface that failed.
    Renaming the parameter back turns those tests red on a TypeError, so the
    rename is guarded, but nothing asserted what is actually advertised to the
    model. This does.

    The generic surface (DEC-56 extension, 2026-08-31) adds `url` — the
    cross-tool read path — so the schema now advertises BOTH parameters, with
    neither required (exactly one at call time; the tool answers
    `nothing_found` for zero or two).
    """
    spec = wiring["build"]().tool_spec
    schema = spec["inputSchema"]["json"]

    # Neither parameter is required (both default to ""); the tool answers
    # `nothing_found` for zero targets or two. The JSON schema therefore
    # carries no `required` list at all.
    assert "required" not in schema
    assert "handle" not in schema["properties"]
    # The description the model reads must use the same words as the
    # parameters, or the mismatch simply returns in a new place.
    assert "code" in schema["properties"]["code"]["description"]
    assert "http" in schema["properties"]["url"]["description"]


# ---- MediaComprehender (issue #116): bytes to a comprehension ---------------
# One model pass, three fields. These tests pin the adapter's own contract in
# isolation; the job that schedules it is `tests/unit/test_media_comprehension.py`.


class _RecordingVisionClient:
    """A minimal `VisionClient` stand-in that records every `describe` call it
    received. Recording is the point: `client.calls == []` is what proves a
    lottie's JSON bytes never reached a vision model — a double that didn't
    record would make that assertion vacuous."""

    def __init__(self, *, result: VisionResult | None = None) -> None:
        self.result = result or VisionResult(
            available=True,
            description='{"title": "a cat", "description": "a cartoon cat", '
                        '"content": ""}',
        )
        self.calls: list[tuple[bytes, str, str]] = []
        self.completions: list[str] = []

    async def describe(self, *, data: bytes, media_type: str,
                       prompt: str = "", model: str = "",
                       provider: str = "omniroute",
                       max_bytes: int | None = None) -> VisionResult:
        self.calls.append((data, media_type, prompt))
        return self.result

    async def complete(self, *, prompt: str, text: str, model: str = "") -> str:
        self.completions.append(text)
        return '{"title": "a cat", "description": "a cartoon cat"}'


def _comprehender(client) -> MediaComprehender:
    return MediaComprehender(
        client=client,
        describe_models={"image": "mimo", "video": "k3", "audio": "mimo"},
        text_model="mimo",
    )


async def test_a_comprehender_refuses_a_non_readable_type_without_calling_out():
    """Discord lottie stickers are JSON. Handing JSON to a vision model is not
    a degraded read, it is a wrong one — and the model would answer something."""
    client = _RecordingVisionClient()

    got = await _comprehender(client).describe(
        data=b'{"v":"5.5"}', media_type="application/json")

    assert got.available is False
    assert got.reason == "unsupported_format"
    assert client.calls == [], "nothing was sent"


async def test_a_modality_with_no_configured_describer_is_unavailable():
    """R-27, at the adapter. A wiring gap is «I could not look», never «there
    was nothing there» — and never a silent fallback to the image model, which
    would send a video to a router that drops it and answer about the text it
    did receive."""
    client = _RecordingVisionClient()
    comprehender = MediaComprehender(client=client, describe_models={"image": "mimo"})

    got = await comprehender.describe(data=b"\x00\x00", media_type="video/mp4")

    assert got.available is False
    assert got.reason == "no_video_describer"
    assert client.calls == []


async def test_an_unavailable_vision_result_never_becomes_a_blank_comprehension():
    """R-51. A timeout must not turn into `[image a3f2c1: ]`."""
    client = _RecordingVisionClient(
        result=VisionResult(available=False, reason="timeout"))

    got = await _comprehender(client).describe(
        data=b"\x89PNG...", media_type="image/png")

    assert got.available is False
    assert got.reason == "timeout"
    assert (got.title, got.text, got.content) == ("", "", "")


async def test_one_pass_carries_all_three_fields():
    client = _RecordingVisionClient(result=VisionResult(
        available=True,
        description='{"title": "a whiteboard", "description": "a whiteboard in '
                    'a meeting room", "content": "Q3 targets: 40%"}',
    ))

    got = await _comprehender(client).describe(
        data=b"\x89PNG...", media_type="image/png")

    assert got == MediaDescription(
        available=True, title="a whiteboard",
        text="a whiteboard in a meeting room", content="Q3 targets: 40%",
    )
    assert len(client.calls) == 1, "three fields, ONE call"


async def test_a_label_picks_the_authored_question():
    """The prompt is selected by LABEL — a system-controlled fact (the
    platform's own sticker flag plus the blob's MIME type), never anything a
    room typed. A sticker is asked what it depicts and explicitly not what the
    sender meant by it; a photo is asked what it shows."""
    client = _RecordingVisionClient()

    await _comprehender(client).describe(
        data=b"\x89PNG...", media_type="image/png", label="sticker")

    prompt = client.calls[0][2]
    assert "chat sticker" in prompt
    assert "do not interpret the sender's mood or intent" in prompt.lower()


async def test_prose_instead_of_json_becomes_the_description():
    """FAIL-SOFT, not fail-blank. A model that answers in a paragraph has still
    READ the bytes; reporting `unavailable` for it would throw away a real
    reading over punctuation, and `unavailable` means «I could not look»."""
    client = _RecordingVisionClient(result=VisionResult(
        available=True, description="a cartoon cat, sitting on a laptop"))

    got = await _comprehender(client).describe(
        data=b"\x89PNG...", media_type="image/png")

    assert got.available is True
    assert got.text == "a cartoon cat, sitting on a laptop"
    assert got.title == ""


async def test_a_fenced_json_answer_is_not_lost_to_its_fence():
    client = _RecordingVisionClient(result=VisionResult(
        available=True,
        description='```json\n{"title": "a cat", "description": "a cat", '
                    '"content": ""}\n```',
    ))

    got = await _comprehender(client).describe(
        data=b"\x89PNG...", media_type="image/png")

    assert got.title == "a cat"


async def test_summarize_is_a_text_call_and_never_a_second_listen():
    """The audio lane. A voice note's content is already in hand — SenseVoice
    transcribed it at ingest — so the title and description come from a cheap
    text pass over those words, never from asking a model to listen again."""
    client = _RecordingVisionClient()

    got = await _comprehender(client).summarize(text="喂你今日得唔得閒啊")

    assert got.available is True
    assert client.calls == [], "no vision call for a transcript already held"
    assert client.completions == ["喂你今日得唔得閒啊"]
    assert got.content == "", "the caller already holds the words"


async def test_summarize_with_no_text_model_is_a_wiring_gap():
    """R-27 again: `no_text_describer` is `unavailable`. The caller keeps the
    transcript it has and stores it with no title — the words are the evidence,
    the title is a convenience."""
    client = _RecordingVisionClient()
    comprehender = MediaComprehender(client=client, describe_models={"image": "m"})

    got = await comprehender.summarize(text="喂你今日得唔得閒啊")

    assert got.available is False
    assert got.reason == "no_text_describer"


# ---- the two layers (issue #116) -------------------------------------------
#
# Layer 1 serves what the spawned comprehension already read at ingest — no
# model call, no wait, and the SAME reading the transcript rendered a line of,
# so the tool cannot contradict the prompt about what a picture shows.
#
# Layer 2 takes a caller-supplied question, reads the media AGAIN with it,
# bypasses the cache on read and WRITES NOTHING BACK. That is not an
# optimisation: the cache rests on "a description is a pure function of the
# BYTES, so a sha256 hit is valid forever", and a query-conditioned answer is a
# function of (bytes, question). Writing one to `media_objects.description`
# would poison that hash for every room and every future sighting of the same
# image.


def _comprehended(sha: str, **kw) -> MediaObject:
    base: dict = {
        "sha256": sha, "media_type": "image/png", "size": len(RED_PNG),
        "source": "inbound", "title": "a red square",
        "description": "a single red pixel",
        "content": "", "created_at_ms": 1,
    }
    base.update(kw)
    return MediaObject(**base)


async def test_layer_1_serves_the_stored_comprehension_without_a_model_call(wiring):
    """The ordinary answer. Everything the model pass produced — title, what it
    shows, and everything written in it — because `media_fetch` is exactly the
    surface the untruncated `content` was kept for."""
    await _append_image(wiring["store"], conversation_id="g1", sha256=PNG_SHA)
    index = FakeMediaObjectStore([_comprehended(
        PNG_SHA, content="a whiteboard reading Q3 targets 40%")])

    out = await _call(
        wiring["build"](media_store=index), code=PNG_SHA[:6])

    assert out["status"] == "ok"
    assert "a red square" in out["description"]
    assert "a single red pixel" in out["description"]
    assert "Q3 targets 40%" in out["description"]
    assert wiring["vision"].calls == 0, "the read already happened at ingest"


async def test_an_uncomprehended_medium_still_falls_through_to_a_live_read(wiring):
    """The positive control for the test above AND the honest degrade: an index
    row with all three fields empty is an INDEX ENTRY, not a description of
    nothing. Serving it as an answer would return `ok` with nothing in it."""
    await _append_image(wiring["store"], conversation_id="g1", sha256=PNG_SHA)
    index = FakeMediaObjectStore([_comprehended(
        PNG_SHA, title="", description="", content="")])

    out = await _call(wiring["build"](media_store=index), code=PNG_SHA[:6])

    assert out["status"] == "ok"
    assert wiring["vision"].calls == 1


async def test_layer_2_reads_again_with_the_question_and_bypasses_the_cache(wiring):
    """A question the stored reading does not answer. The cache is not consulted
    (a hit would answer somebody else's question) and the vision call carries
    the caller's words inside an AUTHORED frame."""
    await _append_image(wiring["store"], conversation_id="g1", sha256=PNG_SHA)
    index = FakeMediaObjectStore([_comprehended(PNG_SHA)])
    cache = VisionCache()
    cache.put(PNG_SHA, "a stale cached description")
    tool = wiring["build"](media_store=index, vision_cache=cache)

    out = await _call(
        tool, code=PNG_SHA[:6], question="what colour is the pixel")

    assert out["status"] == "ok"
    assert out["description"] == "A red one-pixel square."
    assert wiring["vision"].calls == 1, "a question always costs a real look"


async def test_layer_2_writes_nothing_to_either_cache_tier(wiring):
    """**The soundness property.** A query-conditioned answer written back to
    `media_objects.description` would poison that sha256 for every room and
    every future sighting — the transcript would then render one room's answer
    to one room's question as what the picture IS."""
    await _append_image(wiring["store"], conversation_id="g1", sha256=PNG_SHA)
    index = FakeMediaObjectStore([_comprehended(PNG_SHA)])
    cache = VisionCache()
    tool = wiring["build"](media_store=index, vision_cache=cache)

    await _call(tool, code=PNG_SHA[:6], question="what colour is the pixel")

    assert len(cache) == 0, "a question-conditioned answer is never cached"
    assert index.comprehended == [], "and never lands on the row"

    # POSITIVE CONTROL — the same tool, the same image, NO question: this one
    # is a pure function of the bytes and does populate the cache, so the
    # assertion above is not passing because nothing writes the cache at all.
    fresh = FakeMediaObjectStore([_comprehended(PNG_SHA, title="", description="")])
    await _call(
        wiring["build"](media_store=fresh, vision_cache=cache), code=PNG_SHA[:6])
    assert len(cache) == 1


async def test_layer_2_is_screened_on_the_way_out_like_every_other_serve(wiring):
    """The authored-prompt invariant is broken here deliberately, and the
    barrier that makes it acceptable is unchanged: `scan_identifiers` runs on
    EVERY serve, this one included."""
    uuid = "12345678-1234-1234-1234-1234567890ab"
    await _append_image(wiring["store"], conversation_id="g1", sha256=PNG_SHA)
    wiring["vision"].description = f"The form says account {uuid}."
    index = FakeMediaObjectStore([_comprehended(PNG_SHA)])

    out = await _call(
        wiring["build"](media_store=index),
        code=PNG_SHA[:6], question="what account number is on the form",
    )

    assert out["status"] == "ok" and out.get("withheld") is True
    assert uuid not in repr(out)


async def test_the_schema_advertises_the_question_parameter(wiring):
    """The model can only reach layer 2 through the schema. Every other test
    here calls `tool._tool_func(**kw)` directly, which bypasses Strands and
    therefore bypasses the schema — the exact surface that failed once before
    (the `handle`/`code` rename)."""
    schema = wiring["build"]().tool_spec["inputSchema"]["json"]
    assert "question" in schema["properties"]
    assert "required" not in schema

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
from nora.domain.media import MediaBlob
from nora.domain.models import ConversationTarget, NormalizedMessage
from nora.infra.blobs import FilesystemBlobStore
from nora.infra.blobs.cipher import BlobCipher
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.vision import VisionCache, VisionResult
from nora.obs.metrics import Metrics
from nora.persons import load_persons
from tests.fakes import FakeHoncho, SinkSender
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

    async def describe(self, *, data: bytes, media_type: str) -> VisionResult:
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
                          message_cap=NATIVE_TOOL_BUDGETS["send_reply"], metrics=Metrics())
    metrics = Metrics()
    persons = load_persons(persons_file)
    room = Room(platform="signal", conversation_id="g1", roster_source=None, persons=persons)
    vision = CountingVision()
    cache = VisionCache()

    def build(*, vision_source=vision, blob_store=blobs, vision_cache=cache):
        tools = build_tools(
            store=store, conversation_id="g1", conversation_type="group",
            persons=persons, honcho=FakeHoncho(), deliverer=deliverer, metrics=metrics,
            room=room, transit_source=None,
            vision_source=vision_source, blob_store=blob_store, vision_cache=vision_cache,
            question="what is in the photo", context_window=40,
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
    out = await _call(wiring["build"](), handle=PNG_SHA[:6])
    assert out["status"] == "ok"
    assert out["description"] == "A red one-pixel square."
    assert out["handle"] == PNG_SHA[:6]
    assert wiring["vision"].calls == 1


async def test_the_bracketed_marker_resolves_too(wiring):
    """The model is shown `[image a3f2c1]` and may quote the whole marker back."""
    await _append_image(wiring["store"], conversation_id="g1", sha256=PNG_SHA)
    out = await _call(wiring["build"](), handle=f"[image {PNG_SHA[:6]}]")
    assert out["status"] == "ok"


# ---- the cache: no second MiMo call for the same content hash (§3, criterion 5)

async def test_a_second_fetch_of_the_same_hash_makes_no_mimo_call(wiring):
    """The cache is keyed by sha256, checked before any MiMo call. Reverting the
    cache lookup in `media_fetch` turns this red: the count becomes 2."""
    await _append_image(wiring["store"], conversation_id="g1", sha256=PNG_SHA)
    tool = wiring["build"]()
    first = await _call(tool, handle=PNG_SHA[:6])
    second = await _call(tool, handle=PNG_SHA[:6])
    assert first["description"] == second["description"] == "A red one-pixel square."
    assert wiring["vision"].calls == 1, "the second fetch called MiMo again"
    assert wiring["metrics"].get("nora_media_fetch_total", status="ok", cached="true") == 1
    assert wiring["metrics"].get("nora_media_fetch_total", status="ok", cached="false") == 1


async def test_the_cache_survives_across_tags_not_just_within_one(wiring):
    """The cache lives on the registry (per process), so a fresh tool built for a
    later tag still hits it — a per-tag cache would never hit."""
    await _append_image(wiring["store"], conversation_id="g1", sha256=PNG_SHA)
    await _call(wiring["build"](), handle=PNG_SHA[:6])       # tag 1
    await _call(wiring["build"](), handle=PNG_SHA[:6])       # tag 2, new tool, same cache
    assert wiring["vision"].calls == 1


# ---- the untrusted-egress screen, on EVERY serve (§4, criterion 6) -----------

async def test_a_transcribed_uuid_is_screened_out_even_on_a_cache_hit(wiring):
    """`scan_identifiers` runs on every SERVE, not once at store time, so a cache
    hit cannot bypass it. A UUID MiMo read off the image never reaches the
    result — reverting the screen turns this red on the UUID."""
    uuid = "12345678-1234-1234-1234-1234567890ab"
    wiring["vision"].description = f"A screenshot of a settings page showing id {uuid}."
    await _append_image(wiring["store"], conversation_id="g1", sha256=PNG_SHA)
    tool = wiring["build"]()
    first = await _call(tool, handle=PNG_SHA[:6])
    assert first["status"] == "ok"
    assert first.get("withheld") is True
    assert "description" not in first
    assert uuid not in repr(first)
    # Second serve is a cache HIT (no new MiMo call) and is screened all the same.
    second = await _call(tool, handle=PNG_SHA[:6])
    assert uuid not in repr(second)
    assert wiring["vision"].calls == 1


async def test_the_screen_does_not_catch_a_bare_local_number_and_says_so(wiring):
    """Honest about the residual risk: `scan_identifiers` does NOT match a bare
    local number (R-18's standing blind spot), so a business card MiMo
    transcribes as `9000 0101` PASSES the screen and is served. This pins the
    limitation rather than claiming a protection the tool does not deliver."""
    wiring["vision"].description = "A business card. The number reads 9000 0101."
    await _append_image(wiring["store"], conversation_id="g1", sha256=PNG_SHA)
    out = await _call(wiring["build"](), handle=PNG_SHA[:6])
    assert out["status"] == "ok"
    assert "9000 0101" in out["description"]


async def test_a_transcribed_e164_number_is_screened_out(wiring):
    """The other half of the same honesty: a `+`-prefixed E.164 IS caught."""
    wiring["vision"].description = "A contact card for +85290000101."
    await _append_image(wiring["store"], conversation_id="g1", sha256=PNG_SHA)
    out = await _call(wiring["build"](), handle=PNG_SHA[:6])
    assert out["status"] == "ok"
    assert out.get("withheld") is True
    assert "+85290000101" not in repr(out)


# ---- resolution: scope, window, ambiguity (§1, criterion 1) ------------------

async def test_a_handle_from_another_conversation_is_nothing_found(wiring):
    """R-21: resolved WITHIN this conversation's media_json only. An image in g2
    is un-resolvable in g1 — nothing_found, never described."""
    await _append_image(wiring["store"], conversation_id="g2", sha256=PNG_SHA)
    out = await _call(wiring["build"](), handle=PNG_SHA[:6])
    assert out["status"] == "nothing_found"
    assert wiring["vision"].calls == 0


async def test_a_bare_image_with_no_handle_is_nothing_found(wiring):
    """The tool must not fire on a bare `[image]` with no code — custody holds
    nothing to resolve, so nothing_found and no MiMo call."""
    out = await _call(wiring["build"](), handle="[image]")
    assert out["status"] == "nothing_found"
    assert wiring["vision"].calls == 0


async def test_two_blobs_sharing_the_prefix_are_ambiguous_never_pick_first(wiring):
    """24 bits collide (§1). Two DISTINCT blobs sharing the 6-hex handle is a
    question, not a default — ambiguous, listing both, and no MiMo call."""
    a = "abcdef" + "0" * 58
    b = "abcdef" + "1" * 58
    await _append_image(wiring["store"], conversation_id="g1", sha256=a, ts=100)
    await _append_image(wiring["store"], conversation_id="g1", sha256=b, ts=101)
    out = await _call(wiring["build"](), handle="abcdef")
    assert out["status"] == "ambiguous"
    assert set(out["could_be"]) == {"abcdef"}  # both render to the same 6-hex
    assert wiring["vision"].calls == 0


async def test_the_same_blob_in_two_rows_is_not_an_ambiguity(wiring):
    """A forwarded meme is one blob in two rows — dedup by full sha256, so it
    resolves rather than reading as a collision."""
    await _append_image(wiring["store"], conversation_id="g1", sha256=PNG_SHA, ts=100)
    await _append_image(wiring["store"], conversation_id="g1", sha256=PNG_SHA, ts=101)
    out = await _call(wiring["build"](), handle=PNG_SHA[:6])
    assert out["status"] == "ok"


# ---- non-image blobs, before any MiMo call (§4, criterion 4) -----------------

async def test_a_voice_note_is_not_an_image_before_any_mimo_call(wiring):
    await _append_image(wiring["store"], conversation_id="g1",
                        sha256="a" * 64, media_type="audio/aac")
    out = await _call(wiring["build"](), handle="a" * 6)
    assert out["status"] == "not_an_image"
    assert wiring["vision"].calls == 0


# ---- unavailable: absent, corrupt, and the wiring gate (§4/§5, criterion 3) --

async def test_an_absent_blob_is_unavailable_not_nothing_found(wiring):
    """The row names a blob whose bytes are not in the store — `store.get`
    returns None. That is `unavailable` (I can't open it), never nothing_found
    (there was never one) and never a raise."""
    absent = "b" * 64
    await _append_image(wiring["store"], conversation_id="g1", sha256=absent)
    out = await _call(wiring["build"](), handle="b" * 6)
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
    out = await _call(tool, handle=PNG_SHA[:6])
    assert out["status"] == "unavailable"


async def test_a_hung_model_is_unavailable_not_a_guess(wiring):
    wiring["vision"].available = False
    await _append_image(wiring["store"], conversation_id="g1", sha256=PNG_SHA)
    out = await _call(wiring["build"](), handle=PNG_SHA[:6])
    assert out["status"] == "unavailable"
    assert "description" not in out


async def test_a_describe_that_raises_is_wrapped_not_propagated(wiring):
    """Belt-and-braces for `test_a_tool_never_raises_into_the_agent_loop`: even
    if the vision collaborator raises, the tool lands on `unavailable`."""
    class _Exploding:
        async def describe(self, *, data, media_type):
            raise RuntimeError("vision on fire")

    await _append_image(wiring["store"], conversation_id="g1", sha256=PNG_SHA)
    out = await _call(wiring["build"](vision_source=_Exploding()), handle=PNG_SHA[:6])
    assert out["status"] == "unavailable"


async def test_the_store_going_down_is_unavailable_not_a_raise(wiring, monkeypatch):
    async def boom(*a, **kw):
        raise RuntimeError("db down")

    monkeypatch.setattr(wiring["store"], "recent", boom)
    out = await _call(wiring["build"](), handle=PNG_SHA[:6])
    assert out["status"] == "unavailable"


# ---- inert-by-config: the gate (§5) ------------------------------------------

async def test_no_blob_store_is_unavailable_the_inert_by_config_shape(wiring):
    """`[media] enabled=false` or no key → no blob store → the tool answers
    `unavailable`, the route-with-no-key shape, never nothing_found."""
    out = await _call(wiring["build"](blob_store=None), handle=PNG_SHA[:6])
    assert out["status"] == "unavailable"


async def test_no_vision_client_is_unavailable_reachable_omniroute_gate(wiring):
    await _append_image(wiring["store"], conversation_id="g1", sha256=PNG_SHA)
    out = await _call(wiring["build"](vision_source=None), handle=PNG_SHA[:6])
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
    cross = await _call(wiring["build"](), handle=PNG_SHA[:6])
    assert cross["status"] == "nothing_found"
    assert wiring["vision"].calls == 0

    # Untrusted egress: same image, now in g1, description carries a UUID.
    wiring["vision"].description = f"A form pre-filled with account {uuid}."
    await _append_image(wiring["store"], conversation_id="g1", sha256=PNG_SHA)
    tool = wiring["build"]()
    served = await _call(tool, handle=PNG_SHA[:6])
    assert served["status"] == "ok" and served.get("withheld") is True
    assert uuid not in repr(served)
    cached = await _call(tool, handle=PNG_SHA[:6])       # cache hit, screened all the same
    assert uuid not in repr(cached)
    assert wiring["vision"].calls == 1

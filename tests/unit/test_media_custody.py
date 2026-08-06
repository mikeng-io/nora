"""Media custody (DEC-45): the taxonomy, the stage, and what the record claims.

The load-bearing property throughout is one sentence: **a handle in the body is
a promise the bytes exist.** Every failing path below therefore asserts on the
ABSENCE of a handle as much as on the counter, because a placeholder that
claims custody Nora does not have is the A9 violation this work exists to end,
merely relocated.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from nora.domain.media import (
    MediaBlob,
    MediaRef,
    body_with_handles,
    label_for,
    media_json,
    parse_media_type,
    placeholder_suffix,
    render_placeholder,
    resolve_media_type,
)
from nora.domain.models import NormalizedMessage
from nora.infra.blobs import FilesystemBlobStore
from nora.ingest.stages import media as media_stage
from nora.obs.metrics import Metrics
from nora.testing import FakeBlobStore, FakeMediaSource

JPEG = b"\xff\xd8\xff\xe0 pretend jpeg"
JPEG_SHA = hashlib.sha256(JPEG).hexdigest()


def _msg(*, body: str, refs: tuple[MediaRef, ...]) -> NormalizedMessage:
    return NormalizedMessage(
        platform="signal", conversation_id="g1", conversation_type="group",
        sender_id="+85212345678", sender_name="Miles", timestamp=1700,
        body=body, raw={}, media=refs,
    )


# --- the taxonomy -----------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("image/jpeg", ("image", "jpeg")),
        ("AUDIO/OGG; codecs=opus", ("audio", "ogg")),
        ("  video/mp4  ", ("video", "mp4")),
        ("application/vnd.oasis.opendocument.text",
         ("application", "vnd.oasis.opendocument.text")),
        # Not RFC 6838 shapes, and each must be rejected rather than half-read.
        ("nonsense", ("", "")),
        ("garbage/x", ("", "")),          # unregistered top-level type
        ("image/", ("", "")),
        ("image/jpeg/extra", ("", "")),
        ("", ("", "")),
    ],
)
def test_parse_media_type_follows_rfc_6838(raw, expected):
    assert parse_media_type(raw) == expected


def test_an_invented_top_level_type_cannot_become_a_label():
    """`garbage/x` is malformed, not merely unknown. Passing it through would
    let a sender name its own category."""
    assert label_for(MediaRef(platform_id="x", media_type="garbage/x")) == "file"


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        (MediaRef(platform_id="a", media_type="image/png"), "image"),
        (MediaRef(platform_id="a", media_type="image/gif"), "image"),
        (MediaRef(platform_id="a", media_type="video/mp4"), "video"),
        (MediaRef(platform_id="a", media_type="audio/mpeg"), "audio"),
        (MediaRef(platform_id="a", media_type="audio/aac", voice_note=True), "voice note"),
        (MediaRef(platform_id="a", media_type="text/plain"), "document"),
        (MediaRef(platform_id="a", media_type="application/pdf"), "document"),
        (MediaRef(platform_id="a", media_type="application/octet-stream"), "file"),
        (MediaRef(platform_id="a", media_type="font/woff2"), "file"),
        (MediaRef(platform_id="a"), "file"),
    ],
)
def test_label_for_names_what_arrived(ref, expected):
    assert label_for(ref) == expected


def test_a_voice_note_flag_on_a_non_audio_type_does_not_rename_it():
    """The flag qualifies `audio/*`; it does not override the type. A PDF
    marked `voiceNote` is a mislabelled PDF, not somebody speaking."""
    ref = MediaRef(platform_id="a", media_type="application/pdf", voice_note=True)
    assert label_for(ref) == "document"


def test_the_filename_is_only_ever_a_fallback_for_the_type():
    assert resolve_media_type(declared="image/png", filename="thing.pdf") == "image/png"
    assert resolve_media_type(declared="", filename="thing.pdf") == "application/pdf"
    assert resolve_media_type(declared="", filename="thing.unknownext") == ""


def test_a_label_never_contains_the_filename():
    """The old fallback rendered `[attachment: <filename>]` into the body Nora
    reasons over — sender-controlled text of unbounded shape."""
    hostile = MediaRef(platform_id="a", filename="call +85212345678 now.bin")
    assert label_for(hostile) == "file"
    assert "852" not in render_placeholder(label_for(hostile))


# --- the handle and the body rewrite ----------------------------------------


def test_a_placeholder_gains_the_handle_only_once_there_is_one():
    assert render_placeholder("image") == "[image]"
    assert render_placeholder("image", "a3f2c1") == "[image a3f2c1]"


def test_body_with_handles_rewrites_the_trailing_placeholder_run():
    body = "睇下呢個 [image] [voice note]"
    out = body_with_handles(body, ("image", "voice note"), ("a3f2c1", "8b04ee"))
    assert out == "睇下呢個 [image a3f2c1] [voice note 8b04ee]"


def test_a_failed_attachment_leaves_a_gap_rather_than_shifting_the_others():
    """Handles are matched to placeholders by INDEX. A missing one must render
    as no handle, not as the next attachment's."""
    body = "[image] [video]"
    out = body_with_handles(body, ("image", "video"), ("", "1c9d77"))
    assert out == "[image] [video 1c9d77]"


def test_a_body_whose_tail_is_not_the_expected_run_is_left_alone():
    """The precondition is checked, not assumed: replacing the k-th `[` by
    position would corrupt a body whose SENDER-SUPPLIED text contains one."""
    body = "look at [image] in the middle"
    assert body_with_handles(body, ("image",), ("a3f2c1",)) == body


def test_a_bracket_in_the_senders_own_text_survives_the_rewrite():
    body = "[not a placeholder] [image]"
    out = body_with_handles(body, ("image",), ("a3f2c1",))
    assert out == "[not a placeholder] [image a3f2c1]"


def test_placeholder_suffix_tolerates_fewer_handles_than_labels():
    assert placeholder_suffix(("image", "video"), ("a3f2c1",)) == "[image a3f2c1] [video]"


# --- what the row records ---------------------------------------------------


def test_media_json_is_null_when_nothing_is_held():
    """Not `[]`: a column that reads `[]` on every row is one nobody can
    filter, and every row in this database today holds nothing."""
    assert media_json(()) is None


def test_media_json_records_the_four_fields_and_no_bytes():
    """Type and key, never content. She cannot read the bytes and this does not
    change that."""
    blob = MediaBlob(sha256=JPEG_SHA, media_type="image/jpeg", size=len(JPEG), filename="a.jpg")
    decoded = json.loads(media_json((blob,)))
    assert decoded == [{
        "sha256": JPEG_SHA, "media_type": "image/jpeg",
        "size": len(JPEG), "filename": "a.jpg",
    }]


# --- the filesystem store ---------------------------------------------------


async def test_the_filesystem_store_shards_by_the_first_two_hex_characters(tmp_path):
    store = FilesystemBlobStore(tmp_path)
    key = await store.put(data=JPEG)
    assert (tmp_path / key[:2] / key[2:]).read_bytes() == JPEG


async def test_a_second_put_of_the_same_bytes_leaves_no_temp_file_behind(tmp_path):
    store = FilesystemBlobStore(tmp_path)
    await store.put(data=JPEG)
    await store.put(data=JPEG)
    assert [p.name for p in tmp_path.rglob("*") if p.is_file() and p.name.startswith(".")] == []


async def test_delete_makes_a_blob_gone_and_is_idempotent(tmp_path):
    """The mechanism the operator's retention decision will need — present so
    that making the decision costs a policy, not an implementation."""
    store = FilesystemBlobStore(tmp_path)
    key = await store.put(data=JPEG)
    assert await store.delete(key=key) is True
    assert await store.get(key=key) is None
    assert await store.delete(key=key) is False


# --- the stage --------------------------------------------------------------


async def test_a_message_with_no_attachments_never_touches_the_collaborators():
    """The ordinary case — every message this deployment has ever seen — costs
    one `if`, and must not count a custody outcome it did not have."""
    metrics = Metrics()
    source = FakeMediaSource()
    message = _msg(body="早晨", refs=())
    out = await media_stage.run(
        message=message, source=source, blobs=FakeBlobStore(), metrics=metrics,
    )
    assert out is message
    assert source.fetched == []
    assert metrics.get("nora_media_custody_total") == 0


async def test_custody_puts_the_handle_in_the_body_and_the_hash_in_the_row():
    metrics = Metrics()
    blobs = FakeBlobStore()
    message = _msg(
        body="睇下 [image]",
        refs=(MediaRef(platform_id="att1", media_type="image/jpeg", filename="a.jpg"),),
    )
    out = await media_stage.run(
        message=message, source=FakeMediaSource({"att1": JPEG}), blobs=blobs, metrics=metrics,
    )
    assert out.body == f"睇下 [image {JPEG_SHA[:6]}]"
    assert out.media_blobs == (
        MediaBlob(sha256=JPEG_SHA, media_type="image/jpeg", size=len(JPEG), filename="a.jpg"),
    )
    assert blobs.blobs[JPEG_SHA] == JPEG
    assert metrics.get("nora_media_custody_total", status="stored") == 1
    assert metrics.get("nora_media_attachments_total", label="image") == 1


async def test_the_recorded_size_is_measured_not_the_platforms_claim():
    """The claim is what the fetch may refuse on; what is recorded is what is
    held. A lying `size` must not become a lying row."""
    out = await media_stage.run(
        message=_msg(body="[image]", refs=(
            MediaRef(platform_id="att1", media_type="image/jpeg", size=999_999),
        )),
        source=FakeMediaSource({"att1": JPEG}), blobs=FakeBlobStore(), metrics=Metrics(),
    )
    assert out.media_blobs[0].size == len(JPEG)


async def test_an_unfetchable_attachment_records_the_message_without_a_claim():
    """Degrade honestly: the row still says an image arrived, it just never
    claims Nora has it — and the loss is counted with its reason."""
    metrics = Metrics()
    message = _msg(body="[image]", refs=(
        MediaRef(platform_id="gone", media_type="image/jpeg"),
    ))
    out = await media_stage.run(
        message=message, source=FakeMediaSource({}), blobs=FakeBlobStore(), metrics=metrics,
    )
    assert out.body == "[image]"
    assert out.media_blobs == ()
    assert metrics.get("nora_media_custody_total", status="not_found") == 1
    assert metrics.get("nora_media_custody_total", status="stored") == 0


async def test_a_daemon_that_is_down_costs_the_bytes_and_never_the_message():
    metrics = Metrics()
    out = await media_stage.run(
        message=_msg(body="[voice note]", refs=(
            MediaRef(platform_id="att1", media_type="audio/aac", voice_note=True),
        )),
        source=FakeMediaSource({"att1": JPEG}, unavailable=True),
        blobs=FakeBlobStore(), metrics=metrics,
    )
    assert out.body == "[voice note]"
    assert metrics.get("nora_media_custody_total", status="unreachable") == 1


async def test_a_disk_that_refuses_the_write_is_counted_not_swallowed():
    metrics = Metrics()
    out = await media_stage.run(
        message=_msg(body="[image]", refs=(
            MediaRef(platform_id="att1", media_type="image/jpeg"),
        )),
        source=FakeMediaSource({"att1": JPEG}),
        blobs=FakeBlobStore(fail_on_put=True), metrics=metrics,
    )
    assert out.body == "[image]"
    assert out.media_blobs == ()
    assert metrics.get("nora_media_custody_total", status="store_failed") == 1


async def test_custody_not_being_wired_is_counted_rather_than_passed_over():
    """The state on the day this shipped. A message that carried an attachment
    while custody was switched off is exactly the loss an operator needs to
    see, and it is invisible in every other series."""
    metrics = Metrics()
    out = await media_stage.run(
        message=_msg(body="[image] [video]", refs=(
            MediaRef(platform_id="a", media_type="image/png"),
            MediaRef(platform_id="b", media_type="video/mp4"),
        )),
        source=None, blobs=None, metrics=metrics,
    )
    assert out.body == "[image] [video]"
    assert metrics.get("nora_media_custody_total", status="not_configured") == 2
    assert metrics.get("nora_media_attachments_total") == 2


async def test_one_failure_among_several_does_not_cost_the_others_their_handles():
    metrics = Metrics()
    png = b"\x89PNG stand-in"
    message = _msg(body="[image] [video] [document]", refs=(
        MediaRef(platform_id="ok1", media_type="image/png"),
        MediaRef(platform_id="missing", media_type="video/mp4"),
        MediaRef(platform_id="ok2", media_type="application/pdf"),
    ))
    out = await media_stage.run(
        message=message,
        source=FakeMediaSource({"ok1": png, "ok2": JPEG}),
        blobs=FakeBlobStore(), metrics=metrics,
    )
    assert out.body == (
        f"[image {hashlib.sha256(png).hexdigest()[:6]}] [video] [document {JPEG_SHA[:6]}]"
    )
    assert len(out.media_blobs) == 2
    assert metrics.get("nora_media_custody_total", status="stored") == 2
    assert metrics.get("nora_media_custody_total", status="not_found") == 1


async def test_the_same_bytes_twice_are_stored_once_and_share_a_handle():
    """Dedup, which is one of the three things hashing the CONTENT rather than
    the metadata bought — and it works across two DIFFERENT platform ids and
    two different filenames, which is what forwarding actually looks like."""
    blobs = FakeBlobStore()
    out = await media_stage.run(
        message=_msg(body="[image] [image]", refs=(
            MediaRef(platform_id="first", media_type="image/jpeg", filename="a.jpg"),
            MediaRef(platform_id="second", media_type="image/jpeg", filename="forwarded.jpg"),
        )),
        source=FakeMediaSource({"first": JPEG, "second": JPEG}),
        blobs=blobs, metrics=Metrics(),
    )
    assert out.body == f"[image {JPEG_SHA[:6]}] [image {JPEG_SHA[:6]}]"
    assert len(blobs.blobs) == 1
    # Both rows still record their own filename beside the shared key.
    assert [b.filename for b in out.media_blobs] == ["a.jpg", "forwarded.jpg"]


async def test_the_stage_asks_the_source_for_the_platforms_own_id():
    """Not the filename, not the hash — the id signal-cli's store uses. A stage
    that asked for anything else would fetch nothing forever."""
    source = FakeMediaSource({"att-xyz": JPEG})
    await media_stage.run(
        message=_msg(body="[image]", refs=(
            MediaRef(platform_id="att-xyz", media_type="image/jpeg", filename="a.jpg"),
        )),
        source=source, blobs=FakeBlobStore(), metrics=Metrics(),
    )
    assert source.fetched == ["att-xyz"]

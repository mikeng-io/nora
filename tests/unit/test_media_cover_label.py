"""A shared item's still picture renders as `[cover image …]` and is recorded
as one.

A reel shared into an Instagram group must not render as
`[shared reel by @x <link>] [image <handle>]`: asked "what is in this reel",
the model would fetch the `[image …]`, read the one frame the reel's cover is,
and tell the room it could not see the clip, with `instagram_read` in its
tools and the link in the line. "image" would name a picture that is not the
thing being asked about. These pin the vocabulary half:

* the label a preview ref earns, and only a preview ref;
* the placeholder grammar every consumer shares (`placeholders_in`,
  `enrich_placeholders`, `body_with_handles`, `label_agrees`), so the new
  label is found and rewritten exactly like the old one;
* `MediaBlob.role`, the one field that carries "this is a cover" past the
  parser into `media_json`, where `media_fetch` reads it, and its backward
  compatibility with every row written without it.

A share whose link validated carries no picture at all, so `[cover image …]`
is the fallback for a share with no link, and the lines here are that shape.
"""

from __future__ import annotations

import json

from nora.domain.media import (
    COVER_ROLE,
    LABELS,
    MediaBlob,
    MediaRef,
    blobs_from_media_json,
    body_with_handles,
    enrich_placeholders,
    label_agrees,
    label_for,
    media_json,
    placeholder_suffix,
    placeholders_in,
    render_placeholder,
)

SHA = "c0de01" + "0" * 58
# A share with no link: a line that still carries a cover (so does a story
# share with its link; a post or reel share whose link validated carries no
# picture).
LINE = "[shared story by @synth.example]"


def test_a_preview_image_is_labelled_a_cover_and_nothing_else_is():
    preview = MediaRef(platform_id="x", media_type="image/*", on_demand=True)
    photo = MediaRef(platform_id="x", media_type="image/*")

    assert label_for(preview) == "cover image"
    assert label_for(photo) == "image"
    assert "cover image" in LABELS


def test_a_preview_that_is_not_an_image_keeps_its_plain_label():
    """Only an image has ever arrived as a preview (`fromXMAPreview`), so
    nothing else gains a word nobody measured."""
    video = MediaRef(platform_id="x", media_type="video/*", on_demand=True)
    sticker = MediaRef(platform_id="x", media_type="image/webp", sticker=True, on_demand=True)

    assert label_for(video) == "video"
    assert label_for(sticker) == "sticker"


def test_the_cover_placeholder_is_rendered_found_and_rewritten_like_any_other():
    """The full life of the placeholder: the parser's bare form, custody's
    handle rewrite, the join's reader, and the render-time enrichment."""
    bare = f"what is in this story {LINE} {render_placeholder('cover image')}"
    assert bare.endswith("[cover image]")

    staged = body_with_handles(bare, ("cover image",), (SHA[:6],))
    assert staged == f"what is in this story {LINE} [cover image c0de01]"
    assert placeholder_suffix(("cover image",), (SHA[:6],)) == "[cover image c0de01]"

    assert placeholders_in(staged) == (("cover image", "c0de01"),)
    assert enrich_placeholders(staged, {"c0de01": "a beach at dusk"}) == (
        f"what is in this story {LINE} [cover image c0de01: a beach at dusk]"
    )


def test_the_cover_label_is_not_read_as_a_bare_image_placeholder():
    """Longest-first alternation: `[cover image c0de01]` is ONE placeholder
    labelled `cover image`, never an `image` placeholder with stray text."""
    assert placeholders_in("[cover image c0de01] [image abc123]") == (
        ("cover image", "c0de01"), ("image", "abc123"),
    )


def test_cover_image_refines_an_image_and_nothing_else():
    """The join takes a body's label only when it agrees with the recorded
    type, and `cover image` is a refinement of `image/*` exactly as `sticker`
    is: a typed `[cover image …]` over a voice note must not be believed."""
    assert label_agrees("cover image", "image/jpeg")
    assert not label_agrees("cover image", "audio/aac")
    assert not label_agrees("cover image", "video/mp4")


def test_the_role_round_trips_through_media_json():
    blob = MediaBlob(sha256=SHA, media_type="image/jpeg", size=3, role=COVER_ROLE)

    (back,) = blobs_from_media_json(media_json((blob,)))

    assert back == blob
    assert back.role == "cover"


def test_an_ordinary_blob_writes_exactly_the_four_keys_it_always_did():
    """No `role` key when there is no role, so every ordinary row's text is
    byte-identical to a row written without the field."""
    blob = MediaBlob(sha256=SHA, media_type="image/jpeg", size=3, filename="a.jpg")

    assert json.loads(media_json((blob,)) or "") == [{
        "sha256": SHA, "media_type": "image/jpeg", "size": 3, "filename": "a.jpg",
    }]


def test_a_row_written_before_the_role_existed_reads_as_an_ordinary_attachment():
    legacy = json.dumps([
        {"sha256": SHA, "media_type": "image/jpeg", "size": 3, "filename": None},
    ])

    (blob,) = blobs_from_media_json(legacy)

    assert blob.role == ""
    assert blob == MediaBlob(sha256=SHA, media_type="image/jpeg", size=3)

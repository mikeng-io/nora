"""Strip embedded metadata from image bytes BEFORE they are hashed (DEC-47).

A photo's EXIF is worse PII than the photo: it carries the GPS coordinate of
where it was taken and the id of the device that took it. Two more reasons this
runs before the content hash rather than after:

* **Dedup.** The content key is the sha256 of these bytes. If EXIF rode along,
  the same picture forwarded twice with different GPS would be two different
  keys — two blobs for one photo, and the dedup that hashing the content bought
  would be defeated by the metadata it was supposed to be independent of.
* **It is the plaintext that gets stored.** Encryption at rest protects the
  bytes on disk, but a future retrieval tool decrypts; stripping here means the
  GPS is gone from what Nora holds at all, not merely from what sits on the
  volume.

**Images only.** Pillow can open an image, drop its metadata, and re-encode it
— `Image.save` does not carry EXIF forward unless explicitly handed it, so a
plain re-save strips it. For audio, video and documents there is no cheap,
universal metadata stripper, so those pass through UNCHANGED and their embedded
metadata is NOT removed. That is a stated limitation, not a silent one: the
caller records `size` from what this returns, and for a non-image that is the
original bytes.

**Never raises.** This is on the ingest path, and the port above it forbids an
attachment from costing the message. An image Pillow cannot decode (truncated,
a format it does not support, an `image/*` type that is really something else)
falls back to the original bytes with a warning — still encrypted at rest, and
counted/stored like any other blob. Losing the strip on one malformed image is
not worth losing the record of the message that carried it.

Lives in `nora.ingest` rather than `nora.domain.media` because `nora.domain` is
stdlib-only by contract and Pillow is not stdlib; the taxonomy and serialization
stay in the domain, and this — the one transform that needs an image library —
sits in the stage layer that already depends on third-party code.
"""

from __future__ import annotations

import logging
from io import BytesIO

from PIL import Image

log = logging.getLogger("nora.ingest.exif")


def strip_exif(data: bytes, media_type: str) -> bytes:
    """Return `data` with embedded image metadata removed, for image types only.

    `media_type` is the RESOLVED type — `nora.domain.media.resolve_media_type`
    for an ordinary attachment (the registered top-level type, never sniffed
    here), or `nora.domain.media.sniff_media_type` upstream for a sticker
    (Signal declares none at all). A non-image, or an image Pillow cannot
    read, comes back byte-for-byte unchanged.

    **Never called for a sticker at all** — `nora.ingest.stickers` hashes the
    fetched bytes directly and skips this function, deliberately: DEC-47's
    PII argument does not apply to published, platform-distributed pack art,
    and this function's re-encode has no `save_all`, so it would silently
    flatten an animated sticker to one frame before it was ever hashed. See
    that module's `_read` for both reasons in full.
    """
    if not media_type.startswith("image/"):
        return data
    try:
        with Image.open(BytesIO(data)) as img:
            fmt = img.format
            # Copy the PIXELS into a fresh image with an empty `.info`, so no
            # `exif`/`icc_profile`/comment carried on the original can ride
            # along — `paste` moves the raster, never the metadata. `save` is
            # then handed no exif, so the GPS and device id are simply not
            # written back.
            clean = Image.new(img.mode, img.size)
            clean.paste(img)
            out = BytesIO()
            clean.save(out, format=fmt)
            return out.getvalue()
    except Exception:                                              # noqa: BLE001
        # Blind and narrow on purpose: any decode/encode failure is the same
        # answer — keep the original bytes so the attachment is still held
        # (encrypted), rather than dropping custody over a metadata nicety.
        log.warning(
            "could not strip metadata from a %s attachment; storing it unstripped "
            "(still encrypted at rest) — see nora/ingest/exif.py",
            media_type,
        )
        return data

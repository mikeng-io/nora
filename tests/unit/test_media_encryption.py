"""Encryption at rest + EXIF stripping for media custody (DEC-47).

Every probe here is the observable form of one of these rules:

* **EXIF is stripped from images BEFORE hashing.** A photo's EXIF carries GPS
  and a device id — worse PII than the picture — and content addressing would
  otherwise dedup on EXIF *differences*, so two copies of one photo with
  different GPS would be two blobs. Stripping first is what makes the content
  key the key of the PICTURE.
* **The bytes on disk are ciphertext.** AES-GCM over the stripped plaintext,
  nonce per blob stored alongside. A JPEG's `FF D8` magic must never appear on
  disk; the hash that keys the blob is of the PLAINTEXT so dedup still works.
* **Round trip is lossless.** `get` decrypts to exactly what `put` was handed.
* **Non-images are not stripped** — there is no cheap universal stripper — and
  that is stated, not hidden: a non-image round-trips byte-for-byte.

Fixtures are REAL images built by Pillow here, because a fake `b"\\xff\\xd8..."`
is not something `Image.open` can read and the strip would no-op on it. These
are fixtures throughout.
"""

from __future__ import annotations

import hashlib
from io import BytesIO

import pytest
from PIL import Image

from nora.infra.blobs import FilesystemBlobStore
from nora.infra.blobs.cipher import BlobCipher, InvalidTag

# A deterministic 32-byte AES-256 key for the tests. Never a real key.
TEST_KEY = bytes(range(32))
JPEG_MAGIC = b"\xff\xd8"


def _jpeg_with_gps(*, lat: float, pixel: int = 0) -> bytes:
    """A real, decodable JPEG carrying EXIF PII — a device id and a location
    string, the metadata the strip removes.

    `pixel` varies the image CONTENT; `lat` varies only the EXIF, so two calls
    with the same `pixel` and different `lat` are the same picture wearing
    different metadata (a JPEG string tag, which Pillow serializes cleanly —
    the point here is EXIF presence, not the GPS binary layout).
    """
    img = Image.new("RGB", (4, 4), color=(pixel % 256, 0, 0))
    exif = Image.Exif()
    exif[0x0110] = "SecretPhone X9"                 # Model — the device id
    exif[0x9286] = f"taken at latitude {lat}"       # UserComment — a location
    buf = BytesIO()
    img.save(buf, format="JPEG", exif=exif.tobytes())
    return buf.getvalue()


def _exif_of(data: bytes) -> Image.Exif:
    return Image.open(BytesIO(data)).getexif()


@pytest.fixture
def store(tmp_path) -> FilesystemBlobStore:
    return FilesystemBlobStore(tmp_path / "media", cipher=BlobCipher(TEST_KEY))


# --- encryption at rest -----------------------------------------------------


async def test_the_bytes_on_disk_are_ciphertext_not_the_plaintext(store, tmp_path):
    """Nothing on disk identifies the file as a picture, and no run of the
    plaintext survives. AES-GCM output is indistinguishable from random.

    Asserting `JPEG_MAGIC not in on_disk` would be UNSOUND: the magic is two
    bytes, so it turns up somewhere in ~745 bytes of random output about 1.14% of
    the time. A spurious red at that rate teaches an operator to re-run and move
    on, which is how a real red gets waved through.

    Two coincidental bytes in the middle of ciphertext do not make a picture
    readable; what would is a viewer sniffing the HEADER, or a recognisable run of
    plaintext surviving. Both are asserted below, and both are deterministic — the
    header check exactly, and the 16-byte run at 2^-128."""
    plaintext = _jpeg_with_gps(lat=22)
    key = await store.put(data=plaintext)
    on_disk = (tmp_path / "media" / key[:2] / key[2:]).read_bytes()
    assert on_disk != plaintext
    assert not on_disk.startswith(JPEG_MAGIC)     # no viewer sniffs it as a JPEG
    assert plaintext[:16] not in on_disk          # no plaintext run survives


async def test_get_round_trips_the_plaintext_byte_for_byte(store):
    plaintext = _jpeg_with_gps(lat=22)
    key = await store.put(data=plaintext)
    assert await store.get(key=key) == plaintext


async def test_the_key_is_the_hash_of_the_plaintext_not_the_ciphertext(store):
    """Dedup and verifiability both rest on this: the key is sha256 of what was
    handed to `put`, even though what lands on disk is ciphertext."""
    plaintext = _jpeg_with_gps(lat=22)
    key = await store.put(data=plaintext)
    assert key == hashlib.sha256(plaintext).hexdigest()


async def test_a_wrong_key_cannot_read_a_blob(store, tmp_path):
    """A second store with a different key over the same directory must fail to
    decrypt rather than return garbage — GCM authenticates."""
    plaintext = _jpeg_with_gps(lat=22)
    key = await store.put(data=plaintext)
    other = FilesystemBlobStore(tmp_path / "media", cipher=BlobCipher(bytes(range(1, 33))))
    with pytest.raises(InvalidTag):
        await other.get(key=key)


async def test_each_blob_gets_its_own_nonce(store, tmp_path):
    """Distinct plaintexts must not share a nonce — GCM nonce reuse is fatal.
    The nonce is stored alongside the ciphertext (it is not secret)."""
    k1 = await store.put(data=_jpeg_with_gps(lat=22, pixel=1))
    k2 = await store.put(data=_jpeg_with_gps(lat=22, pixel=2))
    n1 = (tmp_path / "media" / k1[:2] / k1[2:]).read_bytes()[:12]
    n2 = (tmp_path / "media" / k2[:2] / k2[2:]).read_bytes()[:12]
    assert n1 != n2


# --- EXIF stripping ---------------------------------------------------------


async def test_exif_is_absent_from_the_stored_image(store):
    """The GPS and device id the photo arrived with are gone from what Nora
    holds. Decrypt the blob and there is no EXIF left to read."""
    original = _jpeg_with_gps(lat=22)
    assert _exif_of(original)              # the fixture really does carry EXIF
    key = await store.put(data=strip_for_test(original))
    restored = await store.get(key=key)
    assert not dict(_exif_of(restored))


async def test_same_picture_different_exif_dedups_to_one_blob(store):
    """Content addressing on the STRIPPED bytes: the same picture with two
    different GPS coordinates is ONE blob, because the key is the key of the
    picture, not of the metadata."""
    a = strip_for_test(_jpeg_with_gps(lat=22, pixel=7))
    b = strip_for_test(_jpeg_with_gps(lat=48, pixel=7))
    assert a == b                          # stripping erased the only difference
    ka = await store.put(data=a)
    kb = await store.put(data=b)
    assert ka == kb


def test_a_non_image_is_not_stripped_and_survives_byte_for_byte():
    """No cheap universal stripper for audio/video/docs — so they are stored
    as-is, and that is stated rather than hidden."""
    from nora.ingest.exif import strip_exif

    blob = b"ID3\x04\x00 fake mp3 with \x00 embedded tags"
    assert strip_exif(blob, "audio/mpeg") == blob
    assert strip_exif(blob, "application/pdf") == blob


def strip_for_test(data: bytes) -> bytes:
    from nora.ingest.exif import strip_exif

    return strip_exif(data, "image/jpeg")

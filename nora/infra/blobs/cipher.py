"""AES-256-GCM at rest for the blob store (DEC-47).

Media is the most PII-heavy data in the system — faces, kids, homes, and (in a
photo's EXIF) the GPS coordinate of where it was taken. `FilesystemBlobStore`
therefore never writes plaintext: it hands every blob's bytes to a `BlobCipher`,
which seals them under a key the host injects (DEC-8: name the env var, never
inline the secret), and writes the ciphertext.

**Why GCM, and how the nonce is handled.** AES-GCM is authenticated: a blob read
back under the wrong key does not decrypt to garbage, it RAISES — which is what
lets `get` distinguish "this is the wrong key / a corrupt blob" from "these are
the bytes". GCM's one hard rule is that a (key, nonce) pair must never encrypt
two different messages, so every blob gets a fresh 12-byte random nonce, stored
as the first 12 bytes of the file. The nonce is not secret; reuse is what is
fatal, and content addressing makes reuse structurally impossible here — a blob
is written at most once (its key is the hash of its plaintext, and
`FilesystemBlobStore` skips a path that already exists), so the same nonce is
never asked to seal a second, different plaintext under the same key.

**The key is of the PLAINTEXT, not the ciphertext.** Dedup, verifiability and
the rendered `[image a3f2c1]` handle all rest on the content hash, so the store
hashes the plaintext it is given and only THEN encrypts. This module is not
where that happens (`FilesystemBlobStore.put` is); it is only the seal/open
primitive and the one place that turns a configured env-var value into a key.

May import: stdlib and `cryptography`. Not `nora.obs` (layers contract); this
module counts nothing and a misconfigured key is the composition root's problem
to log and degrade, not this primitive's.
"""

from __future__ import annotations

import base64
import binascii
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

__all__ = ["BlobCipher", "InvalidTag"]

# AES-256 key length and GCM's standard nonce length. 32 bytes is a deliberate
# choice over 16 (AES-128): the key is generated once and lives in an env var,
# so the larger key costs nothing and the smaller one would be a choice nobody
# made for no benefit.
KEY_BYTES = 32
NONCE_BYTES = 12


class BlobCipher:
    """Seal/open one blob's bytes under a fixed AES-256-GCM key.

    Constructed with the raw 32-byte key. `from_base64` is the env-facing
    constructor: it decodes the standard-base64 value the operator sets and
    rejects anything that is not exactly 32 bytes, so a truncated or mistyped
    key is a loud `ValueError` at the composition root rather than a silent
    weaker cipher.
    """

    def __init__(self, key: bytes) -> None:
        if len(key) != KEY_BYTES:
            raise ValueError(
                f"blob encryption key must be {KEY_BYTES} bytes, got {len(key)}"
            )
        self._aead = AESGCM(key)

    @classmethod
    def from_base64(cls, value: str) -> BlobCipher:
        """Build from the base64-encoded key an operator puts in the env var.

        Generate one with:

            python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"
        """
        try:
            key = base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("blob encryption key is not valid base64") from exc
        return cls(key)

    def seal(self, plaintext: bytes) -> bytes:
        """`nonce || ciphertext`. A fresh random nonce per call — see module
        docstring on why reuse cannot happen under content addressing."""
        nonce = os.urandom(NONCE_BYTES)
        return nonce + self._aead.encrypt(nonce, plaintext, None)

    def open(self, sealed: bytes) -> bytes:
        """Recover the plaintext. Raises `InvalidTag` on a wrong key or a
        tampered/corrupt blob, and `ValueError` if the file is too short to
        even carry a nonce — both are "this is not readable", never a silent
        wrong answer."""
        if len(sealed) < NONCE_BYTES:
            raise ValueError("sealed blob is too short to contain a nonce")
        nonce, ciphertext = sealed[:NONCE_BYTES], sealed[NONCE_BYTES:]
        return self._aead.decrypt(nonce, ciphertext, None)

"""One hash over the instruction text that produced a row — the Dreamer's
half of what `doctrine_sha256` already is on the tag path.

`nora.doctrine.loader.DoctrineLoader.get` assembles the sections that apply to
a tag, hashes the assembled text once, and every audit row for that turn
carries the digest. Dreamer rows carry the same kind of digest: `room_lexeme`
holds a model-authored `sense`, and the digest of the text that asked for it
lets a wrong gloss be attributed to a revision and a later edit be shown to
have fixed it.

**One function, deliberately.** The caller passes the instruction strings (an
assembled doctrine body plus one objective body); the column is the same
whatever they are, and so is this function.

Framework-free and stdlib-only, at the layers' floor, because both
`nora.language` and `nora.agent` have to name it and neither may import the
other.
"""

from __future__ import annotations

import hashlib

# The digest is 64 lowercase hex characters. The DB CHECK constraints on the
# four Dreamer tables assert exactly this width, so a caller that stamps a
# version string, an empty string, or a truncated digest is refused by
# Postgres rather than stored as unverifiable provenance.
INSTRUCTION_SHA256_HEX_LEN = 64


def instruction_sha256(*parts: str) -> str:
    """SHA-256 over `parts` joined by a newline, hex-encoded.

    The separator is not decoration: without it `("ab", "c")` and `("a", "bc")`
    hash identically, so two genuinely different instruction sets would be
    indistinguishable in the column that exists to distinguish them. `\\n` is
    the same joiner `nora.doctrine.loader._assemble` uses for the tag path's
    own assembled text, so the two provenance chains are computed the same way.

    Order is the CALLER's and is part of the identity — an adaptor must pass
    its parts in the order the runtime assembles them, and must not vary that
    order per call, or one instruction set produces two digests.

    Passing no parts is a programming error, not an empty instruction set: a
    row stamped with the digest of the empty string would claim attributable
    provenance while naming nothing (R-27's `unavailable` masquerading as
    `nothing_found`, one layer down). Raise instead.
    """
    if not parts:
        raise ValueError("instruction_sha256 needs at least one instruction part")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()

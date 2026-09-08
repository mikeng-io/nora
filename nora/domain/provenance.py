"""One hash over the instruction text that produced a row — the Dreamer's
half of what `doctrine_sha256` already is on the tag path.

`nora.doctrine.loader.DoctrineLoader.get` assembles the sections that apply to
a tag, hashes the assembled text once, and every audit row for that turn
carries the digest. Nothing the Dreamer writes had an equivalent: `room_lexeme`
holds a model-authored `sense` and no record of the text that asked for it, so
a wrong gloss (`big walk` glossed as a nighttime stroll, for a co-op puzzle
video game) cannot be attributed to a revision and a later edit cannot be shown
to have fixed it. See
`docs/superpowers/specs/2026-08-20-dreamer-prompt-substrate-design.md` §4.3.

**One function, deliberately.** It was written while the instructions were
inline Python constants, to land the provenance column before the instruction
substrate existed; under the substrate they are an assembled doctrine body
plus one objective body, which is what every caller passes today. The COLUMN
is the same either way and so is this function — what changes is only which
strings the caller passes. A row
written before the substrate is attributable to the constant that produced it;
a row written after is attributable to the assembly. Neither is orphaned.

Framework-free and stdlib-only, at the layers' floor, because both
`nora.language` (today's adaptors) and `nora.agent` (tomorrow's Dreamer
runtime) have to name it and neither may import the other.
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

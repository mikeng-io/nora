"""`nora.domain.provenance.instruction_sha256` — the one hash the Dreamer's
four tables stamp.

Every assertion here is about a way the digest could FAIL to distinguish two
instruction sets, because a stamp that collides is worse than no stamp: it
looks like provenance while attributing two revisions to one.
"""

from __future__ import annotations

import hashlib

import pytest

from nora.domain.provenance import INSTRUCTION_SHA256_HEX_LEN, instruction_sha256


def test_a_digest_is_64_lowercase_hex_characters():
    digest = instruction_sha256("some instruction text")
    assert len(digest) == INSTRUCTION_SHA256_HEX_LEN
    assert digest == digest.lower()
    int(digest, 16)  # raises if it is not hex


def test_one_part_is_the_plain_sha256_of_that_text():
    """The single-part case is what every adaptor writes (an inline
    constant), so it must be reproducible by hand from a row's stamp alone —
    `sha256sum` on the constant, nothing bespoke."""
    text = "You are reading a batch of messages…"
    assert instruction_sha256(text) == hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_changing_one_character_changes_the_digest():
    assert instruction_sha256("do NOT report ordinary words") != instruction_sha256(
        "do NOT report ordinary word"
    )


def test_the_same_text_split_differently_does_not_collide():
    """Without a separator, `("ab", "c")` and `("a", "bc")` hash identically —
    so a doctrine edit that moved one line into the objective would be
    invisible in the column that exists to see it. This is the assertion that
    fails if the joiner is ever dropped."""
    assert instruction_sha256("ab", "c") != instruction_sha256("a", "bc")


def test_order_is_part_of_the_identity():
    assert instruction_sha256("doctrine", "objective") != instruction_sha256(
        "objective", "doctrine"
    )


def test_no_parts_raises_rather_than_hashing_nothing():
    """A row stamped with the digest of the empty string would claim
    attributable provenance while naming no instruction at all — `unavailable`
    dressed as `nothing_found` (R-27), one layer down."""
    with pytest.raises(ValueError):
        instruction_sha256()

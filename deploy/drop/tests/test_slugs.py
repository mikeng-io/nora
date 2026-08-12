from __future__ import annotations

from slugs import ALPHABET, SLUG_LEN, random_slug


def test_random_slug_has_the_right_length_and_alphabet():
    slug = random_slug()
    assert len(slug) == SLUG_LEN
    assert all(c in ALPHABET for c in slug)


def test_the_alphabet_excludes_ambiguous_characters():
    """0/O/I/l -- so a slug survives being read aloud (shortener.js's own
    comment, replicated verbatim)."""
    for ambiguous in "0OIl":
        assert ambiguous not in ALPHABET


def test_two_calls_are_very_unlikely_to_collide():
    slugs = {random_slug() for _ in range(200)}
    assert len(slugs) == 200

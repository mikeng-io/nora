"""The portal's two locales must make the SAME claims, not just carry the
same keys.

**The defect this exists for, 2026-09-13.** The note tier's landing copy said
a background curator «merges, retires, and promotes» notes. The curator's
merge half was deleted 2026-09-08 (`nora/language/note_curator.py`'s module
docstring; `CURATOR_ACTIONS` no longer names it), so the sentence was false.
It was corrected in `app/messages/en.json` and left standing in
`app/messages/zh-Hant.json` — and `app/src/i18n/routing.ts` sets
`defaultLocale: "zh-Hant"`, so the FALSE copy was the one the site served by
default and the corrected English was the fallback nobody sees first. A
one-locale correction reads as done and ships the bug.

Nothing caught it. No frontend test referenced `landing.tier.notes.
description`; the only locale-parity test (`app/src/components/
presence-profile.test.ts`) formats `map.profile.*` ICU keys and asserts
nothing about the other ~380; and `make gate` is `lint typecheck imports
test` — Python only, so a `vitest` file added under `app/` would run in no
gate and no CI at all (this repo has no CI workflows). That is why this guard
is a PYTHON test: it is the only place a check of these files actually runs.

**What it measures, and what it does not.** It cannot judge whether a
sentence is TRUE — no test can. It measures the property that was actually
violated: the two locales disagreed. The measurement is exact rather than a
proxy because of how this copy is written — the Cantonese-inflected
Traditional Chinese embeds the English technical vocabulary VERBATIM
(`closing condition`, `curator`, `retire`, `promote`, `merge`), so every
backend term the copy names is a literal ASCII token in both files. So:

  every ASCII word of four or more letters in a `zh-Hant` value must also
  appear in the English text of the same BLOCK (the parent object — title,
  domain and description together, since the locales split a term across
  those three differently).

Prefix-matching either way absorbs ordinary inflection (`retire`/`retires`,
`note`/`notes`, `review`/`reviews`). What it does not absorb is a term one
locale names and the other does not — which is exactly `merge`.

Fail-closed: the pinned pairs below are the only exemptions, they are pinned
per (key, word) rather than per word, and the list only shrinks. A word that
is not pinned and does not match is a FAILURE, never a skip.

The residue this does not see: a claim rewritten in both locales and wrong in
both, and a divergence that uses no ASCII term. Neither is the defect that
shipped, and pretending otherwise would be the decoration this repo's
`measure` rule warns about. The first of those is not hypothetical —
`landing.tier.knowledge` and `landing.tier.language` describe two tiers
deleted on 2026-09-07 (#115), identically in both files, so this guard passes
them. Issue #240 carries that.

A fourth hole, and not a hypothetical one either: the parametrization below
is one-directional (`loc != "en"`), so it only asks whether a non-English
locale names a term the English copy does not. The mirror of the shipped
defect — a term corrected in `zh-Hant` and left standing in `en` — would pass
silently. Making the check symmetric was tried and measured impractical as a
literal mirror: comparing English's block text against `zh-Hant`'s word set
the same way flags ~800 words, because the English copy is ordinary prose
("close", "chat", "send", ...) rather than a small set of embedded loanwords
— the asymmetry this file's argument for exactness (¶3 above) rests on runs
only one way. Closing this hole for real needs English measured against a
known backend-vocabulary list, not against whatever four-letter words
`zh-Hant` happens to use; that is a different, larger guard and is not this
one. Tracked as a follow-up rather than fixed here.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
MESSAGES = ROOT / "app" / "messages"

#: The locale the site serves when nothing asks for another, read back from
#: the router rather than assumed — the whole reason a zh-Hant-only falsehood
#: is worse than an en-only one.
_ROUTING = ROOT / "app" / "src" / "i18n" / "routing.ts"

_ASCII_WORD = re.compile(r"[a-z]{4,}")

#: (key, word) pairs where the two locales legitimately reach for different
#: words — the Cantonese copy takes an English loanword the English copy does
#: not use, or uses an irregular form. Each is a STYLE difference, not a claim
#: difference; each is pinned individually so a new one has to be argued for.
#: This list only shrinks.
_PINNED: frozenset[tuple[str, str]] = frozenset({
    # «Miles 自己 build» vs "Built by Miles" — an irregular past tense, so the
    # prefix rule cannot see them as the same word.
    ("tools.dek", "build"),
})


def _flatten(node: Any, prefix: str = "") -> dict[str, str]:
    flat: dict[str, str] = {}
    for key, value in node.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            flat.update(_flatten(value, path))
        else:
            flat[path] = str(value)
    return flat


def _load(locale: str) -> dict[str, str]:
    return _flatten(json.loads((MESSAGES / f"{locale}.json").read_text("utf-8")))


def _locales() -> list[str]:
    found = sorted(p.stem for p in MESSAGES.glob("*.json"))
    assert found, f"no locale files under {MESSAGES} — this guard measures nothing"
    return found


def test_pinned_list_only_shrinks() -> None:
    """`_PINNED`'s docstring says the list only shrinks — this is the guard
    that makes that true instead of a comment.

    Without this, `_PINNED` is a back door: any (key, word) pair can be added
    to make a real drift disappear, and nothing anywhere reddens. That is not
    hypothetical — appending `("landing.tier.notes.description", "merge")`
    restores the exact defect this file exists to catch (`merge` standing in
    the zh-Hant copy after the English copy dropped it) and the parametrized
    test still passes, because the pin swallows the one word that would have
    caught it.

    6 was the count measured on 2026-09-13, the day this guard was added; 1
    since 2026-09-26, when the portal's landing copy (five of the six pins)
    moved to site/. A shrink lowers this number along with the list; growing
    the list without lowering this number is the back door, and this is what
    turns that red.
    """
    assert len(_PINNED) <= 1, (
        f"_PINNED grew to {len(_PINNED)} entries without this ceiling "
        "moving with it. Its docstring says the list only shrinks — if a "
        "new pin is genuinely a style difference, argue it in the pin's "
        "comment AND raise this number in the same change; if it silences "
        "a real claim difference, fix the copy instead."
    )


def test_the_default_locale_is_the_one_this_guard_assumes_is_at_risk() -> None:
    """A positive control for the premise, not decoration.

    The reason a zh-Hant-only falsehood outranks an en-only one is that
    zh-Hant is what the site SERVES by default. If that flips, this file's
    reasoning has to be re-read rather than silently kept.
    """
    source = _ROUTING.read_text("utf-8")
    assert 'defaultLocale: "zh-Hant"' in source, (
        "app/src/i18n/routing.ts no longer declares zh-Hant as the default "
        "locale. This guard's docstring argues from that fact; re-read it "
        "before changing this assertion."
    )


def test_every_locale_carries_exactly_the_same_keys() -> None:
    """The cheap half: a key added to one locale and not the other renders as
    the raw key path on the served page."""
    locales = _locales()
    reference, *rest = locales
    expected = set(_load(reference))
    for locale in rest:
        actual = set(_load(locale))
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        assert not missing and not extra, (
            f"{locale}.json is out of step with {reference}.json — "
            f"missing {missing}, extra {extra}"
        )


@pytest.mark.parametrize("locale", [loc for loc in _locales() if loc != "en"])
def test_no_locale_names_a_term_the_english_copy_does_not(locale: str) -> None:
    """The half that would have caught it.

    `merge` survives as a literal ASCII token in the Cantonese copy long
    after the English copy drops it, because this copy embeds the English
    technical vocabulary verbatim.
    """
    english = _load("en")
    other = _load(locale)

    blocks: dict[str, list[str]] = {}
    for key in other:
        blocks.setdefault(key.rsplit(".", 1)[0], []).append(key)

    drifted: list[str] = []
    for _block, keys in sorted(blocks.items()):
        english_words = set(
            _ASCII_WORD.findall(" ".join(english[k] for k in keys).lower())
        )
        for key in sorted(keys):
            for word in _ASCII_WORD.findall(other[key].lower()):
                if (key, word) in _PINNED:
                    continue
                if any(
                    e.startswith(word) or word.startswith(e) for e in english_words
                ):
                    continue
                drifted.append(f"{key}: {locale} names {word!r}, en does not")

    assert not drifted, (
        f"{locale}.json names backend vocabulary the English copy of the same "
        "block does not — a claim corrected in one locale and left standing in "
        "the other. Correct the copy, or pin the pair in _PINNED with the "
        "reason it is a style difference rather than a claim difference:\n  "
        + "\n  ".join(drifted)
    )

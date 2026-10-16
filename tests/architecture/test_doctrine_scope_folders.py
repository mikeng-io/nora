"""`nora/doctrine/` is split into scope folders, and this is the gate that keeps
the split real rather than cosmetic.

**Why a folder, not just a convention.** `applies_to` already declares which
occasions a file reaches (`tests/test_doctrine_content.py`'s `EXPECTED`
table); a folder adds nothing the loader reads (`DoctrineStore._scan` scans
`**/*.md` and `order` is what places a section in the assembly — see its
docstring). What the folder buys is a SECOND, independent check on the same
claim: a file's directory says what kind of surface it is for, and this test
refuses a file whose declared audiences disagree with the folder it shipped
in. Two sources of truth that must agree are harder to drift than one, the
same argument `kind` makes for the section tag (`loader.py`'s module
docstring).

**The three folders, and what each promises:**

- `core/` — every surface she speaks on. A file here reaches ALL FOUR of her
  room audiences (`tag`, `heartbeat`, `participation`, `proactive`) AND the
  public `summons` — not a subset, because "core" means occasion-neutral, and a
  core file that narrowed its own audience would be a `rooms/` file mis-filed.
- `rooms/` — group chats. A file here reaches SOME of those same four
  audiences (`participation-conduct.md` is `participation`-only) — never an
  audience `core/` does not also cover, because a `rooms/` file is still
  something SHE reads, never a model measuring her from outside.
- `instruments/` — prompts for OTHER models, not hers: the Relevance Gate,
  the reaction picker, the Dreamer. A file here reaches only
  `relevance_gate`, `reaction` or `dreamer` — never one of the four SHE
  reads, because an instrument file leaking into her own prompt is exactly
  the failure `participation.md`'s own frontmatter doctrine warns about
  (`loader.py` AUDIENCES' docstring: "a name nothing reads removes this file
  from every prompt silently" cuts both ways — the wrong name ADDS it
  somewhere it must never reach, too).

- `public/` — strangers (the public summons). A file here reaches EXACTLY
  `summons` and nothing else: a public file in a group turn would tell her the
  room is strangers, and a room file in a summons would tell strangers about the
  room. `core/` is the only folder that reaches both, which is why `core/`
  declares `summons` too.

**This dict is the one place a folder gets added.**
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nora.doctrine.loader import parse_section

ROOT = Path(__file__).resolve().parents[2]
DOCTRINE_DIR = ROOT / "nora" / "doctrine"

# Every audience a turn of HERS can ask for (`nora.doctrine.loader.AUDIENCES`
# minus the three instrument-only names) — the loader's own closed vocabulary
# is the source; this tuple is asserted against it below rather than
# hand-duplicated silently.
_HER_AUDIENCES = frozenset({"tag", "heartbeat", "participation", "proactive"})
_INSTRUMENT_AUDIENCES = frozenset({"relevance_gate", "reaction", "dreamer", "speech_format"})
# The public surface: a turn of hers, but one strangers read. Its own set, so
# `rooms/` can never draw from it.
_PUBLIC_AUDIENCES = frozenset({"summons"})

# folder -> ("exact" | "subset", the allowed/required audience set).
# "exact": every file in the folder must declare EXACTLY this set.
# "subset": every file in the folder must declare a non-empty SUBSET of it.
# Add a new scope folder's row HERE and nowhere else (module docstring).
_SCOPE_FOLDERS: dict[str, tuple[str, frozenset[str]]] = {
    "core": ("exact", _HER_AUDIENCES | _PUBLIC_AUDIENCES),
    "rooms": ("subset", _HER_AUDIENCES),
    "instruments": ("subset", _INSTRUMENT_AUDIENCES),
    "public": ("exact", _PUBLIC_AUDIENCES),
}


def _all_doctrine_files() -> list[Path]:
    return sorted(DOCTRINE_DIR.glob("**/*.md"))


def test_the_scope_folders_cover_the_audiences_the_loader_knows():
    """The table above is a hand-written partition of `AUDIENCES`; if the
    loader's own vocabulary ever grows, this fails loudly instead of the new
    audience silently falling into neither set."""
    from nora.doctrine.loader import AUDIENCES

    assert set(AUDIENCES) == _HER_AUDIENCES | _INSTRUMENT_AUDIENCES | _PUBLIC_AUDIENCES
    assert _HER_AUDIENCES.isdisjoint(_INSTRUMENT_AUDIENCES)
    assert _PUBLIC_AUDIENCES.isdisjoint(_HER_AUDIENCES | _INSTRUMENT_AUDIENCES)


def test_no_doctrine_file_sits_directly_at_the_top_level():
    """`nora/doctrine/*.md` (one level, not `**/*.md`) must be empty — every
    file lives in a scope folder. A file left at the top would still load
    (the scan is recursive) and would still pass every OTHER gate in this
    module, so this is the only check that would catch it."""
    top_level = sorted(p.name for p in DOCTRINE_DIR.glob("*.md"))
    assert top_level == [], (
        f"{top_level} sit directly under {DOCTRINE_DIR}, not in a scope folder "
        "(core/, rooms/, instruments/, public/)"
    )


def test_every_doctrine_file_lives_in_exactly_one_known_scope_folder():
    unknown = [
        p.relative_to(DOCTRINE_DIR)
        for p in _all_doctrine_files()
        if p.parent.name not in _SCOPE_FOLDERS or p.parent.parent != DOCTRINE_DIR
    ]
    assert unknown == [], (
        f"{unknown} are not directly inside one of {sorted(_SCOPE_FOLDERS)} — "
        "either a new scope folder needs a row in _SCOPE_FOLDERS, or the file "
        "is nested two levels deep instead of one"
    )


@pytest.mark.parametrize(
    "path", _all_doctrine_files(), ids=lambda p: str(p.relative_to(DOCTRINE_DIR))
)
def test_each_files_audiences_match_its_folders_contract(path: Path):
    folder = path.parent.name
    mode, allowed = _SCOPE_FOLDERS[folder]
    section = parse_section(path)
    declared = set(section.applies_to)

    assert declared, f"{path.relative_to(DOCTRINE_DIR)} declares no applies_to"

    if mode == "exact":
        assert declared == allowed, (
            f"{path.relative_to(DOCTRINE_DIR)} is in {folder}/ but applies_to="
            f"{sorted(declared)} is not exactly {sorted(allowed)}"
        )
    else:
        assert declared <= allowed, (
            f"{path.relative_to(DOCTRINE_DIR)} declares {sorted(declared - allowed)}, "
            f"outside {folder}/'s allowed audiences {sorted(allowed)}"
        )


def test_rooms_never_declares_an_instrument_audience_and_vice_versa():
    """The two `subset` folders (`rooms/`, `instruments/`) draw from disjoint
    pools by construction (the audience-coverage test above); this restates it
    as a direct, file-level check so a future third `subset` folder can't
    quietly share an audience between what SHE reads and what measures her."""
    for path in _all_doctrine_files():
        folder = path.parent.name
        if folder not in _SCOPE_FOLDERS:
            continue
        declared = set(parse_section(path).applies_to)
        if folder == "rooms":
            assert declared.isdisjoint(_INSTRUMENT_AUDIENCES | _PUBLIC_AUDIENCES), path
        elif folder == "instruments":
            assert declared.isdisjoint(_HER_AUDIENCES | _PUBLIC_AUDIENCES), path
        elif folder == "public":
            assert declared.isdisjoint(_HER_AUDIENCES | _INSTRUMENT_AUDIENCES), path

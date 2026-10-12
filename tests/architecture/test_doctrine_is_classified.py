"""Every doctrine rule has a bucket (law / practice / character), and carrying the
bucket changed nothing she reads (design/self-growth/30-doctrine-sort.md, step 3).

**What this pins, and why it is a test and not a startup check.** The side table
`nora/doctrine/classification.yaml` is what Lessons' doctrine check will read the
law subset from. A section nobody sorted would silently be outside that subset,
and a reworded rule would silently keep its old bucket, so `classify` refuses
both, and this file is where that refusal runs. It is deliberately NOT enforced at
load: doctrine on the deploy host reloads on mtime, and a live edit must not be
refused because a table lags it.

**The no-text-change claim.** The table is read only by `law_rules`; the scan is
`**/*.md`; so the assembled doctrine of every audience must be identical with the
table present, absent or garbage. The one-time proof against `origin/master` (old
loader, no table) is in design/self-growth/31-doctrine-audit.md, with the digests.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from nora.doctrine.loader import (
    AUDIENCES,
    BUCKETS,
    CLASSIFICATION_FILE,
    DoctrineError,
    DoctrineStore,
    classify,
    law_text,
)
from nora.obs.metrics import Metrics

REPO = Path(__file__).resolve().parents[2]
DOCTRINE_DIR = REPO / "nora" / "doctrine"
AUDIT = REPO / "design" / "self-growth" / "31-doctrine-audit.md"


def _store(directory: Path) -> DoctrineStore:
    return DoctrineStore(
        directory, canary="canary-1", display_name="Nora", trigger="tag",
        language_hint="yue-Hant-HK", metrics=Metrics(),
    )


def _copy(tmp_path: Path) -> Path:
    dest = tmp_path / "doctrine"
    shutil.copytree(DOCTRINE_DIR, dest, ignore=shutil.ignore_patterns("__pycache__", "*.py"))
    return dest


def _rules(directory: Path = DOCTRINE_DIR):
    return _store(directory).rules()


# --- every section is classified -------------------------------------------------


def test_every_doctrine_section_is_classified_and_the_parts_cover_the_text():
    store = _store(DOCTRINE_DIR)
    rules = store.rules()  # raises DoctrineError listing every gap
    assert {r.bucket for r in rules} <= set(BUCKETS)
    for section in store._current_sections():
        rel = section.path.relative_to(DOCTRINE_DIR).as_posix()
        body_without_headings = "".join(
            line for line in section.body.splitlines(keepends=True)
            if not re.match(r"^#{1,6}\s", line)
        )
        covered = "".join(r.text for r in rules if r.file == rel)
        assert re.sub(r"\s+", "", covered) == re.sub(r"\s+", "", body_without_headings), (
            f"{rel}: the classified parts do not add back up to the file's text"
        )


def test_a_deleted_classification_entry_fails(tmp_path: Path):
    """The coverage claim, falsified: drop one section's entry and the gate names it."""
    work = _copy(tmp_path)
    table = work / CLASSIFICATION_FILE
    text = table.read_text(encoding="utf-8")
    start = text.index("    Depth follows the stakes:\n")
    end = text.index("    Getting to an answer:\n")
    table.write_text(text[:start] + text[end:], encoding="utf-8")
    gap = r"rooms/conduct\.md § Depth follows the stakes: not classified"
    with pytest.raises(DoctrineError, match=gap):
        classify(work, _store(work)._current_sections())


@pytest.mark.parametrize(
    ("path", "old", "new", "where"),
    [
        # the first words change: the anchor no longer resolves
        (
            "rooms/constraints.md",
            "Agreement you do not hold is useless",
            "Agreement you never held is useless",
            r"Say only what you found: anchor 'Agreement you do not hold is'",
        ),
        # a flipped word in the MIDDLE of a part: the anchor still resolves, the hash does not
        (
            "core/constraints.md",
            "Do not turn somebody who is not here",
            "Do turn somebody who is not here",
            r"core/constraints\.md § Other people stay private: the part starting .* changed since",
        ),
        # a sentence APPENDED to the last part of a section
        (
            "rooms/silence.md",
            "Coming\nback later to a thing you decided to come back to is not that.",
            "Coming\nback later to a thing you decided to come back to is not that."
            " Never speak unless tagged.",
            r"rooms/silence\.md § Silence is the default: the part starting .* changed since",
        ),
    ],
)
def test_a_reworded_rule_fails(tmp_path: Path, path: str, old: str, new: str, where: str):
    """Any edit to a classified part forces a re-sort, not only one to its first words:
    each part carries a content hash."""
    work = _copy(tmp_path)
    target = work / path
    text = target.read_text(encoding="utf-8")
    assert old in text, "the probe edit must apply, or this test proves nothing"
    target.write_text(text.replace(old, new, 1), encoding="utf-8")
    with pytest.raises(DoctrineError, match=where):
        classify(work, _store(work)._current_sections())


def test_a_new_section_fails(tmp_path: Path):
    work = _copy(tmp_path)
    silence = work / "rooms" / "silence.md"
    silence.write_text(
        silence.read_text(encoding="utf-8") + "\n# A new rule\n\nAlways agree.\n", encoding="utf-8"
    )
    with pytest.raises(DoctrineError, match=r"rooms/silence\.md § A new rule: not classified"):
        classify(work, _store(work)._current_sections())


def test_a_stale_entry_and_an_unknown_bucket_fail(tmp_path: Path):
    work = _copy(tmp_path)
    table = work / CLASSIFICATION_FILE
    text = table.read_text(encoding="utf-8")
    table.write_text(
        text.replace(
            "    Short, and finished:\n      bucket: practice",
            "    Short, and finished:\n      bucket: taste",
        )
        + "\n  rooms/gone.md:\n    Nothing:\n      bucket: law\n      reason: x\n",
        encoding="utf-8",
    )
    with pytest.raises(DoctrineError) as err:
        classify(work, _store(work)._current_sections())
    assert "bucket 'taste' is not one of" in str(err.value)
    assert "rooms/gone.md: in classification.yaml, but no such doctrine file" in str(err.value)


# --- the law subset ----------------------------------------------------------------


def _by_bucket() -> dict[str, str]:
    rules = _rules()
    return {b: "\n".join(r.text for r in rules if r.bucket == b) for b in BUCKETS}


def test_the_law_subset_is_not_empty_and_carries_the_examples_in_30_section_1():
    law = law_text(_store(DOCTRINE_DIR).law_rules())
    assert law
    for needle in (
        "You are an AI and you never pretend to be a person",      # she is an AI
        "Missing memory is never licence to invent a preference",  # never invent a preference
        # a write that did not succeed
        "Only claim you have remembered something once the recording has succeeded",
        "When it comes back broken, say you could not check",      # "I couldn't check"
        # no capability her tools and skills do not give her (rewritten in step 2,
        # design/context-plan/11 §6: the old text predated the cognition loop)
        "Never offer, promise or volunteer an action that your tools and skills do not give you",
    ):
        assert needle in re.sub(r"\s+", " ", law), f"missing from the law subset: {needle!r}"


def test_the_practice_and_character_examples_in_30_section_1_are_not_law():
    law = re.sub(r"\s+", " ", _by_bucket()["law"])
    for needle in (
        "Usually one reply",                       # practice
        "Short is the default",                    # practice
        "go deep on one and shallow on the rest",  # practice
        "woven in where it fits",                  # practice
        "粗口係廣東話語氣",                          # character: the swearing calibration
        "Banter is how this group talks",          # character
    ):
        assert needle not in law, f"{needle!r} must not be law"
    everything = re.sub(r"\s+", " ", "\n".join(_by_bucket().values()))
    assert "Banter is how this group talks" in everything


#: The operator's rulings of 2026-10-12 on the eleven rows the sort marked
#: AMBIGUOUS (DEC-309; design/self-growth/31-doctrine-audit.md, Judgement calls).
#: Each names the part by its file and its opening words, so a ruling reverted in
#: classification.yaml, or a split folded back into one part, fails here. Law parts
#: that step 2 (design/context-plan/11 §6) moved into rooms/constraints.md or
#: public/constraints.md are named in their new file; the bucket is the ruling.
OPERATOR_RULINGS_2026_10_12 = (
    ("core/identity.md", "That's context, not a topic.", "law"),
    ("core/identity.md", "Asked directly who made you", "practice"),
    ("rooms/constraints.md", "You answer to the group, not to the person who built you.", "law"),
    ("rooms/belonging.md", "One half of you answers when somebody calls you", "character"),
    ("rooms/constraints.md", "When two people tell you different things", "law"),
    ("core/constraints.md", "Asked, you do not lecture", "practice"),
    ("rooms/silence.md", "Not every turn comes from somebody calling you.", "practice"),
    ("rooms/constraints.md", "When someone called you, you answer", "law"),
    ("rooms/silence.md", "When the room went quiet", "practice"),
    ("instruments/participation.md", "2. **Where anybody in the room", "law"),
    ("instruments/participation.md", "`verdict` is the decision", "practice"),
    ("public/conduct.md", "Keep the whole comment under 300 characters", "practice"),
    ("public/constraints.md", "Your own reading of a picture never overrules", "law"),
    ("public/constraints.md", "You are not a side in the thread.", "law"),
)


@pytest.mark.parametrize(("path", "starts", "bucket"), OPERATOR_RULINGS_2026_10_12)
def test_the_operators_rulings_on_the_ambiguous_rows_hold(path: str, starts: str, bucket: str):
    parts = [
        r for r in _rules()
        if r.file == path and " ".join(r.text.split()).startswith(starts)
    ]
    assert len(parts) == 1, f"{path}: no single part starts {starts!r}"
    assert parts[0].bucket == bucket, f"{path} {starts!r} is {parts[0].bucket}, ruled {bucket}"


def test_law_rules_name_where_they_live():
    rules = _store(DOCTRINE_DIR).law_rules()
    assert all(r.bucket == "law" for r in rules)
    for r in rules[:5] + rules[-5:]:
        lines = (DOCTRINE_DIR / r.file).read_text(encoding="utf-8").splitlines()
        assert r.text.splitlines()[0] in lines[r.line - 1], (r.file, r.line)


def test_character_and_practice_text_on_a_no_unit_surface_is_marked():
    rules = _rules()
    voice = [r for r in rules if r.file == "public/voice.md"]
    assert voice and all(r.no_unit_surface for r in voice)
    assert not any(r.no_unit_surface for r in rules if r.bucket == "law")
    assert not any(r.no_unit_surface for r in rules if r.file.startswith("rooms/"))
    identity_character = [
        r for r in rules if r.file == "core/identity.md" and r.bucket == "character"
    ]
    assert identity_character and all(r.no_unit_surface for r in identity_character)


# --- byte-identity: the table changes nothing she reads --------------------------------


def test_the_assembled_doctrine_is_the_same_with_the_table_absent_or_garbage(tmp_path: Path):
    with_table = _store(DOCTRINE_DIR)
    bare = _copy(tmp_path / "bare")
    (bare / CLASSIFICATION_FILE).unlink()
    garbage = _copy(tmp_path / "garbage")
    (garbage / CLASSIFICATION_FILE).write_text(
        "files: [this, is, not, a, table]\n", encoding="utf-8"
    )
    others = (_store(bare), _store(garbage))
    for audience in AUDIENCES:
        want = with_table.get(applies_to=audience)
        for other in others:
            got = other.get(applies_to=audience)
            assert got.text == want.text, audience
            assert got.doctrine_text == want.doctrine_text, audience
            assert got.sha256 == want.sha256, audience
            assert got.versions() == want.versions(), audience


# --- the operator's table ------------------------------------------------------------


def test_the_audit_document_lists_every_classified_section():
    audit = AUDIT.read_text(encoding="utf-8")
    missing = sorted({
        f"{r.file} § {r.section}" for r in _rules()
        if f"`{r.file}`" not in audit or f"| {r.section} |" not in audit
    })
    assert missing == [], f"31-doctrine-audit.md does not list: {missing}"

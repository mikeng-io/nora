"""Every transcript builder joins the media, not just the tag assembler.

**This file exists because the first cut of issue #116 shipped without it.**
`MediaJoin` was a private method on `TranscriptInjection`, so the tag path saw
what a photo showed and the other SEVEN builders — the fire path, the tag-path
debrief, the standing event fold, the Dreamer's batch, the note curator, lazy
adoption and `load_transcript` — rendered bare handles. On master the sticker
description and the voice-note transcript were baked into `messages.body`,
which meant every one of them had it; the change was a regression on both flag
settings, and it left the note tier blind to exactly the content the issue was
opened to capture.

Every test above the join was written through ONE consumer, so nothing went
red. That is the defect this file is the brake on.

**A STATIC read of the source, deliberately.** The alternative is eight
integration tests, one per builder, each needing that builder's whole
composition — and the thing being asserted is not what one builder does with a
particular row, it is that no builder was FORGOTTEN. A grep-shaped question
gets a grep-shaped gate: the builder set is DERIVED from the source, and every
module reading the record store either joins or is exempted with a reason.

`tests/unit/test_media_join.py` is the other half — what the join DOES.
"""

from __future__ import annotations

import ast
import pathlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

#: The record-store reads that MAKE a module a transcript builder. Any module
#: calling one of these on the audit store is handing rows to something, and
#: since issue #116 those rows carry `[image a3f2c1]` where master carried the
#: description — so it either joins or it renders less than master did.
_TRANSCRIPT_READS = ("recent", "since", "since_including_nora", "unattributed")

#: Modules that call one of the four above and are NOT transcript builders,
#: each with the reason. **This is the allowlist, and the direction matters.**
#: The first version of this gate listed the BUILDERS instead, with no
#: discovery step — so a rename was caught and a builder that was never listed
#: was not, and «never listed» is the only failure mode that has actually
#: happened. Twice: FIVE builders missed when the join shipped, then
#: `load_transcript` missed by the gate written to catch that.
#:
#: Inverted, a new module reading the record store appears in the discovered
#: set and has to be classified — joined, or exempted here with a sentence.
_NOT_A_BUILDER = {
    # The OPERATOR's surface, not a model's: `nora persons pending` lists rows
    # whose sender maps to nobody so a human can add them to identity.toml. A
    # human reading `[image a3f2c1]` in a CLI table is not being told a photo
    # was never looked at; a model reading it in a prompt is.
    "nora/app/cli.py": "renders unattributed rows for an operator on the CLI",
    # The proactive adaptor's QUIET check (issue #136): `recent(limit=1)` per
    # room, and the only field read is `ts` — is the room still talking. No
    # body reaches a prompt from this read; the pass renders notes, Standing
    # and its own log, never transcript rows (39 §1 keeps `load_transcript`
    # out of it on purpose).
    "nora/language/proactive.py": "reads one row's ts per room for the quiet check",
    # The eval seam's read-back (`design/evaluation/01-boundaries.md`): `heard`
    # reads NORA'S OWN rows after a turn, to report what the room heard, and
    # `last_row_id` reads one row's id as a watermark. Neither body reaches a
    # prompt — they are the OUTPUT side. What this test guards is the input
    # side, and the input side of a case is `know()`, which WRITES rows and
    # reads none.
    "nora/subject/knowledge.py": "reads Nora's own rows back as the outcome, never into a prompt",
}


def _discovered_readers(root: pathlib.Path | None = None) -> dict[str, set[str]]:
    """Every module under `nora/` that calls one of `_TRANSCRIPT_READS`.

    A source scan, because the question is «was anything FORGOTTEN» and a
    hand-written list cannot answer it about itself.
    """
    base = root if root is not None else ROOT
    found: dict[str, set[str]] = {}
    for path in sorted((base / "nora").rglob("*.py")):
        rel = path.relative_to(base).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:                                # pragma: no cover
            continue
        hits = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _TRANSCRIPT_READS
        }
        if hits:
            found[rel] = hits
    return found


#: Every module that reads transcript rows and renders them for a model, with
#: the read that makes it one. DERIVED-CHECKED: `test_the_builder_set_is_not
#: _hand_maintained` asserts this equals the discovered set minus
#: `_NOT_A_BUILDER`, so a new builder cannot be forgotten — only classified.
#: The `path=` label each builder must pass. Nothing else guards it — the
#: label is what separates a tag window from adoption's 1000-row backfill in
#: two histograms whose buckets differ by two orders of magnitude, and a
#: copy-pasted `path="tag"` in the fire path would put them in one series with
#: nobody able to see it.
PATHS = {
    "nora/engagement/context.py": "tag",
    "nora/app/fire.py": "fire",
    "nora/engagement/stages/debrief.py": "debrief",
    "nora/ingest/stages/standing_event.py": "standing_event",
    "nora/app/runtime.py": "dreamer",
    "nora/language/note_curator.py": "note_curator",
    "nora/app/adoption.py": "adoption",
    "nora/agent/tools.py": "load_transcript",
    "nora/ingest/relevance_gate.py": "relevance_gate",
    # The eval seam's dream pass (`design/evaluation/`). Its OWN label: this
    # test's control refuses a reused one, and it is right to — nothing here is
    # scraped alongside the loop's read (a `Subject` carries its own `Metrics()`,
    # built per case), so the distinct label costs nothing and says something
    # true about where the read came from.
    "nora/subject/dreaming.py": "dreamer_eval",
}

BUILDERS = {
    "nora/subject/dreaming.py": "since",
    "nora/engagement/context.py": "recent",
    "nora/app/fire.py": "recent",
    "nora/engagement/stages/debrief.py": "since_including_nora",
    "nora/ingest/stages/standing_event.py": "since_including_nora",
    "nora/app/runtime.py": "since",
    "nora/language/note_curator.py": "recent",
    "nora/app/adoption.py": "unattributed",
    "nora/agent/tools.py": "recent",
    "nora/ingest/relevance_gate.py": "recent",
}


def _source(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


@pytest.mark.parametrize("rel", sorted(BUILDERS))
def test_every_transcript_builder_awaits_the_shared_join(rel: str) -> None:
    """`await <something>.enrich(...)` appears in the module, and the name it
    is called on is the shared `MediaJoin` rather than a local reimplementation
    — which the import assertion is what establishes."""
    tree = ast.parse(_source(rel))

    imports_join = any(
        isinstance(node, ast.ImportFrom)
        and (node.module or "").endswith("domain.media_join")
        and any(alias.name == "MediaJoin" for alias in node.names)
        for node in ast.walk(tree)
    )
    assert imports_join, (
        f"{rel} builds a transcript for a model but does not import the "
        f"shared MediaJoin. Either it joins the media or it renders bare "
        f"handles where master rendered the description."
    )

    awaits_enrich = any(
        isinstance(node, ast.Await)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute)
        and node.value.func.attr == "enrich"
        for node in ast.walk(tree)
    )
    # `debrief.py` wraps it in `asyncio.wait_for`, so the awaited call is the
    # timeout rather than `enrich` itself — accept either shape, since what is
    # being asserted is that the call is MADE.
    calls_enrich = awaits_enrich or any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "enrich"
        for node in ast.walk(tree)
    )
    assert calls_enrich, f"{rel} imports MediaJoin but never calls enrich()"

    # And the result must go somewhere. `await join.enrich([])` — awaiting the
    # call and discarding both its input and its output — satisfied the check
    # above, which is verbatim the regression this file's docstring exists to
    # prevent (verified against `nora/app/adoption.py`, 2026-09-09).
    used = any(
        isinstance(node, ast.Assign | ast.AnnAssign | ast.Return)
        and "enrich" in ast.dump(node)
        for node in ast.walk(tree)
    ) or any(
        # `rows = list(await ...enrich(...))` and friends: the await is nested
        # inside a call whose result is bound.
        isinstance(node, ast.Assign | ast.AnnAssign) and "enrich" in ast.dump(node.value)
        for node in ast.walk(tree)
        if getattr(node, "value", None) is not None
    )
    assert used, (
        f"{rel} awaits enrich() and throws the result away — the rows that "
        f"reach the model are the ones the store returned, not the ones the "
        f"join rendered"
    )


def test_the_builder_set_is_not_hand_maintained() -> None:
    """**The gate that would have caught `load_transcript`.**

    `BUILDERS` was a hand-written allowlist with no discovery step, so a
    renamed builder failed and a builder that was never listed did not — and
    «never listed» is the only failure mode that has happened, twice: five
    builders when the join shipped, then `load_transcript` past the gate
    written to catch exactly that.

    Every module under `nora/` that calls one of the record store's transcript
    reads must be classified: a builder, or exempt with a reason. A new one is
    a red test rather than a silent omission."""
    discovered = set(_discovered_readers())
    classified = set(BUILDERS) | set(_NOT_A_BUILDER)

    unclassified = discovered - classified
    assert not unclassified, (
        f"these modules read the record store and are neither joined nor "
        f"exempted: {sorted(unclassified)}. Either call `MediaJoin.enrich` "
        f"(it renders what master rendered) or add it to `_NOT_A_BUILDER` "
        f"with the reason it hands rows to nobody who needs them."
    )
    stale = classified - discovered
    assert not stale, (
        f"these are listed but no longer read the record store: "
        f"{sorted(stale)} — delete the entry rather than leaving a list of "
        f"modules the gate silently checks nothing about."
    )


@pytest.mark.parametrize("rel,read", sorted(BUILDERS.items()))
def test_each_builder_still_makes_the_read_that_makes_it_one(
    rel: str, read: str,
) -> None:
    """The POSITIVE CONTROL for the join assertion, and it is not decoration:
    if a module stopped reading transcript rows, the join test would keep
    passing on an import nobody uses.

    It asserts the SPECIFIC read this module was classified on, not merely that
    the name appears somewhere — an earlier version accepted any attribute call
    with a matching name, so a builder keeping an unrelated `foo.recent(...)`
    stayed green."""
    assert read in _discovered_readers().get(rel, set()), (
        f"{rel} no longer calls {read}() — is it still a transcript builder?"
    )


def test_the_join_is_constructed_exactly_once() -> None:
    """ONE instance for the process. Eight copies would be eight chances to
    disagree about what a picture shows — and eight render bounds, eight
    screens, eight places to forget one."""
    composition = _source("nora/app/composition.py")
    assert composition.count("MediaJoin(") == 1, (
        "the composition root builds more than one MediaJoin; every consumer "
        "must hold the same instance"
    )


def test_the_screen_is_injected_rather_than_imported_by_the_join() -> None:
    """`nora/language/` sits two layers below `nora/agent/` in the import
    contract, so a join that imported `scan_identifiers` directly could never
    be reached by the note tier — the layering failure that would have pushed
    the join back to one consumer. `lint-imports` would catch the violation;
    this catches the shape BEFORE somebody writes it and then has to unpick
    seven call sites.
    """
    join = _source("nora/domain/media_join.py")
    tree = ast.parse(join)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("nora.agent"), (
                "nora.domain.media_join imports from nora.agent; the note tier "
                "cannot reach that layer, so the screen must stay injected"
            )
    assert "MediaJoin(" in _source("nora/app/composition.py")
    # By AST, not substring. `"screen=scan_identifiers" in source` is satisfied
    # by the words appearing ANYWHERE — including in a comment — so replacing
    # the screen with `lambda text: text` and mentioning the old name in the
    # line above kept this green while every model-authored description reached
    # all eight prompts unscreened (verified 2026-09-09).
    tree = ast.parse(_source("nora/app/composition.py"))
    screened = any(
        isinstance(node, ast.Call)
        and getattr(node.func, "id", None) == "MediaJoin"
        and any(
            kw.arg == "screen" and getattr(kw.value, "id", None) == "scan_identifiers"
            for kw in node.keywords
        )
        for node in ast.walk(tree)
    )
    assert screened, (
        "the composition root must hand `MediaJoin` the REAL `scan_identifiers` "
        "screen as its `screen=` argument; a join built without one renders "
        "model prose into eight prompts unscreened"
    )


@pytest.mark.parametrize("rel,path", sorted(PATHS.items()))
def test_each_builder_labels_its_own_path(rel: str, path: str) -> None:
    """One `path=` per site, and each its own.

    The two cost histograms are labelled BY path because the eight callers have
    nothing in common but the code — a tag window is a handful of blobs under a
    reply's latency, adoption's is a thousand-row backfill — so a copy-pasted
    label collapses them into one series where the batch paths saturate and
    nobody can see it. Nothing else in the suite reads what any site passes."""
    tree = ast.parse(_source(rel))
    passed = {
        kw.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "enrich"
        for kw in node.keywords
        if kw.arg == "path" and isinstance(kw.value, ast.Constant)
    }
    assert passed == {path}, (
        f"{rel} labels its media join {sorted(passed)}; it must be "
        f"{path!r} — a shared label puts a tag window and a backfill in one "
        f"histogram."
    )


def test_the_labels_are_distinct() -> None:
    """The control for the parametrized test above: it would pass just as
    happily if `PATHS` named the same string eight times."""
    assert len(set(PATHS.values())) == len(PATHS)
    assert set(PATHS) == set(BUILDERS)


def test_the_discovery_finds_a_builder_it_has_never_seen(tmp_path) -> None:
    """**K10: prove the gate works.**

    `test_the_builder_set_is_not_hand_maintained` is the reason a ninth
    unjoined builder cannot ship, and nothing demonstrated that its AST
    discovery actually FINDS one — a walk that returned nothing would report
    «all builders classified» with total confidence, which is the same
    null-result trap the classification is there to close.

    Driven through the same function the gate uses, over a synthetic tree, so
    the demonstration does not depend on somebody adding a real builder."""
    pkg = tmp_path / "nora"
    (pkg / "sub").mkdir(parents=True)
    (pkg / "sub" / "newcomer.py").write_text(
        "async def go(store):\n"
        "    rows = await store.since_including_nora(conversation_id='g', "
        "after_id=0, limit=10)\n"
        "    return rows\n",
        encoding="utf-8",
    )
    (pkg / "sub" / "innocent.py").write_text(
        "def go(x):\n    return x.unrelated()\n", encoding="utf-8")

    found = _discovered_readers(root=tmp_path)

    assert found == {"nora/sub/newcomer.py": {"since_including_nora"}}, (
        "the discovery walk cannot see a new transcript builder, so the gate "
        "that depends on it proves nothing"
    )

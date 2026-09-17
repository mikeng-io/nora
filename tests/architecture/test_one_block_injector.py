"""Every model-facing block reaches the prompt through ONE injector.

**`ContextBlock` is that injector and it has one assembler caller.** #220 built
it — `render()` owns the tag, the `untrusted` marker, the `note=` clause and the
attribute order; `assemble` owns placement and empty-vs-omit; `catalogue` derives
the header's «What you can see:» list from the same declaration so it cannot go
stale a fifth time. `build_prefill` uses all three. Five other prompt builders
write their opening tags by hand, which is how `<open_notes>` still ships with no
`untrusted="true"` and the note curator's `<note>` elements ship with no
container at all.

**Why a static gate, and why it is not the whole story.** The question is «was a
block FORGOTTEN», and a grep-shaped question gets a grep-shaped gate — the same
construction as `test_media_join_reaches_every_transcript`: discover from the
source, then classify, so a new hand-written tag has to be argued for rather
than merely not noticed.

The static half cannot see whether a block that DOES go through `ContextBlock`
carries anything, because `_declared` will happily emit one with `note=""` and
`body=""` purely so the catalogue names it — which is what `replying_to` and
`tag_message` are today. That is #187's pattern, a declaration standing in for
the thing declared, and the runtime half of this gate lives in
`tests/test_prefill.py::test_every_block_in_the_whole_turn_is_framed`, which
computes over every message the turn emits rather than the one
`_served_blocks` happens to read.

Issue #292. `HAND_WRITTEN` empty is the finish line.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

_OPEN = re.compile(r"<([a-z_][a-z0-9_]*)[\s>]")
_CLOSE = re.compile(r"</([a-z_][a-z0-9_]*)>")

#: **The census, at `08d369b1`, amended once at `821693a1`.** Module -> the
#: elements it opens by hand.
#:
#: The amendment is the mechanism working rather than a hole in it, and it is
#: recorded here because a silently-edited baseline is the thing this file is
#: against. PR #293 (#189) landed `<how_to_read_reasoning>` into
#: `debrief_render.py` hours after the census was taken;
#: `test_the_census_can_only_shrink` reddened on the rebase and named it. It is
#: added deliberately: the module's own row is already an expected failure, so
#: recording one more hand-written element there does not widen what the gate
#: asserts — and the legend it renders is a correct fix (#189's clip mark needs
#: an always-present explanation) that simply has no injector to go through yet.
#: A second such amendment without a reason beside it would be the budget this
#: construction exists to refuse.
#: One `xfail` per module below, so converting a builder turns a NAMED row green
#: and `strict=True` turns it red if the line is left behind. The recorded tags
#: are pinned in both directions by `test_the_census_can_only_shrink`: a module
#: that grows a NEW hand-written tag fails even while its row is still expected
#: to fail, so the gate cannot be widened by the thing it is measuring.
HAND_WRITTEN = {
    "nora/agent/prefill.py": (
        # `context_header` is the static head's own wrapper rather than a data
        # block. `person` is a ROW inside `people_in_window`, which does carry
        # framing. `replying_to` and `tag_message` are the two that matter —
        # they were #239, they are declared to the catalogue with `note=""` and
        # `body=""`, and no framing test can currently see them.
        "context_header", "person", "replying_to", "tag_message",
    ),
    "nora/language/proactive.py": (
        "decisions", "home_room", "now", "open_notes", "people", "rooms",
        "self_card",
    ),
    "nora/engagement/debrief_render.py": (
        "conversation", "how_to_read_reasoning", "how_to_read_the_transcript",
        "reasoning", "standing_prior", "transcript", "turn", "what_happened",
    ),
    "nora/ingest/relevance_gate.py": (
        # `nora_doctrine` is the doctrine wrapper this module re-emits rather
        # than a data block; it goes when the rest do.
        "group_transcript", "nora_doctrine", "standing",
    ),
    "nora/language/note_curator.py": ("note", "now"),
    "nora/language/note_candidates.py": ("room",),
    "nora/language/culture.py": ("previous_account",),
    "nora/skills/loader.py": (
        "available_skills", "description", "name", "skill",
    ),
    "nora/infra/transit/arrivals.py": ("untrusted",),
}

#: Has its own injector, and it is the one #220 took the pattern FROM.
#: `render_section` produces the exact bytes a section contributes and has two
#: consumers — the assembler and the hasher — which is the trick `ContextBlock`
#: borrows for the block and its catalogue line.
_OWN_INJECTOR = {
    "nora/doctrine/loader.py": "`render_section` — doctrine's own injector",
}

#: Not a prompt. Smith renders an HTML PAGE for a person to open in a browser;
#: `<div>` there is a division of a document, not a block handed to a model.
_NOT_A_PROMPT = {
    "nora/compute/smith/page_shell.py": "renders an HTML page, not a prompt",
    "nora/compute/smith/skills/markdown/render.py": "renders HTML",
    "nora/compute/smith/tests/test_page_shell.py": "a test of the HTML",
    "nora/compute/smith/tests/test_render_html_report.py": "a test of the HTML",
    "nora/compute/smith/tests/test_render_map.py": "a test of the HTML",
    "nora/compute/smith/tests/test_render_markdown.py": "a test of the HTML",
    "nora/compute/smith/tests/test_render_mermaid.py": "a test of the HTML",
}


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """Every Constant that is a DOCSTRING — prose about a tag, not a tag.

    Without this the scan reads `render_notes`' own docstring arguing about
    `<notes>` as though the module emitted one. The repo's docstrings discuss
    these elements constantly, which is the whole reason the discriminator
    below is «opens AND closes it in emitted text» rather than «mentions it».
    """
    out: set[int] = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if (
            isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
        ):
            out.add(id(body[0].value))
    return out


def _built_tags(rel: str) -> tuple[str, ...]:
    """The elements a module OPENS AND CLOSES in text it emits.

    Both halves are required, and that is the discriminator rather than a
    heuristic: prose names `<notes>`; a builder writes `</notes>` too. It also
    keeps `<untrusted source=…>`-style wrappers in and leaves ledger keys like
    `"<memory reads this pass>"` and self-closing markers out, because neither
    has a closing tag anywhere.
    """
    tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
    skip = _docstring_nodes(tree)
    parts: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
            parts.append(node.value)
        elif isinstance(node, ast.JoinedStr):
            parts += [
                v.value for v in node.values
                if isinstance(v, ast.Constant) and isinstance(v.value, str)
            ]
    blob = "\n".join(parts)
    return tuple(sorted(set(_OPEN.findall(blob)) & set(_CLOSE.findall(blob))))


def _discovered() -> dict[str, tuple[str, ...]]:
    out = {}
    for path in sorted((ROOT / "nora").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel == "nora/domain/blocks.py":
            continue
        try:
            tags = _built_tags(rel)
        except SyntaxError:                                # pragma: no cover
            continue
        if tags:
            out[rel] = tags
    return out


_CLASSIFIED = set(HAND_WRITTEN) | set(_OWN_INJECTOR) | set(_NOT_A_PROMPT)


def test_every_module_that_writes_a_tag_is_classified():
    """The discovery step. A new hand-written block appears here and has to be
    argued for — it cannot merely go unnoticed, which is the only failure mode
    that has actually happened."""
    discovered = _discovered()
    unclassified = set(discovered) - _CLASSIFIED
    assert not unclassified, (
        f"{sorted(unclassified)} open and close an element by hand and are "
        "classified nowhere. Route it through ContextBlock (see #292), or "
        "classify it in _OWN_INJECTOR / _NOT_A_PROMPT with a reason."
    )
    stale = _CLASSIFIED - set(discovered)
    assert not stale, (
        f"{sorted(stale)} are classified here and write no tag any more. "
        "Delete their lines: a classification for code that is gone widens "
        "the gate silently."
    )


def test_the_census_can_only_shrink():
    """A module still on the census may not grow a NEW hand-written element.

    The `xfail` below says «this module has not been converted yet». It must
    not also say «and anything added to it meanwhile is fine» — that is how a
    pinned baseline becomes a budget. Recorded tags may disappear; new ones
    fail here even while the module's own row is still expected to fail.
    """
    discovered = _discovered()
    for rel, recorded in sorted(HAND_WRITTEN.items()):
        added = set(discovered.get(rel, ())) - set(recorded)
        assert not added, (
            f"{rel} grew hand-written element(s) {sorted(added)} while still "
            "on #292's census. New model-facing blocks go through ContextBlock."
        )


@pytest.mark.parametrize("rel", sorted(HAND_WRITTEN))
@pytest.mark.xfail(
    strict=True,
    reason="issue #292: #220's injector reached one prompt builder of six",
)
def test_no_block_is_written_by_hand(rel: str):
    """THE GATE. One injector, so `untrusted`, the framing clause, the order
    and the catalogue line stop being per-site decisions."""
    assert not _built_tags(rel), (
        f"{rel} opens {list(_built_tags(rel))} by hand instead of through "
        "ContextBlock.render()"
    )

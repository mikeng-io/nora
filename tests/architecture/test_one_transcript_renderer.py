"""Every transcript a model reads is rendered by ONE renderer — `render_messages`.

**This file exists because #220 closed with its own renderer wired into one
prompt builder.** `render_messages` puts each message in its own element, with
the handle, the age and the speaker as ATTRIBUTES, because the separator has to
be something a row's content cannot contain — `relevance_gate`'s own rule,
which `render_rows` satisfies only by collapsing every body to one line first.
The tag path got it. The relevance gate, the note curator, the note-candidates
pass, the culture fold and the debrief fold did not, and each still flattens a
member's paragraph, list or pasted block into a single line before a model
reads it.

That is the same shape #116 shipped and `test_media_join_reaches_every_transcript`
was written to brake: a cross-cutting property applied to the builder somebody
happened to have open. This is its render-side twin, and it is deliberately the
same construction — **discover from the source, then classify** — because the
only failure mode that has actually happened here is a builder nobody listed.

**The exemption is real and it is narrow.** `markup=False` says the destination
is not a prompt: the operator's Discord review channel, where escaping writes
`&amp;` in front of a person and breaks every URL. Those callers are enumerated
in `test_escaping_is_for_prompts_only` and are exempt HERE too — but this file
asserts the exemption rather than trusting it, because an exemption nothing
checks is how the list stops matching the code.

`tests/architecture/test_one_row_renders_one_line.py` is the other half: what a
row renderer must do for the builders that still use one.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

#: The two renderers in the tree that turn records into prompt text. A module
#: calling either is rendering something for somebody, and has to say which.
_RENDERERS = ("render_rows", "render_messages", "render_elements", "render_row_window")

#: **The element renderers.** `render_messages` is a thin caller of
#: `render_elements` (one renderer, two entry points), so a builder is
#: converted when it uses EITHER and no longer uses `render_rows`. Builders
#: whose attribute set is the transcript's — `n`/`age`/`from`/`replying_to` —
#: call `render_messages`; the ones carrying a different set (a real row id, a
#: wall clock, a room label, a delivery mark) call `render_elements` directly
#: rather than bending the transcript's signature around them.
#: `render_row_window` is `render_elements` with `n` fixed to the row's own id
#: (context-assembly step 1) — the same one renderer, a third entry point — and
#: is what note-candidates and the curator call, so it counts as converted for
#: the same reason.
_ELEMENT_RENDERERS = ("render_messages", "render_elements", "render_row_window")

#: **The converted builders.** Module -> what it renders.
CONVERTED = {
    "nora/agent/prefill.py": "the tag turn's group transcript",
    "nora/ingest/relevance_gate.py": "the window the gate decides on",
    "nora/language/culture.py": "the batch the culture fold reads",
    "nora/language/note_curator.py":
        "her own sends, and the evidence rows the retirement decision reads",
    "nora/language/note_candidates.py":
        "the message batch candidates are proposed from — `n` is the ROW ID "
        "here, because `accept()` joins the model's `message_ids` onto it",
    "nora/engagement/debrief_render.py":
        "the window the fold and the grounding verdict read",
}

#: **Transcript builders still on `render_rows`** — the census this gate was
#: opened with, at `08d369b1`. Each entry is one `xfail` below, so converting a
#: builder turns a NAMED row green and forgetting to delete its line here turns
#: the same row red (`strict=True`). The list is the scope of issue #292; an
#: empty dict is the finish line.
#:
#: Ordered by blast radius, lightest first — the order the conversion lands in.
UNCONVERTED: dict[str, str] = {}

#: Modules that call a row renderer and are NOT rendering a transcript. The
#: direction matters, exactly as it does in `test_media_join_reaches_every_
#: transcript`: listing the builders alone catches a rename and misses a
#: builder that was never listed, and «never listed» is the failure that keeps
#: happening. Inverted, a new caller appears in the discovered set and has to be
#: classified — converted, or exempted here with a sentence.
_NOT_A_TRANSCRIPT = {
    # ONE ROOM, ONE LINE. The proactive pass renders `UnitRoom` rows — a
    # unit's rooms and where her own fold left each one — never a member's
    # message. Design 39 §1 keeps `load_transcript` out of that pass on
    # purpose, which is the same reason `test_media_join_reaches_every_
    # transcript` exempts this module.
    "nora/language/proactive.py": "renders one line per ROOM, not per message",
    # The NOTE INDEX, not a transcript — one element per standing note, through
    # the same `render_elements` the transcripts use. It appears in this
    # discovery because "calls an element renderer" is the net; it is exempt
    # from the transcript rule because it renders no message. Its own
    # unification is the other half of #292 and is gated by
    # `test_one_block_injector`.
    "nora/language/note_render.py": "renders the note index, not a transcript",
    # The SKILL CATALOGUE — one element per installed skill, name and
    # when-to-load as attributes. Operator-installed metadata, not a message
    # anybody sent, so the transcript rule does not apply; it is here because
    # "calls an element renderer" is the net this gate casts.
    "nora/skills/loader.py": "renders the skill catalogue, not a transcript",
    # THE WORKING SET, not a transcript — one element per thing found and not
    # yet given the room, through the same `render_elements` the transcripts
    # use (design/standing-compact/30-working-set.md, issue #445). `note_
    # render.py`'s own reason one tier over: it renders no message, so the
    # transcript rule does not apply.
    "nora/language/standing_render.py": "renders the working set, not a transcript",
}

#: The operator's surfaces: a Discord review channel, not a prompt. Exempt from
#: the renderer rule AND asserted to be what they claim — see
#: `test_an_operator_surface_actually_passes_markup_false`.
_OPERATOR_SURFACE = {
    "nora/app/fire.py": "the operator's review channel — `markup=False`",
}


def _calls(rel: str) -> dict[str, list[ast.Call]]:
    """Every `render_rows` / `render_messages` call in one module, by name."""
    tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
    found: dict[str, list[ast.Call]] = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in _RENDERERS
        ):
            found.setdefault(node.func.id, []).append(node)
    return found


def _discovered() -> set[str]:
    """Every module under `nora/` that calls a row renderer.

    A source scan, because the question is «was anything FORGOTTEN» and a
    hand-written list cannot answer that about itself.
    """
    out = set()
    for path in sorted((ROOT / "nora").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel == "nora/domain/blocks.py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:                                # pragma: no cover
            continue
        if any(
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id in _RENDERERS
            for n in ast.walk(tree)
        ):
            out.add(rel)
    return out


_CLASSIFIED = {**CONVERTED, **UNCONVERTED, **_NOT_A_TRANSCRIPT, **_OPERATOR_SURFACE}


def test_every_row_renderer_caller_is_classified():
    """The discovery step, and the reason this gate is not a bare allowlist.

    A new module that renders rows for a model appears here and must be
    classified. Nothing else in this file can notice one that was never listed.
    """
    discovered = _discovered()
    unclassified = discovered - set(_CLASSIFIED)
    assert not unclassified, (
        f"{sorted(unclassified)} render rows and are classified nowhere. "
        "Add each to CONVERTED (it uses render_messages), UNCONVERTED (it is a "
        "transcript still on render_rows — see #292), _NOT_A_TRANSCRIPT, or "
        "_OPERATOR_SURFACE, with a reason."
    )
    stale = set(_CLASSIFIED) - discovered
    assert not stale, (
        f"{sorted(stale)} are classified here and no longer render rows at "
        "all. Delete their lines — a classification for code that is gone is "
        "the stale catalogue this construction exists to prevent."
    )


def test_no_module_is_classified_twice():
    """Four dicts, one home each. Two entries for one module means one of them
    is being silently ignored by `_CLASSIFIED`'s merge."""
    seen: dict[str, list[str]] = {}
    for label, group in (
        ("CONVERTED", CONVERTED), ("UNCONVERTED", UNCONVERTED),
        ("_NOT_A_TRANSCRIPT", _NOT_A_TRANSCRIPT),
        ("_OPERATOR_SURFACE", _OPERATOR_SURFACE),
    ):
        for rel in group:
            seen.setdefault(rel, []).append(label)
    doubled = {k: v for k, v in seen.items() if len(v) > 1}
    assert not doubled, f"classified in more than one group: {doubled}"


@pytest.mark.parametrize("rel", sorted(CONVERTED))
def test_a_converted_builder_renders_messages_and_not_rows(rel: str):
    """What CONVERTED means, asserted rather than asserted-to-have-been-done."""
    calls = _calls(rel)
    assert any(r in calls for r in _ELEMENT_RENDERERS), (
        f"{rel} no longer calls an element renderer"
    )
    assert "render_rows" not in calls, (
        f"{rel} is listed as converted and calls render_rows again — a "
        "transcript builder does not get to have both"
    )


@pytest.mark.parametrize("rel", sorted(UNCONVERTED))
@pytest.mark.xfail(
    strict=True,
    reason="issue #292: #220's transcript renderer reached one builder of six",
)
def test_every_transcript_builder_renders_messages(rel: str):
    """THE GATE. One renderer for every transcript a model reads.

    Each `xfail` here is one row of #292's census. Converting a builder turns
    its own row green; `strict=True` means converting one and forgetting to
    move it out of `UNCONVERTED` turns that row red instead of passing
    silently, which is the half a non-strict xfail gets wrong.
    """
    calls = _calls(rel)
    assert "render_rows" not in calls, (
        f"{rel} still renders its transcript with render_rows, which collapses "
        "every body to one line before a model reads it"
    )
    assert any(r in calls for r in _ELEMENT_RENDERERS)


@pytest.mark.parametrize("rel", sorted(_OPERATOR_SURFACE))
def test_an_operator_surface_actually_passes_markup_false(rel: str):
    """The exemption, CHECKED. `markup=False` is what makes a caller an
    operator surface; a call in an exempt module that omits it is a prompt
    render hiding behind the exemption, and nothing else in this file looks."""
    for call in _calls(rel).get("render_rows", []):
        flag = next((k for k in call.keywords if k.arg == "markup"), None)
        literal = flag.value if flag is not None else None
        assert isinstance(literal, ast.Constant) and literal.value is False, (
            f"{rel}:{call.lineno} is in _OPERATOR_SURFACE but does not pass "
            "markup=False — either it is a prompt render (convert it) or the "
            "exemption is wrong"
        )

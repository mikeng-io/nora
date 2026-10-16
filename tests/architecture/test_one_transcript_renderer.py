"""Every transcript a model reads is rendered by ONE renderer — `render_messages`.

`render_messages` puts each message in its own element, with the handle, the age and the
speaker as ATTRIBUTES, because the separator has to be something a row's content cannot
contain — `relevance_gate`'s own rule, which `render_rows` satisfies only by collapsing
every body to one line first. A builder still on `render_rows` flattens a member's
paragraph, list or pasted block into a single line before a model reads it.

This is the render-side twin of `test_media_join_reaches_every_transcript`: a
cross-cutting property applied to the builder somebody happened to have open. It is
deliberately the same construction — **discover from the source, then classify** — because
the failure mode is a builder nobody listed.

**The exemption is real and it is narrow.** `markup=False` says the destination is not a
prompt: the operator's Discord review channel, where escaping writes `&amp;` in front of a
person and breaks every URL. Those callers are enumerated in
`test_escaping_is_for_prompts_only` and are exempt HERE too — but this file asserts the
exemption rather than trusting it, because an exemption nothing checks is how the list
stops matching the code.

`tests/architecture/test_one_row_renders_one_line.py` is the other half: what a row
renderer must do for the builders that still use one.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

#: The two renderers in the tree that turn records into prompt text. A module
#: calling either is rendering something for somebody, and has to say which.
_RENDERERS = ("render_rows", "render_messages", "render_elements", "render_row_window")

#: **The element renderers.** `render_messages` is a thin caller of `render_elements` (one
#: renderer, two entry points), so a builder is converted when it uses EITHER and no
#: longer uses `render_rows`. Builders whose attribute set is the transcript's —
#: `n`/`age`/`from`/`replying_to` — call `render_messages`; the ones carrying a different
#: set (a real row id, a wall clock, a room label, a delivery mark) call `render_elements`
#: directly rather than bending the transcript's signature around them.
#: `render_row_window` is `render_elements` with `n` fixed to the row's own id — the same
#: one renderer, a third entry point — and is what the Cognition agenda calls, so it
#: counts as converted for the same reason.
_ELEMENT_RENDERERS = ("render_messages", "render_elements", "render_row_window")

#: **The converted builders.** Module -> what it renders.
CONVERTED = {
    "nora/agent/prefill.py": "the tag turn's group transcript",
    "nora/ingest/relevance_gate.py": "the window the gate decides on",
    "nora/language/culture.py": "the batch the culture fold reads",
    "nora/language/character.py": "a person's own lines a Character session reads",
    "nora/language/lessons.py": "her sends and their quoted replies a Lessons session reads",
    "nora/engagement/debrief_render.py":
        "the window the fold and the grounding verdict read",
    "nora/language/cognition_render.py":
        "the Cognition session's agenda: each item's messages through "
        "`render_row_window` (`n` is the message's own id, so what the model "
        "reads is what `note_create` requires) and its Rollup rows through "
        "`render_elements`",
}

#: Modules that call a row renderer and are NOT rendering a transcript. The
#: direction matters, exactly as it does in `test_media_join_reaches_every_
#: transcript`: listing the builders alone catches a rename and misses a
#: builder that was never listed, and «never listed» is the failure that keeps
#: happening. Inverted, a new caller appears in the discovered set and has to be
#: classified — converted, or exempted here with a sentence.
_NOT_A_TRANSCRIPT = {
    # The NOTE INDEX, not a transcript — one element per standing note, through the same
    # `render_elements` the transcripts use. It appears in this discovery because "calls
    # an element renderer" is the net; it is exempt from the transcript rule because it
    # renders no message. Its unification is gated by `test_one_block_injector`.
    "nora/language/note_render.py": "renders the note index, not a transcript",
    "nora/language/memo_render.py": "renders the memo index (DEC-306), not a transcript",
    # The `<lessons>` block: one element per active lesson, her own practice.
    "nora/language/lesson_render.py": "renders her lessons, not a transcript",
    # The doctrine check's law list: one element per law rule, doctrine text.
    "nora/language/doctrine_check.py": "renders law rules for the doctrine check, not a transcript",
    # The SKILL CATALOGUE — one element per installed skill, name and
    # when-to-load as attributes. Operator-installed metadata, not a message
    # anybody sent, so the transcript rule does not apply; it is here because
    # "calls an element renderer" is the net this gate casts.
    "nora/skills/loader.py": "renders the skill catalogue, not a transcript",
    # THE WORKING SET, not a transcript — one element per thing found and not yet given
    # the room, through the same `render_elements` the transcripts use. `note_render.py`'s
    # own reason one tier over: it renders no message, so the transcript rule does not
    # apply.
    "nora/language/standing_render.py": "renders the working set, not a transcript",
    # THE EVENT INDEX, not a transcript — one element per recorded event (id, status,
    # when, venue, revision, the title as content), through the same `render_elements` the
    # transcripts use. `note_render.py`'s own reason one index over: it renders no
    # message.
    "nora/language/event_render.py": "renders the event index, not a transcript",
    # THE SUB-AGENT'S BRIEF, not a transcript: its objective, inputs and documents and
    # each decision (question, answer, who made it) as elements through `render_elements`.
    # The worker starts with no conversation at all.
    "nora/language/subagent_render.py": "renders a brief, never a transcript",
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


def _row_renderers_called(tree: ast.Module) -> dict[str, int]:
    out: dict[str, int] = {}
    for n in ast.walk(tree):
        if (
            isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id in _RENDERERS
        ):
            out[n.func.id] = out.get(n.func.id, 0) + 1
    return out


def _unclassified(discovered: set[str]) -> set[str]:
    return discovered - set(_CLASSIFIED)


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


_CLASSIFIED = {**CONVERTED, **_NOT_A_TRANSCRIPT, **_OPERATOR_SURFACE}


def test_every_row_renderer_caller_is_classified():
    """The discovery step, and the reason this gate is not a bare allowlist.

    A new module that renders rows for a model appears here and must be
    classified. Nothing else in this file can notice one that was never listed.
    """
    discovered = _discovered()
    unclassified = _unclassified(discovered)
    assert not unclassified, (
        f"{sorted(unclassified)} render rows and are classified nowhere. "
        "Convert it (render_messages, then add it to CONVERTED), or, if it "
        "renders no transcript, add it to _NOT_A_TRANSCRIPT or "
        "_OPERATOR_SURFACE with a reason. There is no list of transcript "
        "builders still on render_rows."
    )
    stale = set(_CLASSIFIED) - discovered
    assert not stale, (
        f"{sorted(stale)} are classified here and no longer render rows at "
        "all. Delete their lines — a classification for code that is gone is "
        "the stale catalogue this construction exists to prevent."
    )


def test_no_module_is_classified_twice():
    """Three dicts, one home each. Two entries for one module means one of them
    is being silently ignored by `_CLASSIFIED`'s merge."""
    seen: dict[str, list[str]] = {}
    for label, group in (
        ("CONVERTED", CONVERTED),
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


def test_a_transcript_builder_on_render_rows_is_caught():
    """Positive control, through the same two functions the gate uses: a
    synthetic module that renders a transcript with `render_rows` is
    discovered and classified nowhere, so the census test fails on it."""
    offender = ast.parse("def build(rows):\n    return render_rows(rows)\n")
    assert _row_renderers_called(offender) == {"render_rows": 1}
    assert _unclassified({"nora/scratch.py"}) == {"nora/scratch.py"}
    assert _unclassified({"nora/language/culture.py"}) == set()


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

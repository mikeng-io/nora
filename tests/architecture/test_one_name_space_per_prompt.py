"""Every renderer names a speaker the same way, and none of them names one by id.

**DEC-80's missing half.** That decision unified the name space — the transcript
labels a speaker by their `identity.toml` name, not their self-set platform
profile name — because one prompt held `group_members: ["Grant Carver"]` while
every transcript line read `陳志明 Chi Ming`, and asked where Grant was she
answered that nobody in the group is called that, then told a DIFFERENT member
that Grant was them.

Its four named tests each go through ONE consumer. So when the note tier grew two
renderers ending the chain in `row.sender_id` instead of `"unknown"`, nothing went
red — and that is two defects in one expression:

* **A third name for one person in one prompt**, which is DEC-80's own bug. The
  `<notes>` index renders beside these lines.
* **A raw platform identifier on the wire to the model provider** — an E.164 or
  an ACI — which is the egress R-18 exists to keep closed.
  `app/fire._gate_verdict_text` carried the identical line; its fix comment
  records that R-18 refused the whole post at the Deliverer, so unmapped
  senders' verdicts silently never reached the review channel. The note tier's
  copies had no such backstop, because a prompt has no Deliverer.

DEC-80's own row records the shape this file exists for: *"the first pass unified
two of THREE renderers"*, and *"the claim «both context assemblers call it, so the
two paths cannot drift» was true and incomplete: the count was three."* It is now
nine, and a per-consumer test cannot answer «was anything forgotten».

**Discovered, not listed**, for `test_media_join_reaches_every_transcript.py`'s
measured reason — listing the renderers catches a rename and misses the one that
was never listed, which is the only failure mode that has actually happened.
Issue #219 tracks folding all of these into one renderer, which is what removes
the need for this gate rather than adding a fourth of them.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: The chain every speaker label ends in. `"unknown"` rather than
#: `Persons.sanitize_unmapped` ("852…678") because five renderers already use it
#: and the point of DEC-80 is ONE name space — moving all of them to the
#: sanitized form is a live option (issue #219 lists it as a decision to record),
#: but having both in use is the thing that is never right.
_FALLBACK = "unknown"

#: Names a renderer may end the chain in instead, with the reason. Empty, and
#: that is the state worth defending: every exemption here is a prompt or a post
#: where one person can appear under two names.
_NOT_THE_FALLBACK: dict[tuple[str, str], str] = {}


def _speaker_chains() -> dict[tuple[str, str], str]:
    """Every `row.person_name or …` chain under `nora/`, by (module, function).

    The discovery key is `person_name` appearing in a `BoolOp` of `or`s, which is
    what a speaker label IS in this codebase. A renderer that spells it some
    other way is invisible here, and
    `test_no_renderer_names_a_speaker_outside_the_discovered_set` below is the
    backstop — WRITTEN, unlike the version of this sentence that shipped on
    2026-09-12, which cited a test by name that did not exist anywhere in the
    tree. A docstring claiming a failure mode is covered, when it is not, is
    worse than silence: it is the one thing in this file a review called
    genuinely decorative.
    """
    found: dict[tuple[str, str], str] = {}
    for path in sorted((ROOT / "nora").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:                                # pragma: no cover
            continue
        for fn in (n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)):
            for node in ast.walk(fn):
                if not (isinstance(node, ast.BoolOp)
                        and isinstance(node.op, ast.Or)):
                    continue
                parts = [ast.unparse(v) for v in node.values]
                if not any(p.endswith(".person_name") for p in parts):
                    continue
                found[(rel, fn.name)] = " or ".join(parts)
    return found


def test_no_renderer_names_a_speaker_by_their_platform_id():
    """The R-18 half. A `sender_id` at the end of this chain is a phone number
    or an ACI reaching whatever the renderer feeds — a model provider for six of
    them."""
    leaks = {
        site: chain for site, chain in _speaker_chains().items()
        if "sender_id" in chain
    }
    assert not leaks, (
        "a speaker label ends in a raw platform identifier — use "
        f'`or "{_FALLBACK}"`:\n'
        + "\n".join(f"  {f}  in {fn}()  {c}" for (f, fn), c in sorted(leaks.items()))
    )


def test_every_speaker_label_ends_in_the_same_fallback():
    """The DEC-80 half, which is the one a leak test alone would not catch: two
    renderers could both avoid `sender_id` and still disagree, which is exactly
    the two-name-spaces prompt the decision was made about."""
    wrong = {
        site: chain for site, chain in _speaker_chains().items()
        if not chain.rstrip().endswith(f"'{_FALLBACK}'")
        and site not in _NOT_THE_FALLBACK
    }
    assert not wrong, (
        f'every speaker label ends `or "{_FALLBACK}"`. These do not — either '
        "change them, or add the site to `_NOT_THE_FALLBACK` with the reason:\n"
        + "\n".join(f"  {f}  in {fn}()  {c}" for (f, fn), c in sorted(wrong.items()))
    )


def test_the_projection_is_preferred_over_the_profile_name():
    """`person_name` FIRST, always. Reversing the order is DEC-80 undone while
    every other assertion here stays green: the label would resolve, carry no
    identifier, and still be the spoofable self-set platform name that the
    roster and `group_members` do not use."""
    for site, chain in _speaker_chains().items():
        parts = [p.strip() for p in chain.split(" or ")]
        assert parts[0].endswith(".person_name"), (
            f"{site[0]} in {site[1]}() prefers something over the identity "
            f"name: {chain}"
        )


#: Modules that name a speaker WITHOUT an `or`-chain through `person_name`, each
#: with the reason. Every entry is invisible to the three assertions above, so
#: this list is the exact size of this gate's blind spot — stated, rather than
#: left for a review to find.
_NAMES_A_SPEAKER_ANOTHER_WAY = {
    "nora/obs/determination.py": (
        "reads the projection through `getattr(row, 'person_name', None)` "
        "because `window_from` is duck-typed over anything with the two fields "
        "and this module may import no `nora.*` — so the chain is a call, not "
        "an attribute, and the AST discovery cannot see it. Its fallback is "
        "`None` rather than `\"unknown\"` on purpose: `render_mirror` has always "
        "rendered an unnamed sender as nothing, and it is an operator surface."
    ),
}


def test_no_renderer_names_a_speaker_outside_the_discovered_set():
    """The backstop the docstring above used to CITE without anybody writing it.

    Any module that reads `sender_name` in a rendering position but has no
    discovered `person_name` chain is naming a speaker in a way the three
    assertions above cannot check. That is allowed, and it is declared here with
    the reason — which is the difference between a known blind spot and an
    unknown one.
    """
    import ast

    # NO skip list keyed on "this function has a chain somewhere". Two earlier
    # cuts of this test had one — first per file, then per function — and
    # NEITHER bound: `fire._gate_verdict_text` holds the canonical chain AND
    # could render a bare `row.sender_name` on the very next line, and both
    # versions waved it through on the strength of the chain above it.
    #
    # What is actually wrong is rendering the RAW attribute into a line, whether
    # or not a correct chain exists nearby. So that is what this looks for, and
    # the only exits are the declared blind spots.
    offenders = {}
    for path in sorted((ROOT / "nora").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel in _NAMES_A_SPEAKER_ANOTHER_WAY:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:                                # pragma: no cover
            continue
        for fn in (n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)):
            for node in ast.walk(fn):
                if not isinstance(node, ast.JoinedStr):
                    continue
                if any(
                    isinstance(n, ast.Attribute) and n.attr == "sender_name"
                    for part in node.values if isinstance(part, ast.FormattedValue)
                    for n in ast.walk(part)
                ):
                    offenders[f"{rel}::{fn.name}"] = node.lineno
    assert not offenders, (
        "these render a speaker from `sender_name` with no `person_name` chain, "
        "so every assertion in this file skips them. Give them the canonical "
        "chain, or declare them in `_NAMES_A_SPEAKER_ANOTHER_WAY` with a "
        f"reason:\n{offenders}"
    )


def test_every_declared_blind_spot_still_exists():
    """A stale entry is a module this file claims to have classified and has not
    looked at since — the same inverse the exemption list two files over uses."""
    for rel in _NAMES_A_SPEAKER_ANOTHER_WAY:
        assert (ROOT / rel).exists(), f"declared blind spot is gone: {rel}"


def test_the_discovered_set_covers_the_renderers_we_know_about():
    """The non-degeneracy guard. Every assertion above passes trivially if the
    discovery finds nothing, and a refactor that renames `person_name` or moves
    these chains behind a helper would do exactly that — silently.

    The floor is the DISCOVERED count at the time of writing — seven chains,
    not the nine RENDERERS, and the first version of this sentence confused the
    two in a file whose whole purpose is non-degeneracy. A shared
    renderer (issue #219) will legitimately take it DOWN, and that is the moment
    to delete this file rather than lower the number: one renderer needs no gate
    saying the other eight agree with it.
    """
    chains = _speaker_chains()
    assert len(chains) >= 7, (
        f"only {len(chains)} speaker chains discovered ({sorted(chains)}) — "
        "either they moved behind a helper (delete this file, the gate's job is "
        "done) or the detector stopped matching (fix it; the assertions above "
        "are worthless until it does)"
    )

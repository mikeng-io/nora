"""ADR 0007 structural enforcement: every model-facing tool description must
match the tool-surface policy.

The policy (docs/adr/0007-tool-surface-policy.md):

1. Opening = what + when-to-call, in the first paragraph (ADR 0015 §2).
   Wrapped lines belong to that same paragraph. Style guidance, voice control and negative
   examples live AFTER the opening in a labelled SHOULD block, never as
   conditions on calling.
2. RFC 2119 register: MUST is only turn correctness; SHOULD is style;
   MAY is permission. Permission is never phrased as obligation, and
   advisory style is never phrased as a reason not to call.
3. English-only normative prose. Quoted non-English tokens are allowed
   only as load-bearing examples (a trigger word the tool fires on, an
   example argument). CJK in instruction prose fails.
4. Three-level length ladder: L1 sentence (what+when), L2 +one constraint,
   L3 contract (status machines). Most tools are L1.

Why this exists: a long style essay in a tool description is read by
reasoning models as preconditions on deserving to call, and they answer in
prose instead of calling. This test exists so the essay shape cannot return
and the opening cannot drift back to style-first.
"""

import ast
import re
from pathlib import Path

import pytest

from nora.agent.registry import _opening

TOOLS_PATH = Path(__file__).parent.parent / "nora" / "agent" / "tools.py"

#: When-to-call trigger frames, in the tool's own register. A description
#: opening must contain at least one of these after the what-clause.
WHEN_TRIGGERS = re.compile(
    r"call this when|call this for|use when|use this when|when you|"
    r"when somebody|when someone|the moment somebody|the moment someone|"
    r"whenever someone|whenever somebody|whenever you|if somebody|"
    r"when the room|when answering|call it when|somebody tells you|"
    r"someone tells you|when the time|when a message|when somebody asks|"
    r"when someone asks|when the room wants|use it to|call this before|"
    r"call list_events|only call this if|reach for it when|before you|"
    r"reach for it before|call it whenever|whenever a number",
    re.IGNORECASE,
)

#: Lexical suppression guard over the first paragraph, unwrapped by the same
#: extractor the catalogue uses (ADR 0015 §2). Later scope/contract content is
#: outside this check; this regex does not classify its meaning. A SHOULD/MAY
#: label inside the opening does not exempt the remainder of that paragraph.
SUPPRESSION_TOKENS = re.compile(
    r"too long|too soft|only a scoreboard|do not send|never send|"
    r"is not worth|not good enough|do not call|don't call|"
    r"not worth calling|no need to call|better not to",
    re.IGNORECASE,
)

#: MUST phrasing in the whole docstring (register discipline: MUST is
#: reserved for turn correctness; a MUST that is really style fails).
MUST_RE = re.compile(r"\bMUST\b")
SHOULD_LABEL_RE = re.compile(r"^SHOULD\b", re.MULTILINE)

#: CJK range — normative prose must be English. Quoted examples (「」…)
#: are allowed; the check strips quoted spans before testing prose.
CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")
# CJK 角括號 «…» and 「…」 are both quoted-example delimiters used in this
# codebase; strip both so the English-only check tests normative prose only.
QUOTED_RE = re.compile(r"(?:「[^「」]*」|«[^«»]*»)")


def _model_facing_docstrings() -> list[tuple[str, str]]:
    """(tool_name, docstring) for every tool registered via `_spec(...)`."""
    src = TOOLS_PATH.read_text()
    tree = ast.parse(src)
    func_docs: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node) or ""
            if len(doc.strip()) >= 40:
                func_docs[node.name] = doc
    out: list[tuple[str, str]] = []
    for m in re.finditer(r'_spec\("([^"]+)",\s*([a-z_]+)', src):
        name, fname = m.group(1), m.group(2)
        if fname in func_docs:
            out.append((name, func_docs[fname]))
    return out


def _unquoted(s: str) -> str:
    """Strip quoted spans (「…」 / "…" / '…') so English-only checks test
    normative prose, not load-bearing quoted examples."""
    return QUOTED_RE.sub("", s)


def test_known_spec_tools_are_covered() -> None:
    docs = dict(_model_facing_docstrings())
    # A representative set must be present so a `_spec` rename or a docstring
    # that drops below the extraction floor turns this file red loudly.
    assert {"send_reply", "weather", "note_create", "media_fetch",
            "version_list", "version_read"} <= set(docs)


@pytest.mark.parametrize(
    ("tool", "doc"),
    _model_facing_docstrings(),
    ids=[t for t, _ in _model_facing_docstrings()],
)
def test_opening_has_a_when_to_call_clause(tool: str, doc: str) -> None:
    # The complete first paragraph, with soft line wraps joined (ADR 0015 §2).
    head_text = _opening(doc)
    assert WHEN_TRIGGERS.search(head_text), (
        f"{tool}: opening has no when-to-call trigger. First sentences: "
        f"{head_text[:120]!r}"
    )


@pytest.mark.parametrize(
    ("tool", "doc"),
    _model_facing_docstrings(),
    ids=[t for t, _ in _model_facing_docstrings()],
)
def test_no_permission_suppression_in_the_opening(tool: str, doc: str) -> None:
    head_text = _opening(doc)
    hit = SUPPRESSION_TOKENS.search(head_text)
    assert hit is None, (
        f"{tool}: opening contains permission-suppression phrasing "
        f"{hit.group(0)!r} — style guidance must come after the opening, "
        "labelled SHOULD, never as a condition on calling"
    )


def test_the_suppression_guard_rejects_a_phrase_late_in_the_opening() -> None:
    doc = "Read a public page. Call this when asked; do not call if the reply is too long."
    assert doc.index("do not call") > 8
    with pytest.raises(AssertionError, match="permission-suppression"):
        test_no_permission_suppression_in_the_opening("synthetic", doc)


def test_the_suppression_guard_reads_wrapped_opening_lines() -> None:
    # Both suppression phrases cross soft wraps, with no paragraph break.
    doc = "Read a public page. Call this when asked; do not\ncall if the reply is too\nlong."
    with pytest.raises(AssertionError, match="permission-suppression"):
        test_no_permission_suppression_in_the_opening("synthetic", doc)


def test_a_style_label_inside_the_opening_does_not_hide_suppression() -> None:
    doc = "Read a public page. Call this when asked. SHOULD: do not call if the reply is too long."
    with pytest.raises(AssertionError, match="permission-suppression"):
        test_no_permission_suppression_in_the_opening("synthetic", doc)


@pytest.mark.parametrize("content", [
    "It knows only people in this conversation, so do not call it for a name already given.",
    "Returns ok or unavailable. Do not call another backend after unavailable.",
    "SHOULD: Do not send a draft that is too long.",
    "MAY: Call again; no need to call when the result is already known.",
], ids=["scope", "contract", "style", "permission"])
def test_the_suppression_guard_accepts_guidance_after_the_opening(content: str) -> None:
    test_no_permission_suppression_in_the_opening(
        "synthetic", f"Read a public page. Call this when asked.\n\n{content}",
    )


@pytest.mark.parametrize(
    ("tool", "doc"),
    _model_facing_docstrings(),
    ids=[t for t, _ in _model_facing_docstrings()],
)
def test_normative_prose_is_english(tool: str, doc: str) -> None:
    prose = _unquoted(doc)
    match = CJK_RE.search(prose)
    assert match is None, (
        f"{tool}: normative prose contains CJK {match.group(0)!r}. Quoted "
        "non-English tokens are allowed only as load-bearing examples "
        "(trigger words, example arguments); instruction prose must be English."
    )


@pytest.mark.parametrize(
    ("tool", "doc"),
    _model_facing_docstrings(),
    ids=[t for t, _ in _model_facing_docstrings()],
)
def test_a_must_is_turn_correctness_not_style(tool: str, doc: str) -> None:
    # A MUST whose object is a style judgement ("too long", "too soft",
    # "polite", "concise") is a SHOULD wearing a MUST costume.
    if not MUST_RE.search(doc):
        return
    style_must = re.search(
        r"MUST NOT be|MUST be (?:short|long|polite|concise|soft|hard|friendly)",
        doc,
        re.IGNORECASE,
    )
    assert style_must is None, (
        f"{tool}: MUST used for a style judgement ({style_must.group(0)!r}) — "
        "MUST is reserved for turn correctness (RFC 2119 register split)"
    )


@pytest.mark.parametrize(
    ("tool", "doc"),
    _model_facing_docstrings(),
    ids=[t for t, _ in _model_facing_docstrings()],
)
def test_style_guidance_is_labelled(tool: str, doc: str) -> None:
    # If the docstring has a second paragraph / bullet body beyond the
    # opening, it must be behind a register label (SHOULD or MAY), not a
    # bare "Read text back..." imperative essay.
    lines = [ln.strip() for ln in doc.strip().splitlines() if ln.strip()]
    if len(lines) <= 2:
        return  # L1 sentence: no style block to label
    # Everything after the opening should be under SHOULD/MAY/Args or status
    # contract lines. Look for a bare imperative paragraph in the middle.
    labels_or_args = 0
    for ln in lines[1:]:
        if ln.startswith(("SHOULD", "MAY", "Args:", "- ", "* ", "`", "**")):
            labels_or_args += 1
    # If the docstring is long but has no labelled register block at all, it
    # is the essay shape.
    if len(lines) > 6 and labels_or_args == 0:
        pytest.fail(
            f"{tool}: {len(lines)} lines but no labelled register block "
            "(SHOULD/MAY). Long descriptions must structure guidance behind "
            "register labels (ADR 0007 §2)."
        )


#: A catalogue line's token ceiling — `tests/unit/test_tool_catalogue.py`'s
#: `LINE_CEILING`, restated here because this is the test that holds the
#: SOURCE of that line to it. The two must agree.
L1_LINE_CEILING = 40


@pytest.mark.parametrize(
    ("tool", "doc"),
    _model_facing_docstrings(),
    ids=[t for t, _ in _model_facing_docstrings()],
)
def test_the_opening_paragraph_is_an_l1(tool: str, doc: str) -> None:
    """Policy 4's L1 — «what + when» — is the docstring's FIRST PARAGRAPH, and
    that paragraph is the tool's catalogue line (`nora.agent.registry.
    _opening`). So it must carry the when-to-call clause ITSELF, not somewhere
    in the next eight lines, and fit a line.

    The trigger-only guard above uses the same boundary; this also checks
    the catalogue line's token ceiling, extracted independently below.
    """
    import tiktoken

    opening = " ".join(doc.strip().split("\n\n")[0].split())
    assert WHEN_TRIGGERS.search(opening), (
        f"{tool}: the first paragraph (its catalogue line) says what the tool "
        f"does but not when to call it: {opening!r}"
    )
    tokens = len(tiktoken.get_encoding("cl100k_base").encode(f"- {tool} — {opening}"))
    assert tokens <= L1_LINE_CEILING, (
        f"{tool}: the first paragraph is {tokens} tokens as a catalogue line "
        f"(ceiling {L1_LINE_CEILING}). Split it: L1 first, the rest after a "
        "blank line."
    )


# ---- ADR 0015 §3 / §6 (DEC-200): level, ceiling, language, punctuation --------
#
# Counted over the description Strands SENDS (`ToolRegistry._descriptions()`:
# the docstring minus `Args:`), not the AST docstring the tests above read:
# that is the text every call pays for.

#: ADR 0015's ceilings, cl100k tokens over the sent description; each moves only
#: with a measurement recorded beside it.
LEVEL_CEILINGS = {1: 80, 2: 160, 3: 320}

#: The tools not yet rewritten to ADR 0015. It only SHRINKS: it must stay a
#: subset of the digest-pinned name snapshot, so a tool added after the ADR can
#: never be exempted here. A rewrite batch removes its tools in the same commit
#: that rewrites them.
#:
#: EMPTY: every rule below covers every tool. The name is kept so the
#: shrink-only test still has something to hold: adding a tool back here is an
#: exemption, and that test refuses any name newer than the snapshot.
_NOT_YET_REWRITTEN: frozenset[str] = frozenset()


#: A run of CJK, kana or hangul. ADR 0015 §3's unit: a distinct maximal run,
#: counted once however often it repeats. Punctuation such as 「」 separates runs.
_NON_LATIN_RUN = re.compile(r"[㐀-䶿一-鿿぀-ヿ가-힯]+")
_DASHES = ("—", "–")  # em dash, en dash


def _sent_descriptions() -> dict[str, str]:
    from nora.agent.registry import ToolRegistry

    return dict(ToolRegistry()._descriptions())


def _sent_specs() -> dict[str, str]:
    """Each tool's WHOLE spec as Strands sends it (description, parameter
    descriptions, schema), as JSON text. The dash and language rules apply to
    all of it: an `Args:` line is model-facing text too, and a check over the
    description alone would let one hide there. Built for a Discord room and a
    Dreamer adaptor so the platform- and Dreamer-scoped tools are included."""
    import json

    from nora.agent.ports import adapt
    from nora.agent.registry import ToolRegistry
    from nora.agent.room import Room
    from nora.obs.metrics import Metrics
    from nora.testing.fakes import FakeMemoryStore

    class _Persons:
        def resolve(self, platform: str, sender_id: str):
            return None

    class _Store:
        async def recent(self, *, conversation_id: str, limit: int):
            return []

    room = Room(platform="discord", conversation_id="g1", roster_source=None,
                persons=_Persons())
    specs = ToolRegistry().specs(
        store=_Store(), conversation_id="g1", conversation_type="group",
        persons=_Persons(), honcho=FakeMemoryStore(), deliverer=None,
        metrics=Metrics(), room=room, dream_adaptor="policy",
    )
    return {
        s.name: json.dumps(adapt(s).tool_spec, ensure_ascii=False) for s in specs
    }


def _rewritten() -> list[str]:
    from nora.agent.tools import NATIVE_TOOLS

    # A plugin tool (`skills`) is built and described by its plugin, not by
    # this repo, so there is no text of ours for these rules to govern.
    return sorted(
        n for n, spec in NATIVE_TOOLS.items()
        if n not in _NOT_YET_REWRITTEN and not spec.plugin
    )


def test_the_allowlist_only_shrinks() -> None:
    from tests.architecture.test_tool_names import _SNAPSHOT_2026_09_24

    extra = sorted(_NOT_YET_REWRITTEN - _SNAPSHOT_2026_09_24)
    assert not extra, (
        f"{extra} added to _NOT_YET_REWRITTEN: a tool added after ADR 0015 "
        "meets it from its first commit and is never exempted"
    )


def test_send_reply_is_held_to_the_policy_like_every_other_tool() -> None:
    """POSITIVE CONTROL for the empty allowlist. With nothing exempt, a bug in
    `_rewritten()` that dropped tools would make every rule below pass on less
    than it claims, so `send_reply` is asserted present. Its description is
    Level 3: delivery (ADR 0007 §5a's MUST) plus its
    `sent`/`held_for_review`/`refused` contract, which stays in the
    description because the tool returns a bare string and has no `note` to
    carry it."""
    from nora.agent.tools import NATIVE_TOOLS

    assert "send_reply" in _rewritten()
    assert NATIVE_TOOLS["send_reply"].level == 3
    sent = " ".join(_sent_descriptions()["send_reply"].split())
    for status in ("`sent`", "`held_for_review`", "`refused`"):
        assert status in sent, status
    assert "You MUST call" in sent


def test_every_rewritten_tool_declares_a_level() -> None:
    from nora.agent.tools import NATIVE_TOOLS

    bad = {n: NATIVE_TOOLS[n].level for n in _rewritten()
           if NATIVE_TOOLS[n].level not in LEVEL_CEILINGS}
    assert not bad, f"declare level=1|2|3 on NativeTool (ADR 0015 §3): {bad}"


def test_every_declared_level_is_on_the_ladder() -> None:
    from nora.agent.tools import NATIVE_TOOLS

    bad = {n: s.level for n, s in NATIVE_TOOLS.items()
           if s.level is not None and s.level not in LEVEL_CEILINGS}
    assert not bad, f"level must be one of {sorted(LEVEL_CEILINGS)}: {bad}"


def test_a_rewritten_description_fits_its_level() -> None:
    import tiktoken

    from nora.agent.tools import NATIVE_TOOLS

    enc = tiktoken.get_encoding("cl100k_base")
    sent = _sent_descriptions()
    over = {}
    for name in _rewritten():
        level = NATIVE_TOOLS[name].level
        if name not in sent or level not in LEVEL_CEILINGS:
            continue
        tokens = len(enc.encode(sent[name]))
        if tokens > LEVEL_CEILINGS[level]:
            over[name] = f"{tokens} > {LEVEL_CEILINGS[level]} (level {level})"
    assert not over, f"over the level ceiling (ADR 0015 §3): {over}"


def test_no_dash_in_a_rewritten_tool_spec() -> None:
    sent = _sent_specs()
    bad = {n: sum(sent[n].count(d) for d in _DASHES)
           for n in _rewritten() if n in sent and any(d in sent[n] for d in _DASHES)}
    assert not bad, (
        f"em or en dashes in the tool spec, description or arguments (ADR 0015 "
        f"§3); use a comma, full stop, colon or parentheses: {bad}"
    )


def test_at_most_one_non_english_string_in_a_rewritten_tool_spec() -> None:
    sent = _sent_specs()
    bad = {}
    for name in _rewritten():
        runs = set(_NON_LATIN_RUN.findall(sent.get(name, "")))
        if len(runs) > 1:
            bad[name] = sorted(runs)
    assert not bad, (
        f"more than one non-English string in the tool spec (ADR 0015 §3: keep "
        f"only the trigger the tool fires on; examples go): {bad}"
    )


_SECTION_HEADERS = (
    "Args:", "Returns:", "Raises:", "Yields:", "Examples:", "Example:", "Note:", "Notes:",
)


@pytest.mark.parametrize(
    ("tool", "doc"),
    _model_facing_docstrings(),
    ids=[t for t, _ in _model_facing_docstrings()],
)
def test_no_text_after_args_is_silently_dropped(tool: str, doc: str) -> None:
    """Strands drops the `Args:` section AND any unindented text after it that
    is not a recognised section header: prose placed below the arguments never
    reaches the model, with no error. Everything she must read goes above
    `Args:`."""
    import inspect

    lines = inspect.cleandoc(doc).splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if ln.strip() == "Args:")
    except StopIteration:
        return
    for ln in lines[start + 1:]:
        if not ln.strip() or ln.startswith((" ", "\t")):
            continue
        assert ln.strip().startswith(_SECTION_HEADERS), (
            f"{tool}: {ln.strip()[:60]!r} sits after Args: and is dropped by "
            "Strands before the model sees it. Move it above Args:."
        )
        break


# ---- ADR 0015 §6: "a Level 3 status has a result `note`" -----------------
#
# 0015 §6 names this check `test_every_listed_status_returns_a_note`.
#
# WHAT THIS CHECKS: every `ast.Return` belonging to a Level 3,
# `output_contract="three_state"` tool's OWN closure in nora/agent/tools.py
# -- not a nested helper's returns (`generate_image`'s `_made_note`/
# `_canonical_self`/`resolve` return bytes, a marker, `str | None`, never the
# tool's own status dict). Each own return's value is classified into one of
# the shapes below:
#
#   (a) a dict literal, `{"status": "X", ..., "note": ...}`. A `status` that
#       is not itself a literal (`remind`'s `{"status": rule.outcome, "note":
#       rule.note}`, built from a rate-limit rule object) cannot be matched
#       against the listed statuses, but must still carry a `note` key whose
#       value is not a literal empty string or `None`; a dynamic value
#       (`rule.note`) that cannot be proven non-empty either way is not
#       blamed for that.
#   (b) `dict(NAME)` / bare `NAME`, where NAME is a module-level (or
#       `build_tool_specs`-body-level, `remind`'s `notes_off`) constant dict.
#   (c) `_with_note(out, NOTES)`, where NOTES is a notes mapping naming what
#       it covers. `out` (its first argument) must also be a verified
#       pass-through (see (e)), so stripping `out`'s `note` before the call
#       is caught.
#   (d) a bare `NAME` whose dict was built with no `note`, later given one by
#       `NAME["note"] = ...` (`remind`'s `result`). The assignment has to be
#       a DIRECT SIBLING of the `return`, in the same block, before it -- not
#       merely present somewhere earlier in the function regardless of which
#       `if` it is nested in. A mutant that moved `remind`'s
#       `result["note"] = ...` inside `if rule is not None:` while
#       `return result` stayed unconditional must fail.
#   (e) a named PASS-THROUGH: a bare `NAME` bound to an opaque external call
#       (`out` from `social_source.read(...)`), or a dict literal spreading
#       one (`instagram_read`'s `{**out, "person": name}`). A bare name is
#       trusted as a pass-through only if EVERY assignment to it in the tool
#       is either an awaited `*.read(...)` call or an already-classifiable
#       note-bearing value (a literal dict with a note, or `dict(CONST)` with
#       one); any other binding -- a comprehension that strips `"note"`, a
#       subscript, a plain string (`instagram_read`'s `read`) -- fails
#       closed, naming the offending binding.
#   (f) `_link_redirect(owner, claim, link_input)` -- `nora.agent.
#       link_owners.redirect`, imported under that name, called directly
#       from `x_read`'s, `linkedin_read`'s and `instagram_read`'s own
#       closures (a claim by another registered owner). Recognised by name
#       rather than followed into its own module: `redirect()`'s CONTRACT is
#       what is trusted, not its current source -- it always returns
#       `{"status": "nothing_found", "note": <composed>}`, and the note is
#       never empty by construction (`LinkClaim.__post_init__` refuses to
#       build an unreadable claim with no `unreadable_template`; the
#       readable template always fills every slot). A literal status
#       ("nothing_found") with a note this test can trust, same as (a).
#
# For (a)/(b)/(d)/(f), whose literal status is one the docstring lists, the
# return's `note` must be present and PROVABLY a non-empty string (a
# `Constant`, an f-string, string concatenation, an `if`/`else` expression,
# or a `{...}.get(key, default)` lookup resolved through local assignments
# and same-tool nested closures -- `generate_image`'s `note = {...}.get(
# reason, "...")` and `note = _made_note("...")`), checked on EVERY such
# return INDEPENDENTLY, never "at least one covers it".
#
# For (c)/(e), whose status is not statically known, the REACHABLE set is a
# per-tool declaration (`_DELEGATE_REACHABLE_STATUSES`, hand-verified
# against each backend's own status vocabulary, e.g. `SocialBridgeClient.
# _read` never answers `refused`), never "whatever some unrelated return
# happens to prove": a status proven by an input-validation return says
# nothing about what the backend can send back. The notes mapping ((c)
# only) covers whatever keys it names; anything left in the reachable set
# must be in `_backend_notes()` -- see its own docstring for exactly what
# that does and does not verify, and for what is still NOT checked (a
# genuine, stated limit of static analysis).
#
# A `return` whose value fits none of the shapes -- or a bare `return` with
# no value, or a helper call this cannot resolve, or a pass-through binding
# this cannot verify -- is a FAILURE naming the shape, not a skip
# (fail-closed): the property this enforces is "every return path has a
# note", so a return this test cannot read is one it cannot vouch for. A
# call to a module-level helper function taking no arguments
# (`_x_egress_refused()`) is followed ONE level -- read as if its own
# return(s) stood in the caller's place -- because that is a pure refactor
# of an existing literal return, not a new shape.
#
# `send_reply` is excluded: its `output_contract` is `None`, ADR 0015 §3's
# own documented exception (it returns a bare string, not a dict, so it has
# no `note` to carry).
#
# WHAT THIS STILL DOES NOT CATCH: whether
# `nora.infra.social.bridge_client.SocialBridgeClient._read` or
# `nora.infra.social.render._search` (the Instagram search `ok` result)
# actually RETURN the note-bearing payload this test trusts them to -- that
# is a claim about a call site in a DIFFERENT module, which this test does
# not read (it only verifies, in `_backend_notes()`, that the named
# constants those call sites are supposed to use still carry a note
# themselves). `bridge_client.py`'s non-`ok` path (both `nothing_found` and
# `unavailable`) is guarded instead by `tests/test_social_bridge_client.py`
# (`test_a_bridge_note_never_reaches_the_model`,
# `test_bridge_unavailable_passes_through` and six sibling tests, all
# asserting `out["note"].strip()`). `render._search` dropping
# `"note": SEARCH_FRAMING` from its OWN return is guarded by
# `tests/unit/test_instagram_search_tool.py`; closing that gap here would
# mean reading a second module's control flow, which this static pass over
# `nora/agent/tools.py` deliberately does not do.


#: Per tool, the statuses its DELEGATE return(s) -- (c)/(e) -- can actually
#: carry, independent of what any OTHER, unrelated return in the same tool
#: proves. Hand-verified against each backend's own vocabulary:
#: `SocialBridgeClient._read` (x/LinkedIn) only ever answers `ok`/
#: `nothing_found`/`unavailable` (its own status check restricts to exactly
#: these three); `refused`, where a docstring lists it, is raised
#: EXCLUSIVELY by the tool's own local egress check, never by this call.
#: `instagram_read`'s router can additionally answer `ambiguous`/`refused`
#: for a handle or link input, not only through the tool's own
#: person-resolution branch. A tool absent here has no delegate return.
_DELEGATE_REACHABLE_STATUSES: dict[str, frozenset[str]] = {
    "send_to": frozenset({"ok", "ambiguous", "refused", "unavailable"}),
    "x_read": frozenset({"ok", "nothing_found", "unavailable"}),
    "x_search": frozenset({"ok", "nothing_found", "unavailable"}),
    "linkedin_read": frozenset({"ok", "nothing_found", "unavailable"}),
    "linkedin_search": frozenset({"ok", "nothing_found", "unavailable"}),
    "instagram_search": frozenset({"ok", "nothing_found", "unavailable"}),
    "instagram_read": frozenset({"nothing_found", "unavailable", "ambiguous", "refused"}),
}


def _level3_three_state_tools() -> list[str]:
    from nora.agent.tools import NATIVE_TOOLS

    return sorted(
        n for n, spec in NATIVE_TOOLS.items()
        if spec.level == 3 and spec.output_contract == "three_state"
    )


def _tool_function_node(name: str) -> ast.AsyncFunctionDef:
    src = TOOLS_PATH.read_text()
    tree = ast.parse(src)
    spec_targets = dict(re.findall(r'_spec\("([^"]+)",\s*([a-z_]+)', src))
    fname = spec_targets[name]
    return next(
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == fname
    )


#: A tool's own hedge on its note contract: "Anything but `X` carries a
#: note" narrows the universal claim to every listed status except `X`.
_EXCEPTION_RE = re.compile(r"Anything but `(\w+)` carries a")


def _required_statuses(doc: str) -> set[str]:
    """The statuses the tool's OWN docstring says its result carries a note
    for. Grounded in the closed status vocabulary (`nora.agent.ports.
    OUTPUT_STATUSES`) rather than a hand-picked word list, so a status added
    to that vocabulary is picked up here too. Most tools claim every listed
    status ("Every result carries a `note`..." / "... with a `note` on how
    to report it."); a tool that narrows its own claim (`instagram_read`) is
    honoured, not overridden, because that hedge IS the tool's contract with
    the model, stated once above `Args:` and paid on every call.
    """
    from nora.agent.ports import OUTPUT_STATUSES

    idx = doc.find("`status`")
    assert idx != -1, f"no `status` contract sentence in: {doc[:200]!r}"
    window = doc[idx:]
    end = window.find("\n\n")
    if end != -1:
        window = window[:end]
    bare_anchor = re.match(r"`status`:\s*", window)
    if bare_anchor:
        # The "Returns `status`: ok, refused or unavailable, with a `note`"
        # shape (`remind`): the words are BARE, not backtick-quoted, so a
        # backtick-only scan would miss them. Scoped to the clause before
        # ", with a `note`" specifically so it cannot swallow prose from the
        # rest of the paragraph the way a plain word scan over the whole
        # window did (it once read `nothing_found`'s "the prompt was
        # `refused`" as a fourth status of `generate_image`, which lists
        # only three besides `ok`).
        clause = window[bare_anchor.end():]
        clause = clause.split(", with a")[0].split(".")[0]
        listed = {w for w in re.findall(r"[a-z_]+", clause) if w in OUTPUT_STATUSES}
    else:
        # The backtick-quoted shapes: "`status` is `ok`, `nothing_found`...
        # or `refused`." and generate_image's bulleted "`status` is one of:
        # \n- `ok`: ...". Only a BACKTICKED word counts as a listed status,
        # so a status word used in ordinary prose ("the prompt was refused")
        # is not read as a fourth status.
        listed = {
            w for w in re.findall(r"`([a-z_]+)`", window) if w in OUTPUT_STATUSES
        }
    assert listed, f"no status words (of {sorted(OUTPUT_STATUSES)}) found in {window!r}"
    excepted = set(_EXCEPTION_RE.findall(doc))
    return listed - excepted


def _scan_dict_constants(container: ast.AST) -> dict[str, ast.Dict]:
    """name -> its `ast.Dict` literal, for every `NAME = {...}`, `NAME: T =
    {...}` or `NAME: T = MappingProxyType({...})` assignment DIRECTLY in
    `container`'s own body (module level, or one function's own top-level
    body -- not recursing into a nested `if`/`try`, matching how these are
    actually declared). Most of this file's shared payload constants carry a
    type annotation, e.g. `_X_NOTES: Mapping[str, str] =
    MappingProxyType({...})`, so both `ast.Assign` and `ast.AnnAssign` must
    be read."""
    out: dict[str, ast.Dict] = {}
    for node in ast.iter_child_nodes(container):
        target: ast.expr | None
        value: ast.expr | None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
        else:
            continue
        if not isinstance(target, ast.Name) or value is None:
            continue
        if (isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
                and value.func.id == "MappingProxyType" and value.args
                and isinstance(value.args[0], ast.Dict)):
            value = value.args[0]
        if isinstance(value, ast.Dict):
            out[target.id] = value
    return out


def _all_dict_constants(tree: ast.Module, functions: dict[str, ast.AST]) -> dict[str, ast.Dict]:
    """Module-level constants PLUS `build_tool_specs`'s OWN body-level ones.
    Every Level 3 tool is a closure nested inside `build_tool_specs`, and a
    few shared payloads are declared once there rather than at module scope
    -- `notes_off` (`remind`'s `dict(notes_off)`), visible to `remind` the
    same way `_X_OFF` is visible at module scope: Python closures see both."""
    out = _scan_dict_constants(tree)
    outer = functions.get("build_tool_specs")
    if outer is not None:
        for name, node in _scan_dict_constants(outer).items():
            out.setdefault(name, node)
    return out


def _module_functions(tree: ast.Module) -> dict[str, ast.AST]:
    """name -> node, for every module-level `def`/`async def` in tools.py --
    the one-level helper-following rule (see the module comment above)."""
    return {
        node.name: node for node in ast.iter_child_nodes(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _local_closures(fn: ast.AST) -> dict[str, ast.AST]:
    """Nested `def`/`async def`s declared directly in `fn`'s OWN top-level
    body (`generate_image`'s `_made_note`, `_canonical_self`, `resolve`) --
    used only to follow a call to one when resolving whether a `note` value
    is provably non-empty (`note = _made_note("...")`)."""
    return {
        node.name: node for node in ast.iter_child_nodes(fn)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _dict_has_note(node: ast.Dict) -> bool:
    return any(isinstance(k, ast.Constant) and k.value == "note" for k in node.keys)


def _dict_has_status_key(node: ast.Dict) -> bool:
    return any(isinstance(k, ast.Constant) and k.value == "status" for k in node.keys)


def _dict_status_literal(node: ast.Dict) -> str | None:
    for key, value in zip(node.keys, node.values, strict=True):
        if (isinstance(key, ast.Constant) and key.value == "status"
                and isinstance(value, ast.Constant) and isinstance(value.value, str)):
            return value.value
    return None


def _dict_is_passthrough(node: ast.Dict) -> bool:
    """A `**expr` spread among the keys (AST marks one with a `None` key) --
    `instagram_read`'s `{**out, "person": name}`: its status is whatever the
    spread carries, never a literal this file can read."""
    return any(k is None for k in node.keys)


def _spread_source(node: ast.Dict) -> ast.expr | None:
    """The `**expr` operand of a passthrough dict (`{**out, "person":
    name}` -> `out`'s `ast.Name`), or `None` if there is none."""
    for k, v in zip(node.keys, node.values, strict=True):
        if k is None:
            return v
    return None


def _notes_mapping_keys(node: ast.Dict) -> frozenset[str] | None:
    """`_X_NOTES`-shaped: keys are status words, used as `_with_note`'s
    second argument, not a single record with one literal `status` field.
    `None` when `node` is not that shape at all (a single-record dict, e.g.
    a mismatched call). Does NOT require every key to be a real status: a
    key that stops being one (a rename, a typo) simply drops out of the
    returned set, which is exactly the regression this exists to catch, not
    a reason to call the whole dict unreadable."""
    from nora.agent.ports import OUTPUT_STATUSES

    if _dict_has_status_key(node):
        return None
    return frozenset(
        k.value for k in node.keys
        if isinstance(k, ast.Constant) and isinstance(k.value, str) and k.value in OUTPUT_STATUSES
    )


def _resolve_nonempty_str(
    expr: ast.expr, assigns: dict[str, ast.expr], depth: int = 0,
    local_funcs: dict[str, ast.AST] | None = None,
) -> bool | None:
    """`True`: provably a non-empty string. `False`: provably empty or not a
    string (`None`). `None`: cannot tell statically -- a caller must treat
    that as "not proven", never as a pass. `local_funcs` (nested closures in
    the SAME tool, from `_local_closures`) lets a note value that calls one
    of them (`generate_image`'s `note = _made_note("...")`) be followed, with
    its parameters substituted by the actual arguments at the call site."""
    if depth > 5:
        return None
    if isinstance(expr, ast.Constant):
        if isinstance(expr.value, str):
            return expr.value != ""
        return False
    if isinstance(expr, ast.JoinedStr):  # an f-string
        return True
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add):
        # String concatenation only ever ADDS length, so one side provably
        # non-empty is enough -- needed for `remind`'s note, "Say back the
        # moment..." + (" Say the limit too..." if rule is not None else
        # ""), whose right side is a conditional this does not evaluate.
        left = _resolve_nonempty_str(expr.left, assigns, depth + 1, local_funcs)
        right = _resolve_nonempty_str(expr.right, assigns, depth + 1, local_funcs)
        if left is True or right is True:
            return True
        if left is False and right is False:
            return False
        return None
    if isinstance(expr, ast.IfExp):
        body = _resolve_nonempty_str(expr.body, assigns, depth + 1, local_funcs)
        orelse = _resolve_nonempty_str(expr.orelse, assigns, depth + 1, local_funcs)
        if body is True and orelse is True:
            return True
        if body is False and orelse is False:
            return False
        return None
    if isinstance(expr, ast.Name):
        rhs = assigns.get(expr.id)
        if rhs is None:
            return None
        return _resolve_nonempty_str(rhs, assigns, depth + 1, local_funcs)
    if (isinstance(expr, ast.Call) and isinstance(expr.func, ast.Attribute)
            and expr.func.attr == "get" and len(expr.args) == 2):
        # `{...}.get(key, default)` -- generate_image's `note = {"quota":
        # "...", ...}.get(reason, "I can't make images right now...")`.
        mapping = expr.func.value
        if isinstance(mapping, ast.Name):
            mapping = assigns.get(mapping.id)
        if isinstance(mapping, ast.Dict) and not _dict_is_passthrough(mapping):
            values = [
                _resolve_nonempty_str(v, assigns, depth + 1, local_funcs) for v in mapping.values
            ]
            default = _resolve_nonempty_str(expr.args[1], assigns, depth + 1, local_funcs)
            if all(v is True for v in values) and default is True:
                return True
            if default is False:
                return False
        return None
    if (isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name)
            and local_funcs and expr.func.id in local_funcs and not expr.keywords):
        # A call to a NESTED closure of the same tool (`generate_image`'s
        # `note = _made_note("The picture is already in this chat...")`):
        # followed with its parameters substituted by the actual arguments,
        # then every one of ITS OWN return values resolved the same way.
        # `True` only if EVERY return path is provably non-empty (we do not
        # know which branch runs); `False` only if every one is provably
        # empty; otherwise unproven.
        target = expr.func.id
        fn = local_funcs[target]
        params = [a.arg for a in fn.args.args]
        if len(params) == len(expr.args) and not fn.args.kwonlyargs:
            subst = {**assigns, **dict(zip(params, expr.args, strict=True))}
            returns = [r for r in _own_returns(fn) if r.value is not None]
            if returns:
                results = [
                    _resolve_nonempty_str(r.value, subst, depth + 1, local_funcs)
                    for r in returns
                ]
                if all(r is True for r in results):
                    return True
                if all(r is False for r in results):
                    return False
        return None
    return None


def _dict_note_status(
    node: ast.Dict, assigns: dict[str, ast.expr], local_funcs: dict[str, ast.AST] | None = None,
) -> bool | None:
    """`False`: no `note` key at all, or one provably empty. `True`:
    provably non-empty. `None`: a `note` key is present but its value cannot
    be resolved either way (e.g. `rule.note`, an attribute of an object this
    file does not trace) -- NOT the same as `False`, and a caller checking a
    dynamic-status dict (which cannot be matched against the listed statuses
    regardless) treats only `False` as a problem."""
    if not _dict_has_note(node):
        return False
    note_expr = next(
        v for k, v in zip(node.keys, node.values, strict=True)
        if isinstance(k, ast.Constant) and k.value == "note"
    )
    return _resolve_nonempty_str(note_expr, assigns, 0, local_funcs)


def _resolve_dict_note_ok(
    node: ast.Dict, assigns: dict[str, ast.expr], local_funcs: dict[str, ast.AST] | None = None,
) -> bool:
    return _dict_note_status(node, assigns, local_funcs) is True


def _own_returns(fn: ast.AST) -> list[ast.Return]:
    """Every `ast.Return` belonging to `fn` ITSELF, never a nested closure's
    (`generate_image`'s `_made_note`/`_canonical_self`/`resolve` return
    unrelated things entirely)."""
    out: list[ast.Return] = []

    def visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Return):
                out.append(child)
                continue
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            visit(child)

    visit(fn)
    return out


def _local_assigns(fn: ast.AST) -> dict[str, ast.expr]:
    """name -> its most recent own-body RHS (a plain `ast.Assign` or
    `ast.AnnAssign` to a bare `Name`, not into a nested closure) -- resolves
    a `note` value that is itself a variable back to the expression that
    built it (`generate_image`'s `note = {...}.get(reason, "...")`,
    `remind`'s `result: dict[str, object] = {...}`)."""
    out: dict[str, ast.expr] = {}

    def visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            if (isinstance(child, ast.Assign) and len(child.targets) == 1
                    and isinstance(child.targets[0], ast.Name)):
                out[child.targets[0].id] = child.value
            elif (isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name)
                    and child.value is not None):
                out[child.target.id] = child.value
            visit(child)

    visit(fn)
    return out


def _all_assigns(fn: ast.AST) -> dict[str, list[ast.expr]]:
    """name -> EVERY own-body RHS ever assigned to it (not just the most
    recent), in source order -- used to verify a pass-through binding (e):
    a name trusted as an opaque backend result must never, ANYWHERE in the
    tool, be bound to something else (a dict comprehension that strips
    `"note"`, a plain string)."""
    out: dict[str, list[ast.expr]] = {}

    def visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            if (isinstance(child, ast.Assign) and len(child.targets) == 1
                    and isinstance(child.targets[0], ast.Name)):
                out.setdefault(child.targets[0].id, []).append(child.value)
            elif (isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name)
                    and child.value is not None):
                out.setdefault(child.target.id, []).append(child.value)
            visit(child)

    visit(fn)
    return out


def _enclosing_blocks(fn: ast.AST) -> dict[int, list[ast.stmt]]:
    """id(stmt) -> the exact statement list `stmt` is a DIRECT member of (the
    function body, an `if`'s body/orelse, a `try`'s body/handler/orelse/
    finalbody, a `for`/`while`'s body/orelse) -- so a `var["note"] = ...`
    assignment can be required to be a SIBLING of the `return` it is meant
    to cover, in the SAME block, not merely present somewhere earlier in the
    function regardless of which conditional branch it sits in."""
    out: dict[int, list[ast.stmt]] = {}

    def walk(body: list[ast.stmt]) -> None:
        for stmt in body:
            out[id(stmt)] = body
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            for field in ("body", "orelse", "finalbody"):
                child = getattr(stmt, field, None)
                if isinstance(child, list) and child:
                    walk(child)
            for h in getattr(stmt, "handlers", None) or []:
                walk(h.body)

    body = getattr(fn, "body", None)
    if isinstance(body, list):
        walk(body)
    return out


def _note_assigned_before(
    var: str, ret: ast.Return, blocks: dict[int, list[ast.stmt]],
) -> list[ast.expr]:
    """Every RHS assigned to `var["note"]` as a DIRECT STATEMENT in the SAME
    block as `ret`, textually before it (`remind`'s `result["note"] =
    (...)`, a sibling of `return result` at the top level of the function
    body). A mutant that nests the assignment inside `if rule is not None:`
    while the `return` stays unconditional is a DIFFERENT block, so it is
    not found here."""
    block = blocks.get(id(ret))
    if block is None:
        return []
    idx = block.index(ret)
    out: list[ast.expr] = []
    for stmt in block[:idx]:
        if isinstance(stmt, ast.Assign):
            for t in stmt.targets:
                if (isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name)
                        and t.value.id == var and isinstance(t.slice, ast.Constant)
                        and t.slice.value == "note"):
                    out.append(stmt.value)
    return out


def _is_backend_read_call(expr: ast.expr) -> bool:
    """`await X.read(...)` -- the one shape a genuine backend call takes in
    these tools (`social_source.read(...)`, `linkedin_source.read(...)`)."""
    if isinstance(expr, ast.Await):
        expr = expr.value
    return (isinstance(expr, ast.Call) and isinstance(expr.func, ast.Attribute)
            and (expr.func.attr == "read" or (
                expr.func.attr == "send" and isinstance(expr.func.value, ast.Name)
                and expr.func.value.id == "relay"
            )))


def _verify_passthrough_binding(
    var: str, *, all_assigns: dict[str, list[ast.expr]], constants: dict[str, ast.Dict],
    assigns: dict[str, ast.expr], local_funcs: dict[str, ast.AST] | None,
) -> str | None:
    """`None`: every assignment to `var` in the tool is either a genuine
    backend call (`await X.read(...)`) or an already-classifiable
    note-bearing value (a literal dict with a note, or `dict(CONST)` with
    one), so trusting `var` as an opaque pass-through (shape (e)) is
    grounded. Otherwise, the reason the binding could not be verified --
    `instagram_read`'s `read` (a plain string, never a backend call), and a
    dict comprehension that strips `"note"` before the return, both land
    here."""
    bindings = all_assigns.get(var)
    if not bindings:
        return f"{var!r} has no local assignment this test can verify (a parameter?)"
    for b in bindings:
        if _is_backend_read_call(b):
            continue
        if (isinstance(b, ast.Dict) and not _dict_is_passthrough(b)
                and _dict_status_literal(b) is not None):
            if _resolve_dict_note_ok(b, assigns, local_funcs):
                continue
            return f"bound to a dict literal with no non-empty note: {ast.unparse(b)[:60]}"
        if (isinstance(b, ast.Call) and isinstance(b.func, ast.Name) and b.func.id == "dict"
                and len(b.args) == 1 and isinstance(b.args[0], ast.Name)):
            const = constants.get(b.args[0].id)
            if (const is not None and _dict_status_literal(const) is not None
                    and _resolve_dict_note_ok(const, assigns, local_funcs)):
                continue
            return f"bound to dict(...) of an unresolved name: {ast.unparse(b)[:60]}"
        return f"bound to an unverified expression: {ast.unparse(b)[:60]}"
    return None


def _backend_notes() -> dict[str, set[str]]:
    """A FIXED, NAMED map: for each of the six social tools, the statuses
    (of the ones in `_DELEGATE_REACHABLE_STATUSES`) whose note this file
    cannot prove locally, because the tool hands an opaque backend result
    through, wrapped by at most a notes mapping that does not cover that
    status. Each named constant below is asserted HERE to carry a `note`
    itself -- that much this test can and does check, and a mutation to one
    of these constants (`results.AMBIGUOUS`/`REFUSED`/`NOTHING_FOUND`/
    `UNAVAILABLE`/`NO_ACCOUNT`/`NO_SUCH_HANDLE`, `instagram_client.
    SEARCH_NOTHING_FOUND`) fails every test that calls this function,
    immediately, on the assert.

    What it does NOT check, and cannot from this file alone: that the backend
    actually RETURNS the named constant for that status at its own call site.
    That is a claim about `nora/infra/social/bridge_client.py` and
    `nora/infra/social/instagram_client.py`'s own control flow, neither of
    which this test reads. `bridge_client.py`'s non-ok path (both
    `nothing_found` and `unavailable`) is guarded by
    `tests/test_social_bridge_client.py` (`test_bridge_unavailable_passes_through`
    and `test_a_bridge_note_never_reaches_the_model`). `render._search`
    dropping `SEARCH_FRAMING` from its own return is guarded by
    `tests/unit/test_instagram_search_tool.py`.
    """
    from nora.infra.social import instagram_client, render, results

    assert results.NOTHING_FOUND.get("note")
    assert results.UNAVAILABLE.get("note")
    assert results.AMBIGUOUS.get("note")
    assert results.REFUSED.get("note")
    assert results.NO_ACCOUNT.get("note")
    assert results.NO_SUCH_HANDLE.get("note")
    assert instagram_client.SEARCH_NOTHING_FOUND.get("note")
    assert render.SEARCH_FRAMING

    # `x_read`/`x_search`/`linkedin_read`/`linkedin_search` all go through
    # `SocialBridgeClient._read`, which answers `nothing_found`/
    # `unavailable` from these two shared constants for every platform it
    # serves; `ok` is covered locally (shape (c): `_X_NOTES` /
    # `_LINKEDIN_READ_NOTES` / `_LINKEDIN_SEARCH_NOTES` via `_with_note`).
    bridge = {"nothing_found", "unavailable"}
    return {
        # Relay.send's dynamic notes are covered by tests/test_relay.py's
        # every-non-ok-note test and its successful-delivery note control.
        "send_to": {"ok", "ambiguous", "refused", "unavailable"},
        "x_read": set(bridge),
        "x_search": set(bridge),
        "linkedin_read": set(bridge),
        "linkedin_search": set(bridge),
        # `instagram_search` hands `social_source.read(...)`'s result
        # through unwrapped (no `_with_note`): `ok` carries `SEARCH_
        # FRAMING` inline in `render._search`, `nothing_
        # found` is its own `SEARCH_NOTHING_FOUND`, `unavailable` is
        # `results.UNAVAILABLE`.
        "instagram_search": {"ok", "nothing_found", "unavailable"},
        # `instagram_read` also hands `out` through bare (`return out` /
        # `return {**out, "person": name}`, no `_with_note`), and the
        # ROUTER can answer `nothing_found` (`NO_ACCOUNT`/`NO_SUCH_
        # HANDLE`), `unavailable`, `ambiguous` or `refused` for a HANDLE or
        # LINK input too, not only through the tool's own person-
        # resolution branch. `ok` is excepted by the tool's own docstring
        # hedge ("Anything but `ok` carries a note"), see the module
        # comment above.
        "instagram_read": {"nothing_found", "unavailable", "ambiguous", "refused"},
    }


def _classify_value(
    value: ast.expr, *, constants: dict[str, ast.Dict], functions: dict[str, ast.AST],
    assigns: dict[str, ast.expr], all_assigns: dict[str, list[ast.expr]],
    ret: ast.Return, blocks: dict[int, list[ast.stmt]],
    local_funcs: dict[str, ast.AST] | None = None, allow_helper: bool = True,
) -> list[tuple]:
    """Classify one `ast.Return`'s value into the shapes (see the
    module comment): `("literal", status, ok)` for (a)/(b)/(d)/(f) -- a known
    status and whether its note is provably non-empty; `("delegate",
    mapping_keys)` for (c)/(e) -- status unknown, `mapping_keys` is what a
    (c)-shaped `_with_note` mapping covers (empty for (e)); `("skip",)` for
    a dict literal whose `status` key is present but not a literal and DOES
    carry a note key that is not provably empty (`remind`'s `rule.
    outcome`); `("bad", why)` for anything else -- fail-closed."""
    # (f) `_link_redirect(owner, claim, link_input)` -- `nora.agent.
    # link_owners.redirect`, recognised by name (see the module comment):
    # its contract is a fixed literal status and a note that is never empty
    # by construction, checked once at `LinkClaim` build time, not per call
    # site here.
    if (isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
            and value.func.id == "_link_redirect"):
        return [("literal", "nothing_found", True)]
    # (a) a dict literal carrying a LITERAL `status` key, whether or not it
    # also spreads something alongside it (`instagram_read`'s
    # `{"status": "ambiguous", **_asked_about(spoken), "note": ...}`: the
    # spread adds a field, the status is still the tool's own literal).
    if isinstance(value, ast.Dict):
        status = _dict_status_literal(value)
        if status is not None:
            return [("literal", status, _resolve_dict_note_ok(value, assigns, local_funcs))]
    # (e) a dict literal with NO literal `status`, spreading one in instead
    # (`instagram_read`'s `{**out, "person": name}`) -- a passthrough.
    if isinstance(value, ast.Dict) and _dict_is_passthrough(value):
        source = _spread_source(value)
        if isinstance(source, ast.Name):
            reason = _verify_passthrough_binding(
                source.id, all_assigns=all_assigns, constants=constants,
                assigns=assigns, local_funcs=local_funcs,
            )
            if reason is not None:
                return [("bad", f"passthrough spread of {source.id!r}, {reason}")]
            return [("delegate", frozenset())]
        return [("bad", f"passthrough spread of a non-name expression: {ast.unparse(value)[:80]}")]
    # A dict literal with neither a literal nor a spread `status`: a
    # DYNAMIC status this file cannot match against the listed statuses
    # either way (`remind`'s `{"status": rule.outcome, "note": rule.note}`),
    # still required to carry a `note` key that is not provably empty.
    if isinstance(value, ast.Dict):
        if _dict_has_status_key(value):
            note_status = _dict_note_status(value, assigns, local_funcs)
            if note_status is False:
                return [(
                    "bad",
                    f"dynamic-status dict has no non-empty note: {ast.unparse(value)[:80]}",
                )]
            return [("skip",)]
        return [("bad", f"dict literal with no `status` key: {ast.unparse(value)[:80]}")]
    # (c) `_with_note(x, MAPPING)`.
    if (isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
            and value.func.id == "_with_note" and len(value.args) == 2):
        out_arg = value.args[0]
        if isinstance(out_arg, ast.Name):
            reason = _verify_passthrough_binding(
                out_arg.id, all_assigns=all_assigns, constants=constants,
                assigns=assigns, local_funcs=local_funcs,
            )
            if reason is not None:
                return [("bad", f"_with_note(...)'s first argument {out_arg.id!r}, {reason}")]
        mapping_arg = value.args[1]
        mapping = constants.get(mapping_arg.id) if isinstance(mapping_arg, ast.Name) else None
        keys = _notes_mapping_keys(mapping) if mapping is not None else None
        if keys is not None:
            return [("delegate", keys)]
        return [("bad", f"_with_note(...) with an unresolved mapping: {ast.unparse(value)[:80]}")]
    # (b) `dict(NAME)`.
    if (isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
            and value.func.id == "dict" and len(value.args) == 1
            and isinstance(value.args[0], ast.Name)):
        const = constants.get(value.args[0].id)
        if const is not None:
            status = _dict_status_literal(const)
            if status is not None:
                return [("literal", status, _resolve_dict_note_ok(const, assigns, local_funcs))]
        return [("bad", f"dict(...) of an unresolved name: {ast.unparse(value)[:80]}")]
    # (b)/(d)/(e) a bare `Name`.
    if isinstance(value, ast.Name):
        const = constants.get(value.id)
        if const is not None:
            status = _dict_status_literal(const)
            if status is not None:
                return [("literal", status, _resolve_dict_note_ok(const, assigns, local_funcs))]
            return [("bad", f"bare name {value.id!r} is a constant with no literal `status`")]
        rhs = assigns.get(value.id)
        if isinstance(rhs, ast.Dict) and not _dict_is_passthrough(rhs):
            status = _dict_status_literal(rhs)
            if status is not None:
                if _dict_has_note(rhs):
                    return [("literal", status, _resolve_dict_note_ok(rhs, assigns, local_funcs))]
                noted = any(
                    _resolve_nonempty_str(n, assigns, 0, local_funcs) is True
                    for n in _note_assigned_before(value.id, ret, blocks)
                )
                return [("literal", status, noted)]  # shape (d)
            if _dict_has_status_key(rhs):
                note_status = _dict_note_status(rhs, assigns, local_funcs)
                if note_status is False:
                    return [(
                        "bad",
                        f"bare name {value.id!r}: dynamic-status dict has no non-empty note",
                    )]
                return [("skip",)]
        # An opaque local: a function parameter, or the result of an
        # `await ...read(...)` call -- shape (e). Every binding must verify.
        reason = _verify_passthrough_binding(
            value.id, all_assigns=all_assigns, constants=constants,
            assigns=assigns, local_funcs=local_funcs,
        )
        if reason is not None:
            return [("bad", f"bare name {value.id!r}, {reason}")]
        return [("delegate", frozenset())]
    # A module-level helper taking no arguments, followed ONE level.
    if (allow_helper and isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
            and value.func.id in functions and not value.args and not value.keywords):
        inner = functions[value.func.id]
        inner_returns = _own_returns(inner)
        if not inner_returns:
            return [("bad", f"helper {value.func.id}() has no return this test can read")]
        inner_blocks = _enclosing_blocks(inner)
        inner_assigns = _local_assigns(inner)
        inner_all_assigns = _all_assigns(inner)
        inner_local_funcs = _local_closures(inner)
        out: list[tuple] = []
        for r in inner_returns:
            if r.value is None:
                return [("bad", f"helper {value.func.id}() has a bare `return`")]
            out.extend(_classify_value(
                r.value, constants=constants, functions=functions,
                assigns=inner_assigns, all_assigns=inner_all_assigns,
                ret=r, blocks=inner_blocks, local_funcs=inner_local_funcs,
                allow_helper=False,
            ))
        return out
    return [("bad", f"unrecognised return shape: {ast.unparse(value)[:80]}")]


#: The write path into her computer's `inbox/`: `computer_import`, for a sent
#: file and a shared file alike, ends in `return await _computer_copy_in(tool,
#: request, take, copied)`, a closure of `build_tool_specs`. Shape (g): such a
#: return is read as the returns it stands for, never trusted: every own
#: return of `_computer_copy_in` except the two that hand back the caller's
#: answers (`<x>.answer`, `copied(...)`); every return of the caller's `take`
#: closure (and of any closure of the tool it hands off to with
#: `return await NAME(...)`), each of which must be `_Stopped(outcome, row,
#: ANSWER)` (ANSWER classified like any return) or `_Inbound(...)` (bytes, no
#: answer); and every return of its `copied` closure. Anything else there is
#: `bad`, fail-closed like the rest.
_COPY_IN = "_computer_copy_in"


def _named_def(root: ast.AST, name: str) -> ast.AST | None:
    return next(
        (n for n in ast.walk(root)
         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name),
        None,
    )


def _copy_in_call(ret: ast.Return) -> ast.Call | None:
    value = ret.value
    if (isinstance(value, ast.Await) and isinstance(value.value, ast.Call)
            and isinstance(value.value.func, ast.Name) and value.value.func.id == _COPY_IN):
        return value.value
    return None


def _expand_copy_in(
    fn: ast.AST, call: ast.Call, tree: ast.Module,
) -> tuple[list[tuple[ast.Return, ast.expr, ast.AST]], list[str]]:
    """Shape (g): the `(return, value, owning function)` triples a
    `_computer_copy_in` return stands for, and the problems found reading
    them."""
    problems: list[str] = []
    out: list[tuple[ast.Return, ast.expr, ast.AST]] = []
    shared = _named_def(tree, _COPY_IN)
    if shared is None or len(call.args) != 4 or not all(
            isinstance(a, ast.Name) for a in call.args[2:]):
        return [], [f"{_COPY_IN}(...) not readable: {ast.unparse(call)[:80]}"]
    params = [a.arg for a in shared.args.args]  # type: ignore[attr-defined]
    copied_param = params[3] if len(params) == 4 else ""
    for r in _own_returns(shared):
        v = r.value
        if isinstance(v, ast.Attribute) and v.attr == "answer":
            continue  # the caller's `_Stopped` answer, read below
        if (isinstance(v, ast.Call) and isinstance(v.func, ast.Name)
                and v.func.id == copied_param):
            continue  # the caller's `copied`, read below
        if v is not None:
            out.append((r, v, shared))
    take_name, copied_name = call.args[2].id, call.args[3].id  # type: ignore[attr-defined]
    take, copied = _named_def(fn, take_name), _named_def(fn, copied_name)
    if take is None or copied is None:
        return [], [f"{_COPY_IN}'s take/copied are not closures of the tool"]
    # `take` may hand off to another closure of the tool (`return await
    # take_shared(ref)`): followed, and held to the same two shapes.
    pending, seen = [take], set()
    while pending:
        step = pending.pop()
        if id(step) in seen:
            continue
        seen.add(id(step))
        for r in _own_returns(step):
            v = r.value
            if (isinstance(v, ast.Await) and isinstance(v.value, ast.Call)
                    and isinstance(v.value.func, ast.Name)):
                inner = _named_def(fn, v.value.func.id)
                if inner is not None:
                    pending.append(inner)
                    continue
            if isinstance(v, ast.Call) and isinstance(v.func, ast.Name):
                if v.func.id == "_Inbound":
                    continue
                if v.func.id == "_Stopped" and len(v.args) == 3:
                    out.append((r, v.args[2], step))
                    continue
            problems.append(f"{take_name}: a return that is neither _Stopped nor _Inbound")
    out += [(r, r.value, copied) for r in _own_returns(copied) if r.value is not None]
    return out, problems


def _check_tool(tool: str, doc: str) -> list[str]:
    """Every problem found for `tool` (empty = every return whose status is
    one this tool's docstring lists carries a note). See the module comment
    above for the shapes and what is and is not verified."""
    src = TOOLS_PATH.read_text()
    tree = ast.parse(src)
    fn = _tool_function_node(tool)
    functions = _module_functions(tree)
    constants = _all_dict_constants(tree, functions)
    required = _required_statuses(doc)
    backend = _backend_notes().get(tool, set())
    reachable = _DELEGATE_REACHABLE_STATUSES.get(tool, frozenset())

    problems: list[str] = []
    delegate_mapping_keys: list[frozenset[str]] = []

    triples: list[tuple[ast.Return, ast.expr, ast.AST]] = []
    for ret in _own_returns(fn):
        if ret.value is None:
            problems.append("a bare `return` with no value")
            continue
        call = _copy_in_call(ret)
        if call is None:
            triples.append((ret, ret.value, fn))
            continue
        expanded, found = _expand_copy_in(fn, call, tree)
        triples += expanded
        problems += found

    for i, (ret, value, owner) in enumerate(triples):
        # Each value is read in its OWN function's scope: a `take` closure's
        # `answer` resolves among `take`'s assignments, not the tool's.
        assigns, all_assigns = _local_assigns(owner), _all_assigns(owner)
        blocks, local_funcs = _enclosing_blocks(owner), _local_closures(owner)
        for item in _classify_value(
            value, constants=constants, functions=functions,
            assigns=assigns, all_assigns=all_assigns, ret=ret, blocks=blocks,
            local_funcs=local_funcs,
        ):
            kind = item[0]
            if kind == "bad":
                problems.append(f"return #{i}: {item[1]}")
            elif kind == "literal":
                _, status, ok = item
                if status in required and not ok:
                    problems.append(f"return #{i}: status {status!r} has no non-empty note")
            elif kind == "delegate":
                _, mapping_keys = item
                delegate_mapping_keys.append(mapping_keys)
            elif kind == "skip":
                continue
            else:  # pragma: no cover - every branch above sets a known kind
                problems.append(f"return #{i}: unhandled classification {item!r}")

    for mapping_keys in delegate_mapping_keys:
        # Checked only against the declared reachable set and the named backend
        # allowlist, never against a status some unrelated, locally-proven return
        # also carries: input-validation returns say nothing about what the
        # backend can send back.
        missing = sorted((required & reachable) - mapping_keys - backend)
        if missing:
            problems.append(
                f"a delegated return can carry {missing} with no covered note "
                f"(not in its own notes mapping, not in "
                f"_backend_notes()[{tool!r}] = {sorted(backend)})"
            )
    return problems


@pytest.mark.parametrize("tool", _level3_three_state_tools())
def test_every_listed_status_returns_a_note(tool: str) -> None:
    """ADR 0015 §6: "a Level 3 status has a result `note`". See the module comment
    above this function for the return shapes this reads, why every return (not
    at least one) must carry a note, and what it cannot see (two backend call
    sites, guarded by other named tests).
    """
    doc = dict(_model_facing_docstrings())[tool]
    problems = _check_tool(tool, doc)
    assert not problems, f"{tool}: " + "; ".join(problems)

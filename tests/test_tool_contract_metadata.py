"""ADR 0008 enforcement: every native tool declares its contract triple.

The ADR (docs/adr/0008-tool-contract-metadata.md, DEC-152) adds three
machine-readable fields to `NativeTool` — what an interrupted call
may have DONE (`side_effect_state`), whether replaying it produces the same
effect (`idempotency`), and whether the harness may replay it (`retry`). This
file is their READER.

**Why a reader is the point.** A field with no reader is a claim about the
system that the system never checks, and it rots in place. ADR 0008 states the
rule as "each field ships with its reader (an enforcement test or a harness
use), or it is not added". These tests are what make the three fields
load-bearing rather than decorative.

**Structural, not just runtime.** Half of what has to be true here is
invisible at runtime: `side_effect_state` DEFAULTS to `"none"`, so a write verb
that forgot to declare and a read that declared honestly are byte-identical
once the dataclass is constructed. The declaration is therefore read off the
SOURCE — the same shape `tests/test_tool_surface_policy.py` uses for ADR 0007
descriptions — and `test_the_structural_reader_sees_the_whole_table` is the
positive control that stops the AST walk from passing by finding nothing.
"""

import ast
from pathlib import Path

import pytest

from nora.agent.tools import (
    IDEMPOTENCY_VALUES,
    NATIVE_TOOLS,
    RETRY_POLICIES,
    SIDE_EFFECT_STATES,
    _spec,
)

TOOLS_PATH = Path(__file__).parent.parent / "nora" / "agent" / "tools.py"

#: The three fields, named once. Every test below derives from this rather than
#: repeating the names, so adding a fourth contract field is one edit here.
CONTRACT_FIELDS = ("side_effect_state", "idempotency", "retry")

#: The fourth (ADR 0009, DEC-153) is `output_contract`, and it is deliberately
#: NOT in `CONTRACT_FIELDS`: the triple above must be declared EXPLICITLY by
#: every entry because their defaults are permissive in the dangerous direction,
#: while `output_contract=None` merely means "not validated" — the honest answer
#: for `now` (a string), `send_reply` (a bool) and `skills` (`plugin=True`).
#: Requiring it explicitly here would make three entries lie to satisfy a test.
#:
#: It IS in the `_spec()` copy check below, and that is the deliberate extension
#: of this file: the copy failure is worse for this field than for the other
#: three, because `nora.agent.ports.adapt` READS it at runtime and a dropped
#: copy switches that tool's validation off in silence. Its own declaration
#: rules live in `tests/test_output_contracts.py`.
COPIED_FIELDS = (*CONTRACT_FIELDS, "output_contract")

#: ADR 0008 §1 lists `not_applied` as a RUNTIME outcome — a call refused before
#: it applied (an R-2/R-4/R-6/R-18 veto). It is not a property a tool can state
#: about itself, so it is not in the declared vocabulary and no entry may use
#: it. Named here because this test asserts its ABSENCE, and an absence test
#: that spells the forbidden value wrong passes for the wrong reason.
NOT_A_DECLARABLE_STATE = "not_applied"


def _declared_kwargs() -> dict[str, dict[str, str]]:
    """{tool name: {contract field: literal value}} read off the SOURCE.

    Only EXPLICIT keywords are collected. A tool relying on the dataclass
    default appears here with that field missing, which is exactly what
    `test_every_tool_declares_all_three_explicitly` fails on — at runtime the
    two are indistinguishable.
    """
    tree = ast.parse(TOOLS_PATH.read_text())
    table = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "NATIVE_TOOLS":
            table = node.value
    assert isinstance(table, ast.Dict), "NATIVE_TOOLS is no longer a dict literal"

    out: dict[str, dict[str, str]] = {}
    for key, value in zip(table.keys, table.values, strict=True):
        assert isinstance(key, ast.Constant), "a NATIVE_TOOLS key is not a literal name"
        assert isinstance(value, ast.Call), f"{key.value} is not a NativeTool(...) call"
        declared: dict[str, str] = {}
        for kw in value.keywords:
            if kw.arg in CONTRACT_FIELDS:
                assert isinstance(kw.value, ast.Constant), (
                    f"{key.value}.{kw.arg} must be a literal, not a computed value"
                )
                declared[kw.arg] = kw.value.value
        out[key.value] = declared
    return out


DECLARED = _declared_kwargs()


def test_the_structural_reader_sees_the_whole_table() -> None:
    """The positive control for every source-read assertion in this file.

    `== 0` is also true of a walk that found nothing (CLAUDE.md's own rule), and
    an AST reader that silently stops matching — the dict renamed, the entries
    moved behind a helper — would turn every check below into a green no-op.
    This pins the reader against the LIVE table it is supposed to be reading.
    """
    assert set(DECLARED) == set(NATIVE_TOOLS)
    assert len(DECLARED) == len(NATIVE_TOOLS) > 0


@pytest.mark.parametrize("tool", sorted(NATIVE_TOOLS))
def test_every_tool_declares_all_three_explicitly(tool: str) -> None:
    """Fail-closed defaults are not a licence to omit the declaration.

    `side_effect_state` defaults to `"none"` — the PERMISSIVE value — because it
    is the honest answer for two thirds of the table. That makes silence
    dangerous in exactly one direction: a write verb added without a
    declaration would inherit "changes nothing outside the turn". Requiring the
    keyword explicitly is what stops that, and it is the same discipline
    `test_every_tool_declares_a_time_axis` applies to `time_shiftable`.
    """
    missing = [f for f in CONTRACT_FIELDS if f not in DECLARED[tool]]
    assert not missing, f"{tool} does not declare: {', '.join(missing)}"


@pytest.mark.parametrize("tool", sorted(NATIVE_TOOLS))
def test_declared_values_are_in_the_closed_vocabularies(tool: str) -> None:
    """The vocabularies are READ from the module, never restated here.

    CLAUDE.md's label rule: one named constant the code declares against, and
    everything else derives from it. A hand-copied list in a test is a second
    definition, and the pair drifts silently the first time a value is added.
    """
    spec = NATIVE_TOOLS[tool]
    assert spec.side_effect_state in SIDE_EFFECT_STATES, spec.side_effect_state
    assert spec.idempotency in IDEMPOTENCY_VALUES, spec.idempotency
    assert spec.retry in RETRY_POLICIES, spec.retry


def test_not_applied_is_not_a_declarable_side_effect_state() -> None:
    """ADR 0008 §1 lists it; the declared vocabulary excludes it on purpose.

    `not_applied` describes a CALL that was refused before it applied — a
    runtime outcome of a veto — not a property of the tool. A tool cannot
    truthfully declare "I fail before applying"; only a particular call can
    have done so. This asserts both halves: the vocabulary does not offer it
    and no entry reaches past the vocabulary to use it anyway.
    """
    assert NOT_A_DECLARABLE_STATE not in SIDE_EFFECT_STATES
    used = {n for n, s in NATIVE_TOOLS.items() if s.side_effect_state == NOT_A_DECLARABLE_STATE}
    assert used == set(), f"declared as a static property: {', '.join(sorted(used))}"
    # Positive control: the same comprehension DOES find the states that exist,
    # so an empty result above means "nobody declared it", never "the field
    # moved and this reads nothing".
    assert {n for n, s in NATIVE_TOOLS.items() if s.side_effect_state == "unknown"}


def test_send_reply_declares_the_r2_latch_policy() -> None:
    """The ADR's first pinned example, and the one the harness already lives by.

    R-2's latch counts ATTEMPTS, not successes, and v2 has no auto-retry — so a
    `send_reply` that times out may already have put messages in the room
    (`nora.engagement.pipeline`: "Messages may ALREADY have gone out — under
    multi-message that is the common shape"). Pinned by value because the ADR
    names the values.
    """
    spec = NATIVE_TOOLS["send_reply"]
    assert spec.side_effect_state == "unknown"
    assert spec.idempotency == "non_idempotent"
    assert spec.retry == "never"


def test_media_fetch_declares_the_content_addressed_cache_exemption() -> None:
    """The ADR's second pinned example — the `read_only=True` "arguably a lie".

    `media_fetch` writes a `media_descriptions` row and is still declared
    `side_effect_state="none"`, because that write is a content-addressed cache
    of a pure function of the bytes: replaying changes no answer and discloses
    nothing new. It is the ONE place a retry after an `unknown` outcome is
    free, which is why it is also the only `retry="safe"` write-adjacent tool.
    The exemption stays exactly as narrow as `NativeTool.read_only`'s own note
    states it.
    """
    spec = NATIVE_TOOLS["media_fetch"]
    assert spec.read_only is True
    assert spec.side_effect_state == "none"
    assert spec.idempotency == "idempotent"
    assert spec.retry == "safe"


def test_a_read_only_tool_never_declares_a_side_effect() -> None:
    """ADR 0008 §1: `read_only=True` MUST declare `side_effect_state="none"`.

    Derived from the table's own `read_only` flag rather than from a copied
    list of names — the failure `NativeTool`'s docstring says this table exists
    to prevent is a tool added to one list and forgotten in the other.
    """
    reads = {n for n, s in NATIVE_TOOLS.items() if s.read_only}
    assert reads, "no read-only tools found — the flag moved"
    offenders = {n for n in reads if NATIVE_TOOLS[n].side_effect_state != "none"}
    assert offenders == set(), f"read-only but declares a side effect: {sorted(offenders)}"


def test_no_act_verb_claims_it_changes_nothing() -> None:
    """The other direction, and the one that actually protects the harness.

    An act verb declaring `none` would tell a future retry policy that an
    interrupted `send_to` wrote nothing — the precise wrong answer ADR 0008 §1
    exists to forbid. `skills` is excluded for the reason
    `test_every_registered_tool_declares_whether_it_only_reads` excludes it:
    `plugin=True` means this registry does not build it, so there is no body
    here to classify; it takes the fail-closed `unknown` instead.
    """
    acts = {n for n, s in NATIVE_TOOLS.items() if not s.read_only and not s.plugin}
    assert acts, "no act verbs found — the flags moved"
    offenders = {n for n in acts if NATIVE_TOOLS[n].side_effect_state == "none"}
    assert offenders == set(), f"a write verb claims it changes nothing: {sorted(offenders)}"


def test_retry_is_only_safe_where_replaying_is_free() -> None:
    """ADR 0008 §2: `retry` is a harness POLICY, `idempotency` a tool PROPERTY.

    They are separate axes, and the ADR only permits one crossing between them:
    a retry after an `unknown` outcome is safe exactly where replaying with the
    same arguments produces the same effect. `safe` on a `non_idempotent` tool
    is the ADR's failure case written down — a second delivery, a second note,
    a second reminder.
    """
    offenders = {
        n for n, s in NATIVE_TOOLS.items()
        if s.retry == "safe" and s.idempotency != "idempotent"
    }
    assert offenders == set(), f"retry=safe on a non-idempotent tool: {sorted(offenders)}"
    assert {n for n, s in NATIVE_TOOLS.items() if s.retry == "safe"}, "nothing is retryable"


def test_unsafe_without_key_names_its_key() -> None:
    """Nothing declares it today; the check exists so the first one cannot skip it.

    ADR 0008 §2: a tool declaring `retry="unsafe_without_key"` must name the
    idempotency key in its schema — the model has to be able to pass it, or the
    policy is a word with no mechanism. Written as a check on the declaration
    rather than left to a future review, because "the field exists so a future
    write tool cannot ship with an implicit retry policy" is the ADR's stated
    reason for having the value at all.
    """
    keyed = {n for n, s in NATIVE_TOOLS.items() if s.retry == "unsafe_without_key"}
    for name in sorted(keyed):
        doc = _spec(name, lambda: None).call.__doc__ or ""
        assert "idempotency key" in doc.lower() or "idempotency_key" in doc, (
            f"{name} declares retry=unsafe_without_key but names no key in its schema"
        )
    # The vocabulary offers it even though the loop above is empty today —
    # asserted so a value that quietly disappears fails here rather than making
    # this test vacuous forever.
    assert "unsafe_without_key" in RETRY_POLICIES


@pytest.mark.parametrize("tool", sorted(NATIVE_TOOLS))
def test_spec_carries_the_declaration_rather_than_the_default(tool: str) -> None:
    """`_spec()` is the plumbing, and copying it by hand is how a field is lost.

    `_spec`'s own docstring says why it exists: nine hand-written constructor
    calls is how `terminal` and `time_shiftable` would have been forgotten. The
    contract triple has the worse version of that failure — the defaults are
    `none`/`non_idempotent`/`never`, so a dropped copy turns every write verb
    on the table into a tool that declares it wrote nothing.

    `output_contract` is in `COPIED_FIELDS` and has the worse version again: it
    is the one field on the table with a RUNTIME reader, so a dropped copy does
    not produce a wrong declaration some later test can catch — it turns that
    tool's result validation off, and nothing downstream looks any different.
    """
    async def _call() -> dict:
        return {}

    declared = NATIVE_TOOLS[tool]
    spec = _spec(tool, _call)
    for field in COPIED_FIELDS:
        assert getattr(spec, field) == getattr(declared, field), field

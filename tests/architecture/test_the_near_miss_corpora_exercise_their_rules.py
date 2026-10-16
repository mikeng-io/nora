"""A near-miss corpus must break when the rule it is about is deleted.

`no_trigger.yaml` and `near_miss.yaml` assert that trigger-SHAPED text does not
engage her. Each case is named for the `nora/tag.py` rule it exercises. But a
case passes by producing no reply, and a case whose input never reaches the rule
also produces no reply — so the corpus can be entirely inert and read 16/16.

MEASURED 2026-09-18, by deleting each rule and re-scoring: EIGHT of the sixteen
passed with both rules gone. Four of those — `norax`, `nora1`, `` `nora` ``,
`Sonora` — were near-misses of the PLAIN trigger while the corpus was configured
with `nora.000`, so `is_tagged`'s scanner found nothing and never reached the
rule the case is named for. They were four copies of `plain_no_mention`.

`near_miss.yaml` had already been templated with `{trigger}` for exactly this
reason (an operator re-enabled the plain trigger in 2026-08 and two cases
silently stopped being near-misses); `no_trigger.yaml` had not. The fix stopped
at a file boundary, which is why this test reads both.

The trigger is the DEPLOYMENT'S (`engagement.trigger.text`, `nora`), rendered
here the way `evals.backbone._drive` renders it against the composed subject —
a corpus can no longer state one of its own.

Costs milliseconds and needs no model: it is the cheapest real check in the
suite.
"""
from __future__ import annotations

import pathlib

import pytest

from evals.backbone import substitute_trigger
from evals.scenarios import load_all

ROOT = pathlib.Path(__file__).resolve().parents[2]
_SRC = (ROOT / "nora" / "tag.py").read_text(encoding="utf-8")
#: The deployment's trigger text — `nora/config.py`'s own default, which is
#: what the deployment runs (its `[engagement.trigger] text = "nora"`).
_TRIGGER = "nora"

#: `(label, exact source text, replacement)` — the two rules these corpora are
#: about. Exact text, asserted present: a variant built from a string that does
#: not match is the ORIGINAL function, and "nothing broke" would then mean
#: "nothing ran".
_RULES = [
    (
        "word-boundary (B.2 rule 3)",
        "        if _WORD.match(before) or _WORD.match(after):\n"
        "            continue                       "
        "# a word character on either side is not a tag\n",
        "",
    ),
    (
        "code-span strip",
        "    text = strip_code_spans(normalize(body))",
        "    text = normalize(body)",
    ),
]


#: Cases that are about a property NO deletable rule governs, with the reason.
#:
#: Declared rather than allowed: the whole point of this test is that "passes
#: for no reason" and "passes for its own reason" look identical, so an
#: exemption has to state which one it is.
_NOT_ABOUT_A_RULE = {
    # THE CONTROL. A line with no trigger in it at all. It must produce no
    # reply, and it is inert under every rule by construction — that IS the
    # case. Without it the corpus could not tell "the rules work" from "the
    # trigger never appears".
    "plain_no_mention": "the control: no trigger in the line at all",
    # A trigger broken by whitespace is not the trigger. Nothing STRIPS
    # whitespace, so no rule can be deleted to make `n ora.520` match — the
    # property is the literal search itself, which is `finditer`, not a guard
    # around it.
    "space_inside_token_mid_word": "a split token is not the token; no rule to delete",
    "near_miss_broken_spacing": "a split token is not the token; no rule to delete",
    # A MISSPELLING of the trigger is not the trigger — `nora_520` and
    # `nora520` against a configured `nora.000`. Same shape as the split token:
    # the literal search finds nothing, and there is no guard to delete.
    #
    # These two are also the cases that CANNOT be templated: "the trigger with
    # its dot removed" is not expressible against an arbitrary trigger, and
    # substituting anyway produced `nora.000_520` and `nora.000520`, which are
    # near-misses of nothing. They go stale if the operator changes the
    # trigger, and that cost is stated in the corpus rather than hidden behind
    # a substitution that does not mean anything.
    "not_the_trigger_underscore_variant": "a misspelling of the trigger; no rule to delete",
    "missing_dot_typo": "a misspelling of the trigger; no rule to delete",
}


def _variant(old: str, new: str):
    assert old in _SRC, (
        f"nora/tag.py no longer contains the rule text this test deletes:\n{old!r}\n"
        "Update it — a variant built from a stale string is the original "
        "function, and this test would then prove nothing while passing."
    )
    namespace: dict = {}
    exec(compile(_SRC.replace(old, new), "tag_variant", "exec"), namespace)  # noqa: S102
    return namespace["is_tagged"]


@pytest.mark.parametrize("corpus", ["no_trigger.yaml", "near_miss.yaml"])
def test_no_case_passes_with_both_matching_rules_deleted(corpus: str) -> None:
    """Every case must be broken by at least ONE rule's removal.

    A case broken by neither is not testing `nora/tag.py` at all — whatever its
    name says.
    """
    cases = load_all(corpus.removesuffix(".yaml"))
    inputs = {c.name: substitute_trigger(c.input, _TRIGGER) for c in cases}

    reached: set[str] = set()
    for _label, old, new in _RULES:
        without = _variant(old, new)
        reached |= {name for name, text in inputs.items() if without(text, _TRIGGER)}

    inert = [
        c.name for c in cases
        if c.name not in reached and c.name not in _NOT_ABOUT_A_RULE
    ]
    assert not inert, (
        f"{corpus}: {len(inert)} case(s) pass with BOTH matching rules deleted, "
        f"so they assert nothing about `nora/tag.py`: {inert}. A near-miss that "
        "does not contain the configured trigger is not a near-miss."
    )


@pytest.mark.parametrize("corpus", ["no_trigger.yaml", "near_miss.yaml"])
def test_every_case_is_a_near_miss_under_the_shipped_rules(corpus: str) -> None:
    """The control for the test above: with the rules INTACT, nothing tags."""
    from nora.tag import is_tagged

    cases = load_all(corpus.removesuffix(".yaml"))
    tagged = [
        c.name for c in cases if is_tagged(substitute_trigger(c.input, _TRIGGER), _TRIGGER)
    ]
    assert not tagged, f"{corpus}: these DO engage her: {tagged}"

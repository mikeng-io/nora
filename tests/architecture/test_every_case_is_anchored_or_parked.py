"""Every corpus case is a real moment, a parked one, or a visibly unanchored one.

`design/evaluation/11-the-sandbox-rebuild.md` §7.1: a case runs a COMPLETE
Nora rebuilt from production data, or it does not run. There is no third
assembly. This pins that at the corpus surface, where it decays first:

- the loader refuses every key that used to smuggle an assembly in (`knows`,
  `config`, `policy`, `room`, `now`, a `transcript_limit` or a stated
  `culture_key`/`unit` inside `snapshot:`) — so a case cannot say what she
  knows except by naming where and when;
- every case in every corpus is anchored, parked with a reason, or counted as
  unanchored — and the unanchored set is NAMED here, so it can only shrink by
  a change to this file. A case quietly added without an anchor is a
  failing test, not a silent ERROR in a report nobody reads;
- an anchored case names a room the deployment's own policy knows, and a
  moment inside the window the clone can hold.

Replaces `test_corpora_are_rehomed_not_rewritten.py`, whose premise — the
150 cases carry their authored content unchanged across the port — ended
when the content stopped being authored.
"""
from __future__ import annotations

import pathlib

import pytest
import yaml

from evals.scenarios import CASE_KEYS, CORPUS_ENTRY, SNAPSHOT_KEYS, census, load_all

ROOT = pathlib.Path(__file__).resolve().parents[2]
CORPORA = sorted(CORPUS_ENTRY)

#: The rooms the deployment's `policy.toml` names, by platform. The ids are
#: already in the corpora; this is the closed set a `snapshot:` may name.
#: Discord and WhatsApp rooms are listed because a gate/dreamer case may
#: anchor there; a `mention: true` case may not (the adapters are off in the
#: sandbox), and that is pinned below.
KNOWN_ROOMS = {
    ("signal", "MuM4gROdh0/9GIRlOa9ckXgykZMkkNmZquM4gROdh08="),
    ("signal", "Lf2L9zqeNUahRsOz6BA+sTB8aWfbuvqu9vOhpvmJi34="),
    ("signal", "x5hcBMQITtdUfSC0o9AHsqJKU7ywYMrOG5hcBMQITtc="),
    ("discord", "100000000000000201/100000000000000202"),
    ("discord", "100000000000000203/100000000000000204"),
    ("whatsapp", "175356896463213715@g.us"),
}
#: The clone's history begins 2026-08-05 (`messages` from the cutover).
FIRST_ROW_MS = 1_785_928_686_349

#: The cases that are UNANCHORED on purpose: the tool-result seam
#: (`11-the-sandbox-rebuild.md` §7.2) does not exist yet, and P-B was
#: rejected as a parking reason — they load, run, and ERROR until it does.
#: Adding a name here is a decision; removing one is the seam landing.
UNANCHORED_BY_DESIGN = {
    "being_contradicted": {"real_evidence_may_overturn"},
    "identity_grounding": {
        "fabrication_invented_time_window",
        "grounded_correct_top_when_alias_stated",
        "grounded_refusal_when_stat_is_missing",
        "identity_fabrication_ambiguous_deaths_english_rank2",
        "identity_fabrication_ambiguous_deaths_rank2",
        "identity_fabrication_ambiguous_playtime_rank2",
        "identity_fabrication_deaths_rank4",
        "identity_grounded_when_alias_is_known_rank2",
    },
    "source_scope": {
        "scoped_source_silence_is_not_a_denial",
        "scoped_source_silence_stale_window_english",
        "source_that_actually_denies_may_overturn",
        "source_that_covers_the_question_is_answered_plainly",
    },
}


def _raw(name: str) -> list[dict]:
    doc = yaml.safe_load((ROOT / "evals" / "corpora" / f"{name}.yaml").read_text(encoding="utf-8"))
    return list(doc.get("cases") if isinstance(doc, dict) else doc)


@pytest.mark.parametrize("name", CORPORA)
def test_every_case_loads_under_the_closed_key_set(name: str) -> None:
    load_all(name)
    for row in _raw(name):
        assert set(row) <= CASE_KEYS, (name, row["name"], sorted(set(row) - CASE_KEYS))
        if row.get("snapshot"):
            assert set(row["snapshot"]) == SNAPSHOT_KEYS, (name, row["name"])


@pytest.mark.parametrize("name", CORPORA)
def test_every_case_is_anchored_parked_or_named_as_unanchored(name: str) -> None:
    cases = load_all(name)
    unanchored = {
        c.name for c in cases
        if not c.metadata.get("snapshot") and not c.metadata.get("parked")
    }
    assert unanchored == UNANCHORED_BY_DESIGN.get(name, set()), (
        f"{name}: unanchored cases differ from the named set. Anchor them, park "
        f"them with a reason, or add them to UNANCHORED_BY_DESIGN with one: "
        f"{sorted(unanchored ^ UNANCHORED_BY_DESIGN.get(name, set()))}"
    )
    c = census(name)
    assert c.total == len(cases)
    assert c.unanchored == len(unanchored)


@pytest.mark.parametrize("name", CORPORA)
def test_every_anchor_names_a_known_room_inside_the_clones_history(name: str) -> None:
    for case in load_all(name):
        shot = case.metadata.get("snapshot")
        if not shot:
            continue
        room = (shot["platform"], shot["conversation_id"])
        assert room in KNOWN_ROOMS, (name, case.name, room)
        assert int(shot["before_ms"]) >= FIRST_ROW_MS, (name, case.name, shot["before_ms"])


@pytest.mark.parametrize("name", CORPORA)
def test_a_mention_case_anchors_where_a_mention_can_fire(name: str) -> None:
    """Discord's and WhatsApp's adapters are off in the sandbox, so the
    registry answers to no id there and `deliver(mention=True)` refuses."""
    for case in load_all(name):
        shot = case.metadata.get("snapshot")
        if case.metadata.get("mention") and shot:
            assert shot["platform"] == "signal", (name, case.name, shot["platform"])


def test_the_whole_corpus_is_accounted_for() -> None:
    """150 cases, every one in exactly one state. The number is the corpus
    as ported on 2026-09-20; a change to it is a change to what the suite
    claims to measure and belongs in a commit that says so."""
    totals = [census(n) for n in CORPORA]
    assert sum(c.total for c in totals) == 150
    assert sum(c.active + c.parked for c in totals) == 150

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
    ("whatsapp", "165393256957216018@g.us"),
}
#: The clone's history begins 2026-08-05 (`messages` from the cutover).
FIRST_ROW_MS = 1_785_928_686_349

#: The cases that are UNANCHORED on purpose: the tool-result seam
#: (`11-the-sandbox-rebuild.md` §7.2) does not exist yet, and P-B was
#: rejected as a parking reason — they load, run, and ERROR until it does.
#: Adding a name here is a decision; removing one is the seam landing.
UNANCHORED_BY_DESIGN = {
    # Blocked on S2b-2's <computer> block (design/sandbox-runtime §15), a seam
    # this work owes: a correct `computer_read` turn needs to know the file
    # exists, and an anchor carries no workspace state.
    "tool_selection": {"route_computer_read_named_file"},
    "being_contradicted": {"real_evidence_may_overturn"},
    # Blocked on the DISCORD ADAPTER divergence (§1.2), which is also a seam
    # this rebuild owes: their premise exists only in a Discord room, and a
    # turn cannot run where no adapter is wired. Parking them was proposed
    # and rejected for §7.1's stated reason — a case blocked on our own
    # unfinished work is unfinished, not parked, and parking it would retire
    # it to make the rebuild look complete.
    "participation": {
        "other-service-command", "admin-joke-known", "admin-joke-unknown",
    },
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


@pytest.mark.parametrize("name", CORPORA)
def test_a_case_that_speaks_anchors_where_an_adapter_is_wired(name: str) -> None:
    """MEASURED LIVE 2026-09-21: three `participation` cases and two
    `proactive_act` cases anchored in Discord rooms and came back
    `failed_error` with a `KeyError` out of `fire.py:1615`.

    `FirePath._turn` resolves the room's own adapter before she says anything
    — speaking into a room on a platform she is not on is worse than staying
    quiet — and the sandbox composes no Discord adapter (§1.2's declared
    divergence). So a turn case anchored there cannot run, whatever the model
    would have said. `gate` and `dreamer` read and write without speaking and
    are unaffected, which is why the rule reads `drives_a_turn` rather than a
    list of corpora.
    """
    from evals.rebuilt.entry import ENTRY_POINTS

    if not ENTRY_POINTS[CORPUS_ENTRY[name]].drives_a_turn:
        return
    for case in load_all(name):
        shot = case.metadata.get("snapshot")
        if shot:
            assert shot["platform"] == "signal", (
                f"{name}:{case.name} drives a turn in a {shot['platform']} room, "
                "and the sandbox wires no adapter for it"
            )


def test_the_whole_corpus_is_accounted_for() -> None:
    """274 cases, every one in exactly one state. The number is the corpus
    as ported on 2026-09-20 (150), plus `tool_selection`'s 16 S2 routing cases
    (2026-09-23, design/tool-catalogue §2c), plus its 4 per-network social
    routing cases (2026-09-24, design/instagram-adoption/18, P5) and P11's 4
    (`instagram_search` twice, its `instagram_read` boundary pair, an X
    search) and P10/P12's 4 (a reel link, a profile link and a typed handle
    to `instagram_read`, a name to find to `instagram_search`) and the 4
    shared-item cases (a shared reel in English and Cantonese, a shared post,
    and the reel with its pre-fix `[image …]` rendering; 2026-09-24,
    design/instagram-adoption/13 «the cover says it is one»), plus
    `standing`'s 15 fold cases (10 anchored, 1 counterfactual with its prior
    declared absent, 4 parked — issue #445; design/standing-compact/
    20-verification §1), plus
    `presence`'s 7 (2026-09-24, design/self-awareness/05), plus
    `tool_reporting`'s 8 (2026-09-24, ADR 0015 §4), plus `tool_selection`'s 3
    room-tool rows (2026-09-24, design/self-awareness/07), plus its 3
    image-generation argument cases (2026-09-24: her reference on «draw
    yourself», none on a picture without her, the date written out on a
    poster), plus DEC-201's X handle case (`route_x_read_handle`; the X link
    and search cases were renamed, not added) and its 3 LinkedIn cases (a
    profile link, a company link, a name to find), plus `tool_selection`'s
    `route_generate_image_self_again_loads_the_skill_first` (2026-09-25:
    guards the post-fix behaviour on the closest faithful anchor for the
    «draw yourself again» skill-skip failure; it does not reproduce the live
    condition, since standing replayed from production cannot carry it,
    ADR 0015/0016), plus `appearance`'s 2 (2026-09-25, identity.md v12/v13:
    what she says about her own form, asked and unasked), plus
    `tool_selection`'s 2 link-owner-registry cases (2026-09-25,
    design/social-per-network/30-link-owner-registry.md "Migration" PR 2: a
    pasted X post link and a LinkedIn profile link, each "what does this
    say?", expecting the network's own read tool with the link — the same
    property P10/P12 pins for Instagram, over the two networks this PR
    registers), plus its 1 PR-3 case
    (`route_x_read_link_owner_registry_wrong_owner_instagram_first`,
    2026-09-25, "Migration" PR 3: an X link introduced as "this insta post",
    grading a recovery through the registry's cross-network redirect rather
    than a dead end — `instagram_read`, not `linkedin_read`, is the wrong
    owner this case primes, because `linkedin_read` already named `x_read`
    for an X link before this PR (DEC-201) and so would not tell the two
    arms apart; no `before:` ordering, since the redirect path is an
    `instagram_read` call the case must PERMIT, not forbid), plus
    `proactive_decide`'s 3 (2026-09-25, issue #158: d01, d02 and d03
    each gain a `d0N_already_booked_*` guard case at their old
    07:00 moment — a reminder already pending on the exact note there, so
    the correct answer is silence about THAT note, not a second row on it
    (`expect: none` with `rows: [{note: ..., absent: true}]`, reviewed twice:
    the first re-anchor of d01 and d02 was itself found wrong and redone —
    d01 moves from active to parked alongside d03, since no instant is both
    the objective's "right day" and free of an already-pending reminder on
    this note; d02 re-anchors to a genuinely clean instant before ANY
    decision, pending or resolved, about its note), plus
    `note_candidates`' 2 body-completeness twins of p1 and p3 (2026-09-25,
    design/dreamer-agent-loop/42-note-format.md S0, carried over from closed
    #513: graded on what the written body names, not only on whether a note
    exists), plus its parked sibling-title update case (2026-09-25, design
    42 §10.1: the one real moment predates #228's title key), plus
    `standing`'s 5 open-plan `carry` cases (2026-09-26,
    design/standing-compact/40-open-plans.md §6: the five production folds
    design/group-situation/10 confirmed dropped an open plan), less the
    dated meet-up case removed when design 40 was withdrawn (2026-09-26: a
    dated plan is the note tier's to hold), plus `note_candidates`' p10, the
    two-dates-in-one-batch production defect graded by `date_split`
    (2026-09-26, design 40 §7), plus its p11, an update among same-day
    sibling arrangements (2026-09-26, design 42 §10.1: the wrong-note case
    p5/p6 could not be, their siblings being one arrangement), plus
    `tool_selection`'s 3 `routine_read` cases and its first `note_create`
    case and `tool_reporting`'s `routine_read` case (2026-09-28,
    design/proposed/27-presence-analytics.md §4.3: a question about later is
    neither where_is nor where_been; note_create's trimmed opening had no
    routing case at all), plus `tool_selection`'s 2 arrival/departure cases
    (2026-09-28, design/27 §4.4 M1), plus `tool_selection`'s 3
    `record_query` cases (2026-09-29, design/cognition-loop/15-map.md §4: a
    past day, who mentioned a thing, an English interval, each naming the
    neighbour it could lose to), plus the new `tool_selection_cognition`
    corpus's 13 (2026-09-29, migration step 3a-ii: the Cognition catalogue's
    own confusable pairs — note_create/update, event_propose/revise/
    set_status, wake_at/move/cancel, remind/move/cancel, close, and
    record_query/rollup_read one layer over), plus `tool_selection`'s 6
    computer cases (2026-09-30, design/sandbox-runtime §4, DEC-243: two
    `computer_run` data tasks against `calculate` (a regression with a
    p-value, a CSV table), three negatives where maths, a web page and a
    picture must NOT reach `computer_run`, and a named-file `computer_read`
    left UNANCHORED until S2b-2's <computer> block; the positives measure the
    `[computer] enabled` arm only), plus `tool_reporting`'s
    `report_computer_run_in_own_words` (2026-09-30, S2b-2: after a run she
    answers in her own words, not the bare stdout; computer-on arm only), plus
    `tool_selection_cognition`'s 5
    (2026-09-30, PR B «what she can read»: `rollup_read` against `record_query`
    twice and against `where_been` past its 90 days, and `standing_read` read
    before `standing_revise` and against `note_read`/`note_search`), plus
    `tool_selection`'s 2 S3a cases (2026-10-01, design/sandbox-runtime §7: a
    simulation that is `computer_run`'s, never `calculate`'s (it first also
    required a computer-use skill load; measured 1/5 and dropped as a wrong
    expectation, operator 2026-10-01), and a `computer_import` of a sent file
    against `media_fetch`; computer-on arm only), plus 5 `tool_selection` cases
    for `document_publish` (design/artifact §26: two positives on the documents-on
    arm, three negatives: a count, a picture, a CSV file handed over with
    `computer_export`), plus 2 more for documents (2026-10-01, the skill's PR: a
    short question that stays a chat answer, and `document_read` before any
    `document_publish` when changing a document; the second is an ORDER
    criterion on an id no sandbox holds, so it measures reading first), plus 3
    `tool_selection` cases for `media_search`'s room scope (DEC-138 amended
    2026-10-01: the photos from last week's dinner in English and Cantonese, and
    a photo in view that stays on `media_fetch`), plus 1 `tool_selection` case
    for `<documents>` (2026-10-01, design/artifact §29: changing a document whose id
    is in the block and nowhere else, seeded into the room with `metadata.documents`;
    documents-on arm only), plus 1 for `document_set_status` (a delete whose id is
    in `<documents>` only, seeded the same way, against `document_publish`;
    documents-on arm only), plus 1 negative for it (an edit that says «remove»
    must not reach `document_set_status`; documents-on arm only), plus 5 `tool_selection`
    cases for the sub-agent tools (2026-10-04,
    design/subagent §3, DEC-283: two positives on the sub-agent-on arm, a conversion of eight
    files and research then writing, and three negatives that are valid on both arms: a sum, one
    document, a short question), less the 40 cases of the
    `note_candidates` and `proactive_decide` corpora (2026-10-03, DEC-277: the Dreamer passes they
    measured are deleted with their adaptors, the cognition session owns
    notes and reminders now), plus 2 `tool_selection` cases for `computer_browse`
    (2026-10-07, design/browser-runtime B2: a search box to operate and a page
    to click through; browse-on arm only), plus the 5 `lessons` cases
    (2026-10-09, design/self-growth/40 §8: 4 lessons counterfactuals and a
    control, the `<lessons>` block's before/after), plus 8 memo routing cases
    (2026-10-09, DEC-306: 6 `tool_selection_cognition` for the memo verbs and
    `memo_read`, 2 `tool_selection` for «記低» about the group and about the
    speaker), plus 3 `tool_selection` cases for `file_fetch` (2026-10-10,
    design/external-files §3: a shared doc and a shared sheet link opened by
    their handle, and a plain web page that stays `web_fetch`'s; NOT yet run),
    plus 1 `tool_selection` case for `computer_import` with a shared file's
    link (2026-10-11, design/external-files §5: a shared sheet put on her
    computer to compute on, against `file_fetch`; NOT yet run), plus 1
    `tool_selection` case for `media_fetch` with a shared photo's link
    (2026-10-11, design/external-files §2: looked at through the source,
    against `file_fetch` and `web_fetch`; NOT yet run), plus 2
    `proactive_act` cases from the 2026-10-11 triple ask (design/cognition-
    loop/35 §5): a12, her own answer five minutes old, and a13, the group
    met an hour before the ask, plus the `personality` corpus's 20
    (2026-10-13, design/context-plan/12 §5: the character cut's instrument,
    four kinds of ordinary tag turn with a short-reply control each; NOT yet
    run), plus 2 `lessons` cases (2026-10-13, DEC-311: a lesson that goes
    ahead of a practice default, and a control whose lesson must not go ahead
    of a rule), plus the `conversational_recall` corpus's 8 (2026-10-14,
    design/unified-memory/00-intent.md "Sequence" step 0: J1 a photo's
    reading twice, J2 a dated plan twice, J3 an outing's food and doings,
    J4 outings in a week twice, every case on a full restore; NOT yet run),
    plus 10 `tool_selection` cases for `memory_search` (2026-10-16, DEC-316:
    8 positives against `about`, `who_said`, `where_been`, `media_search` and
    `lookup_person`, two with a date span and two with `kinds`, and 2
    boundary controls that stay `about`'s and `who_said`'s; NOT yet run);
    a change to it
    is a change to what the suite claims to measure and belongs in a commit that
    says so. Recomputed with `census()` at each merge, never summed."""
    totals = [census(n) for n in CORPORA]
    assert sum(c.total for c in totals) == 317
    assert sum(c.active + c.parked for c in totals) == 317

"""The model-facing projections of folded observation heads — `head_summary`
and `head_detail` — share ONE read model (design/platform-observations/projections.md):

- a poll's aggregate option counts are the SOURCE's evidence; the retained
  per-voter selections are a separate, labelled RECORDED-SUBSET tally —
  never summed, never a claim of upstream completeness;
- a membership head counts only the native active member state as current
  members — pending, departed and unrecognized states are their own
  labelled buckets, never invented presence;
- every surface stays inside scope/handle redaction and budgets.

These are projection-level guards: they read the fields Standing,
`poll_read` and `record_query` agree on, not the fold's private state.
"""

from __future__ import annotations

from nora.domain.observations import (
    Coverage,
    MembershipPayload,
    Observation,
    PollPayload,
    SourceScope,
    StoredObservation,
    fold_observation,
    head_detail,
    head_summary,
    observation_summary,
)

SCOPE = SourceScope(platform="signal", account_key="acct",
                    conversation_id="conv-1")
NOW = 1_790_000_000_000

_NAMES = {"u1": "Ana", "u2": "Bo", "u3": "Cy"}


def _name_of(ref: str):
    return _NAMES.get(ref)


def _obs(kind: str = "poll", *, object_ref: str = "poll-1",
         operation: str = "create", payload=None, **kw) -> Observation:
    return Observation(object_kind=kind, scope=SCOPE, object_ref=object_ref,
                       operation=operation, payload=payload,
                       received_ms=NOW, **kw)


def _stored(obs: Observation, id: int) -> StoredObservation:
    return StoredObservation(id=id, event_id=f"e{id}",
                             source_digest=f"d{id}", observation=obs)


def _fold(*rows: StoredObservation):
    head = None
    for row in rows:
        head = fold_observation(head, row)
    return head


def _poll_create(id: int = 1) -> StoredObservation:
    return _stored(_obs(payload=PollPayload(
        question="lunch?", options=(("a", "yes"), ("b", "no")),
        state="open", anonymous=False, multi_select=True)), id)


def _vote(voter: str, option: str, id: int) -> StoredObservation:
    return _stored(_obs(operation="add", actor_id=voter, actor_status="known",
                        payload=PollPayload(voter_ref=voter,
                                            option_ref=option)), id)


def _select(voter: str, selected, id: int) -> StoredObservation:
    return _stored(_obs(operation="revise",
                        payload=PollPayload(voter_ref=voter,
                                            selected=tuple(selected))), id)


def _member(subject: str, op: str, id: int, *,
            state: str = "") -> StoredObservation:
    return _stored(_obs(kind="membership", object_ref="channel:c1",
                        operation=op, subject_ref=subject,
                        payload=MembershipPayload(state=state)), id)


def _counts_snapshot(counts, id: int, *, voter_count=None,
                     covered="option_counts", complete=True):
    return _stored(_obs(
        operation="snapshot",
        payload=PollPayload(option_counts=counts, voter_count=voter_count),
        coverage=Coverage(complete=complete, covered=covered)), id)


def _tally_by_label(entries) -> dict[str, int]:
    return {e["label"]: e["count"] for e in entries}


# ── the recorded-subset tally ────────────────────────────────────────────

def test_named_votes_report_a_labelled_subset_tally_without_aggregates():
    """A Signal-style poll — definition plus per-voter selections, no
    aggregate snapshot ever — still reports its split by option, as the
    RECORDED SUBSET, on both read surfaces."""
    head = _fold(_poll_create(), _vote("u1", "a", 2), _vote("u2", "b", 3),
                 _vote("u3", "b", 4))
    summary = head_summary(head)
    assert "recorded subset" in summary
    assert "no=2" in summary and "yes=1" in summary
    detail = head_detail(head, name_of=_name_of)
    assert _tally_by_label(detail["recorded_tally"]) == {"yes": 1, "no": 2}
    assert detail["recorded_voters"] == 3          # unique voters
    assert detail["recorded_selections"] == 3      # selection count
    assert detail["voters_named"] == ["Ana", "Bo", "Cy"]
    assert "tally" not in detail                   # no aggregate was recorded
    assert "voter_count" not in detail
    # No raw platform ref escapes as an option handle.
    for entry in detail["recorded_tally"]:
        assert entry["option"] not in ("a", "b")


def test_multiselect_counts_each_option_and_keeps_voters_distinct():
    # A selection is a set: repeated refs from one voter cannot add votes.
    head = _fold(_poll_create(), _select("u1", ("a", "b", "a"), 2),
                 _select("u2", ("b",), 3))
    detail = head_detail(head, name_of=_name_of)
    assert _tally_by_label(detail["recorded_tally"]) == {"yes": 1, "no": 2}
    assert detail["recorded_voters"] == 2       # two unique people …
    assert detail["recorded_selections"] == 3   # … cast three selections


def test_add_remove_and_revise_recast_the_subset():
    """A removed single-option vote and an empty `selected` revise both
    leave the recorded subset — withdrawal is not a zero-count option."""
    head = _fold(_poll_create(), _vote("u1", "a", 2), _vote("u2", "b", 3))
    head = fold_observation(head, _stored(_obs(
        operation="remove",
        payload=PollPayload(voter_ref="u1", option_ref="a")), 4))
    detail = head_detail(head, name_of=_name_of)
    assert _tally_by_label(detail["recorded_tally"]) == {"no": 1}
    head = fold_observation(head, _select("u2", (), 5))
    detail = head_detail(head, name_of=_name_of)
    assert "recorded_tally" not in detail and "recorded_voters" not in detail
    assert "recorded subset" not in head_summary(head)


def test_a_snapshot_empty_selection_is_a_withdrawal_not_a_voter():
    """A covered `voters` row whose selection is empty is a withdrawal:
    the record stays in the fold but casts no tally and counts no voter."""
    head = _fold(_poll_create(), _vote("u1", "a", 2))
    head = fold_observation(head, _stored(_obs(
        operation="snapshot",
        payload=PollPayload(voters=(("u1", ()), ("u2", ("b", "b")))),
        coverage=Coverage(complete=True, covered="voters")), 3))
    assert head.state["votes"]["u1"] == []    # the withdrawal is retained
    detail = head_detail(head, name_of=_name_of)
    assert _tally_by_label(detail["recorded_tally"]) == {"no": 1}
    assert detail["recorded_voters"] == 1     # u1 withdrew — not a voter
    assert detail["voters_named"] == ["Bo"]


def test_aggregate_and_named_selections_report_separately_never_summed():
    """An anonymous aggregate snapshot plus one recorded named selection
    keep their own labelled counts — the subset is never added to the
    source's totals and never masquerades as them."""
    head = _fold(_poll_create(), _counts_snapshot(
        {"a": 5, "b": 2}, 2, voter_count=7), _vote("u1", "a", 3))
    detail = head_detail(head, name_of=_name_of)
    assert _tally_by_label(detail["tally"]) == {"yes": 5, "no": 2}
    assert detail["voter_count"] == 7
    assert _tally_by_label(detail["recorded_tally"]) == {"yes": 1}
    assert detail["recorded_voters"] == 1
    assert detail["counts_complete"] is True
    summary = head_summary(head)
    assert "totals" in summary and "recorded subset" in summary


def test_complete_empty_snapshot_empties_the_reported_totals():
    head = _fold(_poll_create(), _counts_snapshot(
        {"a": 3, "b": 2}, 2, voter_count=5))
    assert "totals" in head_summary(head)
    head = fold_observation(head, _counts_snapshot({}, 3, voter_count=0))
    detail = head_detail(head, name_of=_name_of)
    assert "tally" not in detail
    assert detail["voter_count"] == 0
    assert detail["counts_complete"] is True
    assert "totals" not in head_summary(head)


# ── current membership ───────────────────────────────────────────────────

def test_join_leave_rejoin_requested_project_only_active_members():
    """u1 join → leave → rejoin; u2 requested: only the rejoined u1 counts
    as a member — the request reports as pending, the history stays."""
    head = _fold(_member("u1", "add", 1), _member("u1", "remove", 2),
                 _member("u1", "add", 3), _member("u2", "add", 4,
                                                  state="requested"))
    detail = head_detail(head, name_of=_name_of)
    assert detail["member_count"] == 1
    assert detail["members_named"] == ["Ana"]
    assert detail["pending_count"] == 1
    assert detail["pending_named"] == ["Bo"]
    assert "departed_count" not in detail   # u1 rejoined — nothing departed
    assert head_summary(head) == "membership: 1 member(s); 1 pending"


def test_departed_members_report_as_departed_not_as_members():
    head = _fold(_member("u1", "add", 1), _member("u2", "add", 2),
                 _member("u1", "remove", 3))
    detail = head_detail(head, name_of=_name_of)
    assert detail["member_count"] == 1
    assert detail["members_named"] == ["Bo"]
    assert detail["departed_count"] == 1
    assert detail["departed_named"] == ["Ana"]
    assert head_summary(head) == "membership: 1 member(s); 1 departed"


def test_an_unknown_member_state_is_neither_member_nor_departed():
    head = _fold(_member("u1", "add", 1),
                 _member("u2", "revise", 2, state="mystery"))
    detail = head_detail(head, name_of=_name_of)
    assert detail["member_count"] == 1
    assert detail["unknown_state_count"] == 1
    assert "pending_count" not in detail and "departed_count" not in detail
    assert head_summary(head) == "membership: 1 member(s); 1 unknown-state"


# ── artifact: ONE bounded projection on summary and detail ───────────────

from nora.domain.observations import ArtifactPayload  # noqa: E402


def _artifact(md: dict, *, op: str = "create", actor: str = "",
              actor_status: str = "not_supplied",
              coverage: Coverage | None = None) -> Observation:
    return _obs(kind="artifact", object_ref="event:e1", operation=op,
                actor_id=actor, actor_status=actor_status,
                payload=ArtifactPayload(
                    artifact_kind="event", metadata=md,
                    custody="declared"), coverage=coverage or Coverage())


def test_event_artifact_projects_time_fields_and_per_responder_responses():
    """`start_time_s` lands under `start_time` (not the stored suffix),
    each responder keeps their OWN recorded response, and a withdrawal
    removes only that responder's row."""
    head = _fold(_stored(_artifact({
        "name": "Breakfast", "start_time_s": 1790000000,
        "interested_count": 4}), 1))
    head = fold_observation(head, _stored(_artifact(
        {"response": "yes"}, op="revise", actor="u1",
        actor_status="known"), 2))
    head = fold_observation(head, _stored(_artifact(
        {"response": "maybe"}, op="revise", actor="u2",
        actor_status="known"), 3))
    detail = head_detail(head, name_of=_name_of)
    (entry,) = detail["artifacts"]
    # Time renders as ISO text under `start_time` — never the raw epoch.
    assert entry["start_time"].startswith("202")
    assert "start_time_s" not in entry and "start_time_" not in entry
    assert entry["interested_count"] == 4
    # Per-responder: both answers survive, named, never a last-response scalar.
    assert entry["responders"] == {"Ana": "yes", "Bo": "maybe"}
    assert entry["recorded_responders"] == 2
    assert entry["recorded_subset"] is True
    head = fold_observation(head, _stored(_artifact(
        {"response": ""}, op="revise", actor="u1",
        actor_status="known"), 4))          # u1 withdraws
    detail = head_detail(head, name_of=_name_of)
    (entry,) = detail["artifacts"]
    assert entry["responders"] == {"Bo": "maybe"}
    assert entry["recorded_responders"] == 1


def test_artifact_projection_is_bounded_and_reports_unprojected_keys():
    """Nothing a parser stored leaves silently: a non-whitelisted key
    reports as a COUNT on the detail surface, and no stored key value
    escapes raw into either render."""
    head = _fold(_stored(_artifact({
        "name": "x" * 500,
        "some_native_blob": {"deep": [1, 2, 3]},
        "join_url": "https://internal.example/join"}), 1))
    detail = head_detail(head, name_of=_name_of)
    (entry,) = detail["artifacts"]
    assert len(entry["name"]) <= 120
    assert entry["unprojected_fields"] == 2      # blob + join_url counted
    assert "some_native_blob" not in entry and "join_url" not in entry
    assert "internal.example" not in str(detail)


def test_a_response_delta_names_the_actor_answer_and_its_retraction():
    """The RSVP delta IS the observation's fact — actor, answer and extra
    guests on the same line a history read emits; a source-declared
    withdrawal says so; an answer the source marked unreadable is never
    called a withdrawal."""
    obs = _artifact({"response": "going", "extra_guest_count": 2},
                    op="revise", actor="u1", actor_status="known")
    line = observation_summary(obs, name_of=_name_of)
    assert "Ana" in line and "going" in line and "2 extra" in line
    obs = _artifact({"response": ""}, op="revise", actor="u1",
                    actor_status="known")
    assert "withdrew" in observation_summary(obs, name_of=_name_of)
    obs = _artifact(
        {"response": ""}, op="revise", actor="u1", actor_status="known",
        coverage=Coverage(complete=False, covered="response",
                          gaps=("response",)))
    line = observation_summary(obs, name_of=_name_of)
    assert "withdrew" not in line and "unknown" in line


def test_a_declared_null_is_a_clear_not_a_stale_field():
    """A full-after update reporting a field absent (explicit null) drops
    the stale value in the fold — description, location and end time all
    clear, while a reported `interested_count` of 0 stays a real zero."""
    head = _fold(_stored(_artifact({
        "name": "Breakfast", "description": "café", "location_name": "place",
        "end_time_s": 1790003600, "interested_count": 4}), 1))
    head = fold_observation(head, _stored(_artifact({
        "description": None, "location_name": None, "end_time_s": None,
        "interested_count": 0}, op="revise"), 2))
    (entry,) = head_detail(head, name_of=_name_of)["artifacts"]
    assert "description" not in entry and "location_name" not in entry
    assert "end_time" not in entry
    assert entry["interested_count"] == 0
    assert entry["name"] == "Breakfast"


def test_head_summary_names_artifact_status_responders_and_contacts():
    """The standing label is the same bounded projection as the detail —
    lifecycle status, who answered what and bounded contact fields all
    survive the one-line render."""
    head = _fold(_stored(_artifact({
        "name": "Breakfast", "start_time_s": 1790000000,
        "status": "canceled",
        "contacts": [{"display_name": "Dee", "phones": ["+1 555"]}],
        "contacts_more": 2},), 1))
    head = fold_observation(head, _stored(_artifact(
        {"response": "going"}, op="revise", actor="u1",
        actor_status="known"), 2))
    summary = head_summary(head, name_of=_name_of)
    assert "Breakfast" in summary and "canceled" in summary
    assert "Ana" in summary and "going" in summary
    assert "Dee" in summary
    (entry,) = head_detail(head, name_of=_name_of)["artifacts"]
    assert entry["contacts_more"] == 2
    assert entry["contacts"][0]["phones"] == ["+1 555"]


def test_head_summary_names_an_event_with_its_time_and_cancellation():
    head = _fold(_stored(_artifact({
        "name": "Breakfast", "start_time_s": 1790000000}), 1))
    summary = head_summary(head)
    assert "Breakfast" in summary and "at 202" in summary
    head = fold_observation(head, _stored(_artifact(
        {"is_canceled": True}, op="revise"), 2))
    assert "canceled" in head_summary(head)


def test_member_names_resolve_only_for_active_subjects():
    """The named-member list can no longer leak a departed subject's name:
    only currently active members resolve through `name_of`."""
    head = _fold(_member("u1", "add", 1), _member("u1", "remove", 2))
    detail = head_detail(head, name_of=_name_of)
    assert detail["member_count"] == 0
    assert "members_named" not in detail or "Ana" not in detail["members_named"]
    assert detail["departed_named"] == ["Ana"]
    # An unresolved ref never emits its raw platform id.
    raw = _fold(_member("stranger-7", "add", 1))
    detail = head_detail(raw, name_of=_name_of)
    assert detail["member_count"] == 1 and detail["members_unnamed"] == 1
    assert "stranger-7" not in str(detail)


def test_contact_current_summary_keeps_shared_contact_values():
    head = _fold(_stored(_artifact({"contacts": [{
        "display_name": "Example Contact", "organization": "Example Org",
        "phones": ["extension 41"], "emails": ["example@example.invalid"]}]}), 1))
    summary = head_summary(head)
    assert "Example Contact" in summary and "Example Org" in summary
    assert "extension 41" in summary and "example@example.invalid" in summary


def test_contact_nested_field_caps_report_omitted_values():
    head = _fold(_stored(_artifact({"contacts": [{
        "display_name": "Example Contact",
        "phones": [f"extension {i}" for i in range(9)]}]}), 1))
    contact = head_detail(head)["artifacts"][0]["contacts"][0]
    assert len(contact["phones"]) == 6
    assert contact["phones_more"] == 3
    assert "+7" in head_summary(head)

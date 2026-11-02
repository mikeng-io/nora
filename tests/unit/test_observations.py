"""No-I/O tests for the typed observation contract: envelope versioning and
compat, the observation wire round-trip and validation, and the fold's
ordering/coverage semantics (design/platform-observations 02)."""

from __future__ import annotations

import pytest

from nora.domain.models import EditTarget
from nora.domain.observations import (
    ArtifactPayload,
    Coverage,
    MembershipEntry,
    MembershipPayload,
    MessageMetadataPayload,
    Observation,
    PinPayload,
    PollPayload,
    ReactionPayload,
    RoomPayload,
    SourceScope,
    StoredObservation,
    coverage_label,
    fold_observation,
    head_detail,
    rebuild_head,
    target_from_key,
    target_key,
)
from nora.platform.bridge.envelope import (
    ENVELOPE_VERSION,
    KINDS,
    V1_KINDS,
    Envelope,
    canonical_source_key,
)
from nora.platform.bridge.rehydrate import (
    observation_from_dict,
    observation_to_dict,
)

SCOPE = SourceScope(platform="discord", account_key="acct",
                    conversation_id="room1")
NOW = 1_790_000_000_000


def _obs(kind: str = "poll", *, object_ref: str = "poll-1",
         operation: str = "create", payload=None, **kw) -> Observation:
    if payload is None:
        payload = PollPayload(question="lunch?",
                              options=(("a", "yes"), ("b", "no")),
                              state="open")
    return Observation(
        object_kind=kind, scope=SCOPE, object_ref=object_ref,
        operation=operation, payload=payload,
        received_ms=NOW, **kw)


def _stored(obs: Observation, id: int = 1, *, event_id: str = "e1",
            digest: str = "d1") -> StoredObservation:
    return StoredObservation(id=id, event_id=event_id, source_digest=digest,
                             observation=obs)


def _env(**kw) -> Envelope:
    base = {
        "platform": "discord", "account_key": "acct", "kind": "message",
        "source_key": canonical_source_key("discord", "acct", "room1",
                                           "message", "u1"),
        "event_id": "v1:abc", "conversation_id": "room1",
        "conversation_type": "channel", "sender_id": "u1",
        "sender_name": None,
        "sent_ms": NOW, "received_ms": NOW, "queued_ms": NOW,
        "time_quality": "upstream",
        "message": {"platform": "discord", "body": "hi"},
    }
    base.update(kw)
    return Envelope(**base)


# ── envelope versioning / compatibility ──────────────────────────────────

def test_v1_message_envelope_round_trips_as_v1():
    env = _env()
    fields = env.to_fields()
    assert fields["v"] == 1  # nothing a v1 reader cannot interpret
    back = Envelope.from_fields(fields)
    assert back == env


def test_v1_envelope_without_observation_key_parses():
    """A record written by the old library (no `observation` key at all)
    still rehydrates — journal replay after upgrade."""
    fields = _env().to_fields()
    del fields["observation"]
    assert fields["v"] == 1
    assert Envelope.from_fields(fields).observation is None


def test_observation_sidecar_emits_v2():
    obs = _obs()
    env = _env(observation=observation_to_dict(obs))
    fields = env.to_fields()
    assert fields["v"] == 2
    back = Envelope.from_fields(fields)
    assert back.observation == env.observation
    assert back.message == env.message


def test_pure_observation_envelope_carries_no_message():
    """A non-message event (a vote, a join) is kind+observation with
    message=None — never a fake NormalizedMessage."""
    obs = _obs(operation="add",
               payload=PollPayload(voter_ref="v1", option_ref="a"),
               actor_id="v1", actor_status="known")
    env = _env(kind="poll", message=None,
               observation=observation_to_dict(obs))
    fields = env.to_fields()
    assert fields["v"] == 2 and fields["message"] is None
    back = Envelope.from_fields(fields)
    assert back.message is None and back.kind == "poll"


def test_unsupported_envelope_version_is_retained_failure():
    with pytest.raises(ValueError, match="unsupported envelope version"):
        Envelope.from_fields({"v": 3, "kind": "message"})
    with pytest.raises(ValueError, match="unsupported envelope version"):
        Envelope.from_fields({"v": 0, "kind": "message"})


def test_v1_rejects_observation_sidecar():
    fields = _env().to_fields()
    fields["observation"] = {"contract": 1}
    with pytest.raises(ValueError, match="v1 envelope cannot carry"):
        Envelope.from_fields(fields)


def test_v1_rejects_missing_message():
    fields = _env().to_fields()
    fields["message"] = None
    with pytest.raises(ValueError, match="v1 envelope requires message"):
        Envelope.from_fields(fields)


def test_v2_rejects_unknown_kind():
    fields = _env(observation={"contract": 1}).to_fields()
    fields["kind"] = "nonsense"
    with pytest.raises(ValueError, match="unknown bridge kind"):
        Envelope.from_fields(fields)


def test_v2_requires_message_or_observation():
    fields = _env().to_fields()
    fields["v"] = 2
    fields["message"] = None
    fields["observation"] = None
    with pytest.raises(ValueError, match="needs message or observation"):
        Envelope.from_fields(fields)


def test_envelope_version_constant_is_current_emit_max():
    assert ENVELOPE_VERSION == 2
    assert "poll" in KINDS and "message" in V1_KINDS


# ── observation wire round-trip + validation ─────────────────────────────

@pytest.mark.parametrize("payload,op", [
    (PollPayload(question="q", options=(("a", "A"),), state="open"), "create"),
    (PollPayload(voter_ref="v1", option_ref="a"), "add"),
    (PollPayload(voter_ref="v1", selected=("a",)), "revise"),
    (PollPayload(option_counts={"a": 3}, voter_count=3), "snapshot"),
    (ReactionPayload(emoji="👍"), "add"),
    (ReactionPayload(counts={"👍": 2}), "snapshot"),
    (MembershipPayload(role="admin", state="member"), "add"),
    (MembershipPayload(covered="channel",
                       members=(MembershipEntry("u1"),)), "snapshot"),
    (RoomPayload(fields={"title": "new"}), "revise"),
    (PinPayload(message=EditTarget(message_id="m9")), "add"),
    (ArtifactPayload(artifact_kind="post", custody="declared"), "create"),
    (MessageMetadataPayload(forward_origin="room2", view_once=False),
     "create"),
])
def test_observation_wire_round_trip(payload, op):
    kind = payload.kind
    obs = _obs(kind=kind, operation=op, payload=payload,
               object_ref=f"{kind}-obj")
    wire = observation_to_dict(obs)
    back = observation_from_dict(wire)
    assert back == obs


def test_observation_wrong_contract_rejected():
    wire = observation_to_dict(_obs())
    wire["contract"] = 99
    with pytest.raises(ValueError, match="unsupported observation contract"):
        observation_from_dict(wire)


def test_observation_wrong_scope_rejected():
    obs = _obs()
    wire = observation_to_dict(obs)
    other = SourceScope(platform="discord", account_key="acct",
                        conversation_id="OTHER-room")
    with pytest.raises(ValueError, match="scope does not match"):
        observation_from_dict(wire, expect_scope=other)
    assert observation_from_dict(wire, expect_scope=SCOPE) == obs


def test_invalid_operation_for_kind_rejected():
    with pytest.raises(ValueError, match="invalid for"):
        _obs(kind="pin", operation="revise",
             payload=PinPayload(message=EditTarget(message_id="m")))
    with pytest.raises(ValueError, match="invalid for"):
        _obs(kind="room", operation="add",
             payload=RoomPayload(fields={"t": "x"}))


def test_bad_actor_status_and_time_quality_rejected():
    with pytest.raises(ValueError, match="actor_status"):
        _obs(actor_status="known")  # no actor_id
    with pytest.raises(ValueError, match="actor_id contradicts"):
        _obs(actor_id="x", actor_status="not_applicable")
    with pytest.raises(ValueError, match="upstream time_quality"):
        _obs(time_quality="upstream")  # no occurred_ms
    with pytest.raises(ValueError, match="revision_scope"):
        _obs(source_position=5)  # position without a scope


def test_poll_create_requires_options_and_delta_requires_voter():
    with pytest.raises(ValueError, match="requires options"):
        _obs(payload=PollPayload(question="q"))
    with pytest.raises(ValueError, match="voter_ref and option_ref"):
        _obs(operation="add", payload=PollPayload(option_ref="a"))
    with pytest.raises(ValueError, match="requires selected"):
        _obs(operation="revise", payload=PollPayload(voter_ref="v"))
    with pytest.raises(ValueError, match="unknown poll state"):
        _obs(payload=PollPayload(options=(("a", "A"),), state="half-open"))


def test_empty_object_ref_rejected():
    with pytest.raises(ValueError, match="object_ref"):
        _obs(object_ref="")


def test_target_key_round_trip():
    t = EditTarget(message_id="m123")
    assert target_from_key(target_key(t)) == t
    t2 = EditTarget(sent_ts=1700000000000)
    assert target_from_key(target_key(t2)).sent_ts == 1700000000000
    assert target_key(None) is None and target_from_key(None) is None
    with pytest.raises(ValueError, match="malformed"):
        target_from_key("nope:x")


# ── the fold ─────────────────────────────────────────────────────────────

def _poll_create(id: int = 1, pos: int | None = None) -> StoredObservation:
    return _stored(_obs(payload=PollPayload(
        question="lunch?", options=(("a", "yes"), ("b", "no")),
        state="open", anonymous=False),
        source_position=pos, revision_scope="gw" if pos is not None else "",
        occurred_ms=NOW), id)


def _poll_vote(voter: str, option: str, id: int,
               pos: int | None = None) -> StoredObservation:
    return _stored(_obs(
        operation="add", actor_id=voter, actor_status="known",
        payload=PollPayload(voter_ref=voter, option_ref=option),
        source_position=pos, revision_scope="gw" if pos is not None else "",
        occurred_ms=NOW), id)


def test_poll_fold_create_vote_close():
    head = None
    for row in (_poll_create(1, pos=1), _poll_vote("u1", "a", 2, pos=2),
                _poll_vote("u2", "b", 3, pos=3),
                _stored(_obs(operation="revise",
                             payload=PollPayload(state="closed",
                                                 close_ms=NOW + 1000),
                             source_position=4, revision_scope="gw"), 4)):
        head = fold_observation(head, row)
    assert head.state["question"] == "lunch?"
    assert head.state["options"] == {"a": {"label": "yes"},
                                     "b": {"label": "no"}}
    assert head.state["votes"] == {"u1": ["a"], "u2": ["b"]}
    assert head.state["state"] == "closed"
    assert head.ordering == "ordered" and head.position == 4
    assert head.applied == 4


def test_poll_response_before_definition_is_unresolved():
    head = fold_observation(None, _poll_vote("u1", "a", 1))
    assert head.state["options"] == {} and head.state["question"] is None
    assert head.coverage.get("awaiting_definition") is True
    # The definition arriving later resolves the same object.
    head = fold_observation(head, _poll_create(2))
    assert head.state["question"] == "lunch?"
    assert head.coverage.get("awaiting_definition") is not True
    assert head.state["votes"] == {"u1": ["a"]}  # the early vote survives


def test_voter_selection_replacement_and_withdrawal():
    head = fold_observation(None, _poll_create(1))
    head = fold_observation(head, _poll_vote("u1", "a", 2))
    head = fold_observation(head, _stored(_obs(
        operation="revise", payload=PollPayload(voter_ref="u1",
                                                selected=("b",))), 3))
    assert head.state["votes"] == {"u1": ["b"]}
    head = fold_observation(head, _stored(_obs(
        operation="revise", payload=PollPayload(voter_ref="u1",
                                                selected=())), 4))
    assert head.state["votes"] == {}  # [] = a withdrawn vote


def test_option_label_revision_keeps_option_identity():
    head = fold_observation(None, _poll_create(1))
    head = fold_observation(head, _stored(_obs(
        operation="revise",
        payload=PollPayload(options=(("a", "definitely"),))), 2))
    assert head.state["options"]["a"]["label"] == "definitely"
    assert "b" in head.state["options"]  # unmentioned option unchanged


def test_anonymous_aggregate_snapshot_never_invents_voters():
    head = fold_observation(None, _poll_create(1))
    head = fold_observation(head, _stored(_obs(
        operation="snapshot",
        payload=PollPayload(option_counts={"a": 5}, voter_count=5)), 2))
    assert head.state["option_counts"] == {"a": 5}
    assert head.state["voter_count"] == 5
    assert head.state["votes"] == {}  # counts are not identities


def test_stale_delta_marks_gap_instead_of_applying():
    head = fold_observation(None, _poll_create(1, pos=1))
    head = fold_observation(head, _poll_vote("u1", "a", 2, pos=3))
    # position 2 arrives after position 3 applied: retained, flagged, not
    # folded into the head out of order.
    head = fold_observation(head, _poll_vote("u2", "b", 3, pos=2))
    assert head.ordering == "gap"
    assert head.coverage.get("ordering_gap") is True
    assert head.state["votes"] == {"u1": ["a"]}
    assert head.applied == 2


def test_stale_snapshot_cannot_overlay_newer_state():
    head = fold_observation(None, _poll_create(1, pos=1))
    head = fold_observation(head, _poll_vote("u1", "a", 2, pos=3))
    head = fold_observation(head, _stored(_obs(
        operation="snapshot", source_position=2, revision_scope="gw",
        payload=PollPayload(option_counts={"a": 0, "b": 9},
                            voter_count=9)), 3))
    assert head.state["votes"] == {"u1": ["a"]}
    assert head.state["option_counts"] == {}  # old snapshot did not land
    assert head.coverage.get("superseded_snapshots") == 1
    assert head.ordering == "ordered"


def test_incremental_head_equals_full_rebuild():
    rows = [_poll_create(1, pos=1), _poll_vote("u1", "a", 2, pos=2),
            _poll_vote("u2", "a", 3, pos=3)]
    inc = None
    for r in rows:
        inc = fold_observation(inc, r)
    assert rebuild_head(rows, kind="poll", object_ref="poll-1") == inc


def test_unordered_stream_folds_in_arrival_order_honestly():
    head = fold_observation(None, _poll_create(1))
    head = fold_observation(head, _poll_vote("u1", "a", 2))
    assert head.ordering == "arrival" and head.position is None
    assert head.state["votes"] == {"u1": ["a"]}


def test_partial_membership_snapshot_merges_complete_replaces():
    def mk(members, complete):
        return _stored(_obs(
            kind="membership", object_ref="channel:c1", operation="snapshot",
            payload=MembershipPayload(covered="channel", members=members),
            coverage=Coverage(complete=complete, covered="members")), 1)
    head = fold_observation(
        None, _stored(_obs(
            kind="membership", object_ref="channel:c1", operation="add",
            subject_ref="u0", payload=MembershipPayload(role="mod")), 0))
    head = fold_observation(head, mk(
        (MembershipEntry("u1"), MembershipEntry("u2", role="admin")),
        complete=False))
    # Partial: u0 observed earlier stays; the snapshot merged, never removed.
    assert set(head.state["members"]) == {"u0", "u1", "u2"}
    head = fold_observation(head, mk((MembershipEntry("u3"),), complete=True))
    # Complete: the covered collection IS the object — unlisted subjects are
    # no longer members of it.
    assert set(head.state["members"]) == {"u3"}


def test_member_remove_marks_state_not_departure():
    head = fold_observation(None, _stored(_obs(
        kind="membership", object_ref="channel:c1", operation="remove",
        subject_ref="u1", actor_id="mod1", actor_status="known",
        payload=MembershipPayload()), 1))
    # Removal records what the source said — never a voluntary departure.
    assert head.state["members"]["u1"]["state"] == "left"


def test_room_delta_and_partial_snapshot_semantics():
    def room(fields, op="revise", complete=True):
        return _stored(_obs(
            kind="room", object_ref="chan-1", operation=op,
            payload=RoomPayload(fields=fields),
            coverage=Coverage(complete=complete, covered="fields")), 1)
    head = fold_observation(None, room({"title": "t", "topic": "old"}))
    head = fold_observation(head, room({"topic": "new"}))
    assert head.state["fields"] == {"title": "t", "topic": "new"}
    # explicit null clears; absent stays
    head = fold_observation(head, room({"title": None}))
    assert head.state["fields"] == {"topic": "new"}
    # partial snapshot merges, never clears
    head = fold_observation(head, room({"slow": 10}, op="snapshot",
                                       complete=False))
    assert head.state["fields"] == {"topic": "new", "slow": 10}


def test_reaction_deltas_and_unattributed_counts():
    def r(emoji, op, actor=None, status="known", **kw):
        return _stored(_obs(
            kind="reaction", object_ref="msg:m1", operation=op,
            target=EditTarget(message_id="m1"), actor_id=actor or "",
            actor_status=status,
            payload=ReactionPayload(emoji=emoji, **kw)), 1)
    head = fold_observation(None, r("👍", "add", actor="u1"))
    head = fold_observation(head, r("👍", "add", actor="u2"))
    head = fold_observation(head, r("👍", "add", status="anonymous"))
    assert head.state["standing"] == {"👍": ["u1", "u2"]}
    assert head.state["counts"] == {"👍": 1}  # unattributed, not an identity
    head = fold_observation(head, r("👍", "remove", actor="u2"))
    assert head.state["standing"] == {"👍": ["u1"]}
    # clear with a covered emoji scope removes only that emoji
    head = fold_observation(head, r("❤️", "add", actor="u9"))
    head = fold_observation(head, r("👍", "clear", status="not_applicable",
                                    covered_emojis=("👍",)))
    assert head.state["standing"] == {"❤️": ["u9"]}
    assert head.state["counts"].get("👍") is None


def test_reaction_snapshot_replaces_covered_standing():
    head = fold_observation(None, _stored(_obs(
        kind="reaction", object_ref="msg:m1", operation="add",
        target=EditTarget(message_id="m1"), actor_id="u1",
        actor_status="known", payload=ReactionPayload(emoji="👍")), 1))
    head = fold_observation(head, _stored(_obs(
        kind="reaction", object_ref="msg:m1", operation="snapshot",
        target=EditTarget(message_id="m1"),
        payload=ReactionPayload(counts={"🎉": 4}, reactors=None),
        coverage=Coverage(complete=True, covered="reactions")), 2))
    # A complete snapshot's counts stand; old per-actor standing is replaced
    # (identities aren't synthesized from counts).
    assert head.state["counts"] == {"🎉": 4}
    assert head.state["standing"] == {}
    assert head.state["snapshot"] is True


def test_pin_fold_and_snapshot():
    def pin(t, op):
        return _stored(_obs(
            kind="pin", object_ref="room1", operation=op,
            payload=PinPayload(message=t) if op != "snapshot"
            else PinPayload(pins=(t,)),
            coverage=Coverage(complete=True)), 1)
    head = fold_observation(None, pin(EditTarget(message_id="m1"), "add"))
    head = fold_observation(head, pin(EditTarget(message_id="m2"), "add"))
    assert set(head.state["pins"]) == {"id:m1", "id:m2"}
    head = fold_observation(head, pin(EditTarget(message_id="m1"), "remove"))
    assert set(head.state["pins"]) == {"id:m2"}
    head = fold_observation(head, pin(EditTarget(message_id="m9"), "snapshot"))
    assert set(head.state["pins"]) == {"id:m9"}


def test_artifact_fold_and_withdraw():
    head = fold_observation(None, _stored(_obs(
        kind="artifact", object_ref="art-1", operation="create",
        payload=ArtifactPayload(artifact_kind="post",
                                metadata={"author": "someone"},
                                custody="declared", preview=True)), 1))
    assert head.state["artifacts"]["art-1"]["custody"] == "declared"
    head = fold_observation(head, _stored(_obs(
        kind="artifact", object_ref="art-1", operation="withdraw",
        payload=ArtifactPayload(artifact_kind="post")), 2))
    assert head.state["artifacts"] == {}


def test_message_metadata_fold():
    head = fold_observation(None, _stored(_obs(
        kind="message_metadata", object_ref="msg:m7", operation="create",
        payload=MessageMetadataPayload(forward_origin="r9",
                                       view_once=True)), 1))
    assert head.state["fields"]["forward_origin"] == "r9"
    assert head.state["fields"]["view_once"] is True


def test_timestamp_target_author_survives_wire_round_trip():
    obs = _obs(target=EditTarget(sent_ts=NOW), target_author_id="author-a")
    restored = observation_from_dict(observation_to_dict(obs))
    assert restored.target_author_id == "author-a"
    assert restored.target == EditTarget(sent_ts=NOW)
    assert target_key(restored.target, author_id="author-a") != target_key(
        restored.target, author_id="author-b"
    )
    assert target_from_key(target_key(restored.target, author_id="author-a")) == obs.target


def test_current_head_revision_tracks_a_changed_source_scope_without_global_order():
    head = fold_observation(None, _stored(_obs(
        source_position=50, revision_scope="session-a"), 1))
    head = fold_observation(head, _stored(_obs(
        operation="revise", payload=PollPayload(state="closed"),
        source_position=2, revision_scope="session-b"), 2))
    assert head.state["state"] == "closed"
    assert (head.position, head.scope) == (2, "session-b")
    assert head.ordering == "arrival" and head.coverage["unordered"]


def test_rejected_late_event_cannot_relabel_current_state_time():
    head = fold_observation(None, _stored(_obs(
        occurred_ms=NOW, time_quality="upstream",
        source_position=5, revision_scope="stream"), 1))
    head = fold_observation(head, _stored(_obs(
        operation="revise", payload=PollPayload(state="closed"),
        occurred_ms=NOW + 100, time_quality="upstream",
        source_position=3, revision_scope="stream"), 2))
    assert head.state["state"] == "open" and head.observed_ms == NOW
    head = fold_observation(head, _stored(_obs(
        operation="revise", payload=PollPayload(state="closed"),
        source_position=6, revision_scope="stream"), 3))
    assert head.state["state"] == "closed" and head.ordering == "gap"
    assert "ordering_gap" in coverage_label(head.coverage)


def test_partial_snapshot_grain_survives_model_facing_current_state():
    head = fold_observation(None, _stored(_obs(
        kind="membership", operation="snapshot",
        payload=MembershipPayload(covered="guild:source-id",
                                  members=(MembershipEntry("u1"),)),
        coverage=Coverage(complete=False, covered="guild:source-id"))))
    label = coverage_label(head.coverage)
    assert "partial" in label and "guild:" in label and "source-id" not in label
    detail = head_detail(head)
    assert detail["member_count"] == 1
    assert detail["coverage"]["partial"] is True
    assert "source-id" not in str(detail)


def _poll_counts_snapshot(counts, id: int, *, voter_count=None,
                          covered="option_counts", complete=True):
    return _stored(_obs(
        operation="snapshot",
        payload=PollPayload(option_counts=counts, voter_count=voter_count),
        coverage=Coverage(complete=complete, covered=covered)), id)


def _member_delta(subject: str, op: str, id: int, *,
                  state: str = "", role: str = "") -> StoredObservation:
    return _stored(_obs(
        kind="membership", object_ref="channel:c1", operation=op,
        subject_ref=subject,
        payload=MembershipPayload(role=role, state=state)), id)


# ── poll aggregate snapshots: the covered count set ──────────────────────

def test_complete_counts_snapshot_replaces_including_the_empty_table():
    """A COMPLETE `option_counts` coverage replaces the whole count table —
    a covered EMPTY table (the WA `pollResultSnapshot` "no recorded votes"
    shape) leaves zero counts behind, never the older nonzero ones."""
    head = fold_observation(None, _poll_create(1))
    head = fold_observation(head, _poll_counts_snapshot(
        {"a": 3, "b": 2}, 2, voter_count=5))
    assert head.state["option_counts"] == {"a": 3, "b": 2}
    assert head.state["voter_count"] == 5
    assert head.state["counts_complete"] is True
    head = fold_observation(head, _poll_counts_snapshot(
        {}, 3, voter_count=0))
    assert head.state["option_counts"] == {}
    assert head.state["voter_count"] == 0
    assert head.state["counts_complete"] is True


def test_omitted_options_die_only_under_a_complete_claim():
    """Complete replaces the covered set — an omitted option is no longer in
    it. Partial merges: it updates what it reports and leaves the rest."""
    head = fold_observation(None, _poll_create(1))
    head = fold_observation(head, _poll_counts_snapshot(
        {"a": 3, "b": 2}, 2, voter_count=5))
    head = fold_observation(head, _poll_counts_snapshot(
        {"a": 4}, 3, complete=False))
    assert head.state["option_counts"] == {"a": 4, "b": 2}
    assert head.state["counts_complete"] is False
    assert head.state["voter_count"] == 5  # untouched partial evidence
    head = fold_observation(head, _poll_counts_snapshot(
        {"a": 5}, 4))
    assert head.state["option_counts"] == {"a": 5}
    # The covered table carries no voter total: unknown, not the old 5.
    assert head.state["voter_count"] is None
    assert head.state["counts_complete"] is True


def test_voters_only_coverage_preserves_aggregate_evidence():
    """A complete `voters` snapshot owns the voter set; the aggregate count
    table is a different facet and survives it exactly."""
    head = fold_observation(None, _poll_create(1))
    head = fold_observation(head, _poll_counts_snapshot(
        {"a": 5, "b": 2}, 2, voter_count=7))
    head = fold_observation(head, _stored(_obs(
        operation="snapshot",
        payload=PollPayload(voters=(("u1", ("a",)), ("u2", ("a", "b")))),
        coverage=Coverage(complete=True, covered="voters")), 3))
    assert head.state["votes"] == {"u1": ["a"], "u2": ["a", "b"]}
    assert head.state["option_counts"] == {"a": 5, "b": 2}
    assert head.state["voter_count"] == 7
    assert head.state["counts_complete"] is True


def test_counts_outside_their_covered_claim_merge_as_partial():
    """Counts a complete snapshot supplies under a DIFFERENT covered claim
    are partial evidence: they merge, flip `counts_complete` off, and keep
    the options they omitted."""
    head = fold_observation(None, _poll_create(1))
    head = fold_observation(head, _poll_counts_snapshot(
        {"a": 5, "b": 2}, 2, voter_count=7))
    head = fold_observation(head, _poll_counts_snapshot(
        {"a": 9}, 3, covered="voters"))
    assert head.state["option_counts"] == {"a": 9, "b": 2}
    assert head.state["counts_complete"] is False
    # voter_count was supplied on neither the covered claim nor the merge.
    assert head.state["voter_count"] == 7


def test_empty_voter_set_under_complete_voters_coverage_is_a_mass_withdrawal():
    head = fold_observation(None, _poll_create(1))
    head = fold_observation(head, _poll_vote("u1", "a", 2))
    head = fold_observation(head, _poll_vote("u2", "b", 3))
    head = fold_observation(head, _stored(_obs(
        operation="snapshot", payload=PollPayload(voters=()),
        coverage=Coverage(complete=True, covered="voters")), 4))
    assert head.state["votes"] == {}


# ── membership standing vocabulary ───────────────────────────────────────

def test_member_join_leave_rejoin_requested_fold_their_own_states():
    """join → leave → rejoin → requested: every recorded standing keeps its
    own state in the folded map — removal records, never erases."""
    head = fold_observation(None, _member_delta("u1", "add", 1))
    assert head.state["members"]["u1"]["state"] == "member"
    head = fold_observation(head, _member_delta("u1", "remove", 2))
    assert head.state["members"]["u1"]["state"] == "left"
    head = fold_observation(head, _member_delta("u1", "add", 3))
    assert head.state["members"]["u1"]["state"] == "member"
    head = fold_observation(head, _member_delta(
        "u2", "add", 4, state="requested"))
    assert head.state["members"]["u2"]["state"] == "requested"
    head = fold_observation(head, _member_delta(
        "u3", "revise", 5, state="banned"))
    assert head.state["members"]["u3"]["state"] == "banned"
    assert set(head.state["members"]) == {"u1", "u2", "u3"}


def test_complete_member_snapshot_replaces_with_native_states():
    """A covered-complete member snapshot replaces the whole collection —
    its own states included (a `left` row the snapshot carries is kept as
    history, not dropped and not counted as a member)."""
    head = fold_observation(None, _member_delta("u1", "add", 1))
    snapshot = _obs(
        kind="membership", object_ref="channel:c1", operation="snapshot",
        payload=MembershipPayload(
            covered="channel",
            members=(MembershipEntry("u2", state="member"),
                     MembershipEntry("u3", state="requested"),
                     MembershipEntry("u4", state="left"))),
        coverage=Coverage(complete=True, covered="members"))
    head = fold_observation(head, _stored(snapshot, 2))
    members = head.state["members"]
    assert set(members) == {"u2", "u3", "u4"}   # u1 is out of the covered set
    assert members["u3"]["state"] == "requested"
    assert members["u4"]["state"] == "left"

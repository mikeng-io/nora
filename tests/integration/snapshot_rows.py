"""Generated rows for `test_snapshot_holds_what_was_held`: `ROWS` is a valid
row per in-place table for key `i`, `REVISE` the column each revision moves,
`APPEND` one append-only row per table citing the stable key-0 parents."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from nora.domain.ticks import due_tick_of_at
from tests.integration import managed_snapshot_rows as managed_rows

T0 = datetime(2026, 9, 1, tzinfo=UTC)
MS0 = int(T0.timestamp() * 1000)


def _uuid(table: str, i: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{table}:{i}"))


def _hex(table: str, i: int) -> str:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"{table}:{i}").hex


#: What every presence Rollup row carries (0071).
_ROLLUP: dict[str, Any] = {"build_id": 1, "build_lineage": [["presence", "s", None]]}


def _UNIT_ROLLUP(i: int) -> dict[str, Any]:
    return {
        "build_id": 1, "unit": "u", "build_lineage": [["presence", "a", None]],
        "valid_from_ms": MS0 + i, "valid_to_ms": MS0 + 1000 + i, "as_of_ms": MS0 + i,
    }


def _ledger_row(kind: str, i: int) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": f"{kind}-{i}", "revision": 1, "parent_revision": None,
        "unit": "u", "scope": "unit", "platform": "signal", "conversation_id": "g1",
        "status": "open", "last_revised_at": MS0 + i,
        "made_at" if kind == "commitment" else "opened_at": MS0 + i,
        "topic" if kind == "thread" else "subject": f"Generated work {i}",
    }
    if kind == "thread":
        row.update(note_ids=[], decision_ids=[], message_ids=[], spans=[], last_moved_at=MS0 + i)
    else:
        row["body"] = f"Generated evidence {i}"
    if kind == "commitment":
        row["owed_to"] = ["room"]
    return row


#: A valid row per in-place table, for key `i`.
ROWS: dict[str, Callable[[int], dict[str, Any]]] = {
    **managed_rows.ROWS,
    "legacy_graph_arrival": lambda i: {
        "unit": "u", "message_id": 1000+i, "revision": f"generated-{i}", "room": "signal:g1",
    },
    "legacy_entity": lambda i: {
        "id": f"graph-entity-{i}", "kind": "event", "unit": "u", "name": f"Generated event {i}",
    },
    "legacy_relation": lambda i: {
        "id": f"graph-relation-{i}", "src_kind": "person", "src_id": "generated-person",
        "rel": "attended", "dst_kind": "event", "dst_id": "graph-entity-0",
        "layer": "notebook", "unit": "u", "asserted_at": T0, "written_by": "generated",
    },
    "legacy_relation_evidence": lambda i: {
        "id": 85000 + i, "relation_id": "graph-relation-0", "source_kind": "message",
        "source_id": "1000", "role": "supports", "span": f"Generated span {i}",
    },
    "legacy_memory_work_item": lambda i: {
        "id": f"graph-work-{i}", "source_kind": "message", "source_id": f"generated-{i}",
        "unit": "u", "input_hash": f"generated-hash-{i}", "as_of": T0,
    },
    # The Cognitive Graph's tables (0155): `registry` rows are spare identities
    # nothing references; the kind tables' registry parents are seeded by
    # `_write` — the same registry+row write the store makes.
    "registry": lambda i: {
        "id": f"cg-spare-{i}", "unit": "u", "kind": "entity",
    },
    "entity": lambda i: {
        "id": f"cg-entity-{i}", "kind": "entity", "basis": "computed", "unit": "u",
        "exposure": "internal", "writer": "generated", "state": "admitted",
        "name": f"Generated entity {i}",
    },
    "claim": lambda i: {
        "id": f"cg-claim-{i}", "kind": "claim", "basis": "inferred",
        "reasoning": "deduced", "confidence": "high", "unit": "u",
        "exposure": "internal", "writer": "generated", "state": "live",
        "part": "fact", "content": {"text": f"Generated claim {i}"},
    },
    "intention": lambda i: {
        "id": f"cg-intention-{i}", "kind": "intention", "basis": "committed",
        "unit": "u", "exposure": "internal", "writer": "generated", "state": "open",
        "owner": "nora", "owed_to": "person:mike", "form": "achieve",
        "done_when_form": "act_event", "done_when": {"act": f"act-{i}"},
        "delivery_mode": "when_it_comes_up",
    },
    "edge": lambda i: {
        "id": f"cg-edge-{i}", "kind": "edge", "basis": "computed", "unit": "u",
        "exposure": "internal", "writer": "generated", "state": "live",
        "type": "about", "src_id": f"cg-anchor-src-{i}", "dst_id": f"cg-anchor-dst-{i}",
    },
    "standing_decision": lambda i: _ledger_row("decision", i),
    "standing_commitment": lambda i: _ledger_row("commitment", i),
    "standing_thread": lambda i: _ledger_row("thread", i),
    "extraction": lambda i: {
        "id": 81000 + i, "unit": "u", "platform": "signal", "origin_room": "g1",
        "source_ids": [], "context_ids": [], "snapshot": {}, "version": 1, "model": "m",
    },
    "extraction_queue": lambda i: {
        "id": 84000 + i,
        "message_id": 1000 + i, "unit": "u", "platform": "signal", "origin_room": "g1",
        "source_key": f"s{i}", "extraction_id": 81000,
    },
    "extraction_claim": lambda i: {
        "id": 82000 + i, "extraction_id": 81000, "ordinal": i, "unit": "u",
        "platform": "signal", "origin_room": "g1", "source_ids": [], "content": {},
    },
    "extraction_attempt": lambda i: {
        "id": 83000 + i, "extraction_id": 81000, "attempt": i, "model": "m",
    },
    "messages": lambda i: {
        "id": 1000 + i, "platform": "signal", "conversation_id": "g1",
        "conversation_type": "group", "sender_id": f"s{i}", "sender_name": f"n{i}",
        "ts": MS0 + i, "body": f"message {i}", "is_nora": 0,
    },
    "message_edit": lambda i: {
        "id": 3000 + i, "message_id": 1000, "ts": MS0 + i, "body": f"edit {i}",
    },
    # The two cascading children of `messages` (0069).
    "message_reaction": lambda i: {
        "id": 4000 + i, "message_id": 1000, "sender_id": f"s{i}", "emoji": "👍",
        "ts": MS0 + i,
    },
    "message_delete": lambda i: {
        "id": 5000 + i, "message_id": 1000, "sender_id": "", "ts": MS0 + i,
    },
    "note_candidate": lambda i: {
        "id": _uuid("nc", i), "unit": "u", "kind": "miss", "probe": f"p{i}",
        "first_seen_at": MS0, "last_seen_at": MS0, "occurrence_count": 1,
    },
    "note": lambda i: {
        "id": _uuid("note", i), "unit": "u", "title": f"note {i}", "description": "d",
        "uncertainty": "open", "closing_condition": "c", "anchor_date": 20260901,
        "established_in": "signal:g1", "created_at": MS0 + i,
        "head_revision_id": _uuid("head", i),
    },
    "culture": lambda i: {"key": f"room{i}",
        "head_revision_id": _uuid("cr", 100 + i), "updated_at": MS0},
    "standing": lambda i: {
        "platform": "signal", "conversation_id": f"g{i}", "body": f"position {i}",
        "updated_at": MS0,
    },
    "reminders": lambda i: {
        "id": _hex("rem", i), "requester_person_id": "p", "platform": "signal",
        "conversation_id": "g1", "conversation_type": "group", "text": f"r{i}",
        "created_at": T0, "due_at": T0 + timedelta(hours=1), "state": "pending",
        "due_tick": due_tick_of_at(T0 + timedelta(hours=1)),
    },
    "compute_jobs": lambda i: {
        "id": _uuid("job", i), "backend_job_id": f"b{i}", "task": "t", "platform": "signal",
        "conversation_id": "g1", "conversation_type": "group", "requester_person_id": "p",
        "created_at": T0, "state": "pending", "kind": "subagent", "payload": {},
    },
    "instagram_summons": lambda i: {
        "id": _uuid("ig", i), "message_id": f"m{i}", "media_id": "md", "shortcode": "sc",
        "author_igid": "a", "state": "claimed", "mode": "dry_run",
    },
    "version_announcements": lambda i: {
        "version": f"V{i}", "platform": "signal", "conversation_id": "g1",
        "announced_at": T0, "state": "announcing",
    },
    "participation_window_state": lambda i: {
        "platform": "signal", "conversation_id": f"g{i}", "last_window_end_at": MS0,
    },
    "dream_schedule_state": lambda i: {
        "adaptor_name": "a", "scope_id": f"s{i}", "last_fired_at": MS0,
    },
    "consolidation_watermark": lambda i: {
        "consolidator_name": "c", "scope_id": f"s{i}", "last_message_id": i,
    },
    "analytics_watermark": lambda i: {
        "deriver_name": "d", "subject_id": f"s{i}", "last_fix_id": i,
    },
    "tag_debounce_state": lambda i: {
        "platform": "signal", "conversation_id": f"g{i}", "last_allowed_at": MS0,
    },
    "media_objects": lambda i: {
        "sha256": f"{i:064x}", "source": "inbound", "description": f"shows {i}",
        "created_at_ms": MS0,
    },
    "video_readings": lambda i: {
        "sha256": f"{i:064x}", "provider": "p", "model": "m", "chunk_seconds": 10,
        "chunk_max": 3, "reading_digest": "d", "reading": f"reading {i}",
    },
    "presence_fixes": lambda i: {
        "id": 2000 + i, "circle_id": "c", "person_id": "p", "ts": MS0 + i, "lat": 22.25,
        "lng": 114.125, "wire_state": "ok", "recorded_at": MS0 + i,
    },
    "music_queue": lambda i: {
        "id": _hex("mq", i), "guild_id": "g", "position": i, "source": "youtube",
        "url": f"u{i}", "title": f"t{i}", "added_by_person_id": "p", "added_at": T0,
    },
    "music_session": lambda i: {
        "guild_id": f"g{i}", "voice_channel_id": "v", "notify_channel_id": "n",
        "paused": False, "volume": 1.0, "updated_at": T0,
    },
    "geocode_cache": lambda i: {
        "lat_rounded": 22.0 + i / 8, "lng_rounded": 114.0, "address": f"street {i}",
    },
    "sticker_index": lambda i: {
        "platform": "signal", "fetch_token": f"t{i}", "sha256": f"{i:064x}",
    },
    # Migration 0071: the Rollup's builds, fold states, presence rows and
    # unit rollups, and the collector's saved places.
    "fetched_pages": lambda i: {
        "conversation_id": "g1", "url": f"https://example.invalid/generated/{i}",
        "blob_key": f"generated-page-{i}", "fetched_at": T0, "expires_at": T0 + timedelta(days=1),
    },
    "social_item": lambda i: {
        "id": _uuid("si", i), "network": "instagram", "item_id": f"item{i}",
        "person": "ken", "account": "10000001", "source_kind": "post", "taken_at": T0,
        "seen_at": T0, "origin": "poll",
    },
    "social_item_count": lambda i: {
        "id": _uuid("sc", i), "network": "instagram", "item_id": f"item{i}",
        "likes": i, "comments": 1, "likes_hidden": False,
    },
    "social_item_state": lambda i: {
        "id": _uuid("ss", i), "network": "instagram", "item_id": f"item{i}", "source_state": "gone",
    },
    "social_poll": lambda i: {
        "id": _uuid("sp", i), "network": "instagram", "person": "ken", "stage": "probe",
        "outcome": "unchanged",
    },
    "source_health": lambda i: {
        "id": 7100 + i, "source": "presence", "scope": "c1", "event": "poll", "outcome": "ok",
        "at_ms": MS0 + i,
    },
    "presence_places": lambda i: {
        "circle_id": "c", "place_id": f"pl{i}", "unit": None, "name": f"place {i}", "lat": 22.30,
        "lng": 114.20, "updated_ms": MS0 + i,
    },
    "presence_stays": lambda i: {
        "id": 6000 + i, "subject_id": "s", "person_id": "s", "circle_id": "c",
        "started_ts": MS0 + i, "ended_ts": MS0 + 600_000 + i, "lat": 22.35, "lng": 114.30,
        "radius_m": 50.0, "fix_count": 3, **_ROLLUP,
    },
    "presence_trips": lambda i: {
        "id": 6100 + i, "subject_id": "s", "person_id": "s", "circle_id": "c",
        "started_ts": MS0 + i, "ended_ts": MS0 + 60_000 + i, "distance_m": 500.0,
        "avg_speed_kmh": 30.0, "fix_count": 3, "sequence": [], **_ROLLUP,
    },
    "presence_derived_places": lambda i: {
        "id": 6200 + i, "subject_id": "s", "circle_id": "c", "lat": 22.40, "lng": 114.40,
        "radius_m": 100.0, "source_kind": "unlabeled", "place_confidence": 0.5,
        "first_seen_ts": MS0, "last_seen_ts": MS0, **_ROLLUP,
    },
    "presence_occupancy": lambda i: {
        "id": 6300 + i, "subject_id": "s", "place_id": 6200, "hour_of_week": i,
        "dwell_ms": 1000, "updated_ts": MS0, **_ROLLUP,
    },
    "presence_place_rhythm": lambda i: {
        "subject_id": "s", "place_id": 6200 + i, "sample_count": 1, "updated_ts": MS0,
        **_ROLLUP,
    },
    "presence_corridors": lambda i: {
        "id": 6400 + i, "subject_id": "s", "sequence": ["Road A"], "sample_count": 3,
        "first_seen_ts": MS0, "last_seen_ts": MS0, **_ROLLUP,
    },
    "rollup_build": lambda i: {
        "id": 1 + i, "code_version": "test", "params_sha256": "", "status": "building",
    },
    "rollup_fold_state": lambda i: {
        "build_id": 1, "subject_id": f"s{i}", "pos_recorded_ms": MS0 + i, "pos_rank": 0,
        "pos_id": i, "logical_now_ms": MS0 + i, "frontier_ts": MS0,
    },
    "rollup_colocation": lambda i: {
        "id": 6500 + i, "persons": ["a", "b"], "lat": 22.45, "lng": 114.50,
        "radius_m": 50.0, "stay_count": 2, **_UNIT_ROLLUP(i),
    },
    "rollup_outing": lambda i: {
        "id": 6600 + i, "persons": ["a", "b"], "lat": 22.50, "lng": 114.60,
        **_UNIT_ROLLUP(i),
    },
    "rollup_daybook": lambda i: {
        "id": 6700 + i, "person_id": "a", "local_date": f"2026-09-0{1 + i}", "stays": [],
        "stay_ms": 0, "home_ms": 0, "colocation_ms": 0, "outings": 0, "rooms": {},
        "messages": i, "reactions": 0, **_UNIT_ROLLUP(i),
    },
    "rollup_tie": lambda i: {
        "id": 6800 + i, "person_a": "a", "person_b": "b", "iso_week": f"2026-W3{i}",
        "replies_ab": i, "replies_ba": 0, "mentions_ab": 0, "mentions_ba": 0,
        "reactions_ab": 0, "reactions_ba": 0, "colocation_ms": 0, **_UNIT_ROLLUP(i),
    },
    "rollup_response": lambda i: {
        "id": 6900 + i, "message_id": 1000 + i, "platform": "signal",
        "conversation_id": "g1", "sent_ts": MS0 + i, "replies": 0, "reactions": 0,
        "edits": 0, "outcome": "open", **_UNIT_ROLLUP(i),
    },
    # Migration 0074_event_revision: the head, still updated in place (a
    # compare-and-swap cache over `event_revision`, APPEND below).
    "events": lambda i: {
        "id": _uuid("event", i), "conversation_id": "g1", "title": f"event {i}",
        "created_by": "p", "status": "proposed", "created_at": T0,
    },
    # Migration 0082: one open session per scope.
    "computer_sessions": lambda i: {
        "id": _uuid("cs", i), "platform": "signal", "conversation_id": f"g{i}",
        "scope": f"{i:02x}" * 16, "objective": f"task {i}", "status": "open",
        "created_at": T0, "updated_at": T0, "last_activity_at": T0,
        "actor": "tag", "created_by_tag": f"t{i}",
    },
    # The topic account's head, retired in place over append-only revisions.
    "notebook_entry": lambda i: {"id": f"generated-entry-{i}", "kind": "topic",
        "unit": "u", "subject": f"generated-subject-{i}", "retired": False,
        "recorded_at": T0},
    # The Profile account head advances on each write.
    "profile": lambda i: {
        "key": f"u#character:p{i}", "entity_kind": "human", "unit": "u",
        "revision": 1, "updated_at": MS0 + i,
    },
    # 0144_platform_observation: the typed history, carrying BOTH message
    # links the way production sets them — `delivered_message_id` the
    # utterance that carried the fact and `target_message_id` the message it
    # affects. `target_message_id` is SET NULL when its message is pruned,
    # and re-linked in place by the resolve pass.
    "platform_observation": lambda i: {
        "id": 7000 + i, "contract": 1, "source_kind": "reaction",
        "event_id": f"ev-{i}", "source_digest": f"digest-{i}",
        "platform": "signal", "account_key": "acct",
        "conversation_id": "g1", "object_ref": f"msg:s{i}:{MS0 + i}",
        "operation": "add",
        "target_key": f"ts:{MS0 + i}:author:s{i}",
        "target_author_id": f"s{i}",
        "target_message_id": 1000 + i, "delivered_message_id": 1000,
        "actor_id": f"s{i}", "actor_status": "known",
        "occurred_ms": MS0 + i, "received_ms": MS0 + i,
        "time_quality": "upstream",
        "payload": {"emoji": "👍", "covered_emojis": [], "covered_actors": [],
                    "counts": {}, "reactors": None},
        "coverage": {"complete": True, "covered": "event",
                     "gaps": [], "reason": ""},
    },
    # The fold's compare-and-swap head over the same object.
    "platform_object_state": lambda i: {
        "id": 7100 + i, "platform": "signal", "account_key": "acct",
        "conversation_id": "g1", "source_kind": "reaction",
        "object_ref": f"msg:s{i}:{MS0 + i}",
        "source_state": {"standing": {"👍": [f"s{i}"]}, "counts": {},
                  "reactors": [f"s{i}"], "snapshot": False},
        "applied": 1, "head_observation_id": 7000 + i,
        "observed_ms": MS0 + i, "ordering": "arrival",
        "coverage": {"complete": True, "covered": "event",
                     "gaps": [], "reason": ""},
        "projection_rev": 1,
    },
}

#: The column each revision moves, and to what.
REVISE: dict[str, tuple[str, Callable[[int], Any]]] = {
    **managed_rows.REVISE,
    "legacy_graph_arrival": ("batched", lambda i: True),
    "legacy_entity": ("name", lambda i: f"Revised generated event {i}"),
    "legacy_relation": ("status", lambda i: "superseded"),
    "legacy_relation_evidence": ("span", lambda i: f"Revised generated span {i}"),
    "legacy_memory_work_item": ("attempts", lambda i: 1),
    "registry": ("content_hash", lambda i: f"hash-{i}"),
    "entity": ("name", lambda i: f"Revised entity {i}"),
    "claim": ("content", lambda i: {"text": f"Revised claim {i}"}),
    "intention": ("review_time", lambda i: T0 + timedelta(hours=100 + i)),
    "edge": ("state", lambda i: "retired"),
    "standing_decision": ("subject", lambda i: f"Revised choice {i}"),
    "standing_commitment": ("subject", lambda i: f"Revised promise {i}"),
    "standing_thread": ("topic", lambda i: f"Revised topic {i}"),
    "extraction": ("attempts", lambda i: i + 10),
    "extraction_queue": ("source_key", lambda i: f"revised-{i}"),
    "extraction_claim": ("content", lambda i: {"what": f"revised {i}"}),
    "extraction_attempt": ("input_tokens", lambda i: i + 10),
    # The CHECK keeps the column closed: a revision cycles the legitimate
    # non-seed statuses (seeds are `pending`) instead of inventing values.
    "messages": ("delivery_status",
                 lambda i: ("sent", "failed", "uncertain")[i % 3]),
    "message_edit": ("body", lambda i: f"re-edited {i}"),
    "message_reaction": ("removed", lambda i: 1),
    "message_delete": ("ts", lambda i: MS0 + 100 + i),
    "note_candidate": ("occurrence_count", lambda i: 10 + i),
    "note": ("strikes", lambda i: 1 + i),
    "culture": ("updated_at", lambda i: MS0 + 1000 + i),
    "computer_sessions": ("objective", lambda i: f"task {i}, continued"),
    "profile": ("revision", lambda i: 2 + i),
    "standing": ("body", lambda i: f"revised position {i}"),
    "reminders": ("state", lambda i: "cancelled"),
    "compute_jobs": ("state", lambda i: "firing"),
    "instagram_summons": ("state", lambda i: "published"),
    "version_announcements": ("state", lambda i: "done"),
    "participation_window_state": ("last_window_end_at", lambda i: MS0 + 5 + i),
    "dream_schedule_state": ("last_fired_at", lambda i: MS0 + 5 + i),
    "consolidation_watermark": ("last_message_id", lambda i: 100 + i),
    "analytics_watermark": ("last_fix_id", lambda i: 100 + i),
    "tag_debounce_state": ("last_allowed_at", lambda i: MS0 + 5 + i),
    "media_objects": ("description", lambda i: f"now shows {i}"),
    "video_readings": ("reading", lambda i: f"re-read {i}"),
    "presence_fixes": ("address", lambda i: f"near {i}"),
    "music_queue": ("position", lambda i: 50 + i),
    "music_session": ("paused", lambda i: True),
    "geocode_cache": ("address", lambda i: f"renamed {i}"),
    "sticker_index": ("hits", lambda i: 7 + i),
    "fetched_pages": ("blob_key", lambda i: f"generated-page-revision-{i}"),
    "social_item": ("caption", lambda i: f"edited caption {i}"),
    "social_item_count": ("likes", lambda i: i + 10),
    "social_item_state": ("source_state", lambda i: "hidden"),
    "social_poll": ("outcome", lambda i: "read_ok"),
    "source_health": ("detail", lambda i: f"observed {i}"),
    "presence_places": ("name", lambda i: f"renamed place {i}"),
    "presence_stays": ("place_id", lambda i: 6200 + i),
    "presence_trips": ("corridor_id", lambda i: 6400 + i),
    "presence_derived_places": ("label", lambda i: f"label {i}"),
    "presence_occupancy": ("dwell_ms", lambda i: 2000 + i),
    "presence_place_rhythm": ("sample_count", lambda i: 5 + i),
    "presence_corridors": ("sample_count", lambda i: 9 + i),
    "rollup_build": ("status", lambda i: "abandoned"),
    "rollup_fold_state": ("logical_now_ms", lambda i: MS0 + 99 + i),
    "rollup_colocation": ("stay_count", lambda i: 3 + i),
    "rollup_outing": ("open", lambda i: 1),
    "rollup_daybook": ("messages", lambda i: 10 + i),
    "rollup_tie": ("replies_ba", lambda i: 1 + i),
    "rollup_response": ("outcome", lambda i: "responded"),
    "events": ("status", lambda i: "confirmed"),
    "notebook_entry": ("retired", lambda i: True),
    # 0144: the table's real in-place write — a retained observation's
    # target re-linking (`resolve_targets`/`relink_arriving_targets`).
    "platform_observation": ("target_message_id", lambda i: 1000),
    # The fold's head re-folding (`_apply_head`'s UPDATE).
    "platform_object_state": ("source_state", lambda i: {
        "standing": {"👀": [f"s{i}"]}, "counts": {},
        "reactors": [f"s{i}"], "snapshot": False}),
}

#: One append-only row, number `n`, citing the stable key-0 parents.
APPEND: dict[str, Callable[[int], dict[str, Any]]] = {
    **managed_rows.APPEND,
    "graph_stimulus": lambda n: {
        "unit": "u", "kind": "new_item", "graph_id": f"generated-stimulus-item-{n}",
        "eligible_at": T0, "source": "message",
    },
    "nora_act": lambda n: {
        "id": f"generated-act-{n}", "graph_id": f"generated-act-item-{n}", "unit": "u",
        "executor": "test.deliver", "trigger_id": f"generated-trigger-{n}", "ordinal": 0,
        "destination": "test-destination", "act_kind": "delivery", "guarantee": "unknown",
    },
    "nora_act_outcome": lambda n: {
        "id": n, "act_id": f"generated-act-{n}", "unit": "u",
        "graph_id": f"generated-outcome-item-{n}", "verdict": "done",
    },
    "nora_pass_output": lambda n: {
        "id": f"generated-output-{n}", "graph_id": f"generated-output-item-{n}", "unit": "u",
        "pass": f"generated-pass-{n}", "output_kind": "expression", "content": ["Generated draft"],
    },
    "notebook_entry_revision": lambda i: {
        "id": f"generated-entry-revision-{i}", "entry_id": "generated-entry-0", "seq": i,
        "base_revision_id": f"generated-entry-revision-{i - 1}" if i > 1 else None,
        "content": {
            "about": [["topic", f"generated-topic-{i}"]], "body": f"Generated account {i}",
            "citations": [["message", "1000"]], "confidence": "medium", "state": "active",
            "writer": "cognition", "session_id": "generated-session", "model": "generated-model",
            "instruction_hash": "a" * 64, "links": [],
        },
    },
    "legacy_graph_refusal": lambda n: {"id": 86000+n, "kind": "relation",
        "part_id": f"generated-{n}", "reason": "vocabulary"},
    "comprehend_web_page": lambda n: {
        "id": f"generated-page-{n}", "unit": "u", "session_id": n,
        "url": f"https://example.invalid/page-{n}", "fetched_at": T0,
        "content_hash": f"generated-hash-{n}", "text": f"generated text {n}", "tool_call": {},
    },
    "comprehend_result": lambda n: {
        "work_id": f"generated-work-{n}", "input_hash": f"generated-hash-{n}",
        "session_id": n, "part": "reshape", "payload": {"would_writes": [f"generated {n}"]},
    },
    "standing_decision_revision": lambda n: _ledger_row("decision", n),
    "standing_commitment_revision": lambda n: _ledger_row("commitment", n),
    "standing_thread_revision": lambda n: _ledger_row("thread", n),
    "note_revision": lambda n: {
        "id": _uuid("nr", n), "note_id": _uuid("note", 0), "body": f"body {n}",
        "written_at": MS0 + n, "established_by": "test", "citations": [],
    },
    "standing_revision": lambda n: {
        "platform": "signal", "conversation_id": "g0", "body": f"pos {n}",
        "fold_count": n, "updated_at": MS0 + n,
    },
    "culture_revision": lambda n: {
        "id": _uuid("cr", n), "culture_key": "room0", "body": f"account {n}",
        "written_at": MS0 + n, "established_by": "test",
    },
    "decision_log": lambda n: {
        "id": f"d{n}", "writer": "tag", "platform": "signal", "conversation_id": "g1",
        "verdict": "replied", "ts": MS0 + n,
    },
    "note_merge_decision": lambda n: {
        "id": _uuid("nm", n), "unit": "u", "mode": "shadow", "outcome": "would_merge",
        "survivor_id": _uuid("note", 0), "duplicate_id": _uuid("note", 0),
        "survivor_title": "a", "duplicate_title": "b", "established_by": "t", "ts": MS0 + n,
    },
    "tool_call_log": lambda n: {
        "id": _uuid("tc", n), "conversation_id": "g1", "conversation_type": "group",
        "tag_id": f"t{n}", "tool_name": "now", "tool_input": {}, "tool_result": {"n": n},
    },
    "computer_events": lambda n: {
        "id": _uuid("ce", n), "operation_id": _uuid("ce-op", n), "phase": "finished",
        "started_at": T0, "finished_at": T0, "actor": "tag",
        "platform": "signal", "conversation_id": "g1", "conversation_type": "group",
        "tag_id": f"t{n}", "scope": "ab" * 16, "tool": "computer_run", "path": "direct",
        "trace_id": "", "span_id": "", "status": "ok",
        "request": {"command": f"echo {n}"},
    },
    # ADR 0020 (0084_documents): a document's identity and one snapshot per revision.
    "documents": lambda n: {
        "id": _uuid("doc", n), "platform": "signal", "conversation_id": "g1",
        "created_by": "ken",
    },
    "document_revision": lambda n: {
        "document_id": _uuid("doc", n), "revision": 1, "title": f"doc {n}",
        "spec": {"version": 1}, "text": f"doc {n}", "sha256": f"{n:064x}",
        "written_by": "tag",
    },
    # 0087_document_state (DEC-255): one row per change of a document's state.
    "document_state": lambda n: {
        "document_id": _uuid("doc", n), "state": "archived", "set_by": "ken",
    },
    # 0097_otp (DEC-287): a one-time code, and one check of it.
    "otp_challenge": lambda n: {
        "challenge_id": _uuid("otp", n), "person_id": "ken", "purpose": "sign_in",
        "platform": "signal", "code_hash": f"{n:064x}", "issued_at": T0,
        "expires_at": T0 + timedelta(minutes=5),
    },
    "otp_attempt": lambda n: {
        "challenge_id": _uuid("otp", n), "ok": False, "attempted_at": T0,
    },
    # 0100_lessons: a lesson, and one revision of it.
    "lesson": lambda n: {"id": _uuid("ls", n), "unit": "friends", "created_at": MS0 + n},
    "lesson_revision": lambda n: {
        "id": _uuid("lr", n), "lesson_id": _uuid("ls", n), "seq": 1, "body": f"lesson {n}",
        "citations": [str(n + 1)], "state": "active", "written_at": MS0 + n,
        "established_by": "test",
    },
    # One told Profile line.
    "profile_line": lambda n: {
        "id": _uuid("pl", n), "profile_key": f"u#character:p{n}", "part": "facts",
        "kind": "attribute", "text": f"fact {n}", "mode": "told", "citations": [str(n + 1)],
        "told_by": f"p{n}", "state": "current", "support_count": 1, "unit": "u",
        "written_at": MS0 + n, "written_by_session": "test",
    },
    # One dispute of a Profile line.
    "profile_dispute": lambda n: {
        "id": _uuid("pd", n), "profile_key": f"u#character:p{n}", "line_id": None,
        "disputed_by": f"p{n}", "kind": "dispute", "claim": f"not so {n}",
        "message_id": n + 1, "unit": "u", "written_at": MS0 + n,
    },
    # 0103_memos (DEC-306): a kept memo, and one revision of it.
    "memo": lambda n: {
        "id": _uuid("mm", n), "unit": "friends", "platform": "signal",
        "conversation_id": "g1", "created_at": MS0 + n,
    },
    "memo_revision": lambda n: {
        "id": _uuid("mr", n), "memo_id": _uuid("mm", n), "seq": 1, "title": f"memo {n}",
        "body": f"## Kept\n- kept {n} [{n + 1}]", "citations": [str(n + 1)], "mode": "told",
        "state": "kept", "written_at": MS0 + n, "established_by": "test",
    },
    # 0108_social_perception: one attempt to perceive an item's media.
    "social_perception": lambda n: {
        "id": _uuid("sv", n), "network": "instagram", "item_id": f"item{n}",
        "person": "ken", "units": ["friends"], "origin": "poll", "outcome": "ok",
        "calls": 1, "at": T0,
    },
    # 0109_blob_tombstone: a story frame's bytes deleted at their bound.
    "blob_tombstone": lambda n: {
        "id": _uuid("bt", n), "sha256": f"{n:064x}", "kind": "story_frame", "reason": "hard_limit",
        "deleted_at": T0,
    },
    # 0101_self_tension: a lesson refused by the law.
    "self_tension": lambda n: {
        "id": _uuid("st", n), "unit": "friends", "source": "lesson",
        "doctrine_file": "rooms/conduct.md", "doctrine_line": n + 1,
        "doctrine_text": f"rule {n}", "would_have_said": f"lesson {n}",
        "citations": [f"response:{n + 1}"], "why": "it loosens the rule",
        "written_at": MS0 + n, "established_by": "test",
    },
    # Migration 0083: one row per file that left the computer.
    "computer_exports": lambda n: {
        "id": _uuid("cx", n), "platform": "signal", "conversation_id": "g1",
        "scope": "ab" * 16, "path": f"out/{n}.png", "destination": "link",
        "sha256": f"{n:064x}", "size_bytes": n, "media_type": "image/png",
        "title": f"page {n}", "tag_id": f"t{n}", "operation_id": _uuid("cx-op", n),
        "artifact_id": f"art_{n}", "url": f"https://example.invalid/{n}",
    },
    "revision_blob": lambda n: {"sha256": f"{n:064x}", "content": f"text {n}"},
    "doctrine_revision": lambda n: {
        "corpus": "doctrine", "content_sha256": f"{n:064x}", "files": {}, "loaded_at": T0,
    },
    "config_revision": lambda n: {
        "file": "policy.toml", "path": "/p", "content_sha256": f"{n:064x}", "loaded_at": T0,
    },
    "deploy_revision": lambda n: {"started_at": T0, "manifest_status": "absent"},
    # Migration 0069/0070 (DEC-234): the cognition loop's stimuli, wake log and source
    # health.
    "stimulus": lambda n: {
        "unit": "u", "kind": "message", "item": "message", "ref": f"messages:{n}",
        "at_ms": MS0 + n,
    },
    "cognition_wake": lambda n: {
        "unit": "u", "mode": "live", "reason": "settled", "from_stimulus_id": 0,
        "through_stimulus_id": n, "stimulus_count": 1, "eligible_count": 1, "at_ms": MS0 + n,
    },
    # Migration 0074_event_revision: the chain `events` is a compare-and-swap cache
    # over, and RSVPs, append-only.
    "event_revision": lambda n: {
        "id": _uuid("evr", n), "event_id": _uuid("event", 0), "status": "proposed",
        "title": f"revision {n}", "written_at": T0,
    },
    "event_rsvps": lambda n: {
        "id": _uuid("rsvp", n), "event_id": _uuid("event", 0), "person_id": f"p{n}",
        "response": "yes", "responded_at": T0,
    },
    # Migration 0077_assess_report: the Assess adaptor's one store.
    "assess_report": lambda n: {
        "kind": "nightly_dq", "run_at_ms": MS0 + n,
        "results": [{"check": "cross_room_duplicate_events", "status": "ok"}],
    },
    # Migration 0078_cognition_session: the Cognition adaptor's session and
    # its write verbs. `session_id` on the other tables is left unset
    # (nullable, no FK — see the migration's own FOREIGN KEYS paragraph):
    # this fixture appends each table's row independently, with no ordering
    # guarantee that a session row for a given id already exists.
    "cognition_session": lambda n: {
        "unit": "u", "mode": "live", "started_at_ms": MS0 + n,
        "from_stimulus_id": 0, "through_stimulus_id": n,
        "situation": {"unit": "u"}, "agenda": {"items": []},
    },
    "cognition_session_close": lambda n: {
        "item_index": 0, "kind": "message", "reason": "written",
    },
    # The pace verbs. `session_id` left unset (null, FK-nullable)
    # for the same reason as `cognition_session_close` above.
    "cognition_self_wake": lambda n: {
        "unit": "u", "verb": "wake_at", "wake_ref": f"w{n}", "transition": "created",
        "subject_rows": [f"event:{n}"], "why": "check the venue again", "at_ms": MS0 + n,
        "outcome": "recorded",
    },
    # The session lifecycle (no foreign keys, see schema.py).
    "cognition_wake_outcome": lambda n: {
        "wake_id": n, "unit": "u", "outcome": "closed", "at_ms": MS0 + n,
    },
    "cognition_carry": lambda n: {
        "unit": "u", "stimulus_id": n, "strike": 1, "state": "carried", "wake_id": n,
    },
    "stimulus_retraction": lambda n: {
        "unit": "u", "stimulus_id": n, "reason": "cancelled",
    },
    "stimulus_claim": lambda n: {
        "unit": "u", "stimulus_id": n, "wake_id": n,
    },
}

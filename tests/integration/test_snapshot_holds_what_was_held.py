"""Write, revise and retire across every store migrated here, snapshot at two
moments, and get EXACTLY what was held at each.

WHAT IS HELD is read from the live tables at each moment (`to_jsonb` of
every row) — the thing the restore claims to reproduce, observed directly,
never through `row_history`. The restore is then built after the source has
moved on, into a sandbox database of its own (`sandbox_as_of`, the harness's
per-case database), and read back the same way. Equality is of whole row
sets, append-only tables included, so a row too many, a row too few or one
column carrying a later value all fail.

Per in-place table, six rows keyed 0..5 walk every transition:

- 0 written before t1 and never touched (the parent append-only rows cite);
- 1 written before t1, revised between t1 and t2, retired after t2;
- 2 written before t1, retired between t1 and t2;
- 3 written, revised and retired all before t1;
- 4 written between t1 and t2, revised after t2;
- 5 written after t2.

Append-only tables get one row in each of the three stretches.

And one case no store asks for: an edit of message 2, which is pruned
between t1 and t2. `message_edit.message_id` is ON DELETE CASCADE, so the
prune deletes the edit with no statement naming it; the edit must be held at
t1, gone at t2, and kept in history as hidden.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa

from nora.infra.sql import create_sql_engine
from nora.infra.sql.history import APPEND_ONLY, HISTORY_TABLES
from nora.infra.sql.snapshot_at import copied_tables
from nora.subject import sandbox_as_of

T0 = datetime(2026, 9, 1, tzinfo=UTC)
MS0 = int(T0.timestamp() * 1000)

#: The edit of message 2 that the prune cascades away.
PRUNED_EDIT = 3100


def _uuid(table: str, i: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{table}:{i}"))


def _hex(table: str, i: int) -> str:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"{table}:{i}").hex


#: What every presence Rollup row carries (0071).
_ROLLUP: dict[str, Any] = {"build_id": 1, "lineage": [["presence", "s", None]]}


def _UNIT_ROLLUP(i: int) -> dict[str, Any]:
    return {
        "build_id": 1, "unit": "u", "lineage": [["presence", "a", None]],
        "valid_from_ms": MS0 + i, "valid_to_ms": MS0 + 1000 + i, "as_of_ms": MS0 + i,
    }


#: A valid row per in-place table, for key `i`.
ROWS: dict[str, Callable[[int], dict[str, Any]]] = {
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
    "culture": lambda i: {
        "key": f"room{i}", "head_revision_id": _uuid("cr", 100 + i), "updated_at": MS0,
    },
    "standing": lambda i: {
        "platform": "signal", "conversation_id": f"g{i}", "body": f"position {i}",
        "updated_at": MS0,
    },
    "reminders": lambda i: {
        "id": _hex("rem", i), "requester_person_id": "p", "platform": "signal",
        "conversation_id": "g1", "conversation_type": "group", "text": f"r{i}",
        "created_at": T0, "due_at": T0 + timedelta(hours=1), "state": "pending",
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
    "presence_places": lambda i: {
        "circle_id": "c", "place_id": f"pl{i}", "name": f"place {i}", "lat": 22.25,
        "lng": 114.125, "updated_ms": MS0 + i,
    },
    "presence_stays": lambda i: {
        "id": 6000 + i, "subject_id": "s", "person_id": "s", "circle_id": "c",
        "started_ts": MS0 + i, "ended_ts": MS0 + 600_000 + i, "lat": 22.25, "lng": 114.125,
        "radius_m": 50.0, "fix_count": 3, **_ROLLUP,
    },
    "presence_trips": lambda i: {
        "id": 6100 + i, "subject_id": "s", "person_id": "s", "circle_id": "c",
        "started_ts": MS0 + i, "ended_ts": MS0 + 60_000 + i, "distance_m": 500.0,
        "avg_speed_kmh": 30.0, "fix_count": 3, "sequence": [], **_ROLLUP,
    },
    "presence_derived_places": lambda i: {
        "id": 6200 + i, "subject_id": "s", "circle_id": "c", "lat": 22.25, "lng": 114.125,
        "radius_m": 100.0, "kind": "home", "confidence": 0.5, "first_seen_ts": MS0,
        "last_seen_ts": MS0, **_ROLLUP,
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
        "id": 6500 + i, "persons": ["a", "b"], "lat": 22.25, "lng": 114.125,
        "radius_m": 50.0, "stay_count": 2, **_UNIT_ROLLUP(i),
    },
    "rollup_outing": lambda i: {
        "id": 6600 + i, "persons": ["a", "b"], "lat": 22.25, "lng": 114.125,
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
    # 0117_persona: an account's head, its revision bumped by each write.
    "profile": lambda i: {
        "key": f"u#character:p{i}", "entity_kind": "human", "unit": "u",
        "revision": 1, "updated_at": MS0 + i,
    },
}

#: The column each revision moves, and to what.
REVISE: dict[str, tuple[str, Callable[[int], Any]]] = {
    "extraction": ("attempts", lambda i: i + 10),
    "extraction_queue": ("source_key", lambda i: f"revised-{i}"),
    "extraction_claim": ("content", lambda i: {"what": f"revised {i}"}),
    "extraction_attempt": ("input_tokens", lambda i: i + 10),
    "messages": ("delivery_status", lambda i: f"delivered-{i}"),
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
}

#: One append-only row, number `n`, citing the stable key-0 parents.
APPEND: dict[str, Callable[[int], dict[str, Any]]] = {
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
    # 0117_persona: one told line.
    "profile_line": lambda n: {
        "id": _uuid("pl", n), "profile_key": f"u#character:p{n}", "part": "facts",
        "kind": "attribute", "text": f"fact {n}", "mode": "told", "citations": [str(n + 1)],
        "told_by": f"p{n}", "state": "current", "support_count": 1, "unit": "u",
        "written_at": MS0 + n, "written_by_session": "test",
    },
    # 0121_persona_dispute: one dispute of a Profile line.
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
    # 0107_social_stream: an item, a probe of it, and a state it was given.
    "social_item": lambda n: {
        "id": _uuid("si", n), "network": "instagram", "item_id": f"item{n}",
        "person": "ken", "account": "10000001", "kind": "post", "taken_at": T0,
        "seen_at": T0, "origin": "poll",
    },
    "social_poll": lambda n: {
        "id": _uuid("sp", n), "network": "instagram", "person": "ken", "stage": "probe",
        "outcome": "unchanged",
    },
    "social_item_state": lambda n: {
        "id": _uuid("ss", n), "network": "instagram", "item_id": f"item{n}", "state": "gone",
    },
    # 0108_social_perception: one attempt to perceive an item's media.
    "social_perception": lambda n: {
        "id": _uuid("sv", n), "network": "instagram", "item_id": f"item{n}",
        "person": "ken", "units": ["friends"], "origin": "poll", "outcome": "ok",
        "calls": 1, "at": T0,
    },
    # 0119_social_item_count: an item's counts as one read carried them.
    "social_item_count": lambda n: {
        "id": _uuid("sc", n), "network": "instagram", "item_id": f"item{n}",
        "likes": n, "comments": 1, "likes_hidden": False,
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
    "source_health": lambda n: {
        "source": "presence", "scope": "c1", "event": "poll", "outcome": "ok",
        "at_ms": MS0 + n,
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
}


def test_the_fixture_covers_every_restorable_table() -> None:
    assert set(ROWS) == set(REVISE) == set(HISTORY_TABLES)
    assert set(APPEND) == set(APPEND_ONLY)


def _table(name: str) -> sa.Table:
    from nora.infra.sql.schema import METADATA
    return METADATA.tables[name]


def _key(table: str, row: dict[str, Any]) -> list[Any]:
    t = _table(table)
    return [t.c[c] == row[c] for c in HISTORY_TABLES[table]]


async def _write(conn: Any, table: str, i: int) -> None:
    await conn.execute(sa.insert(_table(table)).values(ROWS[table](i)))


async def _revise(conn: Any, table: str, i: int) -> None:
    column, value = REVISE[table]
    await conn.execute(
        sa.update(_table(table)).where(*_key(table, ROWS[table](i))).values({column: value(i)})
    )


async def _retire(conn: Any, table: str, i: int) -> None:
    await conn.execute(sa.delete(_table(table)).where(*_key(table, ROWS[table](i))))


async def _append(conn: Any, table: str, n: int) -> None:
    await conn.execute(sa.insert(_table(table)).values(APPEND[table](n)))


async def _held(engine: Any) -> dict[str, list[str]]:
    """Every restorable table's rows, as canonical JSON, sorted."""
    out: dict[str, list[str]] = {}
    async with engine.connect() as conn:
        for table in copied_tables():
            images = (await conn.execute(
                sa.text(f'SELECT to_jsonb(t)::text FROM "{table}" t')  # noqa: S608 — module constant
            )).scalars().all()
            out[table] = sorted(json.dumps(json.loads(i), sort_keys=True) for i in images)
    return out


async def _clock(engine: Any) -> datetime:
    async with engine.connect() as conn:
        return (await conn.execute(sa.text("SELECT clock_timestamp()"))).scalar_one()


@pytest.mark.asyncio
async def test_a_snapshot_at_each_moment_holds_exactly_what_was_held(
    pg_url: str, pg_template: str,
) -> None:
    engine = create_sql_engine(pg_url)
    tables = [t for t in copied_tables() if t in HISTORY_TABLES]
    try:
        async with engine.begin() as conn:  # before t1
            # `culture.head_revision_id` is a foreign key: its revisions first.
            for i in range(6):
                await _append(conn, "culture_revision", 100 + i)
            for table in tables:
                for i in (0, 1, 2, 3):
                    await _write(conn, table, i)
            await conn.execute(sa.insert(_table("message_edit")).values(
                id=PRUNED_EDIT, message_id=ROWS["messages"](2)["id"], ts=MS0, body="edited",
            ))
        async with engine.begin() as conn:
            for table in tables:
                await _revise(conn, table, 3)
                await _retire(conn, table, 3)
            for table in APPEND:
                await _append(conn, table, 1)
        t1 = await _clock(engine)
        held_t1 = await _held(engine)

        async with engine.begin() as conn:  # between t1 and t2
            for table in tables:
                await _revise(conn, table, 1)
                await _retire(conn, table, 2)
                await _write(conn, table, 4)
            for table in APPEND:
                await _append(conn, table, 2)
        t2 = await _clock(engine)
        held_t2 = await _held(engine)

        async with engine.begin() as conn:  # after t2
            for table in tables:
                await _revise(conn, table, 4)
                await _retire(conn, table, 1)
                await _write(conn, table, 5)
            for table in APPEND:
                await _append(conn, table, 3)
        now = await _held(engine)
    finally:
        await engine.dispose()

    # Positive control: every table moved at each step, so a restore that
    # returned today's rows, or t1's at t2, cannot pass.
    for table in (*tables, *APPEND):
        assert held_t1[table] != held_t2[table] != now[table], table

    def edits(held: dict[str, list[str]]) -> set[int]:
        return {json.loads(r)["id"] for r in held["message_edit"]}

    assert PRUNED_EDIT in edits(held_t1) and PRUNED_EDIT not in edits(held_t2)
    engine = create_sql_engine(pg_url)
    try:
        async with engine.connect() as conn:
            hidden = (await conn.execute(sa.text(
                "SELECT op FROM row_history WHERE table_name = 'message_edit' "
                "AND row_key = jsonb_build_object('id', CAST(:i AS integer)) "
                "AND superseded_at IS NULL"
            ), {"i": PRUNED_EDIT})).scalar_one()
    finally:
        await engine.dispose()
    assert hidden == "delete"  # the cascade was recorded: hidden, and kept

    for at, held in ((t1, held_t1), (t2, held_t2)):
        async with sandbox_as_of(pg_url, at, template=pg_template) as (url, report):
            restored_engine = create_sql_engine(url)
            try:
                restored = await _held(restored_engine)
            finally:
                await restored_engine.dispose()
        assert report.orphaned == {}
        for table in copied_tables():
            assert restored[table] == held[table], f"{table} at {at.isoformat()}"


@pytest.mark.asyncio
async def test_a_history_version_missing_a_later_column_restores_it_as_null(
    pg_url: str, pg_template: str,
) -> None:
    """0075_withdrawal added `hidden_at` to `messages` with no row_history
    catch-up: a version recorded before that migration has no `hidden_at` key in
    its `image` at all, never a `null` value. A restore to an instant covered by
    such a version must still produce a row — with `hidden_at` NULL, the column's
    own default for a row nothing ever hid — not an error and not a
    spuriously-hidden one."""
    engine = create_sql_engine(pg_url)
    try:
        async with engine.begin() as conn:
            await _write(conn, "messages", 0)
        t1 = await _clock(engine)
        async with engine.begin() as conn:
            # Simulate a pre-0075 version: strip `hidden_at` from the image
            # `row_history` already holds, rather than leaving it `null` —
            # a key ABSENT is what every version recorded before the
            # migration actually looks like.
            await conn.execute(sa.text(
                "UPDATE row_history SET image = image - 'hidden_at' "
                "WHERE table_name = 'messages' "
                "AND row_key = jsonb_build_object('id', CAST(:i AS bigint))"
            ), {"i": ROWS["messages"](0)["id"]})
    finally:
        await engine.dispose()

    async with sandbox_as_of(pg_url, t1, template=pg_template) as (url, report):
        restored_engine = create_sql_engine(url)
        try:
            async with restored_engine.connect() as conn:
                hidden_at = (await conn.execute(sa.text(
                    'SELECT hidden_at FROM "messages" WHERE id = :i'
                ), {"i": ROWS["messages"](0)["id"]})).scalar_one_or_none()
        finally:
            await restored_engine.dispose()
    assert report.orphaned == {}
    assert hidden_at is None

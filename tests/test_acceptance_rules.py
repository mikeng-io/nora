"""Every rule in `RULE_TESTS` has a passing named test."""

import hashlib
import re
import subprocess
from pathlib import Path

from nora.agent.egress import scan_identifiers
from nora.domain.media import MediaBlob, MediaObject, MediaRef
from nora.domain.media_join import MediaJoin
from nora.domain.models import (
    NormalizedMessage,
    PersonRef,
)
from nora.domain.ports import EngageDecision, EngagementRequest
from nora.engagement.context import TranscriptInjection
from nora.ingest.comprehension import MediaComprehension, Subject
from nora.ingest.stages import media as media_stage
from nora.obs.metrics import Metrics
from nora.persons import Persons
from nora.platform.registry import MediaSourceRegistry
from nora.testing import (
    FakeBlobStore,
    FakeMediaInterpreter,
    FakeMediaObjectStore,
    FakeMediaSource,
    FakeMemoryStore,
    FakeRecordStore,
)

# R-90's fixture: one real image, and a roster that can name its sender — the
# assembler resolves the transcript's speakers before the media join runs.
_R90_BYTES = b"\x89PNG r-89 pixels"
_R90_SHA = hashlib.sha256(_R90_BYTES).hexdigest()
_R90_PERSONS = Persons(
    by_person={"miles": PersonRef(
        person_id="miles", name="Miles", honcho_peer_id="miles")},
    index={("signal", "+85212345678"): "miles"},
)

ROOT = Path(__file__).resolve().parents[1]

# The named test that enforces each rule.
RULE_TESTS = {
    # Replace this draft key with the DEC number allocated immediately before merge.
    "policy-hot-reload": "test_policy_reload_admits_the_next_message_from_a_new_room",
    # R-1: untagged ambient traffic reaches the agent ONLY under the participation
    # occasion's gating: an operator-opted-in room, one quiescence-closed window,
    # one evaluation through `FirePath.speak`. The named test pins that ordinary
    # ambient traffic constructs ZERO agents; its sibling pins the exception's
    # gating.
    "R-1": "test_ambient_reaches_the_agent_only_through_participation_gating",
    # R-1b: the send-path clause of R-1. `nora.app.fire.FirePath` makes "only the
    # tagging path may cause a send" false — a fire turn is a send not caused by
    # a tag. R-1's test harnesses only ambient input, so it stays green no matter
    # how many non-tag send paths exist and is no brake on this clause. R-1b is
    # that brake: `Deliverer` is the only caller of `adapter.send`, it is
    # constructed only by `deliver.build`, and `deliver.build` has exactly two
    # callers — the tag pipeline and the fire path. The participation occasion
    # changes NOTHING here: it goes through `FirePath.speak`, so the two callers
    # remain two.
    "R-1b": "test_only_the_tag_path_and_the_fire_path_can_send",
    "R-2": "test_send_reply_past_the_cap_is_cancelled",
    "R-2b": "test_deliverer_latch_is_single_shot",
    "R-3": "test_send_to_other_group_cannot_escape_the_tagged_conversation",
    "R-4": "test_empty_reply_not_sent",
    "R-5": "test_silent_failure_matrix",
    "R-6": "test_leak_guard_blocks_canary",
    "R-6b": "test_leak_guard_blocks_doctrine_verbatim",
    "R-6c": "test_leak_guard_allows_normal_reply",
    "R-7": "test_tool_call_budget_enforced_for_load_transcript",
    "R-8": "test_limits_passed_through",
    "R-9": "test_debounce_window",
    "R-10": "test_concurrent_tags_same_conversation",
    "R-10b": "test_lock_released_on_timeout",
    "R-10c": "test_lock_released_on_llm_error",
    "R-11": "test_trigger_matrix",
    "R-12": "test_self_tag_ignored",
    "R-13": "test_prefill_escapes_angle_brackets",
    "R-13b": "test_forged_closing_tag_is_escaped",
    # R-13c: R-13's rule on the LINE axis rather than the tag axis, a separate
    # key because the two R-13 tests never asserted it. `escape` closes the block
    # boundary; a newline in a row's body forges a whole ROW inside the block,
    # with its own `#n` handle and somebody else's name on it. The brake is the
    # renderer's, not the instruction's.
    #
    # TWO shapes close it. On a LINE, the brake is `nora.domain.prompt.one_line`
    # — applied by `render_rows` to every accessor, on every one of the eight
    # line grammars in the tree, and
    # `tests/architecture/test_one_row_renders_one_line.py` is what stops the
    # ninth shipping without it. In the tag prefill's `<group_transcript>` there
    # are no lines: `render_messages` gives each message its own `<m>` element,
    # so the forgery needs a `</m>` and `escape` is what refuses it. The named
    # test below reads that block, so it asserts the second shape.
    "R-13c": "test_a_message_cannot_forge_another_message",
    "R-15": "test_no_redundant_read_fails_when_prefill_was_sufficient",
    "R-16": "test_secret_never_logged_in_the_message",
    "R-18": "test_a_reply_carrying_someones_phone_number_is_refused",
    "R-17": "test_degraded_identity_enrichment_is_counted_and_r12_still_holds",
    "R-20": "test_a_forged_quote_cannot_put_words_in_someone_elses_mouth",
    "R-21": "test_not_being_here_is_indistinguishable_from_not_existing",
    "R-22": "test_nothing_known_is_distinguishable_from_could_not_ask",
    "R-23": "test_a_budget_stop_is_distinguishable_from_a_silent_model",
    "R-24": "test_no_single_call_can_spend_the_whole_turn_budget",
    "R-27": "test_an_unknown_route_is_never_reported_as_no_service",
    # R-27's own rule on the place axis, not a new rule: «that place is not in
    # the stop data» and «that place exists but this route does not go there»
    # are different claims, and collapsing them is the same wrong-source-read-
    # as-nothing-found trap R-27 was registered for.
    "R-27b": "test_a_place_the_route_does_not_serve_is_never_no_such_place",
    # R-28: the OUTBOUND egress boundary for routing. A route query contains two
    # locations that leave the host to Google; a person's name or a resolved
    # position in origin/destination is a hard block, refused before any HTTP
    # call (DEC-46).
    "R-28": "test_a_route_naming_a_person_is_refused_before_egress",
    # R-2/R-2b: DEC-40 made `send_reply`'s budget the rule (not one message), and
    # both named tests assert that invariant. A new key would leave the old one
    # pointing at a rule nobody enforces.
    "R-25": "test_a_turn_cut_off_between_messages_is_counted_as_partial",
    # R-29: a skill source is operator configuration only. `SkillStore.resolve`
    # is a pure name lookup, so a message can name a skill the operator shipped
    # (the intended activation) but can never introduce a new source — item 6
    # can never reach item 5 (DEC-48).
    "R-29": "test_a_message_can_name_a_skill_but_never_load_one",
    # R-30: a second tag for a conversation already handling one is QUEUED, not
    # dropped — three people tagging at once each get a reply, in FIFO order,
    # while different conversations still run concurrently (DEC-48). See
    # `nora.engagement.queue`.
    "R-30": "test_three_tags_for_one_conversation_are_answered_in_fifo_order",
    # R-31: the presence disclosure seam is fail-closed (DEC-54). `whereis`
    # refuses to disclose a circle member's position into a conversation whose
    # people are NOT a subset of the circle — a REFUSAL, not a silent omission.
    # This is the INVERSE of the room tools' R-21, and nothing else in Nora
    # enforces it.
    "R-31": "test_whereis_refuses_to_disclose_outside_the_circle",
    # R-32: the collector is a change-recorder, not a sampler (DEC-55). A row is
    # written only when a member's `ts` advances, so two reads of an UNMOVED
    # member produce ONE row — the property that makes presence a time-series of
    # distinct fixes rather than a duplication of every poll.
    "R-32": "test_an_unmoved_member_is_recorded_once",
    # R-33: media_fetch reads a stored image back through MiMo-V2.5, scoped to
    # THIS conversation's custody over a bounded window, cached by content hash
    # and screened on every serve, under its own vision timeout (derived from
    # llm.timeout_seconds, provably < the loop budget) and byte ceiling. The
    # MiMo description is untrusted egress — identifier-screened like `about`,
    # with the honest caveat that a bare local number is a residual risk equal
    # to R-18's. A describable modality with no describer configured is
    # `unavailable` (a wiring gap, R-27); only a blob no describer reads is
    # `not_an_image` (DEC-114). Inert-by-config behind [media] enabled. See
    # DEC-56.
    "R-33": "test_a_stored_image_is_read_only_within_scope_and_screened_on_serve",
    # R-34 is retired (DEC-73); the number is not reused.
    # R-35: saved-place resolution is subset-scoped and falls through, never
    # refuses (DEC-60). A room that is not a verified subset of
    # the circle resolves NO household place name — silently, so an outsider
    # cannot learn a guarded name EXISTS (the deliberate inverse of R-31's loud
    # refusal: there the subject is a person's position and silence would lie;
    # here the dictionary entry's existence is itself the secret).
    "R-35": "test_a_non_subset_room_resolves_no_saved_place",
    # R-36…R-39: reminders + the heartbeat (DEC-70).
    #
    # R-36: a reminder NEVER fires early. The tick's batch is `due_at <= now` and
    # nothing else — no rounding, no "close enough", no catch-up window. It is
    # the first rule because it is the one a scheduler gets wrong quietly: firing
    # a minute early is a message that arrives before the thing it is about, and
    # nobody reports it as a bug.
    "R-36": "test_a_row_not_yet_due_does_not_fire",
    # R-37: a row fires ONCE, ever. Taking a row is a compare-and-set on
    # `state = 'pending'` inside the statement, not a read-then-write: `due()` is
    # a query rather than a claim, and the tick awaits a turn per group before it
    # reaches the later rows, so a cancel landing in that window must win.
    "R-37": "test_claim_is_a_compare_and_set_from_pending",
    # R-37b: the same rule across a CRASH, where it is a decision rather than a
    # mechanism. A row left FIRING resolves to FAILED and is never
    # re-queued: nothing links a row to the message it may already have
    # delivered, so re-queueing double-sends anything that died after egress.
    # At-most-once — losing one reminder visibly beats saying it twice.
    "R-37b": "test_sweep_resolves_firing_rows_to_failed",
    # R-38: only a MAPPED person may create a reminder. `requester_person_id` is
    # what the fire-time policy re-check runs against, so an unmapped sender
    # has no owner to write and inventing one would put unprompted speech into a
    # room under nobody's name. Enforced at `remind()` and re-checked at fire
    # time, because identity.toml loads once at startup and the horizon is 30 days.
    "R-38": "test_remind_refuses_an_unmapped_requester",
    # R-39: `remind` refuses on a FIRE TURN — the structural bar on recurrence.
    # Without it Nora re-arms her own reminder each time one comes due, and none
    # of the reminder caps can stop her: `max_pending_per_requester` counts
    # PENDING rows and the fire has already freed one before the replacement is
    # written. The flag is turn PROVENANCE (`fire_turn`), threaded from
    # `ToolRegistry.build`, because a fire turn is otherwise a well-formed turn
    # in the same conversation and no tool can tell the two apart.
    "R-39": "test_remind_refuses_on_a_fire_turn",
    # R-40: an MCP server's tools reach `Agent(tools=...)` for a tag only when
    # `policy.toml`'s per-conversation `tools` allowlist names that server —
    # resolved and enforced at ASSEMBLY (`EngagementPipeline._run_admitted`
    # calling `ToolRegistry.build(..., allowed_mcp=...)`), never at egress
    # (DEC-71).
    "R-40": "test_unauthorized_conversation_never_receives_pulse_tools",
    # R-41: matching an unlabeled entry in something looked up (a handle, an
    # id) to the person Nora is talking to requires an actual link — never
    # inferred from rank or list order (DEC-75). Behavioural, same shape as
    # R-14: asserted by conduct.md, measured by the adversarial corpus — NOT
    # a currently-passing gate (DEC-75's own text records the measured
    # failure rate; the test below only pins that the corpus exists).
    "R-41": "test_r41_is_covered_by_the_identity_grounding_corpus",
    # R-42 is retired (DEC-314); the number is not reused.
    # R-43: a compute job is delivered AT MOST ONCE, ever — the same discipline
    # R-37/R-37b hold for reminders, applied to the third Occasion (DEC-99).
    "R-43": "test_a_job_never_delivered_twice",
    # R-44 and R-45 are retired; the numbers are not reused.
    # R-46: a reply leaves on the platform its tag arrived on; an unroutable
    # platform refuses rather than falling back.
    "R-46": "test_a_discord_tag_is_answered_on_discord_not_signal",
    # R-47: the circle is who may be asked about; the room is only who may be
    # told. Scoping the target to room membership answered «nobody here goes by
    # that» about a friend Nora knows and can locate.
    "R-47": "test_a_circle_member_who_is_not_in_the_room_can_still_be_asked_about",
    # R-48: only a cacheable tool's successful call ever gets logged for later
    # recall.
    "R-48": "test_a_non_cacheable_tool_is_never_logged",
    # R-49 (DEC-105): a relay crosses into another room only within one
    # unit, only for somebody allowed in both, and only under their name.
    "R-49": "test_a_room_in_another_unit_is_refused",
    # R-50 (DEC-106): her memory OF THE TAGGER rides in the prefill, and the
    # tool that would fetch it again points at the block instead. The duplicate
    # is the whole risk the block carries, so the guard is the named rule.
    "R-50": "test_about_the_tagger_points_at_the_block_instead_of_fetching_it",
    # R-90: `messages.body` carries a media label and a handle and nothing else;
    # the comprehension is joined at RENDER time, in the context assembler, and
    # `scan_identifiers` runs on every render. The durable body is read verbatim
    # by two consumers with opposite needs, so a description in it would make
    # "Honcho detailed, transcript short" structurally impossible.
    "R-90": "test_r90_the_body_carries_the_handle_and_the_render_carries_the_reading",
    # R-51: a media description is never invented. Every way the comprehension
    # pass can fail to produce one — a timeout, an unreadable format, an empty
    # answer — must leave the bare `[image a3f2c1]` placeholder, never a blank
    # `[image a3f2c1: ]` and never a guessed sentence.
    "R-51": "test_r51_a_media_description_is_never_invented",
    # R-53 (DEC-123): a source that is SILENT on the question is not a source
    # that denies it. Behavioural, the same shape as R-14 and R-41 — asserted
    # by `rooms/constraints.md` § Say only what you found, measured by the
    # corpus, NOT a currently-passing gate. R-27's three-state discipline is
    # enforced on tool RESULTS; this is the same discipline one level up, on
    # INFERENCE OVER results, where nothing enforced it: a scoped article's
    # omission read as a denial overturns a correct sourced answer.
    "R-53": "test_r53_is_covered_by_the_source_scope_corpus",
    # R-56 (DEC-133, the Dreamer runtime): the Dreamer may LOOK and may not ACT.
    # A background process that can speak to the group unprompted — or set a
    # reminder, or write to the events store — with nobody waiting on it is a
    # categorically different and more dangerous thing than one that can only
    # read, so this is a property of the runtime rather than a setting an
    # operator could get wrong.
    #
    # Points at the DERIVED set's own pin rather than a second copy of the
    # assertion. `read_only_tools()` is computed from a field on each tool, and
    # the test named here measures the computed set against the act verbs — so a
    # tool added tomorrow is classified by whoever adds it and cannot silently
    # join the Dreamer's ceiling. The hand-written list of act verbs lives in
    # the TEST while the code derives.
    "R-56": "test_no_act_verb_is_ever_read_only",
    # R-57: the block on a `where_is`-disclosed address entering `route` is
    # enforced in code; `nora/skills/navigation/SKILL.md` prose alone is
    # unenforced on any turn the skill doesn't load. `route`'s R-28 screen
    # catches an identifier, a raw coordinate, a known person's name; this is
    # the fourth case, and it can only be caught by PROVENANCE — a plain address
    # is indistinguishable from a typed place by shape — so `where_is` and
    # `route` share one turn-scoped set (`_disclosed_live_addresses`) instead of
    # a single shape screen.
    "R-57": "test_a_disclosed_live_position_is_refused_as_a_route_argument_in_an_untrusted_room",
    # R-58: the deployed signal-cli never emits `voiceNote` over JSON-RPC, so
    # Signal's parser falls back to the filename Signal's own recorder generates.
    # That fallback is gated on the resolved media type ALSO being audio/* —
    # `nora/ingest/stages/media.py` trusts `ref.voice_note` alone to route bytes
    # to local STT (DEC-131), so a bare filename match would let a sender route
    # ANY attachment there by naming it right, even one `label_for` still
    # displays as `[file]`.
    "R-58": "test_a_matching_filename_on_a_non_audio_attachment_is_not_a_voice_note",
    # R-52 (DEC-117): `create_event` fail-closes on ambiguity — more than one
    # open event — refusing rather than guessing which of two-or-more
    # already-open events is meant.
    "R-52": "test_r52_two_open_events_refuses_a_third_rather_than_guessing",
    # R-55: a note revision is VETOED unless `note_read` succeeded for that note
    # in the same turn. The injected index carries title + description only —
    # enough to know a note exists, nowhere near enough to revise one — and
    # `note_update` replaces the note's rendered body, so a revision written
    # blind destroys the arrangement the tier exists to hold. The named test
    # asserts through `ReplyGuard.evaluate`'s refusal rather than by reading the
    # latch set: the set is invisible to the model, the veto is where a miss
    # costs something, and a test that reads the set goes green against a latch
    # that never vetoes anything.
    "R-55": "test_r55_a_revision_is_vetoed_when_this_turn_never_read_the_note",
    # R-59: starting a NEW music session for a guild requires the requester to
    # be in a voice channel; an existing session is never gated this way (only
    # STARTING one names a destination channel).
    "R-59": "test_r59_play_refused_when_requester_not_in_voice_and_no_player_exists",
    # R-60: a voice channel emptying of humans disconnects immediately, no grace
    # period — distinct from timer 2 (queue empty, nothing playing -> idle-timeout
    # leave), covered by the sibling tests `test_queue_emptying_schedules_an_idle_
    # disconnect` and `test_a_new_track_before_the_idle_timeout_cancels_
    # the_pending_disconnect` in the same file.
    "R-60": "test_r60_a_voice_channel_emptied_of_humans_disconnects_immediately",
    # R-61: Nora's agent music tools
    # (play_music/skip_music/top_music/stop_music/music_status) are
    # Discord-only and never guess an identity to act on. Off Discord, with no
    # `MusicToolBridge` wired, or with a tagger identity.toml maps to no
    # Discord account at all, every one answers `unavailable` before ever
    # touching `MusicPlayerManager` — R-59's own "requester must be in a
    # voice channel to start a session" rejection is reused unchanged
    # underneath (`DiscordMusicToolBridge` calls the identical
    # `MusicPlayerManager.play`), never re-implemented or bypassed here.
    "R-61": "test_music_verbs_are_absent_off_discord",
    # R-62: when a Discord music
    # queue empties with nothing next, MusicPlayer._advance may seed ONE
    # autoplay pick from TrackSource.related() before falling back to the
    # ordinary idle-timeout behaviour, gated by [music] autoplay_enabled
    # (default false). related() returning empty falls straight through to
    # idle -- never retried or substituted with a guess.
    "R-62": "test_autoplay_enqueues_and_plays_a_related_track_when_the_queue_empties",
    "R-63": "test_autoplay_skips_tracks_already_played_this_session",
    # R-64: with [music]
    # autoplay_prewarm_seconds above 0, MusicPlayer resolves the NEXT
    # autoplay pick (Mix lookup AND its playable stream) before the
    # current track ends, so the track-end advance plays the prewarmed
    # stream directly -- zero network calls at the gap. A prewarmed pick
    # already played this session is refused (R-63 dedup still applies).
    "R-64": "test_prewarm_resolves_the_next_autoplay_pick_before_the_track_ends",
    # R-65: a dead voice connection tears the session down like a stop
    # instead of stranding now-playing state.
    "R-65": "test_a_dead_voice_connection_tears_down_the_session",
    # R-66 is retired; the number is not reused.
    # R-67: a completed prewarm that answered EMPTY gets ONE live `related()`
    # retry at the actual track end, never more -- only if the live answer is
    # also empty does the chain fall through to idle (R-62's "never retried"
    # discipline applied to the LIVE answer; the bound of one makes a loop
    # unreachable). Without the retry, a transient empty prewarm Mix that had
    # recovered by track end would leave autoplay silent for good.
    "R-67": "test_an_empty_prewarm_is_retried_once_at_track_end",
    # R-68: every transition that can touch a guild's MusicPlayer runs under
    # that guild's lock -- skip/stop/channel-empty/idle-timeout join
    # play()/on_track_finished()/on_voice_death() so a natural track-end, a
    # voice-death event, and a user command can never interleave.
    # resume_from_persisted's voice.play() and the dead-voice teardown's session
    # delete are both exception-safe.
    "R-68": "test_a_dead_voice_connection_tears_down_the_session",
    # R-88: the most recent track that ACTUALLY started is persisted as
    # `music_session.last_played_*` and survives the queue emptying, so a
    # boot-time resume that finds nothing playing re-seeds the autoplay
    # chain from it instead of sitting silent in a live voice channel.
    "R-88": "test_advance_seeds_autoplay_from_last_played_when_nothing_playing",
    # R-89 (DEC-159): a series may not repeat faster than
    # `[reminders] min_interval_seconds`, and the tool REFUSES rather than
    # clamping — a silently shortened series is a broken promise.
    "R-89": "test_an_every_minute_ask_is_refused_with_something_she_can_say",
    # R-69 names the FIRST of four tests (the naming convention this dict
    # follows), and it is the one whose failure is unrecoverable: an exception
    # raised inside a `finally` skips the rest of that `finally`, and the
    # statement immediately after the debrief call is the one that releases
    # R-10's per-conversation lock and dispatches R-30's queue. The other
    # three — the partial-reply false alarm, the latency-series separation,
    # and the stage's own `BaseException` catch — are named in the decision
    # index row.
    "R-69": "test_a_debrief_that_blows_up_still_releases_the_lock",
    # R-70 names the test that PREVENTS the defect rather than the one that
    # detects a hang: it refuses a hold budget the timeouts do not fit. The hang
    # tests and the window pair are named in the index row.
    "R-70": "test_the_standing_slot_hold_refuses_a_budget_the_timeouts_do_not_fit",
    # R-71 and R-72 are retired; the numbers are not reused.
    # R-73 points at `who_said`'s enforcement of the principle; the decision
    # index's R-73 row cites that test as a satisfying one.
    "R-73": "test_neither_the_words_nor_a_line_reaches_the_collector",
    # R-76 names the CHILD test rather than either drop test, and that is the
    # deliberate choice: «ingest is not exported» is the symptom everyone
    # remembers, but the rule a future change breaks is that the decision is
    # about ROOTS. Applying it per-span looks like a simplification, passes the
    # ingest tests, and silently punches holes in every real turn's trace where
    # its stages are. That test declares a CHILD `plumbing` under a
    # `generation` root and requires it exported anyway; the two drop tests and
    # the two engagement ones are named in the index row.
    "R-76": "test_a_generation_root_keeps_the_plumbing_underneath_it",
    # R-81 is retired; the number is not reused.
    # R-82 names the fail-closed branch rather than the happy path, because it
    # is the clause a future change is most likely to "simplify": an empty
    # window LOOKS like an empty room, and `nothing_found` reads as the more
    # informative answer. It is not — `EngagementContext._recent` returns `()`
    # for a FAILED store read too, so that simplification turns an outage into
    # a confident denial in the middle of a group argument, which is the exact
    # failure `who_said` was built to stop. The window-not-the-store test, the
    # DEC-24 schema test and the span tests are named in the index row.
    "R-82": "test_an_unread_window_is_unavailable_never_nothing_found",
    # R-83 names the CARVE-OUT test rather than the tagging one. Only one of the
    # two halves is the rule: the tagging clause is a mechanism joined to a
    # position that already existed, while the carve-out is the branch that did
    # not exist at all — and it is the half a rewrite loses, because the
    # paragraph it sits under reads as complete without it (the same failure
    # mode R-53's third clause has, recorded in
    # `test_conduct_separates_a_source_that_is_silent…`).
    "R-83": "test_r83_the_anti_cave_position_is_scoped_to_a_checked_claim",
    # R-84: the music message table. The named test is the module that pins every
    # string the table renders — a wording change to ANY music surface has to
    # land there, so the diff shows it.
    "R-84": "test_now_playing_line_carries_title_and_url",
    # R-86 (DEC-151, DEC-201): every social read tool is read-only at three
    # layers, and the named test DERIVES all three from the code — the bridge's
    # allowlist against OpenCLI's write verbs, the tool's (platform, what)
    # vocabulary against that allowlist, and the NativeTool declaration — so a
    # hand-edit to any one layer that drifts from the others goes red.
    "R-86": "test_r86_social_reads_are_read_only_at_three_layers",
    # R-87: Standing folds read SINCE a row-id watermark
    # (`since_including_nora`, capped at [standing] fold_batch), not the reply
    # path's recency window — a conversation that passes 40 messages between
    # tags must not lose the middle. The named test drives the real stage
    # through a store holding 60 rows and asserts the fold's prompt reaches the
    # row a 40-row window would clip.
    "R-87": "test_r87_the_standing_fold_reads_since_a_row_id_watermark",
    # R-91: one memory ration for the whole Dreamer pass, spent across
    # `about`/`ask_memory`/`search_memory_workspace`/`lookup_person` together and
    # answered — R-27 `unavailable` — rather than vetoed. The named test runs the
    # REAL Strands loop, because the property is what the MODEL gets back: an
    # error result is a correction, and a model corrects by retrying, which is
    # the loop the ceiling exists to end.
    "R-91": "test_a_third_memory_read_is_answered_unavailable_and_the_pass_completes",
    # R-92 is retired; the number is not reused.
    # R-93 (DEC-187): ONE Discord voice connection, owned by the
    # audio-controller — nothing in nora's production tree opens a voice
    # connection or holds a VoiceClient. The named test is the architecture
    # gate that fails the build if one creeps back.
    "R-93": "test_nora_never_opens_a_discord_voice_connection",
    # R-94 (DEC-187): end-music and leave-voice are separate
    # lifecycles — `/music stop` and idle expiry end the session, but the
    # voice connection STAYS; only `/discord exit`, an emptied channel and
    # voice-death destroy it.
    "R-94": "test_music_stop_is_end_music_and_stays_connected",
    # R-95 (DEC-187): a `track-finished` naming a superseded
    # playback_id is the old track's tail — the staleness check runs UNDER
    # the guild lock against the currently-expected playback, and the queue
    # never advances twice.
    "R-95": "test_a_track_end_for_a_superseded_playback_does_not_advance",
    # R-96 (DEC-187): voice commands pass a pure gate — guild member + caller
    # physically in a voice channel + that channel's GUILD in `voice_guild_ids` —
    # and auto-join joins the CALLER's channel or rejects `not_in_allowed_guild`.
    # The gate is guild-scoped because a server's voice channels are not a list
    # anyone can keep true. The named test is the full matrix.
    "R-96": "test_the_join_decision_matrix",
    # R-97: a turn nobody asked for does not take the floor to report an absence.
    # The named test is the five silence twins driven with the trajectory the
    # live suite measures; four of them false-speak if either production line is
    # reverted.
    "R-97": "test_the_five_silence_twins_stay_silent",
    # R-98: a story is read only for a Tier 0 person. The named test asserts on
    # the fake bridge's OWN request log that no story request was sent for a
    # person in no room, beside a positive control showing the same person's
    # profile does reach the bridge.
    "R-98": "test_a_story_for_someone_in_no_room_is_refused_and_never_requested",
    # R-99: a tool call carrying an argument its own signature does not declare
    # is refused before Strands' generated pydantic model can silently drop it.
    # The named test drives the REAL guard hooks through `FakeAgentRunner` and
    # asserts both halves: the tool body never runs, and the refusal names the
    # unknown argument and what the tool actually accepts.
    "R-99": "test_the_veto_fires_before_the_tool_body_ever_runs",
    # R-100: a link to one of OUR documents is minted by a tool, never authored
    # by the model. The named test drives the real `Deliverer` through
    # `deliver.build` with the carrier armed and asserts the made-up link never
    # reaches the adapter while the rest of what she said does, and it is counted.
    "R-100": "test_a_reply_with_a_made_up_link_is_sent_without_it_and_counted",
    # Nobody outside identity.toml is sent, or recorded from, a direct message.
    "R-101": "test_a_dm_to_an_address_not_in_identity_toml_is_refused",
}


def _collected() -> set[str]:
    out = subprocess.run(["uv", "run", "pytest", "--collect-only", "-q"],
                         cwd=ROOT, capture_output=True, text=True).stdout
    return {line.split("::")[-1].split("[")[0] for line in out.splitlines() if "::" in line}


def test_every_enforced_rule_has_a_named_test():
    collected = _collected()
    missing = {rule: name for rule, name in RULE_TESTS.items() if name not in collected}
    assert not missing, f"rules without their named test: {missing}"


def test_r14_is_covered_by_the_adversarial_corpus():
    """R-14 is behavioural: asserted by constraints.md, measured by the corpus."""
    assert (ROOT / "evals" / "corpora" / "injections.yaml").exists()
    assert (ROOT / "evals" / "corpora" / "transcript_injection.yaml").exists()


def test_r41_is_covered_by_the_identity_grounding_corpus():
    """R-41 is behavioural: asserted by conduct.md, measured by this corpus.

    Unlike R-14, this corpus is NOT currently a passing gate — DEC-75 records
    that the shipped doctrine wording still fabricates on its ambiguous cases.
    This test pins that the measurement exists and is findable, not that the
    rule holds; see DEC-75 for the number.
    """
    assert (ROOT / "evals" / "corpora" / "identity_grounding.yaml").exists()


def test_r53_is_covered_by_the_source_scope_corpus():
    """R-53 is behavioural: asserted by conduct.md, measured by this corpus.

    Same posture as R-41 above, and for the same reason. The corpus replays the
    SHAPE of a real incident — a source narrower than the question, whose
    silence about the rest was read as a denial — with the game and the objects
    changed so a pass cannot be a memory of the real one, plus two controls: a
    source that genuinely denies (she may overturn) and a source that covers the
    question at the right scope (she answers plainly). Without those, a fix that
    overcorrected into hedging everything would score as a pass.

    It is a measured record of the doctrine gap, not a gate the shipped doctrine
    is known to clear. It IS selectable —
    `python -m evals source_scope` — because being ungated and being unrunnable
    are different things. This test pins that the measurement exists and is
    findable, not that the rule holds.
    """
    assert (ROOT / "evals" / "corpora" / "source_scope.yaml").exists()


def test_r83_is_covered_by_the_being_contradicted_corpus():
    """R-83 is behavioural: asserted by conduct.md and voice.md, measured by this
    corpus.

    `test_r83_the_anti_cave_position_is_scoped_to_a_checked_claim` above
    measures that the position is STATED and where, not that the model follows
    it. A stated position is not a followed one: she gave an answer about
    herself, somebody put a bare name in front of her hours later with nothing
    behind it, and she conceded and then supplied detail nobody had given her,
    with all four surfaces that should have held her in her prompt.

    Same posture as R-41 and R-53: the corpus carries the bare-assertion cases and
    BOTH controls — real evidence arriving (updating is correct) and a claim she
    never grounded (R-83's own carve-out, where neither holding nor conceding is
    right) — so a fix that overcorrected into never moving cannot score as a pass.
    It is a measured record of the gap, not a gate the shipped doctrine is known
    to clear. `python -m evals being_contradicted` runs it; one window is not a
    rate. This test pins that the measurement exists and is findable, not that
    the rule holds.
    """
    assert (ROOT / "evals" / "corpora" / "being_contradicted.yaml").exists()


def _message(*, platform: str, body: str, media: tuple[MediaRef, ...]) -> NormalizedMessage:
    return NormalizedMessage(
        platform=platform, conversation_id="g1", conversation_type="group",
        sender_id="+85212345678", sender_name="Miles", timestamp=1700,
        body=body, raw={}, media=media,
    )


class _CollectingSpawn:
    """`spawn`, collected instead of backgrounded. The comprehension job never
    awaits its own work — that is the point of it — so a test that wants to
    observe the result must hold the coroutine and await it explicitly. The
    alternative (a real `create_task` plus a sleep) is a race written into an
    assertion."""

    def __init__(self) -> None:
        self.coros: list = []

    def __call__(self, coro) -> None:
        self.coros.append(coro)

    async def drain(self) -> None:
        for coro in self.coros:
            await coro
        self.coros.clear()


async def test_r51_a_media_description_is_never_invented():
    """R-51. Every way reading a medium can fail — the model timing out, the
    format being unreadable, the answer coming back empty — leaves the bare
    placeholder and is counted with an R-27 `unavailable`. None of them writes
    a blank description, and none writes a guessed one. An ERROR is never a
    PASS.

    The rule covers every medium, not only stickers: the way a failure has to
    look is the same for all of them. Asserted at the STORE rather than at a
    body: nothing written is what makes every later render show the bare handle,
    which is the property the rule is actually about.

    The positive control is the last block: the same job, the same wiring, an
    interpreter that DOES answer — so this cannot be passing because nothing
    ran."""
    blob = MediaBlob(sha256="c" * 64, media_type="image/png", size=4)
    subject = Subject(blob=blob, label="image")
    blobs = FakeBlobStore()
    blobs.blobs[blob.sha256] = b"\x89PNG"

    for interpreter, reason in (
        (FakeMediaInterpreter(available=False, reason="timeout"), "timeout"),
        (FakeMediaInterpreter(available=False, reason="unsupported_format"),
         "unsupported_format"),
        (FakeMediaInterpreter("", title="", content=""), "empty"),
    ):
        store = FakeMediaObjectStore()
        memory = FakeMemoryStore()
        metrics = Metrics()
        spawn = _CollectingSpawn()
        job = MediaComprehension(
            blobs=blobs, store=store, interpreter=interpreter, memory=memory,
            metrics=metrics, spawn=spawn,
        )
        job.schedule(
            subjects=(subject,), platform="signal", conversation_id="g1",
            person=None, timestamp=1700,
        )
        await spawn.drain()

        assert store.comprehended == [], f"{reason} invented a comprehension"
        assert metrics.get(
            "nora_media_comprehension_total", platform="signal", label="image",
            status="unavailable", reason=reason,
        ) == 1
        assert metrics.get(
            "nora_media_comprehension_total", platform="signal", label="image",
            status="nothing_found", reason=reason,
        ) == 0, "a failed read is never «there was nothing there»"

    # POSITIVE CONTROL — the identical wiring with a describer that answers.
    store = FakeMediaObjectStore()
    spawn = _CollectingSpawn()
    job = MediaComprehension(
        blobs=blobs, store=store, interpreter=FakeMediaInterpreter("a cartoon cat"),
        memory=FakeMemoryStore(), metrics=Metrics(), spawn=spawn,
    )
    job.schedule(
        subjects=(subject,), platform="signal", conversation_id="g1",
        person=None, timestamp=1700,
    )
    await spawn.drain()
    assert len(store.comprehended) == 1, "the control must actually comprehend"


def test_recall_lookups_is_offered_as_evidence_not_only_as_a_cache():
    """DEC-112. `recall_lookups` is offered as her EVIDENCE, not only as a cache.
    A `when` clause that sold the tool as a COST saving — «call this before
    re-running…» — left it unread while she retracted sourced, correct answers as
    老作 rather than read them back; being called a liar does not feel like being
    about to re-search.

    The evidence framing lives in the tool's SCHEMA docstring — the single source
    of truth for what the tool is for — and the position lives in `conduct.md`.
    Revert either and this goes red.
    """
    import inspect

    from nora.agent import tools as tools_module
    src = inspect.getsource(tools_module)
    # The merged docstring is the LLM-facing one (Strands reads the callable's
    # docstring as the schema description). Assert the evidence framing
    # survived into it: `actually`, `doubted`/`guessed`, and the retained
    # cost framing `re-running`.
    start = src.find("def recall_lookups(")
    assert start != -1, "recall_lookups callable not found in tools.py"
    open_q = src.find('"""', start)
    close_q = src.find('"""', open_q + 3)
    doc = src[open_q + 3:close_q].lower()

    # The half DEC-92's prose could not reach: this is her EVIDENCE, not a cache.
    assert "actually" in doc, doc
    assert "doubted" in doc or "guessed" in doc, doc
    # The original cost framing is kept, not replaced — re-running is still waste.
    assert "re-running" in doc, doc

    conduct = (ROOT / "nora" / "doctrine" / "rooms" / "conduct.md").read_text()
    # The law half of «Being contradicted» lives in rooms/constraints.md.
    law = (ROOT / "nora" / "doctrine" / "rooms" / "constraints.md").read_text()
    section = law.split("# When you are told you are wrong", 1)[1].split("\n# ", 1)[0]
    # The prose is hard-wrapped, so every phrase below spans a newline in the
    # file; match on the sentence, not on the line breaks it happens to have.
    contradicted = " ".join(section.split())

    # Not remembering the checking is not evidence the checking did not happen.
    assert ("Not remembering whether you checked is not the same as not having "
            "checked") in contradicted, contradicted
    # She has a record of what she checked, still in the log, and is told to
    # read it before concluding she guessed.
    assert ("the grounds you checked before may still be in the record"
            ) in contradicted, contradicted
    # Conceding a fault she does not have is a lie, not modesty.
    assert "blame turned" in contradicted, contradicted

    # The position must NOT name the tool: tools are described by their schemas,
    # and an authored copy is a cache with no invalidation.
    assert "recall_lookups" not in conduct
    assert "recall_lookups" not in law


# name -> the scope folder it ships under, same table as
# tests/test_doctrine_content.py::_SCOPE_FOLDER.
_DOCTRINE_SCOPE_FOLDER = {
    "identity": "core", "constraints": "core",
    "voice": "rooms", "conduct": "rooms",
    "participation-conduct": "rooms",
    "participation": "instruments", "reaction": "instruments",
}


def _doctrine(name: str) -> str:
    """Whitespace-normalised, because every doctrine file is hard-wrapped and
    every phrase asserted below spans a line break in it."""
    folder = _DOCTRINE_SCOPE_FOLDER[name]
    return " ".join(
        (ROOT / "nora" / "doctrine" / folder / f"{name}.md").read_text().split()
    )


def test_r83_the_anti_cave_position_is_scoped_to_a_checked_claim():
    """R-83 / DEC-147. The failure: Nora attributes a statement to a group member
    without looking, matching the most aggressive strings in the room onto the
    person who is denying her, and then three surfaces built for a DIFFERENT case
    hold her there: this file's own «not remembering having checked is not
    evidence that you did not check» (DEC-112), `voice.md`'s 「冇錯就冇嘢認 …
    企硬」, and `send_reply`'s «never invent a mistake you did not make just to
    have something to concede» (DEC-92). Each is right for a claim with a lookup
    behind it. Put together they convert an UNGROUNDED assertion into a defended
    one, because not one of them has a branch for «I never grounded this».

    The branch is R-27's three states read off her own record: found is `ok`
    and she holds it; not found — or not readable — is `unavailable`, and
    going to look, or letting it drop, is the reply. That is R-5's temperament
    («silence beats a wrong answer») on the contradiction path, which is the
    one place nothing else wires it.

    Pinned as a POSITION and not as one example's nouns, the same way R-53 is,
    so it cannot be satisfied by a sentence naming the example's words:

      1. holding is conditional — it is what checking bought,
      2. a claim she cannot find herself having grounded is not one she stands
         on, however sure it feels,
      3. there is a real reply for that case, and it is not a defence.

    THE POSITIVE CONTROL is the second half. A carve-out that ate the anti-cave
    stance would satisfy any presence check on the three moves above while
    quietly undoing DEC-112 and DEC-123, so the clauses that hold her on a
    CHECKED claim are asserted still present, on both sides of the DEC-92
    split. The third of that armour sits in the doctrine and is pinned by
    `tests/test_tools.py::test_the_three_reply_defects_live_in_the_doctrine_not_the_send_reply_schema`;
    it is deliberately not re-asserted here, because two copies of one literal
    is the unguarded-copy failure this repo keeps paying for.

    HONEST SCOPE: a content test, the same posture as
    `test_recall_lookups_is_offered_as_evidence_not_only_as_a_cache` above. It
    measures that the position is stated, and where. It does not measure that
    the model follows it; `evals/corpora/being_contradicted.yaml` does, and
    `test_r83_is_covered_by_the_being_contradicted_corpus` below pins that the
    corpus exists. The corpus exists because a position stated on four surfaces
    is not a followed one (DEC-78/DEC-92: prose alone does not move behaviour).

    `conduct.md` is written in English; `voice.md` stays Cantonese because that
    file IS a specimen of the register it teaches. Quoted SPEECH stays Cantonese
    everywhere, so the anchors below are English prose and keep the Cantonese
    wherever it is something somebody says.
    """
    voice = _doctrine("voice")
    # The law lines of «Being contradicted» live in rooms/constraints.md § When
    # you are told you are wrong; the practice line (「我去睇返」) stays in
    # conduct.md.
    law = " ".join((ROOT / "nora" / "doctrine" / "rooms" / "constraints.md").read_text().split())

    # The carve-out lives in the section that owns whether a challenge should
    # move her at all. `voice.md` owns how it SOUNDS and is operator-only; a
    # copy there is the drift DEC-92's split exists to prevent.
    section = " ".join(
        (ROOT / "nora" / "doctrine" / "rooms" / "constraints.md").read_text()
        .split("# When you are told you are wrong", 1)[1].split("\n# ", 1)[0].split()
    )
    being_contradicted = " ".join(
        (ROOT / "nora" / "doctrine" / "rooms" / "conduct.md").read_text()
        .split("# Being contradicted", 1)[1].split("\n# ", 1)[0].split()
    )

    # 1. Holding is conditional on having checked.
    assert ("Hold the position when you have grounds and change it when the "
            "counter-evidence is enough") in section, section
    # 2. The branch that did not exist: no grounding, no position to stand on,
    #    and the confidence that shows up under pressure is worth least.
    assert ("when you cannot find where you grounded it, do not dig in on a "
            "feeling") in section, section
    assert "do not mistake fresh confidence for evidence" in section
    # 3. R-5's temperament: there is a real reply, and neither branch of it is
    #    a defence of the ungrounded claim. The utterance stays Cantonese: it is
    #    something she SAYS, not prose about what she does.
    assert ("Saying 「我去睇返」 or letting the thing go are both fine"
            in being_contradicted), being_contradicted

    # POSITIVE CONTROL — the armour for a CHECKED claim is untouched. Delete
    # any of these to buy the carve-out and this goes red, which is the whole
    # point: the ask was a missing branch, not a weaker stance.
    assert ("Not remembering whether you checked is not the same as not having "
            "checked") in law
    assert "the grounds you checked before may still be in the record" in law
    assert "Agreeing you invented something when you did not is not modesty" in law
    assert "Do not fake a concession to keep the peace" in law
    assert "冇錯就冇嘢認" in voice
    assert "企硬" in voice


def test_the_verdict_position_names_the_mechanism_that_performs_a_tag():
    """DEC-147's other half. `conduct.md`'s 評判 section says the losing side is
    why she is there, to be told to their face and tagged rather than reported
    on (the clauses are asserted below, in string literals, so this paragraph
    does not become a second copy of them), and `send_reply`'s schema teaches
    (DEC-86) that an `@` written immediately before the name the room uses for
    somebody makes a real tag. The position lives in one surface and its only
    mechanism in another, so unless the doctrine joins them 「tag 返佢」 reads as
    a manner of speaking, which is exactly how a verdict about somebody gets
    delivered past them.

    The doctrine says what a tag IS, in one sentence, with no worked example and
    no name — DEC-96's rule, that every concrete example becomes a closed set,
    applies to a handle as much as to an emoji. The example belongs on the
    schema, where `tests/test_tools.py::
    test_the_tag_form_the_model_is_told_to_write_is_the_one_the_composer_resolves` drives it
    through the real composer; the doctrine carries the position only.
    `conduct.md` is written in English; quoted SPEECH stays Cantonese, so the
    anchors below are English prose.
    """
    section = " ".join(
        (ROOT / "nora" / "doctrine" / "rooms" / "conduct.md").read_text()
        .split("# Being asked to judge", 1)[1].split("\n# ", 1)[0].split()
    )

    # The join itself: tagging them back is the act, not a description of one.
    assert "Tagging them back is not a turn of phrase" in section, section
    assert "an actual @ in the sentence" in section, section
    # Against the name the ROOM uses, the same binding the composer resolves.
    assert "carrying the name the room calls them by" in section, section
    # And the reason, which is the position it was always attached to: without
    # the tag she is reporting on him however hard the line lands.
    assert "talking about them to the room rather than to them" in section, section

    # POSITIVE CONTROL — the verdict position it hangs off is still there. If a
    # rewrite keeps the mechanism and loses 「唔好淨係報導」, the sentence
    # becomes formatting advice.
    assert "you do not just report who was right and who was wrong" in section, section

    # The doctrine states the position and NOT the example: a handle written
    # here would be the closed set DEC-96 measured, and the schema is where a
    # worked one is already pinned against the composer.
    assert not re.search(r"@\w", section), (
        "conduct.md ships a worked @handle — that is the schema's job, and a "
        "name in the doctrine becomes the only name she uses"
    )


def test_r86_social_reads_are_read_only_at_three_layers():
    """R-86 / DEC-151, DEC-201: every social tool is read-only. The decision index
    and `RULE_TESTS` name this test by this name. Social tools read
    content a model can be prompt-injected by, over backends that also serve
    writes — OpenCLI drives an AUTHENTICATED browser with `twitter post`/
    `like`/`follow`/`delete`; the instagram-bridge serves `/send`, `/react`, …
    — so none of them may reach a write. Read-only is enforced at three
    layers, and this test DERIVES all three from the code, from ONE vocabulary
    table covering every social tool (`nora.agent.tools.
    social_read_vocabulary`), so an edit to any layer that drifts from the
    others goes red here:

    1. each tool's closed `(platform, what)` vocabulary — there is no
       free-form command passthrough;
    2. the BACKEND allowlists: the host-side bridge's `READ_ONLY_ALLOWLIST`
       for OpenCLI (its command slot is only ever filled from the mapping's
       values; the behavioural half is `tests/unit/test_social_bridge_server.
       py`), and the router's `EXCEPTION_ROUTES` for the instagram-bridge,
       whose client issues only `GET /accounts…` and `GET /posts/…` (pinned
       on a recording fake bridge in `tests/unit/test_instagram_read_tool.py`,
       `tests/unit/test_instagram_read_links.py` and
       `tests/unit/test_instagram_search_tool.py`);
    3. each tool's `NativeTool` declaration `read_only=True`, the Dreamer's
       ceiling.

    The derivation is TOTAL: every pair a tool can construct is served by
    exactly one backend, every pair a backend serves is constructed by exactly
    one tool (no Instagram read has two tools). OpenCLI serves X and
    LinkedIn only: no `instagram` key is in `READ_ONLY_ALLOWLIST`, and the X
    tools construct no Instagram pair at all.
    """
    from nora.agent import tools as tools_module
    from nora.infra.social import EXCEPTION_ROUTES, bridge_server

    vocabulary = tools_module.social_read_vocabulary()
    assert set(vocabulary) == {
        "x_read", "x_search", "linkedin_read", "linkedin_search",
        "instagram_read", "instagram_search",
    }

    # Layer 2: every command the bridge can spawn is disjoint from OpenCLI's
    # write verbs (the set in `bridge_server.OPENCLI_WRITE_VERBS`).
    for _site, command in bridge_server.READ_ONLY_ALLOWLIST.values():
        assert command not in bridge_server.OPENCLI_WRITE_VERBS, (
            f"the bridge can spawn a WRITE verb: {command}"
        )

    # Layer 1 matches layer 2 exactly: what the tools construct, together, IS
    # what the two backends serve, together.
    served = set(bridge_server.READ_ONLY_ALLOWLIST) | EXCEPTION_ROUTES
    constructed = set().union(*vocabulary.values())
    assert constructed == served

    # One read, one tool, one backend: every pair of tools is disjoint.
    names = sorted(vocabulary)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            assert not (vocabulary[a] & vocabulary[b]), (
                f"a social read has two tools: {a} and {b}"
            )
    x_reads = vocabulary["x_read"] | vocabulary["x_search"]
    linkedin_reads = vocabulary["linkedin_read"] | vocabulary["linkedin_search"]
    read, search = vocabulary["instagram_read"], vocabulary["instagram_search"]
    assert read | search == EXCEPTION_ROUTES, (
        "instagram_read and instagram_search read through the instagram-bridge "
        "only (design/instagram-adoption/15)"
    )
    assert not (EXCEPTION_ROUTES & set(bridge_server.READ_ONLY_ALLOWLIST)), (
        "a pair is on the bridge's list AND in OpenCLI's allowlist, so a verb "
        "has two backends"
    )

    # OpenCLI serves no Instagram read. Asserted on the KEYS, not derived from
    # the vocabulary, because the cutover this guards is exactly an allowlist
    # row left behind after its tool moved: the host would still spawn an
    # Instagram command for anything that asked, and the derivation above would
    # read that as a missing tool rather than a leftover backend.
    assert not [
        key for key in bridge_server.READ_ONLY_ALLOWLIST if key[0] == "instagram"
    ], "OpenCLI still serves an Instagram read"
    assert all(platform == "x" for platform, _what in x_reads), (
        "an X tool constructs a read on another network"
    )
    assert all(platform == "linkedin" for platform, _what in linkedin_reads), (
        "a LinkedIn tool constructs a read on another network"
    )
    assert x_reads | linkedin_reads == set(bridge_server.READ_ONLY_ALLOWLIST), (
        "the X and LinkedIn tools together are exactly what OpenCLI serves"
    )
    # DEC-201: `linkedin search` is OpenCLI's JOBS search; LinkedIn's
    # search row must be `people-search`.
    assert bridge_server.READ_ONLY_ALLOWLIST[("linkedin", "search")] == (
        "linkedin", "people-search",
    )

    # Layer 3: the declaration the Dreamer's ceiling is derived from.
    for tool in vocabulary:
        assert tools_module.NATIVE_TOOLS[tool].read_only is True, tool


async def write_prior(store) -> None:
    from nora.domain.standing import Standing

    await store.write(standing=Standing(
        platform="signal", conversation_id="g1", body="the carried position",
        fold_count=3, updated_at=100, last_folded_row_id=10))


async def test_r87_the_standing_fold_reads_since_a_row_id_watermark():
    """R-87. The standing fold reads
    `since_including_nora(after_id=last_folded_row_id, limit=fold_batch)` —
    everything since the last fold, capped at the batch — NOT the reply path's
    recency window. A conversation that passes 40 messages between tags must not
    lose the middle, which a fold over only the last `window` (40) rows would.

    Driven through the REAL debrief stage with a prior that carries a
    watermark of 10 and a store holding 60 rows: the fold's prompt must
    contain rows 11-60 (the stretch since the watermark, reaching far beyond
    any 40-row window), and the written position must advance the watermark to
    the last row read (60). A fold that still sliced a window would show rows
    21-60 at most and would not see rows 11-20 — the exact clipping the
    watermark exists to remove.
    """
    import json

    from nora.domain.models import DeliveryStatus, TranscriptRow
    from nora.domain.tag_trace import TagTrace
    from nora.engagement.stages import debrief
    from nora.testing import FakeRecordStore, InMemoryStandingStore

    class _Client:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        @property
        def model(self) -> str:
            return "test-model"

        async def complete(
            self, *, system: str, prompt: str, trace_attributes=None,
        ) -> debrief.Completion:
            self.prompts.append(f"{system}\n\n{prompt}")
            return debrief.Completion(
                json.dumps({"standing": "the revised position"}), "stop")

    class _Objective:
        def objective_body(self, name: str) -> str:
            assert name == debrief.OBJECTIVE, name
            return "OBJECTIVE-TEXT"

    store = InMemoryStandingStore()
    await write_prior(store)
    record = FakeRecordStore()
    for i in range(1, 61):
        await record.append(message=NormalizedMessage(
            "signal", "g1", "group", "+852111", "Miles", i, f"m{i}", {}))
    client = _Client()
    trace = TagTrace(
        tag_id="tag-1", conversation_id="g1", messages=(),
        transcript=(TranscriptRow(
            id=60, sender_id="+852111", sender_name="Miles", ts=60,
            body="m60", is_nora=False, delivery_status=DeliveryStatus.SENT,
            raw_json=None),),
        outcome="send_reply", created_at=60,
    )
    await debrief.run(
        trace=trace, platform="signal", store=store,
        client=client, objective=_Objective(), metrics=Metrics(),
        display_name="Nora", record_store=record, max_chars=1200, fold_batch=200,
        timeout_seconds=5.0, store_timeout_seconds=5.0, now_ms=60,
    )
    # The prompt must reach back past any 40-row window: rows 11-20 are the
    # stretch a window fold would clip, and they are the whole point.
    joined = client.prompts[0]
    assert "m11" in joined, "the fold must read the stretch since the watermark"
    assert "m60" in joined
    row = await store.read(platform="signal", conversation_id="g1")
    assert row is not None and row.last_folded_row_id == 60


async def test_r90_the_body_carries_the_handle_and_the_render_carries_the_reading():
    """R-90, end to end across the seam it is about.

    The durable body written by custody carries `[image a3f2c1]` and nothing
    else — no description, no transcript — and the SAME row, read back through
    the context assembler with the comprehension on it, renders the short form.
    One row, two readings, which is the whole point: `memory_feed` wants
    everything and the prefill wants a line, and one string could not serve
    both.

    Written across BOTH halves deliberately. Asserting only that the body is
    bare would pass against a build that never renders a description anywhere;
    asserting only that the render works would pass against one that also bakes
    it in. The pair is the rule.
    """
    blob = MediaBlob(sha256=_R90_SHA, media_type="image/png", size=len(_R90_BYTES))
    audit = FakeRecordStore()
    source = FakeMediaSource({"att1": _R90_BYTES})

    stored = await media_stage.run(
        message=_message(
            platform="signal", body="睇下 [image]",
            media=(MediaRef(platform_id="att1", media_type="image/png"),),
        ),
        sources=MediaSourceRegistry([source]), blobs=FakeBlobStore(),
        metrics=Metrics(),
    )

    handle = blob.sha256[:6]
    assert stored.body == f"睇下 [image {handle}]", "the body is a label and a handle"

    await audit.append(message=stored)
    media = FakeMediaObjectStore([MediaObject(
        sha256=blob.sha256, media_type="image/png", size=4, source="inbound",
        title="a plate of noodles", description="a bowl of wonton noodles",
        content="生記粥麵", created_at_ms=1,
    )])
    assembler = TranscriptInjection(
        audit=audit, memory=FakeMemoryStore(), persons=_R90_PERSONS,
        context_window=10, metrics=Metrics(),
        # The SHARED join, with the REAL screen — the same object every other
        # transcript builder is handed (`tests/architecture/
        # test_media_join_reaches_every_transcript.py` is what makes «every»
        # true).
        media_join=MediaJoin(
            store=media, screen=scan_identifiers, metrics=Metrics()),
    )

    ctx = await assembler.assemble(request=EngagementRequest(
        message=_message(platform="signal", body="nora.000 呢個係咩", media=()),
        decision=EngageDecision(engage=True, reason="trigger_text"),
        tag_id="t1",
    ))

    assert ctx.transcript[-1].body == (
        f"睇下 [image {handle}: a plate of noodles - a bowl of wonton noodles]"
    )
    # The full `content` stays OUT of the transcript — it is what `media_fetch`
    # serves, and putting it in every prefill is the token cost the split
    # exists to avoid.
    assert "生記粥麵" not in ctx.transcript[-1].body

"""E.7 criterion 5: every rule R-1…R-17 has a passing named test."""

import re
import subprocess
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError, IntegrityError

from nora.domain.media import MediaRef
from nora.domain.models import (
    ExchangeExample,
    LexemeExample,
    NormalizedMessage,
    RoomExchange,
    RoomExchangePattern,
    RoomLanguage,
    RoomLexeme,
)
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.schema import ROOM_EXCHANGE
from nora.ingest.stages import media as media_stage
from nora.ingest.stickers import StickerReader
from nora.language.exchange_observer import _parse_item
from nora.language.portrait_synthesizer import PortraitSynthesizer
from nora.obs.metrics import Metrics
from nora.platform.registry import MediaSourceRegistry
from nora.testing import (
    FakeBlobStore,
    FakeMediaDescriptionStore,
    FakeMediaInterpreter,
    FakeMediaSource,
)

ROOT = Path(__file__).resolve().parents[1]

# The named test the spec's B.1 table assigns to each rule.
RULE_TESTS = {
    # R-1's SECOND clause, revised (design/dreamer-agent-loop/35) — the first
    # revision of "ambient traffic never reaches the agent". Untagged messages
    # may now reach the agent ONLY under the participation occasion's gating:
    # an operator-opted-in room, one quiescence-closed window, one evaluation
    # through `FirePath.speak`. The renamed test keeps the original pin
    # (ordinary ambient traffic constructs ZERO agents) and its sibling pins
    # the exception's gating.
    "R-1": "test_ambient_reaches_the_agent_only_through_participation_gating",
    # R-75: the curator, the note tier's only cross-note writer. Named test
    # is the date-only refusal, because it is the one property that decides
    # whether ANY merge happens in production — merge eligibility reads
    # `note_entity`, which has no writer, so every group is refused today and
    # the counter is the reversal condition. The other enforcing tests are on
    # the index row, including the one the branch could not merge without:
    # a group the model asked to merge is never retired piecemeal.
    "R-75": "test_a_date_only_collision_is_refused_and_counted",
    # R-1b: R-1's FIRST clause, revised. `nora.app.fire.FirePath` made "only the
    # tagging path may cause a send" false — a fire turn is a send not caused by
    # a tag. R-1 keeps its entry rather than losing it (the DEC-40 precedent
    # below): its test asserts the ambient clause. But it harnesses
    # only ambient input, so it stays green no matter how many non-tag send paths
    # exist, which makes it no brake at all on the revised clause. R-1b is that
    # brake: `Deliverer` is the only caller of `adapter.send`, it is constructed
    # only by `deliver.build`, and `deliver.build` has exactly two callers — the
    # tag pipeline and the fire path. The participation occasion changes NOTHING
    # here: it goes through `FirePath.speak`, so the two callers remain two.
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
    # call (design/14 §2.2, DEC-46). design/14 §2.3 reserved R-27/R-28 for
    # grounding; R-27 went to transit, so this is R-28 — search/fetch, when they
    # ship, take the next free number.
    "R-28": "test_a_route_naming_a_person_is_refused_before_egress",
    # R-2/R-2b keep their entries rather than gaining new ones: DEC-40 revised
    # the RULE (one message became `send_reply`'s budget) and both named tests
    # were rewritten in place to assert the revised invariant, which is what
    # `design/proposed/16-multi-message-replies.md` §6.7 asks for. A new key
    # would leave the old one pointing at a rule nobody enforces.
    "R-25": "test_a_turn_cut_off_between_messages_is_counted_as_partial",
    # R-29: a skill source is operator configuration only. `SkillStore.resolve`
    # is a pure name lookup, so a message can name a skill the operator shipped
    # (the intended activation) but can never introduce a new source — item 6
    # can never reach item 5 (design/12 §2, DEC-48).
    "R-29": "test_a_message_can_name_a_skill_but_never_load_one",
    # R-29: a second tag for a conversation already handling one is QUEUED, not
    # dropped — three people tagging at once each get a reply, in FIFO order,
    # while different conversations still run concurrently. Next free number
    # after R-28 (R-26 is reserved by design/proposed/18 for the unshipped
    # context-budget work), DEC-48. See `nora.engagement.queue`.
    "R-30": "test_three_tags_for_one_conversation_are_answered_in_fifo_order",
    # R-31: the presence disclosure seam is fail-closed (design/19 §5, DEC-54).
    # `whereis` refuses to disclose a circle member's position into a
    # conversation whose people are NOT a subset of the circle — a REFUSAL, not
    # a silent omission. This is the INVERSE of the room tools' R-21 and nothing
    # else in Nora enforced it; the reviews were explicit that «the same rule the
    # roster uses» was an overstatement. Next free number after R-30.
    "R-31": "test_whereis_refuses_to_disclose_outside_the_circle",
    # R-32: the collector is a change-recorder, not a sampler (design/19 §1,
    # DEC-55). A row is written only when a member's `ts` advances, so two reads
    # of an UNMOVED member produce ONE row — the property that makes presence a
    # time-series of distinct fixes rather than a duplication of every poll.
    "R-32": "test_an_unmoved_member_is_recorded_once",
    # R-33: media_fetch reads a stored image back through MiMo-V2.5, scoped to
    # THIS conversation's custody over a bounded window, cached by content hash
    # and screened on every serve, under its own vision timeout (derived from
    # llm.timeout_seconds, provably < the loop budget) and byte ceiling. The
    # MiMo description is untrusted egress — identifier-screened like `about`,
    # with the honest caveat that a bare local number is a residual risk equal
    # to R-18's. AMENDED by DEC-114 (2026-08-17): no longer "image only,
    # `not_an_image` for the rest" — a describable modality with no describer
    # configured is `unavailable` (a wiring gap, R-27), and only a blob no
    # describer reads is still `not_an_image`. Inert-by-config behind
    # [media] enabled. Next free number after the presence pair R-31/R-32
    # (R-26 is still reserved by design/proposed/18). See DEC-56.
    "R-33": "test_a_stored_image_is_read_only_within_scope_and_screened_on_serve",
    # R-34 RETIRED (DEC-73, 2026-08-08): it was the `fetch` fence — "may open only
    # a URL a search returned this turn" — defence-in-depth for the SSRF surface a
    # self-hosted Firecrawl created by fetching a model-chosen URL. `web_fetch` and
    # Firecrawl are gone (Tavily searches from its own servers and is never handed
    # a URL), so there is no fence and no rule; the number is not reused.
    # R-35: saved-place resolution is subset-scoped and falls through, never
    # refuses (design/23 §4.1, DEC-60). A room that is not a verified subset of
    # the circle resolves NO household place name — silently, so an outsider
    # cannot learn a guarded name EXISTS (the deliberate inverse of R-31's loud
    # refusal: there the subject is a person's position and silence would lie;
    # here the dictionary entry's existence is itself the secret).
    "R-35": "test_a_non_subset_room_resolves_no_saved_place",
    # R-36…R-39: reminders + the heartbeat (DEC-70). The next free numbers after
    # R-35; R-26 is still reserved by design/proposed/18.
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
    # reaches the later rows, so a cancel landing in that window must win. With
    # `mark` doing the take it did not, and a cancelled row went
    # CANCELLED -> FIRING -> DONE and was spoken.
    "R-37": "test_claim_is_a_compare_and_set_from_pending",
    # R-37b: the same rule across a CRASH, where it is a decision rather than a
    # mechanism (§8.2). A row left FIRING resolves to FAILED and is never
    # re-queued: nothing links a row to the message it may already have
    # delivered, so re-queueing double-sends anything that died after egress.
    # At-most-once — losing one reminder visibly beats saying it twice.
    "R-37b": "test_sweep_resolves_firing_rows_to_failed",
    # R-38: only a MAPPED person may create a reminder (§12). `requester_person_id`
    # is what the fire-time policy re-check runs against, so an unmapped sender
    # has no owner to write and inventing one would put unprompted speech into a
    # room under nobody's name. Enforced at `remind()` and re-checked at fire
    # time, because identity.toml loads once at startup and the horizon is 30 days.
    "R-38": "test_remind_refuses_an_unmapped_requester",
    # R-39: `remind` refuses on a FIRE TURN — the structural bar on recurrence
    # (§7.1, §9). Without it Nora re-arms her own reminder each time one comes
    # due, and none of §9's caps can stop her: `max_pending_per_requester` counts
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
    # R-42: a version is announced AT MOST ONCE, per room, ever (DEC-97). The
    # next free number after R-41. Nora's second unprompted-speech occasion, and
    # the failure it exists to prevent is specific: a container crash-looping
    # every few seconds would otherwise post the same upgrade note into a group
    # chat on every boot. The claim is an INSERT on a primary key taken BEFORE
    # the turn, so a row EXISTING — announced, failed, or stranded mid-turn —
    # bars a second attempt. That is R-37b's at-most-once decision applied to a
    # second path, and for the same reason: nothing links a claim to a message
    # that may already have reached the group.
    #
    # The named test simulates the restart the way a restart works — everything
    # in-process rebuilt, only the database surviving — because an in-memory
    # claim (or a read-then-write) passes any single-process assertion.
    "R-42": "test_a_restart_never_re_announces_the_same_version",
    # R-43: a compute job is delivered AT MOST ONCE, ever — the same
    # discipline R-37/R-37b hold for reminders, applied to the third
    # Occasion (DEC-99). The next free number after R-42.
    "R-43": "test_a_job_never_delivered_twice",
    # R-45: a one-shot nudge, not a refusal — make_artifact called with
    # context empty after place_info/route already ran this turn (DEC-101).
    # R-44 was withdrawn 2026-08-14 (the prior occupant of this range); the
    # next free number is R-45, not a reuse.
    "R-45": "test_make_artifact_is_nudged_once_when_context_is_empty_after_gathering",
    # design/discord-integration/ — a reply leaves on the platform its tag
    # arrived on; an unroutable platform refuses rather than falling back.
    "R-46": "test_a_discord_tag_is_answered_on_discord_not_signal",
    # R-47: the circle is who may be asked about; the room is only who may be
    # told. Scoping the target to room membership answered «nobody here goes by
    # that» about a friend Nora knows and can locate.
    "R-47": "test_a_circle_member_who_is_not_in_the_room_can_still_be_asked_about",
    # R-48: only a cacheable tool's successful call ever gets logged for later
    # recall (tool-recall-log spec). Renumbered from a first-draft R-46 at
    # merge time (2026-08-14) — R-46/R-47 above landed on master first, from
    # a concurrent branch that minted them independently for an unrelated
    # decision (Discord routing).
    "R-48": "test_a_non_cacheable_tool_is_never_logged",
    # R-49 (DEC-105): a relay crosses into another room only within one
    # workspace, only for somebody allowed in both, and only under their name.
    "R-49": "test_a_room_in_another_workspace_is_refused",
    # R-50 (DEC-106): her memory OF THE TAGGER rides in the prefill, and the
    # tool that would fetch it again points at the block instead. The duplicate
    # is the whole risk the block carries, so the guard is the named rule.
    "R-50": "test_about_the_tagger_points_at_the_block_instead_of_fetching_it",
    # R-51: a sticker description is never invented (sticker spec §6). Every
    # way `StickerReader`/`StickerInterpreter` can fail to produce one — a
    # timeout, an unreadable format, an empty read-back — must leave the bare
    # `[sticker a3f2c1]` placeholder, never a blank `[sticker a3f2c1: ]` and
    # never a guessed sentence. Next free number after R-50.
    "R-51": "test_r51_a_sticker_description_is_never_invented",
    # R-53 (DEC-123): a source that is SILENT on the question is not a source
    # that denies it. Behavioural, the same shape as R-14 and R-41 — asserted
    # by `conduct.md`'s "What a source does not say", measured by the corpus,
    # NOT a currently-passing gate. R-27's three-state discipline is enforced
    # on tool RESULTS; this is the same discipline one level up, on INFERENCE
    # OVER results, where nothing enforced it: a scoped article's omission was
    # read as a denial and overturned a correct sourced answer (audit F7,
    # 2026-08-17 02:16 UTC). Next free number after R-52.
    "R-53": "test_r53_is_covered_by_the_source_scope_corpus",
    # R-54 (fact-tier spec, DEC-78's move applied to inference over sources):
    # R-53 states this rule for the model, in prose, in conduct.md — and its
    # own corpus (`evals/adversarial/source_scope.yaml`) is deliberately held
    # out of `SUITES`, so the prose cannot be shown to hold as a passing gate.
    # R-54 is the same rule moved into a SHAPE instead: `FactVerdict.not_covered`
    # has no reachable `supersedes`, and `contradicted` requires both a source
    # and `addresses_claim` — «the source did not cover it, therefore drop the
    # fact» is unrepresentable rather than merely discouraged. Points at
    # `tests/domain/test_facts.py`'s own pin rather than a third copy of the
    # same assertion (review finding, task 9 round 1): that file already names
    # both halves — `test_not_covered_cannot_supersede_anything` and
    # `test_contradicted_requires_a_source_that_addresses_the_claim`.
    "R-54": "test_not_covered_cannot_supersede_anything",
    # R-56 (DEC-133, the Dreamer runtime). R-55 was taken by the note tier's
    # read-before-write latch, which landed first — re-read the index at MERGE
    # time, not at claim time.: the Dreamer may LOOK and may not
    # ACT. A background process that can speak to the group unprompted — or set
    # a reminder, or write to the events store — with nobody waiting on it is a
    # categorically different and more dangerous thing than one that can only
    # read, so this is a property of the runtime rather than a setting an
    # operator could get wrong.
    #
    # Points at the DERIVED set's own pin rather than a second copy of the
    # assertion, the same way R-54 points at `tests/domain/test_facts.py`.
    # `read_only_tools()` is computed from a field on each tool, and the test
    # named here measures the computed set against the act verbs — so a tool
    # added tomorrow is classified by whoever adds it and cannot silently join
    # the Dreamer's ceiling. The specification's own hand-written list of act
    # verbs was four short when it was checked, which is the best available
    # argument for the mechanism and the reason the hand-list lives in the TEST
    # while the code derives.
    "R-56": "test_no_act_verb_is_ever_read_only",
    # R-57: the block on a `where_is`-disclosed address entering `route` used
    # to live only in `nora/skills/navigation/SKILL.md` prose — unenforced any
    # turn the skill didn't load (confirmed live: the F1/Fung/Clara trace
    # that prompted this rule never called `skills` at all). `route`'s R-28
    # screen catches an identifier, a raw coordinate, a known person's name;
    # this is the fourth case, and it can only be caught by PROVENANCE — a
    # plain address is indistinguishable from a typed place by shape — so
    # `where_is` and `route` now share one turn-scoped set
    # (`_disclosed_live_addresses`) instead of a single shape screen.
    "R-57": "test_a_disclosed_live_position_is_refused_as_a_route_argument_in_an_untrusted_room",
    # R-58: the deployed signal-cli (0.14.7) never emits `voiceNote` over
    # JSON-RPC at all (confirmed by decompiling the shipped jar — the fix is
    # merged upstream but unreleased), so Signal's parser falls back to the
    # filename Signal's own recorder generates. That fallback is gated on the
    # resolved media type ALSO being audio/* — `nora/ingest/stages/media.py`
    # trusts `ref.voice_note` alone to route bytes to local STT (DEC-131), so
    # a bare filename match would let a sender route ANY attachment there by
    # naming it right, even one `label_for` still displays as `[file]`.
    "R-58": "test_a_matching_filename_on_a_non_audio_attachment_is_not_a_voice_note",
    # R-52 (DEC-117): `create_event` fail-closes on ambiguity — spec §3.1's
    # "more than one open event" case — refusing rather than guessing which
    # of two-or-more already-open events is meant.
    "R-52": "test_r52_two_open_events_refuses_a_third_rather_than_guessing",
    # R-55 (note-tier spec §6.2): a note revision is VETOED unless `note_read`
    # succeeded for that note in the same turn. The injected index carries
    # title + description only — enough to know a note exists, nowhere near
    # enough to revise one — and `note_update` replaces the note's rendered
    # body, so a revision written blind destroys the arrangement the tier
    # exists to hold. Next free number after R-54. The named test asserts
    # through `ReplyGuard.evaluate`'s refusal rather than by reading the
    # latch set: the set is invisible to the model, the veto is where a miss
    # costs something, and a test that reads the set goes green against a
    # latch that never vetoes anything.
    "R-55": "test_r55_a_revision_is_vetoed_when_this_turn_never_read_the_note",
    # DEC-111 (the Dreamer, room-language consolidator): her own words are
    # never evidence for the room lexicon. `RecordStore.since` — the
    # consolidation cursor `LanguageConsolidator` reads — excludes `is_nora`
    # rows IN THE QUERY, not in a caller that could forget it. The loop this
    # prevents is real: she emits a word, it strengthens, the block
    # normalises it, she emits it more.
    "DEC-111": "test_her_own_words_are_never_evidence_for_the_room_lexicon",
    # DEC-120 (Dreamer Plan 2, exchange patterns): the withhold rule DEC-111
    # deferred is a structural guarantee, not a convention — no model-authored
    # gloss field exists (a closed-set `act` only), and the database refuses
    # any row naming fewer than two people.
    "DEC-120": "test_exchange_pattern_cannot_attribute_a_disposition_to_a_person",
    # DEC-121 (Dreamer Plan 3, room portrait): the narrative-construction
    # function cannot be called with participant/speaker identity —
    # `_LexemeFact`/`_ExchangeFact` carry no `person_id`/`participants`
    # field, so identity is stripped at the TYPE boundary before any
    # narrative string is built, proved behaviourally against the real
    # synthesizer (a `CHECK` cannot forbid "contains a person's id" in a
    # free-text column, so this is the type-boundary sibling of DEC-120's
    # DB-CHECK guarantee, not a weaker substitute for it).
    "DEC-121": "test_room_portrait_narrative_cannot_reference_a_person",
    # R-59 (design/discord-music/03-state-and-persistence.md, "Connect
    # trigger — and its rejection"): starting a NEW music session for a
    # guild requires the requester to be in a voice channel; an existing
    # session is never gated this way (only STARTING one names a
    # destination channel). Renumbered from R-57 on merge (2026-08-23): R-57
    # and R-58 were independently claimed by two other sessions in the same
    # window (see those two entries above) — re-verified at merge time and
    # R-59/R-60 confirmed free.
    "R-59": "test_r59_play_refused_when_requester_not_in_voice_and_no_player_exists",
    # R-60 (same document, "Disconnect behavior"): a voice channel emptying
    # of humans disconnects immediately, no grace period — distinct from
    # timer 2 (queue empty, nothing playing -> idle-timeout leave), covered
    # by the sibling tests `test_queue_emptying_schedules_an_idle_
    # disconnect` and `test_a_new_track_before_the_idle_timeout_cancels_
    # the_pending_disconnect` in the same file. Renumbered from R-58 on the
    # same merge as R-59, for the same collision.
    "R-60": "test_r60_a_voice_channel_emptied_of_humans_disconnects_immediately",
    # R-61 (design/discord-music/07-agent-tool.md): Nora's agent music tools
    # (play_music/skip_music/top_music/stop_music/music_status) are
    # Discord-only and never guess an identity to act on. Off Discord, with no
    # `MusicToolBridge` wired, or with a tagger identity.toml maps to no
    # Discord account at all, every one answers `unavailable` before ever
    # touching `MusicPlayerManager` — R-59's own "requester must be in a
    # voice channel to start a session" rejection is reused unchanged
    # underneath (`DiscordMusicToolBridge` calls the identical
    # `MusicPlayerManager.play`), never re-implemented or bypassed here.
    "R-61": "test_play_music_is_unavailable_off_discord",
    # R-62 (design/discord-music/08-autoplay.md): when a Discord music
    # queue empties with nothing next, MusicPlayer._advance may seed ONE
    # autoplay pick from TrackSource.related() before falling back to the
    # ordinary idle-timeout behaviour, gated by [music] autoplay_enabled
    # (default false). related() returning empty falls straight through to
    # idle -- never retried or substituted with a guess.
    "R-62": "test_autoplay_enqueues_and_plays_a_related_track_when_the_queue_empties",
    "R-63": "test_autoplay_skips_tracks_already_played_this_session",
    # R-64 (design/discord-music/08-autoplay.md): with [music]
    # autoplay_prewarm_seconds above 0, MusicPlayer resolves the NEXT
    # autoplay pick (Mix lookup AND its playable stream) before the
    # current track ends, so the track-end advance plays the prewarmed
    # stream directly -- zero network calls at the gap. A prewarmed pick
    # already played this session is refused (R-63 dedup still applies).
    "R-64": "test_prewarm_resolves_the_next_autoplay_pick_before_the_track_ends",
    # R-65: a dead voice connection tears the session down like a stop
    # instead of stranding now-playing state.
    "R-65": "test_a_dead_voice_connection_tears_down_the_session",
    # R-66: DiscordVoiceSession monitors the voice websocket + audio thread
    # while a track is loaded, and on two consecutive dead observations
    # signals on_voice_death so the session is torn down like a stop.
    "R-66": "test_the_voice_death_monitor_signals_death_after_two_dead_ticks",
    # R-67: a completed prewarm that answered EMPTY gets ONE live `related()`
    # retry at the actual track end, never more -- only if the live answer is
    # also empty does the chain fall through to idle (R-62's "never retried"
    # discipline applied to the LIVE answer; the bound of one makes a loop
    # unreachable). External audit Codex MAJOR-1 (2026-08-26) first made the
    # prewarm's empty the FINAL answer; the live failure of 2026-09-01 --
    # autoplay went permanently silent after a transient empty prewarm Mix
    # that had recovered by track end -- bounded that to one retry.
    "R-67": "test_an_empty_prewarm_is_retried_once_at_track_end",
    # R-68: every transition that can touch a guild's MusicPlayer runs under
    # that guild's lock -- skip/stop/channel-empty/idle-timeout join
    # play()/on_track_finished()/on_voice_death() so a natural track-end,
    # the voice-death monitor, and a user command can never interleave
    # (external audit Codex MAJOR-2). resume_from_persisted's voice.play()
    # and the dead-voice teardown's session delete are both exception-safe
    # (Codex MAJOR-3/4).
    "R-68": "test_a_dead_voice_connection_tears_down_the_session",
    # R-88: the most recent track that ACTUALLY started is persisted as
    # `music_session.last_played_*` and survives the queue emptying, so a
    # boot-time resume that finds nothing playing re-seeds the autoplay
    # chain from it instead of sitting silent in a live voice channel.
    # (Numbered at gate-cleanup time: the change landed 2026-09-01
    # mislabelled "R-67", colliding with the prewarm rule above.)
    "R-88": "test_advance_seeds_autoplay_from_last_played_when_nothing_playing",
    # R-89 (DEC-159, #109): a series may not repeat faster than
    # `[reminders] min_interval_seconds`, and the tool REFUSES rather than
    # clamping — a silently shortened series is the same broken promise the
    # issue was opened about.
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
    # R-70 names the test that would have PREVENTED the defect rather than the
    # one that detects a hang: the budget was wrong in prose (60+20 against an
    # [llm] timeout that is 120 and required-with-no-default, omitting the two
    # store calls entirely), and this is the gate that now refuses exactly that
    # arithmetic. The hang tests and the window pair are named in the index row.
    "R-70": "test_the_standing_slot_hold_refuses_a_budget_the_timeouts_do_not_fit",
    # R-71 names the never-rewritten test rather than the position-survives
    # one: both are in the index row, but a rewritten verdict is the failure
    # the SPLIT from `Standing` exists to prevent, and it is the one a future
    # writer reintroduces by adding a convenient upsert.
    "R-71": "test_a_second_verdict_for_one_turn_is_refused",
    # R-72 names the PRECEDENCE test rather than either trigger: getting the
    # order backwards is the failure that silently corrupts §6's own reading of
    # whether the trigger has degraded into a clock.
    "R-72": "test_ungrounded_wins_when_both_conditions_hold",
    # R-73 names the IDENTIFIER test rather than the coordinate one: both are
    # in the index row, but a phone number reaching a third-party collector is
    # the exposure `nora.agent.egress` exists for, and the prompt side is the
    # half that carries the turn's raw tool results.
    "R-73": "test_an_identifier_never_reaches_the_collector",
    # R-74 names the no-evidence refusal rather than the citation check,
    # because it is the clause a future change is most likely to "simplify":
    # a passed anchor date LOOKS sufficient for expiry, and treating it as
    # sufficient is what turns a quiet room into a swept notebook. The other
    # five grounds are named in the decision index row.
    "R-74": "test_no_retirement_is_reachable_without_evidence",
    # R-76 names the CHILD test rather than either drop test, and that is the
    # deliberate choice: «ingest is not exported» is the symptom everyone
    # remembers, but the rule a future change breaks is that the decision is
    # about ROOTS. Applying it per-span looks like a simplification, passes the
    # ingest tests, and silently punches holes in every real turn's trace where
    # its stages used to be. That test declares a CHILD `plumbing` under a
    # `generation` root and requires it exported anyway; the two drop tests and
    # the two engagement ones are named in the index row.
    "R-76": "test_a_generation_root_keeps_the_plumbing_underneath_it",
    # The knowledge tier's two new behavioural rules — R-77/R-78, not 75/76:
    # master took those first for the note tier's date-only collision and the
    # Langfuse root rule, and the number goes to whoever landed. Both of these
    # are enforced where the WRITES happen rather than at a render, which is
    # why their named tests live beside the stores.
    "R-77": "test_a_claim_about_a_roster_person_is_refused_at_the_sink",
    "R-78": "test_degree_two_promotes_and_degree_one_does_not",
    # R-79 names the LEXEME render, not the fact one, and that is the whole
    # point of the choice. «A dispute marks rather than mutes» held in the
    # fact tier from the first commit and reddens under mutation there; the
    # lexeme half wrote its four columns and NOTHING READ THEM, so the
    # contradicted gloss kept rendering as unqualified truth while
    # `nora_dispute_targets_total` reported `ok`. The clause a future change
    # breaks is the read, and this is the tier where it was already broken
    # once. The fact-render tests and the store round trip are named in the
    # index row.
    "R-79": "test_a_disputed_gloss_renders_marked_and_still_renders",
    # R-80 names the MIXED-GEOMETRY test rather than the re-embed one, and the
    # choice is the same as R-79's: both are needed, and the one a future
    # change breaks is the filter. `model=` reads like a convenience argument
    # — the kind a later author deletes as tidying — and the design bundle
    # never raises the problem it solves, so the row and this entry are the
    # only places the reason is written down.
    "R-80": "test_the_dense_read_never_mixes_two_models_geometries",
    # R-81 (DEC-145): attribution is SOURCING. The debrief's grounding rubric
    # asked whether a claim was supported by «something someone said in the
    # transcript» and said in as many words that it was «not about
    # correctness» — so a reply that put one person's words in another
    # person's mouth passed, because the words WERE said, by someone. The
    # named test asserts on the prompt the fold was actually given rather
    # than on the markdown, because the clause only does anything where the
    # model can read it; its second half reads the same prompt back for the
    # speaker labels the rule presupposes. Next free number after R-80.
    "R-81": "test_r81_a_quote_is_grounded_only_against_the_person_who_said_it",
    # R-82 names the fail-closed branch rather than the happy path, because it
    # is the clause a future change is most likely to "simplify": an empty
    # window LOOKS like an empty room, and `nothing_found` reads as the more
    # informative answer. It is not — `EngagementContext._recent` returns `()`
    # for a FAILED store read too, so that simplification turns an outage into
    # a confident denial in the middle of a group argument, which is the exact
    # failure `who_said` was built to stop. The window-not-the-store test, the
    # DEC-24 schema test and the span tests are named in the index row.
    "R-82": "test_an_unread_window_is_unavailable_never_nothing_found",
    # R-83 names the CARVE-OUT test rather than the tagging one. Both halves
    # ship in the same `conduct.md` section bump, but only one of them is the
    # rule: the tagging clause is a mechanism joined to a position that already
    # existed, while the carve-out is the branch that did not exist at all —
    # and it is the half a rewrite loses, because the paragraph it sits under
    # reads as complete without it (the same failure mode R-53's third clause
    # has, recorded in `test_conduct_separates_a_source_that_is_silent…`).
    "R-83": "test_r83_the_anti_cave_position_is_scoped_to_a_checked_claim",
    # R-84: the music message table. The named test is the module that pins
    # every string the table renders — a wording change to ANY music surface
    # now has to land there, so the diff shows it.
    "R-84": "test_now_playing_line_carries_title_and_url",
    # R-86 (DEC-151): `social_fetch` is read-only at three layers, and the
    # named test DERIVES all three from the code — the bridge's allowlist
    # against OpenCLI's write verbs, the tool's (platform, what) vocabulary
    # against that allowlist, and the NativeTool declaration — so a hand-edit
    # to any one layer that drifts from the others goes red.
    "R-86": "test_r86_social_fetch_is_read_only_at_three_layers",
    # R-87 (design/standing-watermark): Standing folds read SINCE a row-id
    # watermark (`since_including_nora`, capped at [standing] fold_batch), not
    # the reply path's recency window — a conversation that passes 40 messages
    # between tags must not lose the middle. The named test drives the real
    # stage through a store holding 60 rows and asserts the fold's prompt
    # reaches the row the old 40-row window would have clipped.
    "R-87": "test_r87_the_standing_fold_reads_since_a_row_id_watermark",
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
    assert (ROOT / "evals" / "adversarial" / "injections.yaml").exists()
    assert (ROOT / "evals" / "adversarial" / "transcript_injection.yaml").exists()


def test_r41_is_covered_by_the_identity_grounding_corpus():
    """R-41 is behavioural: asserted by conduct.md, measured by this corpus.

    Unlike R-14, this corpus is NOT currently a passing gate — DEC-75 records
    that the shipped doctrine wording still fabricates on 3/3 of its ambiguous
    cases (the grounded judge's number; the earlier "0/3" was the BLIND judge
    failing to detect it). This test pins that the measurement exists and is
    findable, not that the rule holds; see DEC-75 for the honest number.
    """
    assert (ROOT / "evals" / "adversarial" / "identity_grounding.yaml").exists()


def test_r53_is_covered_by_the_source_scope_corpus():
    """R-53 is behavioural: asserted by conduct.md, measured by this corpus.

    Same posture as R-41 above, and for the same reason. The corpus replays the
    2026-08-17 incident's SHAPE — a source narrower than the question, whose
    silence about the rest was read as a denial — with the game and the objects
    changed so a pass cannot be a memory of the real one, plus two controls: a
    source that genuinely denies (she may overturn) and a source that covers the
    question at the right scope (she answers plainly). Without those, a fix that
    overcorrected into hedging everything would score as a pass.

    It is deliberately NOT wired into `evals/run.py`'s `SUITES`: it is a
    measured record of the doctrine gap, not a gate the shipped doctrine is
    known to clear. This test pins that the measurement exists and is findable,
    not that the rule holds.
    """
    assert (ROOT / "evals" / "adversarial" / "source_scope.yaml").exists()


def _message(*, platform: str, body: str, media: tuple[MediaRef, ...]) -> NormalizedMessage:
    return NormalizedMessage(
        platform=platform, conversation_id="g1", conversation_type="group",
        sender_id="+85212345678", sender_name="Miles", timestamp=1700,
        body=body, raw={}, media=media,
    )


async def test_r51_a_sticker_description_is_never_invented():
    """R-51. Every way reading a sticker can fail — the model timing out, the
    format being unreadable, the bytes being gone, the description carrying an
    identifier — leaves the placeholder and is counted. None of them produces a
    blank description, and none produces a guessed one. An ERROR is never a
    PASS."""
    ref = MediaRef(platform_id="", media_type="image/png", sticker=True,
                   fetch_token="sticker:p:1")  # noqa: S106
    for interpreter in (
        FakeMediaInterpreter(available=False, reason="timeout"),
        FakeMediaInterpreter(available=False, reason="unsupported_format"),
        FakeMediaInterpreter(""),
    ):
        reader = StickerReader(
            blobs=FakeBlobStore(), descriptions=FakeMediaDescriptionStore(),
            interpreter=interpreter, model="fake", metrics=Metrics(),
        )
        out = await media_stage.run(
            message=_message(platform="signal", body="[sticker]", media=(ref,)),
            sources=MediaSourceRegistry([FakeMediaSource({"sticker:p:1": b"\x89PNG"})]),
            blobs=FakeBlobStore(), metrics=Metrics(), stickers=reader,
        )
        assert out.body.startswith("[sticker ")
        assert ": " not in out.body, f"{interpreter.reason} invented a description"


async def test_her_own_words_are_never_evidence_for_the_room_lexicon(pg_url):
    """DEC-111. The runaway loop this prevents: she emits a word -> it
    strengthens -> the block normalises it -> she emits it more. `is_nora = 0`
    lives in `RecordStore.since` itself (`nora/infra/sql/audit.py`) — the
    Dreamer's consolidation cursor — not in a caller that could forget it.

    Reverting the predicate must turn this red: it is asserted here against
    the real `SqlRecordStore`, not the fake, because the predicate lives in
    the SQL `WHERE` clause itself."""
    run_migrations(pg_url)
    store = SqlRecordStore(create_sql_engine(pg_url))
    try:
        ids = []
        for i, (body, is_nora) in enumerate(
            [("what's the play tmr", False), ("the play is at 8pm", True)], start=1
        ):
            ids.append(await store.append(
                message=NormalizedMessage(
                    "signal", "g1", "group", "+852111", "Miles", i, body, {}),
                is_nora=is_nora,
            ))

        rows = await store.since(conversation_id="g1", after_id=0, limit=10)

        assert [r.body for r in rows] == ["what's the play tmr"], (
            "a Nora-authored row reached the Dreamer's evidence stream"
        )
    finally:
        await store.aclose()


async def test_exchange_pattern_cannot_attribute_a_disposition_to_a_person(pg_url):
    """DEC-120. Two structural guarantees, proved at the layer that cannot be
    bypassed — mirrors `test_her_own_words_are_never_evidence_for_the_room_lexicon`'s
    own "assert it where it cannot be forgotten" shape.

    (1) `_parse_item` rejects any `act` outside the closed set, at PARSE
    time — no model-authored gloss field exists anywhere on this feature for
    a rejected claim to fall back into.
    (2) The DATABASE, not application code, refuses a row naming fewer than
    two participants — proved with a direct INSERT against `room_exchange`,
    bypassing every Python-side guard, exactly the "prove it at the layer
    that cannot be bypassed" argument DEC-111's own test makes for `is_nora`.
    """
    # (1) closed-set enforcement at parse time.
    assert _parse_item({
        "act": "made_up_act_not_in_the_set", "message_ids": [1, 2], "confidence": 0.9,
    }) is None, "an act outside EXCHANGE_ACTS must be rejected, not stored"
    assert _parse_item({
        "act": "insult_trade", "message_ids": [1, 2], "confidence": 0.9,
    }) is not None, "a genuine closed-set act must still parse"

    # (2) the database refuses a single-participant row.
    run_migrations(pg_url)
    engine = create_sql_engine(pg_url)

    async def _insert_one_participant() -> None:
        # `pytest.raises` wraps this WHOLE call, not a statement inside the
        # `async with` — the exception must still be in flight when
        # `engine.begin()`'s own `__aexit__` runs, so it rolls back rather
        # than trying (and failing a SECOND time) to commit an already
        # -aborted Postgres transaction.
        async with engine.begin() as conn:
            await conn.execute(sa.insert(ROOM_EXCHANGE).values(
                scope_id="g1", act="insult_trade", participants=["miles"],
                first_seen_at=1, last_seen_at=1,
            ))

    try:
        with pytest.raises((IntegrityError, DBAPIError)):
            await _insert_one_participant()
    finally:
        await engine.dispose()


async def test_room_portrait_narrative_cannot_reference_a_person():
    """DEC-121. The behavioural, grounded version of the guarantee (R-27:
    bind to the real property) — a `CHECK` cannot forbid "contains a
    person's id" in `room_portrait.narrative` (free text), so this proves it
    the way DEC-111's own test does for `is_nora`: at the layer that cannot
    be bypassed, which here is the REAL synthesizer, not a mock of it.

    `LexemeExample.person_id` and `RoomExchangePattern.participants` both
    carry a deliberately identifiable marker; if `PortraitSynthesizer` ever
    started threading either field into `_build_narrative` (directly, or by
    widening `_LexemeFact`/`_ExchangeFact` to carry it), this test goes red.
    """
    marker = "WITNESS_MARKER_person_should_never_appear"

    class _FakeLanguageReader:
        async def room_language(self, *, scope_id: str, limit: int) -> RoomLanguage:
            return RoomLanguage(scope_id=scope_id, lexemes=(
                RoomLexeme(
                    canonical_form="big walk", sense="a group hike", occurrence_count=3,
                    distinct_speakers=2, first_seen_ms=1, last_seen_ms=2, state="live",
                    examples=(
                        LexemeExample(message_id=1, person_id=marker, text="big walk?"),
                    ),
                ),
            ))

    class _FakeExchangeReader:
        async def room_exchange(self, *, scope_id: str, limit: int) -> RoomExchange:
            return RoomExchange(scope_id=scope_id, patterns=(
                RoomExchangePattern(
                    act="banter", participants=(marker, "someone_else"),
                    occurrence_count=4, first_seen_ms=1, last_seen_ms=2, state="live",
                    examples=(
                        ExchangeExample(message_id=1, person_id=marker, text="lol stop"),
                    ),
                ),
            ))

    class _FakePortraitStore:
        def __init__(self) -> None:
            self.narrative: str | None = None

        async def upsert_portrait(
            self, *, scope_id: str, narrative: str,
            lexeme_basis: tuple[str, ...], exchange_basis: tuple[str, ...],
        ) -> None:
            self.narrative = narrative

    store = _FakePortraitStore()
    synthesizer = PortraitSynthesizer(
        language=_FakeLanguageReader(), exchange=_FakeExchangeReader(), store=store,
        max_rendered_lexemes=6, max_rendered_exchanges=4,
    )
    await synthesizer.synthesize(scope_id="g1")

    assert store.narrative is not None and store.narrative != "", (
        "the synthesizer should have produced a narrative from real material"
    )
    assert marker not in store.narrative, (
        f"a person identifier leaked into the room portrait narrative: {store.narrative!r}"
    )


def test_recall_lookups_is_offered_as_evidence_not_only_as_a_cache():
    """DEC-112. Measured 2026-08-17: `recall_lookups` was reached for 0 times
    across 30 consecutive live turns while the log it reads held 21 rows, and
    she retracted four sourced, correct answers as 老作 rather than read them
    back. The cause was its `when` clause, which sold the tool as a COST saving
    — «call this before re-running…» — and being called a liar does not feel
    like being about to re-search.

    DEC-150 deleted the `when` clause and the capability block it fed. The
    evidence framing survives in the tool's SCHEMA docstring — the single
    source of truth for what the tool is for — and the position lives in
    `conduct.md`. Revert either and this goes red.

    The conduct.md assertions below were re-pinned to the compressed
    (35293bb5) phrasing; the positions are unchanged.
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

    conduct = (ROOT / "nora" / "doctrine" / "conduct.md").read_text()
    section = conduct.split("# Being contradicted", 1)[1].split("\n# ", 1)[0]
    # The prose is hard-wrapped, so every phrase below spans a newline in the
    # file; match on the sentence, not on the line breaks it happens to have.
    contradicted = " ".join(section.split())

    # Not remembering the checking is not evidence the checking did not happen.
    assert "唔記得有冇查過，唔代表冇查過" in contradicted, contradicted
    # She has a record of what she checked, still in the log, and is told to
    # read it before concluding she guessed.
    assert "之前查過嘅根據可能仲喺記錄度" in contradicted, contradicted
    # Conceding a fault she does not have is a lie, not modesty.
    assert "blame turned" in contradicted, contradicted

    # The position must NOT name the tool: tools are described by their schemas,
    # and an authored copy is a cache with no invalidation.
    assert "recall_lookups" not in conduct


def _doctrine(name: str) -> str:
    """Whitespace-normalised, because every doctrine file is hard-wrapped and
    every phrase asserted below spans a line break in it."""
    return " ".join(
        (ROOT / "nora" / "doctrine" / f"{name}.md").read_text().split()
    )


def test_r83_the_anti_cave_position_is_scoped_to_a_checked_claim():
    """R-83 / DEC-147. Measured 2026-08-28, live: Nora told a group member he
    had said 「收隊」. Somebody else had. She never looked — she matched the most
    aggressive strings in the room onto the person who was denying her — and
    then three surfaces built for a DIFFERENT case held her there: this file's
    own «not remembering having checked is not evidence that you did not
    check» (DEC-112), `voice.md`'s 「冇錯就冇嘢認 … 企硬」, and `send_reply`'s
    «never invent a mistake you did not make just to have something to
    concede» (DEC-92). Each is right for a claim with a lookup behind it. Put
    together they convert an UNGROUNDED assertion into a defended one, because
    not one of them has a branch for «I never grounded this».

    The branch is R-27's three states read off her own record: found is `ok`
    and she holds it; not found — or not readable — is `unavailable`, and
    going to look, or letting it drop, is the reply. That is R-5's temperament
    («silence beats a wrong answer») on the contradiction path, which is the
    one place nothing had wired it.

    Pinned as a POSITION and not as the incident's nouns, the same way R-53 is,
    so it cannot be satisfied by a sentence about 收隊:

      1. holding is conditional — it is what checking bought,
      2. a claim she cannot find herself having grounded is not one she stands
         on, however sure it feels,
      3. there is a real reply for that case, and it is not a defence.

    THE POSITIVE CONTROL is the second half. A carve-out that ate the anti-cave
    stance would satisfy any presence check on the three moves above while
    quietly undoing DEC-112 and DEC-123, so the clauses that hold her on a
    CHECKED claim are asserted still present, on both sides of the DEC-92
    split. The tool-surface third of that armour is pinned by
    `tests/test_tools.py::test_the_send_reply_schema_carries_the_three_reply_defects`
    and is deliberately not re-asserted here — two copies of one literal is the
    unguarded-copy failure this repo keeps paying for.

    HONEST SCOPE: a content test, the same posture as
    `test_recall_lookups_is_offered_as_evidence_not_only_as_a_cache` above. It
    measures that the position is stated, and where. It does not measure that
    the model follows it — there is no corpus for this one — and DEC-78/DEC-92's
    finding that prose alone does not move behaviour is untested either way.

    The conduct.md assertions below were re-pinned to the compressed
    (35293bb5) phrasing; the positions are unchanged.
    """
    conduct = _doctrine("conduct")
    voice = _doctrine("voice")

    # The carve-out lives in the section that owns whether a challenge should
    # move her at all. `voice.md` owns how it SOUNDS and is operator-only; a
    # copy there is the drift DEC-92's split exists to prevent.
    section = " ".join(
        (ROOT / "nora" / "doctrine" / "conduct.md").read_text()
        .split("# Being contradicted", 1)[1].split("\n# ", 1)[0].split()
    )

    # 1. Holding is conditional on having checked.
    assert "有根據就守住，反證夠就改" in section, section
    # 2. The branch that did not exist: no grounding, no position to stand on —
    #    and the confidence that shows up under pressure is worth least.
    assert "搵唔返自己曾經 ground 過，就唔好靠感覺硬撐" in section, section
    assert "唔好將新出現嘅自信當證據" in section
    # 3. R-5's temperament: there is a real reply, and neither branch of it is
    #    a defence of the ungrounded claim.
    assert "話「我去睇返」或者放低件事都可以" in section, section

    # POSITIVE CONTROL — the armour for a CHECKED claim is untouched. Delete
    # any of these to buy the carve-out and this goes red, which is the whole
    # point: the ask was a missing branch, not a weaker stance.
    assert "唔記得有冇查過，唔代表冇查過" in conduct
    assert "之前查過嘅根據可能仲喺記錄度" in conduct
    assert "Agreeing you invented something when you did not is not modesty" in conduct
    assert "冇錯就冇嘢認" in voice
    assert "企硬" in voice


def test_the_verdict_position_names_the_mechanism_that_performs_a_tag():
    """DEC-147's other half. `conduct.md`'s 評判 section has said since v11 that
    the losing side is why she is there, to be told to their face and tagged
    rather than reported on (the clauses are asserted below, in string
    literals, so this paragraph does not become a second copy of them), and
    `send_reply`'s schema has taught since DEC-86 that an `@` written
    immediately before the name the room uses for somebody makes a real tag.
    Nothing joined them: the position lived in one surface and its only
    mechanism in another, and in the 2026-08-28 incident she addressed nobody
    at all. 「tag 返佢」 read as a manner of speaking, which is exactly how a
    verdict about somebody gets delivered past them.

    The doctrine now says what a tag IS, in one sentence, with no worked
    example and no name — DEC-96's rule, that every concrete example becomes a
    closed set, applies to a handle as much as to an emoji. The example belongs
    on the schema, where `tests/test_tools.py::
    test_the_tag_form_the_model_is_told_to_write_is_the_one_the_composer_resolves` drives it
    through the real composer; the doctrine carries the position only.
    """
    section = " ".join(
        (ROOT / "nora" / "doctrine" / "conduct.md").read_text()
        .split("# 評判", 1)[1].split("\n# ", 1)[0].split()
    )

    # The join itself: 「tag 返佢」 is the act, not a description of one.
    assert "「tag 返佢」唔係一個講法" in section, section
    assert "打個 @" in section, section
    # Against the name the ROOM uses — the same binding the composer resolves.
    assert "個房叫佢嗰個名" in section, section
    # And the reason, which is the position it was always attached to: without
    # the tag she is reporting on him however hard the line lands.
    assert "唔係同佢講" in section, section

    # POSITIVE CONTROL — the verdict position it hangs off is still there. If a
    # rewrite keeps the mechanism and loses 「唔好淨係報導」, the sentence
    # becomes formatting advice. (The 35293bb5 compress reworded the clause
    # from 「唔好企喺度報導」 to 「唔好淨係報導」 — same position; the
    # mechanism sentence above is what was restored.)
    assert "唔好淨係報導邊個啱邊個錯" in section, section

    # The doctrine states the position and NOT the example: a handle written
    # here would be the closed set DEC-96 measured, and the schema is where a
    # worked one is already pinned against the composer.
    assert not re.search(r"@\w", section), (
        "conduct.md ships a worked @handle — that is the schema's job, and a "
        "name in the doctrine becomes the only name she uses"
    )


def test_r86_social_fetch_is_read_only_at_three_layers():
    """R-86 / DEC-151. `social_fetch` reads X and Instagram through OpenCLI,
    whose write commands (`twitter post`/`like`/`follow`/`delete`, …) drive an
    AUTHENTICATED browser — a model that can be prompt-injected by the very
    content it fetches must never reach one. The read-only property is
    enforced at three layers, and this test DERIVES all three from the code
    (never hand-copied duplicates), so an edit to any one layer that drifts
    from the others goes red here:

    1. the tool's closed `(platform, what)` vocabulary
       (`nora.agent.tools._SOCIAL_PLATFORMS`/`_SOCIAL_WHATS`) — there is no
       free-form command passthrough;
    2. the host-side bridge's allowlist
       (`nora.infra.social.bridge_server.READ_ONLY_ALLOWLIST`), the single
       source of truth for the argv mapping — its command slot is only ever
       filled from the mapping's values, so no write verb is constructible
       (the behavioural half — a refused pair never spawns — is pinned by
       `tests/unit/test_social_bridge_server.py`);
    3. the `NativeTool` declaration `read_only=True`, the Dreamer's ceiling.
    """
    from nora.agent import tools as tools_module
    from nora.infra.social import bridge_server

    # Layer 2: every command the bridge can spawn is disjoint from OpenCLI's
    # write verbs (the set in `bridge_server.OPENCLI_WRITE_VERBS`, verified
    # against `opencli list -f json` 1.8.7 on 2026-08-30).
    for _site, command in bridge_server.READ_ONLY_ALLOWLIST.values():
        assert command not in bridge_server.OPENCLI_WRITE_VERBS, (
            f"the bridge can spawn a WRITE verb: {command}"
        )

    # Layer 1 matches layer 2 exactly: the tool's vocabulary IS the bridge's
    # allowlist plus the one recorded limitation (`instagram post` — OpenCLI
    # 1.8.7 has no caption-by-URL command), which the tool answers
    # `unavailable` itself and the bridge refuses without spawning.
    tool_pairs = {
        (platform, what)
        for platform in tools_module._SOCIAL_PLATFORMS
        for what in tools_module._SOCIAL_WHATS
    }
    assert tool_pairs - set(bridge_server.READ_ONLY_ALLOWLIST) == (
        tools_module._SOCIAL_UNSUPPORTED
    )

    # Layer 3: the declaration the Dreamer's ceiling is derived from.
    assert tools_module.NATIVE_TOOLS["social_fetch"].read_only is True


async def write_prior(store) -> None:
    from nora.domain.standing import Standing

    await store.write(standing=Standing(
        platform="signal", conversation_id="g1", body="the carried position",
        fold_count=3, updated_at=100, consumed_verdict_tag=None,
        last_folded_row_id=10))


async def test_r87_the_standing_fold_reads_since_a_row_id_watermark():
    """R-87 (design/standing-watermark). The standing fold reads
    `since_including_nora(after_id=last_folded_row_id, limit=fold_batch)` —
    everything since the last fold, capped at the batch — NOT the reply path's
    recency window. The defect this fixes: a conversation that passes 40
    messages between tags used to lose the middle, because the fold only ever
    saw the last `window` (40) rows.

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

        async def complete(self, *, prompt: str) -> debrief.Completion:
            self.prompts.append(prompt)
            return debrief.Completion(
                json.dumps({"standing": "the revised position"}), "stop")

    class _Objective:
        def objective_body(self, name: str) -> str:
            assert name in {debrief.OBJECTIVE, debrief.VERDICT_OBJECTIVE}
            return "OBJECTIVE-TEXT" if name == debrief.OBJECTIVE else "VERDICT-TEXT"

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
        store_verdict=None, notes_sink=None, workspace_of=lambda p, c: "ws",
        client=client, objective=_Objective(), metrics=Metrics(),
        display_name="Nora", record_store=record, max_chars=1200, fold_batch=200,
        timeout_seconds=5.0, store_timeout_seconds=5.0,
        rebuild_after_folds=20, now_ms=60,
    )
    # The prompt must reach back past any 40-row window: rows 11-20 are the
    # stretch a window fold clipped, and they are the whole point.
    joined = client.prompts[0]
    assert "m11" in joined, "the fold must read the stretch since the watermark"
    assert "m60" in joined
    row = await store.read(platform="signal", conversation_id="g1")
    assert row is not None and row.last_folded_row_id == 60

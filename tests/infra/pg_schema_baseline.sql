"end" timestamp with time zone,
"end" timestamp with time zone,
"position" integer NOT NULL,
"when" timestamp with time zone,
"when" timestamp with time zone,
$$;
$$;
$$;
$$;
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
);
ADD CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num);
ADD CONSTRAINT analytics_watermark_pkey PRIMARY KEY (deriver_name, subject_id);
ADD CONSTRAINT artifacts_pkey PRIMARY KEY (id);
ADD CONSTRAINT artifacts_slug_key UNIQUE (slug);
ADD CONSTRAINT assess_report_pkey PRIMARY KEY (id);
ADD CONSTRAINT blob_tombstone_pkey PRIMARY KEY (id);
ADD CONSTRAINT cognition_carry_pkey PRIMARY KEY (id);
ADD CONSTRAINT cognition_self_wake_pkey PRIMARY KEY (id);
ADD CONSTRAINT cognition_self_wake_session_id_fkey FOREIGN KEY (session_id) REFERENCES public.cognition_session(id);
ADD CONSTRAINT cognition_session_close_pkey PRIMARY KEY (id);
ADD CONSTRAINT cognition_session_pkey PRIMARY KEY (id);
ADD CONSTRAINT cognition_wake_outcome_pkey PRIMARY KEY (id);
ADD CONSTRAINT cognition_wake_pkey PRIMARY KEY (id);
ADD CONSTRAINT compute_jobs_pkey PRIMARY KEY (id);
ADD CONSTRAINT computer_events_pkey PRIMARY KEY (id);
ADD CONSTRAINT computer_exports_pkey PRIMARY KEY (id);
ADD CONSTRAINT computer_sessions_pkey PRIMARY KEY (id);
ADD CONSTRAINT config_revision_pkey PRIMARY KEY (id);
ADD CONSTRAINT consolidation_watermark_pkey PRIMARY KEY (consolidator_name, scope_id);
ADD CONSTRAINT culture_head_revision_id_fkey FOREIGN KEY (head_revision_id) REFERENCES public.culture_revision(id);
ADD CONSTRAINT culture_pkey PRIMARY KEY (key);
ADD CONSTRAINT culture_revision_pkey PRIMARY KEY (id);
ADD CONSTRAINT decision_log_pkey PRIMARY KEY (id);
ADD CONSTRAINT deploy_revision_pkey PRIMARY KEY (id);
ADD CONSTRAINT doctrine_revision_pkey PRIMARY KEY (id);
ADD CONSTRAINT document_state_pkey PRIMARY KEY (id);
ADD CONSTRAINT documents_pkey PRIMARY KEY (id);
ADD CONSTRAINT dream_schedule_state_pkey PRIMARY KEY (adaptor_name, scope_id);
ADD CONSTRAINT event_revision_event_id_fkey FOREIGN KEY (event_id) REFERENCES public.events(id);
ADD CONSTRAINT event_revision_parent_revision_id_fkey FOREIGN KEY (parent_revision_id) REFERENCES public.event_revision(id);
ADD CONSTRAINT event_revision_pkey PRIMARY KEY (id);
ADD CONSTRAINT event_rsvps_event_id_fkey FOREIGN KEY (event_id) REFERENCES public.events(id);
ADD CONSTRAINT event_rsvps_pkey PRIMARY KEY (id);
ADD CONSTRAINT events_pkey PRIMARY KEY (id);
ADD CONSTRAINT exchange_observation_pkey PRIMARY KEY (id);
ADD CONSTRAINT exchange_observation_scope_id_act_message_ids_key UNIQUE (scope_id, act, message_ids);
ADD CONSTRAINT fact_asserter_fact_id_fkey FOREIGN KEY (fact_id) REFERENCES public.fact(id) ON DELETE CASCADE;
ADD CONSTRAINT fact_asserter_pkey PRIMARY KEY (fact_id, person_id, message_id);
ADD CONSTRAINT fact_candidate_pkey PRIMARY KEY (id);
ADD CONSTRAINT fact_candidate_triple_key UNIQUE (unit, subject_id, predicate, object);
ADD CONSTRAINT fact_entity_pkey PRIMARY KEY (id);
ADD CONSTRAINT fact_entity_unit_name_key UNIQUE (unit, canonical_name);
ADD CONSTRAINT fact_object_entity_id_fkey FOREIGN KEY (object_entity_id) REFERENCES public.fact_entity(id);
ADD CONSTRAINT fact_pkey PRIMARY KEY (id);
ADD CONSTRAINT fact_premise_fact_id_fkey FOREIGN KEY (fact_id) REFERENCES public.fact(id) ON DELETE CASCADE;
ADD CONSTRAINT fact_premise_pkey PRIMARY KEY (fact_id, premise_fact_id);
ADD CONSTRAINT fact_premise_premise_fact_id_fkey FOREIGN KEY (premise_fact_id) REFERENCES public.fact(id) ON DELETE CASCADE;
ADD CONSTRAINT fact_superseded_by_fkey FOREIGN KEY (superseded_by) REFERENCES public.fact(id);
ADD CONSTRAINT fk_cognition_session_close_session_id FOREIGN KEY (session_id) REFERENCES public.cognition_session(id);
ADD CONSTRAINT geocode_cache_pkey PRIMARY KEY (lat_rounded, lng_rounded);
ADD CONSTRAINT instagram_summons_comment_id_key UNIQUE (comment_id);
ADD CONSTRAINT instagram_summons_message_id_key UNIQUE (message_id);
ADD CONSTRAINT instagram_summons_pkey PRIMARY KEY (id);
ADD CONSTRAINT language_observation_message_id_canonical_form_sense_key UNIQUE (message_id, canonical_form, sense);
ADD CONSTRAINT language_observation_pkey PRIMARY KEY (id);
ADD CONSTRAINT lesson_pkey PRIMARY KEY (id);
ADD CONSTRAINT lesson_revision_pkey PRIMARY KEY (id);
ADD CONSTRAINT media_objects_pkey PRIMARY KEY (sha256);
ADD CONSTRAINT memo_pkey PRIMARY KEY (id);
ADD CONSTRAINT memo_revision_pkey PRIMARY KEY (id);
ADD CONSTRAINT memory_edge_pkey PRIMARY KEY (src_kind, src_id, rel, dst_kind, dst_id);
ADD CONSTRAINT memory_index_state_pkey PRIMARY KEY (id);
ADD CONSTRAINT memory_node_pkey PRIMARY KEY (id);
ADD CONSTRAINT memory_projection_queue_pkey PRIMARY KEY (kind, source_id);
ADD CONSTRAINT message_delete_message_id_fkey FOREIGN KEY (message_id) REFERENCES public.messages(id) ON DELETE CASCADE;
ADD CONSTRAINT message_delete_pkey PRIMARY KEY (id);
ADD CONSTRAINT message_edit_message_id_fkey FOREIGN KEY (message_id) REFERENCES public.messages(id) ON DELETE CASCADE;
ADD CONSTRAINT message_edit_pkey PRIMARY KEY (id);
ADD CONSTRAINT message_reaction_message_id_fkey FOREIGN KEY (message_id) REFERENCES public.messages(id) ON DELETE CASCADE;
ADD CONSTRAINT message_reaction_pkey PRIMARY KEY (id);
ADD CONSTRAINT messages_pkey PRIMARY KEY (id);
ADD CONSTRAINT music_queue_pkey PRIMARY KEY (id);
ADD CONSTRAINT music_session_pkey PRIMARY KEY (guild_id);
ADD CONSTRAINT note_candidate_pkey PRIMARY KEY (id);
ADD CONSTRAINT note_candidate_probe_key UNIQUE (unit, probe);
ADD CONSTRAINT note_entity_entity_id_fkey FOREIGN KEY (entity_id) REFERENCES public.fact_entity(id);
ADD CONSTRAINT note_entity_note_id_fkey FOREIGN KEY (note_id) REFERENCES public.note(id);
ADD CONSTRAINT note_entity_pkey PRIMARY KEY (note_id, entity_id);
ADD CONSTRAINT note_merge_decision_duplicate_fkey FOREIGN KEY (duplicate_id) REFERENCES public.note(id);
ADD CONSTRAINT note_merge_decision_pkey PRIMARY KEY (id);
ADD CONSTRAINT note_merge_decision_survivor_fkey FOREIGN KEY (survivor_id) REFERENCES public.note(id);
ADD CONSTRAINT note_pkey PRIMARY KEY (id);
ADD CONSTRAINT note_replaced_by_fkey FOREIGN KEY (replaced_by) REFERENCES public.note(id);
ADD CONSTRAINT note_revision_note_id_fkey FOREIGN KEY (note_id) REFERENCES public.note(id);
ADD CONSTRAINT note_revision_pkey PRIMARY KEY (id);
ADD CONSTRAINT otp_attempt_pkey PRIMARY KEY (id);
ADD CONSTRAINT otp_challenge_pkey PRIMARY KEY (challenge_id);
ADD CONSTRAINT participation_window_state_pkey PRIMARY KEY (platform, conversation_id);
ADD CONSTRAINT persona_dispute_pkey PRIMARY KEY (id);
ADD CONSTRAINT persona_line_pkey PRIMARY KEY (id);
ADD CONSTRAINT persona_pkey PRIMARY KEY (key);
ADD CONSTRAINT pk_document_revision PRIMARY KEY (document_id, revision);
ADD CONSTRAINT presence_corridor_speed_pkey PRIMARY KEY (subject_id, corridor_id, hour_of_week);
ADD CONSTRAINT presence_corridors_pkey PRIMARY KEY (id);
ADD CONSTRAINT presence_derived_places_pkey PRIMARY KEY (id);
ADD CONSTRAINT presence_fixes_pkey PRIMARY KEY (id);
ADD CONSTRAINT presence_occupancy_pkey PRIMARY KEY (id);
ADD CONSTRAINT presence_place_rhythm_pkey PRIMARY KEY (subject_id, place_id);
ADD CONSTRAINT presence_places_pkey PRIMARY KEY (circle_id, place_id);
ADD CONSTRAINT presence_stays_pkey PRIMARY KEY (id);
ADD CONSTRAINT presence_trips_pkey PRIMARY KEY (id);
ADD CONSTRAINT reasoning_strip_log_pkey PRIMARY KEY (id);
ADD CONSTRAINT reminders_pkey PRIMARY KEY (id);
ADD CONSTRAINT rendered_claim_pkey PRIMARY KEY (conversation_id, kind, row_key);
ADD CONSTRAINT revision_blob_pkey PRIMARY KEY (sha256);
ADD CONSTRAINT rollup_build_pkey PRIMARY KEY (id);
ADD CONSTRAINT rollup_colocation_pkey PRIMARY KEY (id);
ADD CONSTRAINT rollup_daybook_pkey PRIMARY KEY (id);
ADD CONSTRAINT rollup_fold_state_pkey PRIMARY KEY (build_id, subject_id);
ADD CONSTRAINT rollup_outing_pkey PRIMARY KEY (id);
ADD CONSTRAINT rollup_response_pkey PRIMARY KEY (id);
ADD CONSTRAINT rollup_tie_pkey PRIMARY KEY (id);
ADD CONSTRAINT room_exchange_pkey PRIMARY KEY (scope_id, act, participants);
ADD CONSTRAINT room_lexeme_pkey PRIMARY KEY (scope_id, canonical_form, sense);
ADD CONSTRAINT room_portrait_pkey PRIMARY KEY (scope_id);
ADD CONSTRAINT row_history_pkey PRIMARY KEY (id);
ADD CONSTRAINT self_tension_pkey PRIMARY KEY (id);
ADD CONSTRAINT social_item_count_pkey PRIMARY KEY (id);
ADD CONSTRAINT social_item_pkey PRIMARY KEY (id);
ADD CONSTRAINT social_item_state_pkey PRIMARY KEY (id);
ADD CONSTRAINT social_perception_pkey PRIMARY KEY (id);
ADD CONSTRAINT social_poll_pkey PRIMARY KEY (id);
ADD CONSTRAINT source_health_pkey PRIMARY KEY (id);
ADD CONSTRAINT standing_pkey PRIMARY KEY (platform, conversation_id);
ADD CONSTRAINT standing_revision_pkey PRIMARY KEY (id);
ADD CONSTRAINT sticker_index_pkey PRIMARY KEY (platform, fetch_token);
ADD CONSTRAINT stimulus_pkey PRIMARY KEY (id);
ADD CONSTRAINT stimulus_retraction_pkey PRIMARY KEY (id);
ADD CONSTRAINT tag_debounce_state_pkey PRIMARY KEY (platform, conversation_id);
ADD CONSTRAINT tag_trace_pkey PRIMARY KEY (id);
ADD CONSTRAINT tool_call_log_pkey PRIMARY KEY (id);
ADD CONSTRAINT turn_verdict_pkey PRIMARY KEY (tag_id);
ADD CONSTRAINT uq_computer_events_operation_phase UNIQUE (operation_id, phase);
ADD CONSTRAINT uq_memory_node_kind_source UNIQUE (kind, source_id);
ADD CONSTRAINT uq_music_queue_guild_position UNIQUE (guild_id, "position");
ADD CONSTRAINT uq_occupancy_bin UNIQUE (subject_id, place_id, hour_of_week);
ADD CONSTRAINT version_announcements_pkey PRIMARY KEY (version, platform, conversation_id);
ADD CONSTRAINT video_readings_pkey PRIMARY KEY (sha256, provider, model, chunk_seconds, chunk_max, reading_digest);
ALTER TABLE ONLY public.alembic_version
ALTER TABLE ONLY public.analytics_watermark
ALTER TABLE ONLY public.artifacts
ALTER TABLE ONLY public.artifacts
ALTER TABLE ONLY public.assess_report
ALTER TABLE ONLY public.blob_tombstone
ALTER TABLE ONLY public.cognition_carry
ALTER TABLE ONLY public.cognition_self_wake
ALTER TABLE ONLY public.cognition_self_wake
ALTER TABLE ONLY public.cognition_session
ALTER TABLE ONLY public.cognition_session_close
ALTER TABLE ONLY public.cognition_session_close
ALTER TABLE ONLY public.cognition_wake
ALTER TABLE ONLY public.cognition_wake_outcome
ALTER TABLE ONLY public.compute_jobs
ALTER TABLE ONLY public.computer_events
ALTER TABLE ONLY public.computer_events
ALTER TABLE ONLY public.computer_exports
ALTER TABLE ONLY public.computer_sessions
ALTER TABLE ONLY public.config_revision
ALTER TABLE ONLY public.consolidation_watermark
ALTER TABLE ONLY public.culture
ALTER TABLE ONLY public.culture
ALTER TABLE ONLY public.culture_revision
ALTER TABLE ONLY public.decision_log
ALTER TABLE ONLY public.deploy_revision
ALTER TABLE ONLY public.doctrine_revision
ALTER TABLE ONLY public.document_revision
ALTER TABLE ONLY public.document_state
ALTER TABLE ONLY public.documents
ALTER TABLE ONLY public.dream_schedule_state
ALTER TABLE ONLY public.event_revision
ALTER TABLE ONLY public.event_revision
ALTER TABLE ONLY public.event_revision
ALTER TABLE ONLY public.event_rsvps
ALTER TABLE ONLY public.event_rsvps
ALTER TABLE ONLY public.events
ALTER TABLE ONLY public.exchange_observation
ALTER TABLE ONLY public.exchange_observation
ALTER TABLE ONLY public.fact
ALTER TABLE ONLY public.fact
ALTER TABLE ONLY public.fact
ALTER TABLE ONLY public.fact_asserter
ALTER TABLE ONLY public.fact_asserter
ALTER TABLE ONLY public.fact_candidate
ALTER TABLE ONLY public.fact_candidate
ALTER TABLE ONLY public.fact_entity
ALTER TABLE ONLY public.fact_entity
ALTER TABLE ONLY public.fact_premise
ALTER TABLE ONLY public.fact_premise
ALTER TABLE ONLY public.fact_premise
ALTER TABLE ONLY public.geocode_cache
ALTER TABLE ONLY public.instagram_summons
ALTER TABLE ONLY public.instagram_summons
ALTER TABLE ONLY public.instagram_summons
ALTER TABLE ONLY public.language_observation
ALTER TABLE ONLY public.language_observation
ALTER TABLE ONLY public.lesson
ALTER TABLE ONLY public.lesson_revision
ALTER TABLE ONLY public.media_objects
ALTER TABLE ONLY public.memo
ALTER TABLE ONLY public.memo_revision
ALTER TABLE ONLY public.memory_edge
ALTER TABLE ONLY public.memory_index_state
ALTER TABLE ONLY public.memory_node
ALTER TABLE ONLY public.memory_node
ALTER TABLE ONLY public.memory_projection_queue
ALTER TABLE ONLY public.message_delete
ALTER TABLE ONLY public.message_delete
ALTER TABLE ONLY public.message_edit
ALTER TABLE ONLY public.message_edit
ALTER TABLE ONLY public.message_reaction
ALTER TABLE ONLY public.message_reaction
ALTER TABLE ONLY public.messages
ALTER TABLE ONLY public.music_queue
ALTER TABLE ONLY public.music_queue
ALTER TABLE ONLY public.music_session
ALTER TABLE ONLY public.note
ALTER TABLE ONLY public.note
ALTER TABLE ONLY public.note_candidate
ALTER TABLE ONLY public.note_candidate
ALTER TABLE ONLY public.note_entity
ALTER TABLE ONLY public.note_entity
ALTER TABLE ONLY public.note_entity
ALTER TABLE ONLY public.note_merge_decision
ALTER TABLE ONLY public.note_merge_decision
ALTER TABLE ONLY public.note_merge_decision
ALTER TABLE ONLY public.note_revision
ALTER TABLE ONLY public.note_revision
ALTER TABLE ONLY public.otp_attempt
ALTER TABLE ONLY public.otp_challenge
ALTER TABLE ONLY public.participation_window_state
ALTER TABLE ONLY public.persona
ALTER TABLE ONLY public.persona_dispute
ALTER TABLE ONLY public.persona_line
ALTER TABLE ONLY public.presence_corridor_speed
ALTER TABLE ONLY public.presence_corridors
ALTER TABLE ONLY public.presence_derived_places
ALTER TABLE ONLY public.presence_fixes
ALTER TABLE ONLY public.presence_occupancy
ALTER TABLE ONLY public.presence_occupancy
ALTER TABLE ONLY public.presence_place_rhythm
ALTER TABLE ONLY public.presence_places
ALTER TABLE ONLY public.presence_stays
ALTER TABLE ONLY public.presence_trips
ALTER TABLE ONLY public.reasoning_strip_log
ALTER TABLE ONLY public.reminders
ALTER TABLE ONLY public.rendered_claim
ALTER TABLE ONLY public.revision_blob
ALTER TABLE ONLY public.rollup_build
ALTER TABLE ONLY public.rollup_colocation
ALTER TABLE ONLY public.rollup_daybook
ALTER TABLE ONLY public.rollup_fold_state
ALTER TABLE ONLY public.rollup_outing
ALTER TABLE ONLY public.rollup_response
ALTER TABLE ONLY public.rollup_tie
ALTER TABLE ONLY public.room_exchange
ALTER TABLE ONLY public.room_lexeme
ALTER TABLE ONLY public.room_portrait
ALTER TABLE ONLY public.row_history
ALTER TABLE ONLY public.self_tension
ALTER TABLE ONLY public.social_item
ALTER TABLE ONLY public.social_item_count
ALTER TABLE ONLY public.social_item_state
ALTER TABLE ONLY public.social_perception
ALTER TABLE ONLY public.social_poll
ALTER TABLE ONLY public.source_health
ALTER TABLE ONLY public.standing
ALTER TABLE ONLY public.standing_revision
ALTER TABLE ONLY public.sticker_index
ALTER TABLE ONLY public.stimulus
ALTER TABLE ONLY public.stimulus_retraction
ALTER TABLE ONLY public.tag_debounce_state
ALTER TABLE ONLY public.tag_trace
ALTER TABLE ONLY public.tool_call_log
ALTER TABLE ONLY public.turn_verdict
ALTER TABLE ONLY public.version_announcements
ALTER TABLE ONLY public.video_readings
ALTER TABLE public.assess_report ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
ALTER TABLE public.cognition_carry ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
ALTER TABLE public.cognition_self_wake ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
ALTER TABLE public.cognition_session ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
ALTER TABLE public.cognition_session_close ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
ALTER TABLE public.cognition_wake ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
ALTER TABLE public.cognition_wake_outcome ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
ALTER TABLE public.config_revision ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
ALTER TABLE public.deploy_revision ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
ALTER TABLE public.doctrine_revision ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
ALTER TABLE public.document_state ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
ALTER TABLE public.exchange_observation ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
ALTER TABLE public.language_observation ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
ALTER TABLE public.memory_node ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
ALTER TABLE public.message_delete ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
ALTER TABLE public.message_edit ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
ALTER TABLE public.message_reaction ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
ALTER TABLE public.messages ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
ALTER TABLE public.otp_attempt ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
ALTER TABLE public.presence_corridors ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
ALTER TABLE public.presence_derived_places ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
ALTER TABLE public.presence_fixes ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
ALTER TABLE public.presence_occupancy ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
ALTER TABLE public.presence_stays ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
ALTER TABLE public.presence_trips ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
ALTER TABLE public.rollup_build ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
ALTER TABLE public.rollup_colocation ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
ALTER TABLE public.rollup_daybook ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
ALTER TABLE public.rollup_outing ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
ALTER TABLE public.rollup_response ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
ALTER TABLE public.rollup_tie ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
ALTER TABLE public.row_history ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
ALTER TABLE public.source_health ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
ALTER TABLE public.stimulus ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
ALTER TABLE public.stimulus_retraction ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
AND superseded_at IS NULL;
AND superseded_at IS NULL;
AS $$
AS $$
AS $$
AS $$
BEGIN
BEGIN
BEGIN
BEGIN
CACHE 1
CACHE 1
CACHE 1
CACHE 1
CACHE 1
CACHE 1
CACHE 1
CACHE 1
CACHE 1
CACHE 1
CACHE 1
CACHE 1
CACHE 1
CACHE 1
CACHE 1
CACHE 1
CACHE 1
CACHE 1
CACHE 1
CACHE 1
CACHE 1
CACHE 1
CACHE 1
CACHE 1
CACHE 1
CACHE 1
CACHE 1
CACHE 1
CACHE 1
CACHE 1
CACHE 1
CACHE 1
CACHE 1
CACHE 1
CACHE 1
CASE
COMMENT ON EXTENSION pg_trgm IS 'text similarity measurement and index searching based on trigrams';
COMMENT ON EXTENSION vector IS 'vector data type and ivfflat and hnsw access methods';
CONSTRAINT ck_assess_report_kind CHECK ((kind = 'nightly_dq'::text)),
CONSTRAINT ck_assess_report_results_shape CHECK ((jsonb_typeof(results) = 'array'::text))
CONSTRAINT ck_blob_tombstone_kind CHECK ((kind = 'story_frame'::text)),
CONSTRAINT ck_blob_tombstone_reason CHECK ((reason = ANY (ARRAY['embedded'::text, 'hard_limit'::text]))),
CONSTRAINT ck_blob_tombstone_sha256 CHECK ((sha256 ~ '^[0-9a-f]{64}$'::text))
CONSTRAINT ck_cognition_carry_state CHECK ((state = ANY (ARRAY['carried'::text, 'released'::text, 'struck'::text])))
CONSTRAINT ck_cognition_self_wake_outcome CHECK ((outcome = ANY (ARRAY['recorded'::text, 'refused'::text]))),
CONSTRAINT ck_cognition_self_wake_outcome_shape CHECK (((outcome = 'recorded'::text) OR (refusal_reason IS NOT NULL))),
CONSTRAINT ck_cognition_self_wake_transition CHECK ((transition = ANY (ARRAY['created'::text, 'moved'::text, 'cancelled'::text, 'fired'::text]))),
CONSTRAINT ck_cognition_self_wake_verb CHECK ((verb = ANY (ARRAY['wake_at'::text, 'wake_move'::text, 'wake_cancel'::text, 'wake_fire'::text])))
CONSTRAINT ck_cognition_session_close_reason CHECK ((reason = ANY (ARRAY['written'::text, 'no_change'::text, 'waiting'::text, 'covered'::text, 'struck'::text])))
CONSTRAINT ck_cognition_session_mode CHECK ((mode = 'live'::text))
CONSTRAINT ck_cognition_wake_mode CHECK ((mode = 'live'::text)),
CONSTRAINT ck_cognition_wake_outcome_cause CHECK ((cause = ANY (ARRAY['infra'::text, 'agenda'::text, 'none'::text]))),
CONSTRAINT ck_cognition_wake_outcome_outcome CHECK ((outcome = ANY (ARRAY['closed'::text, 'failed'::text, 'timeout'::text, 'cancelled'::text])))
CONSTRAINT ck_cognition_wake_reason CHECK ((reason = ANY (ARRAY['settled'::text, 'strong_cue'::text, 'rollup'::text, 'batch'::text, 'max_age'::text, 'time'::text, 'self'::text])))
CONSTRAINT ck_compute_jobs_state CHECK ((state = ANY (ARRAY['pending'::text, 'firing'::text, 'done'::text, 'failed'::text, 'cancelled'::text])))
CONSTRAINT ck_computer_events_actor CHECK (((actor = ANY (ARRAY['cognition'::text, 'fire'::text, 'operator'::text, 'subagent'::text, 'sweeper'::text, 'tag'::text])) OR (starts_with(actor, 'dreamer:'::text) AND (length(actor) > 8)))),
CONSTRAINT ck_computer_events_path CHECK ((path = ANY (ARRAY['direct'::text, 'subagent'::text]))),
CONSTRAINT ck_computer_events_phase CHECK ((phase = ANY (ARRAY['finished'::text, 'started'::text]))),
CONSTRAINT ck_computer_events_phase_times CHECK ((((phase = 'started'::text) AND (finished_at IS NULL)) OR ((phase = 'finished'::text) AND (finished_at IS NOT NULL) AND (finished_at >= started_at))))
CONSTRAINT ck_computer_exports_destination CHECK ((destination = ANY (ARRAY['chat'::text, 'keep'::text, 'link'::text]))),
CONSTRAINT ck_computer_exports_target CHECK ((((destination = 'link'::text) AND (artifact_id IS NOT NULL) AND (url IS NOT NULL)) OR ((destination <> 'link'::text) AND (artifact_id IS NULL) AND (NOT revised) AND (blob_key IS NOT NULL))))
CONSTRAINT ck_computer_sessions_actor CHECK (((actor = ANY (ARRAY['cognition'::text, 'fire'::text, 'operator'::text, 'subagent'::text, 'sweeper'::text, 'tag'::text])) OR (starts_with(actor, 'dreamer:'::text) AND (length(actor) > 8)))),
CONSTRAINT ck_computer_sessions_expired_reason CHECK (((expired_reason IS NULL) OR (expired_reason = ANY (ARRAY['idle'::text, 'jobs_ended'::text, 'running_past_limit'::text])))),
CONSTRAINT ck_computer_sessions_expiry CHECK ((((status = 'open'::text) AND (expired_at IS NULL) AND (expired_reason IS NULL)) OR ((status = 'expired'::text) AND (expired_at IS NOT NULL) AND (expired_reason IS NOT NULL)))),
CONSTRAINT ck_computer_sessions_status CHECK ((status = ANY (ARRAY['expired'::text, 'open'::text])))
CONSTRAINT ck_corridor_speed_how CHECK (((hour_of_week >= 0) AND (hour_of_week < 168)))
CONSTRAINT ck_derived_places_kind CHECK ((kind = ANY (ARRAY['home'::text, 'work'::text, 'unlabeled'::text])))
CONSTRAINT ck_document_revision_media CHECK ((array_to_string(media, ','::text) ~ '^([0-9a-f]{64}(,[0-9a-f]{64})*)?$'::text)),
CONSTRAINT ck_document_revision_positive CHECK ((revision >= 1)),
CONSTRAINT ck_document_revision_sha256 CHECK ((sha256 ~ '^[0-9a-f]{64}$'::text)),
CONSTRAINT ck_document_revision_spec_size CHECK ((octet_length((spec)::text) <= 1048576))
CONSTRAINT ck_document_state_set_by CHECK ((length(set_by) > 0)),
CONSTRAINT ck_document_state_state CHECK ((state = ANY (ARRAY['active'::text, 'archived'::text, 'deleted'::text])))
CONSTRAINT ck_event_revision_confidence_is_a_level CHECK (((confidence IS NULL) OR (confidence = ANY (ARRAY['low'::text, 'medium'::text, 'high'::text])))),
CONSTRAINT ck_event_revision_field_meta_shape CHECK ((jsonb_typeof(field_meta) = 'object'::text)),
CONSTRAINT ck_event_revision_lineage_shape CHECK ((jsonb_typeof(lineage) = 'object'::text)),
CONSTRAINT ck_event_revision_reasoning_is_a_mode CHECK (((reasoning IS NULL) OR (reasoning = ANY (ARRAY['told'::text, 'deduced'::text, 'abduced'::text])))),
CONSTRAINT ck_event_revision_status CHECK ((status = ANY (ARRAY['staged'::text, 'proposed'::text, 'confirmed'::text, 'happened'::text, 'cancelled'::text, 'lapsed'::text, 'evicted'::text])))
CONSTRAINT ck_event_rsvps_response CHECK ((response = ANY (ARRAY['yes'::text, 'no'::text, 'maybe'::text])))
CONSTRAINT ck_events_confidence_is_a_level CHECK (((confidence IS NULL) OR (confidence = ANY (ARRAY['low'::text, 'medium'::text, 'high'::text])))),
CONSTRAINT ck_events_field_meta_shape CHECK ((jsonb_typeof(field_meta) = 'object'::text)),
CONSTRAINT ck_events_lineage_shape CHECK ((jsonb_typeof(lineage) = 'object'::text)),
CONSTRAINT ck_events_reasoning_is_a_mode CHECK (((reasoning IS NULL) OR (reasoning = ANY (ARRAY['told'::text, 'deduced'::text, 'abduced'::text])))),
CONSTRAINT ck_events_status CHECK ((status = ANY (ARRAY['staged'::text, 'proposed'::text, 'confirmed'::text, 'happened'::text, 'cancelled'::text, 'lapsed'::text, 'evicted'::text])))
CONSTRAINT ck_instagram_summons_mode CHECK ((mode = ANY (ARRAY['dry_run'::text, 'live'::text]))),
CONSTRAINT ck_instagram_summons_state CHECK ((state = ANY (ARRAY['claimed'::text, 'unresolved'::text, 'ignored_summoner'::text, 'stale'::text, 'unreadable'::text, 'silent'::text, 'staged'::text, 'flushing'::text, 'published'::text, 'refused'::text, 'capped'::text, 'duplicate'::text, 'ambiguous'::text, 'failed'::text])))
CONSTRAINT ck_lesson_has_a_unit CHECK ((length(btrim(unit)) > 0))
CONSTRAINT ck_lesson_revision_body_ceiling CHECK ((length(body) <= 2000)),
CONSTRAINT ck_lesson_revision_cites_something CHECK ((cardinality(citations) >= 1)),
CONSTRAINT ck_lesson_revision_has_a_body CHECK ((length(btrim(body)) > 0)),
CONSTRAINT ck_lesson_revision_instruction_sha256 CHECK (((instruction_sha256 IS NULL) OR (instruction_sha256 ~ '^[0-9a-f]{64}$'::text))),
CONSTRAINT ck_lesson_revision_names_its_writer CHECK ((length(btrim(established_by)) > 0)),
CONSTRAINT ck_lesson_revision_relearn_after CHECK (((retired_by = 'operator'::text) = (relearn_after IS NOT NULL))),
CONSTRAINT ck_lesson_revision_retired_by CHECK (((state = 'retired'::text) = (retired_by IS NOT NULL))),
CONSTRAINT ck_lesson_revision_retired_by_who CHECK (((retired_by IS NULL) OR (retired_by = ANY (ARRAY['session'::text, 'operator'::text])))),
CONSTRAINT ck_lesson_revision_seq CHECK ((seq >= 1)),
CONSTRAINT ck_lesson_revision_state CHECK ((state = ANY (ARRAY['active'::text, 'retired'::text])))
CONSTRAINT ck_memo_has_a_unit CHECK ((length(btrim(unit)) > 0))
CONSTRAINT ck_memo_revision_body_ceiling CHECK ((length(body) <= 4000)),
CONSTRAINT ck_memo_revision_cites_something CHECK ((cardinality(citations) >= 1)),
CONSTRAINT ck_memo_revision_has_a_body CHECK ((length(btrim(body)) > 0)),
CONSTRAINT ck_memo_revision_has_a_title CHECK ((length(btrim(title)) > 0)),
CONSTRAINT ck_memo_revision_mode CHECK ((mode = ANY (ARRAY['told'::text, 'deduced'::text, 'abduced'::text]))),
CONSTRAINT ck_memo_revision_names_its_writer CHECK ((length(btrim(established_by)) > 0)),
CONSTRAINT ck_memo_revision_seq CHECK ((seq >= 1)),
CONSTRAINT ck_memo_revision_state CHECK ((state = ANY (ARRAY['kept'::text, 'withdrawn'::text]))),
CONSTRAINT ck_memo_revision_title_ceiling CHECK ((length(title) <= 200))
CONSTRAINT ck_memory_edge_rel CHECK ((rel = ANY (ARRAY['authored_by'::text, 'in_room'::text, 'shared_by'::text, 'attached_to'::text, 'during'::text, 'about'::text, 'cites'::text, 'supersedes'::text, 'tracks'::text, 'closes_into'::text, 'leaves_behind'::text, 'draws_on'::text, 'informed_by'::text, 'disputes'::text, 'feedback_on'::text, 'depicts'::text, 'mentions'::text, 'located_in'::text, 'outcome_of'::text])))
CONSTRAINT ck_memory_index_state_one_row CHECK (id),
CONSTRAINT ck_memory_index_state_status CHECK ((status = ANY (ARRAY['never'::text, 'building'::text, 'complete'::text])))
CONSTRAINT ck_memory_node_embed_input CHECK (((embed_input IS NULL) OR (embed_input = ANY (ARRAY['text'::text, 'bytes'::text])))),
CONSTRAINT ck_memory_node_has_a_scope CHECK (((kind = ANY (ARRAY['person'::text, 'room'::text, 'unit'::text, 'nora'::text, 'place'::text])) OR (unit IS NOT NULL) OR (origin_room IS NOT NULL) OR (cardinality(persons) > 0))),
CONSTRAINT ck_memory_node_kind CHECK ((kind = ANY (ARRAY['message'::text, 'media'::text, 'note'::text, 'memo'::text, 'event'::text, 'lesson'::text, 'character'::text, 'outing'::text, 'stay'::text, 'trip'::text, 'person'::text, 'room'::text, 'unit'::text, 'nora'::text, 'place'::text, 'social'::text, 'social_media'::text, 'social_location'::text]))),
CONSTRAINT ck_memory_node_state CHECK ((state = ANY (ARRAY['live'::text, 'retired'::text, 'superseded'::text])))
CONSTRAINT ck_music_queue_position_non_negative CHECK (("position" >= 0))
CONSTRAINT ck_music_session_volume_range CHECK (((volume >= (0.0)::double precision) AND (volume <= (2.0)::double precision)))
CONSTRAINT ck_occupancy_how CHECK (((hour_of_week >= 0) AND (hour_of_week < 168)))
CONSTRAINT ck_otp_challenge_code_hash CHECK ((code_hash ~ '^[0-9a-f]{64}$'::text)),
CONSTRAINT ck_otp_challenge_expiry CHECK ((expires_at > issued_at)),
CONSTRAINT ck_otp_challenge_platform CHECK ((platform = ANY (ARRAY['signal'::text, 'whatsapp'::text]))),
CONSTRAINT ck_otp_challenge_purpose CHECK ((purpose = ANY (ARRAY['sign_in'::text, 'adopt'::text])))
CONSTRAINT ck_persona_dispute_claim CHECK (((length(btrim(claim)) > 0) AND (length(claim) <= 300))),
CONSTRAINT ck_persona_dispute_has_a_key CHECK ((length(btrim(persona_key)) > 0)),
CONSTRAINT ck_persona_dispute_has_a_unit CHECK ((length(btrim(unit)) > 0)),
CONSTRAINT ck_persona_dispute_kind CHECK ((kind = ANY (ARRAY['dispute'::text, 'forget'::text]))),
CONSTRAINT ck_persona_dispute_who CHECK ((length(btrim(disputed_by)) > 0))
CONSTRAINT ck_persona_entity_kind CHECK ((entity_kind = ANY (ARRAY['human'::text, 'nora'::text, 'agent'::text, 'room'::text, 'unit'::text]))),
CONSTRAINT ck_persona_has_a_key CHECK ((length(btrim(key)) > 0)),
CONSTRAINT ck_persona_has_a_unit CHECK ((length(btrim(unit)) > 0)),
CONSTRAINT ck_persona_line_abduced_explains CHECK (((mode <> 'abduced'::text) OR (cardinality(draws_on) >= 1))),
CONSTRAINT ck_persona_line_cites CHECK ((cardinality(citations) >= 1)),
CONSTRAINT ck_persona_line_deduced_premises CHECK (((mode <> 'deduced'::text) OR ((cardinality(citations) + cardinality(draws_on)) >= 2))),
CONSTRAINT ck_persona_line_draws_on CHECK (((mode = ANY (ARRAY['deduced'::text, 'abduced'::text])) OR (cardinality(draws_on) = 0))),
CONSTRAINT ck_persona_line_has_a_unit CHECK ((length(btrim(unit)) > 0)),
CONSTRAINT ck_persona_line_kind_part CHECK ((((part = 'identity'::text) AND (kind = ANY (ARRAY['name'::text, 'role'::text, 'relationship'::text, 'attribute'::text, 'instruction'::text, 'purpose'::text, 'norm'::text]))) OR ((part = 'character'::text) AND (kind = 'trait'::text)) OR ((part = 'hypothesis'::text) AND (kind = 'explanation'::text)))),
CONSTRAINT ck_persona_line_mode_part CHECK ((((part = 'identity'::text) AND (mode = ANY (ARRAY['told'::text, 'deduced'::text]))) OR ((part = 'character'::text) AND (mode = ANY (ARRAY['deduced'::text, 'induced'::text]))) OR ((part = 'hypothesis'::text) AND (mode = 'abduced'::text)))),
CONSTRAINT ck_persona_line_names_its_writer CHECK ((length(btrim(written_by_session)) > 0)),
CONSTRAINT ck_persona_line_not_self CHECK (((supersedes IS NULL) OR (supersedes <> id))),
CONSTRAINT ck_persona_line_part CHECK ((part = ANY (ARRAY['identity'::text, 'character'::text, 'hypothesis'::text]))),
CONSTRAINT ck_persona_line_prediction CHECK (
CONSTRAINT ck_persona_line_state CHECK ((state = ANY (ARRAY['current'::text, 'disputed'::text, 'outranked'::text, 'hidden'::text, 'superseded'::text, 'retired'::text, 'refuted'::text]))),
CONSTRAINT ck_persona_line_support_count CHECK ((support_count >= 1)),
CONSTRAINT ck_persona_line_text CHECK (((length(btrim(text)) > 0) AND (length(text) <= 300))),
CONSTRAINT ck_persona_line_told_by CHECK (((mode = 'told'::text) = (told_by IS NOT NULL))),
CONSTRAINT ck_persona_line_verdict CHECK (((verdict IS NULL) OR (verdict = ANY (ARRAY['open'::text, 'confirmed'::text, 'refuted'::text, 'expired'::text, 'unknown'::text])))),
CONSTRAINT ck_persona_line_verdict_by CHECK (((verdict_by IS NULL) = (verdict_at IS NULL))),
CONSTRAINT ck_persona_line_verdict_part CHECK (((part = 'hypothesis'::text) = (verdict IS NOT NULL)))
CONSTRAINT ck_persona_revision CHECK ((revision >= 0))
CONSTRAINT ck_reasoning_strip_log_counts CHECK (((continuation_calls >= 1) AND (continuation_calls <= model_calls) AND (strip_calls >= 0) AND (strip_calls <= continuation_calls) AND (stripped_blocks >= 0) AND ((strip_calls = 0) = (stripped_blocks = 0)))),
CONSTRAINT ck_reasoning_strip_log_tag_id_not_empty CHECK (((tag_id IS NULL) OR (tag_id <> ''::text)))
CONSTRAINT ck_reminders_recurrence CHECK ((((recurrence = 'none'::text) AND (recurrence_tz IS NULL) AND (recurrence_count IS NULL) AND (recurrence_until IS NULL) AND (series_id IS NULL) AND (series_anchor_at IS NULL) AND (occurrence = 0)) OR ((recurrence <> 'none'::text) AND (recurrence_tz IS NOT NULL) AND (recurrence_count >= 1) AND (series_id IS NOT NULL) AND (series_anchor_at IS NOT NULL) AND (occurrence >= 0) AND (occurrence < recurrence_count)))),
CONSTRAINT ck_reminders_recurrence_interval CHECK ((recurrence_interval >= 1)),
CONSTRAINT ck_reminders_recurrence_kind CHECK ((recurrence = ANY (ARRAY['none'::text, 'minutely'::text, 'hourly'::text, 'daily'::text, 'weekly'::text, 'monthly'::text, 'yearly'::text]))),
CONSTRAINT ck_reminders_state CHECK ((state = ANY (ARRAY['pending'::text, 'firing'::text, 'done'::text, 'cancelled'::text, 'failed'::text])))
CONSTRAINT ck_rollup_build_status CHECK ((status = ANY (ARRAY['building'::text, 'current'::text, 'superseded'::text, 'abandoned'::text])))
CONSTRAINT ck_rollup_response_outcome CHECK ((outcome = ANY (ARRAY['open'::text, 'responded'::text, 'silent'::text, 'unanswered'::text, 'unknown'::text])))
CONSTRAINT ck_rollup_tie_pair_order CHECK ((person_a < person_b))
CONSTRAINT ck_self_tension_has_a_unit CHECK ((length(btrim(unit)) > 0)),
CONSTRAINT ck_self_tension_names_its_writer CHECK ((length(btrim(established_by)) > 0)),
CONSTRAINT ck_self_tension_quotes_the_law CHECK ((length(btrim(doctrine_text)) > 0)),
CONSTRAINT ck_self_tension_says_what_it_would CHECK ((length(btrim(would_have_said)) > 0)),
CONSTRAINT ck_self_tension_source CHECK ((source = 'lesson'::text))
CONSTRAINT ck_social_item_count_hidden CHECK (((likes IS NULL) OR (NOT likes_hidden)))
CONSTRAINT ck_social_item_has_an_id CHECK ((length(btrim(item_id)) > 0)),
CONSTRAINT ck_social_item_kind CHECK ((kind = ANY (ARRAY['story_frame'::text, 'post'::text, 'reel'::text, 'thread_post'::text]))),
CONSTRAINT ck_social_item_origin CHECK ((origin = ANY (ARRAY['poll'::text, 'backfill'::text]))),
CONSTRAINT ck_social_item_state_place CHECK (((place IS NULL) OR (state = 'place'::text))),
CONSTRAINT ck_social_item_state_state CHECK ((state = ANY (ARRAY['gone'::text, 'hidden'::text, 'place'::text])))
CONSTRAINT ck_social_item_story_frame_expires CHECK (((kind <> 'story_frame'::text) OR (expires_at IS NOT NULL)))
CONSTRAINT ck_social_perception_calls CHECK ((calls >= 0)),
CONSTRAINT ck_social_perception_origin CHECK ((origin = ANY (ARRAY['poll'::text, 'backfill'::text]))),
CONSTRAINT ck_social_perception_outcome CHECK ((outcome = ANY (ARRAY['ok'::text, 'nothing_found'::text, 'unavailable'::text, 'failed'::text, 'capped'::text])))
CONSTRAINT ck_social_poll_outcome CHECK ((outcome = ANY (ARRAY['unchanged'::text, 'story_due'::text, 'items_due'::text, 'partial'::text, 'count_fell'::text, 'read_ok'::text, 'halted'::text, 'backed_off'::text, 'error'::text, 'abandoned'::text, 'unavailable'::text]))),
CONSTRAINT ck_social_poll_stage CHECK ((stage = ANY (ARRAY['probe'::text, 'story'::text, 'items'::text, 'item'::text, 'backfill'::text, 'relist'::text, 'recount'::text])))
CONSTRAINT ck_source_health_event CHECK ((event = ANY (ARRAY['poll'::text, 'transition'::text])))
CONSTRAINT ck_stimulus_kind CHECK ((kind = ANY (ARRAY['message'::text, 'social'::text, 'play'::text, 'rollup'::text, 'time'::text, 'self'::text])))
CONSTRAINT ck_version_announcements_state CHECK ((state = ANY (ARRAY['announcing'::text, 'done'::text, 'failed'::text])))
CONSTRAINT config_revision_file_check CHECK ((file = ANY (ARRAY['config.toml'::text, 'policy.toml'::text, 'identity.toml'::text])))
CONSTRAINT culture_has_a_key_check CHECK ((length(btrim(key)) > 0))
CONSTRAINT culture_revision_body_fits_the_ceiling_check CHECK ((length(body) <= 32000)),
CONSTRAINT culture_revision_confidence_is_a_level_check CHECK (((confidence IS NULL) OR (confidence = ANY (ARRAY['low'::text, 'medium'::text, 'high'::text])))),
CONSTRAINT culture_revision_has_a_body_check CHECK ((length(btrim(body)) > 0)),
CONSTRAINT culture_revision_instruction_sha256_check CHECK (((instruction_sha256 IS NULL) OR (instruction_sha256 ~ '^[0-9a-f]{64}$'::text))),
CONSTRAINT culture_revision_lineage_shape_check CHECK ((jsonb_typeof(lineage) = 'object'::text)),
CONSTRAINT culture_revision_model_is_named_check CHECK (((model IS NULL) OR (length(btrim(model)) > 0))),
CONSTRAINT culture_revision_names_its_writer_check CHECK ((length(btrim(established_by)) > 0)),
CONSTRAINT culture_revision_parent_is_not_self_check CHECK (((parent_revision_id IS NULL) OR (parent_revision_id <> id))),
CONSTRAINT culture_revision_reasoning_is_a_mode_check CHECK (((reasoning IS NULL) OR (reasoning = ANY (ARRAY['computed'::text, 'told'::text, 'deduced'::text, 'induced'::text, 'abduced'::text, 'said'::text]))))
CONSTRAINT decision_log_reason_fits_the_block_check CHECK ((length(reason) <= 80)),
CONSTRAINT decision_log_verdict_is_known_check CHECK ((verdict = ANY (ARRAY['spoke'::text, 'silent'::text, 'remind'::text, 'decline'::text, 'replied'::text, 'failed'::text]))),
CONSTRAINT decision_log_writer_is_known_check CHECK ((writer = ANY (ARRAY['participation'::text, 'proactive_decide'::text, 'proactive_act'::text, 'tag'::text])))
CONSTRAINT deploy_revision_manifest_status_check CHECK ((manifest_status = ANY (ARRAY['ok'::text, 'absent'::text, 'unreadable'::text])))
CONSTRAINT doctrine_revision_corpus_check CHECK ((corpus = ANY (ARRAY['doctrine'::text, 'dreamer'::text])))
CONSTRAINT exchange_observation_act_check CHECK ((act = ANY (ARRAY['insult_trade'::text, 'banter'::text, 'tease'::text, 'callback_joke'::text, 'agreement_ritual'::text, 'disagreement_ritual'::text, 'compliment_exchange'::text]))),
CONSTRAINT exchange_observation_instruction_sha256_check CHECK (((instruction_sha256 IS NULL) OR (instruction_sha256 ~ '^[0-9a-f]{64}$'::text))),
CONSTRAINT exchange_observation_message_ids_check CHECK ((array_length(message_ids, 1) >= 2)),
CONSTRAINT exchange_observation_model_check CHECK (((model IS NULL) OR (length(btrim(model)) > 0))),
CONSTRAINT exchange_observation_participants_check CHECK ((array_length(participants, 1) >= 2))
CONSTRAINT fact_asserter_person_is_named_check CHECK ((length(btrim(person_id)) > 0))
CONSTRAINT fact_candidate_instruction_sha256_check CHECK ((instruction_sha256 ~ '^[0-9a-f]{64}$'::text)),
CONSTRAINT fact_candidate_model_is_named_check CHECK ((length(btrim(model)) > 0)),
CONSTRAINT fact_candidate_predicate_fits_the_ceiling_check CHECK ((length(predicate) <= 200)),
CONSTRAINT fact_candidate_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'adjudicated'::text, 'not_covered'::text]))),
CONSTRAINT fact_candidate_term_fits_the_ceiling_check CHECK (((length(subject_id) <= 800) AND (length(object) <= 800)))
CONSTRAINT fact_fidelity_check CHECK ((fidelity = ANY (ARRAY['tentative'::text, 'proven'::text, 'deprecated'::text]))),
CONSTRAINT fact_instruction_sha256_check CHECK ((instruction_sha256 ~ '^[0-9a-f]{64}$'::text)),
CONSTRAINT fact_model_is_named_check CHECK ((length(btrim(model)) > 0)),
CONSTRAINT fact_predicate_fits_the_ceiling_check CHECK ((length(predicate) <= 200)),
CONSTRAINT fact_premise_no_self_reference_check CHECK ((fact_id <> premise_fact_id))
CONSTRAINT fact_source_excerpt_warrant_check CHECK (((source_excerpt IS NOT NULL) = (warrant_kind = 'source'::text))),
CONSTRAINT fact_source_url_warrant_check CHECK (((source_url IS NOT NULL) = (warrant_kind = 'source'::text))),
CONSTRAINT fact_supersede_reason_check CHECK (((supersede_reason IS NULL) OR (supersede_reason = ANY (ARRAY['stale'::text, 'wrong'::text, 'merged'::text])))),
CONSTRAINT fact_supersede_reason_is_timed_check CHECK (((supersede_reason IS NULL) = (superseded_at IS NULL))),
CONSTRAINT fact_supersede_source_is_timed_check CHECK (((supersede_source IS NULL) OR (superseded_at IS NOT NULL))),
CONSTRAINT fact_supersession_is_timed_check CHECK (((superseded_by IS NULL) = (superseded_at IS NULL))),
CONSTRAINT fact_term_fits_the_ceiling_check CHECK (((length(subject_id) <= 800) AND (length(object_literal) <= 800))),
CONSTRAINT fact_warrant_kind_check CHECK ((warrant_kind = ANY (ARRAY['source'::text, 'premises'::text, 'observation'::text, 'stated'::text])))
CONSTRAINT language_observation_instruction_sha256_check CHECK (((instruction_sha256 IS NULL) OR (instruction_sha256 ~ '^[0-9a-f]{64}$'::text))),
CONSTRAINT language_observation_model_check CHECK (((model IS NULL) OR (length(btrim(model)) > 0)))
CONSTRAINT messages_conversation_type_check CHECK ((conversation_type = ANY (ARRAY['group'::text, 'dm'::text])))
CONSTRAINT note_candidate_body_fits_the_ceiling_check CHECK (((body IS NULL) OR (length(body) <= 32000))),
CONSTRAINT note_candidate_body_has_content_check CHECK (((body IS NULL) OR (length(btrim(body)) > 0))),
CONSTRAINT note_candidate_closing_condition_check CHECK (((closing_condition IS NULL) OR (length(btrim(closing_condition)) > 0))),
CONSTRAINT note_candidate_create_fields_check CHECK (((kind <> 'create'::text) OR ((title IS NOT NULL) AND (description IS NOT NULL) AND (body IS NOT NULL) AND (closing_condition IS NOT NULL) AND (anchor_date IS NOT NULL) AND (status IS NOT NULL)))),
CONSTRAINT note_candidate_description_fits_the_index_check CHECK (((description IS NULL) OR ((length(btrim(description)) > 0) AND (length(description) <= 200)))),
CONSTRAINT note_candidate_kind_check CHECK ((kind = ANY (ARRAY['miss'::text, 'create'::text]))),
CONSTRAINT note_candidate_miss_fields_check CHECK (((kind <> 'miss'::text) OR ((probe IS NOT NULL) AND (first_seen_at IS NOT NULL) AND (last_seen_at IS NOT NULL)))),
CONSTRAINT note_candidate_status_check CHECK (((status IS NULL) OR (status = ANY (ARRAY['pending'::text, 'created'::text, 'refused'::text])))),
CONSTRAINT note_candidate_title_fits_the_index_check CHECK (((title IS NULL) OR ((length(btrim(title)) > 0) AND (length(title) <= 60))))
CONSTRAINT note_confidence_is_a_level_check CHECK (((confidence IS NULL) OR (confidence = ANY (ARRAY['low'::text, 'medium'::text, 'high'::text])))),
CONSTRAINT note_description_fits_the_index_check CHECK (((length(btrim(description)) > 0) AND (length(description) <= 200))),
CONSTRAINT note_has_a_closing_condition_check CHECK ((length(btrim(closing_condition)) > 0)),
CONSTRAINT note_has_an_anchor_check CHECK (((anchor_date IS NOT NULL) OR (cardinality(anchor_entity_ids) > 0))),
CONSTRAINT note_lineage_shape_check CHECK ((jsonb_typeof(lineage) = 'array'::text)),
CONSTRAINT note_merge_decision_mode_is_known_check CHECK ((mode = ANY (ARRAY['shadow'::text, 'on'::text]))),
CONSTRAINT note_merge_decision_outcome_is_known_check CHECK ((outcome = ANY (ARRAY['would_merge'::text, 'merged'::text, 'stale'::text, 'too_long'::text, 'not_open'::text, 'overlap'::text, 'cross_room'::text, 'not_changed'::text]))),
CONSTRAINT note_merge_decision_reason_is_a_sentence_check CHECK ((length(reason) <= 500))
CONSTRAINT note_reasoning_is_a_mode_check CHECK (((reasoning IS NULL) OR (reasoning = ANY (ARRAY['computed'::text, 'told'::text, 'deduced'::text, 'induced'::text, 'abduced'::text, 'said'::text])))),
CONSTRAINT note_replacement_implies_retirement_check CHECK (((replaced_by IS NULL) OR (retired_at IS NOT NULL))),
CONSTRAINT note_revision_body_fits_the_ceiling_check CHECK ((length(body) <= 32000)),
CONSTRAINT note_revision_confidence_is_a_level_check CHECK (((confidence IS NULL) OR (confidence = ANY (ARRAY['low'::text, 'medium'::text, 'high'::text])))),
CONSTRAINT note_revision_has_a_body_check CHECK ((length(btrim(body)) > 0)),
CONSTRAINT note_revision_instruction_sha256_check CHECK (((instruction_sha256 IS NULL) OR (instruction_sha256 ~ '^[0-9a-f]{64}$'::text))),
CONSTRAINT note_revision_lineage_shape_check CHECK ((jsonb_typeof(lineage) = 'array'::text)),
CONSTRAINT note_revision_model_is_named_check CHECK (((model IS NULL) OR (length(btrim(model)) > 0))),
CONSTRAINT note_revision_names_its_writer_check CHECK ((length(btrim(established_by)) > 0)),
CONSTRAINT note_revision_reasoning_is_a_mode_check CHECK (((reasoning IS NULL) OR (reasoning = ANY (ARRAY['computed'::text, 'told'::text, 'deduced'::text, 'induced'::text, 'abduced'::text, 'said'::text]))))
CONSTRAINT note_strikes_are_not_negative_check CHECK ((strikes >= 0)),
CONSTRAINT note_title_fits_the_index_check CHECK (((length(btrim(title)) > 0) AND (length(title) <= 60)))
CONSTRAINT rendered_claim_kind_check CHECK ((kind = ANY (ARRAY['fact'::text, 'lexeme'::text]))),
CONSTRAINT rendered_claim_surface_is_named_check CHECK ((length(btrim(surface)) > 0))
CONSTRAINT revision_blob_sha256_check CHECK ((sha256 ~ '^[0-9a-f]{64}$'::text))
CONSTRAINT room_exchange_act_check CHECK ((act = ANY (ARRAY['insult_trade'::text, 'banter'::text, 'tease'::text, 'callback_joke'::text, 'agreement_ritual'::text, 'disagreement_ritual'::text, 'compliment_exchange'::text]))),
CONSTRAINT room_exchange_instruction_sha256_check CHECK (((instruction_sha256 IS NULL) OR (instruction_sha256 ~ '^[0-9a-f]{64}$'::text))),
CONSTRAINT room_exchange_model_check CHECK (((model IS NULL) OR (length(btrim(model)) > 0))),
CONSTRAINT room_exchange_participants_check CHECK ((array_length(participants, 1) >= 2)),
CONSTRAINT room_exchange_state_check CHECK ((state = ANY (ARRAY['live'::text, 'fading'::text, 'stale'::text])))
CONSTRAINT room_lexeme_instruction_sha256_check CHECK (((instruction_sha256 IS NULL) OR (instruction_sha256 ~ '^[0-9a-f]{64}$'::text))),
CONSTRAINT room_lexeme_model_check CHECK (((model IS NULL) OR (length(btrim(model)) > 0))),
CONSTRAINT room_lexeme_state_check CHECK ((state = ANY (ARRAY['live'::text, 'fading'::text, 'stale'::text])))
CONSTRAINT row_history_op_is_known_check CHECK ((op = ANY (ARRAY['backfill'::text, 'insert'::text, 'update'::text, 'delete'::text]))),
CONSTRAINT row_history_supersession_is_whole_check CHECK (((superseded_at IS NULL) = (superseded_by IS NULL)))
CONSTRAINT standing_body_fits_the_ceiling_check CHECK (((length(btrim(body)) > 0) AND (length(body) <= 4000))),
CONSTRAINT standing_confidence_is_a_level_check CHECK (((confidence IS NULL) OR (confidence = ANY (ARRAY['low'::text, 'medium'::text, 'high'::text])))),
CONSTRAINT standing_fold_count_is_not_negative_check CHECK ((fold_count >= 0)),
CONSTRAINT standing_lineage_shape_check CHECK ((jsonb_typeof(lineage) = 'object'::text)),
CONSTRAINT standing_reasoning_is_a_mode_check CHECK (((reasoning IS NULL) OR (reasoning = ANY (ARRAY['computed'::text, 'told'::text, 'deduced'::text, 'induced'::text, 'abduced'::text, 'said'::text])))),
CONSTRAINT standing_revision_body_fits_the_ceiling_check CHECK (((length(btrim(body)) > 0) AND (length(body) <= 4000))),
CONSTRAINT standing_revision_confidence_is_a_level_check CHECK (((confidence IS NULL) OR (confidence = ANY (ARRAY['low'::text, 'medium'::text, 'high'::text])))),
CONSTRAINT standing_revision_fold_count_is_not_negative_check CHECK ((fold_count >= 0)),
CONSTRAINT standing_revision_lineage_shape_check CHECK ((jsonb_typeof(lineage) = 'object'::text)),
CONSTRAINT standing_revision_reasoning_is_a_mode_check CHECK (((reasoning IS NULL) OR (reasoning = ANY (ARRAY['computed'::text, 'told'::text, 'deduced'::text, 'induced'::text, 'abduced'::text, 'said'::text])))),
CONSTRAINT standing_revision_sections_are_not_blank_when_given_check CHECK ((((hold_open IS NULL) OR (length(btrim(hold_open)) > 0)) AND ((i_owe IS NULL) OR (length(btrim(i_owe)) > 0)))),
CONSTRAINT standing_revision_source_check CHECK ((source = ANY (ARRAY['fold'::text, 'operator'::text, 'cognition'::text]))),
CONSTRAINT standing_revision_working_set_fits_the_ceiling_check CHECK (((jsonb_array_length(working_set) <= 20) AND (octet_length((working_set)::text) <= 2400)))
CONSTRAINT standing_sections_are_not_blank_when_given_check CHECK ((((hold_open IS NULL) OR (length(btrim(hold_open)) > 0)) AND ((i_owe IS NULL) OR (length(btrim(i_owe)) > 0)))),
CONSTRAINT standing_working_set_fits_the_ceiling_check CHECK (((jsonb_array_length(working_set) <= 20) AND (octet_length((working_set)::text) <= 2400)))
CONSTRAINT tag_trace_outcome_check CHECK ((outcome = ANY (ARRAY['send_reply'::text, 'recovered'::text, 'silent'::text, 'vetoed'::text, 'timeout'::text, 'llm_error'::text])))
CONSTRAINT turn_verdict_grounded_has_no_unsupported_check CHECK ((NOT (grounded AND (cardinality(unsupported) > 0)))),
CONSTRAINT turn_verdict_unsupported_fits_the_list_check CHECK ((cardinality(unsupported) <= 10))
CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;
CREATE FUNCTION public.computer_events_refuse_change() RETURNS trigger
CREATE FUNCTION public.computer_exports_refuse_change() RETURNS trigger
CREATE FUNCTION public.nora_row_history() RETURNS trigger
CREATE FUNCTION public.nora_row_history_refuse_truncate() RETURNS trigger
CREATE INDEX idx_assess_report_kind_run ON public.assess_report USING btree (kind, run_at_ms);
CREATE INDEX idx_blob_tombstone_sha ON public.blob_tombstone USING btree (sha256, recorded_at);
CREATE INDEX idx_cognition_carry_unit_stimulus ON public.cognition_carry USING btree (unit, stimulus_id, id);
CREATE INDEX idx_cognition_self_wake_unit_recorded ON public.cognition_self_wake USING btree (unit, recorded_at);
CREATE INDEX idx_cognition_self_wake_unit_ref ON public.cognition_self_wake USING btree (unit, wake_ref);
CREATE INDEX idx_cognition_session_close_session ON public.cognition_session_close USING btree (session_id);
CREATE INDEX idx_cognition_session_unit_id ON public.cognition_session USING btree (unit, id);
CREATE INDEX idx_cognition_wake_unit_mode_id ON public.cognition_wake USING btree (unit, mode, id);
CREATE INDEX idx_compute_jobs_state ON public.compute_jobs USING btree (state);
CREATE INDEX idx_computer_events_conv_ts ON public.computer_events USING btree (conversation_id, created_at);
CREATE INDEX idx_computer_events_session_ts ON public.computer_events USING btree (session_id, created_at);
CREATE INDEX idx_computer_exports_artifact ON public.computer_exports USING btree (artifact_id, created_at);
CREATE INDEX idx_computer_exports_room ON public.computer_exports USING btree (conversation_id, created_at);
CREATE INDEX idx_computer_sessions_room ON public.computer_sessions USING btree (conversation_id, last_activity_at);
CREATE INDEX idx_config_revision_file_loaded ON public.config_revision USING btree (file, loaded_at);
CREATE INDEX idx_culture_revision_key_written ON public.culture_revision USING btree (culture_key, written_at DESC, id DESC);
CREATE INDEX idx_decision_log_conv_ts ON public.decision_log USING btree (conversation_id, ts DESC, id DESC);
CREATE INDEX idx_decision_log_unit_ts ON public.decision_log USING btree (unit, ts DESC, id DESC);
CREATE INDEX idx_doctrine_revision_corpus_loaded ON public.doctrine_revision USING btree (corpus, loaded_at);
CREATE INDEX idx_document_state_latest ON public.document_state USING btree (document_id, id);
CREATE INDEX idx_documents_creator ON public.documents USING btree (created_by);
CREATE INDEX idx_documents_room ON public.documents USING btree (platform, conversation_id);
CREATE INDEX idx_event_revision_event ON public.event_revision USING btree (event_id, recorded_at);
CREATE INDEX idx_event_rsvps_event_person ON public.event_rsvps USING btree (event_id, person_id, recorded_at);
CREATE INDEX idx_events_conversation ON public.events USING btree (conversation_id, status);
CREATE INDEX idx_events_unit ON public.events USING btree (unit_id, status);
CREATE INDEX idx_exchange_observation_scope ON public.exchange_observation USING btree (scope_id);
CREATE INDEX idx_fact_disputed ON public.fact USING btree (unit) WHERE (disputed_at IS NOT NULL);
CREATE INDEX idx_fact_premise_premise_fact_id ON public.fact_premise USING btree (premise_fact_id);
CREATE INDEX idx_fact_standing ON public.fact USING btree (unit, subject_id) WHERE (superseded_at IS NULL);
CREATE INDEX idx_instagram_summons_state ON public.instagram_summons USING btree (state, mode);
CREATE INDEX idx_language_observation_scope ON public.language_observation USING btree (scope_id);
CREATE INDEX idx_lesson_unit ON public.lesson USING btree (unit);
CREATE INDEX idx_memo_unit ON public.memo USING btree (unit);
CREATE INDEX idx_memory_edge_dst ON public.memory_edge USING btree (dst_kind, dst_id);
CREATE INDEX idx_memory_node_embedding_256 ON public.memory_node USING hnsw (((public.subvector(embedding, 1, 256))::public.halfvec(256)) public.halfvec_cosine_ops);
CREATE INDEX idx_memory_node_origin_room ON public.memory_node USING btree (origin_room);
CREATE INDEX idx_memory_node_sha256 ON public.memory_node USING btree (sha256);
CREATE INDEX idx_memory_node_text_trgm ON public.memory_node USING gin (text public.gin_trgm_ops);
CREATE INDEX idx_memory_node_unembedded ON public.memory_node USING btree (embed_attempts, id DESC) WHERE ((embedding IS NULL) AND (state = 'live'::text) AND (kind = ANY (ARRAY['message'::text, 'media'::text, 'note'::text, 'memo'::text, 'event'::text, 'lesson'::text, 'character'::text, 'social'::text, 'social_media'::text, 'social_location'::text])));
CREATE INDEX idx_memory_node_unit ON public.memory_node USING btree (unit);
CREATE INDEX idx_message_delete_message ON public.message_delete USING btree (message_id);
CREATE INDEX idx_message_edit_message_ts ON public.message_edit USING btree (message_id, ts);
CREATE INDEX idx_message_reaction_message ON public.message_reaction USING btree (message_id);
CREATE INDEX idx_messages_body_trgm ON public.messages USING gin (body public.gin_trgm_ops);
CREATE INDEX idx_messages_conv_platform_message_id ON public.messages USING btree (conversation_id, platform_message_id);
CREATE INDEX idx_messages_conv_ts ON public.messages USING btree (conversation_id, ts DESC);
CREATE INDEX idx_messages_dream_lag ON public.messages USING btree (conversation_id, id) WHERE (is_nora = 0);
CREATE INDEX idx_messages_is_nora ON public.messages USING btree (is_nora);
CREATE INDEX idx_note_anchor ON public.note USING btree (unit, anchor_date) WHERE (retired_at IS NULL);
CREATE INDEX idx_note_merge_decision_unit_ts ON public.note_merge_decision USING btree (unit, ts);
CREATE INDEX idx_note_revision_body_trgm ON public.note_revision USING gin (body public.gin_trgm_ops);
CREATE INDEX idx_note_revision_note_id ON public.note_revision USING btree (note_id);
CREATE INDEX idx_note_standing ON public.note USING btree (unit) WHERE (retired_at IS NULL);
CREATE INDEX idx_otp_challenge_person ON public.otp_challenge USING btree (person_id, purpose, issued_at);
CREATE INDEX idx_persona_dispute_key ON public.persona_dispute USING btree (persona_key, written_at);
CREATE INDEX idx_persona_line_key ON public.persona_line USING btree (persona_key, written_at);
CREATE INDEX idx_presence_corridors_build_subject ON public.presence_corridors USING btree (build_id, subject_id);
CREATE INDEX idx_presence_corridors_subject ON public.presence_corridors USING btree (subject_id);
CREATE INDEX idx_presence_derived_places_build_subject ON public.presence_derived_places USING btree (build_id, subject_id);
CREATE INDEX idx_presence_derived_places_subject_kind ON public.presence_derived_places USING btree (subject_id, kind);
CREATE INDEX idx_presence_member_ts ON public.presence_fixes USING btree (circle_id, person_id, ts DESC);
CREATE INDEX idx_presence_stays_build_subject ON public.presence_stays USING btree (build_id, subject_id, started_ts);
CREATE INDEX idx_presence_stays_subject_ts ON public.presence_stays USING btree (subject_id, started_ts DESC);
CREATE INDEX idx_presence_trips_build_subject ON public.presence_trips USING btree (build_id, subject_id, started_ts);
CREATE INDEX idx_presence_trips_subject_started ON public.presence_trips USING btree (subject_id, started_ts DESC);
CREATE INDEX idx_reasoning_strip_log_path_ts ON public.reasoning_strip_log USING btree (path, created_at);
CREATE INDEX idx_reminders_due ON public.reminders USING btree (state, due_at);
CREATE INDEX idx_rendered_claim_window ON public.rendered_claim USING btree (conversation_id, last_rendered_at DESC);
CREATE INDEX idx_rollup_colocation_key ON public.rollup_colocation USING btree (build_id, unit, valid_from_ms);
CREATE INDEX idx_rollup_daybook_key ON public.rollup_daybook USING btree (build_id, unit, person_id, local_date);
CREATE INDEX idx_rollup_outing_key ON public.rollup_outing USING btree (build_id, unit, valid_from_ms);
CREATE INDEX idx_rollup_response_key ON public.rollup_response USING btree (build_id, unit, message_id);
CREATE INDEX idx_rollup_tie_key ON public.rollup_tie USING btree (build_id, unit, iso_week);
CREATE INDEX idx_row_history_key ON public.row_history USING btree (table_name, row_key, recorded_at);
CREATE INDEX idx_row_history_table_recorded ON public.row_history USING btree (table_name, recorded_at);
CREATE INDEX idx_self_tension_unit ON public.self_tension USING btree (unit, written_at);
CREATE INDEX idx_social_item_count_item ON public.social_item_count USING btree (network, item_id, recorded_at);
CREATE INDEX idx_social_item_person ON public.social_item USING btree (network, person, taken_at);
CREATE INDEX idx_social_item_read ON public.social_item USING btree (read_id);
CREATE INDEX idx_social_item_state_item ON public.social_item_state USING btree (network, item_id, recorded_at);
CREATE INDEX idx_social_perception_at ON public.social_perception USING btree (at);
CREATE INDEX idx_social_perception_item ON public.social_perception USING btree (network, item_id, at);
CREATE INDEX idx_social_poll_person ON public.social_poll USING btree (network, person, recorded_at);
CREATE INDEX idx_source_health_source_at ON public.source_health USING btree (source, at_ms);
CREATE INDEX idx_standing_revision_room_updated ON public.standing_revision USING btree (platform, conversation_id, updated_at DESC, id DESC);
CREATE INDEX idx_stimulus_unit_due ON public.stimulus USING btree (unit, due_ms) WHERE (due_ms IS NOT NULL);
CREATE INDEX idx_stimulus_unit_id ON public.stimulus USING btree (unit, id);
CREATE INDEX idx_stimulus_unit_message_recorded ON public.stimulus USING btree (unit, recorded_at) WHERE (kind = 'message'::text);
CREATE INDEX idx_stimulus_unit_self_item ON public.stimulus USING btree (unit, item) WHERE (kind = 'self'::text);
CREATE INDEX idx_stimulus_unit_undated_recorded ON public.stimulus USING btree (unit, recorded_at) WHERE (due_ms IS NULL);
CREATE INDEX idx_tag_trace_conversation_created ON public.tag_trace USING btree (conversation_id, created_at);
CREATE INDEX idx_tool_call_log_conv_ts ON public.tool_call_log USING btree (conversation_id, created_at DESC);
CREATE INDEX idx_turn_verdict_ungrounded ON public.turn_verdict USING btree (conversation_id, created_at) WHERE (grounded = false);
CREATE TABLE public.alembic_version (
CREATE TABLE public.analytics_watermark (
CREATE TABLE public.artifacts (
CREATE TABLE public.assess_report (
CREATE TABLE public.blob_tombstone (
CREATE TABLE public.cognition_carry (
CREATE TABLE public.cognition_self_wake (
CREATE TABLE public.cognition_session (
CREATE TABLE public.cognition_session_close (
CREATE TABLE public.cognition_wake (
CREATE TABLE public.cognition_wake_outcome (
CREATE TABLE public.compute_jobs (
CREATE TABLE public.computer_events (
CREATE TABLE public.computer_exports (
CREATE TABLE public.computer_sessions (
CREATE TABLE public.config_revision (
CREATE TABLE public.consolidation_watermark (
CREATE TABLE public.culture (
CREATE TABLE public.culture_revision (
CREATE TABLE public.decision_log (
CREATE TABLE public.deploy_revision (
CREATE TABLE public.doctrine_revision (
CREATE TABLE public.document_revision (
CREATE TABLE public.document_state (
CREATE TABLE public.documents (
CREATE TABLE public.dream_schedule_state (
CREATE TABLE public.event_revision (
CREATE TABLE public.event_rsvps (
CREATE TABLE public.events (
CREATE TABLE public.exchange_observation (
CREATE TABLE public.fact (
CREATE TABLE public.fact_asserter (
CREATE TABLE public.fact_candidate (
CREATE TABLE public.fact_entity (
CREATE TABLE public.fact_premise (
CREATE TABLE public.geocode_cache (
CREATE TABLE public.instagram_summons (
CREATE TABLE public.language_observation (
CREATE TABLE public.lesson (
CREATE TABLE public.lesson_revision (
CREATE TABLE public.media_objects (
CREATE TABLE public.memo (
CREATE TABLE public.memo_revision (
CREATE TABLE public.memory_edge (
CREATE TABLE public.memory_index_state (
CREATE TABLE public.memory_node (
CREATE TABLE public.memory_projection_queue (
CREATE TABLE public.message_delete (
CREATE TABLE public.message_edit (
CREATE TABLE public.message_reaction (
CREATE TABLE public.messages (
CREATE TABLE public.music_queue (
CREATE TABLE public.music_session (
CREATE TABLE public.note (
CREATE TABLE public.note_candidate (
CREATE TABLE public.note_entity (
CREATE TABLE public.note_merge_decision (
CREATE TABLE public.note_revision (
CREATE TABLE public.otp_attempt (
CREATE TABLE public.otp_challenge (
CREATE TABLE public.participation_window_state (
CREATE TABLE public.persona (
CREATE TABLE public.persona_dispute (
CREATE TABLE public.persona_line (
CREATE TABLE public.presence_corridor_speed (
CREATE TABLE public.presence_corridors (
CREATE TABLE public.presence_derived_places (
CREATE TABLE public.presence_fixes (
CREATE TABLE public.presence_occupancy (
CREATE TABLE public.presence_place_rhythm (
CREATE TABLE public.presence_places (
CREATE TABLE public.presence_stays (
CREATE TABLE public.presence_trips (
CREATE TABLE public.reasoning_strip_log (
CREATE TABLE public.reminders (
CREATE TABLE public.rendered_claim (
CREATE TABLE public.revision_blob (
CREATE TABLE public.rollup_build (
CREATE TABLE public.rollup_colocation (
CREATE TABLE public.rollup_daybook (
CREATE TABLE public.rollup_fold_state (
CREATE TABLE public.rollup_outing (
CREATE TABLE public.rollup_response (
CREATE TABLE public.rollup_tie (
CREATE TABLE public.room_exchange (
CREATE TABLE public.room_lexeme (
CREATE TABLE public.room_portrait (
CREATE TABLE public.row_history (
CREATE TABLE public.self_tension (
CREATE TABLE public.social_item (
CREATE TABLE public.social_item_count (
CREATE TABLE public.social_item_state (
CREATE TABLE public.social_perception (
CREATE TABLE public.social_poll (
CREATE TABLE public.source_health (
CREATE TABLE public.standing (
CREATE TABLE public.standing_revision (
CREATE TABLE public.sticker_index (
CREATE TABLE public.stimulus (
CREATE TABLE public.stimulus_retraction (
CREATE TABLE public.tag_debounce_state (
CREATE TABLE public.tag_trace (
CREATE TABLE public.tool_call_log (
CREATE TABLE public.turn_verdict (
CREATE TABLE public.version_announcements (
CREATE TABLE public.video_readings (
CREATE TRIGGER computer_events_append_only BEFORE DELETE OR UPDATE ON public.computer_events FOR EACH ROW EXECUTE FUNCTION public.computer_events_refuse_change();
CREATE TRIGGER computer_events_no_truncate BEFORE TRUNCATE ON public.computer_events FOR EACH STATEMENT EXECUTE FUNCTION public.computer_events_refuse_change();
CREATE TRIGGER computer_exports_append_only BEFORE DELETE OR UPDATE ON public.computer_exports FOR EACH ROW EXECUTE FUNCTION public.computer_exports_refuse_change();
CREATE TRIGGER computer_exports_no_truncate BEFORE TRUNCATE ON public.computer_exports FOR EACH STATEMENT EXECUTE FUNCTION public.computer_exports_refuse_change();
CREATE TRIGGER nora_row_history AFTER INSERT OR DELETE OR UPDATE ON public.analytics_watermark FOR EACH ROW EXECUTE FUNCTION public.nora_row_history('deriver_name', 'subject_id');
CREATE TRIGGER nora_row_history AFTER INSERT OR DELETE OR UPDATE ON public.compute_jobs FOR EACH ROW EXECUTE FUNCTION public.nora_row_history('id');
CREATE TRIGGER nora_row_history AFTER INSERT OR DELETE OR UPDATE ON public.computer_sessions FOR EACH ROW EXECUTE FUNCTION public.nora_row_history('id');
CREATE TRIGGER nora_row_history AFTER INSERT OR DELETE OR UPDATE ON public.consolidation_watermark FOR EACH ROW EXECUTE FUNCTION public.nora_row_history('consolidator_name', 'scope_id');
CREATE TRIGGER nora_row_history AFTER INSERT OR DELETE OR UPDATE ON public.culture FOR EACH ROW EXECUTE FUNCTION public.nora_row_history('key');
CREATE TRIGGER nora_row_history AFTER INSERT OR DELETE OR UPDATE ON public.dream_schedule_state FOR EACH ROW EXECUTE FUNCTION public.nora_row_history('adaptor_name', 'scope_id');
CREATE TRIGGER nora_row_history AFTER INSERT OR DELETE OR UPDATE ON public.events FOR EACH ROW EXECUTE FUNCTION public.nora_row_history('id');
CREATE TRIGGER nora_row_history AFTER INSERT OR DELETE OR UPDATE ON public.geocode_cache FOR EACH ROW EXECUTE FUNCTION public.nora_row_history('lat_rounded', 'lng_rounded');
CREATE TRIGGER nora_row_history AFTER INSERT OR DELETE OR UPDATE ON public.instagram_summons FOR EACH ROW EXECUTE FUNCTION public.nora_row_history('id');
CREATE TRIGGER nora_row_history AFTER INSERT OR DELETE OR UPDATE ON public.media_objects FOR EACH ROW EXECUTE FUNCTION public.nora_row_history('sha256');
CREATE TRIGGER nora_row_history AFTER INSERT OR DELETE OR UPDATE ON public.message_delete FOR EACH ROW EXECUTE FUNCTION public.nora_row_history('id');
CREATE TRIGGER nora_row_history AFTER INSERT OR DELETE OR UPDATE ON public.message_edit FOR EACH ROW EXECUTE FUNCTION public.nora_row_history('id');
CREATE TRIGGER nora_row_history AFTER INSERT OR DELETE OR UPDATE ON public.message_reaction FOR EACH ROW EXECUTE FUNCTION public.nora_row_history('id');
CREATE TRIGGER nora_row_history AFTER INSERT OR DELETE OR UPDATE ON public.messages FOR EACH ROW EXECUTE FUNCTION public.nora_row_history('id');
CREATE TRIGGER nora_row_history AFTER INSERT OR DELETE OR UPDATE ON public.music_queue FOR EACH ROW EXECUTE FUNCTION public.nora_row_history('id');
CREATE TRIGGER nora_row_history AFTER INSERT OR DELETE OR UPDATE ON public.music_session FOR EACH ROW EXECUTE FUNCTION public.nora_row_history('guild_id');
CREATE TRIGGER nora_row_history AFTER INSERT OR DELETE OR UPDATE ON public.note FOR EACH ROW EXECUTE FUNCTION public.nora_row_history('id');
CREATE TRIGGER nora_row_history AFTER INSERT OR DELETE OR UPDATE ON public.note_candidate FOR EACH ROW EXECUTE FUNCTION public.nora_row_history('id');
CREATE TRIGGER nora_row_history AFTER INSERT OR DELETE OR UPDATE ON public.participation_window_state FOR EACH ROW EXECUTE FUNCTION public.nora_row_history('platform', 'conversation_id');
CREATE TRIGGER nora_row_history AFTER INSERT OR DELETE OR UPDATE ON public.persona FOR EACH ROW EXECUTE FUNCTION public.nora_row_history('key');
CREATE TRIGGER nora_row_history AFTER INSERT OR DELETE OR UPDATE ON public.presence_corridors FOR EACH ROW EXECUTE FUNCTION public.nora_row_history('id');
CREATE TRIGGER nora_row_history AFTER INSERT OR DELETE OR UPDATE ON public.presence_derived_places FOR EACH ROW EXECUTE FUNCTION public.nora_row_history('id');
CREATE TRIGGER nora_row_history AFTER INSERT OR DELETE OR UPDATE ON public.presence_fixes FOR EACH ROW EXECUTE FUNCTION public.nora_row_history('id');
CREATE TRIGGER nora_row_history AFTER INSERT OR DELETE OR UPDATE ON public.presence_occupancy FOR EACH ROW EXECUTE FUNCTION public.nora_row_history('id');
CREATE TRIGGER nora_row_history AFTER INSERT OR DELETE OR UPDATE ON public.presence_place_rhythm FOR EACH ROW EXECUTE FUNCTION public.nora_row_history('subject_id', 'place_id');
CREATE TRIGGER nora_row_history AFTER INSERT OR DELETE OR UPDATE ON public.presence_places FOR EACH ROW EXECUTE FUNCTION public.nora_row_history('circle_id', 'place_id');
CREATE TRIGGER nora_row_history AFTER INSERT OR DELETE OR UPDATE ON public.presence_stays FOR EACH ROW EXECUTE FUNCTION public.nora_row_history('id');
CREATE TRIGGER nora_row_history AFTER INSERT OR DELETE OR UPDATE ON public.presence_trips FOR EACH ROW EXECUTE FUNCTION public.nora_row_history('id');
CREATE TRIGGER nora_row_history AFTER INSERT OR DELETE OR UPDATE ON public.reminders FOR EACH ROW EXECUTE FUNCTION public.nora_row_history('id');
CREATE TRIGGER nora_row_history AFTER INSERT OR DELETE OR UPDATE ON public.rollup_build FOR EACH ROW EXECUTE FUNCTION public.nora_row_history('id');
CREATE TRIGGER nora_row_history AFTER INSERT OR DELETE OR UPDATE ON public.rollup_colocation FOR EACH ROW EXECUTE FUNCTION public.nora_row_history('id');
CREATE TRIGGER nora_row_history AFTER INSERT OR DELETE OR UPDATE ON public.rollup_daybook FOR EACH ROW EXECUTE FUNCTION public.nora_row_history('id');
CREATE TRIGGER nora_row_history AFTER INSERT OR DELETE OR UPDATE ON public.rollup_fold_state FOR EACH ROW EXECUTE FUNCTION public.nora_row_history('build_id', 'subject_id');
CREATE TRIGGER nora_row_history AFTER INSERT OR DELETE OR UPDATE ON public.rollup_outing FOR EACH ROW EXECUTE FUNCTION public.nora_row_history('id');
CREATE TRIGGER nora_row_history AFTER INSERT OR DELETE OR UPDATE ON public.rollup_response FOR EACH ROW EXECUTE FUNCTION public.nora_row_history('id');
CREATE TRIGGER nora_row_history AFTER INSERT OR DELETE OR UPDATE ON public.rollup_tie FOR EACH ROW EXECUTE FUNCTION public.nora_row_history('id');
CREATE TRIGGER nora_row_history AFTER INSERT OR DELETE OR UPDATE ON public.standing FOR EACH ROW EXECUTE FUNCTION public.nora_row_history('platform', 'conversation_id');
CREATE TRIGGER nora_row_history AFTER INSERT OR DELETE OR UPDATE ON public.sticker_index FOR EACH ROW EXECUTE FUNCTION public.nora_row_history('platform', 'fetch_token');
CREATE TRIGGER nora_row_history AFTER INSERT OR DELETE OR UPDATE ON public.tag_debounce_state FOR EACH ROW EXECUTE FUNCTION public.nora_row_history('platform', 'conversation_id');
CREATE TRIGGER nora_row_history AFTER INSERT OR DELETE OR UPDATE ON public.version_announcements FOR EACH ROW EXECUTE FUNCTION public.nora_row_history('version', 'platform', 'conversation_id');
CREATE TRIGGER nora_row_history AFTER INSERT OR DELETE OR UPDATE ON public.video_readings FOR EACH ROW EXECUTE FUNCTION public.nora_row_history('sha256', 'provider', 'model', 'chunk_seconds', 'chunk_max', 'reading_digest');
CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON public.analytics_watermark FOR EACH STATEMENT EXECUTE FUNCTION public.nora_row_history_refuse_truncate();
CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON public.compute_jobs FOR EACH STATEMENT EXECUTE FUNCTION public.nora_row_history_refuse_truncate();
CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON public.computer_sessions FOR EACH STATEMENT EXECUTE FUNCTION public.nora_row_history_refuse_truncate();
CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON public.consolidation_watermark FOR EACH STATEMENT EXECUTE FUNCTION public.nora_row_history_refuse_truncate();
CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON public.culture FOR EACH STATEMENT EXECUTE FUNCTION public.nora_row_history_refuse_truncate();
CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON public.dream_schedule_state FOR EACH STATEMENT EXECUTE FUNCTION public.nora_row_history_refuse_truncate();
CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON public.events FOR EACH STATEMENT EXECUTE FUNCTION public.nora_row_history_refuse_truncate();
CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON public.geocode_cache FOR EACH STATEMENT EXECUTE FUNCTION public.nora_row_history_refuse_truncate();
CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON public.instagram_summons FOR EACH STATEMENT EXECUTE FUNCTION public.nora_row_history_refuse_truncate();
CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON public.media_objects FOR EACH STATEMENT EXECUTE FUNCTION public.nora_row_history_refuse_truncate();
CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON public.message_delete FOR EACH STATEMENT EXECUTE FUNCTION public.nora_row_history_refuse_truncate();
CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON public.message_edit FOR EACH STATEMENT EXECUTE FUNCTION public.nora_row_history_refuse_truncate();
CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON public.message_reaction FOR EACH STATEMENT EXECUTE FUNCTION public.nora_row_history_refuse_truncate();
CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON public.messages FOR EACH STATEMENT EXECUTE FUNCTION public.nora_row_history_refuse_truncate();
CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON public.music_queue FOR EACH STATEMENT EXECUTE FUNCTION public.nora_row_history_refuse_truncate();
CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON public.music_session FOR EACH STATEMENT EXECUTE FUNCTION public.nora_row_history_refuse_truncate();
CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON public.note FOR EACH STATEMENT EXECUTE FUNCTION public.nora_row_history_refuse_truncate();
CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON public.note_candidate FOR EACH STATEMENT EXECUTE FUNCTION public.nora_row_history_refuse_truncate();
CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON public.participation_window_state FOR EACH STATEMENT EXECUTE FUNCTION public.nora_row_history_refuse_truncate();
CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON public.persona FOR EACH STATEMENT EXECUTE FUNCTION public.nora_row_history_refuse_truncate();
CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON public.presence_corridors FOR EACH STATEMENT EXECUTE FUNCTION public.nora_row_history_refuse_truncate();
CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON public.presence_derived_places FOR EACH STATEMENT EXECUTE FUNCTION public.nora_row_history_refuse_truncate();
CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON public.presence_fixes FOR EACH STATEMENT EXECUTE FUNCTION public.nora_row_history_refuse_truncate();
CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON public.presence_occupancy FOR EACH STATEMENT EXECUTE FUNCTION public.nora_row_history_refuse_truncate();
CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON public.presence_place_rhythm FOR EACH STATEMENT EXECUTE FUNCTION public.nora_row_history_refuse_truncate();
CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON public.presence_places FOR EACH STATEMENT EXECUTE FUNCTION public.nora_row_history_refuse_truncate();
CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON public.presence_stays FOR EACH STATEMENT EXECUTE FUNCTION public.nora_row_history_refuse_truncate();
CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON public.presence_trips FOR EACH STATEMENT EXECUTE FUNCTION public.nora_row_history_refuse_truncate();
CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON public.reminders FOR EACH STATEMENT EXECUTE FUNCTION public.nora_row_history_refuse_truncate();
CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON public.rollup_build FOR EACH STATEMENT EXECUTE FUNCTION public.nora_row_history_refuse_truncate();
CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON public.rollup_colocation FOR EACH STATEMENT EXECUTE FUNCTION public.nora_row_history_refuse_truncate();
CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON public.rollup_daybook FOR EACH STATEMENT EXECUTE FUNCTION public.nora_row_history_refuse_truncate();
CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON public.rollup_fold_state FOR EACH STATEMENT EXECUTE FUNCTION public.nora_row_history_refuse_truncate();
CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON public.rollup_outing FOR EACH STATEMENT EXECUTE FUNCTION public.nora_row_history_refuse_truncate();
CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON public.rollup_response FOR EACH STATEMENT EXECUTE FUNCTION public.nora_row_history_refuse_truncate();
CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON public.rollup_tie FOR EACH STATEMENT EXECUTE FUNCTION public.nora_row_history_refuse_truncate();
CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON public.standing FOR EACH STATEMENT EXECUTE FUNCTION public.nora_row_history_refuse_truncate();
CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON public.sticker_index FOR EACH STATEMENT EXECUTE FUNCTION public.nora_row_history_refuse_truncate();
CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON public.tag_debounce_state FOR EACH STATEMENT EXECUTE FUNCTION public.nora_row_history_refuse_truncate();
CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON public.version_announcements FOR EACH STATEMENT EXECUTE FUNCTION public.nora_row_history_refuse_truncate();
CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON public.video_readings FOR EACH STATEMENT EXECUTE FUNCTION public.nora_row_history_refuse_truncate();
CREATE UNIQUE INDEX note_candidate_create_anchor_key ON public.note_candidate USING btree (unit, anchor_date, anchor_entity_ids, lower(btrim(title))) WHERE (kind = 'create'::text);
CREATE UNIQUE INDEX uq_cognition_wake_outcome_wake ON public.cognition_wake_outcome USING btree (wake_id);
CREATE UNIQUE INDEX uq_computer_sessions_open_scope ON public.computer_sessions USING btree (scope) WHERE (status = 'open'::text);
CREATE UNIQUE INDEX uq_lesson_revision_seq ON public.lesson_revision USING btree (lesson_id, seq);
CREATE UNIQUE INDEX uq_memo_revision_seq ON public.memo_revision USING btree (memo_id, seq);
CREATE UNIQUE INDEX uq_otp_attempt_ok ON public.otp_attempt USING btree (challenge_id) WHERE ok;
CREATE UNIQUE INDEX uq_persona_line_supersedes ON public.persona_line USING btree (supersedes) WHERE (supersedes IS NOT NULL);
CREATE UNIQUE INDEX uq_reminders_proactive_open_note ON public.reminders USING btree (((payload ->> 'note_id'::text))) WHERE ((kind = 'proactive'::text) AND (state = ANY (ARRAY['pending'::text, 'firing'::text])));
CREATE UNIQUE INDEX uq_rollup_build_current ON public.rollup_build USING btree (status) WHERE (status = 'current'::text);
CREATE UNIQUE INDEX uq_row_history_open ON public.row_history USING btree (table_name, row_key) WHERE (superseded_at IS NULL);
CREATE UNIQUE INDEX uq_social_item_key ON public.social_item USING btree (network, item_id);
CREATE UNIQUE INDEX uq_stimulus_retraction_stimulus ON public.stimulus_retraction USING btree (stimulus_id);
CREATE UNIQUE INDEX uq_stimulus_rollup_ref ON public.stimulus USING btree (unit, ref) WHERE (kind = 'rollup'::text);
CREATE UNIQUE INDEX uq_stimulus_social_ref ON public.stimulus USING btree (unit, ref) WHERE (kind = 'social'::text);
CREATE UNIQUE INDEX uq_stimulus_time_ref ON public.stimulus USING btree (unit, ref) WHERE (kind = 'time'::text);
DECLARE
ELSE ((prediction IS NULL) AND (prediction_observable IS NULL) AND (prediction_window_end IS NULL))
END
END
END
END
END IF;
END IF;
END IF;
END IF;
END IF;
END IF;
END IF;
END IF;
END IF;
END IF;
END IF;
END LOOP;
END),
ERRCODE = 'insufficient_privilege';
ERRCODE = 'insufficient_privilege';
FOREACH col IN ARRAY TG_ARGV LOOP
IF TG_OP <> 'DELETE' THEN
IF TG_OP <> 'INSERT' THEN
IF TG_OP = 'DELETE' OR (TG_OP = 'UPDATE' AND new_key <> old_key) THEN
IF TG_OP = 'DELETE' THEN
IF TG_OP = 'TRUNCATE' AND current_setting('nora.row_history', true) = 'off' THEN
IF TG_OP = 'TRUNCATE' AND current_setting('nora.row_history', true) = 'off' THEN
IF TG_OP = 'UPDATE' AND new_image = old_image THEN
IF current_setting('nora.row_history', true) = 'off' THEN
IF current_setting('nora.row_history', true) = 'off' THEN
IF new_image IS NOT NULL THEN
IF old_image IS NOT NULL THEN
INCREMENT BY 1
INCREMENT BY 1
INCREMENT BY 1
INCREMENT BY 1
INCREMENT BY 1
INCREMENT BY 1
INCREMENT BY 1
INCREMENT BY 1
INCREMENT BY 1
INCREMENT BY 1
INCREMENT BY 1
INCREMENT BY 1
INCREMENT BY 1
INCREMENT BY 1
INCREMENT BY 1
INCREMENT BY 1
INCREMENT BY 1
INCREMENT BY 1
INCREMENT BY 1
INCREMENT BY 1
INCREMENT BY 1
INCREMENT BY 1
INCREMENT BY 1
INCREMENT BY 1
INCREMENT BY 1
INCREMENT BY 1
INCREMENT BY 1
INCREMENT BY 1
INCREMENT BY 1
INCREMENT BY 1
INCREMENT BY 1
INCREMENT BY 1
INCREMENT BY 1
INCREMENT BY 1
INCREMENT BY 1
INSERT INTO row_history (id, table_name, row_key, op, image, recorded_at)
INSERT INTO row_history (id, table_name, row_key, op, image, recorded_at)
LANGUAGE plpgsql
LANGUAGE plpgsql
LANGUAGE plpgsql
LANGUAGE plpgsql
MESSAGE = 'computer_events is append-only: ' || TG_OP || ' refused',
MESSAGE = 'computer_exports is append-only: ' || TG_OP || ' refused',
NO MAXVALUE
NO MAXVALUE
NO MAXVALUE
NO MAXVALUE
NO MAXVALUE
NO MAXVALUE
NO MAXVALUE
NO MAXVALUE
NO MAXVALUE
NO MAXVALUE
NO MAXVALUE
NO MAXVALUE
NO MAXVALUE
NO MAXVALUE
NO MAXVALUE
NO MAXVALUE
NO MAXVALUE
NO MAXVALUE
NO MAXVALUE
NO MAXVALUE
NO MAXVALUE
NO MAXVALUE
NO MAXVALUE
NO MAXVALUE
NO MAXVALUE
NO MAXVALUE
NO MAXVALUE
NO MAXVALUE
NO MAXVALUE
NO MAXVALUE
NO MAXVALUE
NO MAXVALUE
NO MAXVALUE
NO MAXVALUE
NO MAXVALUE
NO MINVALUE
NO MINVALUE
NO MINVALUE
NO MINVALUE
NO MINVALUE
NO MINVALUE
NO MINVALUE
NO MINVALUE
NO MINVALUE
NO MINVALUE
NO MINVALUE
NO MINVALUE
NO MINVALUE
NO MINVALUE
NO MINVALUE
NO MINVALUE
NO MINVALUE
NO MINVALUE
NO MINVALUE
NO MINVALUE
NO MINVALUE
NO MINVALUE
NO MINVALUE
NO MINVALUE
NO MINVALUE
NO MINVALUE
NO MINVALUE
NO MINVALUE
NO MINVALUE
NO MINVALUE
NO MINVALUE
NO MINVALUE
NO MINVALUE
NO MINVALUE
NO MINVALUE
RAISE EXCEPTION USING
RAISE EXCEPTION USING
RAISE EXCEPTION USING MESSAGE = 'TRUNCATE ' || TG_TABLE_NAME
RETURN NULL;
RETURN NULL;
RETURN NULL;
RETURN NULL;
RETURN NULL;
RETURN NULL;
RETURN NULL;
SEQUENCE NAME public.assess_report_id_seq
SEQUENCE NAME public.cognition_carry_id_seq
SEQUENCE NAME public.cognition_self_wake_id_seq
SEQUENCE NAME public.cognition_session_close_id_seq
SEQUENCE NAME public.cognition_session_id_seq
SEQUENCE NAME public.cognition_wake_id_seq
SEQUENCE NAME public.cognition_wake_outcome_id_seq
SEQUENCE NAME public.config_revision_id_seq
SEQUENCE NAME public.deploy_revision_id_seq
SEQUENCE NAME public.doctrine_revision_id_seq
SEQUENCE NAME public.document_state_id_seq
SEQUENCE NAME public.exchange_observation_id_seq
SEQUENCE NAME public.language_observation_id_seq
SEQUENCE NAME public.memory_node_id_seq
SEQUENCE NAME public.message_delete_id_seq
SEQUENCE NAME public.message_edit_id_seq
SEQUENCE NAME public.message_reaction_id_seq
SEQUENCE NAME public.messages_id_seq
SEQUENCE NAME public.otp_attempt_id_seq
SEQUENCE NAME public.presence_corridors_id_seq
SEQUENCE NAME public.presence_derived_places_id_seq
SEQUENCE NAME public.presence_fixes_id_seq
SEQUENCE NAME public.presence_occupancy_id_seq
SEQUENCE NAME public.presence_stays_id_seq
SEQUENCE NAME public.presence_trips_id_seq
SEQUENCE NAME public.rollup_build_id_seq
SEQUENCE NAME public.rollup_colocation_id_seq
SEQUENCE NAME public.rollup_daybook_id_seq
SEQUENCE NAME public.rollup_outing_id_seq
SEQUENCE NAME public.rollup_response_id_seq
SEQUENCE NAME public.rollup_tie_id_seq
SEQUENCE NAME public.row_history_id_seq
SEQUENCE NAME public.source_health_id_seq
SEQUENCE NAME public.stimulus_id_seq
SEQUENCE NAME public.stimulus_retraction_id_seq
START WITH 1
START WITH 1
START WITH 1
START WITH 1
START WITH 1
START WITH 1
START WITH 1
START WITH 1
START WITH 1
START WITH 1
START WITH 1
START WITH 1
START WITH 1
START WITH 1
START WITH 1
START WITH 1
START WITH 1
START WITH 1
START WITH 1
START WITH 1
START WITH 1
START WITH 1
START WITH 1
START WITH 1
START WITH 1
START WITH 1
START WITH 1
START WITH 1
START WITH 1
START WITH 1
START WITH 1
START WITH 1
START WITH 1
START WITH 1
START WITH 1
UPDATE row_history SET superseded_at = at, superseded_by = version_id
UPDATE row_history SET superseded_at = at, superseded_by = version_id
VALUES (version_id, TG_TABLE_NAME, new_key, lower(TG_OP), new_image, at);
VALUES (version_id, TG_TABLE_NAME, old_key, 'delete', old_image, at);
WHEN (mode = 'abduced'::text) THEN ((prediction IS NOT NULL) AND (prediction_observable IS NOT NULL) AND (prediction_window_end IS NOT NULL))
WHERE table_name = TG_TABLE_NAME AND row_key = new_key
WHERE table_name = TG_TABLE_NAME AND row_key = old_key
account text NOT NULL,
accuracy_m double precision,
act text NOT NULL,
act text NOT NULL,
actor text NOT NULL,
actor text NOT NULL,
adaptor_name text NOT NULL,
added_at timestamp with time zone NOT NULL,
added_by_person_id text NOT NULL,
address text
address text
address text NOT NULL,
address text,
advanced_counts boolean DEFAULT false NOT NULL,
advanced_marker boolean DEFAULT false NOT NULL,
agenda jsonb NOT NULL,
aliases text[] DEFAULT '{}'::text[] NOT NULL,
alt_text text,
anchor_date integer,
anchor_date integer,
anchor_entity_ids text[] DEFAULT '{}'::text[] NOT NULL,
anchor_entity_ids text[] DEFAULT '{}'::text[] NOT NULL,
announced_at timestamp with time zone NOT NULL,
artifact_id text,
artifact_id text,
as_of bigint,
as_of bigint,
as_of bigint,
as_of bigint,
as_of bigint,
as_of bigint,
as_of bigint,
as_of bigint,
as_of_ms bigint NOT NULL,
as_of_ms bigint NOT NULL,
as_of_ms bigint NOT NULL,
as_of_ms bigint NOT NULL,
as_of_ms bigint NOT NULL,
assembled_sha256 text,
asserted_at bigint NOT NULL,
asserted_at bigint,
asserted_at bigint,
asserted_by text,
asserted_by text,
at timestamp with time zone NOT NULL,
at timestamptz := clock_timestamp();
at_home_of text[] DEFAULT '{}'::text[] NOT NULL,
at_ms bigint CONSTRAINT signal_at_ms_not_null NOT NULL,
at_ms bigint NOT NULL,
at_ms bigint NOT NULL,
at_ms bigint NOT NULL,
at_ms bigint,
at_ms bigint,
attempted_at timestamp with time zone NOT NULL,
attempts integer DEFAULT 0 NOT NULL,
attempts integer DEFAULT 0 NOT NULL,
attempts integer DEFAULT 1 NOT NULL,
author_igid text NOT NULL,
avg_speed_kmh double precision NOT NULL,
backend_job_id text NOT NULL,
battery double precision,
blob_key text,
body text NOT NULL,
body text NOT NULL,
body text NOT NULL,
body text NOT NULL,
body text NOT NULL,
body text NOT NULL,
body text,
body text,
body text,
body text,
bridge_code text,
build_id bigint NOT NULL,
build_id bigint NOT NULL,
build_id bigint NOT NULL,
build_id bigint NOT NULL,
build_id bigint NOT NULL,
build_id bigint NOT NULL,
build_id bigint NOT NULL,
build_id bigint NOT NULL,
build_id bigint NOT NULL,
build_id bigint NOT NULL,
build_id bigint NOT NULL,
build_id bigint NOT NULL,
built_at text,
calls integer DEFAULT 0 NOT NULL,
canonical_form text NOT NULL,
canonical_form text NOT NULL,
canonical_name text NOT NULL,
caption text,
cause text DEFAULT 'infra'::text NOT NULL,
challenge_id uuid NOT NULL,
challenge_id uuid NOT NULL,
check_model text,
checkpoint jsonb,
checkpoint_at timestamp with time zone,
chunk_max integer NOT NULL,
chunk_seconds integer NOT NULL,
circle_id text NOT NULL,
circle_id text NOT NULL,
circle_id text NOT NULL,
circle_id text NOT NULL,
circle_id text NOT NULL,
citations text[] DEFAULT '{}'::text[] NOT NULL,
citations text[] NOT NULL,
citations text[] NOT NULL,
citations text[] NOT NULL,
citations text[] NOT NULL,
citations text[] NOT NULL,
city text
city text
claim text NOT NULL,
closing_condition text NOT NULL,
closing_condition text,
code_hash text NOT NULL,
code_version text NOT NULL,
col text;
collab boolean DEFAULT false NOT NULL,
colocation_ms bigint NOT NULL,
colocation_ms bigint NOT NULL,
comment_id text,
comments bigint,
completed_at timestamp with time zone,
confidence double precision NOT NULL,
confidence text,
confidence text,
confidence text,
confidence text,
confidence text,
confidence text,
confidence text,
config_revision_id bigint,
consolidator_name text NOT NULL,
consumed_verdict_tag text,
consumed_verdict_tag text,
content text DEFAULT ''::text NOT NULL
content text NOT NULL,
content_sha256 text NOT NULL,
content_sha256 text NOT NULL,
context text NOT NULL,
context_sha256 text DEFAULT ''::text NOT NULL,
continuation_calls integer NOT NULL,
conversation_id text NOT NULL,
conversation_id text NOT NULL,
conversation_id text NOT NULL,
conversation_id text NOT NULL,
conversation_id text NOT NULL,
conversation_id text NOT NULL,
conversation_id text NOT NULL,
conversation_id text NOT NULL,
conversation_id text NOT NULL,
conversation_id text NOT NULL,
conversation_id text NOT NULL,
conversation_id text NOT NULL,
conversation_id text NOT NULL,
conversation_id text NOT NULL,
conversation_id text NOT NULL,
conversation_id text NOT NULL,
conversation_id text NOT NULL,
conversation_id text NOT NULL,
conversation_id text NOT NULL,
conversation_id text NOT NULL,
conversation_id text NOT NULL,
conversation_id text NOT NULL,
conversation_id text,
conversation_type text NOT NULL,
conversation_type text NOT NULL,
conversation_type text NOT NULL,
conversation_type text NOT NULL,
conversation_type text NOT NULL,
corpus text NOT NULL,
corridor_id integer NOT NULL,
corridor_id integer,
created_at bigint NOT NULL
created_at bigint NOT NULL,
created_at bigint NOT NULL,
created_at bigint NOT NULL,
created_at bigint NOT NULL,
created_at bigint NOT NULL,
created_at bigint NOT NULL,
created_at text DEFAULT (now())::text NOT NULL
created_at text DEFAULT (now())::text NOT NULL
created_at text DEFAULT (now())::text NOT NULL,
created_at text DEFAULT (now())::text NOT NULL,
created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
created_at timestamp with time zone DEFAULT now() NOT NULL
created_at timestamp with time zone DEFAULT now() NOT NULL
created_at timestamp with time zone DEFAULT now() NOT NULL,
created_at timestamp with time zone DEFAULT now() NOT NULL,
created_at timestamp with time zone DEFAULT now() NOT NULL,
created_at timestamp with time zone DEFAULT now() NOT NULL,
created_at timestamp with time zone DEFAULT now() NOT NULL,
created_at timestamp with time zone NOT NULL,
created_at timestamp with time zone NOT NULL,
created_at timestamp with time zone NOT NULL,
created_at_ms bigint NOT NULL,
created_by text NOT NULL,
created_by text NOT NULL,
created_by_tag text NOT NULL,
cues text[] DEFAULT '{}'::text[] CONSTRAINT signal_cues_not_null NOT NULL,
culture_key text NOT NULL,
cursor text,
cut_cooldown integer DEFAULT 0 NOT NULL,
deleted_at timestamp with time zone NOT NULL,
delivery_status text DEFAULT 'pending'::text NOT NULL,
deriver_name text NOT NULL,
describe_model text,
description text DEFAULT ''::text NOT NULL,
description text NOT NULL,
description text,
destination text NOT NULL,
detail jsonb DEFAULT '{}'::jsonb NOT NULL,
detail jsonb DEFAULT '{}'::jsonb NOT NULL,
detail text DEFAULT ''::text NOT NULL,
detail text DEFAULT ''::text NOT NULL,
disputed_at bigint,
disputed_at bigint,
disputed_by text NOT NULL,
disputed_by text,
disputed_by text,
disputed_reason text,
disputed_reason text,
disputed_tag_id text,
disputed_tag_id text,
distance_m double precision NOT NULL,
distinct_speakers integer DEFAULT 0 NOT NULL,
district text,
district text,
doctrine_file text NOT NULL,
doctrine_line integer NOT NULL,
doctrine_sha256 text,
doctrine_text text NOT NULL,
document_id uuid NOT NULL,
document_id uuid NOT NULL,
draws_on uuid[] DEFAULT '{}'::uuid[] NOT NULL,
dst_id text NOT NULL,
dst_kind text NOT NULL,
due_at timestamp with time zone NOT NULL,
due_ms bigint,
duplicate_id uuid NOT NULL,
duplicate_title text NOT NULL,
duration_mad_ms bigint,
duration_median_ms bigint,
dwell_ms bigint DEFAULT 0 NOT NULL,
edits integer NOT NULL,
eligible_count integer NOT NULL,
embed_attempts integer DEFAULT 0 NOT NULL,
embed_error text,
embed_input text,
embedded_at bigint,
embedding public.halfvec(2048),
embedding_model text,
emoji text NOT NULL,
ended_ts bigint NOT NULL,
ended_ts bigint NOT NULL,
entity_id uuid NOT NULL
entity_kind text NOT NULL,
error text,
established_by text NOT NULL,
established_by text NOT NULL,
established_by text NOT NULL,
established_by text NOT NULL,
established_by text NOT NULL,
established_by text NOT NULL,
established_by text NOT NULL,
established_by text NOT NULL,
established_in text NOT NULL,
event text NOT NULL,
event_id uuid NOT NULL,
event_id uuid NOT NULL,
event_id uuid,
examples jsonb DEFAULT '[]'::jsonb NOT NULL,
examples jsonb DEFAULT '[]'::jsonb NOT NULL,
exchange_basis text[] DEFAULT '{}'::text[] NOT NULL,
exclude_persons text[] DEFAULT '{}'::text[] NOT NULL,
exclude_persons text[] DEFAULT '{}'::text[] NOT NULL,
exclude_persons text[] DEFAULT '{}'::text[] NOT NULL,
exclude_persons text[] DEFAULT '{}'::text[] NOT NULL,
exclude_persons text[] DEFAULT '{}'::text[] NOT NULL,
exclude_persons text[] DEFAULT '{}'::text[] NOT NULL,
exclude_persons text[] DEFAULT '{}'::text[] NOT NULL,
exclude_persons text[] DEFAULT '{}'::text[] NOT NULL,
expired_at timestamp with time zone,
expired_reason text,
expires_at bigint
expires_at timestamp with time zone NOT NULL,
expires_at timestamp with time zone,
external_files_json text,
extractor_confidence double precision NOT NULL,
extractor_confidence double precision NOT NULL,
fact_id uuid NOT NULL,
fact_id uuid NOT NULL,
failure_reason text,
fetch_token text NOT NULL,
fidelity text DEFAULT 'tentative'::text NOT NULL,
field_meta jsonb DEFAULT '{}'::jsonb NOT NULL,
field_meta jsonb DEFAULT '{}'::jsonb NOT NULL,
file text NOT NULL,
files jsonb NOT NULL,
finished_at timestamp with time zone,
finished_at timestamp with time zone,
fired_at timestamp with time zone,
first_failed_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
first_seen timestamp with time zone DEFAULT now() NOT NULL,
first_seen_at bigint NOT NULL,
first_seen_at bigint NOT NULL,
first_seen_at bigint NOT NULL,
first_seen_at bigint,
first_seen_ts bigint NOT NULL,
first_seen_ts bigint NOT NULL,
fix_count integer NOT NULL,
fix_count integer NOT NULL,
fold_count integer DEFAULT 0 NOT NULL,
fold_count integer NOT NULL,
follows integer DEFAULT 0 NOT NULL,
from_stimulus_id bigint CONSTRAINT cognition_wake_from_signal_id_not_null NOT NULL,
from_stimulus_id bigint NOT NULL,
frontier_ts bigint NOT NULL,
git_sha text,
grounded boolean NOT NULL,
guild_id text NOT NULL,
guild_id text NOT NULL,
head_revision_id uuid NOT NULL,
head_revision_id uuid NOT NULL,
head_revision_id uuid,
hidden_at bigint,
hits integer DEFAULT 0 NOT NULL
hold_open text,
hold_open text,
home_ms bigint NOT NULL,
homes_unknown text[] DEFAULT '{}'::text[] NOT NULL,
hostname text DEFAULT ''::text NOT NULL,
hostname text DEFAULT ''::text NOT NULL,
hostname text DEFAULT ''::text NOT NULL,
hour_of_week integer NOT NULL,
hour_of_week integer NOT NULL,
i_owe text,
i_owe text,
id bigint CONSTRAINT signal_id_not_null NOT NULL,
id bigint NOT NULL,
id bigint NOT NULL,
id bigint NOT NULL,
id bigint NOT NULL,
id bigint NOT NULL,
id bigint NOT NULL,
id bigint NOT NULL,
id bigint NOT NULL,
id bigint NOT NULL,
id bigint NOT NULL,
id bigint NOT NULL,
id bigint NOT NULL,
id bigint NOT NULL,
id bigint NOT NULL,
id bigint NOT NULL,
id bigint NOT NULL,
id bigint NOT NULL,
id bigint NOT NULL,
id bigint NOT NULL,
id bigint NOT NULL,
id boolean DEFAULT true NOT NULL,
id integer NOT NULL,
id integer NOT NULL,
id integer NOT NULL,
id integer NOT NULL,
id integer NOT NULL,
id integer NOT NULL,
id integer NOT NULL,
id integer NOT NULL,
id integer NOT NULL,
id integer NOT NULL,
id integer NOT NULL,
id integer NOT NULL,
id integer NOT NULL,
id integer NOT NULL,
id text NOT NULL,
id text NOT NULL,
id uuid DEFAULT gen_random_uuid() NOT NULL,
id uuid DEFAULT gen_random_uuid() NOT NULL,
id uuid DEFAULT gen_random_uuid() NOT NULL,
id uuid DEFAULT gen_random_uuid() NOT NULL,
id uuid DEFAULT gen_random_uuid() NOT NULL,
id uuid DEFAULT gen_random_uuid() NOT NULL,
id uuid DEFAULT gen_random_uuid() NOT NULL,
id uuid DEFAULT gen_random_uuid() NOT NULL,
id uuid DEFAULT gen_random_uuid() NOT NULL,
id uuid DEFAULT gen_random_uuid() NOT NULL,
id uuid DEFAULT gen_random_uuid() NOT NULL,
id uuid DEFAULT gen_random_uuid() NOT NULL,
id uuid DEFAULT gen_random_uuid() NOT NULL,
id uuid DEFAULT gen_random_uuid() NOT NULL,
id uuid DEFAULT gen_random_uuid() NOT NULL,
id uuid DEFAULT gen_random_uuid() NOT NULL,
id uuid DEFAULT gen_random_uuid() NOT NULL,
id uuid DEFAULT gen_random_uuid() NOT NULL,
id uuid DEFAULT gen_random_uuid() NOT NULL,
id uuid DEFAULT gen_random_uuid() NOT NULL,
id uuid DEFAULT gen_random_uuid() NOT NULL,
id uuid DEFAULT gen_random_uuid() NOT NULL,
id uuid NOT NULL,
id uuid NOT NULL,
id uuid NOT NULL,
id uuid NOT NULL,
id uuid NOT NULL,
id uuid NOT NULL,
id uuid NOT NULL,
id uuid NOT NULL,
id uuid NOT NULL,
id uuid NOT NULL,
id uuid NOT NULL,
id uuid NOT NULL,
id uuid NOT NULL,
id uuid NOT NULL,
image jsonb NOT NULL,
image_id text,
instruction_sha256 text NOT NULL,
instruction_sha256 text NOT NULL,
instruction_sha256 text,
instruction_sha256 text,
instruction_sha256 text,
instruction_sha256 text,
instruction_sha256 text,
instruction_sha256 text,
instruction_sha256 text,
instruction_sha256 text,
intent text,
is_driving integer,
is_nora integer DEFAULT 0 NOT NULL,
iso_week text NOT NULL,
issued_at timestamp with time zone NOT NULL,
item text CONSTRAINT signal_item_not_null NOT NULL,
item_id text NOT NULL,
item_id text NOT NULL,
item_id text NOT NULL,
item_id text NOT NULL,
item_index integer NOT NULL,
items_posted integer
key text NOT NULL,
key text NOT NULL,
kind text CONSTRAINT signal_kind_not_null NOT NULL,
kind text DEFAULT 'miss'::text NOT NULL,
kind text DEFAULT 'reminder'::text NOT NULL,
kind text NOT NULL,
kind text NOT NULL,
kind text NOT NULL,
kind text NOT NULL,
kind text NOT NULL,
kind text NOT NULL,
kind text NOT NULL,
kind text NOT NULL,
kind text NOT NULL,
kind text NOT NULL,
kind text NOT NULL,
label text,
last_activity_at timestamp with time zone NOT NULL,
last_adjudicated_at bigint,
last_allowed_at bigint NOT NULL,
last_cluster_id integer DEFAULT 0 NOT NULL,
last_error text NOT NULL
last_fired_at bigint NOT NULL,
last_fix_id integer DEFAULT 0 NOT NULL,
last_folded_row_id bigint,
last_folded_row_id bigint,
last_message_id bigint DEFAULT 0 NOT NULL,
last_played_locator text,
last_played_source text,
last_played_title text,
last_rendered_at bigint NOT NULL,
last_seen timestamp with time zone DEFAULT now() NOT NULL,
last_seen_at bigint NOT NULL,
last_seen_at bigint NOT NULL,
last_seen_at bigint NOT NULL,
last_seen_at bigint,
last_seen_ts bigint NOT NULL,
last_seen_ts bigint NOT NULL,
last_send_at bigint,
last_supported_at bigint,
last_tag_id text NOT NULL,
last_wake_revision_id uuid,
last_window_end_at bigint NOT NULL,
lat double precision NOT NULL,
lat double precision NOT NULL,
lat double precision NOT NULL,
lat double precision NOT NULL,
lat double precision NOT NULL,
lat double precision NOT NULL,
lat_rounded double precision NOT NULL,
late_fixes integer DEFAULT 0 NOT NULL,
latency_ms integer,
latest_reel_media bigint,
lesson_id uuid NOT NULL,
lexeme_basis text[] DEFAULT '{}'::text[] NOT NULL,
likes bigint,
likes_hidden boolean NOT NULL,
line_id uuid,
lineage jsonb DEFAULT '[]'::jsonb NOT NULL,
lineage jsonb DEFAULT '[]'::jsonb NOT NULL,
lineage jsonb DEFAULT '[]'::jsonb NOT NULL,
lineage jsonb DEFAULT '{}'::jsonb NOT NULL,
lineage jsonb DEFAULT '{}'::jsonb NOT NULL,
lineage jsonb DEFAULT '{}'::jsonb NOT NULL,
lineage jsonb DEFAULT '{}'::jsonb NOT NULL,
lineage jsonb DEFAULT '{}'::jsonb NOT NULL,
lineage jsonb NOT NULL,
lineage jsonb NOT NULL,
lineage jsonb NOT NULL,
lineage jsonb NOT NULL,
lineage jsonb NOT NULL,
lineage jsonb NOT NULL,
lineage jsonb NOT NULL,
lineage jsonb NOT NULL,
lineage jsonb NOT NULL,
lineage jsonb NOT NULL,
lineage jsonb NOT NULL,
lng double precision NOT NULL,
lng double precision NOT NULL,
lng double precision NOT NULL,
lng double precision NOT NULL,
lng double precision NOT NULL,
lng double precision NOT NULL,
lng_rounded double precision NOT NULL,
loaded_at timestamp with time zone NOT NULL,
loaded_at timestamp with time zone NOT NULL,
local_date text NOT NULL,
location text,
logical_now_ms bigint NOT NULL,
manifest_status text NOT NULL,
mean_arrival_minute double precision,
mean_departure_minute double precision,
media text[] DEFAULT '{}'::text[] NOT NULL,
media_count integer,
media_id text NOT NULL,
media_json text,
media_ref text,
media_type text DEFAULT ''::text NOT NULL,
media_type text DEFAULT ''::text NOT NULL,
media_type text NOT NULL,
media_type text NOT NULL,
memo_id uuid NOT NULL,
mention_at timestamp with time zone,
mention_persons_json text
mention_persons_json text,
mentioned text[] DEFAULT '{}'::text[] NOT NULL,
mentions_ab integer NOT NULL,
mentions_ba integer NOT NULL,
mentions_json text,
mentions_json text,
message_id bigint NOT NULL,
message_id integer NOT NULL,
message_id integer NOT NULL,
message_id integer NOT NULL,
message_id integer NOT NULL,
message_id integer NOT NULL,
message_id integer NOT NULL,
message_id text NOT NULL,
message_ids integer[] DEFAULT '{}'::integer[] NOT NULL,
message_ids integer[] NOT NULL,
messages integer NOT NULL,
messages jsonb NOT NULL,
mode text NOT NULL,
mode text NOT NULL,
mode text NOT NULL,
mode text NOT NULL,
mode text NOT NULL,
mode text NOT NULL,
model text NOT NULL,
model text NOT NULL,
model text NOT NULL,
model text NOT NULL,
model text,
model text,
model text,
model text,
model text,
model text,
model text,
model text,
model text,
model_calls integer NOT NULL,
model_id text,
model_route text,
name text NOT NULL,
named_persons text[] DEFAULT '{}'::text[] NOT NULL,
narrative text NOT NULL,
network text NOT NULL,
network text NOT NULL,
network text NOT NULL,
network text NOT NULL,
network text NOT NULL,
new_image := to_jsonb(NEW);
new_image jsonb;
new_key := new_key || jsonb_build_object(col, new_image -> col);
new_key jsonb := '{}'::jsonb;
note text,
note_id text,
note_id uuid NOT NULL,
note_id uuid NOT NULL,
notify_channel_id text NOT NULL,
now_playing_locator text,
now_playing_source text,
now_playing_title text,
object text NOT NULL,
object_entity_id uuid,
object_literal text NOT NULL,
objective text NOT NULL,
observed_at bigint NOT NULL,
observed_at bigint NOT NULL,
observed_in text[] DEFAULT '{}'::text[] NOT NULL,
observed_in text[] DEFAULT '{}'::text[] NOT NULL,
occurrence integer DEFAULT 0 NOT NULL,
occurrence_count integer DEFAULT 0 NOT NULL,
occurrence_count integer DEFAULT 0 NOT NULL,
occurrence_count integer DEFAULT 1 NOT NULL,
occurrence_count integer DEFAULT 1 NOT NULL,
ok boolean NOT NULL,
old_image := to_jsonb(OLD);
old_image jsonb;
old_key := old_key || jsonb_build_object(col, old_image -> col);
old_key jsonb := '{}'::jsonb;
oldest_pending_ms bigint,
op text NOT NULL,
open integer DEFAULT 0 NOT NULL
open integer DEFAULT 0 NOT NULL,
open_stay jsonb,
operation_id uuid NOT NULL,
operation_id uuid NOT NULL,
origin text NOT NULL,
origin text NOT NULL,
origin_room text,
outcome text NOT NULL,
outcome text NOT NULL,
outcome text NOT NULL,
outcome text NOT NULL,
outcome text NOT NULL,
outcome text NOT NULL,
outcome text NOT NULL,
outcome text NOT NULL,
outcome text,
outcome text,
outings integer NOT NULL,
output_ref jsonb DEFAULT '{}'::jsonb NOT NULL,
owed_since timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
p50_kmh double precision NOT NULL,
p90_kmh double precision NOT NULL,
params_sha256 text NOT NULL,
parent_revision_id uuid,
parent_revision_id uuid,
parent_revision_id uuid,
part text NOT NULL,
part_of_speech text,
participants text[] NOT NULL,
participants text[] NOT NULL,
path text NOT NULL,
path text NOT NULL,
path text NOT NULL,
path text NOT NULL,
paused boolean NOT NULL,
payload jsonb NOT NULL,
payload jsonb,
people text[] DEFAULT '{}'::text[] NOT NULL,
people text[] DEFAULT '{}'::text[] NOT NULL,
person text NOT NULL,
person text NOT NULL,
person text NOT NULL,
person_a text NOT NULL,
person_b text NOT NULL,
person_id text NOT NULL,
person_id text NOT NULL,
person_id text NOT NULL,
person_id text NOT NULL,
person_id text NOT NULL,
person_id text NOT NULL,
person_id text NOT NULL,
person_id text,
person_id text,
person_id text,
person_id text,
person_id text,
person_id text,
persona_key text NOT NULL,
persona_key text NOT NULL,
persons text[] DEFAULT '{}'::text[] NOT NULL,
persons text[] NOT NULL,
persons text[] NOT NULL,
phase text NOT NULL,
place text,
place_id integer NOT NULL,
place_id integer NOT NULL,
place_id integer,
place_id text NOT NULL,
place_label text,
place_label text,
platform text NOT NULL,
platform text NOT NULL,
platform text NOT NULL,
platform text NOT NULL,
platform text NOT NULL,
platform text NOT NULL,
platform text NOT NULL,
platform text NOT NULL,
platform text NOT NULL,
platform text NOT NULL,
platform text NOT NULL,
platform text NOT NULL,
platform text NOT NULL,
platform text NOT NULL,
platform text NOT NULL,
platform text NOT NULL,
platform text NOT NULL,
platform text,
platform_message_id text,
pos_id bigint NOT NULL,
pos_rank integer NOT NULL,
pos_recorded_ms bigint NOT NULL,
predicate text NOT NULL,
predicate text NOT NULL,
prediction text,
prediction_observable text,
prediction_window_end bigint,
premise_fact_id uuid NOT NULL,
probe text,
projected_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
provenance jsonb DEFAULT '{}'::jsonb NOT NULL,
provenance text NOT NULL,
provider text NOT NULL,
purpose text NOT NULL,
quoted_ts bigint,
radius_m double precision NOT NULL,
radius_m double precision NOT NULL,
radius_m double precision NOT NULL,
radius_m double precision,
raw_json text,
raw_json text,
raw_json text,
raw_json text,
reactions integer NOT NULL,
reactions integer NOT NULL,
reactions_ab integer NOT NULL,
reactions_ba integer NOT NULL,
read_id uuid,
read_id uuid,
reading text NOT NULL,
reading_digest text NOT NULL,
reason text DEFAULT ''::text NOT NULL,
reason text DEFAULT ''::text NOT NULL,
reason text NOT NULL,
reason text NOT NULL,
reason text NOT NULL,
reason text NOT NULL,
reason text,
reason text,
reasoning text,
reasoning text,
reasoning text,
reasoning text,
reasoning text,
reasoning text,
reasoning text,
reasoning_effort text NOT NULL,
recognition_score double precision DEFAULT 0 NOT NULL,
recognition_score double precision DEFAULT 0 NOT NULL,
recorded_at bigint NOT NULL,
recorded_at timestamp with time zone DEFAULT clock_timestamp() CONSTRAINT signal_recorded_at_not_null NOT NULL,
recorded_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL
recorded_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL
recorded_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL
recorded_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL
recorded_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL
recorded_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL
recorded_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL
recorded_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
recorded_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
recorded_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
recorded_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
recorded_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
recorded_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
recorded_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
recorded_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
recorded_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
recorded_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
recorded_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
recorded_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
recorded_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
recorded_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
recorded_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
recorded_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
recorded_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
recorded_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
recorded_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
recorded_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
recorded_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
recorded_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
recorded_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
recorded_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
recorded_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
recorded_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
recorded_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
recorded_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
recorded_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
recorded_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
recorded_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
recorded_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
recorded_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
recorded_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
recorded_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
recorded_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
recorded_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
recorded_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
recorded_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
recorded_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
recorded_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
recorded_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
recorded_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
recorded_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
recorded_at timestamp with time zone NOT NULL,
recurrence text DEFAULT 'none'::text NOT NULL,
recurrence_count integer,
recurrence_interval integer DEFAULT 1 NOT NULL,
recurrence_tz text,
recurrence_until timestamp with time zone,
ref text CONSTRAINT signal_ref_not_null NOT NULL,
refusal_detail text,
refusal_reason text,
rel text NOT NULL,
relearn_after bigint,
reminder_id uuid,
reminder_id uuid,
removed integer DEFAULT 0 NOT NULL,
replaced_by uuid,
replies integer NOT NULL,
replies_ab integer NOT NULL,
replies_ba integer NOT NULL,
reply_comment_id text,
reply_text text,
reposts bigint,
request jsonb DEFAULT '{}'::jsonb NOT NULL,
requester_person_id text NOT NULL,
requester_person_id text NOT NULL,
requester_person_id text,
resolved_at timestamp with time zone DEFAULT now() NOT NULL,
responded_at timestamp with time zone NOT NULL,
responders text[] DEFAULT '{}'::text[] NOT NULL,
response text NOT NULL,
result jsonb DEFAULT '{}'::jsonb NOT NULL,
result_document_ids jsonb,
result_report text,
result_summary text,
result_url text,
results jsonb DEFAULT '[]'::jsonb NOT NULL,
retire_reason text,
retired_at bigint,
retired_by text,
revised boolean DEFAULT false NOT NULL,
revision integer DEFAULT 0 NOT NULL,
revision integer NOT NULL,
revisit_at bigint,
rooms jsonb NOT NULL,
row_key jsonb NOT NULL,
row_key text NOT NULL,
run_at_ms bigint NOT NULL,
sample_count integer DEFAULT 0 NOT NULL,
sample_count integer DEFAULT 0 NOT NULL,
sample_count integer NOT NULL,
saved_place_id text,
saved_place_id text,
saved_place_id text,
scope text DEFAULT ''::text NOT NULL,
scope text NOT NULL,
scope text NOT NULL,
scope text NOT NULL,
scope_id text NOT NULL,
scope_id text NOT NULL,
scope_id text NOT NULL,
scope_id text NOT NULL,
scope_id text NOT NULL,
scope_id text NOT NULL,
scope_id text NOT NULL,
seen_at timestamp with time zone DEFAULT now() NOT NULL,
seen_at timestamp with time zone NOT NULL,
sender_id text NOT NULL,
sender_id text NOT NULL,
sender_id text NOT NULL,
sender_name text,
sense text NOT NULL,
sense text NOT NULL,
sent_ts bigint NOT NULL,
seq integer NOT NULL,
seq integer NOT NULL,
sequence jsonb NOT NULL,
sequence jsonb,
series_anchor_at timestamp with time zone,
series_id uuid,
session_id bigint,
session_id bigint,
session_id bigint,
session_id bigint,
session_id uuid,
session_id uuid,
set_by text NOT NULL,
settled_at bigint,
sha256 text NOT NULL,
sha256 text NOT NULL,
sha256 text NOT NULL,
sha256 text NOT NULL,
sha256 text NOT NULL,
sha256 text NOT NULL,
sha256 text NOT NULL,
sha256 text,
shortcode text NOT NULL,
situation jsonb NOT NULL,
size bigint DEFAULT '0'::bigint NOT NULL,
size integer DEFAULT 0 NOT NULL,
size_bytes bigint NOT NULL,
size_bytes bigint NOT NULL,
skill_versions jsonb DEFAULT '{}'::jsonb NOT NULL,
slug text NOT NULL,
source text DEFAULT 'fold'::text NOT NULL,
source text NOT NULL,
source text NOT NULL,
source text NOT NULL,
source text NOT NULL,
source_excerpt text,
source_id text NOT NULL,
source_id text NOT NULL,
source_url text,
span_id text NOT NULL,
spec jsonb NOT NULL,
speed_kmh double precision
src_id text NOT NULL,
src_kind text NOT NULL,
stage text NOT NULL,
started_at timestamp with time zone NOT NULL,
started_at timestamp with time zone NOT NULL,
started_at timestamp with time zone,
started_at_ms bigint NOT NULL,
started_ts bigint NOT NULL,
started_ts bigint NOT NULL,
state text DEFAULT 'live'::text NOT NULL,
state text DEFAULT 'live'::text NOT NULL,
state text DEFAULT 'live'::text NOT NULL,
state text DEFAULT 'pending'::text NOT NULL,
state text NOT NULL,
state text NOT NULL,
state text NOT NULL,
state text NOT NULL,
state text NOT NULL,
state text NOT NULL,
state text NOT NULL,
state text NOT NULL,
state text NOT NULL,
status text NOT NULL,
status text NOT NULL,
status text NOT NULL,
status text NOT NULL,
status text NOT NULL,
status text NOT NULL,
status text NOT NULL,
status text,
stay_count integer NOT NULL,
stay_ms bigint NOT NULL,
stays jsonb NOT NULL,
stimulus_count integer CONSTRAINT cognition_wake_signal_count_not_null NOT NULL,
stimulus_id bigint NOT NULL,
stimulus_id bigint NOT NULL,
stimulus_id bigint,
stopped_at_detail jsonb DEFAULT '{}'::jsonb NOT NULL,
strike integer DEFAULT 0 NOT NULL,
strike integer NOT NULL,
strikes integer DEFAULT 0 NOT NULL,
strip_calls integer NOT NULL,
stripped_blocks integer NOT NULL,
subject_id text NOT NULL,
subject_id text NOT NULL,
subject_id text NOT NULL,
subject_id text NOT NULL,
subject_id text NOT NULL,
subject_id text NOT NULL,
subject_id text NOT NULL,
subject_id text NOT NULL,
subject_id text NOT NULL,
subject_id text NOT NULL,
subject_id text NOT NULL,
subject_rows jsonb NOT NULL,
supersede_reason text,
supersede_source text,
superseded_at bigint,
superseded_at timestamp with time zone,
superseded_at timestamp with time zone,
superseded_by bigint,
superseded_by bigint,
superseded_by uuid,
supersedes text,
supersedes uuid,
support_count integer NOT NULL,
surface text NOT NULL,
surface_form text NOT NULL,
survivor_id uuid NOT NULL,
survivor_title text NOT NULL,
switched_at timestamp with time zone,
synthesized_at bigint NOT NULL
table_name text NOT NULL,
tag_id text NOT NULL,
tag_id text NOT NULL,
tag_id text NOT NULL,
tag_id text NOT NULL,
tag_id text NOT NULL,
tag_id text,
tag_id text,
tagged text[] DEFAULT '{}'::text[] NOT NULL,
tags_sha256 text,
taken_at timestamp with time zone NOT NULL,
task text NOT NULL,
task_sha256 text DEFAULT ''::text NOT NULL,
text text DEFAULT ''::text NOT NULL,
text text NOT NULL,
text text NOT NULL,
text text NOT NULL,
through_ms bigint DEFAULT 0 NOT NULL,
through_stimulus_id bigint CONSTRAINT cognition_wake_through_signal_id_not_null NOT NULL,
through_stimulus_id bigint NOT NULL,
title text DEFAULT ''::text NOT NULL,
title text NOT NULL,
title text NOT NULL,
title text NOT NULL,
title text NOT NULL,
title text NOT NULL,
title text NOT NULL,
title text NOT NULL,
title text NOT NULL,
title text,
told_by text,
tool text NOT NULL,
tool_input jsonb NOT NULL,
tool_name text NOT NULL,
tool_result jsonb NOT NULL,
total_clips_count integer,
trace_id text NOT NULL,
trace_id text,
trace_id text,
tracked integer DEFAULT 0 NOT NULL,
transcript jsonb NOT NULL,
transition text NOT NULL,
ts bigint NOT NULL,
ts bigint NOT NULL,
ts bigint NOT NULL,
ts bigint NOT NULL,
ts bigint NOT NULL,
ts bigint NOT NULL,
ts bigint NOT NULL,
uncertainty text NOT NULL,
unit text CONSTRAINT signal_unit_not_null NOT NULL,
unit text DEFAULT ''::text NOT NULL,
unit text NOT NULL,
unit text NOT NULL,
unit text NOT NULL,
unit text NOT NULL,
unit text NOT NULL,
unit text NOT NULL,
unit text NOT NULL,
unit text NOT NULL,
unit text NOT NULL,
unit text NOT NULL,
unit text NOT NULL,
unit text NOT NULL,
unit text NOT NULL,
unit text NOT NULL,
unit text NOT NULL,
unit text NOT NULL,
unit text NOT NULL,
unit text NOT NULL,
unit text NOT NULL,
unit text NOT NULL,
unit text NOT NULL,
unit text NOT NULL,
unit text NOT NULL,
unit text,
unit text,
unit_id text DEFAULT ''::text NOT NULL,
units text[] DEFAULT '{}'::text[] NOT NULL,
unprojected integer DEFAULT 0 NOT NULL,
unsupported text[] DEFAULT '{}'::text[] NOT NULL,
updated_at bigint NOT NULL,
updated_at bigint NOT NULL,
updated_at bigint NOT NULL,
updated_at bigint NOT NULL,
updated_at timestamp with time zone DEFAULT now() NOT NULL
updated_at timestamp with time zone DEFAULT now() NOT NULL
updated_at timestamp with time zone DEFAULT now() NOT NULL
updated_at timestamp with time zone DEFAULT now() NOT NULL
updated_at timestamp with time zone DEFAULT now() NOT NULL
updated_at timestamp with time zone DEFAULT now() NOT NULL,
updated_at timestamp with time zone NOT NULL,
updated_at timestamp with time zone NOT NULL,
updated_ms bigint NOT NULL,
updated_ts bigint NOT NULL,
updated_ts bigint NOT NULL,
updated_ts bigint NOT NULL,
url text NOT NULL,
url text,
valid_from bigint NOT NULL,
valid_from_ms bigint NOT NULL,
valid_from_ms bigint NOT NULL,
valid_from_ms bigint NOT NULL,
valid_from_ms bigint NOT NULL,
valid_from_ms bigint NOT NULL,
valid_to bigint,
valid_to_ms bigint NOT NULL,
valid_to_ms bigint NOT NULL,
valid_to_ms bigint NOT NULL,
valid_to_ms bigint NOT NULL,
valid_to_ms bigint NOT NULL,
venue_address text,
venue_address text,
venue_city text,
venue_city text,
venue_district text,
venue_district text,
venue_maps_url text,
venue_maps_url text,
venue_name text,
venue_name text,
venue_place_id text,
venue_place_id text,
venue_price_level text DEFAULT ''::text NOT NULL,
venue_price_level text DEFAULT ''::text NOT NULL,
venue_rating double precision,
venue_rating double precision,
venue_resolved_at timestamp with time zone,
venue_text text,
venue_text text,
verb text NOT NULL,
verdict text NOT NULL,
verdict text,
verdict_at bigint,
verdict_by text,
version text NOT NULL,
version text,
version_id := nextval('row_history_id_seq');
version_id := nextval('row_history_id_seq');
version_id bigint;
version_num character varying(32) NOT NULL
voice_channel_id text NOT NULL,
volume double precision NOT NULL,
waits integer DEFAULT 0 CONSTRAINT signal_waits_not_null NOT NULL,
waits integer DEFAULT 0 NOT NULL,
wake_id bigint NOT NULL,
wake_id bigint NOT NULL,
wake_id bigint,
wake_ref text,
warrant_kind text DEFAULT 'source'::text NOT NULL,
why text NOT NULL,
why text NOT NULL,
window_fix_ids integer[] DEFAULT '{}'::integer[] NOT NULL,
wire_state text NOT NULL,
with_persons text[] DEFAULT '{}'::text[] NOT NULL,
working_set jsonb DEFAULT '[]'::jsonb NOT NULL,
working_set jsonb DEFAULT '[]'::jsonb NOT NULL,
would_have_said text NOT NULL,
writer text NOT NULL,
written_at bigint NOT NULL,
written_at bigint NOT NULL,
written_at bigint NOT NULL,
written_at bigint NOT NULL,
written_at bigint NOT NULL,
written_at bigint NOT NULL,
written_at bigint NOT NULL,
written_at timestamp with time zone NOT NULL,
written_by text DEFAULT 'tag'::text NOT NULL,
written_by text NOT NULL,
written_by_session text NOT NULL,
|| ' would bypass row_history; delete the rows (they are kept as history) '
|| 'or SET LOCAL nora.row_history = ''off'' deliberately';

"position" integer NOT NULL,
"when" timestamp with time zone,
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
ADD CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num);
ADD CONSTRAINT analytics_watermark_pkey PRIMARY KEY (deriver_name, subject_id);
ADD CONSTRAINT artifacts_pkey PRIMARY KEY (id);
ADD CONSTRAINT artifacts_slug_key UNIQUE (slug);
ADD CONSTRAINT compute_jobs_pkey PRIMARY KEY (id);
ADD CONSTRAINT config_revision_pkey PRIMARY KEY (id);
ADD CONSTRAINT consolidation_watermark_pkey PRIMARY KEY (consolidator_name, scope_id);
ADD CONSTRAINT culture_head_revision_id_fkey FOREIGN KEY (head_revision_id) REFERENCES public.culture_revision(id);
ADD CONSTRAINT culture_pkey PRIMARY KEY (key);
ADD CONSTRAINT culture_revision_pkey PRIMARY KEY (id);
ADD CONSTRAINT decision_log_pkey PRIMARY KEY (id);
ADD CONSTRAINT deploy_revision_pkey PRIMARY KEY (id);
ADD CONSTRAINT doctrine_revision_pkey PRIMARY KEY (id);
ADD CONSTRAINT dream_schedule_state_pkey PRIMARY KEY (adaptor_name, scope_id);
ADD CONSTRAINT embedding_pkey PRIMARY KEY (kind, row_key);
ADD CONSTRAINT event_rsvps_event_id_fkey FOREIGN KEY (event_id) REFERENCES public.events(id);
ADD CONSTRAINT event_rsvps_pkey PRIMARY KEY (event_id, person_id);
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
ADD CONSTRAINT geocode_cache_pkey PRIMARY KEY (lat_rounded, lng_rounded);
ADD CONSTRAINT instagram_summons_comment_id_key UNIQUE (comment_id);
ADD CONSTRAINT instagram_summons_message_id_key UNIQUE (message_id);
ADD CONSTRAINT instagram_summons_pkey PRIMARY KEY (id);
ADD CONSTRAINT language_observation_message_id_canonical_form_sense_key UNIQUE (message_id, canonical_form, sense);
ADD CONSTRAINT language_observation_pkey PRIMARY KEY (id);
ADD CONSTRAINT media_objects_pkey PRIMARY KEY (sha256);
ADD CONSTRAINT message_edit_message_id_fkey FOREIGN KEY (message_id) REFERENCES public.messages(id) ON DELETE CASCADE;
ADD CONSTRAINT message_edit_pkey PRIMARY KEY (id);
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
ADD CONSTRAINT participation_window_state_pkey PRIMARY KEY (platform, conversation_id);
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
ADD CONSTRAINT room_exchange_pkey PRIMARY KEY (scope_id, act, participants);
ADD CONSTRAINT room_lexeme_pkey PRIMARY KEY (scope_id, canonical_form, sense);
ADD CONSTRAINT room_portrait_pkey PRIMARY KEY (scope_id);
ADD CONSTRAINT row_history_pkey PRIMARY KEY (id);
ADD CONSTRAINT standing_pkey PRIMARY KEY (platform, conversation_id);
ADD CONSTRAINT standing_revision_pkey PRIMARY KEY (id);
ADD CONSTRAINT sticker_index_pkey PRIMARY KEY (platform, fetch_token);
ADD CONSTRAINT tag_debounce_state_pkey PRIMARY KEY (platform, conversation_id);
ADD CONSTRAINT tag_trace_pkey PRIMARY KEY (id);
ADD CONSTRAINT tool_call_log_pkey PRIMARY KEY (id);
ADD CONSTRAINT turn_verdict_pkey PRIMARY KEY (tag_id);
ADD CONSTRAINT uq_music_queue_guild_position UNIQUE (guild_id, "position");
ADD CONSTRAINT uq_occupancy_bin UNIQUE (subject_id, place_id, hour_of_week);
ADD CONSTRAINT version_announcements_pkey PRIMARY KEY (version, platform, conversation_id);
ADD CONSTRAINT video_readings_pkey PRIMARY KEY (sha256, provider, model, chunk_seconds, chunk_max, reading_digest);
ALTER TABLE ONLY public.alembic_version
ALTER TABLE ONLY public.analytics_watermark
ALTER TABLE ONLY public.artifacts
ALTER TABLE ONLY public.artifacts
ALTER TABLE ONLY public.compute_jobs
ALTER TABLE ONLY public.config_revision
ALTER TABLE ONLY public.consolidation_watermark
ALTER TABLE ONLY public.culture
ALTER TABLE ONLY public.culture
ALTER TABLE ONLY public.culture_revision
ALTER TABLE ONLY public.decision_log
ALTER TABLE ONLY public.deploy_revision
ALTER TABLE ONLY public.doctrine_revision
ALTER TABLE ONLY public.dream_schedule_state
ALTER TABLE ONLY public.embedding
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
ALTER TABLE ONLY public.media_objects
ALTER TABLE ONLY public.message_edit
ALTER TABLE ONLY public.message_edit
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
ALTER TABLE ONLY public.participation_window_state
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
ALTER TABLE ONLY public.room_exchange
ALTER TABLE ONLY public.room_lexeme
ALTER TABLE ONLY public.room_portrait
ALTER TABLE ONLY public.row_history
ALTER TABLE ONLY public.standing
ALTER TABLE ONLY public.standing_revision
ALTER TABLE ONLY public.sticker_index
ALTER TABLE ONLY public.tag_debounce_state
ALTER TABLE ONLY public.tag_trace
ALTER TABLE ONLY public.tool_call_log
ALTER TABLE ONLY public.turn_verdict
ALTER TABLE ONLY public.version_announcements
ALTER TABLE ONLY public.video_readings
ALTER TABLE public.config_revision ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
ALTER TABLE public.deploy_revision ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
ALTER TABLE public.doctrine_revision ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
ALTER TABLE public.exchange_observation ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
ALTER TABLE public.language_observation ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
ALTER TABLE public.message_edit ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
ALTER TABLE public.messages ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
ALTER TABLE public.presence_corridors ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
ALTER TABLE public.presence_derived_places ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
ALTER TABLE public.presence_fixes ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
ALTER TABLE public.presence_occupancy ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
ALTER TABLE public.presence_stays ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
ALTER TABLE public.presence_trips ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
ALTER TABLE public.row_history ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
AND superseded_at IS NULL;
AND superseded_at IS NULL;
AS $$
AS $$
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
COMMENT ON EXTENSION pg_trgm IS 'text similarity measurement and index searching based on trigrams';
COMMENT ON EXTENSION vector IS 'vector data type and ivfflat and hnsw access methods';
CONSTRAINT ck_compute_jobs_state CHECK ((state = ANY (ARRAY['pending'::text, 'firing'::text, 'done'::text, 'failed'::text])))
CONSTRAINT ck_corridor_speed_how CHECK (((hour_of_week >= 0) AND (hour_of_week < 168)))
CONSTRAINT ck_derived_places_kind CHECK ((kind = ANY (ARRAY['home'::text, 'work'::text, 'unlabeled'::text])))
CONSTRAINT ck_event_rsvps_response CHECK ((response = ANY (ARRAY['yes'::text, 'no'::text, 'maybe'::text])))
CONSTRAINT ck_events_status CHECK ((status = ANY (ARRAY['proposed'::text, 'confirmed'::text, 'cancelled'::text])))
CONSTRAINT ck_instagram_summons_mode CHECK ((mode = ANY (ARRAY['dry_run'::text, 'live'::text]))),
CONSTRAINT ck_instagram_summons_state CHECK ((state = ANY (ARRAY['claimed'::text, 'unresolved'::text, 'ignored_summoner'::text, 'stale'::text, 'unreadable'::text, 'silent'::text, 'staged'::text, 'flushing'::text, 'published'::text, 'refused'::text, 'capped'::text, 'duplicate'::text, 'ambiguous'::text, 'failed'::text])))
CONSTRAINT ck_music_queue_position_non_negative CHECK (("position" >= 0))
CONSTRAINT ck_music_session_volume_range CHECK (((volume >= (0.0)::double precision) AND (volume <= (2.0)::double precision)))
CONSTRAINT ck_occupancy_how CHECK (((hour_of_week >= 0) AND (hour_of_week < 168)))
CONSTRAINT ck_reasoning_strip_log_counts CHECK (((continuation_calls >= 1) AND (continuation_calls <= model_calls) AND (strip_calls >= 0) AND (strip_calls <= continuation_calls) AND (stripped_blocks >= 0) AND ((strip_calls = 0) = (stripped_blocks = 0)))),
CONSTRAINT ck_reasoning_strip_log_tag_id_not_empty CHECK (((tag_id IS NULL) OR (tag_id <> ''::text)))
CONSTRAINT ck_reminders_recurrence CHECK ((((recurrence = 'none'::text) AND (recurrence_tz IS NULL) AND (recurrence_count IS NULL) AND (recurrence_until IS NULL) AND (series_id IS NULL) AND (series_anchor_at IS NULL) AND (occurrence = 0)) OR ((recurrence <> 'none'::text) AND (recurrence_tz IS NOT NULL) AND (recurrence_count >= 1) AND (series_id IS NOT NULL) AND (series_anchor_at IS NOT NULL) AND (occurrence >= 0) AND (occurrence < recurrence_count)))),
CONSTRAINT ck_reminders_recurrence_interval CHECK ((recurrence_interval >= 1)),
CONSTRAINT ck_reminders_recurrence_kind CHECK ((recurrence = ANY (ARRAY['none'::text, 'minutely'::text, 'hourly'::text, 'daily'::text, 'weekly'::text, 'monthly'::text, 'yearly'::text]))),
CONSTRAINT ck_reminders_state CHECK ((state = ANY (ARRAY['pending'::text, 'firing'::text, 'done'::text, 'cancelled'::text, 'failed'::text])))
CONSTRAINT ck_version_announcements_state CHECK ((state = ANY (ARRAY['announcing'::text, 'done'::text, 'failed'::text])))
CONSTRAINT config_revision_file_check CHECK ((file = ANY (ARRAY['config.toml'::text, 'policy.toml'::text, 'identity.toml'::text])))
CONSTRAINT culture_has_a_key_check CHECK ((length(btrim(key)) > 0))
CONSTRAINT culture_revision_body_fits_the_ceiling_check CHECK ((length(body) <= 32000)),
CONSTRAINT culture_revision_has_a_body_check CHECK ((length(btrim(body)) > 0)),
CONSTRAINT culture_revision_instruction_sha256_check CHECK (((instruction_sha256 IS NULL) OR (instruction_sha256 ~ '^[0-9a-f]{64}$'::text))),
CONSTRAINT culture_revision_model_is_named_check CHECK (((model IS NULL) OR (length(btrim(model)) > 0))),
CONSTRAINT culture_revision_names_its_writer_check CHECK ((length(btrim(established_by)) > 0)),
CONSTRAINT culture_revision_parent_is_not_self_check CHECK (((parent_revision_id IS NULL) OR (parent_revision_id <> id)))
CONSTRAINT decision_log_reason_fits_the_block_check CHECK ((length(reason) <= 80)),
CONSTRAINT decision_log_verdict_is_known_check CHECK ((verdict = ANY (ARRAY['spoke'::text, 'silent'::text, 'remind'::text, 'decline'::text, 'replied'::text, 'failed'::text]))),
CONSTRAINT decision_log_writer_is_known_check CHECK ((writer = ANY (ARRAY['participation'::text, 'proactive_decide'::text, 'proactive_act'::text, 'tag'::text])))
CONSTRAINT deploy_revision_manifest_status_check CHECK ((manifest_status = ANY (ARRAY['ok'::text, 'absent'::text, 'unreadable'::text])))
CONSTRAINT doctrine_revision_corpus_check CHECK ((corpus = ANY (ARRAY['doctrine'::text, 'dreamer'::text])))
CONSTRAINT embedding_model_is_named_check CHECK ((length(btrim(model)) > 0)),
CONSTRAINT embedding_text_is_present_check CHECK ((length(btrim(text)) > 0))
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
CONSTRAINT note_description_fits_the_index_check CHECK (((length(btrim(description)) > 0) AND (length(description) <= 200))),
CONSTRAINT note_has_a_closing_condition_check CHECK ((length(btrim(closing_condition)) > 0)),
CONSTRAINT note_has_an_anchor_check CHECK (((anchor_date IS NOT NULL) OR (cardinality(anchor_entity_ids) > 0))),
CONSTRAINT note_merge_decision_mode_is_known_check CHECK ((mode = ANY (ARRAY['shadow'::text, 'on'::text]))),
CONSTRAINT note_merge_decision_outcome_is_known_check CHECK ((outcome = ANY (ARRAY['would_merge'::text, 'merged'::text, 'stale'::text, 'too_long'::text, 'not_open'::text, 'overlap'::text, 'cross_room'::text, 'not_changed'::text]))),
CONSTRAINT note_merge_decision_reason_is_a_sentence_check CHECK ((length(reason) <= 500))
CONSTRAINT note_replacement_implies_retirement_check CHECK (((replaced_by IS NULL) OR (retired_at IS NOT NULL))),
CONSTRAINT note_revision_body_fits_the_ceiling_check CHECK ((length(body) <= 32000)),
CONSTRAINT note_revision_has_a_body_check CHECK ((length(btrim(body)) > 0)),
CONSTRAINT note_revision_instruction_sha256_check CHECK (((instruction_sha256 IS NULL) OR (instruction_sha256 ~ '^[0-9a-f]{64}$'::text))),
CONSTRAINT note_revision_model_is_named_check CHECK (((model IS NULL) OR (length(btrim(model)) > 0))),
CONSTRAINT note_revision_names_its_writer_check CHECK ((length(btrim(established_by)) > 0))
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
CONSTRAINT standing_fold_count_is_not_negative_check CHECK ((fold_count >= 0)),
CONSTRAINT standing_revision_body_fits_the_ceiling_check CHECK (((length(btrim(body)) > 0) AND (length(body) <= 4000))),
CONSTRAINT standing_revision_fold_count_is_not_negative_check CHECK ((fold_count >= 0)),
CONSTRAINT standing_revision_source_is_fold_or_operator_check CHECK ((source = ANY (ARRAY['fold'::text, 'operator'::text]))),
CONSTRAINT standing_revision_working_set_fits_the_ceiling_check CHECK (((jsonb_array_length(working_set) <= 20) AND (octet_length((working_set)::text) <= 2400)))
CONSTRAINT standing_working_set_fits_the_ceiling_check CHECK (((jsonb_array_length(working_set) <= 20) AND (octet_length((working_set)::text) <= 2400)))
CONSTRAINT tag_trace_outcome_check CHECK ((outcome = ANY (ARRAY['send_reply'::text, 'recovered'::text, 'silent'::text, 'vetoed'::text, 'timeout'::text, 'llm_error'::text])))
CONSTRAINT turn_verdict_grounded_has_no_unsupported_check CHECK ((NOT (grounded AND (cardinality(unsupported) > 0)))),
CONSTRAINT turn_verdict_unsupported_fits_the_list_check CHECK ((cardinality(unsupported) <= 10))
CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;
CREATE FUNCTION public.nora_row_history() RETURNS trigger
CREATE FUNCTION public.nora_row_history_refuse_truncate() RETURNS trigger
CREATE INDEX idx_compute_jobs_state ON public.compute_jobs USING btree (state);
CREATE INDEX idx_config_revision_file_loaded ON public.config_revision USING btree (file, loaded_at);
CREATE INDEX idx_culture_revision_key_written ON public.culture_revision USING btree (culture_key, written_at DESC, id DESC);
CREATE INDEX idx_decision_log_conv_ts ON public.decision_log USING btree (conversation_id, ts DESC, id DESC);
CREATE INDEX idx_decision_log_unit_ts ON public.decision_log USING btree (unit, ts DESC, id DESC);
CREATE INDEX idx_doctrine_revision_corpus_loaded ON public.doctrine_revision USING btree (corpus, loaded_at);
CREATE INDEX idx_embedding_scope ON public.embedding USING btree (kind, scope);
CREATE INDEX idx_embedding_v1024 ON public.embedding USING hnsw (v1024 public.vector_cosine_ops);
CREATE INDEX idx_events_conversation ON public.events USING btree (conversation_id, status);
CREATE INDEX idx_exchange_observation_scope ON public.exchange_observation USING btree (scope_id);
CREATE INDEX idx_fact_disputed ON public.fact USING btree (unit) WHERE (disputed_at IS NOT NULL);
CREATE INDEX idx_fact_premise_premise_fact_id ON public.fact_premise USING btree (premise_fact_id);
CREATE INDEX idx_fact_standing ON public.fact USING btree (unit, subject_id) WHERE (superseded_at IS NULL);
CREATE INDEX idx_instagram_summons_state ON public.instagram_summons USING btree (state, mode);
CREATE INDEX idx_language_observation_scope ON public.language_observation USING btree (scope_id);
CREATE INDEX idx_message_edit_message_ts ON public.message_edit USING btree (message_id, ts);
CREATE INDEX idx_messages_conv_platform_message_id ON public.messages USING btree (conversation_id, platform_message_id);
CREATE INDEX idx_messages_conv_ts ON public.messages USING btree (conversation_id, ts DESC);
CREATE INDEX idx_messages_dream_lag ON public.messages USING btree (conversation_id, id) WHERE (is_nora = 0);
CREATE INDEX idx_messages_is_nora ON public.messages USING btree (is_nora);
CREATE INDEX idx_note_anchor ON public.note USING btree (unit, anchor_date) WHERE (retired_at IS NULL);
CREATE INDEX idx_note_merge_decision_unit_ts ON public.note_merge_decision USING btree (unit, ts);
CREATE INDEX idx_note_revision_body_trgm ON public.note_revision USING gin (body public.gin_trgm_ops);
CREATE INDEX idx_note_revision_note_id ON public.note_revision USING btree (note_id);
CREATE INDEX idx_note_standing ON public.note USING btree (unit) WHERE (retired_at IS NULL);
CREATE INDEX idx_presence_corridors_subject ON public.presence_corridors USING btree (subject_id);
CREATE INDEX idx_presence_derived_places_subject_kind ON public.presence_derived_places USING btree (subject_id, kind);
CREATE INDEX idx_presence_member_ts ON public.presence_fixes USING btree (circle_id, person_id, ts DESC);
CREATE INDEX idx_presence_stays_subject_ts ON public.presence_stays USING btree (subject_id, started_ts DESC);
CREATE INDEX idx_presence_trips_subject_started ON public.presence_trips USING btree (subject_id, started_ts DESC);
CREATE INDEX idx_reasoning_strip_log_path_ts ON public.reasoning_strip_log USING btree (path, created_at);
CREATE INDEX idx_reminders_due ON public.reminders USING btree (state, due_at);
CREATE INDEX idx_rendered_claim_window ON public.rendered_claim USING btree (conversation_id, last_rendered_at DESC);
CREATE INDEX idx_row_history_key ON public.row_history USING btree (table_name, row_key, recorded_at);
CREATE INDEX idx_row_history_table_recorded ON public.row_history USING btree (table_name, recorded_at);
CREATE INDEX idx_standing_revision_room_updated ON public.standing_revision USING btree (platform, conversation_id, updated_at DESC, id DESC);
CREATE INDEX idx_tag_trace_conversation_created ON public.tag_trace USING btree (conversation_id, created_at);
CREATE INDEX idx_tool_call_log_conv_ts ON public.tool_call_log USING btree (conversation_id, created_at DESC);
CREATE INDEX idx_turn_verdict_ungrounded ON public.turn_verdict USING btree (conversation_id, created_at) WHERE (grounded = false);
CREATE TABLE public.alembic_version (
CREATE TABLE public.analytics_watermark (
CREATE TABLE public.artifacts (
CREATE TABLE public.compute_jobs (
CREATE TABLE public.config_revision (
CREATE TABLE public.consolidation_watermark (
CREATE TABLE public.culture (
CREATE TABLE public.culture_revision (
CREATE TABLE public.decision_log (
CREATE TABLE public.deploy_revision (
CREATE TABLE public.doctrine_revision (
CREATE TABLE public.dream_schedule_state (
CREATE TABLE public.embedding (
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
CREATE TABLE public.media_objects (
CREATE TABLE public.message_edit (
CREATE TABLE public.messages (
CREATE TABLE public.music_queue (
CREATE TABLE public.music_session (
CREATE TABLE public.note (
CREATE TABLE public.note_candidate (
CREATE TABLE public.note_entity (
CREATE TABLE public.note_merge_decision (
CREATE TABLE public.note_revision (
CREATE TABLE public.participation_window_state (
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
CREATE TABLE public.room_exchange (
CREATE TABLE public.room_lexeme (
CREATE TABLE public.room_portrait (
CREATE TABLE public.row_history (
CREATE TABLE public.standing (
CREATE TABLE public.standing_revision (
CREATE TABLE public.sticker_index (
CREATE TABLE public.tag_debounce_state (
CREATE TABLE public.tag_trace (
CREATE TABLE public.tool_call_log (
CREATE TABLE public.turn_verdict (
CREATE TABLE public.version_announcements (
CREATE TABLE public.video_readings (
CREATE TRIGGER nora_row_history AFTER INSERT OR DELETE OR UPDATE ON public.analytics_watermark FOR EACH ROW EXECUTE FUNCTION public.nora_row_history('deriver_name', 'subject_id');
CREATE TRIGGER nora_row_history AFTER INSERT OR DELETE OR UPDATE ON public.compute_jobs FOR EACH ROW EXECUTE FUNCTION public.nora_row_history('id');
CREATE TRIGGER nora_row_history AFTER INSERT OR DELETE OR UPDATE ON public.consolidation_watermark FOR EACH ROW EXECUTE FUNCTION public.nora_row_history('consolidator_name', 'scope_id');
CREATE TRIGGER nora_row_history AFTER INSERT OR DELETE OR UPDATE ON public.culture FOR EACH ROW EXECUTE FUNCTION public.nora_row_history('key');
CREATE TRIGGER nora_row_history AFTER INSERT OR DELETE OR UPDATE ON public.dream_schedule_state FOR EACH ROW EXECUTE FUNCTION public.nora_row_history('adaptor_name', 'scope_id');
CREATE TRIGGER nora_row_history AFTER INSERT OR DELETE OR UPDATE ON public.geocode_cache FOR EACH ROW EXECUTE FUNCTION public.nora_row_history('lat_rounded', 'lng_rounded');
CREATE TRIGGER nora_row_history AFTER INSERT OR DELETE OR UPDATE ON public.instagram_summons FOR EACH ROW EXECUTE FUNCTION public.nora_row_history('id');
CREATE TRIGGER nora_row_history AFTER INSERT OR DELETE OR UPDATE ON public.media_objects FOR EACH ROW EXECUTE FUNCTION public.nora_row_history('sha256');
CREATE TRIGGER nora_row_history AFTER INSERT OR DELETE OR UPDATE ON public.message_edit FOR EACH ROW EXECUTE FUNCTION public.nora_row_history('id');
CREATE TRIGGER nora_row_history AFTER INSERT OR DELETE OR UPDATE ON public.messages FOR EACH ROW EXECUTE FUNCTION public.nora_row_history('id');
CREATE TRIGGER nora_row_history AFTER INSERT OR DELETE OR UPDATE ON public.music_queue FOR EACH ROW EXECUTE FUNCTION public.nora_row_history('id');
CREATE TRIGGER nora_row_history AFTER INSERT OR DELETE OR UPDATE ON public.music_session FOR EACH ROW EXECUTE FUNCTION public.nora_row_history('guild_id');
CREATE TRIGGER nora_row_history AFTER INSERT OR DELETE OR UPDATE ON public.note FOR EACH ROW EXECUTE FUNCTION public.nora_row_history('id');
CREATE TRIGGER nora_row_history AFTER INSERT OR DELETE OR UPDATE ON public.note_candidate FOR EACH ROW EXECUTE FUNCTION public.nora_row_history('id');
CREATE TRIGGER nora_row_history AFTER INSERT OR DELETE OR UPDATE ON public.participation_window_state FOR EACH ROW EXECUTE FUNCTION public.nora_row_history('platform', 'conversation_id');
CREATE TRIGGER nora_row_history AFTER INSERT OR DELETE OR UPDATE ON public.presence_fixes FOR EACH ROW EXECUTE FUNCTION public.nora_row_history('id');
CREATE TRIGGER nora_row_history AFTER INSERT OR DELETE OR UPDATE ON public.reminders FOR EACH ROW EXECUTE FUNCTION public.nora_row_history('id');
CREATE TRIGGER nora_row_history AFTER INSERT OR DELETE OR UPDATE ON public.standing FOR EACH ROW EXECUTE FUNCTION public.nora_row_history('platform', 'conversation_id');
CREATE TRIGGER nora_row_history AFTER INSERT OR DELETE OR UPDATE ON public.sticker_index FOR EACH ROW EXECUTE FUNCTION public.nora_row_history('platform', 'fetch_token');
CREATE TRIGGER nora_row_history AFTER INSERT OR DELETE OR UPDATE ON public.tag_debounce_state FOR EACH ROW EXECUTE FUNCTION public.nora_row_history('platform', 'conversation_id');
CREATE TRIGGER nora_row_history AFTER INSERT OR DELETE OR UPDATE ON public.version_announcements FOR EACH ROW EXECUTE FUNCTION public.nora_row_history('version', 'platform', 'conversation_id');
CREATE TRIGGER nora_row_history AFTER INSERT OR DELETE OR UPDATE ON public.video_readings FOR EACH ROW EXECUTE FUNCTION public.nora_row_history('sha256', 'provider', 'model', 'chunk_seconds', 'chunk_max', 'reading_digest');
CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON public.analytics_watermark FOR EACH STATEMENT EXECUTE FUNCTION public.nora_row_history_refuse_truncate();
CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON public.compute_jobs FOR EACH STATEMENT EXECUTE FUNCTION public.nora_row_history_refuse_truncate();
CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON public.consolidation_watermark FOR EACH STATEMENT EXECUTE FUNCTION public.nora_row_history_refuse_truncate();
CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON public.culture FOR EACH STATEMENT EXECUTE FUNCTION public.nora_row_history_refuse_truncate();
CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON public.dream_schedule_state FOR EACH STATEMENT EXECUTE FUNCTION public.nora_row_history_refuse_truncate();
CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON public.geocode_cache FOR EACH STATEMENT EXECUTE FUNCTION public.nora_row_history_refuse_truncate();
CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON public.instagram_summons FOR EACH STATEMENT EXECUTE FUNCTION public.nora_row_history_refuse_truncate();
CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON public.media_objects FOR EACH STATEMENT EXECUTE FUNCTION public.nora_row_history_refuse_truncate();
CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON public.message_edit FOR EACH STATEMENT EXECUTE FUNCTION public.nora_row_history_refuse_truncate();
CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON public.messages FOR EACH STATEMENT EXECUTE FUNCTION public.nora_row_history_refuse_truncate();
CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON public.music_queue FOR EACH STATEMENT EXECUTE FUNCTION public.nora_row_history_refuse_truncate();
CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON public.music_session FOR EACH STATEMENT EXECUTE FUNCTION public.nora_row_history_refuse_truncate();
CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON public.note FOR EACH STATEMENT EXECUTE FUNCTION public.nora_row_history_refuse_truncate();
CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON public.note_candidate FOR EACH STATEMENT EXECUTE FUNCTION public.nora_row_history_refuse_truncate();
CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON public.participation_window_state FOR EACH STATEMENT EXECUTE FUNCTION public.nora_row_history_refuse_truncate();
CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON public.presence_fixes FOR EACH STATEMENT EXECUTE FUNCTION public.nora_row_history_refuse_truncate();
CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON public.reminders FOR EACH STATEMENT EXECUTE FUNCTION public.nora_row_history_refuse_truncate();
CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON public.standing FOR EACH STATEMENT EXECUTE FUNCTION public.nora_row_history_refuse_truncate();
CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON public.sticker_index FOR EACH STATEMENT EXECUTE FUNCTION public.nora_row_history_refuse_truncate();
CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON public.tag_debounce_state FOR EACH STATEMENT EXECUTE FUNCTION public.nora_row_history_refuse_truncate();
CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON public.version_announcements FOR EACH STATEMENT EXECUTE FUNCTION public.nora_row_history_refuse_truncate();
CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON public.video_readings FOR EACH STATEMENT EXECUTE FUNCTION public.nora_row_history_refuse_truncate();
CREATE UNIQUE INDEX note_candidate_create_anchor_key ON public.note_candidate USING btree (unit, anchor_date, anchor_entity_ids, lower(btrim(title))) WHERE (kind = 'create'::text);
CREATE UNIQUE INDEX uq_reminders_proactive_open_note ON public.reminders USING btree (((payload ->> 'note_id'::text))) WHERE ((kind = 'proactive'::text) AND (state = ANY (ARRAY['pending'::text, 'firing'::text])));
CREATE UNIQUE INDEX uq_row_history_open ON public.row_history USING btree (table_name, row_key) WHERE (superseded_at IS NULL);
DECLARE
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
END LOOP;
FOREACH col IN ARRAY TG_ARGV LOOP
IF TG_OP <> 'DELETE' THEN
IF TG_OP <> 'INSERT' THEN
IF TG_OP = 'DELETE' OR (TG_OP = 'UPDATE' AND new_key <> old_key) THEN
IF TG_OP = 'DELETE' THEN
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
INSERT INTO row_history (id, table_name, row_key, op, image, recorded_at)
INSERT INTO row_history (id, table_name, row_key, op, image, recorded_at)
LANGUAGE plpgsql
LANGUAGE plpgsql
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
RAISE EXCEPTION USING MESSAGE = 'TRUNCATE ' || TG_TABLE_NAME
RETURN NULL;
RETURN NULL;
RETURN NULL;
RETURN NULL;
RETURN NULL;
SEQUENCE NAME public.config_revision_id_seq
SEQUENCE NAME public.deploy_revision_id_seq
SEQUENCE NAME public.doctrine_revision_id_seq
SEQUENCE NAME public.exchange_observation_id_seq
SEQUENCE NAME public.language_observation_id_seq
SEQUENCE NAME public.message_edit_id_seq
SEQUENCE NAME public.messages_id_seq
SEQUENCE NAME public.presence_corridors_id_seq
SEQUENCE NAME public.presence_derived_places_id_seq
SEQUENCE NAME public.presence_fixes_id_seq
SEQUENCE NAME public.presence_occupancy_id_seq
SEQUENCE NAME public.presence_stays_id_seq
SEQUENCE NAME public.presence_trips_id_seq
SEQUENCE NAME public.row_history_id_seq
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
WHERE table_name = TG_TABLE_NAME AND row_key = new_key
WHERE table_name = TG_TABLE_NAME AND row_key = old_key
accuracy_m double precision,
act text NOT NULL,
act text NOT NULL,
adaptor_name text NOT NULL,
added_at timestamp with time zone NOT NULL,
added_by_person_id text NOT NULL,
address text
address text NOT NULL,
address text,
aliases text[] DEFAULT '{}'::text[] NOT NULL,
anchor_date integer,
anchor_date integer,
anchor_entity_ids text[] DEFAULT '{}'::text[] NOT NULL,
anchor_entity_ids text[] DEFAULT '{}'::text[] NOT NULL,
announced_at timestamp with time zone NOT NULL,
artifact_id text,
assembled_sha256 text,
asserted_at bigint NOT NULL,
asserted_at bigint,
asserted_at bigint,
asserted_by text,
asserted_by text,
at timestamptz := clock_timestamp();
attempts integer DEFAULT 0 NOT NULL,
attempts integer DEFAULT 0 NOT NULL,
author_igid text NOT NULL,
avg_speed_kmh double precision NOT NULL,
backend_job_id text NOT NULL,
battery double precision,
body text NOT NULL,
body text NOT NULL,
body text NOT NULL,
body text NOT NULL,
body text,
body text,
body text,
bridge_code text,
built_at text,
canonical_form text NOT NULL,
canonical_form text NOT NULL,
canonical_name text NOT NULL,
chunk_max integer NOT NULL,
chunk_seconds integer NOT NULL,
circle_id text NOT NULL,
circle_id text NOT NULL,
circle_id text NOT NULL,
circle_id text NOT NULL,
circle_id text NOT NULL,
closing_condition text NOT NULL,
closing_condition text,
col text;
comment_id text,
completed_at timestamp with time zone,
confidence double precision NOT NULL,
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
created_at text DEFAULT (now())::text NOT NULL,
created_at text DEFAULT (now())::text NOT NULL,
created_at timestamp with time zone DEFAULT now() NOT NULL
created_at timestamp with time zone DEFAULT now() NOT NULL
created_at timestamp with time zone DEFAULT now() NOT NULL,
created_at timestamp with time zone DEFAULT now() NOT NULL,
created_at timestamp with time zone DEFAULT now() NOT NULL,
created_at timestamp with time zone NOT NULL,
created_at timestamp with time zone NOT NULL,
created_at_ms bigint NOT NULL,
created_by text NOT NULL,
culture_key text NOT NULL,
delivery_status text DEFAULT 'pending'::text NOT NULL,
deriver_name text NOT NULL,
describe_model text,
description text DEFAULT ''::text NOT NULL,
description text NOT NULL,
description text,
disputed_at bigint,
disputed_at bigint,
disputed_by text,
disputed_by text,
disputed_reason text,
disputed_reason text,
disputed_tag_id text,
disputed_tag_id text,
distance_m double precision NOT NULL,
distinct_speakers integer DEFAULT 0 NOT NULL,
due_at timestamp with time zone NOT NULL,
duplicate_id uuid NOT NULL,
duplicate_title text NOT NULL,
duration_mad_ms bigint,
duration_median_ms bigint,
dwell_ms bigint DEFAULT 0 NOT NULL,
embedded_at bigint NOT NULL,
ended_ts bigint NOT NULL,
ended_ts bigint NOT NULL,
entity_id uuid NOT NULL
error text,
established_by text NOT NULL,
established_by text NOT NULL,
established_by text NOT NULL,
established_by text NOT NULL,
established_by text NOT NULL,
established_in text NOT NULL,
event_id uuid NOT NULL,
event_id uuid,
examples jsonb DEFAULT '[]'::jsonb NOT NULL,
examples jsonb DEFAULT '[]'::jsonb NOT NULL,
exchange_basis text[] DEFAULT '{}'::text[] NOT NULL,
expires_at bigint
extractor_confidence double precision NOT NULL,
extractor_confidence double precision NOT NULL,
fact_id uuid NOT NULL,
fact_id uuid NOT NULL,
fetch_token text NOT NULL,
fidelity text DEFAULT 'tentative'::text NOT NULL,
file text NOT NULL,
files jsonb NOT NULL,
fingerprint text NOT NULL,
fired_at timestamp with time zone,
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
git_sha text,
grounded boolean NOT NULL,
guild_id text NOT NULL,
guild_id text NOT NULL,
head_revision_id uuid NOT NULL,
head_revision_id uuid NOT NULL,
hits integer DEFAULT 0 NOT NULL
hostname text DEFAULT ''::text NOT NULL,
hostname text DEFAULT ''::text NOT NULL,
hostname text DEFAULT ''::text NOT NULL,
hour_of_week integer NOT NULL,
hour_of_week integer NOT NULL,
id bigint NOT NULL,
id bigint NOT NULL,
id bigint NOT NULL,
id bigint NOT NULL,
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
is_driving integer,
is_nora integer DEFAULT 0 NOT NULL,
key text NOT NULL,
kind text DEFAULT 'miss'::text NOT NULL,
kind text DEFAULT 'reminder'::text NOT NULL,
kind text NOT NULL,
kind text NOT NULL,
kind text NOT NULL,
label text,
last_adjudicated_at bigint,
last_allowed_at bigint NOT NULL,
last_cluster_id integer DEFAULT 0 NOT NULL,
last_fired_at bigint NOT NULL,
last_fix_id integer DEFAULT 0 NOT NULL,
last_folded_row_id bigint,
last_folded_row_id bigint,
last_message_id integer DEFAULT 0 NOT NULL,
last_played_locator text,
last_played_source text,
last_played_title text,
last_rendered_at bigint NOT NULL,
last_seen timestamp with time zone DEFAULT now() NOT NULL,
last_seen_at bigint NOT NULL,
last_seen_at bigint NOT NULL,
last_seen_at bigint NOT NULL,
last_seen_at bigint,
last_seen_ts bigint NOT NULL
last_seen_ts bigint NOT NULL,
last_send_at bigint,
last_tag_id text NOT NULL,
last_wake_revision_id uuid,
last_window_end_at bigint NOT NULL,
lat double precision NOT NULL,
lat double precision NOT NULL,
lat double precision NOT NULL,
lat double precision NOT NULL,
lat_rounded double precision NOT NULL,
lexeme_basis text[] DEFAULT '{}'::text[] NOT NULL,
lng double precision NOT NULL,
lng double precision NOT NULL,
lng double precision NOT NULL,
lng double precision NOT NULL,
lng_rounded double precision NOT NULL,
loaded_at timestamp with time zone NOT NULL,
loaded_at timestamp with time zone NOT NULL,
manifest_status text NOT NULL,
mean_arrival_minute double precision,
mean_departure_minute double precision,
media_id text NOT NULL,
media_json text,
media_type text DEFAULT ''::text NOT NULL,
media_type text DEFAULT ''::text NOT NULL,
media_type text NOT NULL,
mention_at timestamp with time zone,
mentions_json text,
mentions_json text,
message_id integer NOT NULL,
message_id integer NOT NULL,
message_id integer NOT NULL,
message_id text NOT NULL,
message_ids integer[] DEFAULT '{}'::integer[] NOT NULL,
message_ids integer[] NOT NULL,
messages jsonb NOT NULL,
mode text NOT NULL,
mode text NOT NULL,
model text NOT NULL,
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
model_calls integer NOT NULL,
name text NOT NULL,
narrative text NOT NULL,
new_image := to_jsonb(NEW);
new_image jsonb;
new_key := new_key || jsonb_build_object(col, new_image -> col);
new_key jsonb := '{}'::jsonb;
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
observed_at bigint NOT NULL,
observed_at bigint NOT NULL,
observed_in text[] DEFAULT '{}'::text[] NOT NULL,
observed_in text[] DEFAULT '{}'::text[] NOT NULL,
occurrence integer DEFAULT 0 NOT NULL,
occurrence_count integer DEFAULT 0 NOT NULL,
occurrence_count integer DEFAULT 0 NOT NULL,
occurrence_count integer DEFAULT 1 NOT NULL,
occurrence_count integer DEFAULT 1 NOT NULL,
old_image := to_jsonb(OLD);
old_image jsonb;
old_key := old_key || jsonb_build_object(col, old_image -> col);
old_key jsonb := '{}'::jsonb;
op text NOT NULL,
outcome text NOT NULL,
outcome text NOT NULL,
outcome text,
outcome text,
p50_kmh double precision NOT NULL,
p90_kmh double precision NOT NULL,
parent_revision_id uuid,
parent_revision_id uuid,
part_of_speech text,
participants text[] NOT NULL,
participants text[] NOT NULL,
path text NOT NULL,
path text NOT NULL,
paused boolean NOT NULL,
payload jsonb,
person_id text NOT NULL,
person_id text NOT NULL,
person_id text NOT NULL,
person_id text NOT NULL,
person_id text NOT NULL,
person_id text,
person_id text,
person_id text,
place_id integer
place_id integer NOT NULL,
place_id integer NOT NULL,
place_id text NOT NULL,
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
platform_message_id text,
predicate text NOT NULL,
predicate text NOT NULL,
premise_fact_id uuid NOT NULL,
probe text,
provenance jsonb DEFAULT '{}'::jsonb NOT NULL,
provider text NOT NULL,
quoted_ts bigint,
radius_m double precision NOT NULL,
radius_m double precision NOT NULL,
radius_m double precision,
raw_json text,
raw_json text,
reading text NOT NULL,
reading_digest text NOT NULL,
reason text DEFAULT ''::text NOT NULL,
reason text DEFAULT ''::text NOT NULL,
reasoning_effort text NOT NULL,
recognition_score double precision DEFAULT 0 NOT NULL,
recognition_score double precision DEFAULT 0 NOT NULL,
recorded_at bigint NOT NULL,
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
recorded_at timestamp with time zone NOT NULL,
recurrence text DEFAULT 'none'::text NOT NULL,
recurrence_count integer,
recurrence_interval integer DEFAULT 1 NOT NULL,
recurrence_tz text,
recurrence_until timestamp with time zone,
reminder_id uuid,
reminder_id uuid,
replaced_by uuid,
reply_comment_id text,
reply_text text,
requester_person_id text NOT NULL,
requester_person_id text NOT NULL,
resolved_at timestamp with time zone DEFAULT now() NOT NULL
responded_at timestamp with time zone NOT NULL,
response text NOT NULL,
result_summary text,
result_url text,
retire_reason text,
retired_at bigint,
revisit_at bigint,
row_key jsonb NOT NULL,
row_key text NOT NULL,
row_key text NOT NULL,
sample_count integer DEFAULT 0 NOT NULL,
sample_count integer DEFAULT 0 NOT NULL,
sample_count integer NOT NULL,
saved_place_id text,
scope text NOT NULL,
scope_id text NOT NULL,
scope_id text NOT NULL,
scope_id text NOT NULL,
scope_id text NOT NULL,
scope_id text NOT NULL,
scope_id text NOT NULL,
scope_id text NOT NULL,
seen_at timestamp with time zone DEFAULT now() NOT NULL,
sender_id text NOT NULL,
sender_name text,
sense text NOT NULL,
sense text NOT NULL,
sequence jsonb
sequence jsonb NOT NULL,
series_anchor_at timestamp with time zone,
series_id uuid,
sha256 text NOT NULL,
sha256 text NOT NULL,
sha256 text NOT NULL,
sha256 text NOT NULL,
shortcode text NOT NULL,
size bigint DEFAULT '0'::bigint NOT NULL,
size integer DEFAULT 0 NOT NULL,
size_bytes bigint NOT NULL,
slug text NOT NULL,
source text DEFAULT 'fold'::text NOT NULL,
source text NOT NULL,
source text NOT NULL,
source_excerpt text,
source_url text,
speed_kmh double precision
started_at timestamp with time zone NOT NULL,
started_ts bigint NOT NULL,
started_ts bigint NOT NULL,
state text DEFAULT 'live'::text NOT NULL,
state text DEFAULT 'live'::text NOT NULL,
state text DEFAULT 'pending'::text NOT NULL,
state text NOT NULL,
state text NOT NULL,
state text NOT NULL,
status text NOT NULL,
status text NOT NULL,
status text,
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
supersede_reason text,
supersede_source text,
superseded_at bigint,
superseded_at timestamp with time zone,
superseded_by bigint,
superseded_by uuid,
surface text NOT NULL,
surface_form text NOT NULL,
survivor_id uuid NOT NULL,
survivor_title text NOT NULL,
synthesized_at bigint NOT NULL
table_name text NOT NULL,
tag_id text NOT NULL,
tag_id text NOT NULL,
tag_id text NOT NULL,
tag_id text,
task text NOT NULL,
task_sha256 text DEFAULT ''::text NOT NULL,
text text NOT NULL,
text text NOT NULL,
title text DEFAULT ''::text NOT NULL,
title text NOT NULL,
title text NOT NULL,
title text NOT NULL,
title text NOT NULL,
title text,
tool_input jsonb NOT NULL,
tool_name text NOT NULL,
tool_result jsonb NOT NULL,
trace_id text,
transcript jsonb NOT NULL,
ts bigint NOT NULL,
ts bigint NOT NULL,
ts bigint NOT NULL,
ts bigint NOT NULL,
ts bigint NOT NULL,
uncertainty text NOT NULL,
unit text DEFAULT ''::text NOT NULL,
unit text NOT NULL,
unit text NOT NULL,
unit text NOT NULL,
unit text NOT NULL,
unit text NOT NULL,
unit text NOT NULL,
unit text,
unsupported text[] DEFAULT '{}'::text[] NOT NULL,
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
updated_ms bigint NOT NULL,
updated_ts bigint NOT NULL
updated_ts bigint NOT NULL,
updated_ts bigint NOT NULL,
url text NOT NULL,
v1024 public.vector(1024),
valid_from bigint NOT NULL,
valid_to bigint,
venue_address text,
venue_maps_url text,
venue_name text,
venue_place_id text,
venue_price_level text DEFAULT ''::text NOT NULL,
venue_rating double precision,
venue_resolved_at timestamp with time zone,
venue_text text,
verdict text NOT NULL,
version text NOT NULL,
version text,
version_id := nextval('row_history_id_seq');
version_id := nextval('row_history_id_seq');
version_id bigint;
version_num character varying(32) NOT NULL
voice_channel_id text NOT NULL,
volume double precision NOT NULL,
warrant_kind text DEFAULT 'source'::text NOT NULL,
wire_state text NOT NULL,
working_set jsonb DEFAULT '[]'::jsonb NOT NULL,
working_set jsonb DEFAULT '[]'::jsonb NOT NULL,
writer text NOT NULL,
written_at bigint NOT NULL,
written_at bigint NOT NULL,
|| ' would bypass row_history; delete the rows (they are kept as history) '
|| 'or SET LOCAL nora.row_history = ''off'' deliberately';

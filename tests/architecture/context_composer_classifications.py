"""Semantic owners and the shrink-only inventory of parallel composers."""

COMPOSERS = {
    "message": (
        "nora.domain.blocks.render_messages",
        "nora.domain.blocks.render_row_window",
        "nora.domain.blocks.render_rows",
    ),
    "media": (
        "nora.domain.media.render_placeholder",
        "nora.domain.media.placeholder_suffix",
        "nora.domain.media.body_with_handles",
        "nora.domain.media.enrich_placeholders",
    ),
    "graph": ("nora.language.comprehend_render.preview_text",),
    "profile": (
        "nora.language.profile_render._body",
        "nora.language.profile_render.account_text",
        "nora.language.profile_render.render_profile",
        "nora.language.profile_render.render_profile_people",
    ),
    "note": ("nora.language.note_render.render_note_rows",),
    "memo": ("nora.language.memo_render.render_memo_rows", "nora.language.memo_render.memo_rows"),
    "event": (
        "nora.language.event_render.render_event_rows",
        "nora.language.event_render.event_when",
    ),
    "social": (
        "nora.infra.social.render.render",
        "nora.infra.social.render._reading_line",
        "nora.infra.social.render._profile",
        "nora.infra.social.render._search",
        "nora.infra.social.render._post",
        "nora.infra.social.render._story",
        "nora.infra.social.render._posts",
    ),
    "memory": (
        "nora.agent.memory_search.render_packet",
        "nora.agent.prefill._recalled_block",
        "nora.language.comprehend_render.memory_result_text",
    ),
    "standing": ("nora.language.shared_render.render_shared_state",),
    "culture": (
        "nora.language.culture_render.render_culture",
        "nora.language.culture_render.render_unit_culture",
        "nora.language.culture_render.render_self",
        "nora.language.culture_render.render_character",
    ),
    "presence": ("nora.language.presence_render.render_presence",),
    "document": ("nora.language.documents_render.render_documents",),
    "computer": ("nora.language.computer_render.render_computer",),
    "subagent": (
        "nora.language.subagents_render.render_subagents",
        "nora.language.subagent_render.render_brief",
        "nora.language.checkpoint_render.render_checkpoint",
    ),
    "lesson": ("nora.language.lesson_render.render_lessons",),
    "decision": ("nora.domain.decision_log.render_decisions",),
    "catalogue": ("nora.skills.loader.SkillStore.render_available",),
    "clock": ("nora.language.note_render.today_block",),
    "room_members": ("nora.agent.prefill._room_members_block",),
    "debrief": (
        "nora.engagement.debrief_render._turn_block",
        "nora.engagement.debrief_render._standing_prior",
        "nora.engagement.debrief_render._standing_working_set_prior",
        "nora.engagement.debrief_render._standing_ledger_prior",
    ),
    "bullet": ("nora.domain.blocks.render_bullets",),
    "source_event": ("nora.agent.prefill._transcript_block._event",),
    "transit": ("nora.infra.transit.arrivals.describe",),
    "law": ("nora.language.doctrine_check.render_check_input",),
    "gif_catalogue": ("nora.agent.reaction.ReactionPicker._pick",),
    "room_description": ("nora.language.culture.CultureAdaptor._room_block",),
    "bridge_payload": ("nora.infra.social.bridge_client._render_text",),
}

KNOWN = {
    "nora.agent.prefill:_context_header": ("clock", "gap 4: ContextHeader clock variant"),
    "nora.agent.prefill:_standing_block": ("source_object", "gap 1: retained objects"),
    "nora.agent.reaction:render_recent": ("message", "V10: recent-chat loop"),
    "nora.agent.tools:_source_event_item": ("source_object", "gap 1: observation JSON"),
    "nora.agent.tools:_target_item": ("message", "V8: independent target projection; gap 1"),
    "nora.agent.tools:_transcript_messages": (
        "message",
        "V8: independent tool transcript projection",
    ),
    "nora.agent.tools:build_tool_specs.rollup_read": (
        "rollup",
        "gap 2: cross-surface rollup projection",
    ),
    "nora.agent.tools:build_tool_specs.character_read": (
        "profile_account",
        "gap 6: legacy account tool branch",
    ),
    "nora.agent.tools:build_tool_specs.event_search": ("event", "V11: independent event JSON"),
    "nora.agent.tools:build_tool_specs.note_read": ("note", "gap 5: full notebook read"),
    "nora.agent.tools:build_tool_specs.note_search": ("note", "gap 5: notebook search"),
    "nora.app.cognition_memory:WakeMemory.propose": ("graph", "V7: connected asdict(graph)"),
    "nora.app.extraction:ExtractionWorker._complete": ("message/media", "V4: raw snapshot JSON"),
    "nora.app.profile_spike:event_judge": ("message/profile", "V5: evidence JSON"),
    "nora.domain.summons:render_summons": (
        "social/message/media",
        "V3: comment loop and clipped readings",
    ),
    "nora.language.character:room_cultures_block": (
        "profile_account",
        "gap 6: legacy account dossier",
    ),
    "nora.language.character:_social_block": ("social", "gap 7: cached caption projection"),
    "nora.language.character:CharacterPersonAdaptor.inputs": (
        "profile_account",
        "V12: previous.body; gaps 3/6: editable lines and episodes",
    ),
    "nora.language.character:CharacterUnitAdaptor.inputs": (
        "profile_account",
        "V12: previous.body; gaps 3/6: editable lines and episodes",
    ),
    "nora.language.character:CharacterNoraAdaptor.inputs": (
        "profile_account",
        "V12: previous.body; gaps 3/6: editable lines and episodes",
    ),
    "nora.language.cognition_render:render_agenda": (
        "clock/graph",
        "gap 4: clock heading; graph delegated to preview_text",
    ),
    "nora.language.cognition_render:_item_blocks": (
        "message/media",
        "gaps 1/7: item source observations",
    ),
    "nora.language.cognition_render:_register": (
        "source_object/rollup/social",
        "gaps 1/2/7: independent register projections",
    ),
    "nora.language.comprehend_render:render_comprehend": (
        "message/media",
        "V2: snapshot plus source text",
    ),
    "nora.language.comprehend_render:render_reshape": (
        "graph",
        "V6: raw graph/evidence/source JSON",
    ),
    "nora.language.culture:CultureAdaptor.inputs": (
        "profile_account",
        "V12: previous.body; gaps 3/6: editable lines and episodes",
    ),
    "nora.language.lessons:LessonsAdaptor.inputs": (
        "profile_edit_dossier",
        "gap 6: episode dossier",
    ),
    "nora.language.profile:ProfilePersonAdaptor.inputs": (
        "profile_account",
        "V12: previous.body; gaps 3/6: editable lines and episodes",
    ),
    "nora.language.profile_collective:ProfileCollectiveAdaptor.inputs.source_block": (
        "profile_edit_dossier",
        "gap 3: full source revision JSON",
    ),
    "nora.language.profile_collective:ProfileCollectiveAdaptor.inputs": (
        "profile_account",
        "V12: previous.body; gaps 3/6: editable lines and episodes",
    ),
    "nora.language.profile_verdict:ProfileVerdictAdaptor.inputs": (
        "profile_edit_dossier",
        "gap 3: editable prediction dossier",
    ),
    "nora.subject.context_selector_render:selector_prompt": (
        "message/computer/document/subagent",
        "V9: slice and re-escape composed body",
    ),
}

"""Closed semantic source certificates for test_one_context_composer.

Audit references: context-composer-audit, 2026-11-04, source 0b78f00.
V1-V12 and gap 1-7 refer to its confirmed violations and ownership gaps.
Hashes bind reviewed source, never authorize arbitrary code at a path.
"""

CLASSIFIED = {
    "nora/agent/memory_search.py::render_packet": (
        "memory_packet",
        "nora/agent/memory_search.py::render_packet",
        "032f2bb7b25251cb4bc9",
    ),
    "nora/agent/prefill.py::<module>": (
        "message/media/memory_packet/instruction",
        "nora/agent/prefill.py::<module>",
        "f644f5a03fd0c79c5638",
    ),
    "nora/agent/prefill.py::_block": (
        "message/media/memory_packet/instruction",
        "nora/agent/prefill.py::_block",
        "f156703b9cde71dee2ba",
    ),
    "nora/agent/prefill.py::_transcript_block": (
        "message/media/memory_packet/instruction",
        "nora/agent/prefill.py::_transcript_block",
        "990c3b16d0c5b64d7aea",
    ),
    "nora/agent/prefill.py::_peer_card_block": (
        "message/media/memory_packet/instruction",
        "nora/agent/prefill.py::_peer_card_block",
        "df7925b62ce3f7bbd48b",
    ),
    "nora/agent/prefill.py::_peer_history_block": (
        "message/media/memory_packet/instruction",
        "nora/agent/prefill.py::_peer_history_block",
        "80b712fa54dd0ae40e21",
    ),
    "nora/agent/prefill.py::_room_members_block": (
        "message/media/memory_packet/instruction",
        "nora/agent/prefill.py::_room_members_block",
        "91dbad72a89d8217ed09",
    ),
    "nora/agent/prefill.py::_people_in_window_block": (
        "message/media/memory_packet/instruction",
        "nora/agent/prefill.py::_people_in_window_block",
        "c59b99f15a5e8920f793",
    ),
    "nora/agent/prefill.py::_recalled_block": (
        "message/media/memory_packet/instruction",
        "nora/agent/prefill.py::_recalled_block",
        "7a4dbee9384169f051dc",
    ),
    "nora/agent/prefill.py::_spoken_turn_block": (
        "message/media/memory_packet/instruction",
        "nora/agent/prefill.py::_spoken_turn_block",
        "5b043c6ef31feaac1dae",
    ),
    "nora/agent/prefill.py::_declared": (
        "message/media/memory_packet/instruction",
        "nora/agent/prefill.py::_declared",
        "5fb13376a865bd762876",
    ),
    "nora/agent/prefill.py::build_tag_message": (
        "message/media/memory_packet/instruction",
        "nora/agent/prefill.py::build_tag_message",
        "325d10ac133ffaeb1de3",
    ),
    "nora/agent/prefill.py::_replying_to_block": (
        "message/media/memory_packet/instruction",
        "nora/agent/prefill.py::_replying_to_block",
        "698ae928adebc1fdcebb",
    ),
    "nora/agent/prefill.py::build_prefill": (
        "message/media/memory_packet/instruction",
        "nora/agent/prefill.py::build_prefill",
        "c3bdfa948fa2f6b7d0b0",
    ),
    "nora/agent/reaction.py::<module>": (
        "message/instruction/catalogue",
        "nora/agent/reaction.py::<module>",
        "babef58ca324361359a3",
    ),
    "nora/agent/reaction.py::ReactionPicker._pick": (
        "message/instruction/catalogue",
        "nora/agent/reaction.py::ReactionPicker._pick",
        "08e3315ff12746f561a9",
    ),
    "nora/agent/speech_format.py::<module>": (
        "outside_text",
        "nora/agent/speech_format.py::<module>",
        "48bf3c8946593c2e0828",
    ),
    "nora/agent/speech_format.py::render_input": (
        "outside_text",
        "nora/agent/speech_format.py::render_input",
        "de3193d6cb6fa6465f5f",
    ),
    "nora/agent/tools.py::build_tool_specs.memo_read": (
        "external_page/memo",
        "nora/agent/tools.py::build_tool_specs.memo_read",
        "7e32a2a7b1c560cd9522",
    ),
    "nora/agent/tools.py::build_tool_specs.file_fetch": (
        "external_page/memo",
        "nora/agent/tools.py::build_tool_specs.file_fetch",
        "1c1181f0c15373ca63df",
    ),
    "nora/app/extraction.py::<module>": (
        "message/media",
        "nora/app/extraction.py::<module>",
        "a02cb735a970bd04daa1",
    ),
    "nora/app/profile_spike.py::<module>": (
        "profile/message",
        "nora/app/profile_spike.py::<module>",
        "ff997c77551e9173ff1e",
    ),
    "nora/domain/blocks.py::render_rows": (
        "transport_envelope",
        "nora/domain/blocks.py::render_rows",
        "97bc5abaf9890105baf4",
    ),
    "nora/domain/blocks.py::render_messages": (
        "transport_envelope",
        "nora/domain/blocks.py::render_messages",
        "4adf8034c95c3368f569",
    ),
    "nora/domain/blocks.py::render_row_window": (
        "transport_envelope",
        "nora/domain/blocks.py::render_row_window",
        "ab0e9410866ef1fc3f31",
    ),
    "nora/domain/blocks.py::wrap_outside_text": (
        "transport_envelope",
        "nora/domain/blocks.py::wrap_outside_text",
        "1c86f02f76e08a273080",
    ),
    "nora/domain/decision_log.py::render_decisions": (
        "decision",
        "nora/domain/decision_log.py::render_decisions",
        "f17e2ea94b59ca26ca6c",
    ),
    "nora/domain/media.py::enrich_placeholders": (
        "media",
        "nora/domain/media.py::render_placeholder",
        "915835e7bc3ccd23eb49",
    ),
    "nora/domain/media.py::render_placeholder": (
        "media",
        "nora/domain/media.py::render_placeholder",
        "cb113e36034d3b50aa90",
    ),
    "nora/domain/media.py::placeholder_suffix": (
        "media",
        "nora/domain/media.py::render_placeholder",
        "2fa9416f4c74658283c4",
    ),
    "nora/domain/media.py::body_with_handles": (
        "media",
        "nora/domain/media.py::placeholder_suffix",
        "b640a1990dc7c8ba6cd4",
    ),
    "nora/domain/summons.py::<module>": (
        "social",
        "nora/domain/summons.py::<module>",
        "f2fb2ee1e9f884d46745",
    ),
    "nora/domain/summons.py::_social_post": (
        "social",
        "nora/domain/summons.py::_social_post",
        "faed41201ed7d6efdb16",
    ),
    "nora/engagement/debrief_render.py::<module>": (
        "message/standing/trace",
        "nora/engagement/debrief_render.py::<module>",
        "c0397a168f2cf9c57f28",
    ),
    "nora/engagement/debrief_render.py::_turn_block": (
        "message/standing/trace",
        "nora/engagement/debrief_render.py::_turn_block",
        "a7ae1c38e161af2587d7",
    ),
    "nora/engagement/debrief_render.py::render_debrief_input": (
        "message/standing/trace",
        "nora/engagement/debrief_render.py::render_debrief_input",
        "49cf17be4b76af8c89ba",
    ),
    "nora/engagement/debrief_render.py::_standing_prior": (
        "message/standing/trace",
        "nora/engagement/debrief_render.py::_standing_prior",
        "f4760b39b87106969457",
    ),
    "nora/engagement/debrief_render.py::_standing_working_set_prior": (
        "message/standing/trace",
        "nora/engagement/debrief_render.py::_standing_working_set_prior",
        "dab769d950f722b0c563",
    ),
    "nora/engagement/debrief_render.py::_standing_ledger_prior": (
        "message/standing/trace",
        "nora/engagement/debrief_render.py::_standing_ledger_prior",
        "292dc5263d6e7bc129bc",
    ),
    "nora/infra/social/bridge_client.py::OpenCliBridgeClient._read": (
        "social",
        "nora/infra/social/bridge_client.py::OpenCliBridgeClient._read",
        "f3f975639ae81bd5ee0e",
    ),
    "nora/infra/social/render.py::render": (
        "social",
        "nora/infra/social/render.py::render",
        "8761d9b12083f4e80d80",
    ),
    "nora/infra/social/render.py::_reading_line": (
        "social",
        "nora/infra/social/render.py::_reading_line",
        "f9ed65e0fed3a02399c6",
    ),
    "nora/infra/social/render.py::_profile": (
        "social",
        "nora/infra/social/render.py::_profile",
        "2f4352d34b96f4d8fab8",
    ),
    "nora/infra/social/render.py::_search": (
        "social",
        "nora/infra/social/render.py::_search",
        "6617f9d897552e15be54",
    ),
    "nora/infra/social/render.py::_post": (
        "social",
        "nora/infra/social/render.py::_post",
        "300ab912e27284a79a49",
    ),
    "nora/infra/social/render.py::_story": (
        "social",
        "nora/infra/social/render.py::_story",
        "c292b729399fc1f0b4c5",
    ),
    "nora/infra/social/render.py::_posts": (
        "social",
        "nora/infra/social/render.py::_posts",
        "677aac4984e3eb002f94",
    ),
    "nora/infra/transit/arrivals.py::describe": (
        "transit",
        "nora/infra/transit/arrivals.py::describe",
        "7679d825bb9bc02770ab",
    ),
    "nora/infra/vision/comprehend.py::MediaComprehender.summarize": (
        "media",
        "nora/infra/vision/comprehend.py::MediaComprehender.summarize",
        "a75016b9bfb5f05185c0",
    ),
    "nora/ingest/relevance_gate.py::<module>": (
        "message/standing",
        "nora/ingest/relevance_gate.py::<module>",
        "9b91a2d074d0542b0ac6",
    ),
    "nora/ingest/relevance_gate.py::render_context": (
        "message/standing",
        "nora/domain/blocks.py::render_messages",
        "65942ebd47fb381bf1ca",
    ),
    "nora/language/character.py::<module>": (
        "profile_account",
        "nora/language/character.py::<module>",
        "f8eb65e20d7065f19396",
    ),
    "nora/language/checkpoint_render.py::render_checkpoint": (
        "checkpoint",
        "nora/language/checkpoint_render.py::render_checkpoint",
        "121ea07333d9fca0a9a1",
    ),
    "nora/language/cognition_render.py::<module>": (
        "composition_of_typed_parts",
        "nora/language/cognition_render.py::<module>",
        "f837acd285ef2c29297d",
    ),
    "nora/language/comprehend_render.py::<module>": (
        "graph/instruction",
        "nora/language/comprehend_render.py::<module>",
        "4aea5a76968d5863c5bd",
    ),
    "nora/language/comprehend_render.py::preview_text": (
        "graph/instruction",
        "nora/language/comprehend_render.py::preview_text",
        "155f759b884434bc62f7",
    ),
    "nora/language/comprehend_render.py::memory_result_text": (
        "graph/instruction",
        "nora/language/comprehend_render.py::memory_result_text",
        "48c3f349bb6f26112ef5",
    ),
    "nora/language/comprehend_render.py::render_session_system": (
        "graph/instruction",
        "nora/language/comprehend_render.py::render_session_system",
        "160939075c0e91b43162",
    ),
    "nora/language/computer_render.py::render_computer": (
        "computer",
        "nora/language/computer_render.py::render_computer",
        "d3799e61a90f0cf52c56",
    ),
    "nora/language/confirmation_render.py::<module>": (
        "instruction/recorded_prompt",
        "nora/language/confirmation_render.py::<module>",
        "7db353cfd547c15dabe9",
    ),
    "nora/language/confirmation_render.py::confirmation_prompts": (
        "instruction/recorded_prompt",
        "nora/language/confirmation_render.py::confirmation_prompts",
        "5b0e5f8be236754ec9ff",
    ),
    "nora/language/culture.py::<module>": (
        "profile_account/message",
        "nora/language/culture.py::<module>",
        "6da35867b36832704e5a",
    ),
    "nora/language/culture.py::CultureAdaptor._room_block": (
        "profile_account/message",
        "nora/language/culture.py::CultureAdaptor._room_block",
        "66983d5755faf22b3c63",
    ),
    "nora/language/culture_render.py::render_culture": (
        "profile_account",
        "nora/language/culture_render.py::render_culture",
        "3baf0cc071842396485c",
    ),
    "nora/language/culture_render.py::render_unit_culture": (
        "profile_account",
        "nora/language/culture_render.py::render_unit_culture",
        "70768b06890a7025cab2",
    ),
    "nora/language/culture_render.py::render_self": (
        "profile_account",
        "nora/language/culture_render.py::render_self",
        "384f5e3ca8e16d8860e9",
    ),
    "nora/language/culture_render.py::render_character": (
        "profile_account",
        "nora/language/culture_render.py::render_character",
        "a0b459e9d31e2bd324c9",
    ),
    "nora/language/culture_render.py::render_character.row": (
        "profile_account",
        "nora/language/culture_render.py::render_character.row",
        "0fd6c076d73c6d1a3f96",
    ),
    "nora/language/doctrine_check.py::<module>": (
        "instruction",
        "nora/language/doctrine_check.py::<module>",
        "717c8d223bf46345ad5c",
    ),
    "nora/language/doctrine_check.py::render_check_input": (
        "instruction",
        "nora/language/doctrine_check.py::render_check_input",
        "1447a005a43eb3164c5c",
    ),
    "nora/language/documents_render.py::render_documents": (
        "document",
        "nora/language/documents_render.py::render_documents",
        "9ad905e9499516fc3137",
    ),
    "nora/language/event_render.py::event_when": (
        "event",
        "nora/language/event_render.py::event_when",
        "3afc4314484a389152f8",
    ),
    "nora/language/event_render.py::render_event_rows": (
        "event",
        "nora/language/event_render.py::event_when",
        "f0c119e27c1e81367b7e",
    ),
    "nora/language/lesson_render.py::render_lessons": (
        "instruction",
        "nora/language/lesson_render.py::render_lessons",
        "c85d7e8d763a75fbc5d4",
    ),
    "nora/language/lessons.py::<module>": (
        "profile_edit_dossier/message",
        "nora/language/lessons.py::<module>",
        "4fa7cee453013dea2e9f",
    ),
    "nora/language/memo_render.py::render_memo_rows": (
        "memo",
        "nora/language/memo_render.py::render_memo_rows",
        "c67116ae8f174dd42efa",
    ),
    "nora/language/memo_render.py::memo_rows": (
        "memo",
        "nora/language/memo_render.py::render_memo_rows",
        "2f1b69e0c0694351d0aa",
    ),
    "nora/language/memo_render.py::render_memos": (
        "memo",
        "nora/language/memo_render.py::memo_rows",
        "18c5bc9181b45032d849",
    ),
    "nora/language/note_render.py::render_note_rows": (
        "note/clock",
        "nora/language/note_render.py::render_note_rows",
        "060089f72e5c6e19c0f9",
    ),
    "nora/language/note_render.py::render_notes": (
        "note/clock",
        "nora/language/note_render.py::render_note_rows",
        "a6fbfd8b7cd3c17e88de",
    ),
    "nora/language/note_render.py::today_block": (
        "note/clock",
        "nora/language/note_render.py::today_block",
        "f4fd817bad112016eaf2",
    ),
    "nora/language/presence_render.py::render_presence": (
        "presence",
        "nora/language/presence_render.py::render_presence",
        "f5a62a3c4336354af4c0",
    ),
    "nora/language/profile.py::<module>": (
        "profile_account/message",
        "nora/language/profile.py::<module>",
        "0a85bd87e7e2f30e79a5",
    ),
    "nora/language/profile_collective.py::<module>": (
        "profile_account/message",
        "nora/language/profile_collective.py::<module>",
        "7eb442f479ea5d29b23d",
    ),
    "nora/language/profile_collective.py::ProfileCollectiveAdaptor.inputs.message_block": (
        "profile_account/message",
        "nora/domain/blocks.py::render_row_window",
        "e69d261bd27094d76ad6",
    ),
    "nora/language/profile_render.py::_body": (
        "profile_account",
        "nora/language/profile_render.py::_body",
        "53d95f968a2adc98f526",
    ),
    "nora/language/profile_render.py::account_text": (
        "profile_account",
        "nora/language/profile_render.py::_body",
        "664a7205e1c868e6813f",
    ),
    "nora/language/profile_render.py::render_profile": (
        "profile_account",
        "nora/language/profile_render.py::_body",
        "8f1e185f1929c9d19612",
    ),
    "nora/language/profile_render.py::render_profile_people": (
        "profile_account",
        "nora/language/profile_render.py::_body",
        "bb2f00d6d4cfbc1eedf1",
    ),
    "nora/language/profile_verdict.py::<module>": (
        "profile_edit_dossier",
        "nora/language/profile_verdict.py::<module>",
        "adb53d7c5b07d49a241b",
    ),
    "nora/language/shared_render.py::render_shared_state": (
        "standing",
        "nora/language/shared_render.py::render_shared_state",
        "1f3b38c2a3d01d14d4be",
    ),
    "nora/language/subagent_render.py::<module>": (
        "subagent/instruction",
        "nora/language/subagent_render.py::<module>",
        "9216da53dae5ee3567e1",
    ),
    "nora/language/subagent_render.py::render_brief": (
        "subagent/instruction",
        "nora/language/subagent_render.py::render_brief",
        "245d12491ec164abf054",
    ),
    "nora/language/subagents_render.py::render_subagents": (
        "subagent",
        "nora/language/subagents_render.py::render_subagents",
        "fd8e411f2c9d059eec22",
    ),
    "nora/skills/loader.py::SkillStore.render_available": (
        "catalogue",
        "nora/skills/loader.py::SkillStore.render_available",
        "5737a100b6ad5379f7d8",
    ),
    "nora/subject/context_filter.py::exposure.filtered": (
        "composition_of_typed_parts",
        "nora/subject/context_filter.py::exposure.filtered",
        "87a9118942a11e80c676",
    ),
    "nora/subject/context_selector_render.py::<module>": (
        "composition_of_typed_parts",
        "nora/subject/context_selector_render.py::<module>",
        "74f6993fd6343718c70e",
    ),
}

KNOWN = {
    "nora/agent/prefill.py::_context_header": (
        "clock",
        "gap 4: ContextHeader clock variant",
        "5ad3ec99bcaf51bd06d3",
    ),
    "nora/agent/prefill.py::_standing_block": (
        "source_object",
        "gap 1: retained objects",
        "988e9029930d06864d97",
    ),
    "nora/agent/reaction.py::render_recent": (
        "message",
        "V10: recent-chat loop",
        "c7a9aef5d07b64d5b6fe",
    ),
    "nora/agent/tools.py::_source_event_item": (
        "source_object",
        "gap 1: observation JSON",
        "2fc888d5624073b329e9",
    ),
    "nora/agent/tools.py::_target_item": (
        "message",
        "V8: independent target projection; gap 1",
        "377a1a0c9e732b48b65a",
    ),
    "nora/agent/tools.py::_transcript_messages": (
        "message",
        "V8: independent tool transcript projection",
        "17b4e5ea7811b030c9b5",
    ),
    "nora/agent/tools.py::build_tool_specs.rollup_read": (
        "rollup",
        "gap 2: cross-surface rollup projection",
        "f33d3016ec3aec7d8b37",
    ),
    "nora/agent/tools.py::build_tool_specs.character_read": (
        "profile_account",
        "gap 6: legacy account tool branch",
        "07c2f68bc064d3aee684",
    ),
    "nora/agent/tools.py::build_tool_specs.event_search": (
        "event",
        "V11: independent event JSON",
        "9c9caacb7e060227f028",
    ),
    "nora/agent/tools.py::build_tool_specs.note_read": (
        "note",
        "gap 5: full notebook read",
        "d53c35fca2f219219ed4",
    ),
    "nora/agent/tools.py::build_tool_specs.note_search": (
        "note",
        "gap 5: notebook search",
        "08c4bf61c34e09accb9a",
    ),
    "nora/app/cognition_memory.py::WakeMemory.propose": (
        "graph",
        "V7: connected asdict(graph)",
        "e8d7287ed0fb603ec855",
    ),
    "nora/app/cognition_memory.py::WakeMemory.prepare.reading_sources": (
        "message/media",
        "V1: clipped reading_sources beside preview_text",
        "fa15fb8e4e108cb0dc81",
    ),
    "nora/app/extraction.py::ExtractionWorker._complete": (
        "message/media",
        "V4: raw snapshot JSON",
        "d144704f21eee939dc08",
    ),
    "nora/app/profile_spike.py::event_judge": (
        "message/profile",
        "V5: evidence JSON",
        "3cc93cef16ef90258763",
    ),
    "nora/domain/summons.py::render_summons": (
        "social/message/media",
        "V3: comment loop and clipped readings",
        "b823167404bcdc40794c",
    ),
    "nora/language/character.py::room_cultures_block": (
        "profile_account",
        "gap 6: legacy account dossier",
        "eef33338797c9ae3733d",
    ),
    "nora/language/character.py::_social_block": (
        "social",
        "gap 7: cached caption projection",
        "b97ac58319a135d8fa94",
    ),
    "nora/language/character.py::CharacterPersonAdaptor.inputs": (
        "profile_account",
        "V12: previous.body; gaps 3/6: editable lines and episodes",
        "f14605e3209dd7acaa8f",
    ),
    "nora/language/character.py::CharacterUnitAdaptor.inputs": (
        "profile_account",
        "V12: previous.body; gaps 3/6: editable lines and episodes",
        "9a850e084aeb36814199",
    ),
    "nora/language/character.py::CharacterNoraAdaptor.inputs": (
        "profile_account",
        "V12: previous.body; gaps 3/6: editable lines and episodes",
        "a736ecef6a7416a2812d",
    ),
    "nora/language/cognition_render.py::render_agenda": (
        "clock/graph",
        "gap 4: clock heading; graph delegated to preview_text",
        "06741b7967f04af9c4cf",
    ),
    "nora/language/cognition_render.py::_item_blocks": (
        "message/media",
        "gaps 1/7: item source observations",
        "f31e71a3d1618fe97d7b",
    ),
    "nora/language/cognition_render.py::_register": (
        "source_object/rollup/social",
        "gaps 1/2/7: independent register projections",
        "1176784ca127a972c923",
    ),
    "nora/language/comprehend_render.py::render_comprehend": (
        "message/media",
        "V2: snapshot plus source text",
        "748a6b68c48755521159",
    ),
    "nora/language/comprehend_render.py::render_reshape": (
        "graph",
        "V6: raw graph/evidence/source JSON",
        "b46a7c7d6bf9271f75cc",
    ),
    "nora/language/culture.py::CultureAdaptor.inputs": (
        "profile_account",
        "V12: previous.body; gaps 3/6: editable lines and episodes",
        "11d27f5acf37d415ad34",
    ),
    "nora/language/lessons.py::LessonsAdaptor.inputs": (
        "profile_edit_dossier",
        "gap 6: episode dossier",
        "c68d926d48b2374c2ad6",
    ),
    "nora/language/profile.py::ProfilePersonAdaptor.inputs": (
        "profile_account",
        "V12: previous.body; gaps 3/6: editable lines and episodes",
        "908aff06ad7f3d005029",
    ),
    "nora/language/profile_collective.py::ProfileCollectiveAdaptor.inputs.source_block": (
        "profile_edit_dossier",
        "gap 3: full source revision JSON",
        "35fd4e9dc9009f2580f9",
    ),
    "nora/language/profile_collective.py::ProfileCollectiveAdaptor.inputs": (
        "profile_account",
        "V12: previous.body; gaps 3/6: editable lines and episodes",
        "de5448629950f9906188",
    ),
    "nora/language/profile_verdict.py::ProfileVerdictAdaptor.inputs": (
        "profile_edit_dossier",
        "gap 3: editable prediction dossier",
        "df5074111ae82695fb90",
    ),
    "nora/subject/context_selector_render.py::selector_prompt": (
        "message/computer/document/subagent",
        "V9: slice and re-escape composed body",
        "87eb941ecbf53cdd64d3",
    ),
}

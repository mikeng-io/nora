"""Typed context-body boundary; registration belongs beside assembly, not at callers."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from types import FrameType

COMPOSERS = frozenset(
    {
        "nora.language.register_render:render_register",
        "nora.language.cognition_render:_item_blocks",
        "nora.language.cognition_render:_lesson_evidence_blocks",
        "nora.language.cognition_render:render_agenda",
        "nora.language.context_render:render_clock",
        "nora.language.context_render:render_reads",
        "nora.language.catalogue_render:render_tool_catalogue",
        "nora.agent.prefill:_declared",
        "nora.agent.prefill:_peer_card_block",
        "nora.agent.prefill:_peer_history_block",
        "nora.agent.prefill:_people_in_window_block",
        "nora.agent.prefill:_recalled_block",
        "nora.agent.prefill:_spoken_turn_block",
        "nora.agent.prefill:_transcript_block",
        "nora.agent.reaction:ReactionPicker._pick",
        "nora.domain.blocks:wrap_outside_text",
        "nora.domain.blocks:assemble_declared",
        "nora.domain.decision_log:render_decisions",
        "nora.domain.summons:_social_post",
        "nora.engagement.debrief_render:_standing_ledger_prior",
        "nora.engagement.debrief_render:_standing_prior",
        "nora.engagement.debrief_render:_standing_working_set_prior",
        "nora.engagement.debrief_render:_turn_block",
        "nora.language.browse_render:render_browse_brief",
        "nora.language.checkpoint_render:render_checkpoint",
        "nora.language.comprehend_render:memory_result_text",
        "nora.language.comprehend_render:render_session_system",
        "nora.language.computer_render:render_computer",
        "nora.language.culture:CultureAdaptor._room_block",
        "nora.language.culture_render:render_character",
        "nora.language.culture_render:render_character.row",
        "nora.language.culture_render:render_culture",
        "nora.language.culture_render:render_self",
        "nora.language.culture_render:render_unit_culture",
        "nora.language.doctrine_check:render_check_input",
        "nora.language.documents_render:render_documents",
        "nora.language.lesson_render:render_lessons",
        "nora.language.memo_render:render_memos",
        "nora.language.note_render:render_notes",
        "nora.language.context_render:render_members",
        "nora.language.context_render:render_rooms",
        "nora.language.context_render:render_transcript",
        "nora.language.note_render:today_block",
        "nora.language.presence_render:render_presence",
        "nora.language.profile_collective:ProfileCollectiveAdaptor.inputs.message_block",
        "nora.language.profile_render:render_profile",
        "nora.language.profile_render:render_profile_people",
        "nora.language.shared_render:render_shared_state",
        "nora.language.subagent_render:render_brief",
        "nora.language.subagents_render:render_subagents",
        "nora.skills.loader:SkillStore.render_available",
        "nora.testing.context_blocks:compose_fixture",
        "scripts.memory_graph_spike:prompt",
    }
)

LEGACY_COMPOSERS = frozenset(
    {
        "nora.agent.prefill:_context_header",
        "nora.agent.prefill:_replying_to_block",
        "nora.agent.prefill:_standing_block",
        "nora.agent.prefill:build_tag_message",
        "nora.agent.speech_format:render_input",
        "nora.app.profile_spike:event_judge",
        "nora.ingest.relevance_gate:render_context",
        "nora.language.character:CharacterNoraAdaptor.inputs",
        "nora.language.character:CharacterPersonAdaptor.inputs",
        "nora.language.character:CharacterUnitAdaptor.inputs",
        "nora.language.character:_social_block",
        "nora.language.character:room_cultures_block",
        "nora.language.confirmation_render:confirmation_prompts",
        "nora.language.culture:CultureAdaptor.inputs",
        "nora.language.profile:ProfilePersonAdaptor.inputs",
        "nora.language.profile_collective:ProfileCollectiveAdaptor.inputs",
        "nora.language.profile_collective:ProfileCollectiveAdaptor.inputs.source_block",
        "nora.language.profile_verdict:ProfileVerdictAdaptor.inputs",
        "nora.subject.context_selector_render:selector_prompt",
    }
)

_CONSTRUCTION_KEY = object()


@dataclass(frozen=True, init=False)
class Composed:
    """Body bytes with their registered composer; raw strings cannot impersonate one."""

    text: str
    composer: str

    def __init__(self, text: str, composer: str, *, _key: object) -> None:
        if _key is not _CONSTRUCTION_KEY:
            raise TypeError("Composed is constructed only by registered composers")
        if not isinstance(text, str):
            raise TypeError("composed text must be str")
        object.__setattr__(self, "text", text)
        object.__setattr__(self, "composer", composer)

    def __bool__(self) -> bool:
        return bool(self.text)

    def __str__(self) -> str:
        return self.text


def _caller(frame: FrameType) -> str:
    qualname = frame.f_code.co_qualname.replace(".<locals>", "")
    for suffix in (".<genexpr>", ".<listcomp>", ".<dictcomp>", ".<setcomp>"):
        qualname = qualname.removesuffix(suffix)
    module = str(frame.f_globals["__name__"])
    if module == "__main__":
        try:
            script = Path(frame.f_code.co_filename).resolve().relative_to(
                Path(__file__).resolve().parents[2]
            )
        except ValueError:
            pass
        else:
            module = ".".join(script.with_suffix("").parts)
    return module + ":" + qualname


def compose_body(text: str) -> Composed:
    """Finish a body in its registered owner; callers cannot choose a composer name."""
    owner = _caller(sys._getframe(1))
    if owner not in COMPOSERS:
        raise TypeError(f"unregistered context composer: {owner}")
    return Composed(text, owner, _key=_CONSTRUCTION_KEY)


def legacy_composed(text: str) -> Composed:
    """Temporary raw-body escape, restricted to the frozen cleanup backlog."""
    owner = _caller(sys._getframe(1))
    if owner not in LEGACY_COMPOSERS:
        raise TypeError(f"unlisted legacy context composer: {owner}")
    return Composed(text, "legacy:" + owner, _key=_CONSTRUCTION_KEY)

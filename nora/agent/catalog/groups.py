from __future__ import annotations

from collections.abc import Mapping

from nora.agent.catalog.entries import NATIVE_TOOLS
from nora.agent.catalog.model import TOOL_OCCASIONS
from nora.language.culture import CULTURE_ADAPTOR_NAME

NATIVE_TOOL_BUDGETS: Mapping[str, int] = {name: spec.budget for name, spec in NATIVE_TOOLS.items()}

HOT_TOOLS: frozenset[str] = frozenset(
    {
        "web_search",
        "send_reply",
        "reaction_send",
        "note_read",
        "web_fetch",
        "now",
        "search_memory",
        "memory_search",
    }
)


def hot_tools(occasion: str | None = None) -> frozenset[str]:
    """The tools resident on a turn of `occasion` under deferral (DEC-256).

    `HOT_TOOLS` on every turn, plus every tool whose `hot_on` names the
    occasion. `None` (a text turn) is `HOT_TOOLS` and nothing else, so a text
    turn's surface does not change when a tool declares an occasion. An
    occasion outside `TOOL_OCCASIONS` raises rather than reading as «no
    extras»: a misspelt occasion would otherwise ship the text set to a voice
    turn and report nothing.
    """
    if occasion is None:
        return HOT_TOOLS
    if occasion not in TOOL_OCCASIONS:
        raise ValueError(f"unknown tool occasion {occasion!r}; known: {sorted(TOOL_OCCASIONS)}")
    return HOT_TOOLS | frozenset(
        name for name, spec in NATIVE_TOOLS.items() if occasion in spec.hot_on
    )


LOADER_TOOL = "load_tools"

LOAD_TRANSCRIPT_MAX = 60

LOAD_TRANSCRIPT_DEFAULT = 25

COMPUTER_TOOLS: frozenset[str] = frozenset(
    {
        "computer_run",
        "computer_read",
        "computer_write",
        "computer_edit",
        "computer_export",
        "computer_import",
        "computer_session_list",
        "computer_browse",
        "computer_capture",
        "computer_cancel",
    }
)

SUBAGENT_TOOLS: frozenset[str] = frozenset(
    {
        "subagent_create",
        "subagent_list",
        "subagent_read",
        "subagent_cancel",
    }
)

VIDEO_TOOLS: frozenset[str] = frozenset({"video_create"})

DOCUMENT_TOOLS: frozenset[str] = frozenset(
    {"document_publish", "document_read", "document_set_status"}
)

HONCHO_MEMORY_TOOLS: frozenset[str] = frozenset(
    {
        "ask_memory",
        "search_memory",
        "search_memory_workspace",
        "who_said",
        "about",
    }
)

GRAPH_MEMORY_TOOLS: frozenset[str] = frozenset({"memory_search", "memory_read", "profile_dispute"})

ROOM_ONLY_MEMORY_ADAPTORS: frozenset[str] = frozenset({CULTURE_ADAPTOR_NAME})

"""Context types for graph wake evidence."""

from nora.domain.block_types import BlockType, _t

GRAPH_TYPES: tuple[BlockType, ...] = (
    _t(
        "lesson_evidence",
        "Intention verdicts and possible corrections from the changes this wake claimed. "
        "Code records each verdict at closure with its ending and any witness. "
        "Corrections are other people's messages that addressed you, offered as evidence. "
        "An empty result held no visible evidence from these changes.",
        store="cognitive_graph.lesson_evidence",
        kind="a verdict or an addressed message cue",
        time="when the change became eligible for a wake",
    ),
)

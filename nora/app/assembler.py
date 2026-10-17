"""`build_assembler` — the ONE construction site for `TranscriptInjection`.

Both `nora.app.composition.build_dependencies` (production) and
`nora.app.offline.build_offline_pipeline` (every offline/eval harness) call
this instead of constructing `TranscriptInjection(...)` themselves, so the two
cannot drift apart. `tests/architecture/test_assembler_parity.py` pins both to
this one function: a collaborator added to one side and forgotten on the other
fails a test.

Lives beside `offline.py` rather than inside `composition.py` for the same
reason `eval_support.py` does: `offline.py` already imports from `nora.app`,
so a shared helper in the same package needs no new edge in the import graph.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from nora.agent.lineage import LineagePolicy
from nora.agent.memory_search import MemorySearch
from nora.agent.whereis import circles_from_persons
from nora.config import Config
from nora.domain.media_join import MediaJoin
from nora.domain.memo import MemoStore
from nora.domain.ports import (
    CultureReader,
    MemoryStore,
    RecordStore,
    StandingStore,
)
from nora.domain.quote_window import QuoteWindow
from nora.engagement.computer_context import ComputerContextReader
from nora.engagement.context import (
    StandingNotesReader,
    TranscriptInjection,
)
from nora.engagement.documents_context import DocumentsContextReader
from nora.engagement.self_presence import PresenceReader
from nora.engagement.subagents_context import SubagentsContextReader
from nora.language.lesson_render import ActiveLessons
from nora.obs.metrics import Metrics
from nora.persons import Persons
from nora.policy import Policy


def lineage_policy(cfg: Config, *, policy: Policy, persons: Persons) -> LineagePolicy:
    """The ONE `LineagePolicy` of a process: every room of `policy.toml`, the location circles of
    `identity.toml` (the same `circles_from_persons` `where_is` reads) and
    `[lineage] mode`. A pure value; the tag path's assembler, the fire path
    and the tool registry (`record_query`) each hold one built here, so the
    three cannot disagree about who a room is."""
    return LineagePolicy.from_policy(
        policy, circles=circles_from_persons(persons), mode=cfg.lineage.mode,
    )


def build_assembler(
    cfg: Config,
    *,
    audit: RecordStore,
    memory: MemoryStore,
    persons: Persons,
    metrics: Metrics,
    policy: Policy,
    notes: StandingNotesReader | None = None,
    standing: StandingStore | None = None,
    media_join: MediaJoin | None = None,
    quote_window: QuoteWindow | None = None,
    presence: PresenceReader | None = None,
    culture: CultureReader | None = None,
    lessons: ActiveLessons | None = None,
    computer: ComputerContextReader | None = None,
    documents: DocumentsContextReader | None = None,
    subagents: SubagentsContextReader | None = None,
    memos: MemoStore | None = None,
    memory_search: MemorySearch | None = None,
    clock: Callable[[], datetime] | None = None,
) -> TranscriptInjection:
    return TranscriptInjection(
        audit=audit, memory=memory, persons=persons,
        context_window=cfg.store.context_window, metrics=metrics,
        prompt_window=cfg.store.effective_prompt_window,
        # SHARED (DEC-195) with `nora.app.fire.FirePath`, not built here from
        # `cfg.store.quote_margin`/`.quote_window_multiplier` — those two
        # config values are read ONCE, at the composition root, into the ONE
        # `QuoteWindow` instance both paths hold. `None` (a harness that has
        # not opted in) is the plain `context_window` fetch, no quote-aware
        # growth.
        quote_window=quote_window,
        ai_peer=cfg.memory.ai_peer,
        tagger_card_conclusions=cfg.memory.tagger_card_conclusions,
        notes=notes, policy=policy,
        memos=memos,
        standing=standing,
        # The media join: the transcript's `[image a3f2c1]`
        # placeholders gain their stored comprehension here, in the assembler,
        # so `nora/agent/prefill.py` stays a pure renderer over `TagContext`.
        # `None` (every harness that wires no media index) renders the bare
        # handles, which is what the row actually says. The SAME instance is
        # handed to the five other transcript builders.
        media_join=media_join,
        # `<presence>`. The SAME reader the fire path and the proactive act
        # turn hold, built once at the composition root over `policy.toml` and
        # the live adapter map. `None` renders the network alone.
        presence=presence,
        # `<culture>` (DEC-207). The SAME reader the fire
        # path and every Dreamer adaptor that reads an account hold. `None`
        # while `[culture] enabled = false`, and in every harness that wires
        # no store, which renders no block.
        culture=culture,
        # `<lessons>`: `None` while `[lessons]
        # render` is off, and in every harness that does not wire it.
        lessons=lessons,
        # Lineage. Built here, from the
        # same policy and identity every harness already passes, so an eval
        # turn is filtered exactly as a live one in every `[lineage] mode`.
        lineage=lineage_policy(cfg, policy=policy, persons=persons),
        # `<computer>`. The SAME reader the
        # fire path holds; `None` while `[computer]` is off, and in every
        # harness, which renders no block.
        computer=computer,
        # `<documents>`. The SAME reader the fire path holds;
        # `None` while `[documents]` is off, which renders no block.
        documents=documents,
        # `<subagents>`. TAG PATH ONLY: the fire path is given no such reader.
        # `None` while `[subagent]` is off, which renders no block.
        subagents=subagents,
        # `<recalled>`, the pre-fetch (design/unified-memory/00 step 3b): the
        # SAME `MemorySearch` the tool registry's `memory_search` holds.
        # `None` on the Honcho surface (`[memory_index] enabled = false`),
        # which renders no block.
        memory_search=memory_search,
        # The graph's one clock, so the pre-fetch dates and ranks its items
        # at the instant every other component reads.
        clock=clock,
    )

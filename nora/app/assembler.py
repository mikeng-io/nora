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

from collections.abc import Callable, Mapping, Set
from datetime import datetime

from nora.agent.lineage import LineagePolicy
from nora.agent.whereis import circles_from_persons
from nora.config import Config
from nora.domain.live import LiveSequence
from nora.domain.media_join import MediaJoin
from nora.domain.ports import (
    RecordStore,
    RosterSource,
)
from nora.domain.quote_window import QuoteWindow
from nora.engagement.computer_context import ComputerContextReader
from nora.engagement.context import (
    TranscriptInjection,
)
from nora.engagement.documents_context import DocumentsContextReader
from nora.engagement.self_presence import PresenceReader
from nora.engagement.subagents_context import SubagentsContextReader
from nora.obs.metrics import Metrics
from nora.persons import Persons
from nora.policy import Policy


def lineage_policy(
    cfg: Config, *, policy: Policy, persons: Persons, access: object = None,
) -> LineagePolicy:
    """Shared lineage policy with file topology and selected current identity facts."""
    return LineagePolicy.from_policy(
        policy, circles=LiveSequence(lambda: circles_from_persons(persons)), mode=cfg.lineage.mode,
        content_checks=cfg.content_checks.enabled, access=access,
    )


def build_assembler(
    cfg: Config,
    *,
    audit: RecordStore,
    persons: Persons,
    metrics: Metrics,
    policy: Policy,

    media_join: MediaJoin | None = None,
    quote_window: QuoteWindow | None = None,
    presence: PresenceReader | None = None,
    computer: ComputerContextReader | None = None,
    documents: DocumentsContextReader | None = None,
    subagents: SubagentsContextReader | None = None,
    clock: Callable[[], datetime] | None = None,
    rosters: Mapping[str, RosterSource] | None = None,
    self_ids: Set[str] = frozenset(),
    observations: object = None,
    access: object = None,
) -> TranscriptInjection:
    return TranscriptInjection(
        audit=audit, persons=persons,
        context_window=cfg.store.context_window, metrics=metrics,
        prompt_window=cfg.store.effective_prompt_window,
        # SHARED (DEC-195) with `nora.app.fire.FirePath`, not built here from
        # `cfg.store.quote_margin`/`.quote_window_multiplier` — those two
        # config values are read ONCE, at the composition root, into the ONE
        # `QuoteWindow` instance both paths hold. `None` (a harness that has
        # not opted in) is the plain `context_window` fetch, no quote-aware
        # growth.
        quote_window=quote_window,
        ai_peer=cfg.agent.name, display_name=cfg.doctrine.display_name,
        policy=policy,
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
        # Lineage. Built here, from the
        # same policy and identity every harness already passes, so an eval
        # turn is filtered exactly as a live one in every `[lineage] mode`.
        lineage=lineage_policy(cfg, policy=policy, persons=persons, access=access),
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
        # The graph's one clock, so the pre-fetch dates and ranks its items
        # at the instant every other component reads.
        clock=clock,
        rosters=rosters, self_ids=self_ids,
        # The typed platform-observation Record: the room's source events
        # (transcript) and folded object heads (standing slot). `None` in
        # a harness renders neither — never a guessed source line.
        observations=observations,
        # The selected backend's identity+access facade (`nora.access`).
        # `None` builds a file-mode facade over `persons`/`policy`.
        access=access,
    )

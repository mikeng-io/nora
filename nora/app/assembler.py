"""`build_assembler` — the ONE construction site for `TranscriptInjection`.

Both `nora.app.composition.build_dependencies` (production) and
`nora.app.offline.build_offline_pipeline` (every offline/eval harness) call
this instead of constructing `TranscriptInjection(...)` themselves. Before
this module existed the two sites drifted independently — production passed
18 keyword arguments, `offline.py` passed 7, and an eval's prompt silently
missing `<standing>`/`<notes>` went unnoticed because nothing
compared the two call sites. `tests/architecture/test_assembler_parity.py`
pins both to this one function, so a collaborator added to one side and
forgotten on the other fails a test instead of shipping quietly.

Lives beside `offline.py` rather than inside `composition.py` for the same
reason `eval_support.py` does: `offline.py` already imports from `nora.app`,
so a shared helper in the same package needs no new edge in the import graph.
"""

from __future__ import annotations

from nora.config import Config
from nora.domain.media_join import MediaJoin
from nora.domain.ports import (
    MemoryStore,
    RecordStore,
    StandingStore,
)
from nora.domain.quote_window import QuoteWindow
from nora.engagement.context import (
    StandingNotesReader,
    TranscriptInjection,
)
from nora.obs.metrics import Metrics
from nora.persons import Persons
from nora.policy import Policy


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
) -> TranscriptInjection:
    return TranscriptInjection(
        audit=audit, memory=memory, persons=persons,
        context_window=cfg.store.context_window, metrics=metrics,
        # SHARED (DEC-195) with `nora.app.fire.FirePath`, not built here from
        # `cfg.store.quote_margin`/`.quote_window_multiplier` — those two
        # config values are read ONCE, at the composition root, into the ONE
        # `QuoteWindow` instance both paths hold. `None` (a harness that
        # predates this, or has not opted in) is the pre-2026-09-22 shape:
        # the plain `context_window` fetch, no quote-aware growth.
        quote_window=quote_window,
        ai_peer=cfg.memory.ai_peer,
        self_card_conclusions=cfg.memory.self_card_conclusions,
        tagger_card_conclusions=cfg.memory.tagger_card_conclusions,
        notes=notes, policy=policy,
        standing=standing,
        # The media join (issue #116): the transcript's `[image a3f2c1]`
        # placeholders gain their stored comprehension here, in the assembler,
        # so `nora/agent/prefill.py` stays a pure renderer over `TagContext`.
        # `None` (every harness that wires no media index) renders the bare
        # handles, which is what the row actually says. The SAME instance is
        # handed to the five other transcript builders.
        media_join=media_join,
    )

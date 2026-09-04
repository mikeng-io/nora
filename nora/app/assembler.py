"""`build_assembler` — the ONE construction site for `TranscriptInjection`.

Both `nora.app.composition.build_dependencies` (production) and
`nora.app.offline.build_offline_pipeline` (every offline/eval harness) call
this instead of constructing `TranscriptInjection(...)` themselves. Before
this module existed the two sites drifted independently — production passed
18 keyword arguments, `offline.py` passed 7, and an eval's prompt silently
missing `<standing>`/`<notes>`/`<facts>` went unnoticed because nothing
compared the two call sites. `tests/architecture/test_assembler_parity.py`
pins both to this one function, so a collaborator added to one side and
forgotten on the other fails a test instead of shipping quietly.

Lives beside `offline.py` rather than inside `composition.py` for the same
reason `eval_support.py` does: `offline.py` already imports from `nora.app`,
so a shared helper in the same package needs no new edge in the import graph.
"""

from __future__ import annotations

from nora.config import Config
from nora.domain.ports import (
    FactReader,
    MemoryStore,
    RecordStore,
    RoomExchangeReader,
    RoomLanguageReader,
    RoomPortraitReader,
    StandingStore,
)
from nora.engagement.context import (
    RenderedClaimWriter,
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
    language: RoomLanguageReader | None = None,
    exchange: RoomExchangeReader | None = None,
    portrait: RoomPortraitReader | None = None,
    notes: StandingNotesReader | None = None,
    facts: FactReader | None = None,
    rendered: RenderedClaimWriter | None = None,
    standing: StandingStore | None = None,
) -> TranscriptInjection:
    return TranscriptInjection(
        audit=audit, memory=memory, persons=persons,
        context_window=cfg.store.context_window, metrics=metrics,
        ai_peer=cfg.memory.ai_peer,
        self_card_conclusions=cfg.memory.self_card_conclusions,
        tagger_card_conclusions=cfg.memory.tagger_card_conclusions,
        language=language, exchange=exchange, portrait=portrait,
        notes=notes, policy=policy, facts=facts, rendered=rendered,
        max_room_facts=cfg.facts.max_room_facts,
        max_sender_facts=cfg.facts.max_sender_facts,
        standing=standing,
    )

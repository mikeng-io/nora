"""The one Nora, obtained — never assembled.

`design/evaluation/` in one sentence: **different evaluation suites, different
input, ONE Nora.** This package is that one. It exists because seven places in
the eval layer each composed their own, none of them was
`nora.app.composition.build_dependencies`, and four of the seven measured
something the deployment does not send (`design/evaluation/03-step0-results.md`).

**The rule this package enforces by having no other entry point:** a caller
states what is NOT Nora — where the store is, which doctrine, what happens to a
send — and gets the composed graph back. It does not pass collaborators, because
there is no parameter for one. Anything a caller could hand in is something that
could differ between two callers, and that is what produced the seven.

WHY IT IS A LAYER AND NOT A DIRECTORY (`design/evaluation/01-boundaries.md` §6,
operator 2026-09-17). The layer contract had no slot for a thing that obtains the
real graph and is NOT the application: every slot either forbids reaching the top
or IS the top. `nora.subject` is that slot, above `nora.app`, so:

- it may reach `nora.app` (downward, legal);
- `nora.app` may NOT reach it — `pyproject.toml`'s layers contract refuses the
  reverse, which is the property that stops the application learning it is being
  measured.

WHAT THIS IS NOT. Not a fake, not a mirror, not a "test double of the graph".
`nora/app/offline.py` was the mirror and its own docstring claimed it *"mirrors
`build_dependencies`'s pipeline-wiring tail exactly"* — a claim nothing held, and
the parity test that exists compares `composition.py` to `offline.py` (both
`FirePath(36)`) while never reading `eval_support.py`'s `FirePath(21)`.
"""

from __future__ import annotations

from .dreaming import Pass, adaptor_names, dream
from .knowledge import (
    Knowledge,
    Message,
    NoteSeed,
    ReminderSeed,
    RoomSeed,
    Written,
    arrive,
    deliver,
    heard,
    know,
    last_row_id,
    pending,
    settle,
    silent_failures,
)
from .memory_workspace import (
    DrawWorkspace,
    draw_workspace,
    new_run_id,
    sweep_stale_draw_workspaces,
)
from .obtain import (
    DEFAULT_ROOM,
    Subject,
    SubjectSpec,
    config_default,
    obtain,
)
from .proactive import ActPass, act, owe
from .sandbox import (
    Sandboxed,
    SharedDatabaseMutatedError,
    SharedDatabaseVersions,
    SourceRevisionMismatchError,
    as_knowledge,
    assert_shared_databases_unchanged,
    close_private_blank_templates,
    refuse_if_source_diverges_from_head,
    sandbox_database,
    sandbox_from_snapshot,
    shared_database_versions,
)

__all__ = [
    "DEFAULT_ROOM",
    "ActPass",
    "DrawWorkspace",
    "Knowledge",
    "Message",
    "NoteSeed",
    "Pass",
    "ReminderSeed",
    "RoomSeed",
    "Sandboxed",
    "SharedDatabaseMutatedError",
    "SharedDatabaseVersions",
    "SourceRevisionMismatchError",
    "Subject",
    "SubjectSpec",
    "Written",
    "act",
    "adaptor_names",
    "arrive",
    "as_knowledge",
    "assert_shared_databases_unchanged",
    "close_private_blank_templates",
    "config_default",
    "deliver",
    "draw_workspace",
    "dream",
    "heard",
    "know",
    "last_row_id",
    "new_run_id",
    "obtain",
    "owe",
    "pending",
    "refuse_if_source_diverges_from_head",
    "sandbox_database",
    "sandbox_from_snapshot",
    "settle",
    "shared_database_versions",
    "silent_failures",
    "sweep_stale_draw_workspaces",
]

"""The one Nora, obtained — never assembled.

**Different evaluation suites, different input, ONE Nora.** This package is that
one: the eval layer obtains the graph composed by
`nora.app.composition.build_dependencies` through here and never composes its
own.

**The rule this package enforces by having no other entry point:** a caller
states what is NOT Nora — where the store is, which doctrine, what happens to a
send — and gets the composed graph back. It does not pass collaborators, because
there is no parameter for one. Anything a caller could hand in is something that
could differ between two callers, and that is how separately composed Noras drift apart.

WHY IT IS A LAYER AND NOT A DIRECTORY. The layer contract has no slot for a thing
that obtains the real graph and is NOT the application: every other slot either
forbids reaching the top or IS the top. `nora.subject` is that slot, above `nora.app`, so:

- it may reach `nora.app` (downward, legal);
- `nora.app` may NOT reach it — `pyproject.toml`'s layers contract refuses the
  reverse, which is the property that stops the application learning it is being
  measured.

WHAT THIS IS NOT. Not a fake, not a mirror, not a "test double of the graph": a
mirror of `build_dependencies` is a second composition that only a parity test
holds in line.
"""

from __future__ import annotations

from .dreaming import Pass, adaptor_names, dream
from .knowledge import (
    AccountSeed,
    Knowledge,
    Message,
    NoteSeed,
    ReminderSeed,
    RoomSeed,
    Spawned,
    Written,
    arrive,
    deliver,
    heard,
    know,
    last_row_id,
    settle,
    silent_failures,
    watch,
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
    restored_room,
    sandbox_as_of,
    sandbox_database,
    sandbox_from_snapshot,
    seed_documents,
    seed_lessons,
    shared_database_versions,
    sweep_stale_private_blank_templates,
)
from .sandbox_index import (
    COVERAGE_FLOOR,
    EMBEDDER_FROM_HOST,
    Coverage,
    GraphIndexRefusedError,
    IndexCache,
    SandboxIndex,
    check_served_model,
    index_sandbox,
    source_coverage,
)

__all__ = [
    "COVERAGE_FLOOR",
    "DEFAULT_ROOM",
    "EMBEDDER_FROM_HOST",
    "AccountSeed",
    "ActPass",
    "Coverage",
    "DrawWorkspace",
    "GraphIndexRefusedError",
    "IndexCache",
    "Knowledge",
    "Message",
    "NoteSeed",
    "Pass",
    "ReminderSeed",
    "RoomSeed",
    "SandboxIndex",
    "Sandboxed",
    "SharedDatabaseMutatedError",
    "SharedDatabaseVersions",
    "SourceRevisionMismatchError",
    "Spawned",
    "Subject",
    "SubjectSpec",
    "Written",
    "act",
    "adaptor_names",
    "arrive",
    "as_knowledge",
    "assert_shared_databases_unchanged",
    "check_served_model",
    "close_private_blank_templates",
    "config_default",
    "deliver",
    "draw_workspace",
    "dream",
    "heard",
    "index_sandbox",
    "know",
    "last_row_id",
    "new_run_id",
    "obtain",
    "owe",
    "refuse_if_source_diverges_from_head",
    "restored_room",
    "sandbox_as_of",
    "sandbox_database",
    "sandbox_from_snapshot",
    "seed_documents",
    "seed_lessons",
    "settle",
    "shared_database_versions",
    "silent_failures",
    "source_coverage",
    "sweep_stale_draw_workspaces",
    "sweep_stale_private_blank_templates",
    "watch",
]

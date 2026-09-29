"""«Cognition's dry-run write verbs commit nothing and send nothing» as a
property of the source (design/cognition-loop/70-migration.md §2 step 3a:
"its note, event and Standing verbs in dry run (would-write rows recorded,
nothing committed)").

Two modules make up the dry-run path: `nora.domain.cognition_session` (pure
situation/agenda building — no store, no model) and
`nora.agent.cognition_dry_run` (the write-verb wrappers — validation plus
one `CognitionDryRunStore.record_would_write` call). For either to actually
COMMIT a Register/Standing row or SEND a message, it would first have to
import something that can: a live store's own module
(`nora.infra.sql.notes`, `nora.infra.sql.gatherings`, `nora.infra.sql.
standing`), the fire path (`nora.app.fire`), an adapter, or the agent
runtime. So each module's `nora` imports are pinned to the pure domain
modules and ports it needs. Adding anything else is a design change that
must come with the step that makes shadow live, and this test is where
that change is noticed — `tests/architecture/
test_the_wake_evaluator_cannot_act.py`'s own precedent, one layer over.

The behavioural half — a dry-run call records exactly one would-write row
and never calls an underlying store's mutating method — is
`tests/unit/test_cognition_dry_run.py`.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SESSION = ROOT / "nora" / "domain" / "cognition_session.py"
DRY_RUN = ROOT / "nora" / "agent" / "cognition_dry_run.py"

#: Modules `nora.infra.sql.*` never appears here on purpose: a dry-run
#: wrapper takes ALREADY-READ rows (`prior`, `current`, `shown`) as
#: arguments, the same "prior is a parameter" shape
#: `nora.infra.sql.standing_revise` uses for the live verb next door — it
#: never reads a store itself, let alone writes one.
_DRY_RUN_ALLOWED: frozenset[str] = frozenset({
    "nora.domain.cognition_write",
    "nora.domain.gatherings",
    "nora.domain.note_body",
    "nora.domain.notes",
    "nora.domain.ports",
    "nora.domain.standing",
    "nora.domain.standing_revise",
})

_SESSION_ALLOWED: frozenset[str] = frozenset({
    "nora.domain.stimuli",
})


def _nora_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return {m for m in imported if m.startswith("nora")}


def test_the_session_module_imports_nothing_that_reads_or_acts():
    assert _nora_imports(SESSION) == _SESSION_ALLOWED


def test_the_dry_run_module_imports_nothing_that_can_commit_or_send():
    assert _nora_imports(DRY_RUN) == _DRY_RUN_ALLOWED


def test_neither_module_imports_a_live_sql_store_the_fire_path_or_the_agent():
    forbidden_prefixes = (
        "nora.infra.sql.notes", "nora.infra.sql.gatherings",
        "nora.infra.sql.standing", "nora.app.fire", "nora.agent.dreamer",
        "nora.agent.factory", "nora.engagement",
    )
    for path in (SESSION, DRY_RUN):
        found = _nora_imports(path)
        for forbidden in forbidden_prefixes:
            assert not any(m == forbidden or m.startswith(forbidden + ".") for m in found), (
                path, forbidden, found
            )

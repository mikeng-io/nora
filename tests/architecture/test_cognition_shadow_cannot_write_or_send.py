"""«Cognition's dry-run write, pace and mirrored-remind verbs commit
nothing, send nothing, and the session runner that calls them never holds a
live store either» as a property of the source (design/cognition-loop/
70-migration.md §2 step 3a/3a-ii: "its note, event and Standing verbs in
dry run (would-write rows recorded, nothing committed)"; 3a-ii extends this
to the pace verbs and the mirrored, held `remind` family, and to the
session runner that wires them together).

Five modules make up the shadow path now:

- `nora.domain.cognition_session` (pure situation/agenda building) and
  `nora.domain.cognition_pace` / `nora.domain.cognition_remind` (pure
  guard checks) — no store, no model, ever.
- `nora.agent.cognition_dry_run`, `nora.agent.cognition_pace`,
  `nora.agent.cognition_remind_mirror` — the write/pace/remind-mirror
  wrappers: validation plus one `CognitionDryRunStore.record_*` call
  (and, for `wake_at`/`wake_move`, one `StimulusStore.append` — the
  ALREADY-LIVE wake evaluator's own port, never a live tier store).
- `nora.app.cognition_session` (new at 3a-ii) — the runner that builds a
  session's tools and calls the wrappers above. It is an APP-layer module
  (it legitimately depends on the agent-layer wrappers, unlike the pure
  domain modules), but the same property must hold one level up: it may
  reach the wrappers and the pure domain, and NOTHING that could commit a
  live Register/Standing row or send a message. `nora.app.
  cognition_agent_runner` (the production Strands wiring) is deliberately
  OUTSIDE this test's scope — it is where the real model call and the real
  `nora.agent.factory`/`nora.agent.dream_guard` machinery belong, and it
  holds no write verb of its own; it only ever hands the runner's ALREADY
  dry-run tool list to `create_dream_agent`.

For any of the five to actually COMMIT a Register/Standing row or SEND a
message, it would first have to import something that can: a live store's
own module (`nora.infra.sql.notes`, `nora.infra.sql.gatherings`,
`nora.infra.sql.standing`, `nora.infra.sql.reminders`), the fire path
(`nora.app.fire`), an adapter, or the agent runtime
(`nora.agent.dreamer`/`nora.agent.factory`). So each module's `nora`
imports are pinned to the pure domain modules, ports and wrappers it
needs. Adding anything else is a design change that must come with the
step that makes shadow live, and this test is where that change is
noticed — `tests/architecture/test_the_wake_evaluator_cannot_act.py`'s own
precedent, one layer over.

The behavioural half — a dry-run/pace/remind call records exactly one row
and never calls an underlying store's mutating method, and a whole
session's worth of tool calls never reaches a live store or sends — is
`tests/unit/test_cognition_dry_run.py`, `tests/unit/test_cognition_pace.py`,
`tests/unit/test_cognition_remind.py` and `tests/unit/
test_cognition_session_writes_nothing_live.py`.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SESSION = ROOT / "nora" / "domain" / "cognition_session.py"
DRY_RUN = ROOT / "nora" / "agent" / "cognition_dry_run.py"
PACE_DOMAIN = ROOT / "nora" / "domain" / "cognition_pace.py"
REMIND_DOMAIN = ROOT / "nora" / "domain" / "cognition_remind.py"
PACE_AGENT = ROOT / "nora" / "agent" / "cognition_pace.py"
REMIND_AGENT = ROOT / "nora" / "agent" / "cognition_remind_mirror.py"
SESSION_RUNNER = ROOT / "nora" / "app" / "cognition_session.py"
FETCHED = ROOT / "nora" / "agent" / "fetch_watch.py"

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

_PACE_DOMAIN_ALLOWED: frozenset[str] = frozenset()
_REMIND_DOMAIN_ALLOWED: frozenset[str] = frozenset()

_PACE_AGENT_ALLOWED: frozenset[str] = frozenset({
    "nora.domain.cognition_pace", "nora.domain.ports", "nora.domain.stimuli",
})
_REMIND_AGENT_ALLOWED: frozenset[str] = frozenset({
    "nora.domain.cognition_remind", "nora.domain.ports",
})

#: Watches the session's read tools' results and keeps what they handed out; it
#: reads a tool's answer and writes nothing anywhere.
_FETCHED_ALLOWED: frozenset[str] = frozenset({
    "nora.agent.room_routing", "nora.domain.note_body",
})

_SESSION_RUNNER_ALLOWED: frozenset[str] = frozenset({
    "nora.agent.cognition_dry_run", "nora.agent.cognition_pace", "nora.agent.fetch_watch",
    "nora.agent.cognition_remind_mirror", "nora.domain.cognition_pace",
    "nora.domain.cognition_ground", "nora.domain.cognition_remind",
    "nora.domain.cognition_session",
    "nora.domain.gatherings", "nora.domain.note_body", "nora.domain.ports",
    "nora.domain.standing", "nora.domain.stimuli",
    # The prompt renderer moved here from `nora.domain` (design/context-assembly/
    # 04-every-occasion.md step 3a): pure strings-in, string-out, no store.
    "nora.language.cognition_render",
})

_ALL = {
    SESSION: _SESSION_ALLOWED,
    DRY_RUN: _DRY_RUN_ALLOWED,
    PACE_DOMAIN: _PACE_DOMAIN_ALLOWED,
    REMIND_DOMAIN: _REMIND_DOMAIN_ALLOWED,
    PACE_AGENT: _PACE_AGENT_ALLOWED,
    REMIND_AGENT: _REMIND_AGENT_ALLOWED,
    SESSION_RUNNER: _SESSION_RUNNER_ALLOWED,
    FETCHED: _FETCHED_ALLOWED,
}


def _nora_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return {m for m in imported if m.startswith("nora")}


def test_every_shadow_module_imports_exactly_its_allowlist():
    for path, allowed in _ALL.items():
        assert _nora_imports(path) == allowed, path


def test_no_shadow_module_imports_a_live_sql_store_the_fire_path_or_the_agent():
    forbidden_prefixes = (
        "nora.infra.sql.notes", "nora.infra.sql.gatherings",
        "nora.infra.sql.standing", "nora.infra.sql.reminders",
        "nora.app.fire", "nora.agent.dreamer", "nora.agent.factory",
        "nora.agent.dream_guard", "nora.engagement",
    )
    for path in _ALL:
        found = _nora_imports(path)
        for forbidden in forbidden_prefixes:
            assert not any(m == forbidden or m.startswith(forbidden + ".") for m in found), (
                path, forbidden, found
            )

"""computer_tasks kind: + maintenance (the elected cleanup segment)

`design/browser-runtime/00-design.md`: the browser lifecycle sweep — idle
tab close, orphan reconcile, browser stop — is execution on the scope's VM,
so it runs INSIDE the one transactionally-elected scope owner, never as a
second admission path beside it. The sweep writes a `computer_tasks` row
with `kind="maintenance"`, claims it through the same `try_claim_head`
advisory-lock election every segment uses, and runs it through
`run_segment` like a submitter-owned (`worker`) row. The CHECK needs the
new kind or the claim's INSERT is refused.

The expression is built from `TASK_KINDS` — the list is owned by the
domain, never retyped here, so a later kind widens the constraint the same
way instead of drifting from `schema.py`.
"""

from __future__ import annotations

from alembic import op

from nora.domain.computer_tasks import TASK_KINDS

revision = "0148_computer_task_maintenance"
down_revision = "0147_computer_tasks"
branch_labels = None
depends_on = None

_KINDS = ", ".join(repr(k) for k in sorted(TASK_KINDS))
_PREV = ", ".join(repr(k) for k in sorted(TASK_KINDS - {"maintenance"}))


def upgrade() -> None:
    op.drop_constraint("ck_computer_tasks_kind", "computer_tasks", type_="check")
    op.create_check_constraint(
        "ck_computer_tasks_kind", "computer_tasks", f"kind IN ({_KINDS})")


def downgrade() -> None:
    # Fails loudly if a maintenance row exists — the correct refusal, never
    # a silent narrowing over live rows.
    op.drop_constraint("ck_computer_tasks_kind", "computer_tasks", type_="check")
    op.create_check_constraint(
        "ck_computer_tasks_kind", "computer_tasks", f"kind IN ({_PREV})")

"""`standing_revise`: the write verb for room Standing, the PURE half.

Every writer of a room's `Standing` goes through one verb
(`nora.infra.sql.standing_revise.standing_revise`, the async half that resolves
a caller's `base_revision_id` against the stored head and then calls the checks
below), so every row passes the SAME validator and the head has one shape.

This module holds the checks that need no store: the tier's allowed reasoning
modes (`deduced`, `abduced` and `said`) and the schema (`Where it stands` is
required and non-blank; `I hold open` and `I owe` are optional and, when
given, must not be blank: a section a caller means to clear is omitted,
`None`, never sent empty). `nora.infra.sql.standing_revise` reads the current
head, checks `base_revision_id` against it (needs a store, so it is not here),
calls these functions, and only then calls `SqlStandingStore.write`.
`nora/domain/`'s own rule: stdlib and dataclasses only, no store, no clock read
(every timestamp is a caller's argument).

`I hold open` and `I owe` (`nora.domain.standing.Standing.hold_open`/`.i_owe`)
have a schema check, a render (`nora.language.standing_render.render_standing`)
and a history (`standing_revision`). The debrief objective
(`nora/dreamer/objectives/standing-debrief.md`) does not ask the model for
them, so both stay `None` on rows it writes; asking for them is a doctrine
change gated by the eval suite.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nora.domain.note_body import Shown
    from nora.domain.standing_ledger import LedgerEntry, LedgerOp

from nora.domain.standing import STANDING_BODY_CEILING_CHARS

#: The reasoning modes Standing allows. `computed` is Rollup-only, `told` and
#: `induced` are Register/Character-only; a Standing row is `deduced`, `abduced` or `said`.
ALLOWED_MODES: tuple[str, ...] = ("deduced", "abduced", "said")

#: The section names a `standing_revise` caller may write, closed: a heading
#: not in the schema is refused. `Found, not yet given` (`working_set`) is not
#: here: it has its own validator (`nora.engagement.stages.debrief._working_set`)
#: run before a caller reaches this verb, and code-written sections (history,
#: folds) are never a model argument.
REQUIRED_SECTION = "where_it_stands"
OPTIONAL_SECTIONS: tuple[str, ...] = ("hold_open", "i_owe")


@dataclass(frozen=True, slots=True)
class Refusal:
    """A refused `standing_revise` call: `ADR 0009`'s `refused` status,
    answered with a reason from a closed vocabulary (the refusal names the rule
    so the caller can rewrite) and counted by that reason, never silently
    dropped.
    """

    reason: str
    detail: str


def check_mode(mode: str) -> Refusal | None:
    """The mode must be one Standing allows."""
    if mode not in ALLOWED_MODES:
        return Refusal(
            reason="bad_mode",
            detail=f"{mode!r} is not one of {ALLOWED_MODES} — Standing's "
                   f"allowed reasoning modes (10-tiers.md §4)",
        )
    return None


def check_schema(
    *, where_it_stands: str, hold_open: str | None, i_owe: str | None,
) -> Refusal | None:
    """`Where it stands` is required and non-blank; a given optional section
    must not be blank. A caller that means "nothing to add here" omits the
    section (`None`), it does not send an empty one.

    Mirrors every invariant `nora.domain.standing.Standing.__post_init__`
    enforces on these three fields, including the `STANDING_BODY_CEILING_CHARS`
    ceiling on `hold_open`/`i_owe`, not just the blank check. A caller must be
    able to tell "the model's answer breaks the schema" (refused) from "the
    store did not respond" (unavailable) (R-27); checking the ceiling HERE,
    before `Standing(...)` is constructed, keeps the constructor's own
    `ValueError` unreachable through this verb, so it is never mistaken for a
    store failure.
    """
    if not where_it_stands or not where_it_stands.strip():
        return Refusal(
            reason="missing_required_section",
            detail="'Where it stands' is required and was blank",
        )
    if len(where_it_stands) > STANDING_BODY_CEILING_CHARS:
        return Refusal(
            reason="section_too_long",
            detail=f"'Where it stands' is {len(where_it_stands)} characters, "
                   f"over the {STANDING_BODY_CEILING_CHARS}-character ceiling",
        )
    for name, value in (("I hold open", hold_open), ("I owe", i_owe)):
        if value is not None and not value.strip():
            return Refusal(
                reason="blank_section",
                detail=f"{name!r} was given blank; omit it (None) instead "
                       f"of sending an empty section",
            )
        if value is not None and len(value) > STANDING_BODY_CEILING_CHARS:
            return Refusal(
                reason="section_too_long",
                detail=f"{name!r} is {len(value)} characters, over the "
                       f"{STANDING_BODY_CEILING_CHARS}-character ceiling",
            )
    return None


def check_stale_base(
    *, base_revision_id: str | None, current_revision_id: str | None,
) -> Refusal | None:
    """A `base_revision_id` that is not the current revision is refused
    `stale_base`.

    The token is `standing_revision.id`, the id `standing_read` shows as
    `revision` (DEC-250). Every write to the head appends a `standing_revision`
    row in the same transaction (`SqlStandingStore.write`), so the id changes
    whenever the head does; `fold_count` would not, since it does not move when
    a write passes `increments_fold_count=False`.

    `base_revision_id=None` means the caller asserts no base (a caller
    serialized through `ConversationSlots`) and always passes. A base against a
    head that has no revision id (no head, or a head the chain does not end at)
    is stale: a token that names nothing cannot be the current one.
    """
    if base_revision_id is None:
        return None
    if current_revision_id is None:
        return Refusal(
            reason="stale_base",
            detail="this room's Standing has no revision to write against; "
                   "read it again",
        )
    if base_revision_id != current_revision_id:
        return Refusal(
            reason="stale_base",
            detail=f"base_revision_id={base_revision_id} but the current "
                   f"revision is {current_revision_id}",
        )
    return None


def check_ledger_op(
    op: LedgerOp, prior: LedgerEntry | None, *, shown: Shown,
    unit: str, platform: str, conversation_id: str, now_ms: int, id: str,
) -> LedgerEntry | Refusal:
    """The ledger arm of the same validator; legacy bases remain optional."""
    from dataclasses import replace

    from nora.domain.note_body import NoteBodyError, cited_line, split_sections
    from nora.domain.standing_ledger import LedgerEntry

    if op.op not in {"open", "revise", "close", "touch"}:
        return Refusal("not_an_op", "Standing writes must name an operation")
    if op.kind not in {"decision", "commitment", "thread"}:
        return Refusal("bad_kind", "unknown Standing entry kind")
    refused = check_mode(op.mode)
    if refused:
        return refused
    if op.op != "open":
        if prior is None:
            return Refusal("unknown_entry", "read the entry before changing it")
        if op.op != "touch":
            if op.base is None:
                return Refusal("missing_base", "read the entry's revision first")
            if op.base != prior.revision:
                return Refusal("stale_base", "the entry changed; read it again")
    elif prior is not None or op.id is not None:
        return Refusal("bad_open", "open creates a new entry")
    if op.scope is not None and op.scope not in {"room", "unit"}:
        return Refusal("bad_scope", "scope is room or unit")
    if op.op == "touch" and (
        op.kind != "thread" or not op.message_ids or op.scope is not None
        or op.subject is not None or op.sections or op.outcome is not None
        or op.status is not None or op.note_ids is not None or op.decision_ids is not None
    ):
        return Refusal("bad_touch", "touch only adds thread messages")
    if op.op == "close" and op.kind == "thread":
        return Refusal("bad_close", "threads are pointers, not decisions")
    entry = prior or LedgerEntry(
        id=id, kind=op.kind, unit=unit, scope=op.scope or "room",
        platform=platform, conversation_id=conversation_id, revision=0,
        status="open", subject=op.subject or "", opened_at=now_ms, last_revised_at=now_ms,
    )
    subject = entry.subject if op.subject is None else op.subject
    if not subject.strip() or "\n" in subject:
        return Refusal("bad_subject", "the subject is one nonblank line")
    allowed = {
        "decision": ("Options", "Criteria", "Leaning", "Blocked on", "Reopen if", "Closed as"),
        "commitment": ("Owed", "Done as"), "thread": (),
    }[op.kind]
    if any(key not in allowed for key in op.sections):
        return Refusal("unknown_section", "only this entry kind's authored sections may be written")
    sections = {h: "\n".join(f"- {line}" for line in lines)
                for h, lines in split_sections(entry.body, schema=allowed)} if entry.body else {}
    sections.update({h: "\n".join(f"- {line.removeprefix('- ').strip()}"
                                 for line in value.splitlines())
                     for h, value in op.sections.items()})
    status = entry.status
    if op.op == "close":
        status = op.status or ("closed" if op.kind == "decision" else "done")
        if not op.outcome:
            return Refusal("missing_outcome", "closing needs cited grounds")
        sections["Closed as" if op.kind == "decision" else "Done as"] = "\n".join(
            f"- {line.removeprefix('- ').strip()}" for line in op.outcome.splitlines()
        )
    elif op.status is not None or op.outcome is not None:
        return Refusal("bad_status", "only close changes status")
    if status not in ({"open", "closed", "dropped"} if op.kind == "decision"
                      else {"open", "done", "released"} if op.kind == "commitment"
                      else {"open"}):
        return Refusal("bad_status", "unknown status for this entry kind")
    required = (("Options", "Leaning" if status == "open" else "Closed as")
                if op.kind == "decision" else
                ("Owed",) + (("Done as",) if status != "open" else ())
                if op.kind == "commitment" else ())
    if any(not sections.get(name, "").strip() for name in required):
        return Refusal("missing_required_section", "a required entry section is missing")
    try:
        # Carried sections were already checked. Only new claims need the current frame.
        changed = dict(op.sections)
        if op.op == "close":
            changed["Closed as" if op.kind == "decision" else "Done as"] = op.outcome or ""
        for heading, body in changed.items():
            if not body.strip():
                return Refusal("blank_section", "omit a section rather than sending it blank")
            for line in body.splitlines():
                item = line.removeprefix("- ").strip()
                if heading == "Leaning" and item == "none yet":
                    continue
                cited_line(heading, item, shown, may_cite_nora=True)
    except NoteBodyError as exc:
        return Refusal(exc.rule, "a ledger claim failed the citation grammar")
    body = "\n".join(f"## {name}\n{sections[name]}" for name in allowed if name in sections)
    if len(body) > STANDING_BODY_CEILING_CHARS:
        return Refusal("section_too_long", "entry body exceeds the Standing ceiling")
    if op.kind == "thread" and (op.sections or op.outcome):
        return Refusal("thread_body", "a thread has no body")
    if op.kind != "thread" and op.message_ids:
        return Refusal("bad_messages", "only threads carry message spans")
    return replace(
        entry, scope=op.scope or entry.scope, subject=subject, body=body, status=status,
        revision=entry.revision + 1, last_revised_at=now_ms,
        closed_at=now_ms if op.op == "close" else entry.closed_at,
        note_id=op.note_id if op.note_id is not None else entry.note_id,
        owed_to=op.owed_to if op.owed_to is not None else entry.owed_to,
        due_at=op.due_at if op.due_at is not None else entry.due_at,
        note_ids=op.note_ids if op.note_ids is not None else entry.note_ids,
        decision_ids=op.decision_ids if op.decision_ids is not None else entry.decision_ids,
        message_ids=tuple(sorted(set(entry.message_ids) | set(op.message_ids))),
        reasoning=op.mode,
    )
